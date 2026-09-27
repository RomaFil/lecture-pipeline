"""Розклад пар з Google-календаря «КПІ» — джерело правди для дисципліни запису.

27.09.2026: статичну таблицю SCHEDULE замінено календарем. Розклад плаваючий
(сб 14:15: 12.09 ОТК, 19.09 англійська, 26.09 ОТК), і таблиця в коді неминуче
розʼїжджалась із реальністю, а календар Роман веде сам. 26.09 LLM записав
ОТК-практику в схемотехніку — календар у цей час мав «Основи теорії кіл — практика».

VPS читає календар через секретну iCal-адресу (secrets/kpi_calendar.url, 600),
не через OAuth: токен YouTube лишається недоторканим. Кеш — state/kpi_calendar.ics
(теж 600: в описах подій лежать Zoom-посилання з паролями). Мережа впала —
працюємо з останнім кешем; кеш застарів — кричить alert.sh.

Пара = подія, назва якої закінчується на «(КПІ)»: у тому ж календарі живуть
чекіни, дедлайни й вебінари, причому з тим самим кольором.
"""
import os
import sys
import time
import urllib.request
from datetime import date, datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

HOME = Path(os.getenv("LECTURES_HOME", str(Path.home() / "lectures")))
URL_FILE = HOME / "secrets" / "kpi_calendar.url"
CACHE = HOME / "state" / "kpi_calendar.ics"
TZ = ZoneInfo("Europe/Kyiv")

SUFFIX = "(КПІ)"
REFRESH_MAX_AGE_S = 6 * 3600
# Скільки хвилин запису мусить припадати на пару, щоб пара вважалась його.
MIN_OVERLAP_MIN = 20
# Короткий запис (обірваний, старт із запізненням) — досить половини його довжини.
MIN_OVERLAP_SHARE = 0.5
# Запис захопив дві пари: переможець мусить мати щонайменше вдвічі більший
# перетин, інакше вибір між кандидатами лишається за голосами LLM.
DOMINANCE = 2.0
KINDS = {"лекція", "практика", "лабораторна"}
ALIASES = {"CISCO": "МЕРЕЖІ_CISCO"}
ONLINE_MARKERS = ("онлайн", "online", "zoom", "meet")


def parse_title(summary: str):
    """«Основи теорії кіл — практика (КПІ)» → ("Основи теорії кіл", "практика").

    None — не пара (немає суфікса «(КПІ)»). Тип, якого немає в KINDS
    («Єдиноборства» у боксу), повертається як None: тоді тип вирішує LLM.
    """
    s = summary.strip()
    if not s.endswith(SUFFIX):
        return None
    name, _, kind = s[:-len(SUFFIX)].strip().partition(" — ")
    kind = kind.strip().lower()
    return name.strip(), (kind if kind in KINDS else None)


def subject_code(name: str):
    """Назва дисципліни з календаря → код classify.SUBJECTS, або None."""
    from classify import SUBJECTS
    if name in ALIASES:
        return ALIASES[name]
    for code, (full, short) in SUBJECTS.items():
        if name in (full, short) or name.startswith(full):
            return code
    return None


def _local(dt) -> datetime:
    if dt.tzinfo is None:
        return dt
    return dt.astimezone(TZ).replace(tzinfo=None)


def events_between(start: datetime, end: datetime, path: Path = CACHE) -> list:
    """Пари (події «(КПІ)») з кешу календаря, що хоч якось зачіпають [start, end].

    Повторювані події розгортаються з урахуванням винятків і перенесень
    (RRULE / EXDATE / RECURRENCE-ID). Цілоденні й скасовані — пропускаються.
    """
    import icalendar
    import recurring_ical_events

    cal = icalendar.Calendar.from_ical(path.read_bytes())
    a, b = start.replace(tzinfo=TZ), end.replace(tzinfo=TZ)
    out = []
    for ev in recurring_ical_events.of(cal).between(a - timedelta(hours=12), b + timedelta(hours=12)):
        if str(ev.get("STATUS", "")).upper() == "CANCELLED":
            continue
        parsed = parse_title(str(ev.get("SUMMARY", "")))
        if not parsed:
            continue
        s = ev["DTSTART"].dt
        if not isinstance(s, datetime):
            continue
        e = ev["DTEND"].dt if "DTEND" in ev else s + ev["DURATION"].dt
        name, kind = parsed
        out.append({"start": _local(s), "end": _local(e), "summary": str(ev["SUMMARY"]),
                    "name": name, "code": subject_code(name), "kind": kind,
                    "location": str(ev.get("LOCATION", ""))})
    return out


def lookup(start: datetime, end: datetime, path: Path = CACHE) -> dict:
    """Що за календарем ішло під час запису [start, end] (локальний час, без tz).

    {"code": код | None, "kind": тип | None, "candidates": set кодів,
     "summary": назва події, "unknown": [назви пар, яких немає в SUBJECTS]}

    code заповнений, лише коли одна дисципліна явно домінує (DOMINANCE);
    інакше candidates — список, з якого обирають голоси LLM. Порожні
    candidates = календар цього часу не знає (Ajax, тренування, відпрацювання).
    """
    res = {"code": None, "kind": None, "candidates": set(), "summary": "", "unknown": []}
    if os.getenv("LECTURE_SCHEDULE_HINT", "1") == "0" or end <= start or not path.exists():
        return res
    length = (end - start).total_seconds() / 60
    best = {}
    for ev in events_between(start, end, path):
        overlap = (min(end, ev["end"]) - max(start, ev["start"])).total_seconds() / 60
        if not (overlap >= MIN_OVERLAP_MIN or (overlap > 0 and overlap >= MIN_OVERLAP_SHARE * length)):
            continue
        if ev["code"] is None:
            res["unknown"].append(ev["summary"])
            continue
        if overlap > best.get(ev["code"], (0, None))[0]:
            best[ev["code"]] = (overlap, ev)
    if not best:
        return res
    ranked = sorted(best.items(), key=lambda kv: -kv[1][0])
    res["candidates"] = set(best)
    code, (top, ev) = ranked[0]
    if len(ranked) == 1 or top >= DOMINANCE * ranked[1][1][0]:
        res.update(code=code, kind=ev["kind"], summary=ev["summary"])
    return res


def expected_subjects(start: datetime, end: datetime) -> set:
    """Коди дисциплін, що за календарем ішли під час запису (для replay-інструментів)."""
    return lookup(start, end)["candidates"]


def expected_recordings(day: date, path: Path = CACHE) -> int:
    """Скільки онлайн-пар за календарем має бути записано в цей день.

    Офлайн-пари OBS не записує, тож рахуються лише події, у полі «Місце» яких
    стоїть онлайн-формат (Zoom / Meet / «Онлайн»).
    """
    a = datetime.combine(day, datetime.min.time())
    evs = events_between(a, a + timedelta(days=1), path)
    return sum(1 for e in evs if e["start"].date() == day
               and any(m in e["location"].lower() for m in ONLINE_MARKERS))


def refresh(force: bool = False, timeout: int = 30) -> str:
    """Оновлює кеш календаря. Повертає: updated | fresh | no-url | error: …

    Не кидає винятків: календар — підказка для класифікації, і його недоступність
    не має зупиняти обробку. Застарілий кеш ловить alert.sh.
    """
    if not URL_FILE.exists():
        return "no-url"
    if not force and CACHE.exists() and time.time() - CACHE.stat().st_mtime < REFRESH_MAX_AGE_S:
        return "fresh"
    url = URL_FILE.read_text(encoding="utf-8").strip()
    try:
        with urllib.request.urlopen(url, timeout=timeout) as r:
            data = r.read()
        if b"BEGIN:VCALENDAR" not in data[:500]:
            raise ValueError("відповідь не схожа на iCal")
        CACHE.parent.mkdir(parents=True, exist_ok=True)
        tmp = CACHE.with_suffix(".tmp")
        tmp.write_bytes(data)
        os.chmod(tmp, 0o600)
        os.replace(tmp, CACHE)
        return "updated"
    except Exception as e:  # noqa: BLE001
        return f"error: {type(e).__name__}: {str(e).replace(url, '<url>')}"


if __name__ == "__main__":
    cmd = sys.argv[1] if len(sys.argv) > 1 else ""
    if cmd == "refresh":
        print(refresh(force="--force" in sys.argv))
    elif cmd == "expect":
        if not CACHE.exists():
            sys.exit(1)
        day = date.fromisoformat(sys.argv[2]) if len(sys.argv) > 2 else date.today()
        print(expected_recordings(day))
    elif cmd == "lookup":
        r = lookup(datetime.fromisoformat(sys.argv[2]), datetime.fromisoformat(sys.argv[3]))
        print({**r, "candidates": sorted(r["candidates"])})
    else:
        print("usage: timetable.py refresh [--force] | expect [YYYY-MM-DD] | lookup START END")
        sys.exit(2)
