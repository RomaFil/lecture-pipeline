#!/usr/bin/env python3
"""Розкладання залитих лекцій по плейлістах — по одному на дисципліну.

Плейлісти створюються один раз і кешуються за id у ~/lectures/playlists.json,
щоб не витрачати квоту на пошук при кожній заливці.

Ціна в дозволах: керування плейлістами вимагає скоупа `youtube` (повне керування
акаунтом), який дозволяє й видаляти відео. Вужчого скоупа саме під плейлісти
YouTube API не має. Це свідомий компроміс, узгоджений 30.08.2026.

Квота: playlists.list = 1, playlists.insert = 50, playlistItems.insert = 50 —
дрібниця проти 1600 за саму заливку відео.
"""
import json
import os
import sys
from pathlib import Path

BASE = Path(os.getenv("LECTURES_HOME", Path.home() / "lectures"))
CACHE = BASE / "playlists.json"

sys.path.insert(0, str(BASE / "bin"))

PLAYLIST_DESC = ("Записи занять. Особистий архів, доступ лише за прямим посиланням.")


def _load_cache() -> dict:
    if CACHE.exists():
        try:
            return json.loads(CACHE.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            return {}
    return {}


def _save_cache(data: dict):
    CACHE.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")


def ensure_playlist(svc, title: str) -> str:
    """Повертає id плейліста з назвою title, створюючи його за потреби."""
    cache = _load_cache()
    if title in cache:
        return cache[title]

    # Може існувати з попереднього запуску, коли кеш загубився — спершу шукаємо.
    page = None
    while True:
        r = svc.playlists().list(part="snippet", mine=True, maxResults=50,
                                 pageToken=page).execute()
        for item in r.get("items", []):
            if item["snippet"]["title"] == title:
                cache[title] = item["id"]
                _save_cache(cache)
                return item["id"]
        page = r.get("nextPageToken")
        if not page:
            break

    r = svc.playlists().insert(
        part="snippet,status",
        body={
            "snippet": {"title": title, "description": PLAYLIST_DESC,
                        "defaultLanguage": "uk"},
            # unlisted, як і самі відео: у пошуку не з'являється,
            # відкривається лише за прямим посиланням
            "status": {"privacyStatus": "unlisted"},
        },
    ).execute()
    cache[title] = r["id"]
    _save_cache(cache)
    return r["id"]


def _in_playlist(svc, playlist_id: str, video_id: str) -> bool:
    """Чи є відео вже в плейлісті (1 одиниця квоти)."""
    r = svc.playlistItems().list(part="id", playlistId=playlist_id,
                                 videoId=video_id, maxResults=1).execute()
    return bool(r.get("items"))


# Тимчасові відмови YouTube: 05.10.2026 перше додавання у щойно створений плейліст
# ЦЗОПВ дало 409 «The operation was aborted» (плейліст ще не «встиг» на стороні
# YouTube), і відео лишилось поза плейлістом — повтору не було. Тепер кілька
# спроб із паузами; перед повтором перевіряємо, чи попередня спроба не пройшла
# попри помилку, щоб не плодити дублі.
RETRY_STATUSES = (409, 429, 500, 502, 503, 504)
RETRY_DELAYS = (5, 20, 60)


def add_video(svc, playlist_id: str, video_id: str, delays=RETRY_DELAYS, sleep=None):
    import time
    from googleapiclient.errors import HttpError
    sleep = sleep or time.sleep
    for attempt in range(len(delays) + 1):
        try:
            svc.playlistItems().insert(
                part="snippet",
                body={"snippet": {"playlistId": playlist_id,
                                  "resourceId": {"kind": "youtube#video",
                                                 "videoId": video_id}}},
            ).execute()
            return
        except HttpError as e:
            if e.resp.status not in RETRY_STATUSES or attempt == len(delays):
                raise
            sleep(delays[attempt])
            try:
                if _in_playlist(svc, playlist_id, video_id):
                    return
            except HttpError:
                pass


def place(video_id: str, subject: str) -> str:
    """Кладе відео в плейліст дисципліни. Повертає id плейліста."""
    import yt_upload
    svc = yt_upload.service()
    pid = ensure_playlist(svc, subject)
    add_video(svc, pid, video_id)
    return pid


if __name__ == "__main__":
    import yt_upload
    from classify import SUBJECTS

    svc = yt_upload.service()
    if len(sys.argv) == 3:
        # ручне додавання: playlists.py <video_id> "<повна назва дисципліни>"
        pid = place(sys.argv[1], sys.argv[2])
        print(f"додано у плейліст {pid}: https://youtube.com/playlist?list={pid}")
    else:
        # без аргументів — створити всі плейлісти дисциплін наперед
        for full, _slug in SUBJECTS.values():
            pid = ensure_playlist(svc, full)
            print(f"{full:55} → {pid}")
        print(f"\nкеш: {CACHE}")
