#!/usr/bin/env python3
"""Ручний розбір запису з _needs-review, БЕЗ повторної транскрипції.

    resolve_review.py "<відео з _needs-review>" <КОД_ДИСЦИПЛІНИ> <kind> "<тема>" [--go]

Без --go — пробний прогін: друкує назву, файл транскрипту й нічого не чіпає.

Коли потрібен: класифікатор розійшовся у голосах (1/1/1), відео й транскрипт лежать
у _needs-review, а людина за змістом знає, що це за заняття. Транскрипт уже є в
нотатці поруч із відео, тож whisper не повторюється (година-дві CPU).

Кроки повторюють process_one() від заливки й далі: YouTube (unlisted) → верифікація
→ плейліст → транскрипт в outgoing → відео в archive → статус done. Нотатка з
_needs-review переїжджає в archive під ім'ям `<sha12>__...md`, щоб алерт про
_needs-review погас, а purge_archive() прибрав її разом із відео через ретенцію.

Тримає той самий lock, що й process.py, — паралельно з обробкою не запускається.
"""
import fcntl
import re
import sys
from datetime import datetime
from pathlib import Path

from process import (ARCHIVE, LOCK_PATH, LONG_VIDEO_SECONDS, OUTGOING, REVIEW, LANG,
                     WHISPER_MODEL, log, make_title, open_db, probe_duration,
                     set_status, transcript_name, write_transcript)
import classify as clf
import yt_upload

KINDS = {"лекція", "практика", "лабораторна", "тренування"}


def load_note(video: Path):
    """Знаходить нотатку, що належить відео, і повертає (шлях, sha, тіло транскрипту)."""
    for note in REVIEW.glob("*.md"):
        text = note.read_text(encoding="utf-8")
        if f"джерело: {video.name}\n" in text:
            sha = re.search(r"^sha256: (\w{64})$", text, re.M).group(1)
            body = re.search(r"\n---\n\n# [^\n]*\n\n(.*)\n$", text, re.S).group(1)
            return note, sha, body
    sys.exit(f"нотатки для {video.name} у _needs-review не знайдено")


def main(argv):
    go = "--go" in argv
    args = [a for a in argv if a != "--go"]
    if len(args) != 4:
        sys.exit(__doc__)
    name, code, kind, topic = args
    video = REVIEW / name
    if not video.is_file():
        sys.exit(f"немає файлу {video}")
    if code not in clf.SUBJECTS:
        sys.exit(f"невідомий код дисципліни {code}; є: {', '.join(clf.SUBJECTS)}")
    if kind not in KINDS:
        sys.exit(f"невідомий тип {kind}; є: {', '.join(sorted(KINDS))}")

    full, slug = clf.SUBJECTS[code]
    note, sha, stamped = load_note(video)
    rec_date = datetime.fromtimestamp(video.stat().st_mtime)
    title = make_title(full, topic, kind, rec_date)
    dest = OUTGOING / slug / transcript_name(title, rec_date)
    duration = probe_duration(video)
    print(f"відео:      {video.name} ({video.stat().st_size / 1e6:.0f} МБ, {duration / 60:.0f} хв)")
    print(f"дисципліна: {full} ({slug}), тип: {kind}")
    print(f"title:      {title} ({len(title)} симв.)")
    print(f"транскрипт: {dest.name} ({len(dest.name.encode())} байт)")
    if not go:
        print("\nпробний прогін — додай --go, щоб залити")
        return 0

    lock = open(LOCK_PATH, "w")
    try:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except BlockingIOError:
        sys.exit("process.py зараз працює (lock зайнятий) — спробуй пізніше")

    db = open_db()
    log.info("=== ручний розбір %s → %s / %s ===", video.name, full, kind)
    set_status(db, sha, "classified", subject=full, slug=slug, topic=topic, title=title)

    if duration > LONG_VIDEO_SECONDS:
        allowed, status = yt_upload.long_uploads_allowed()
        if not allowed:
            sys.exit(f"longUploadsStatus={status}, потрібне 'allowed' — не заливаю")

    description = (f"Запис заняття: {full}\nТип: {kind}\n"
                   f"Дата: {rec_date.strftime('%d.%m.%Y %H:%M')}\n\n"
                   "Особистий архів. Доступ лише за прямим посиланням.")
    row = db.execute("SELECT video_id FROM files WHERE sha256=?", (sha,)).fetchone()
    video_id = None
    if row and row[0]:
        ok, _ = yt_upload.verify(row[0])
        if ok:
            video_id = row[0]
            log.info("YouTube: відео вже залите раніше (id=%s)", video_id)
    if video_id is None:
        video_id = yt_upload.upload(video, title, description)
        log.info("YouTube: завантажено id=%s", video_id)

    ok, info = yt_upload.verify(video_id)
    if not ok:
        set_status(db, sha, "upload_unverified", video_id=video_id,
                   error=str(info)[:500])
        sys.exit(f"YouTube не підтвердив завантаження: {info}")
    log.info("YouTube: підтверджено %s privacy=%s upload=%s",
             video_id, info.get("privacyStatus"), info.get("uploadStatus"))

    playlist_url = ""
    try:
        import playlists
        pid = playlists.place(video_id, full)
        playlist_url = f"https://youtube.com/playlist?list={pid}"
        log.info("плейліст: додано у «%s» (%s)", full, pid)
    except Exception as e:  # noqa: BLE001
        log.warning("не вдалося додати в плейліст (відео на місці): %s", e)

    write_transcript(dest, title, {
        "дисципліна": full, "тип": kind, "тема": topic,
        "дата": rec_date.strftime("%Y-%m-%d %H:%M"), "мова": LANG,
        "тривалість_с": duration,
        "модель": f"faster-whisper {WHISPER_MODEL} (cpu/int8)",
        "youtube": f"https://youtu.be/{video_id}",
        "плейліст": playlist_url or "—",
        "джерело": video.name, "sha256": sha,
        "розбір": "вручну (голоси класифікатора розійшлися)",
    }, stamped)
    log.info("транскрипт: %s", dest)

    ARCHIVE.mkdir(parents=True, exist_ok=True)
    video.rename(ARCHIVE / f"{sha[:12]}__{video.name}")
    note.rename(ARCHIVE / f"{sha[:12]}__{note.name}")
    set_status(db, sha, "done", video_id=video_id)
    log.info("%s: ГОТОВО (ручний розбір) → %s", video.name, title)
    print(f"\nГОТОВО: https://youtu.be/{video_id}")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
