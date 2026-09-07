#!/usr/bin/env python3
"""Тести логіки process.py і yt_upload.py, які не потребують ані мережі, ані моделі.

Запускати на VPS venv'ом: ~/lectures/venv/bin/python tests/test_process.py
LECTURES_HOME підміняється на тимчасову теку, тому бойові ~/lectures не чіпаються.
"""
import os
import sys
import tempfile
from datetime import datetime
from pathlib import Path

TMP = tempfile.mkdtemp(prefix="lecture-test-")
os.environ["LECTURES_HOME"] = TMP
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "vps" / "bin"))

import process          # noqa: E402
import yt_upload        # noqa: E402
from googleapiclient.errors import HttpError  # noqa: E402

D = datetime(2026, 9, 8, 14, 15)
CISCO = "Комп'ютерні мережі та безпека за технологіями CISCO"
ok = fail = 0


def check(name, cond, detail=""):
    global ok, fail
    if cond:
        ok += 1
        print(f"PASS {name}")
    else:
        fail += 1
        print(f"FAIL {name}  {detail}")


# --- make_title -------------------------------------------------------------
t = process.make_title("Математичний аналіз. Частина 3", "числові ряди", "лекція", D)
check("лекція не отримує мітки типу", t == "Математичний аналіз. Частина 3 — числові ряди (08.09.2026)", t)

t = process.make_title("Математичний аналіз. Частина 3", "друга ознака порівняння", "практика", D)
check("практика отримує мітку",
      t == "Математичний аналіз. Частина 3 — друга ознака порівняння (практика, 08.09.2026)", t)

t = process.make_title("Основи теорії кіл", "метод контурних струмів", "лабораторна", D)
check("лабораторна отримує мітку",
      t == "Основи теорії кіл — метод контурних струмів (лабораторна, 08.09.2026)", t)

t = process.make_title("Основи теорії кіл", "перехідні процеси", "невідомо", D)
check("невідомий тип мітки не отримує",
      t == "Основи теорії кіл — перехідні процеси (08.09.2026)", t)

# найгірший випадок: найдовша дисципліна + максимально дозволена схемою тема (70)
long_topic = "х" * 70
for kind in ("лекція", "практика", "лабораторна"):
    t = process.make_title(CISCO, long_topic, kind, D)
    check(f"довга назва ({kind}) вкладається в 100", len(t) <= process.TITLE_MAX, f"{len(t)}: {t}")
    check(f"довга назва ({kind}) зберігає дату", t.endswith("08.09.2026)"), t)

t = process.make_title(CISCO, long_topic, "практика", D)
check("тема обрізана з трьома крапками", "…" in t, t)
check("мітка типу вціліла в довгій назві", "(практика, 08.09.2026)" in t, t)
check("двокрапка не переживає safe_name — мітки в дужках", ":" not in t, t)

# межа: рівно стільки, щоб не різати
exact = process.make_title("Загальна фізика. Частина 2", "магнетизм", "лекція", D)
check("коротка назва не чіпається", "…" not in exact, exact)

# --- розпізнавання вичерпаної квоти -----------------------------------------
class _Resp:
    def __init__(self, status):
        self.status = status
        self.reason = "quota"


def http_error(status, reason):
    body = ('{"error":{"code":%d,"errors":[{"reason":"%s"}]}}' % (status, reason)).encode()
    return HttpError(_Resp(status), body)


check("403 quotaExceeded — це квота", yt_upload._is_quota(http_error(403, "quotaExceeded")))
check("429 rateLimitExceeded — це квота", yt_upload._is_quota(http_error(429, "rateLimitExceeded")))
check("403 forbidden — НЕ квота", not yt_upload._is_quota(http_error(403, "forbidden")))
check("404 notFound — НЕ квота", not yt_upload._is_quota(http_error(404, "videoNotFound")))
check("500 — НЕ квота (це retriable)", not yt_upload._is_quota(http_error(500, "backendError")))
check("QuotaExceeded не ловиться як HttpError",
      issubclass(yt_upload.QuotaExceeded, RuntimeError)
      and not issubclass(yt_upload.QuotaExceeded, HttpError))


class _Req:
    def __init__(self, exc):
        self.exc = exc

    def execute(self):
        raise self.exc


try:
    yt_upload._run(_Req(http_error(403, "quotaExceeded")))
    check("_run перетворює квоту на QuotaExceeded", False, "виняток не кинуто")
except yt_upload.QuotaExceeded:
    check("_run перетворює квоту на QuotaExceeded", True)
except Exception as e:
    check("_run перетворює квоту на QuotaExceeded", False, repr(e))

try:
    yt_upload._run(_Req(http_error(404, "videoNotFound")))
    check("_run пропускає звичайні помилки далі", False, "виняток не кинуто")
except yt_upload.QuotaExceeded:
    check("_run пропускає звичайні помилки далі", False, "прийняв за квоту")
except HttpError:
    check("_run пропускає звичайні помилки далі", True)

print(f"\nпройдено {ok}/{ok + fail}")
sys.exit(0 if fail == 0 else 1)
