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


print(f"\nпройдено {ok}/{ok + fail}")
sys.exit(0 if fail == 0 else 1)
