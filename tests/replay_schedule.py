#!/usr/bin/env python3
"""Офлайн-оцінка підказки за розкладом на голосах, зібраних collect_votes.py.

    replay_schedule.py votes.jsonl

Без викликів моделі. Для кожного запису порівнює рішення ДО підказки (більшість 2/3
або _needs-review) і ПІСЛЯ (classify._apply_schedule) з правильною дисципліною.
Підсумок розділяє три різні результати, бо вони не рівноцінні:
  * правильно;
  * у _needs-review (ручний розбір, але без шкоди);
  * МОВЧКИ ХИБНО (чужий плейлист без жодного сигналу) — саме це має зменшитись.

УВАГА щодо чесності оцінки: таблиця timetable.SCHEDULE побудована з тих самих
записів, тож це оцінка ЗА ЗРАЗКОМ (in-sample) — вона показує, що механізм
працює, а не гарантує таку саму точність на нових тижнях. Справжня перевірка —
перші тижні бойової роботи (у логу raw.schedule_note).
"""
import json
import os
import sys
import types
from datetime import datetime

sys.path.insert(0, os.environ.get("LECTURES_BIN", os.path.join(
    os.path.dirname(os.path.abspath(__file__)), "..", "vps", "bin")))
try:
    import requests  # noqa: F401
except ImportError:                       # classify імпортує requests, а тут модель не потрібна
    sys.modules["requests"] = types.ModuleType("requests")
import classify as c  # noqa: E402
import timetable as t  # noqa: E402

D = datetime
# Інтервали записів (старт, кінець): OBS-записи — зі state.db (старт з імені файлу);
# записи 10.09/17.09 з ОТК — скачані з групового каналу (mp4), інтервали — за парами.
INTERVALS = {
    "2026-09-03_12_02_phys.md": (D(2026, 9, 3, 10, 28), D(2026, 9, 3, 12, 2)),
    "2026-09-03_13_26_mat.md": (D(2026, 9, 3, 12, 22), D(2026, 9, 3, 13, 26)),
    "2026-09-03_16_03_otk.md": (D(2026, 9, 3, 14, 18), D(2026, 9, 3, 16, 3)),
    "2026-09-03_17_53_otk.md": (D(2026, 9, 3, 16, 17), D(2026, 9, 3, 17, 53)),
    "2026-09-08_14_54_mat.md": (D(2026, 9, 8, 14, 17), D(2026, 9, 8, 14, 54)),
    "2026-09-10_09_43_sch.md": (D(2026, 9, 10, 8, 29), D(2026, 9, 10, 9, 43)),
    "2026-09-10_11_58_phys.md": (D(2026, 9, 10, 10, 25), D(2026, 9, 10, 11, 58)),
    "2026-09-10_13_13_mat.md": (D(2026, 9, 10, 12, 20), D(2026, 9, 10, 13, 13)),
    "2026-09-10_14_15_otk.md": (D(2026, 9, 10, 14, 15), D(2026, 9, 10, 15, 40)),
    "2026-09-10_16_16_otk.md": (D(2026, 9, 10, 16, 10), D(2026, 9, 10, 17, 35)),
    "2026-09-12_10_22_otk.md": (D(2026, 9, 12, 8, 40), D(2026, 9, 12, 10, 22)),
    "2026-09-12_16_37_otk.md": (D(2026, 9, 12, 14, 30), D(2026, 9, 12, 16, 37)),
    "2026-09-15_10_31_otk.md": (D(2026, 9, 15, 8, 45), D(2026, 9, 15, 10, 31)),
    "2026-09-15_12_23_otk.md": (D(2026, 9, 15, 10, 53), D(2026, 9, 15, 12, 23)),
    "2026-09-15_15_26_mat.md": (D(2026, 9, 15, 14, 18), D(2026, 9, 15, 15, 26)),
    "2026-09-17_08_30_sch.md": (D(2026, 9, 17, 8, 30), D(2026, 9, 17, 9, 55)),
    "2026-09-17_11_55_phys.md": (D(2026, 9, 17, 10, 26), D(2026, 9, 17, 11, 55)),
    "2026-09-17_13_23_mat.md": (D(2026, 9, 17, 12, 22), D(2026, 9, 17, 13, 23)),
    "2026-09-17_18_17_otk.md": (D(2026, 9, 17, 14, 20), D(2026, 9, 17, 16, 50)),
    "2026-09-17_19_58_otk.md": (D(2026, 9, 17, 17, 0), D(2026, 9, 17, 18, 30)),
    "2026-09-19_14_21_eng.md": (D(2026, 9, 19, 12, 21), D(2026, 9, 19, 14, 21)),
}


def outcome(pick, truth):
    return "ok" if pick == truth else ("review" if pick is None else "WRONG")


def main(path):
    rows = [json.loads(ln) for ln in open(path, encoding="utf-8") if ln.strip()]
    before = {"ok": 0, "review": 0, "WRONG": 0}
    after = dict(before)
    n = 0
    for r in rows:
        if r["file"] not in INTERVALS or len(r["results"]) != 3:
            continue
        n += 1
        votes = [x["subject"] for x in r["results"]]
        exp = t.expected_subjects(*INTERVALS[r["file"]])
        win0, cnt0 = c._tally(votes)
        old = win0 if cnt0 >= 2 and win0 in c.SUBJECTS else None
        win1, cnt1, note = c._apply_schedule(votes, exp)
        new = win1 if win1 in c.SUBJECTS and (cnt1 >= 2 or note) else None
        o0, o1 = outcome(old, r["truth_subject"]), outcome(new, r["truth_subject"])
        before[o0] += 1
        after[o1] += 1
        flag = "" if o0 == o1 else f"   <-- {o0} → {o1}"
        print(f"{r['file'][:26]:26} правда={r['truth_subject']:13} голоси={[v[:5] for v in votes]} "
              f"розклад={sorted(x[:5] for x in exp)}  {o0:6} → {o1:6}{flag}")
    print(f"\nЗаписів: {n}")
    print(f"  ДО підказки:    правильно {before['ok']}, у _needs-review {before['review']}, "
          f"МОВЧКИ ХИБНО {before['WRONG']}")
    print(f"  ПІСЛЯ підказки: правильно {after['ok']}, у _needs-review {after['review']}, "
          f"МОВЧКИ ХИБНО {after['WRONG']}")


if __name__ == "__main__":
    main(sys.argv[1] if len(sys.argv) > 1 else "votes.jsonl")
