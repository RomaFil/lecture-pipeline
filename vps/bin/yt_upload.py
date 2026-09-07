#!/usr/bin/env python3
"""Завантаження запису лекції на YouTube як unlisted + перевірка результату.

Ніколи не public: це запис чужої пари, доступ лише за прямим посиланням.

Одноразова авторизація (робиться на Windows, де є браузер):
    python yt_upload.py auth
Далі token.json копіюється на VPS у ~/lectures/secrets/ і оновлюється сам
через refresh_token, без участі людини.

Квота YouTube Data API: 10 000 одиниць/добу, один upload ≈ 1600 → ~6 відео/добу.
Тому файли обробляються послідовно, без паралельних заливок.
"""
import json
import os
import random
import sys
import time
from pathlib import Path

from google.auth.transport.requests import Request
from google.oauth2.credentials import Credentials
from google_auth_oauthlib.flow import InstalledAppFlow
from googleapiclient.discovery import build
from googleapiclient.errors import HttpError
from googleapiclient.http import MediaFileUpload

SECRETS = Path(os.getenv("LECTURES_SECRETS", Path.home() / "lectures" / "secrets"))
CLIENT_SECRET = SECRETS / "client_secret.json"
TOKEN = SECRETS / "token.json"
# youtube.upload — власне заливка; youtube — керування плейлістами.
# Вужчого скоупа саме під плейлісти в API немає, тому `youtube` заміняє
# колишній youtube.readonly і додає право видаляти. Компроміс усвідомлений.
SCOPES = ["https://www.googleapis.com/auth/youtube.upload",
          "https://www.googleapis.com/auth/youtube"]

TITLE_MAX = 100      # жорсткий ліміт YouTube
DESC_MAX = 5000
RETRIABLE = (500, 502, 503, 504)
# Вичерпана квота НЕ входить у RETRIABLE навмисно: повторювати її в межах доби
# безглуздо, вона відновлюється сама опівночі за Тихоокеанським (10:00 за Києвом).
QUOTA_REASONS = ("quotaExceeded", "rateLimitExceeded", "userRateLimitExceeded")


class QuotaExceeded(RuntimeError):
    """Денна квота YouTube Data API вичерпана.

    Окремий тип, а не звичайний RuntimeError, бо це НЕ провина конкретного
    файлу: конвеєр мусить відкотити спробу й дочекатись відновлення квоти,
    а не спалити MAX_ATTEMPTS на трьох прогонах поспіль і забути про запис.
    """


def _is_quota(e: HttpError) -> bool:
    if e.resp.status not in (403, 429):
        return False
    body = getattr(e, "content", b"") or b""
    if isinstance(body, bytes):
        body = body.decode("utf-8", "replace")
    return any(r in body for r in QUOTA_REASONS)


def _run(request):
    """Виконує запит API, відокремлюючи вичерпану квоту від решти помилок."""
    try:
        return request.execute()
    except HttpError as e:
        if _is_quota(e):
            raise QuotaExceeded(f"квота YouTube Data API вичерпана: {e}") from e
        raise


def service():
    if not TOKEN.exists():
        raise RuntimeError(
            f"немає {TOKEN} — спочатку виконай одноразову авторизацію:\n"
            f"    python yt_upload.py auth   (на машині з браузером)")
    creds = Credentials.from_authorized_user_file(str(TOKEN), SCOPES)
    if not creds.valid:
        if not (creds.expired and creds.refresh_token):
            raise RuntimeError("token.json недійсний і не має refresh_token — авторизуйся заново")
        creds.refresh(Request())
        TOKEN.write_text(creds.to_json(), encoding="utf-8")
        TOKEN.chmod(0o600)
    return build("youtube", "v3", credentials=creds, cache_discovery=False)


def authorize():
    """Одноразовий OAuth-флоу з локальним браузером. Створює token.json."""
    if not CLIENT_SECRET.exists():
        raise SystemExit(f"немає {CLIENT_SECRET} — завантаж OAuth client (Desktop app) "
                         "з Google Cloud Console")
    flow = InstalledAppFlow.from_client_secrets_file(str(CLIENT_SECRET), SCOPES)
    creds = flow.run_local_server(port=0, prompt="consent", access_type="offline")
    SECRETS.mkdir(parents=True, exist_ok=True)
    TOKEN.write_text(creds.to_json(), encoding="utf-8")
    TOKEN.chmod(0o600)
    has_refresh = bool(json.loads(TOKEN.read_text(encoding="utf-8")).get("refresh_token"))
    print(f"збережено {TOKEN}; refresh_token присутній: {has_refresh}")
    if not has_refresh:
        print("УВАГА: без refresh_token конвеєр працюватиме лише годину. "
              "Відкликай доступ на myaccount.google.com/permissions і авторизуйся заново.")


def upload(path: Path, title: str, description: str) -> str:
    """Заливає файл як unlisted. Повертає video_id. Кидає виняток при невдачі."""
    body = {
        "snippet": {
            "title": title[:TITLE_MAX],
            "description": description[:DESC_MAX],
            "categoryId": "27",          # Education
            "defaultLanguage": "uk",
        },
        "status": {
            "privacyStatus": "unlisted",  # ніколи не public
            "selfDeclaredMadeForKids": False,
        },
    }
    media = MediaFileUpload(str(path), chunksize=8 * 1024 * 1024, resumable=True,
                            mimetype="video/*")
    request = service().videos().insert(part="snippet,status", body=body, media_body=media)

    response, error, retry = None, None, 0
    while response is None:
        try:
            _, response = request.next_chunk()
        except HttpError as e:
            # Квоту перевіряємо ПЕРШОЮ: 403 не входить у RETRIABLE, тож без цього
            # вона летіла б нагору звичайним HttpError і коштувала б спроби.
            if _is_quota(e):
                raise QuotaExceeded(f"квота вичерпана під час заливки: {e}") from e
            if e.resp.status in RETRIABLE:
                error = e
            else:
                raise
        except (OSError, IOError) as e:
            error = e
        if error is not None:
            retry += 1
            if retry > 8:
                raise RuntimeError(f"upload не вдався після {retry} спроб: {error}")
            sleep = min(60, 2 ** retry) + random.random()
            print(f"тимчасова помилка ({error}), повтор через {sleep:.1f}s", file=sys.stderr)
            time.sleep(sleep)
            error = None
    if not response or "id" not in response:
        raise RuntimeError(f"YouTube не повернув id: {response}")
    return response["id"]


def long_uploads_allowed() -> tuple[bool, str]:
    """Чи дозволені відео довші за 15 хвилин.

    Значення `eligible` НЕ означає дозвіл — це «канал може увімкнути довгі
    завантаження, але ще не увімкнув». Дозвіл — рівно `allowed`. Плутанина між
    цими двома станами 30.08.2026 коштувала втраченої 72-хвилинної лекції:
    заливка пройшла, а YouTube відхилив її вже після видалення файлу з VPS.
    """
    items = _run(service().channels().list(part="status", mine=True)).get("items", [])
    if not items:
        return False, "канал не знайдено"
    status = items[0]["status"].get("longUploadsStatus", "невідомо")
    return status == "allowed", status


def check_processed(video_id: str) -> tuple[bool, dict]:
    """Сувора перевірка для видалення архіву: YouTube мусить ЗАВЕРШИТИ обробку.

    Відрізняється від verify() тим, що `uploaded` тут не приймається: це лише
    «файл прийнято», і відхилення може прийти згодом.
    """
    ok, info = verify(video_id)
    info["processed"] = info.get("uploadStatus") == "processed"
    return bool(ok and info["processed"]), info


def verify(video_id: str) -> tuple[bool, dict]:
    """Підтверджує, що відео справді існує, unlisted і не відхилене.

    Відео видаляється з VPS лише після True — «команда відпрацювала без помилки»
    підтвердженням не вважається.
    """
    items = _run(service().videos().list(part="status,snippet", id=video_id)).get("items", [])
    if not items:
        return False, {"error": "відео з таким id не знайдено"}
    status = items[0]["status"]
    info = {
        "privacyStatus": status.get("privacyStatus"),
        "uploadStatus": status.get("uploadStatus"),
        "failureReason": status.get("failureReason"),
        "rejectionReason": status.get("rejectionReason"),
        "title": items[0]["snippet"].get("title"),
    }
    ok = (info["privacyStatus"] == "unlisted"
          and info["uploadStatus"] in ("uploaded", "processed")
          and not info["failureReason"] and not info["rejectionReason"])
    return ok, info


if __name__ == "__main__":
    cmd = sys.argv[1] if len(sys.argv) > 1 else ""
    if cmd == "auth":
        authorize()
    elif cmd == "verify" and len(sys.argv) == 3:
        ok, info = verify(sys.argv[2])
        print(json.dumps({"ok": ok, **info}, ensure_ascii=False, indent=2))
    elif cmd == "quota":
        # дешева перевірка, що токен живий (1 одиниця квоти)
        svc = service()
        ch = _run(svc.channels().list(part="snippet", mine=True))
        print(json.dumps({"channel": ch["items"][0]["snippet"]["title"]},
                         ensure_ascii=False))
    else:
        print(__doc__)
        sys.exit(2)
