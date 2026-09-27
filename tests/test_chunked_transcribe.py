#!/usr/bin/env python3
"""Тести чистої логіки chunked_transcribe.py — без ffmpeg, без whisper.

Запускати на VPS venv'ом: ~/lectures/venv/bin/python tests/test_chunked_transcribe.py
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "vps" / "bin"))

import chunked_transcribe as ct  # noqa: E402

ok = fail = 0


def check(name, cond, detail=""):
    global ok, fail
    if cond:
        ok += 1
        print(f"PASS {name}")
    else:
        fail += 1
        print(f"FAIL {name}  {detail}")


# --- parse_silence_starts ----------------------------------------------------
# Канонічний фрагмент реального stderr ffmpeg -af silencedetect (skip шум навколо).
SAMPLE_STDERR = """
[silencedetect @ 0x55f1c2] silence_start: 12.345
[silencedetect @ 0x55f1c2] silence_end: 12.9 | silence_duration: 0.555
Stream mapping:
[silencedetect @ 0x55f1c2] silence_start: 298.5
[silencedetect @ 0x55f1c2] silence_start: 611.02
"""
starts = ct.parse_silence_starts(SAMPLE_STDERR)
check("parse_silence_starts: усі три знайдені", starts == [12.345, 298.5, 611.02], starts)
check("parse_silence_starts: порожній ввід -> []", ct.parse_silence_starts("") == [])
check("parse_silence_starts: сміттєвий рядок ігнорується",
      ct.parse_silence_starts("silence_start: не_число\n") == [])


# --- compute_split_points -----------------------------------------------------
# 600с, 2 шматки: ціль розрізу — 300с. Є пауза рівно поруч (298.5) — має обрати її.
pts = ct.compute_split_points(600.0, 2, [12.345, 298.5, 611.02])
check("compute_split_points: обирає найближчу паузу в вікні", pts == [298.5], pts)

# Пауза поза вікном (±30с) — fallback на рівний поділ.
pts = ct.compute_split_points(600.0, 2, [12.345, 611.02])
check("compute_split_points: fallback на рівний поділ, якщо пауз нема в вікні",
      pts == [300.0], pts)

# 3 шматки, 900с: цілі 300 і 600.
pts = ct.compute_split_points(900.0, 3, [305.0, 598.0])
check("compute_split_points: n_chunks=3 дає 2 точки", pts == [305.0, 598.0], pts)

check("compute_split_points: n_chunks<2 -> []", ct.compute_split_points(600.0, 1, [300.0]) == [])
check("compute_split_points: duration<=0 -> []", ct.compute_split_points(0.0, 2, [300.0]) == [])

# Кандидат рівно на межі вікна (30.0) — межа включна.
pts = ct.compute_split_points(600.0, 2, [270.0])
check("compute_split_points: кандидат на межі вікна (=30с) береться", pts == [270.0], pts)
# За межею (30.01) — не береться, fallback.
pts = ct.compute_split_points(600.0, 2, [269.99])
check("compute_split_points: кандидат щойно за межею вікна ігнорується", pts == [300.0], pts)


# --- merge_segment_data --------------------------------------------------------
chunk_a = {
    "raw_segments": [{"start": 0.5, "text": "Перше речення."},
                      {"start": 10.0, "text": "Друге речення."}],
    "segments": 2, "language": "uk", "language_probability": 0.95,
    "duration": 300.0, "model": "large-v3",
}
chunk_b = {
    "raw_segments": [{"start": 0.2, "text": "Третє речення."}],
    "segments": 1, "language": "uk", "language_probability": 0.99,
    "duration": 300.0, "model": "large-v3",
}
merged = ct.merge_segment_data([chunk_a, chunk_b], [0.0, 300.0])

check("merge_segment_data: сума segments", merged["segments"] == 3, merged["segments"])
check("merge_segment_data: сума duration", merged["duration"] == 600.0, merged["duration"])
check("merge_segment_data: перший таймкод шматка A не зсунутий",
      merged["stamped"].splitlines()[0] == "[00:00] Перше речення.", merged["stamped"])
check("merge_segment_data: таймкод шматка B зсунутий на offset",
      merged["stamped"].splitlines()[-1] == "[05:00] Третє речення.", merged["stamped"])
check("merge_segment_data: plain у порядку шматків",
      merged["plain"] == "Перше речення. Друге речення. Третє речення.", merged["plain"])

# language/probability — з найдовшого чанка (за duration); тут рівні,
# перевіряємо на явно різних тривалостях.
chunk_short = {**chunk_b, "duration": 50.0, "language_probability": 0.5}
chunk_long = {**chunk_a, "duration": 550.0, "language_probability": 0.9}
merged2 = ct.merge_segment_data([chunk_short, chunk_long], [0.0, 50.0])
check("merge_segment_data: language_probability з найдовшого чанка",
      merged2["language_probability"] == 0.9, merged2["language_probability"])

# Порожній список чанків для одного зі входів (напр. один шматок без мовлення).
chunk_empty = {"raw_segments": [], "segments": 0, "language": "uk",
               "language_probability": 0.0, "duration": 100.0, "model": "large-v3"}
merged3 = ct.merge_segment_data([chunk_a, chunk_empty], [0.0, 300.0])
check("merge_segment_data: порожній чанк не ламає злиття (segments сумуються)",
      merged3["segments"] == 2, merged3)


# --- best_of у воркері (27.09.2026) -------------------------------------------
# Текстом, а не імпортом: імпорт тягне faster_whisper і модель.
# Репо: ../vps/bin; на VPS тести лежать у ~/lectures/tests, код — у ~/lectures/bin.
_worker = next(p for p in (Path(__file__).resolve().parents[1] / "vps" / "bin" / "transcribe_worker.py",
                           Path(__file__).resolve().parents[1] / "bin" / "transcribe_worker.py")
               if p.exists())
_src = _worker.read_text(encoding="utf-8")
check("воркер: best_of передається в transcribe()", "best_of=BEST_OF" in _src)
check("воркер: best_of=1 за замовчуванням (стрибки RSS ×5 гіпотез на fallback)",
      'os.getenv("WHISPER_BEST_OF", "1")' in _src)
check("воркер: temperature=0 за замовчуванням і передається в transcribe()",
      'os.getenv("WHISPER_TEMPERATURE", "0")' in _src and "temperature=TEMPERATURE" in _src)

# --- drop_loops: петля T=0 з кліпу 27.09.2026 ----------------------------------
import importlib.util  # noqa: E402

_spec = importlib.util.spec_from_file_location("transcribe_worker", _worker)
tw = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(tw)  # без faster_whisper: імпорт моделі — всередині main()

LOOP = "І відповідно між цими вузлами прикладається гармонічний струм."
clip_tail = [(592.3, "Ємність С, яка вмикається між вузлами А та вузлом В."),
             (596.8, LOOP), (600.3, LOOP), (602.3, LOOP), (604.3, LOOP),
             (606.3, LOOP), (608.3, LOOP), (610.3, LOOP)]
got = tw.drop_loops(clip_tail, 600.0)
check("drop_loops: петля за кінцем аудіо зрізана до одного сегмента",
      got == clip_tail[:2], got)

dictation = [(107.2, "То індуктивність віддає енергію."),
             (109.6, "То індуктивність віддає енергію."),
             (113.0, "Відповідно, миттєва потужність")]
check("drop_loops: подвійне диктування лишається", tw.drop_loops(dictation, 600.0) == dictation)

run = [(float(i), "a") for i in range(5)] + [(5.0, "b"), (6.0, "a")]
check("drop_loops: серія >2 обрізається до 2, після перерви лічба з нуля",
      tw.drop_loops(run, 100.0) == [(0.0, "a"), (1.0, "a"), (5.0, "b"), (6.0, "a")],
      tw.drop_loops(run, 100.0))
check("drop_loops: duration=0 не ріже нічого за часом",
      tw.drop_loops([(700.0, "x")], 0) == [(700.0, "x")])

print(f"\nпройдено {ok}/{ok + fail}")
sys.exit(0 if fail == 0 else 1)
