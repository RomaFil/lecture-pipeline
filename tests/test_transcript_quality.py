#!/usr/bin/env python3
"""Тести transcript_quality.py — чиста логіка, без ffmpeg і whisper.

Запуск на VPS: PYTHONPATH=bin ~/lectures/venv/bin/python tests/test_transcript_quality.py
"""
import sys
from pathlib import Path

for _p in (Path(__file__).resolve().parents[1] / "vps" / "bin",
           Path(__file__).resolve().parents[1] / "bin"):
    sys.path.insert(0, str(_p))

import transcript_quality as tq  # noqa: E402

ok = fail = 0


def check(name, cond, detail=""):
    global ok, fail
    if cond:
        ok += 1
        print(f"PASS {name}")
    else:
        fail += 1
        print(f"FAIL {name}  {detail}")


def stamp(rows):
    return "\n".join(f"[{s // 60:02d}:{s % 60:02d}] {t}" for s, t in rows)


SENT = "Тут викладач пояснює щось важливе про струм і напругу"  # 10 слів + «номер N» = 12


def speech(a_min, b_min, every=6):
    """Рівне мовлення: різні рядки по 12 слів кожні 6 с = 120 слів/хв
    (однакові рядки детектор справедливо вважав би петлею)."""
    return [(s, f"{SENT} номер {s}") for s in range(a_min * 60, b_min * 60, every)]


# --- parse_silences ------------------------------------------------------------
ERR = """[silencedetect @ 0x1] silence_start: 60.5
[silencedetect @ 0x1] silence_end: 125 | silence_duration: 64.5
size=N/A
[silencedetect @ 0x1] silence_start: -0.01
[silencedetect @ 0x1] silence_end: 3.0 | silence_duration: 3
[silencedetect @ 0x1] silence_start: 590
"""
sil = tq.parse_silences(ERR, 600.0)
check("parse_silences: пари start/end, від'ємний старт -> 0, незакрита -> до кінця",
      sil == [(60.5, 125.0), (0.0, 3.0), (590.0, 600.0)], sil)
check("parse_silences: порожній вивід -> []", tq.parse_silences("", 100.0) == [])

# --- норма: мовлення всю пару -> без попереджень ---------------------------------
DUR = 30 * 60.0
q = tq.assess(stamp(speech(0, 30)), DUR, [])
check("норма: без попереджень", q["warnings"] == [], q)
check("норма: ~120 слів/хв", 110 <= q["wpm"] <= 130, q["wpm"])
check("норма: summary 'ok'", tq.summary(q).startswith("ok"), tq.summary(q))

# --- дірка: звук є, тексту нема 10 хв (клас temperature=0 27.09) -----------------
rows = speech(0, 10) + speech(20, 30)
q = tq.assess(stamp(rows), DUR, [])
check("дірка: знайдена рівно 10:00-20:00", q["holes"] == [(10, 20)], q["holes"])
check("дірка: 10 хв", q["hole_minutes"] == 10, q["hole_minutes"])
check("дірка: попередження з часом", any("10:00-20:00" in w for w in q["warnings"]), q["warnings"])
check("дірка: summary з ⚠", tq.summary(q).startswith("⚠"), tq.summary(q))

# --- та сама пауза, але тиша -> не дірка (перерва, самостійна робота) ------------
q = tq.assess(stamp(rows), DUR, [(600.0, 1200.0)])
check("перерва в тиші: не дірка", q["hole_minutes"] == 0 and q["warnings"] == [], q)

# --- коротка пауза < HOLE_RUN_MIN хв зі звуком -> не дірка ----------------------
rows2 = speech(0, 10) + speech(12, 30)
q = tq.assess(stamp(rows2), DUR, [])
check("2 хв без тексту: ще не дірка", q["hole_minutes"] == 0, q["holes"])

# --- довгі сегменти режиму E (по 30 с) не дають хибних дірок --------------------
long_rows = [(s, " ".join([SENT] * 5) + f" номер {s}") for s in range(0, 1800, 30)]  # 52 слова / 30 с
q = tq.assess(stamp(long_rows), DUR, [])
check("сегменти по 30 с: без дірок", q["hole_minutes"] == 0, q["holes"])

# --- дірка в кінці запису (петля до кінця шматка) --------------------------------
q = tq.assess(stamp(speech(0, 20)), DUR, [])
check("дірка до кінця запису знайдена", q["holes"] == [(20, 31)] or q["holes"] == [(20, 30)], q["holes"])

# --- silences=None: звук не перевірено -> дірок не рахуємо -----------------------
q = tq.assess(stamp(rows), DUR, None)
check("без аналізу звуку: дірки не вигадуються", q["hole_minutes"] == 0 and not q["audio_checked"], q)
check("без аналізу звуку: summary про це каже", "звук не перевірено" in tq.summary(q), tq.summary(q))

# --- петлі ----------------------------------------------------------------------
loop = speech(0, 5) + [(300 + i, "і от якраз лінкер він дозволяє приєднати") for i in range(6)] \
    + speech(6, 30)
q = tq.assess(stamp(loop), DUR, [])
check("петля: 6 однакових довгих рядків знайдено", q["repeat_runs"] == 1, q)
check("петля: у попередженні час і довжина", any("05:00×6" in w for w in q["warnings"]), q["warnings"])

bye = speech(0, 29) + [(29 * 60 + i, "До побачення.") for i in range(5)]
q = tq.assess(stamp(bye), DUR, [])
check("«До побачення.» ×5 (коротка фраза) — не петля", q["repeat_runs"] == 0, q)

short_loop = speech(0, 20) + [(1200 + i, "Так.") for i in range(12)] + speech(21, 30)
q = tq.assess(stamp(short_loop), DUR, [])
check("коротка фраза ×12 — петля", q["repeat_runs"] == 1, q)

three = speech(0, 5) + [(300 + i, "Дописую формулу для струму в колі") for i in range(3)] + speech(6, 30)
q = tq.assess(stamp(three), DUR, [])
check("подвійне/потрійне диктування (×3) — не петля", q["repeat_runs"] == 0, q)

# --- сміття ------------------------------------------------------------------------
junk = speech(0, 30) + [(100, "До конца 해요 existenci."), (200, "字幕"), (300, "ありがとう")]
q = tq.assess(stamp(sorted(junk)), DUR, [])
check("сміття: 3 рядки з хангилем/CJK/каною", q["junk"] == 3 and any("сміття" in w for w in q["warnings"]), q)
lat = speech(0, 30) + [(100, "Компілятор GCC, Executable and Linkable Format")]
q = tq.assess(stamp(sorted(lat)), DUR, [])
check("латиниця (терміни) — не сміття", q["junk"] == 0, q)

# --- рядки за кінцем аудіо ----------------------------------------------------------
q = tq.assess(stamp(speech(0, 30) + [(1900, "петля за кінцем")]), DUR, [])
check("рядок за кінцем аудіо помічено", q["beyond_end"] == 1, q)

# --- fallback лише передається, не оцінюється --------------------------------------
q = tq.assess(stamp(speech(0, 30)), DUR, [], fallback=7)
check("fallback у результаті", q["fallback"] == 7, q)

# --- порожній транскрипт не падає ------------------------------------------------
q = tq.assess("", DUR, [])
check("порожній транскрипт: не падає, дірка на всю пару", q["hole_minutes"] >= 30, q)

print(f"\nпройдено {ok}/{ok + fail}")
sys.exit(0 if fail == 0 else 1)
