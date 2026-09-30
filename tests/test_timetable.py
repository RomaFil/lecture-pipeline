#!/usr/bin/env python3
"""Календар «КПІ» як джерело правди для класифікації — без мережі й без моделі.

Запускати на VPS venv'ом: ~/lectures/venv/bin/python tests/test_timetable.py
Фікстура нижче повторює формат експорту Google Calendar: UTC-час для разових
подій, TZID=Europe/Kiev + VTIMEZONE для повторюваних, винятки й перенесення.
"""
import os
import sys
import tempfile
from datetime import date, datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "vps" / "bin"))

import classify as clf  # noqa: E402
import timetable as tt  # noqa: E402

ok = fail = 0


def check(name, cond, detail=""):
    global ok, fail
    if cond:
        ok += 1
        print(f"PASS {name}")
    else:
        fail += 1
        print(f"FAIL {name}  {detail}")


ICS = """BEGIN:VCALENDAR
VERSION:2.0
PRODID:-//Google Inc//Google Calendar 70.9054//EN
X-WR-CALNAME:КПІ
X-WR-TIMEZONE:Europe/Kiev
BEGIN:VTIMEZONE
TZID:Europe/Kiev
BEGIN:DAYLIGHT
TZOFFSETFROM:+0200
TZOFFSETTO:+0300
TZNAME:EEST
DTSTART:19700329T030000
RRULE:FREQ=YEARLY;BYMONTH=3;BYDAY=-1SU
END:DAYLIGHT
BEGIN:STANDARD
TZOFFSETFROM:+0300
TZOFFSETTO:+0200
TZNAME:EET
DTSTART:19701025T040000
RRULE:FREQ=YEARLY;BYMONTH=10;BYDAY=-1SU
END:STANDARD
END:VTIMEZONE
BEGIN:VEVENT
UID:otk-0826
DTSTART:20260926T053000Z
DTEND:20260926T070500Z
SUMMARY:Основи теорії кіл — практика (КПІ)
LOCATION:КПІ ім. Сікорського\\, корпус №17\\, ауд. 424
END:VEVENT
BEGIN:VEVENT
UID:checkin
DTSTART:20260926T053000Z
DTEND:20260926T060000Z
SUMMARY:Тижневий чекін з Claude (планування семестру)
END:VEVENT
BEGIN:VEVENT
UID:checkin-sun
DTSTART:20260927T170000Z
DTEND:20260927T174000Z
SUMMARY:Тижневий чекін з Claude (планування семестру)
END:VEVENT
BEGIN:VEVENT
UID:eng-sat
DTSTART;TZID=Europe/Kiev:20260919T141500
DTEND;TZID=Europe/Kiev:20260919T155000
RRULE:FREQ=WEEKLY;COUNT=4
EXDATE;TZID=Europe/Kiev:20260926T141500
SUMMARY:Англійська мова професійного спрямування — практика (КПІ)
LOCATION:КПІ ім. Сікорського\\, корпус №17\\, ауд. 428
END:VEVENT
BEGIN:VEVENT
UID:otk-1426
DTSTART:20260926T111500Z
DTEND:20260926T125000Z
SUMMARY:Основи теорії кіл — практика (КПІ)
END:VEVENT
BEGIN:VEVENT
UID:box
DTSTART;TZID=Europe/Kiev:20260916T141500
DTEND;TZID=Europe/Kiev:20260916T155000
RRULE:FREQ=WEEKLY;COUNT=4
SUMMARY:Бокс — Єдиноборства (КПІ)
LOCATION:Онлайн Zoom
END:VEVENT
BEGIN:VEVENT
UID:box
RECURRENCE-ID;TZID=Europe/Kiev:20260923T141500
DTSTART;TZID=Europe/Kiev:20260923T102500
DTEND;TZID=Europe/Kiev:20260923T120000
SUMMARY:Бокс — Єдиноборства (КПІ)
LOCATION:Онлайн Zoom
END:VEVENT
BEGIN:VEVENT
UID:cisco
DTSTART:20260925T092000Z
DTEND:20260925T105500Z
SUMMARY:CISCO — Лекція (КПІ)
LOCATION:Онлайн Zoom
END:VEVENT
BEGIN:VEVENT
UID:unknown
DTSTART:20260924T053000Z
DTEND:20260924T070500Z
SUMMARY:Філософія науки — лекція (КПІ)
END:VEVENT
BEGIN:VEVENT
UID:phys-lec
DTSTART:20260924T072500Z
DTEND:20260924T090000Z
SUMMARY:Загальна фізика. Частина 2 — лекція (КПІ)
LOCATION:Онлайн Zoom
END:VEVENT
BEGIN:VEVENT
UID:matan-lec
DTSTART:20260924T092000Z
DTEND:20260924T105500Z
SUMMARY:Математичний аналіз. Частина 3 — лекція (КПІ)
LOCATION:Онлайн Zoom
END:VEVENT
BEGIN:VEVENT
UID:phys-cancel
DTSTART:20260926T072500Z
DTEND:20260926T090000Z
STATUS:CANCELLED
SUMMARY:Загальна фізика. Частина 2 — практика (КПІ)
END:VEVENT
BEGIN:VEVENT
UID:allday
DTSTART;VALUE=DATE:20260930
DTEND;VALUE=DATE:20261001
SUMMARY:Основи теорії кіл — дедлайн (КПІ)
END:VEVENT
END:VCALENDAR
""".replace("\n", "\r\n")

tmp = Path(tempfile.mkdtemp())
CAL = tmp / "kpi.ics"
CAL.write_text(ICS, encoding="utf-8")
_d = datetime


def lk(a, b):
    # лише КПІ: ajax_path на неіснуючий файл (інакше тест на VPS підхопив би бойовий
    # кеш Ajax), фіксований слот Ajax вимкнений — його перевіряють окремо нижче
    return tt.lookup(a, b, CAL, ajax_path=tmp / "no-ajax.ics", ajax_slot=False)


# --- назви подій --------------------------------------------------------------
check("назва: дисципліна і тип", tt.parse_title("Основи теорії кіл — практика (КПІ)")
      == ("Основи теорії кіл", "практика"))
check("назва: без суфікса (КПІ) — не пара", tt.parse_title("Тижневий чекін з Claude") is None)
check("назва: тип не з KINDS (бокс) → None", tt.parse_title("Бокс — Єдиноборства (КПІ)") == ("Бокс", None))
check("назва: «Лекція» з великої літери (CISCO)", tt.parse_title("CISCO — Лекція (КПІ)") == ("CISCO", "лекція"))
check("код: CISCO через аліас", tt.subject_code("CISCO") == "МЕРЕЖІ_CISCO")
check("код: повна назва", tt.subject_code("Схемотехніка. Частина 1. Електронні компоненти") == "СХЕМОТЕХНІКА")
check("код: бокс", tt.subject_code("Бокс") == "БОКС")
check("код: невідома дисципліна → None", tt.subject_code("Філософія науки") is None)
check("код: кожна дисципліна з реального календаря 27.09 розпізнається",
      all(tt.subject_code(n) for n in (
          "Основи теорії кіл", "Загальна фізика. Частина 2", "Математичний аналіз. Частина 3",
          "Англійська мова професійного спрямування", "Схемотехніка. Частина 1. Електронні компоненти",
          "Бокс", "CISCO", "Цивільний захист, оборона та патріотичне виховання")))
check("код: ЦЗОПВ з календаря («… — Блок 1 (КПІ)») → ЦЗОПВ, тип не з KINDS → None",
      tt.parse_title("Цивільний захист, оборона та патріотичне виховання — Блок 1 (КПІ)")
      == ("Цивільний захист, оборона та патріотичне виховання", None)
      and tt.subject_code("Цивільний захист, оборона та патріотичне виховання") == "ЦЗОПВ")

# --- lookup -------------------------------------------------------------------
r = lk(_d(2026, 9, 26, 8, 41), _d(2026, 9, 26, 10, 24))
check("РЕГРЕСІЯ 26.09 08:41: календар каже ОТК-практика",
      r["code"] == "ТЕОРІЯ_КІЛ" and r["kind"] == "практика", r)
check("чекін з тим самим часом не заважає (немає суфікса)", r["candidates"] == {"ТЕОРІЯ_КІЛ"}, r)
check("повторювана подія: сб 19.09 14:15 — англійська",
      lk(_d(2026, 9, 19, 14, 16), _d(2026, 9, 19, 15, 40))["code"] == "АНГЛІЙСЬКА")
check("EXDATE + разова подія: сб 26.09 14:36 — ОТК, не англійська",
      lk(_d(2026, 9, 26, 14, 36), _d(2026, 9, 26, 16, 25))["candidates"] == {"ТЕОРІЯ_КІЛ"})
check("плаваючий слот: сб 03.10 14:15 — знову англійська",
      lk(_d(2026, 10, 3, 14, 16), _d(2026, 10, 3, 15, 45))["code"] == "АНГЛІЙСЬКА")
r = lk(_d(2026, 9, 23, 10, 26), _d(2026, 9, 23, 11, 50))
check("перенесення (RECURRENCE-ID): бокс 23.09 о 10:25", r["code"] == "БОКС" and r["kind"] is None, r)
check("перенесений бокс не лишився на старому місці 23.09 14:15",
      lk(_d(2026, 9, 23, 14, 16), _d(2026, 9, 23, 15, 40))["candidates"] == set())
check("бокс 30.09 — звичайне повторення",
      lk(_d(2026, 9, 30, 14, 16), _d(2026, 9, 30, 15, 40))["code"] == "БОКС")
r = lk(_d(2026, 9, 25, 12, 22), _d(2026, 9, 25, 13, 50))
check("CISCO пт 25.09 → МЕРЕЖІ_CISCO, лекція", r["code"] == "МЕРЕЖІ_CISCO" and r["kind"] == "лекція", r)
r = lk(_d(2026, 9, 24, 8, 35), _d(2026, 9, 24, 10, 0))
check("невідома дисципліна: код None, назва в unknown",
      r["code"] is None and r["unknown"] == ["Філософія науки — лекція (КПІ)"], r)
check("скасована пара не рахується", lk(_d(2026, 9, 26, 10, 30), _d(2026, 9, 26, 11, 55))["candidates"] == set())
check("чекін без пари поруч — порожньо", lk(_d(2026, 9, 27, 20, 0), _d(2026, 9, 27, 20, 40))["candidates"] == set())
check("цілоденна подія ігнорується", lk(_d(2026, 9, 30, 8, 0), _d(2026, 9, 30, 10, 0))["candidates"] == set())
r = lk(_d(2026, 9, 24, 10, 30), _d(2026, 9, 24, 13, 50))
check("запис на дві пари порівну → без рішення, два кандидати",
      r["code"] is None and r["candidates"] == {"ФІЗИКА", "МАТАНАЛІЗ"}, r)
r = lk(_d(2026, 9, 24, 10, 30), _d(2026, 9, 24, 12, 40))
check("запис зачепив другу пару на 20 хв → перемагає перша (домінування)",
      r["code"] == "ФІЗИКА" and r["candidates"] == {"ФІЗИКА", "МАТАНАЛІЗ"}, r)
check("короткий запис: досить половини його довжини",
      lk(_d(2026, 9, 25, 13, 40), _d(2026, 9, 25, 14, 0))["code"] == "МЕРЕЖІ_CISCO")
check("кеш відсутній → порожньо, без винятку", tt.lookup(_d(2026, 9, 26, 8, 41), _d(2026, 9, 26, 10, 24),
                                                         tmp / "nope.ics")["candidates"] == set())
os.environ["LECTURE_SCHEDULE_HINT"] = "0"
check("LECTURE_SCHEDULE_HINT=0 вимикає календар", lk(_d(2026, 9, 26, 8, 41), _d(2026, 9, 26, 10, 24))["code"] is None)
del os.environ["LECTURE_SCHEDULE_HINT"]

# --- очікувані записи (перевірка 11 alert.sh) ---------------------------------
check("чт 24.09: дві онлайн-пари, невідома без місця не рахується",
      tt.expected_recordings(date(2026, 9, 24), CAL) == 2, tt.expected_recordings(date(2026, 9, 24), CAL))
check("ср 23.09: перенесений онлайн-бокс рахується", tt.expected_recordings(date(2026, 9, 23), CAL) == 1)
check("сб 26.09: пари в аудиторіях (офлайн) не рахуються", tt.expected_recordings(date(2026, 9, 26), CAL) == 0)
check("нд 27.09: лише чекін → 0", tt.expected_recordings(date(2026, 9, 27), CAL) == 0)

# --- refresh (file:// замість мережі) ------------------------------------------
_url, _cache = tt.URL_FILE, tt.CACHE
tt.URL_FILE, tt.CACHE = tmp / "secrets" / "kpi.url", tmp / "state" / "cache.ics"
check("refresh: немає файлу з адресою → no-url", tt.refresh() == "no-url")
tt.URL_FILE.parent.mkdir()
tt.URL_FILE.write_text(CAL.as_uri() + "\n", encoding="utf-8")
check("refresh: успіх → updated", tt.refresh() == "updated")
check("refresh: кеш з правами 600 (там Zoom-паролі)",
      os.name == "nt" or (tt.CACHE.stat().st_mode & 0o777) == 0o600, oct(tt.CACHE.stat().st_mode))
check("refresh: свіжий кеш без force не перечитується", tt.refresh() == "fresh")
bad = tmp / "bad.html"
bad.write_text("<html>login</html>", encoding="utf-8")
tt.URL_FILE.write_text(bad.as_uri(), encoding="utf-8")
st = tt.refresh(force=True)
check("refresh: не-iCal → error, кеш цілий", st.startswith("error") and b"BEGIN:VCALENDAR" in tt.CACHE.read_bytes(), st)
check("refresh: адреса не потрапляє в текст помилки", bad.as_uri() not in st, st)
tt.URL_FILE.write_text((tmp / "missing.ics").as_uri(), encoding="utf-8")
st = tt.refresh(force=True)
check("refresh: недоступна адреса → error без винятку", st.startswith("error"), st)
tt.URL_FILE, tt.CACHE = _url, _cache

# --- календар Ajax (30.09.2026, інцидент 021) -----------------------------------
AJAX_ICS = """BEGIN:VCALENDAR
VERSION:2.0
PRODID:-//Google Inc//Google Calendar 70.9054//EN
X-WR-CALNAME:AJAX Intership
BEGIN:VTIMEZONE
TZID:Europe/Kiev
BEGIN:DAYLIGHT
TZOFFSETFROM:+0200
TZOFFSETTO:+0300
TZNAME:EEST
DTSTART:19700329T030000
RRULE:FREQ=YEARLY;BYMONTH=3;BYDAY=-1SU
END:DAYLIGHT
BEGIN:STANDARD
TZOFFSETFROM:+0300
TZOFFSETTO:+0200
TZNAME:EET
DTSTART:19701025T040000
RRULE:FREQ=YEARLY;BYMONTH=10;BYDAY=-1SU
END:STANDARD
END:VTIMEZONE
BEGIN:VEVENT
UID:ajax-weekly
DTSTART;TZID=Europe/Kiev:20260908T170000
DTEND;TZID=Europe/Kiev:20260908T200000
RRULE:FREQ=WEEKLY;UNTIL=20261231T000000Z;BYDAY=TU,TH
SUMMARY:Ajax Embedded — заняття (KAI-Ajax Lab)
LOCATION:KAI-Ajax Lab\\, 3 корпус КАІ
END:VEVENT
BEGIN:VEVENT
UID:ajax-weekly
RECURRENCE-ID;TZID=Europe/Kiev:20260929T170000
DTSTART;TZID=Europe/Kiev:20260929T170000
DTEND;TZID=Europe/Kiev:20260929T200000
SUMMARY:Ajax Embedded — Основи електроніки
END:VEVENT
BEGIN:VEVENT
UID:ajax-weekly
RECURRENCE-ID;TZID=Europe/Kiev:20261001T170000
DTSTART;TZID=Europe/Kiev:20261001T170000
DTEND;TZID=Europe/Kiev:20261001T200000
STATUS:CANCELLED
SUMMARY:Ajax Embedded — заняття (KAI-Ajax Lab)
END:VEVENT
BEGIN:VEVENT
UID:ajax-lab-once
DTSTART:20260928T100000Z
DTEND:20260928T120000Z
SUMMARY:AJAX LAB
END:VEVENT
END:VCALENDAR
""".replace("\n", "\r\n")
AJ =tmp / "ajax.ics"
AJ.write_text(AJAX_ICS, encoding="utf-8")


def lka(a, b, kpi=CAL):
    return tt.lookup(a, b, kpi, ajax_path=AJ, ajax_slot=False)


r = lka(_d(2026, 9, 29, 16, 59), _d(2026, 9, 29, 19, 23))
check("РЕГРЕСІЯ 021: вт 29.09 16:59 (Altium) → АЯКС за календарем Ajax",
      r["code"] == "АЯКС" and r["kind"] is None and "Основи електроніки" in r["summary"], r)
check("Ajax: чт 24.09 17:06 — звичайне повторення", lka(_d(2026, 9, 24, 17, 6), _d(2026, 9, 24, 19, 45))["code"] == "АЯКС")
check("Ajax: подія з довільною назвою («AJAX LAB», пн 28.09 13:00) — теж АЯКС",
      lka(_d(2026, 9, 28, 13, 5), _d(2026, 9, 28, 14, 50))["code"] == "АЯКС")
check("Ajax: скасоване заняття 01.10 не рахується",
      lka(_d(2026, 10, 1, 17, 0), _d(2026, 10, 1, 19, 0))["candidates"] == set())
check("Ajax не заважає парам КПІ: 26.09 08:41 — ОТК-практика",
      lka(_d(2026, 9, 26, 8, 41), _d(2026, 9, 26, 10, 24))["code"] == "ТЕОРІЯ_КІЛ")
check("Ajax працює і без кешу КПІ", tt.lookup(_d(2026, 9, 29, 16, 59), _d(2026, 9, 29, 19, 23),
                                             tmp / "nope.ics", ajax_path=AJ, ajax_slot=False)["code"] == "АЯКС")
check("без кешу Ajax і без слота — порожньо на вт 17:00",
      lk(_d(2026, 9, 29, 16, 59), _d(2026, 9, 29, 19, 23))["candidates"] == set())

# --- фіксований слот Ajax вт/чт 17:00 (Роман, 30.09.2026) ---------------------------
def lks(a, b):
    return tt.lookup(a, b, CAL, ajax_path=tmp / "no-ajax.ics")   # слот увімкнений за замовчуванням


r = lks(_d(2026, 9, 29, 16, 59), _d(2026, 9, 29, 19, 23))
check("слот: РЕГРЕСІЯ 021 — вт 29.09 16:59 → АЯКС без жодного календаря Ajax",
      r["code"] == "АЯКС" and r["kind"] is None, r)
check("слот: чт 24.09 17:06-19:45 → АЯКС", lks(_d(2026, 9, 24, 17, 6), _d(2026, 9, 24, 19, 45))["code"] == "АЯКС")
check("слот: ср 30.09 17:00 — не Ajax", lks(_d(2026, 9, 30, 17, 0), _d(2026, 9, 30, 19, 0))["candidates"] == set())
check("слот: пари КПІ вдень у вівторок не зачіпає (сб 26.09 08:41 — ОТК)",
      lks(_d(2026, 9, 26, 8, 41), _d(2026, 9, 26, 10, 24))["code"] == "ТЕОРІЯ_КІЛ")
check("слот: вт 29.09 14:15 (матаналіз) не став Ajax — перетин < 20 хв не рахується",
      "АЯКС" not in lks(_d(2026, 9, 29, 14, 15), _d(2026, 9, 29, 15, 50))["candidates"])
check("слот: до старту стажування (вт 01.09) не діє",
      lks(_d(2026, 9, 1, 17, 0), _d(2026, 9, 1, 19, 0))["candidates"] == set())
check("слот: після AJAX_UNTIL не діє",
      lks(_d(2027, 1, 5, 17, 0), _d(2027, 1, 5, 19, 0))["candidates"] == set())
check("слот + календар Ajax разом не дублюють кандидатів",
      tt.lookup(_d(2026, 9, 29, 16, 59), _d(2026, 9, 29, 19, 23), CAL, ajax_path=AJ)["candidates"] == {"АЯКС"})
_au, _ac = tt.AJAX_URL_FILE, tt.AJAX_CACHE
tt.AJAX_URL_FILE, tt.AJAX_CACHE = tmp / "secrets" / "ajax.url", tmp / "state" / "ajax_cache.ics"
check("refresh_ajax: немає адреси → no-url (календар не налаштований — не збій)", tt.refresh_ajax() == "no-url")
tt.AJAX_URL_FILE.parent.mkdir(exist_ok=True)
tt.AJAX_URL_FILE.write_text(AJ.as_uri(), encoding="utf-8")
check("refresh_ajax: успіх → updated, у свій кеш", tt.refresh_ajax() == "updated"
      and b"AJAX Intership" in tt.AJAX_CACHE.read_bytes())
check("refresh_ajax не чіпає кеш КПІ", not (tmp / "state" / "cache.ics").exists()
      or b"AJAX" not in (tmp / "state" / "cache.ics").read_bytes())
tt.AJAX_URL_FILE, tt.AJAX_CACHE = _au, _ac

# --- класифікація за календарем -----------------------------------------------
_TK, _SC = "ТЕОРІЯ_КІЛ", "СХЕМОТЕХНІКА"
CAL_TK = {"code": _TK, "kind": "практика", "summary": "Основи теорії кіл — практика (КПІ)",
          "candidates": {_TK}}
LONG = "Речення про кола. " * 1500  # > 5000 символів → три фрагменти


def fake(votes, topics=None, kinds=None):
    topics = topics or ["перетворення джерел струму"] * len(votes)
    kinds = kinds or ["лекція"] * len(votes)
    answers = iter([{"subject": v, "topic": t, "kind": k, "confidence": "high"}
                    for v, t, k in zip(votes, topics, kinds)])
    clf._request = lambda system, text, keep_alive=None: next(answers)


_orig_request = clf._request
fake([_SC, _SC, _SC])
r = clf.classify_voted(LONG, calendar=CAL_TK)
check("календар: 3/3 одностайно проти → needs-review з поясненням",
      r["ok"] is False and "одностайно" in r["raw"]["schedule_note"], r)
fake([_SC, _SC, _TK])
r = clf.classify_voted(LONG, calendar=CAL_TK)
check("календар: 2/3 проти → перемагає календар", r["ok"] and r["subject"] == "Основи теорії кіл", r)
check("календар: переважені голоси залишають calendar_note (але не schedule_note)",
      r["raw"].get("calendar_note") and not r["raw"].get("schedule_note"), r["raw"])
check("календар: тип береться з календаря, не з голосів", r["kind"] == "практика", r)
fake([_TK, _TK, _TK])
r = clf.classify_voted(LONG, calendar=CAL_TK)
check("календар: згода — без приміток", r["ok"] and not r["raw"]["calendar_note"], r)
fake([_TK, _TK, _TK], kinds=["лабораторна"] * 3)
r = clf.classify_voted(LONG, calendar={**CAL_TK, "kind": None})
check("календар без типу (бокс) → тип голосами", r["kind"] == "лабораторна", r)
fake([_TK, _TK, _TK], topics=["", "", "метод вузлових потенціалів"])
r = clf.classify_voted(LONG, calendar=CAL_TK)
check("календар: порожня тема у фрагменті → береться тема з іншого", r["ok"] and "вузлових" in r["topic"], r)
fake([_TK, _TK, _TK], topics=["", "", ""])
r = clf.classify_voted(LONG, calendar=CAL_TK)
check("календар: теми порожні всюди → needs-review", r["ok"] is False, r)
fake([_SC])
r = clf.classify_voted("Коротка стенограма про джерела струму і напруги.", calendar=CAL_TK)
check("РЕГРЕСІЯ 26.09: один фрагмент проти календаря — календар перемагає, не needs-review",
      r["ok"] and r["subject"] == "Основи теорії кіл" and r["kind"] == "практика", r)
fake([_SC, _SC, _SC])
r = clf.classify_voted(LONG, calendar={"code": "НЕ_ТАКИЙ_КОД"}, expected=set())
check("невідомий код календаря → стара логіка голосування", r["ok"] and r["subject"].startswith("Схемотехніка"), r)
clf._request = _orig_request

print(f"\n{ok} passed, {fail} failed")
sys.exit(1 if fail else 0)
