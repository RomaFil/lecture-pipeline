#!/usr/bin/env python3
"""Паралельна нарізка: розбиває wav на N шматків по паузах і транскрибує
паралельно окремими процесами transcribe_worker.py, потім зводить результат.

Навіщо: один інстанс whisper масштабується на цій VPS непередбачувано —
задокументована яма CTranslate2/faster-whisper (WHISPER_THREADS 3, 4, 5, 6
усі гірші за 2, cpu_threads=0 "авто" теж). Кілька паралельних інстансів по
WHISPER_THREADS=2 обходять яму, бо це окремі процеси, а не внутрішній
intra-op паралелізм одного інстансу: виміряно 26.09.2026 на 10-хв кліпі —
2 паралельні шматки по 2 потоки дали 1.085х замість 1.623х одним інстансом
на 2 потоках. Деталі й повний план — [[lecture-pipeline]], розділ
«Розслідування 26.09.2026».

Формат виходу — той самий словник, що дає одиночний transcribe_worker.py
(stamped/plain/segments/language/language_probability/duration/model),
щоб process.py та все нижче за течією не потребували змін.
"""
import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

CHUNK_THREADS = os.getenv("CHUNK_THREADS", "2")  # єдине підтверджене хороше значення
SILENCE_NOISE_DB = "-30dB"
SILENCE_MIN_DUR = "0.3"
# Вікно пошуку паузи навколо цільової точки розрізу. Пауза не знайдена в
# вікні — fallback на рівний поділ для цього розрізу: гірший шов (можливе
# розірване слово), але не привід відмовляти запису в обробці.
SPLIT_SEARCH_WINDOW = 30.0


# --------------------------------------------------------------- чисті функції
# (без IO — легко покрити тестами без реального ffmpeg/whisper)

def parse_silence_starts(ffmpeg_stderr: str) -> list[float]:
    """Витягує часи `silence_start` з виводу ffmpeg -af silencedetect."""
    out = []
    for line in ffmpeg_stderr.splitlines():
        line = line.strip()
        if "silence_start:" not in line:
            continue
        tail = line.split("silence_start:", 1)[1].strip()
        token = tail.split()[0] if tail.split() else ""
        try:
            out.append(float(token))
        except ValueError:
            continue
    return out


def compute_split_points(duration: float, n_chunks: int,
                          silence_candidates: list[float],
                          window: float = SPLIT_SEARCH_WINDOW) -> list[float]:
    """n_chunks-1 точок розрізу, кожна притягнута до найближчої паузи в межах
    ±window навколо рівномірної цілі. Пауза не знайдена — точка лишається
    рівномірною (fallback), розріз усе одно відбувається.

    Порожній список при n_chunks < 2 або duration <= 0.
    """
    if n_chunks < 2 or duration <= 0:
        return []
    points = []
    for i in range(1, n_chunks):
        target = duration * i / n_chunks
        candidates_in_window = [c for c in silence_candidates
                                 if abs(c - target) <= window and 0 < c < duration]
        if candidates_in_window:
            points.append(min(candidates_in_window, key=lambda c: abs(c - target)))
        else:
            points.append(target)
    return points


def merge_segment_data(chunk_datas: list[dict], offsets: list[float]) -> dict:
    """Зводить список результатів transcribe_worker.py (з полем raw_segments,
    формат [{"start": сек, "text": ...}, ...]) в один словник того самого
    формату, що дає одиночний виклик.

    offsets[i] — з якої секунди оригінального файлу починається chunk_datas[i].
    language/language_probability беруться з найдовшого (за duration) чанка —
    короткий чанк (наприклад, самий вступ) статистично ненадійніший.
    """
    stamped_lines, plain_parts = [], []
    total_segments = 0
    for data, offset in zip(chunk_datas, offsets):
        for seg in data.get("raw_segments", []):
            abs_start = seg["start"] + offset
            m, sec = int(abs_start) // 60, int(abs_start) % 60
            text = seg["text"].strip()
            stamped_lines.append(f"[{m:02d}:{sec:02d}] {text}")
            plain_parts.append(text)
        total_segments += data.get("segments", 0)

    longest = max(chunk_datas, key=lambda d: d.get("duration", 0))
    return {
        "stamped": "\n".join(stamped_lines),
        "plain": " ".join(plain_parts),
        "segments": total_segments,
        "language": longest.get("language"),
        "language_probability": longest.get("language_probability"),
        "duration": sum(d.get("duration", 0) for d in chunk_datas),
        "model": longest.get("model"),
    }


# ------------------------------------------------------------------------ IO

def probe_duration(wav: Path) -> float:
    r = subprocess.run(
        ["ffprobe", "-v", "error", "-show_entries", "format=duration",
         "-of", "default=noprint_wrappers=1:nokey=1", str(wav)],
        capture_output=True, text=True, timeout=120,
    )
    return float(r.stdout.strip()) if r.returncode == 0 and r.stdout.strip() else 0.0


def find_split_points(wav: Path, n_chunks: int) -> list[float]:
    duration = probe_duration(wav)
    if n_chunks < 2 or duration <= 0:
        return []
    r = subprocess.run(
        ["ffmpeg", "-i", str(wav), "-af",
         f"silencedetect=noise={SILENCE_NOISE_DB}:d={SILENCE_MIN_DUR}",
         "-f", "null", "-"],
        capture_output=True, text=True, timeout=300,
    )
    candidates = parse_silence_starts(r.stderr)
    return compute_split_points(duration, n_chunks, candidates)


def split_audio(wav: Path, points: list[float], tmp_dir: Path) -> list[Path]:
    """Різка на len(points)+1 файлів 16кГц моно (той самий формат, що очікує
    transcribe_worker.py)."""
    bounds = [0.0, *points, None]  # None = до кінця файлу
    chunks = []
    for i in range(len(bounds) - 1):
        start, end = bounds[i], bounds[i + 1]
        out = tmp_dir / f"chunk{i}.wav"
        cmd = ["ffmpeg", "-y", "-i", str(wav)]
        if start:
            cmd += ["-ss", str(start)]
        if end is not None:
            cmd += ["-to", str(end)] if start is None else ["-t", str(end - (start or 0))]
        cmd += ["-ar", "16000", "-ac", "1", str(out)]
        subprocess.run(cmd, check=True, capture_output=True)
        chunks.append(out)
    return chunks


def run_parallel(chunk_paths: list[Path], tmp_dir: Path,
                  threads: str = CHUNK_THREADS, cpu_limit: str | None = None) -> list[dict]:
    """Запускає transcribe_worker.py на кожен шматок паралельно, чекає на
    всі, повертає розпарсені результати в тому самому порядку, що chunk_paths.

    Успіх кожного — за існуванням вихідного файлу, не за кодом виходу:
    cpulimit МАСКУЄ код завершення дитини (див. process.py:transcribe,
    інцидент з 09.09.2026) — той самий патерн тут.

    Очікування реального завершення (а не лише прямого child-процесу
    cpulimit) працює завдяки stdout=PIPE/stderr=PIPE: Popen.communicate()
    блокується до EOF на пайпі, яке настає лише коли ВСІ процеси, що
    успадкували fd (включно з онуком під cpulimit), його закриють —
    тобто до реального завершення transcribe_worker.py, а не самого
    cpulimit. Той самий механізм, що вже надійно працює в
    process.py:transcribe() через subprocess.run(capture_output=True).
    """
    worker = Path(__file__).parent / "transcribe_worker.py"
    procs, outs = [], []
    for chunk in chunk_paths:
        out = tmp_dir / f"{chunk.stem}_out.json"
        cmd = [sys.executable, str(worker), str(chunk), str(out)]
        if cpu_limit and shutil.which("cpulimit"):
            cmd = ["cpulimit", "-l", cpu_limit, "--"] + ["nice", "-n", "10"] + cmd
        else:
            cmd = ["nice", "-n", "10"] + cmd
        env = {**os.environ, "WHISPER_THREADS": threads}
        p = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, env=env)
        procs.append(p)
        outs.append(out)

    results = []
    for p, out, chunk in zip(procs, outs, chunk_paths):
        _stdout, stderr = p.communicate()
        if not out.exists():
            raise RuntimeError(
                f"chunked: transcribe_worker впав на {chunk.name} "
                f"(returncode={p.returncode}, ненадійний під cpulimit): "
                f"{stderr.decode(errors='replace')[-800:]}")
        results.append(json.loads(out.read_text(encoding="utf-8")))
    return results


def transcribe_chunked(wav: Path, n_chunks: int, tmp_dir: Path,
                        threads: str = CHUNK_THREADS,
                        cpu_limit: str | None = None) -> dict:
    """Повний оркестратор: нарізка → паралельна транскрипція → злиття.

    tmp_dir має існувати й бути порожньою робочою текою — шматки й проміжні
    JSON туди пишуться і звідти приберуться (chunk*.wav, chunk*_out.json)
    після успішного злиття; сам tmp_dir не видаляється (керує викликач).
    """
    points = find_split_points(wav, n_chunks)
    offsets = [0.0, *points]
    chunk_paths = split_audio(wav, points, tmp_dir)
    try:
        chunk_datas = run_parallel(chunk_paths, tmp_dir, threads=threads, cpu_limit=cpu_limit)
        return merge_segment_data(chunk_datas, offsets)
    finally:
        for c in chunk_paths:
            c.unlink(missing_ok=True)
        for c in chunk_paths:
            out = tmp_dir / f"{c.stem}_out.json"
            out.unlink(missing_ok=True)
