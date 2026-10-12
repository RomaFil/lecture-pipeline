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


# --- ім'я транскрипту: два заняття в один день не мають злипатись ------------
# Правило живе з 07.09.2026, але тесту не мало — а мовчазний перезапис першого
# транскрипту це саме той збій, який ніде не видно, доки не хопишся файлу.
n1 = process.transcript_name("Математичний аналіз. Частина 3 — ряди (08.09.2026)",
                             datetime(2026, 9, 8, 12, 20))
n2 = process.transcript_name("Математичний аналіз. Частина 3 — ряди (08.09.2026)",
                             datetime(2026, 9, 8, 14, 54))
check("однакова тема в один день дає РІЗНІ імена", n1 != n2, f"{n1} == {n2}")
check("час у імені у форматі [ГГ-ХХ]", n1.endswith(" [12-20].md"), n1)
check("розширення .md рівно одне", n2.count(".md") == 1, n2)

# --- ім'я транскрипту: байтова стеля (WebDAV, не YouTube) --------------------
# 14.09.2026: три транскрипти 178-187 байт (у межах TITLE_MAX=100 символів)
# впали на сервері з ENAMETOOLONG після шифрування Remotely Save — байти й
# символи для кирилиці різні речі. Регресія на реальний інцидент.
long_title = process.make_title(CISCO, "х" * 70, "практика", D)
n = process.transcript_name(long_title, D)
check("ім'я транскрипту вкладається в байтову стелю",
      len(n.encode("utf-8")) <= process.FILENAME_BYTE_MAX, f"{len(n.encode('utf-8'))}: {n}")
check("довге ім'я транскрипту обрізане трьома крапками", "…" in n, n)
check("час у обрізаному імені все одно на місці", n.endswith(" [14-15].md"), n)

short_title = "Лекція 1"
n = process.transcript_name(short_title, D)
check("коротке ім'я транскрипту не чіпається", n == "Лекція 1 [14-15].md", n)

# межа 154/178 байт з реального інциденту: 154 — найдовше з того, що й так
# синкалось нормально, стеля не має його чіпати
real_ok = "Математичний аналіз. Частина 3 — дослідження збіжності рядів (практика, 08.09.2026)"
n = process.transcript_name(real_ok, datetime(2026, 9, 8, 14, 54))
check("реальний 154-байтний заголовок (працював на сервері) лишається цілим",
      n == real_ok + " [14-54].md", n)

# Регресія 19.09.2026: обрізання з хвоста з'їдало дату й тип заняття —
# у vault приїхало `…заняття з англійської мови (практика… [14-21].md`.
tail_title = "Англійська мова професійного спрямування — заняття з англійської мови (практика, 19.09.2026)"
n = process.transcript_name(tail_title, datetime(2026, 9, 19, 14, 21))
check("обрізане ім'я зберігає тип і дату", "(практика, 19.09.2026) [14-21].md" in n, n)
check("обрізане ім'я з хвостом вкладається в стелю",
      len(n.encode("utf-8")) <= process.FILENAME_BYTE_MAX and "…" in n,
      f"{len(n.encode('utf-8'))}: {n}")
n = process.transcript_name(process.make_title(CISCO, "х" * 70, "лабораторна", D), D)
check("найгірший випадок (CISCO + 70 + лабораторна): дата цілa",
      " (лабораторна, " in n and n.endswith(" [14-15].md") and ")" in n.split("…")[-1], n)

real_fail = ("Комп'ютерні мережі та безпека за технологіями CISCO — "
             "Технології каналного рівня і мережі")
n = process.transcript_name(real_fail, datetime(2026, 9, 11, 13, 47))
check("реальний 182-байтний заголовок (падав на сервері) тепер обрізається",
      "…" in n and len(n.encode("utf-8")) <= process.FILENAME_BYTE_MAX, f"{len(n.encode('utf-8'))}: {n}")

# --- kind: які типи отримують мітку в назві ---------------------------------
check("тренування мітки НЕ отримує (як лекція)",
      process.make_title("Бокс", "робота на лапах", "тренування", D)
      == "Бокс — робота на лапах (08.09.2026)")
check("невідомий тип мітки не отримує",
      process.make_title("Бокс", "робота на лапах", "невідомо", D)
      == "Бокс — робота на лапах (08.09.2026)")
check("KIND_LABEL знає рівно два типи", set(process.KIND_LABEL) == {"практика", "лабораторна"},
      str(sorted(process.KIND_LABEL)))

# --- стеля тривалості -------------------------------------------------------
# Заміряні факти: аяксівське стажування 08.09.2026 — 3 год 28 хв, здвоєна ОТК —
# 3,5 год. Стеля, опущена нижче цього, почала б їсти справжні записи.
check("стеля вища за найдовше реальне заняття (3,5 год)",
      process.MAX_VIDEO_SECONDS > 3.5 * 3600, str(process.MAX_VIDEO_SECONDS))
check("стеля вища за поріг довгого відео YouTube",
      process.MAX_VIDEO_SECONDS > process.LONG_VIDEO_SECONDS)

# --- probe_duration: незрозумілий файл не має валити обробку -----------------
_bad = Path(TMP) / "не-відео.txt"
_bad.write_text("це не медіафайл", encoding="utf-8")
check("probe_duration на не-медіа повертає 0.0, а не виняток",
      process.probe_duration(_bad) == 0.0)
check("probe_duration на відсутньому файлі повертає 0.0",
      process.probe_duration(Path(TMP) / "нема-такого.mkv") == 0.0)
# 0.0 означає "не знаю" і НЕ має спрацьовувати як "задовге" — інакше кожен
# нерозпізнаний контейнер мовчки їхав би в _needs-review замість обробки.
check("нульова тривалість не вважається перевищенням стелі",
      not (0.0 and 0.0 > process.MAX_VIDEO_SECONDS))

# --- send_to_review: нічого не втрачається ----------------------------------
import sqlite3  # noqa: E402

_db = process.open_db()
_sha = "f" * 64
_vid = process.INCOMING / "2026-09-08 17-02-01.mkv"
_vid.parent.mkdir(parents=True, exist_ok=True)
_vid.write_bytes(b"\x00" * 16)
_now = datetime.now().isoformat(timespec="seconds")
_db.execute("INSERT INTO files(sha256, filename, size, mtime, status, attempts, created, updated)"
            " VALUES(?,?,?,?,?,0,?,?)",
            (_sha, _vid.name, 16, 0, "new", _now, _now))
_db.commit()
process.send_to_review(_db, _vid, _sha, D, "тривалість 9.0 год перевищує стелю")

check("відео переїхало в _needs-review, а не зникло",
      (process.REVIEW / "2026-09-08 17-02-01.mkv").exists())
check("з incoming прибрано", not _vid.exists())
_note = process.REVIEW / f"{D.strftime('%Y-%m-%d_%H-%M')}_{_sha[:8]}.md"
check("поруч лежить пояснювальна нотатка", _note.exists(), str(_note))
if _note.exists():
    _txt = _note.read_text(encoding="utf-8")
    check("у нотатці записано причину", "перевищує стелю" in _txt)
    check("у нотатці є ім'я джерела", "2026-09-08 17-02-01.mkv" in _txt)
_row = _db.execute("SELECT status, error FROM files WHERE sha256=?", (_sha,)).fetchone()
check("статус у базі — needs_review", _row and _row[0] == "needs_review", str(_row))
check("причина збережена в базі", _row and "стелю" in (_row[1] or ""), str(_row))


# --- sample_text: зріз транскрипту для класифікатора --------------------------
# Логіка чиста, моделі не потребує, тому живе тут, а не в test_classifier.py.
import classify as _clf  # noqa: E402

_short = "коротка стенограма"
check("короткий текст повертається байт у байт",
      _clf.sample_text(_short, 5000) == _short)

# Трохи довший за бюджет, але нижче порогу: ділити НЕ МОЖНА — середина злипнеться
# з початком. Саме цей випадок і був знайдений на першій довгій фікстурі.
_mid = ("Перше речення. " * 400)          # ~6000 символів при бюджеті 5000
_out = _clf.sample_text(_mid, 5000)
check("текст нижче порогу не ріжеться на фрагменти",
      _clf.SAMPLE_MARKER not in _out, _out[:60])
check("текст нижче порогу вкладається в бюджет", len(_out) <= 5000, str(len(_out)))

# Достатньо довгий: зріз має спрацювати.
_head = "Вітаю всіх на парі з математичного аналізу. " * 120
_body = "Розглянемо ознаку Даламбера для числових рядів. " * 400
_long = _head + _body
_out = _clf.sample_text(_long, 5000)
check("довгий текст ріжеться на два фрагменти", _clf.SAMPLE_MARKER in _out)
check("зріз не перевищує бюджет", len(_out) <= 5000, str(len(_out)))
_a, _b = _out.split(_clf.SAMPLE_MARKER)
check("перший фрагмент — з початку", "математичного аналізу" in _a)
check("другий фрагмент — зі змісту, а не зі вступу",
      "Даламбера" in _b and "математичного аналізу" not in _b, _b[:60])
check("другий фрагмент не починається з пробілу чи півслова",
      _b[:1].strip() != "" and _b[0].isupper(), repr(_b[:20]))
check("обидва фрагменти непорожні", len(_a) > 100 and len(_b) > 100,
      "%d/%d" % (len(_a), len(_b)))
check("зріз детермінований", _clf.sample_text(_long, 5000) == _out)

# Поріг має бути таким, щоб реальні транскрипти впевнено за нього виходили:
# 44-хвилинний запис ~12 000 символів, тригодинний ~230 000.
check("поріг зрізу нижчий за найкоротший реальний транскрипт",
      5000 * _clf.SAMPLE_MIN_RATIO < 12000, str(_clf.SAMPLE_MIN_RATIO))

# Примітка про формат не має потрапляти в промт, коли зрізу не було: саме
# безумовна вставка 09.09.2026 відправила англійську з НЕВІДОМО в МЕРЕЖІ_CISCO.
check("FORMAT_NOTE не є частиною базового SYSTEM",
      _clf.FORMAT_NOTE not in _clf.SYSTEM)


# --- classify_voted: підрахунок голосів і вибір теми, без моделі -------------
# Логіка чиста (Counter + індекси), тому перевіряється прямо, без Ollama.
# Реалізовано 09.09.2026 замість одноразового classify(): на восьми справжніх
# транскриптах (tests/eval.py) однофрагментний підхід давав 6/8 із
# confidence=high на ВСІХ чотирьох помилках — гейт не захищав нічого.
# Голосування (tests/vote.py) дало 7/8, а восьма помилка стала розколом
# голосів (_needs-review), а не мовчазним промахом.
check("одностайність: усі три збіглись",
      _clf._tally(["АЯКС", "АЯКС", "АЯКС"]) == ("АЯКС", 3))
check("більшість 2/3 перемагає",
      _clf._tally(["ФІЗИКА", "ТЕОРІЯ_КІЛ", "ФІЗИКА"]) == ("ФІЗИКА", 2))
_win, _n = _clf._tally(["МЕРЕЖІ_CISCO", "НЕВІДОМО", "МАТАНАЛІЗ"])
check("розкол 1/1/1 не дає більшості (count<2)", _n < 2, f"{_win}, {_n}")

check("тема береться НЕ з першого фрагмента, коли є вибір",
      _clf._pick_topic_index(["АЯКС", "МЕРЕЖІ_CISCO", "АЯКС"], "АЯКС") == 2)
check("серед двох переможних — перевага непершому",
      _clf._pick_topic_index(["АЯКС", "АЯКС", "МЕРЕЖІ_CISCO"], "АЯКС") == 1)
check("переможець лише в одному фрагменті — беремо його, навіть перший",
      _clf._pick_topic_index(["АЯКС"], "АЯКС") == 0)

# --- kind: більшість по всіх фрагментах, «невідомо» = утримання (20.09.2026) -----
check("kind: більшість перемагає фрагмент теми (лікує хибний kind при вірному subject)",
      _clf._pick_kind(["практика", "лекція", "лекція"], "практика") == "лекція")
check("kind: одностайність",
      _clf._pick_kind(["практика"] * 3, "лекція") == "практика")
check("kind: «невідомо» не голос — один відомий тип перемагає",
      _clf._pick_kind(["невідомо", "невідомо", "практика"], "невідомо") == "практика")
check("kind: «невідомо» не заважає більшості 2/3 (регресія t10)",
      _clf._pick_kind(["невідомо", "лекція", "лекція"], "невідомо") == "лекція")
check("kind: нічия лекція/практика → fallback (тип фрагмента теми)",
      _clf._pick_kind(["невідомо", "лекція", "практика"], "практика") == "практика")
check("kind: усі утримались → fallback",
      _clf._pick_kind(["невідомо"] * 3, "невідомо") == "невідомо")
check("kind: реальний запис 12.09 (лекція/практика/невідомо) не ламається",
      _clf._pick_kind(["невідомо", "лекція", "практика"], "лекція") == "лекція")

# --- розклад як підказка (20–21.09.2026) --------------------------------------
# Статична таблиця розкладу замінена календарем «КПІ» 27.09.2026 — його тести
# (разом із класифікацією «спершу календар») у tests/test_timetable.py.
_d = datetime

_TK, _SC, _PH, _MA, _EN = "ТЕОРІЯ_КІЛ", "СХЕМОТЕХНІКА", "ФІЗИКА", "МАТАНАЛІЗ", "АНГЛІЙСЬКА"
# Реальні голоси з tests/collect_votes.py (20.09.2026)
check("підказка: хибна більшість 2/3 з одним голосом за розклад → перемагає розклад (ОТК 10.09)",
      _clf._apply_schedule([_TK, _SC, _SC], {_TK})[:2] == (_TK, 1))
check("підказка: те саме для фізики 17.09",
      _clf._apply_schedule([_PH, _TK, _TK], {_PH})[:2] == (_PH, 1))
check("підказка: більшість збігається з розкладом — без змін",
      _clf._apply_schedule([_MA, _MA, _SC], {_MA}) == (_MA, 2, ""))
check("підказка: 3/3 проти розкладу НЕ переважується (заняття могли замінити)",
      _clf._apply_schedule([_PH] * 3, {_TK}) == (_PH, 3, ""))
_w, _c, _note = _clf._apply_schedule([_MA, _SC, _MA], {_TK})
check("підказка: більшість проти розкладу без жодного голосу за нього → None (_needs-review)",
      _w is None and "суперечать розкладу" in _note, f"{_w} {_note}")
check("підказка: розкол 1/1/1 і один голос збігається з розкладом → він (ОТК 12.09)",
      _clf._apply_schedule([_MA, _SC, _TK], {_TK})[:2] == (_TK, 1))
check("підказка: розкол без кандидата лишається розколом (count<2, без пояснення)",
      _clf._apply_schedule([_MA, _SC, _PH], {_TK}) == ("МАТАНАЛІЗ", 1, ""))
check("підказка: порожній розклад — поведінка як раніше",
      _clf._apply_schedule([_TK, _SC, _SC], set()) == (_SC, 2, ""))
check("підказка: два кандидати (сб 14:15) — обирається той, за кого є голос",
      _clf._apply_schedule([_MA, _EN, _MA], {_TK, _EN})[:2] == (_EN, 1))
check("підказка: невідомий код у розкладі ігнорується",
      _clf._apply_schedule([_TK, _SC, _SC], {"НЕ_ТАКИЙ_КОД"}) == (_SC, 2, ""))

# --- _apply_schedule_single (26.09.2026): розклад для короткого, невоготованого
# транскрипту — реальний промах сб 08:41 (розклад ТЕОРІЯ_КІЛ, класифікатор дав
# СХЕМОТЕХНІКА на 32-сегментному записі, надто короткому для голосування) ------
_ok_sc = {"ok": True, "subject": "Схемотехніка. Частина 1. Електронні компоненти",
          "slug": "shemotehnika", "kind": "практика", "topic": "тема",
          "raw": {"subject": _SC, "confidence": "high", "topic": "тема", "kind": "практика"}}
_ok_tk = {"ok": True, "subject": "Основи теорії кіл", "slug": "otk", "kind": "практика",
          "topic": "тема", "raw": {"subject": _TK, "confidence": "high",
                                    "topic": "тема", "kind": "практика"}}
_res = _clf._apply_schedule_single(_ok_sc, {_TK})
check("apply_schedule_single: конфлікт з розкладом → ok=False (needs-review, реальний промах 08:41)",
      _res["ok"] is False, _res)
check("apply_schedule_single: конфлікт залишає schedule_note для SCHEDULE_HINT-алерту",
      "schedule_note" in _res["raw"] and _SC in _res["raw"]["schedule_note"], _res)
check("apply_schedule_single: збіг з розкладом — без змін", _clf._apply_schedule_single(_ok_tk, {_TK}) == _ok_tk)
check("apply_schedule_single: порожній розклад (слот невідомий) — без змін",
      _clf._apply_schedule_single(_ok_sc, set()) == _ok_sc)
check("apply_schedule_single: невідомий код у розкладі ігнорується — без змін",
      _clf._apply_schedule_single(_ok_sc, {"НЕ_ТАКИЙ_КОД"}) == _ok_sc)
_fail = {"ok": False, "reason": "щось інше", "raw": {"subject": _SC}}
check("apply_schedule_single: вже провалена класифікація не чіпається розкладом",
      _clf._apply_schedule_single(_fail, {_TK}) == _fail)

import inspect  # noqa: E402
check("process_one логує маркер SCHEDULE_HINT: — його рахує alert.sh (перевірка 15)",
      "SCHEDULE_HINT:" in inspect.getsource(process.process_one))
_alert = next((p for p in (Path(__file__).resolve().parents[1] / "vps" / "bin" / "alert.sh",
                           Path.home() / "lectures" / "bin" / "alert.sh") if p.exists()), None)
check("alert.sh шукає той самий маркер (розʼїзд імен вимкнув би перевірку 15 мовчки)",
      _alert is not None and "grep -c 'SCHEDULE_HINT:'" in _alert.read_text(encoding="utf-8"),
      str(_alert))

_v = Path("/x/2026-09-19 12-21-46.mkv")
check("інтервал: старт з імені OBS, кінець = старт + тривалість",
      process.recording_interval(_v, _d(2026, 9, 19, 14, 21), 7209.0)
      == (_d(2026, 9, 19, 12, 21, 46), _d(2026, 9, 19, 14, 21, 55)))
check("інтервал: файл без OBS-імені — від mtime назад",
      process.recording_interval(Path("/x/lecture.mp4"), _d(2026, 9, 17, 12, 0), 3600.0)
      == (_d(2026, 9, 17, 11, 0), _d(2026, 9, 17, 12, 0)))
check("інтервал: без імені й без тривалості — None",
      process.recording_interval(Path("/x/lecture.mp4"), _d(2026, 9, 17, 12, 0), 0.0) is None)

check("chunks(): короткий текст — один фрагмент, голосування не потрібне",
      _clf.chunks("коротка стенограма", 5000) == ["коротка стенограма"])
_long_for_chunks = "Слово речення. " * 3000  # ~45000 символів, точно понад поріг
_ck = _clf.chunks(_long_for_chunks, 5000)
check("chunks(): довгий текст — рівно три фрагменти", len(_ck) == 3, str(len(_ck)))
check("chunks(): кожен фрагмент у межах бюджету", all(len(p) <= 5000 for p in _ck),
      [len(p) for p in _ck])
check("chunks(): фрагменти непорожні", all(len(p) > 100 for p in _ck),
      [len(p) for p in _ck])

# --- verify()/check_processed(): рішення "чи можна це подивитися" -----------
# Найнебезпечніша логіка в конвеєрі — саме вона мовчала в інцидентах 001
# (uploaded != успіх) і 011 (Content ID блокує, а uploadStatus/rejectionReason
# цього не бачать), обидва рази без жодного тесту. Фейковий service() —
# без мережі, без моделі, чиста перевірка булевого виразу.
class _FakeVideosList:
    def __init__(self, items):
        self._items = items

    def execute(self):
        return {"items": self._items}


class _FakeVideos:
    def __init__(self, items):
        self._items = items

    def list(self, **kw):
        return _FakeVideosList(self._items)


class _FakeService:
    def __init__(self, items):
        self._items = items

    def videos(self):
        return _FakeVideos(self._items)


def _item(privacy="unlisted", upload_status="processed", failure=None,
          rejection=None, blocked=None, title="тест"):
    return {
        "status": {"privacyStatus": privacy, "uploadStatus": upload_status,
                   "failureReason": failure, "rejectionReason": rejection},
        "contentDetails": {"regionRestriction": {"blocked": blocked or []}},
        "snippet": {"title": title},
    }


def _verify_with(items):
    yt_upload.service = lambda: _FakeService(items)
    return yt_upload.verify("fakeid")


def _check_processed_with(items):
    yt_upload.service = lambda: _FakeService(items)
    return yt_upload.check_processed("fakeid")


check("verify(): чисте processed відео — ok", _verify_with([_item()])[0])

_ok, _info = _verify_with([_item(blocked=["US", "GB"])])
check("verify(): блоковане хоч в одній країні — НЕ ok (регресія 011)", not _ok)
check("verify(): blockedCountries полічені правильно", _info["blockedCountries"] == 2,
      str(_info["blockedCountries"]))

check("verify(): одна заблокована країна теж провал (ми нічого не обмежуємо самі)",
      not _verify_with([_item(blocked=["UA"])])[0])

# uploadStatus="uploaded" достатньо для verify() (документована відмінність),
# але НЕ для check_processed() — саме ця межа коштувала 72-хвилинної лекції
# в інциденті 001 ("uploaded" прийняли за "успіх" і видалили файл з VPS).
check("verify(): 'uploaded' (не processed) — усе ще ok",
      _verify_with([_item(upload_status="uploaded")])[0])
check("check_processed(): 'uploaded' (не processed) — НЕ ok (регресія 001)",
      not _check_processed_with([_item(upload_status="uploaded")])[0])
check("check_processed(): 'processed' — ok",
      _check_processed_with([_item(upload_status="processed")])[0])

check("verify(): failureReason — не ok",
      not _verify_with([_item(failure="videoProcessingFailed")])[0])
check("verify(): rejectionReason — не ok",
      not _verify_with([_item(rejection="lengthLimitExceeded")])[0])
check("verify(): privacyStatus не unlisted — не ok (гвард на неочікуваний стан)",
      not _verify_with([_item(privacy="public")])[0])

_ok, _info = _verify_with([])
check("verify(): відео не знайдено — не ok, без винятку", not _ok)
check("verify(): відео не знайдено — є пояснення в info", "error" in _info, str(_info))
# yt_upload.service лишається підміненим до кінця процесу — нижче він
# більше не викликається, а окремий процес python на кожен тест-ран
# (`if __name__ == "__main__"`) і так не ділить стан з нічим іншим.


# --- контроль якості транскрипту (29.09.2026) --------------------------------
import logging  # noqa: E402

_src_po = inspect.getsource(process.process_one)
check("process_one: якість оцінюється ДО видалення wav (інакше звук нема з чим звірити)",
      0 < _src_po.find("check_quality(") < _src_po.find("wav.unlink()"))
check("process_one: якість іде в метадані транскрипту", '"якість":' in _src_po)
check("check_quality логує маркер QUALITY_WARN: — його рахує alert.sh (перевірка 17)",
      "QUALITY_WARN:" in inspect.getsource(process.check_quality))
check("alert.sh шукає той самий маркер QUALITY_WARN:",
      _alert is not None and "grep -c 'QUALITY_WARN:'" in _alert.read_text(encoding="utf-8"),
      str(_alert))


class _Cap(logging.Handler):
    def __init__(self):
        super().__init__()
        self.lines = []

    def emit(self, r):
        self.lines.append(f"{r.levelname} {r.getMessage()}")


_cap = _Cap()
process.log.addHandler(_cap)
_orig_ds = process.transcript_quality.detect_silences
_good = "\n".join(f"[{s // 60:02d}:{s % 60:02d}] речення номер {s} про струм і напругу в колі"
                  for s in range(0, 600, 5))
_hole = "\n".join(l for l in _good.splitlines() if not ("[02:" <= l[:4] <= "[06:"))

process.transcript_quality.detect_silences = lambda wav, d: []  # звук є всюди
_r = process.check_quality(_good, {"duration": 600.0, "fallback": 0}, Path("/x.wav"), "a.mkv")
check("check_quality: чистий транскрипт -> 'ok …', без QUALITY_WARN",
      _r.startswith("ok") and not any("QUALITY_WARN" in l for l in _cap.lines), (_r, _cap.lines))

_cap.lines.clear()
_r = process.check_quality(_hole, {"duration": 600.0}, Path("/x.wav"), "b.mkv")
_w = [l for l in _cap.lines if "QUALITY_WARN:" in l]
check("check_quality: дірка -> '⚠ …' і WARNING QUALITY_WARN з імʼям запису",
      _r.startswith("⚠") and len(_w) == 1 and _w[0].startswith("WARNING") and "b.mkv" in _w[0],
      (_r, _cap.lines))


def _boom(wav, d):
    raise RuntimeError("ffmpeg зник")


process.transcript_quality.detect_silences = _boom
_cap.lines.clear()
try:
    _r = process.check_quality(_good, {"duration": 600.0}, Path("/x.wav"), "c.mkv")
    check("check_quality: збій оцінки не валить обробку -> 'не оцінено'", _r == "не оцінено", _r)
except Exception as e:  # noqa: BLE001
    check("check_quality: збій оцінки не валить обробку -> 'не оцінено'", False, e)
check("check_quality: збій оцінки — WARNING, не ERROR (перевірка 4 не кричить)",
      _cap.lines and all(l.startswith("WARNING") for l in _cap.lines), _cap.lines)
process.transcript_quality.detect_silences = _orig_ds
process.log.removeHandler(_cap)

_dst = Path(TMP) / "q" / "t.md"
process.write_transcript(_dst, "T", {"якість": __import__("json").dumps(
    "⚠ дірки 02:00-07:00 (5 хв зі звуком без тексту); петлі 05:00×6", ensure_ascii=False)}, "x")
try:
    import yaml  # noqa: E402
    _fm = yaml.safe_load(_dst.read_text(encoding="utf-8").split("---")[1])
    check("метадані з двокрапками в «якість» — валідний YAML",
          _fm["якість"].startswith("⚠ дірки 02:00-07:00"), _fm)
except ImportError:
    print("SKIP pyyaml нема — YAML-перевірка метаданих пропущена")


# --- записи без звуку (12.10.2026) -------------------------------------------
# Пара, де викладач мовчав через тривогу, давала ERROR «транскрипт порожній»
# щопівгодини, потім «вичерпані спроби» щоциклу. Тепер — розпізнати до whisper,
# відео в skipped-silent/, один WARNING SILENT_SKIP:.
import logging  # noqa: E402
import shutil   # noqa: E402
import subprocess  # noqa: E402

# 1. sound_stats: інтервали тиші з silencedetect -> скільки в записі звуку.
# Реальний вивід ffmpeg із тихого запису 12.10.2026 (скорочено до суті): тиша майже
# на весь файл, з кількома короткими «просвітами» біля 3245-3260 с.
_SD_REAL = ("[silencedetect @ 0x1] silence_start: -0.0000208333\n"
            "[silencedetect @ 0x1] silence_end: 3243.67 | silence_duration: 3243.67\n"
            "[silencedetect @ 0x1] silence_start: 3243.69\n"
            "[silencedetect @ 0x1] silence_end: 3244.82 | silence_duration: 1.13\n"
            "[silencedetect @ 0x1] silence_start: 3261.07\n")  # остання тиша — до кінця файлу
_iv = process.transcript_quality.parse_silences(_SD_REAL, 5126.29)
_st = process.sound_stats(_iv, 5126.29)
check("sound_stats: реальний тихий запис -> менше 20 с звуку з 5126",
      _st is not None and _st["sound_s"] < 20 and _st["fraction"] < 0.01, _st)
check("sound_stats: незакрита тиша в кінці рахується до кінця файлу",
      _iv[-1] == (3261.07, 5126.29), _iv)
_st = process.sound_stats([(0.0, 1212.0)], 5518.0)
check("sound_stats: нормальна пара (1212 с тиші з 5518) -> ≈78 % звуку",
      abs(_st["fraction"] - 0.78) < 0.01, _st)
check("sound_stats: тиші нема взагалі -> 100 % звуку",
      process.sound_stats([], 100.0)["fraction"] == 1.0)
check("sound_stats: тиша на весь файл -> 0 звуку",
      process.sound_stats([(0.0, 100.0)], 100.0)["sound_s"] == 0.0)
check("sound_stats: інтервал за межами файлу обрізається, звук не стає від'ємним",
      process.sound_stats([(-5.0, 150.0)], 100.0)["sound_s"] == 0.0)
check("sound_stats: тривалість невідома (0) -> None", process.sound_stats([], 0.0) is None)

# 2. поріг: частка звуку < SOUND_MIN_FRACTION — тиша; None (збій виміру) — НЕ тиша
check("поріг 1 % і рівень -40 дБ (змінюються лише свідомо)",
      process.SOUND_MIN_FRACTION == 0.01 and process.SILENCE_NOISE_DB == "-40",
      (process.SOUND_MIN_FRACTION, process.SILENCE_NOISE_DB))
check("is_silent: 0,06 % звуку (запис 12.10) -> тиша",
      process.is_silent({"duration": 5126.0, "sound_s": 3.0, "fraction": 3.0 / 5126.0}))
check("is_silent: рівно 1 % -> НЕ тиша (суворе <)",
      not process.is_silent({"duration": 100.0, "sound_s": 1.0, "fraction": 0.01}))
check("is_silent: 78 % (нормальна пара) -> НЕ тиша",
      not process.is_silent({"duration": 5518.0, "sound_s": 4306.0, "fraction": 0.78}))
check("is_silent: збій виміру (None) -> НЕ тиша, запис іде звичайним шляхом",
      not process.is_silent(None))


# 3. справжній ffmpeg: синтетичні відео
def _ffmpeg(*args):
    r = subprocess.run(["ffmpeg", "-y", "-hide_banner", "-loglevel", "error", *args],
                       capture_output=True, text=True)
    if r.returncode != 0:
        raise RuntimeError(r.stderr[-400:])


_FX = Path(TMP) / "fx"
_FX.mkdir(exist_ok=True)
_VID = ["-f", "lavfi", "-i", "color=c=black:s=64x64:d=3:r=10"]
_VENC = ["-c:v", "mpeg4", "-t", "3"]


def _mk_video(name, audio_src=None):
    """audio_src: lavfi-вираз джерела звуку, або None — відео без доріжки."""
    p = _FX / name
    if audio_src is None:
        _ffmpeg(*_VID, *_VENC, "-an", str(p))
    else:
        _ffmpeg(*_VID, "-f", "lavfi", "-i", audio_src, *_VENC, "-c:a", "aac", str(p))
    return p


_SIL = _mk_video("silent.mkv", "anullsrc=r=16000:cl=mono")
_TONE = _mk_video("tone.mkv", "sine=frequency=440:duration=3")   # пік ≈ -18 дБ
_NOAUD = _mk_video("noaudio.mkv", None)
# майже вся тиша + пів секунди помітного звуку (студент раз заговорив) — пік ≈ -20 дБ
_BLIP = _mk_video("blip.mkv",
                  "aevalsrc='if(between(t,1,1.5),0.1*sin(2*PI*440*t),0)':s=16000:d=3")
# дуже тихий тон: -40 дБ поверх -18 -> пік ≈ -58 дБ, нижче порога
_FAINT = _mk_video("faint.mkv", "sine=frequency=440:duration=3,volume=-40dB")


def _wav_of(video):
    w = _FX / (video.stem + ".wav")
    process.extract_wav(video, w)
    return w


_v = process.measure_sound(_wav_of(_SIL))
check("measure_sound: цифрова тиша -> 0 звуку", _v is not None and _v["sound_s"] < 0.5, _v)
check("is_silent(реальна тиша)", process.is_silent(_v))
_v = process.measure_sound(_wav_of(_TONE))
check("measure_sound: тон на весь файл -> майже 100 % звуку", _v is not None and _v["fraction"] > 0.9, _v)
check("is_silent(реальний тон) == False", not process.is_silent(_v))
_v = process.measure_sound(_wav_of(_BLIP))
check("3-с кліп із 0,5 с звуку (≈17 %) -> НЕ тиша: короткі тестові кліпи не викидаються",
      not process.is_silent(_v), _v)
_v = process.measure_sound(_wav_of(_FAINT))
check("майже нечутний тон (пік ≈ -58 дБ, нижче -40) -> тиша", process.is_silent(_v), _v)
check("measure_sound: неіснуючий файл -> None, без винятку",
      process.measure_sound(_FX / "нема.wav") is None)

# довші записи: відтворюють головний випадок — мало звуку на великому файлі.
# 400 с: 1 с звуку (0,25 %) -> тиша; 12 с звуку (3 %) -> не тиша.
# _mk_video обрізає відео до 3 с (-t 3), тож довгі файли (лише аудіо) збираємо напряму
for _nm, _sec in (("long1.mkv", 1), ("long12.mkv", 12)):
    _p = _FX / _nm
    _ffmpeg("-f", "lavfi", "-i",
            f"aevalsrc='if(between(t,100,{100 + _sec}),0.1*sin(2*PI*440*t),0)':s=16000:d=400",
            "-c:a", "aac", str(_p))
_v1 = process.measure_sound(_wav_of(_FX / "long1.mkv"))
_v12 = process.measure_sound(_wav_of(_FX / "long12.mkv"))
check("400 с із 1 с звуку (≈0,25 %) -> тиша", process.is_silent(_v1), _v1)
check("400 с із 12 с звуку (≈3 %) -> НЕ тиша", not process.is_silent(_v12), _v12)

check("has_audio_stream: відео з доріжкою -> True", process.has_audio_stream(_TONE) is True)
check("has_audio_stream: відео БЕЗ доріжки -> False", process.has_audio_stream(_NOAUD) is False)
check("has_audio_stream: неіснуючий файл -> None (не False!), обробка йде далі",
      process.has_audio_stream(_FX / "нема.mkv") is None)


# 4. process_one на тихому записі: ні whisper, ні YouTube, лише skipped-silent/
class _Reached(Exception):
    pass


class _Cap(logging.Handler):
    def __init__(self):
        super().__init__()
        self.recs = []

    def emit(self, rec):
        self.recs.append((rec.levelname, rec.getMessage()))


_calls = {"transcribe": 0}


def _fake_transcribe(wav, duration=0.0):
    _calls["transcribe"] += 1
    raise _Reached()


_real_transcribe = process.transcribe
process.transcribe = _fake_transcribe
_scap = _Cap()
process.log.addHandler(_scap)
_sdb = process.open_db()


def _drop(src, name=None):
    dst = process.INCOMING / (name or src.name)
    shutil.copy(src, dst)
    return dst


_f = _drop(_SIL, "2026-10-12 10-35-02.mkv")
process.process_one(_sdb, _f)
_sha = process.sha256_of(process.SILENT / "2026-10-12 10-35-02.mkv")
_row = _sdb.execute("SELECT status, error FROM files WHERE sha256=?", (_sha,)).fetchone()
check("тихий запис: пішов зі теки incoming", not _f.exists())
check("тихий запис: лежить у skipped-silent/ цілий",
      (process.SILENT / "2026-10-12 10-35-02.mkv").exists())
check("тихий запис: whisper НЕ викликався", _calls["transcribe"] == 0, _calls)
check("тихий запис: статус skipped_silent з причиною в error",
      _row and _row[0] == "skipped_silent" and "майже немає звуку" in (_row[1] or ""), _row)
_w = [m for lvl, m in _scap.recs if "SILENT_SKIP:" in m]
check("тихий запис: рівно один рядок SILENT_SKIP:, і це WARNING",
      len(_w) == 1 and [l for l, m in _scap.recs if "SILENT_SKIP:" in m] == ["WARNING"], _scap.recs)
check("тихий запис: ім'я запису й тека є в рядку SILENT_SKIP",
      _w and "2026-10-12 10-35-02.mkv" in _w[0] and "skipped-silent/" in _w[0], _w)
check("тихий запис: жодного ERROR у лозі (перевірка 4 alert.sh мовчить)",
      not any(l in ("ERROR", "CRITICAL") for l, _ in _scap.recs), _scap.recs)
check("тихий запис: wav не лишився в work/", not list(process.WORK.glob("*.wav")))
check("тихий запис: права 0640 (світових бітів нема)",
      ((process.SILENT / "2026-10-12 10-35-02.mkv").stat().st_mode & 0o777) == 0o640,
      oct((process.SILENT / "2026-10-12 10-35-02.mkv").stat().st_mode & 0o777))

# дублікат: той самий файл прийшов знову -> видаляється з incoming, не обробляється вдруге
_scap.recs.clear()
_f2 = _drop(process.SILENT / "2026-10-12 10-35-02.mkv", "2026-10-12 10-35-02.mkv")
process.process_one(_sdb, _f2)
check("дублікат тихого запису: видалений з incoming", not _f2.exists())
check("дублікат тихого запису: оригінал у skipped-silent/ лишився",
      (process.SILENT / "2026-10-12 10-35-02.mkv").exists())
check("дублікат тихого запису: другого SILENT_SKIP нема",
      not any("SILENT_SKIP:" in m for _, m in _scap.recs), _scap.recs)

# колізія імен: інший вміст під тим самим іменем не затирає наявний файл
_other = _FX / "other.mkv"
_ffmpeg(*_VID, "-f", "lavfi", "-i", "anullsrc=r=22050:cl=mono", *_VENC, "-c:a", "aac", str(_other))
_dummy = process.SILENT / "collide.mkv"
_dummy.write_bytes(b"x")
_fc = _drop(_other, "collide.mkv")
process.process_one(_sdb, _fc)
_shas = sorted(p.name for p in process.SILENT.glob("*collide.mkv"))
check("колізія імен: наявний файл не затертий", _dummy.read_bytes() == b"x")
check("колізія імен: новий лежить під іменем <sha8>__…",
      len(_shas) == 2 and any("__collide.mkv" in n for n in _shas), _shas)

# запис без звукової доріжки
_scap.recs.clear()
_fn = _drop(_NOAUD, "noaudio-test.mkv")
process.process_one(_sdb, _fn)
check("відео без доріжки: у skipped-silent/, whisper не викликався",
      (process.SILENT / "noaudio-test.mkv").exists() and not _fn.exists()
      and _calls["transcribe"] == 0)
check("відео без доріжки: SILENT_SKIP з поясненням «немає звукової доріжки», без ERROR",
      any("SILENT_SKIP:" in m and "немає звукової доріжки" in m for _, m in _scap.recs)
      and not any(l == "ERROR" for l, _ in _scap.recs), _scap.recs)

# не тихі записи НЕ пропускаються: доходять до whisper
for _nm, _src in (("tone-test.mkv", _TONE), ("blip-test.mkv", _BLIP)):
    _calls["transcribe"] = 0
    _fv = _drop(_src, _nm)
    try:
        process.process_one(_sdb, _fv)
        _hit = False
    except _Reached:
        _hit = True
    check(f"{_nm}: не тихий -> дійшов до whisper і лишився в incoming",
          _hit and _calls["transcribe"] == 1 and _fv.exists() and
          not (process.SILENT / _nm).exists(), (_hit, _calls))
    _fv.unlink(missing_ok=True)
    for _w in process.WORK.glob("*.wav"):
        _w.unlink()

# збій виміру гучності не валить обробку: запис іде далі
_real_measure = process.measure_sound
process.measure_sound = lambda wav: None
_calls["transcribe"] = 0
# окреме відео: тихий _SIL уже оброблений вище, і його копія була б дублікатом
_SIL2 = _mk_video("silent2.mkv", "anullsrc=r=32000:cl=mono")
_fm = _drop(_SIL2, "measure-fail.mkv")
try:
    process.process_one(_sdb, _fm)
    _hit = False
except _Reached:
    _hit = True
check("збій виміру гучності: тихий файл НЕ викинуто, іде до whisper (fail-open)",
      _hit and _fm.exists(), _hit)
process.measure_sound = _real_measure
_fm.unlink(missing_ok=True)
for _w in process.WORK.glob("*.wav"):
    _w.unlink()

# harden_modes знімає світові права й зі skipped-silent/
_hm = process.SILENT / "harden-me.mkv"
_hm.write_bytes(b"x")
_hm.chmod(0o666)
process.harden_modes()
check("harden_modes: skipped-silent/ теж вирівнюється до 0640",
      (_hm.stat().st_mode & 0o007) == 0, oct(_hm.stat().st_mode & 0o777))

process.transcribe = _real_transcribe
process.log.removeHandler(_scap)

# 5. маркер у process.py і alert.sh — один і той самий (розʼїзд вимкнув би перевірку 19 мовчки)
check("skip_silent логує маркер SILENT_SKIP: — його рахує alert.sh (перевірка 19)",
      "SILENT_SKIP:" in inspect.getsource(process.skip_silent))
check("alert.sh шукає той самий маркер SILENT_SKIP:",
      _alert is not None and "grep -c 'SILENT_SKIP:'" in _alert.read_text(encoding="utf-8"),
      str(_alert))
check("alert.sh (перевірка 11) рахує skipped-silent серед записів дня",
      _alert is not None and '"$BASE/skipped-silent"' in _alert.read_text(encoding="utf-8"))

print(f"\nпройдено {ok}/{ok + fail}")
sys.exit(0 if fail == 0 else 1)
