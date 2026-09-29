#!/usr/bin/env python3
"""Автоматична оцінка якості транскрипту — сигнал для сторожа, а не фільтр.

НАВІЩО. За тиждень 27-29.09.2026 дві зміни декодування тихо псували текст:
temperature=0 загубив половину лекції в петлях, а старий режим видавав сотні
повторених рядків. Жоден алерт цього не бачив — усе знаходили ручні звірки.
Тут три дешеві ознаки, які ловлять саме ці класи збоїв:

- ДІРКИ: хвилини, де у звуковій доріжці є звук (ffmpeg silencedetect), а
  транскрипт майже порожній. Пауза, перерва чи самостійна робота — тиша, вона
  діркою не рахується. Петля, яку whisper «проковтнув», чи перестрибнуте
  вікно — рахується.
- ПОВТОРИ: серія однакових сусідніх рядків (петля greedy-декодування).
- СМІТТЯ: рядки з ієрогліфами, хангилем, арабською тощо — в українській
  лекції їх не буває, це типові галюцинації whisper на шумі.

Результат лише логується (маркер QUALITY_WARN для alert.sh, перевірка 17) і
пишеться в метадані транскрипту. Обробку він не зупиняє: запис із діркою все
одно цінніший на YouTube і в vault, ніж у черзі.

Пороги відкалібровано 29.09.2026 на транскриптах контролю validate2 і
зламаному прогоні temperature=0 (деталі — [[lecture-pipeline]]).
"""
import json
import re
import subprocess
import sys
from pathlib import Path

SILENCE_NOISE_DB = "-30dB"   # той самий поріг, що й для розрізу в chunked_transcribe
SILENCE_MIN_DUR = "0.5"
HOLE_SOUND_MIN = 0.5         # хвилина «звучить», якщо тиші в ній менше половини
HOLE_WORDS_MAX = 15          # ...а слів у транскрипті менше за це (норма мовлення 80-150/хв)
HOLE_RUN_MIN = 3             # діркою вважається лише серія таких хвилин поспіль
REPEAT_RUN_WARN = 4          # стільки однакових рядків поспіль — вже петля, не диктування...
REPEAT_MIN_WORDS = 4         # ...але лише для фраз від 4 слів: «До побачення.» ×4 від
REPEAT_RUN_SHORT = 10        # студентів чи «Дописую.» ×5 — справжні; коротка фраза — з 10
JUNK_WARN = 3                # стільки рядків зі «сміттєвими» письмами — сигнал

_ROW = re.compile(r"\[(\d+):(\d{2})\] ?(.*)")
# Письма, яких в українській лекції не буває: CJK, хангиль, кана, арабське,
# іврит, тайське, деванагарі. Латиниця сюди не входить — терміни й англійська.
_JUNK = re.compile(r"[֐-׿؀-ۿऀ-ॿ฀-๿"
                   r"぀-ヿ㐀-䶿一-鿿가-힯]")


def parse_rows(stamped: str) -> list[tuple[int, str]]:
    out = []
    for line in stamped.splitlines():
        m = _ROW.match(line)
        if m:
            out.append((int(m.group(1)) * 60 + int(m.group(2)), m.group(3).strip()))
    return out


def parse_silences(ffmpeg_stderr: str, duration: float) -> list[tuple[float, float]]:
    """Інтервали тиші з виводу silencedetect. Незакрита тиша триває до кінця файлу."""
    out, start = [], None
    for line in ffmpeg_stderr.splitlines():
        m = re.search(r"silence_start: (-?[\d.]+)", line)
        if m:
            start = max(0.0, float(m.group(1)))
            continue
        m = re.search(r"silence_end: ([\d.]+)", line)
        if m and start is not None:
            out.append((start, float(m.group(1))))
            start = None
    if start is not None:
        out.append((start, duration))
    return out


def words_per_minute(rows: list[tuple[int, str]], duration: float) -> list[float]:
    """Слова кожного сегмента розподіляються рівномірно на його проміжок
    [start, наступний start), не довше 30 с — так довгі 25-30-с сегменти
    режиму E не створюють хибних «порожніх» хвилин."""
    n = int(duration // 60) + 1
    wpm = [0.0] * n
    for i, (s, text) in enumerate(rows):
        w = len(text.split())
        if not w:
            continue
        nxt = rows[i + 1][0] if i + 1 < len(rows) else s + 30
        e = min(max(nxt, s + 1), s + 30)
        span = e - s
        t = s
        while t < e:
            m = int(t // 60)
            step = min(e, (m + 1) * 60) - t
            if m < n:
                wpm[m] += w * step / span
            t += step
    return wpm


def sound_per_minute(silences: list[tuple[float, float]], duration: float) -> list[float]:
    n = int(duration // 60) + 1
    silent = [0.0] * n
    for a, b in silences:
        t = a
        while t < b:
            m = int(t // 60)
            step = min(b, (m + 1) * 60) - t
            if m < n:
                silent[m] += step
            t += step
    out = []
    for m in range(n):
        length = min(60.0, duration - m * 60)
        out.append(0.0 if length <= 0 else max(0.0, 1 - silent[m] / length))
    return out


def find_holes(wpm: list[float], sound: list[float]) -> list[tuple[int, int]]:
    """Серії ≥HOLE_RUN_MIN хвилин зі звуком, але без тексту: [(перша, остання+1)]."""
    holes, run = [], None
    for m, (w, s) in enumerate(zip(wpm, sound)):
        bad = s >= HOLE_SOUND_MIN and w < HOLE_WORDS_MAX
        if bad and run is None:
            run = m
        elif not bad and run is not None:
            if m - run >= HOLE_RUN_MIN:
                holes.append((run, m))
            run = None
    if run is not None and len(wpm) - run >= HOLE_RUN_MIN:
        holes.append((run, len(wpm)))
    return holes


def repeat_runs(rows: list[tuple[int, str]]) -> list[tuple[int, int]]:
    """Серії однакових сусідніх рядків, схожі на петлю: [(секунда початку, довжина)]."""
    out, i = [], 0
    norm = [" ".join(re.findall(r"\w+", t.lower())) for _, t in rows]
    while i < len(rows):
        j = i
        while j + 1 < len(rows) and norm[j + 1] and norm[j + 1] == norm[i]:
            j += 1
        need = REPEAT_RUN_WARN if len(norm[i].split()) >= REPEAT_MIN_WORDS else REPEAT_RUN_SHORT
        if norm[i] and j - i + 1 >= need:
            out.append((rows[i][0], j - i + 1))
        i = j + 1
    return out


def assess(stamped: str, duration: float, silences: list[tuple[float, float]] | None,
           fallback: int | None = None) -> dict:
    """Чиста функція оцінки. silences=None — звукова доріжка не аналізувалась
    (ffmpeg упав), тоді дірки не рахуються, а не рахуються «всюди»."""
    rows = parse_rows(stamped)
    words = sum(len(t.split()) for _, t in rows)
    minutes = max(duration / 60, 1e-9)
    holes = []
    if silences is not None and duration > 0:
        holes = find_holes(words_per_minute(rows, duration), sound_per_minute(silences, duration))
    reps = repeat_runs(rows)
    junk = sum(1 for _, t in rows if _JUNK.search(t))
    beyond = sum(1 for s, _ in rows if duration > 0 and s > duration + 1)
    warns = []
    if holes:
        warns.append("дірки " + ", ".join(f"{a:02d}:00-{b:02d}:00" for a, b in holes)
                     + f" ({sum(b - a for a, b in holes)} хв зі звуком без тексту)")
    if reps:
        warns.append("петлі " + ", ".join(f"{s // 60:02d}:{s % 60:02d}×{n}" for s, n in reps[:5]))
    if junk >= JUNK_WARN:
        warns.append(f"сміття {junk} рядк.")
    if beyond:
        warns.append(f"{beyond} рядк. за кінцем аудіо")
    return {
        "words": words,
        "wpm": round(words / minutes, 1),
        "hole_minutes": sum(b - a for a, b in holes),
        "holes": holes,
        "repeat_runs": len(reps),
        "junk": junk,
        "beyond_end": beyond,
        "fallback": fallback,
        "audio_checked": silences is not None,
        "warnings": warns,
    }


def detect_silences(wav: Path, duration: float) -> list[tuple[float, float]] | None:
    try:
        r = subprocess.run(
            ["nice", "-n", "15", "ffmpeg", "-hide_banner", "-nostdin", "-i", str(wav), "-af",
             f"silencedetect=noise={SILENCE_NOISE_DB}:d={SILENCE_MIN_DUR}", "-f", "null", "-"],
            capture_output=True, text=True, timeout=900)
    except (OSError, subprocess.TimeoutExpired):
        return None
    if r.returncode != 0:
        return None
    return parse_silences(r.stderr, duration)


def summary(q: dict) -> str:
    """Рядок для метаданих транскрипту."""
    if q["warnings"]:
        return "⚠ " + "; ".join(q["warnings"])
    return f"ok ({q['wpm']:.0f} слів/хв)" + ("" if q["audio_checked"] else ", звук не перевірено")


if __name__ == "__main__":
    # Ручна перевірка: transcript_quality.py <out.json або .md> <wav>
    src, wav = Path(sys.argv[1]), Path(sys.argv[2])
    text = src.read_text(encoding="utf-8")
    stamped = json.loads(text)["stamped"] if src.suffix == ".json" else text
    r = subprocess.run(["ffprobe", "-v", "error", "-show_entries", "format=duration",
                        "-of", "default=noprint_wrappers=1:nokey=1", str(wav)],
                       capture_output=True, text=True)
    dur = float(r.stdout.strip())
    q = assess(stamped, dur, detect_silences(wav, dur))
    print(json.dumps(q, ensure_ascii=False))
    print(summary(q))
