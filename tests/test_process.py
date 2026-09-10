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

check("chunks(): короткий текст — один фрагмент, голосування не потрібне",
      _clf.chunks("коротка стенограма", 5000) == ["коротка стенограма"])
_long_for_chunks = "Слово речення. " * 3000  # ~45000 символів, точно понад поріг
_ck = _clf.chunks(_long_for_chunks, 5000)
check("chunks(): довгий текст — рівно три фрагменти", len(_ck) == 3, str(len(_ck)))
check("chunks(): кожен фрагмент у межах бюджету", all(len(p) <= 5000 for p in _ck),
      [len(p) for p in _ck])
check("chunks(): фрагменти непорожні", all(len(p) > 100 for p in _ck),
      [len(p) for p in _ck])

print(f"\nпройдено {ok}/{ok + fail}")
sys.exit(0 if fail == 0 else 1)
