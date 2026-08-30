#!/usr/bin/env python3
"""Одноразовий OAuth-флоу, керований ззовні.

Браузер не відкривається сам: run_local_server(open_browser=False) друкує URL
згоди у stdout, і згоду можна провести у конкретній вкладці, а не в тій, яку
обере система. URL генерує саме run_local_server — генерувати його наперед не
можна, бо він виставляє redirect_uri і власний PKCE-verifier.

Результат — secrets/token.json з refresh_token.
"""
import json
import sys
from pathlib import Path

from google_auth_oauthlib.flow import InstalledAppFlow

BASE = Path(__file__).parent
SECRETS = BASE / "secrets"
SCOPES = ["https://www.googleapis.com/auth/youtube.upload",
          "https://www.googleapis.com/auth/youtube"]
PORT = 8765

flow = InstalledAppFlow.from_client_secrets_file(str(SECRETS / "client_secret.json"), SCOPES)
creds = flow.run_local_server(
    port=PORT,
    open_browser=False,
    authorization_prompt_message="AUTH_URL {url}",
    success_message="Готово. Вкладку можна закрити.",
    timeout_seconds=900,
    # access_type=offline обов`язковий, інакше refresh_token не видадуть;
    # prompt=consent — щоб видали навіть при повторній авторизації
    access_type="offline",
    prompt="consent",
)

token_path = SECRETS / "token.json"
token_path.write_text(creds.to_json(), encoding="utf-8")
data = json.loads(token_path.read_text(encoding="utf-8"))
print("TOKEN_SAVED refresh_token=" + ("yes" if data.get("refresh_token") else "NO"), flush=True)
sys.exit(0 if data.get("refresh_token") else 1)
