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

sys.path.insert(0, str(BASE / "bin"))

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


def write_transcript(dest: Path, title: str, meta: dict, body: str):
    dest.parent.mkdir(parents=True, exist_ok=True)
    front = "\n".join(f"{k}: {v}" for k, v in meta.items())
    dest.write_text(f"---\n{front}\n---\n\n# {title}\n\n{body}\n", encoding="utf-8")


# -------------------------------------------------------------------- pipeline

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
    row = db.execute("SELECT status, attempts FROM files WHERE sha256=?", (sha,)).fetchone()
    if row and row[0] in ("done", "needs_review"):
        log.warning("%s: вже оброблений (%s), видаляю дублікат з incoming", video.name, row[0])
        video.unlink()
        return
    attempts = row[1] if row else 0
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

    log.info("=== %s (%.1f МБ, sha %s) ===", video.name, st.st_size / 1e6, sha[:12])
    rec_date = datetime.fromtimestamp(st.st_mtime)

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
        stamp = rec_date.strftime("%Y-%m-%d_%H-%M")
        write_transcript(
            REVIEW / f"{stamp}_{sha[:8]}.md",
            f"Нерозпізнана лекція {stamp}",
            {"дата": rec_date.strftime("%Y-%m-%d %H:%M"), "джерело": video.name,
             "мова": LANG, "sha256": sha, "статус": "unrecognized",
             "причина": res["reason"]},
            stamped)
        shutil.move(str(video), str(REVIEW / video.name))
        set_status(db, sha, "needs_review", error=res["reason"])
        log.warning("%s: UNRECOGNIZED (%s) → _needs-review, відео збережено, на YouTube НЕ залито",
                    video.name, res["reason"])
        return

    title = safe_name(f"{res['subject']} — {res['topic']} ({rec_date.strftime('%d.%m.%Y')})")
    log.info("title: %s", title)
    set_status(db, sha, "classified", subject=res["subject"], slug=res["slug"],
               topic=res["topic"], title=title)

    # 4. YouTube (unlisted)
    import yt_upload

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

    # 7. транскрипт у outgoing
    dest = OUTGOING / res["slug"] / f"{title}.md"
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

    import yt_upload
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
    for video in files:
        try:
            process_one(db, video)
        except Exception as e:  # noqa: BLE001 — один поганий файл не має валити решту
            failed += 1
            log.exception("%s: ПОМИЛКА: %s", video.name, e)
            try:
                set_status(db, sha256_of(video), "failed", error=str(e)[:500])
            except Exception:
                pass
            for leftover in WORK.glob("*.wav"):
                leftover.unlink(missing_ok=True)
    log.info("підсумок: успішно %d, з помилками %d", len(files) - failed, failed)
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
