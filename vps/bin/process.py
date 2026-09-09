#!/usr/bin/env python3
"""Конвеєр обробки лекційних записів на VPS.

incoming/*.mkv → whisper → класифікація локальною LLM → YouTube (unlisted) → видалення відео.
Транскрипт лишається в outgoing/<slug>/ і його забирає Windows pull-скрипт.

Стан у SQLite за sha256 файлу: один і той самий запис ніколи не обробляється двічі,
навіть якщо його перезалили під іншим іменем.

Нічого не видаляється, доки наступний крок не підтвердив успіх:
  - wav видаляється лише після успішної транскрипції;
  - відео видаляється лише після того, як YouTube через videos.list підтвердив
    id, privacyStatus=unlisted та uploadStatus не failed/rejected.
"""
import fcntl
import hashlib
import json
import logging
import os
import re
import shutil
import sqlite3
import subprocess
import sys
import time
import warnings
from datetime import datetime
from pathlib import Path

BASE = Path(os.getenv("LECTURES_HOME", Path.home() / "lectures"))
INCOMING = BASE / "incoming"
OUTGOING = BASE / "outgoing"
WORK = BASE / "work"
ARCHIVE = BASE / "archive"
REVIEW = BASE / "_needs-review"
LOGS = BASE / "logs"
DB_PATH = BASE / "state.db"
LOCK_PATH = BASE / ".process.lock"

WHISPER_MODEL = os.getenv("WHISPER_MODEL", "large-v3")
LANG = os.getenv("WHISPER_LANG", "uk")
VIDEO_EXT = {".mkv", ".mp4", ".m4v", ".mov", ".webm", ".flv"}
MAX_ATTEMPTS = 3
# файл, змінений щойно, може ще докачуватись — чекаємо, поки він «устоїться»
SETTLE_SECONDS = 60
# скільки днів тримати відео на VPS після заливки, перш ніж видалити.
# Місця вдосталь (350+ ГБ), а тиждень покриває і затримки обробки на YouTube,
# і час помітити проблему очима. Понад 15 хв відео YouTube іноді обробляє годинами.
RETENTION_DAYS = int(os.getenv("LECTURE_RETENTION_DAYS", "7"))
# ліміт YouTube для неверифікованих каналів
LONG_VIDEO_SECONDS = 15 * 60
# Стеля тривалості запису — груба, і навмисно.
#
# Заміряний факт: аяксівське стажування 08.09.2026 йшло 3 год 28 хв (заявлені
# 17:00-20:00 плюс перебір), здвоєна лекція ОТК у четвер — 3,5 год. Тобто штатне
# заняття цілком доходить до 3,5 год, і стеля мусить лежати помітно вище, інакше
# вона почне вбивати справжні записи. П'ять годин — це запас у півтори години.
#
# Чесно про цінність: як фільтр «це не заняття» стеля слабка — випадкове стороннє
# відео зазвичай коротше за 5 год і крізь неї пройде. Справжній захист від чужого
# запису — закритий список дисциплін у classify.py, який відправить незнайоме в
# _needs-review. Стеля ловить інший клас відмови: багатогодинний файл, який зайняв
# би VPS на півдоби (транскрипція йде 0,71x реального часу) і витіснив би записи
# того ж дня. Тому вона питається ДО ffmpeg і whisper — а сам probe_duration
# корисний ще й тим, що пише тривалість у лог до початку роботи. 09.09.2026 саме
# цього рядка й бракувало, щоб побачити проблему одразу.
MAX_VIDEO_SECONDS = int(os.getenv("LECTURE_MAX_SECONDS", str(5 * 3600)))
# Жорсткий ліміт заголовка YouTube. Продубльований тут навмисно: yt_upload ріже
# title[:100] з ХВОСТА, а в хвості у нас дата — тобто мовчазне обрізання забирало б
# саме те, за чим запис потім шукають. Тому вкорочуємо тему, а не заголовок.
TITLE_MAX = 100
# Права на відео, що приїжджають з Windows. `scp -p` зберігає режим ДЖЕРЕЛА,
# а Windows віддає 0666 — тобто записи лягали на VPS доступними на запис усім
# у системі. Аудит безпеки за серпень 2026 окремо засвідчив, що файлів зі
# світовим записом у домашній теці немає; конвеєр це тихо порушив.
# Прибрати `-p` не можна: разом із режимом він переносить mtime, а mtime — це
# дата запису лекції, з якої будується вся назва.
VIDEO_MODE = 0o640
# Тип заняття в назві. Лекція — випадок за замовчуванням і мітки не отримує:
# інакше 90 % записів носили б однакове зайве слово.
# Мітка йде В ДУЖКИ поруч із датою, а не префіксом «Практика: » перед темою, бо
# safe_name() вирізає двокрапку як заборонений у Windows символ — префікс
# перетворювався б на «Практика тема». Знайдено тестом, не здогадкою.
KIND_LABEL = {"практика": "практика", "лабораторна": "лабораторна"}

sys.path.insert(0, str(BASE / "bin"))

# google.api_core на кожному імпорті попереджає, що Python 3.10 (наш у venv)
# перестане підтримуватись 04.10.2026. Факт справжній і записаний у нотатці
# конвеєра — але оскільки yt_upload тепер імпортується на рівні модуля, це
# попередження друкувалось би в run.log на КОЖНОМУ прогоні cron, тобто ~96
# рядків на добу навіть у дні без записів. Попередження, яке повторюється
# 48 разів на день, читати перестають — а разом із ним і решту лога.
warnings.filterwarnings("ignore", category=FutureWarning,
                        module=r"google\.api_core.*")

# Імпорт саме тут, після sys.path: yt_upload потрібен на рівні модуля, щоб main()
# міг спіймати yt_upload.QuotaExceeded окремо від решти помилок.
import yt_upload  # noqa: E402

for d in (INCOMING, OUTGOING, WORK, ARCHIVE, REVIEW, LOGS):
    d.mkdir(parents=True, exist_ok=True)

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(message)s",
    handlers=[logging.FileHandler(LOGS / "process.log", encoding="utf-8"),
              logging.StreamHandler()],
)
log = logging.getLogger()


# --------------------------------------------------------------------------- db

def open_db():
    db = sqlite3.connect(DB_PATH)
    db.execute("""CREATE TABLE IF NOT EXISTS files(
        sha256   TEXT PRIMARY KEY,
        filename TEXT,
        size     INTEGER,
        mtime    INTEGER,
        status   TEXT,
        subject  TEXT,
        slug     TEXT,
        topic    TEXT,
        title    TEXT,
        video_id TEXT,
        attempts INTEGER DEFAULT 0,
        error    TEXT,
        created  TEXT,
        updated  TEXT)""")
    db.commit()
    return db


def set_status(db, sha, status, **fields):
    cols = ", ".join(f"{k}=?" for k in fields)
    sql = f"UPDATE files SET status=?, updated=?{', ' + cols if cols else ''} WHERE sha256=?"
    db.execute(sql, [status, datetime.now().isoformat(timespec="seconds"),
                     *fields.values(), sha])
    db.commit()


def sha256_of(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


# ------------------------------------------------------------------ transcribe

def probe_duration(video: Path) -> float:
    """Тривалість відео в секундах через ffprobe, 0.0 якщо визначити не вдалося.

    Питається ДО ffmpeg і whisper: обидва коштують години, а ffprobe читає лише
    заголовок контейнера. Нуль повертається свідомо замість винятку — незрозумілий
    контейнер не привід відмовляти запису в обробці, це вирішить транскрипція.
    """
    try:
        r = subprocess.run(
            ["ffprobe", "-v", "error", "-show_entries", "format=duration",
             "-of", "default=noprint_wrappers=1:nokey=1", str(video)],
            capture_output=True, text=True, timeout=120,
        )
        return float(r.stdout.strip()) if r.returncode == 0 and r.stdout.strip() else 0.0
    except (ValueError, OSError, subprocess.SubprocessError) as e:  # noqa: BLE001
        log.warning("ffprobe: не вдалося визначити тривалість %s: %s", video.name, e)
        return 0.0


def send_to_review(db, video: Path, sha: str, rec_date: datetime, reason: str,
                   body: str = ""):
    """Кладе відео й пояснювальну нотатку в _needs-review. Нічого не видаляє."""
    stamp = rec_date.strftime("%Y-%m-%d_%H-%M")
    write_transcript(
        REVIEW / f"{stamp}_{sha[:8]}.md",
        f"Нерозпізнана лекція {stamp}",
        {"дата": rec_date.strftime("%Y-%m-%d %H:%M"), "джерело": video.name,
         "мова": LANG, "sha256": sha, "статус": "unrecognized",
         "причина": reason},
        body)
    shutil.move(str(video), str(REVIEW / video.name))
    set_status(db, sha, "needs_review", error=reason)


def extract_wav(video: Path, wav: Path):
    subprocess.run(
        ["ffmpeg", "-y", "-i", str(video), "-vn", "-ar", "16000", "-ac", "1", str(wav)],
        check=True, capture_output=True,
    )


def transcribe(wav: Path) -> dict:
    """Транскрипція ОКРЕМИМ процесом — див. transcribe_worker.py щодо причини.

    Коротко: whisper тримає ~2.9 ГБ, Ollama слідом просить ~4.7 ГБ, а на VPS
    усього 5.8 ГБ. Лише вихід процесу гарантовано повертає пам'ять ОС.
    """
    out = wav.with_suffix(".json")
    log.info("whisper: %s (cpu/int8) у окремому процесі", WHISPER_MODEL)
    t0 = time.time()
    r = subprocess.run(
        [sys.executable, str(BASE / "bin" / "transcribe_worker.py"), str(wav), str(out)],
        capture_output=True, text=True,
        env={**os.environ, "WHISPER_MODEL": WHISPER_MODEL, "WHISPER_LANG": LANG},
    )
    if r.returncode != 0:
        raise RuntimeError(f"transcribe_worker впав ({r.returncode}): {r.stderr[-800:]}")
    data = json.loads(out.read_text(encoding="utf-8"))
    out.unlink(missing_ok=True)
    log.info("whisper: %d сегментів, %.0f с аудіо, мова=%s (p=%.2f), витрачено %.0f с",
             data["segments"], data["duration"], data["language"],
             data["language_probability"], time.time() - t0)
    return data


# ----------------------------------------------------------------------- output

def safe_name(s: str) -> str:
    s = re.sub(r'[\\/:*?"<>|]', "", s)
    return re.sub(r"\s+", " ", s).strip()


def make_title(subject: str, topic: str, kind: str, rec_date: datetime) -> str:
    """`<Дисципліна> — <Тема> ([тип, ]ДД.ММ.РРРР)`, гарантовано ≤ TITLE_MAX.

    Дата обов'язково доживає до кінця: якщо все разом не влазить у ліміт
    YouTube, ріжеться ТЕМА, а не заголовок з хвоста. Найдовша дисципліна
    (CISCO, 50 символів) плюс тема на дозволені схемою 70 давали 136 символів —
    тобто дату зрізало ще до появи міток типу, просто цього ніхто не бачив.
    """
    label = KIND_LABEL.get(kind, "")
    date = rec_date.strftime("%d.%m.%Y")
    suffix = f" ({label}, {date})" if label else f" ({date})"
    head = f"{subject} — "
    room = TITLE_MAX - len(head) - len(suffix)
    if room < 10:
        # Дисципліна аномально довга — ріжемо вже її, аби вціліли тип і дата.
        head = head[:TITLE_MAX - len(suffix) - 10]
        room = 10
    if len(topic) > room:
        # -1 під саме троєкрапку: без цього результат виходив на символ довшим
        # за ліміт, і YouTube зрізав би хвіст із датою.
        topic = topic[:room - 1].rstrip(" .,;:-—") + "…"
    return safe_name(head + topic + suffix)


def write_transcript(dest: Path, title: str, meta: dict, body: str):
    dest.parent.mkdir(parents=True, exist_ok=True)
    front = "\n".join(f"{k}: {v}" for k, v in meta.items())
    dest.write_text(f"---\n{front}\n---\n\n# {title}\n\n{body}\n", encoding="utf-8")


# -------------------------------------------------------------------- pipeline

def harden_modes():
    """Знімає світовий доступ із файлів, що приїхали з Windows.

    Ідемпотентна: 0640 світових бітів не має, тож повторні прогони мовчать.
    Робиться на початку кожного запуску, а не лише при обробці — інакше файл,
    який чекає в черзі години, весь цей час лежить доступним на запис.
    """
    for d in (INCOMING, ARCHIVE, REVIEW):
        for p in d.glob("*"):
            try:
                if p.is_file() and (p.stat().st_mode & 0o007):
                    p.chmod(VIDEO_MODE)
                    log.info("права: %s → %o", p.name, VIDEO_MODE)
            except OSError as e:  # noqa: BLE001
                log.warning("права: не вдалося змінити %s: %s", p.name, e)


def candidates() -> list[Path]:
    now = time.time()
    out = []
    for p in sorted(INCOMING.iterdir()):
        if not p.is_file() or p.suffix.lower() not in VIDEO_EXT:
            continue
        if now - p.stat().st_mtime < SETTLE_SECONDS:
            log.info("%s: щойно змінений, чекаю наступного запуску", p.name)
            continue
        out.append(p)
    return out


def process_one(db, video: Path):
    sha = sha256_of(video)
    row = db.execute(
        "SELECT status, attempts, video_id FROM files WHERE sha256=?", (sha,)).fetchone()
    if row and row[0] in ("done", "needs_review"):
        log.warning("%s: вже оброблений (%s), видаляю дублікат з incoming", video.name, row[0])
        video.unlink()
        return
    attempts = row[1] if row else 0
    prior_video_id = row[2] if row else None
    if attempts >= MAX_ATTEMPTS:
        log.error("%s: вичерпані спроби (%d), пропускаю", video.name, attempts)
        return

    st = video.stat()
    now_iso = datetime.now().isoformat(timespec="seconds")
    if not row:
        db.execute(
            "INSERT INTO files(sha256, filename, size, mtime, status, attempts, created, updated)"
            " VALUES(?,?,?,?,?,0,?,?)",
            (sha, video.name, st.st_size, int(st.st_mtime), "new", now_iso, now_iso))
        db.commit()
    db.execute("UPDATE files SET attempts=attempts+1 WHERE sha256=?", (sha,))
    db.commit()

    rec_date = datetime.fromtimestamp(st.st_mtime)
    duration = probe_duration(video)
    log.info("=== %s (%.1f МБ, %s, sha %s) ===", video.name, st.st_size / 1e6,
             f"{duration/3600:.2f} год" if duration else "тривалість невідома", sha[:12])

    # 0. запобіжник на тривалість — ДО ffmpeg і whisper, бо саме вони коштують години.
    # 08.09.2026 у watch-теку випадково потрапив тригодинний запис стажування Ajax:
    # конвеєр чесно взяв його в роботу і на пів дня зайняв VPS. Заняття тепер у
    # списку дисциплін, але сам клас відмови лишається — тому стеля явна.
    if duration and duration > MAX_VIDEO_SECONDS:
        reason = (f"тривалість {duration/3600:.1f} год перевищує стелю "
                  f"{MAX_VIDEO_SECONDS/3600:.1f} год — це не схоже на заняття")
        send_to_review(db, video, sha, rec_date, reason)
        log.warning("%s: ЗАДОВГЕ (%s) → _needs-review, не транскрибується", video.name, reason)
        return

    # 1. аудіо
    wav = WORK / (sha[:12] + ".wav")
    extract_wav(video, wav)

    # 2. транскрипція
    tr = transcribe(wav)
    stamped, plain = tr["stamped"], tr["plain"]
    wav.unlink()  # wav більше не потрібен — транскрипт уже на диску
    # Поріг ловить лише справді провальний запис (тиша, збитий кодек, нема доріжки).
    # Ставити його високо не можна: коротка пара чи тестовий кліп дають мало тексту
    # цілком законно. Якщо мова розпізналась, але зміст беззмістовний — це вже робота
    # класифікатора, який поверне НЕВІДОМО і відправить файл у _needs-review.
    if tr["segments"] == 0 or len(plain.strip()) < 40:
        raise RuntimeError(
            f"транскрипт порожній: {tr['segments']} сегментів, "
            f"{len(plain.strip())} символів на {tr['duration']:.0f} с аудіо")
    set_status(db, sha, "transcribed")

    # 3. класифікація
    import classify as clf
    res = clf.classify(plain)
    log.info("класифікатор: %s", json.dumps(res.get("raw", {}), ensure_ascii=False))

    if not res["ok"]:
        # unrecognized — нічого не вгадуємо, нічого не видаляємо, нічого не аплоадимо
        send_to_review(db, video, sha, rec_date, res["reason"], stamped)
        log.warning("%s: UNRECOGNIZED (%s) → _needs-review, відео збережено, на YouTube НЕ залито",
                    video.name, res["reason"])
        return

    title = make_title(res["subject"], res["topic"], res["kind"], rec_date)
    log.info("title: %s (тип: %s)", title, res["kind"])
    set_status(db, sha, "classified", subject=res["subject"], slug=res["slug"],
               topic=res["topic"], title=title)

    # 4. YouTube (unlisted)
    # Запобіжник: без верифікації каналу YouTube мовчки відхилить усе довше за
    # 15 хвилин — уже ПІСЛЯ того, як прийме файл. Краще не заливати взагалі,
    # ніж спалити квоту й отримати відмову заднім числом.
    if tr["duration"] > LONG_VIDEO_SECONDS:
        allowed, status = yt_upload.long_uploads_allowed()
        if not allowed:
            raise RuntimeError(
                f"відео {tr['duration']/60:.0f} хв, а канал не має дозволу на довгі "
                f"завантаження (longUploadsStatus={status}; потрібне саме 'allowed', "
                f"'eligible' дозволом НЕ є). Підтверди номер телефону на "
                f"youtube.com/verify і запусти обробку знову")

    description = (
        f"Запис заняття: {res['subject']}\n"
        f"Тип: {res['kind']}\n"
        f"Дата: {rec_date.strftime('%d.%m.%Y %H:%M')}\n\n"
        "Особистий архів. Доступ лише за прямим посиланням."
    )
    # Повторна спроба після `upload_unverified` НЕ має заливати відео вдруге:
    # файл уже на YouTube, а друга заливка коштує ще 1600 одиниць квоти (з 10 000
    # на добу) і лишає дубль на каналі. Спершу питаємо про старий id — це 1 одиниця.
    video_id = None
    if prior_video_id:
        ok, info = yt_upload.verify(prior_video_id)
        if ok:
            video_id = prior_video_id
            log.info("YouTube: відео вже залите раніше (id=%s), повторної заливки немає",
                     video_id)
        else:
            log.warning("YouTube: попереднє відео %s не підтверджується (%s) — заливаю наново",
                        prior_video_id, info.get("uploadStatus") or info.get("error"))
    if video_id is None:
        video_id = yt_upload.upload(video, title, description)
        log.info("YouTube: завантажено id=%s", video_id)

    # 5. підтвердження ПЕРЕД видаленням — сам факт «команда не впала» не рахується
    ok, info = yt_upload.verify(video_id)
    if not ok:
        set_status(db, sha, "upload_unverified", video_id=video_id,
                   error=json.dumps(info, ensure_ascii=False))
        raise RuntimeError(f"YouTube не підтвердив завантаження: {info}")
    log.info("YouTube: підтверджено %s privacy=%s upload=%s",
             video_id, info.get("privacyStatus"), info.get("uploadStatus"))

    # 6. плейліст дисципліни. Навмисно НЕ фатально: відео вже підтверджено на
    # YouTube, і невдале розкладання по полицях не привід валити обробку чи
    # лишати відео на диску. Розкласти потім можна вручну через playlists.py.
    playlist_url = ""
    try:
        import playlists
        pid = playlists.place(video_id, res["subject"])
        playlist_url = f"https://youtube.com/playlist?list={pid}"
        log.info("плейліст: додано у «%s» (%s)", res["subject"], pid)
    except Exception as e:  # noqa: BLE001
        log.warning("не вдалося додати в плейліст (відео на місці, це не критично): %s", e)

    # 7. транскрипт у outgoing.
    # Час запису в імені файлу обов'язковий: `title` містить лише дату, і два
    # заняття з однієї дисципліни в один день (пара + практика, або здвоєна
    # лекція) з однаковою темою дали б однакове ім'я — а write_transcript
    # мовчки перезаписав би перший транскрипт. Перевіряти колізію по наявності
    # файлу не можна: перший міг уже поїхати на ПК і зникнути з outgoing.
    # Час у назву, а не в `title`: заголовок на YouTube лишається чистим.
    dest = OUTGOING / res["slug"] / f"{title} [{rec_date.strftime('%H-%M')}].md"
    write_transcript(dest, title, {
        "дисципліна": res["subject"],
        "тип": res["kind"],
        "тема": res["topic"],
        "дата": rec_date.strftime("%Y-%m-%d %H:%M"),
        "мова": LANG,
        "тривалість_с": tr["duration"],
        "модель": f"faster-whisper {WHISPER_MODEL} (cpu/int8)",
        "youtube": f"https://youtu.be/{video_id}",
        "плейліст": playlist_url or "—",
        "джерело": video.name,
        "sha256": sha,
    }, stamped)
    log.info("транскрипт: %s", dest)

    # 8. відео НЕ видаляється одразу, а лежить в архіві тиждень.
    # Причина: YouTube приймає файл асинхронно. `uploadStatus=uploaded` означає
    # лише «прийнято», і відхилення (задовге відео, Content ID, збій обробки)
    # може прийти згодом — коли видаляти вже пізно. Саме так 30.08.2026 було
    # втрачено 72-хвилинну лекцію. Архів чистить purge_archive() лише після
    # того, як YouTube підтвердить `processed`.
    archived = ARCHIVE / f"{sha[:12]}__{video.name}"
    ARCHIVE.mkdir(parents=True, exist_ok=True)
    shutil.move(str(video), str(archived))
    set_status(db, sha, "done", video_id=video_id)
    log.info("%s: ГОТОВО → %s", video.name, title)
    log.info("відео в архіві до підтвердження обробки: %s", archived.name)


def purge_archive(db):
    """Видаляє з архіву відео, старші за RETENTION_DAYS — але лише підтверджені.

    Перед видаленням у YouTube перепитується стан: файл іде в кошик тільки якщо
    обробка ЗАВЕРШЕНА (`processed`) і відео на місці. Якщо YouTube його відхилив
    чи загубив — файл лишається, а в лог падає гучне попередження. Це і є та
    сітка, якої бракувало, коли 72-хвилинну лекцію видалили за «uploaded».
    """
    cutoff = time.time() - RETENTION_DAYS * 86400
    files = [p for p in ARCHIVE.glob("*") if p.is_file() and p.stat().st_mtime < cutoff]
    if not files:
        return

    log.info("архів: %d файл(ів) старші за %d дн., перевіряю стан на YouTube",
             len(files), RETENTION_DAYS)
    for p in files:
        sha12 = p.name.split("__", 1)[0]
        row = db.execute(
            "SELECT sha256, video_id, title FROM files WHERE sha256 LIKE ?", (sha12 + "%",)
        ).fetchone()
        if not row or not row[1]:
            log.warning("архів: %s — немає video_id у стані, лишаю файл", p.name)
            continue
        sha, video_id, title = row
        try:
            ok, info = yt_upload.check_processed(video_id)
        except yt_upload.QuotaExceeded:
            # Далі по списку буде те саме — припиняємо, а не довбимо вичерпану квоту.
            raise
        except Exception as e:  # noqa: BLE001
            log.warning("архів: %s — не вдалося перевірити (%s), лишаю файл", p.name, e)
            continue
        if ok:
            p.unlink()
            set_status(db, sha, "purged")
            log.info("архів: видалено %s (YouTube підтвердив processed)", p.name)
        else:
            set_status(db, sha, "upload_failed",
                       error=json.dumps(info, ensure_ascii=False)[:500])
            log.error("архів: %s — YouTube НЕ підтвердив обробку (%s). "
                      "Файл ЗБЕРЕЖЕНО, розібратись вручну: %s",
                      p.name, info.get("uploadStatus") or info.get("error"), title)


def main():
    lock = open(LOCK_PATH, "w")
    try:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except BlockingIOError:
        log.info("інший запуск уже працює — виходжу")
        return 0

    db = open_db()

    # Гігієна прав робиться першою і завжди: файл, що чекає в черзі, не має
    # години лежати доступним на запис усім у системі.
    try:
        harden_modes()
    except Exception as e:  # noqa: BLE001
        log.warning("не вдалося вирівняти права (не критично): %s", e)

    # Прибирання архіву робиться щоразу, навіть коли нових записів немає —
    # інакше воно ніколи б не спрацювало у «тихі» дні.
    try:
        purge_archive(db)
    except Exception as e:  # noqa: BLE001
        log.warning("прибирання архіву не вдалося (не критично): %s", e)

    files = candidates()
    if not files:
        log.info("нових файлів немає")
        return 0
    log.info("до обробки: %d файл(ів)", len(files))

    failed = 0
    quota_hit = False
    for video in files:
        try:
            process_one(db, video)
        except yt_upload.QuotaExceeded as e:
            # Вичерпана квота — не провина файлу, тому спробу ВІДКОЧУЄМО. Інакше
            # три прогони cron поспіль вичерпали б MAX_ATTEMPTS, і запис назавжди
            # лишився б із «вичерпані спроби, пропускаю» — при тому що квота
            # відновлюється сама (опівночі за Тихоокеанським = 10:00 за Києвом).
            # Решту черги теж не чіпаємо: їй впаде рівно та сама помилка.
            try:
                db.execute("UPDATE files SET attempts=attempts-1 WHERE sha256=?",
                           (sha256_of(video),))
                db.commit()
            except Exception:  # noqa: BLE001
                pass
            quota_hit = True
            log.error("КВОТА YouTube вичерпана на %s: %s. Обробка зупинена, черга "
                      "дочекається відновлення квоти (~10:00 за Києвом).", video.name, e)
            break
        except Exception as e:  # noqa: BLE001 — один поганий файл не має валити решту
            failed += 1
            log.exception("%s: ПОМИЛКА: %s", video.name, e)
            try:
                set_status(db, sha256_of(video), "failed", error=str(e)[:500])
            except Exception:
                pass
            for leftover in WORK.glob("*.wav"):
                leftover.unlink(missing_ok=True)
    log.info("підсумок: успішно %d, з помилками %d%s", len(files) - failed, failed,
             ", зупинено через квоту" if quota_hit else "")
    return 1 if (failed or quota_hit) else 0


if __name__ == "__main__":
    sys.exit(main())
