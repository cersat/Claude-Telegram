#!/usr/bin/env python3
"""
Отправляет произвольный JSON-запрос к Telegram Bot API.

Использование:
  python3 send_json.py <метод> '<json>'

Примеры:
  python3 send_json.py sendPoll '{"question":"Что лучше?","options":["Python","Go","Rust"],"is_anonymous":false}'
  python3 send_json.py sendDice '{"emoji":"🎲"}'
  python3 send_json.py sendContact '{"phone_number":"+79001234567","first_name":"Ivan"}'
  python3 send_json.py sendMessage '{"text":"Привет!","parse_mode":"Markdown"}'

chat_id подставляется автоматически, если не указан явно.
"""

import sys
import os
import json
import requests
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from config import BOT_TOKEN, ALLOWED_CHAT_ID, BOT_API_BASE

API = f"{BOT_API_BASE}/bot{BOT_TOKEN}"

def send_json(method: str, payload: dict) -> dict:
    if "chat_id" not in payload:
        payload["chat_id"] = ALLOWED_CHAT_ID
    r = requests.post(f"{API}/{method}", json=payload)
    return r.json()

if __name__ == "__main__":
    if len(sys.argv) < 3:
        print("Usage: python3 send_json.py <method> '<json>'")
        print("Example: python3 send_json.py sendPoll '{\"question\":\"...\",\"options\":[\"A\",\"B\"]}'")
        sys.exit(1)

    method = sys.argv[1]
    try:
        payload = json.loads(sys.argv[2])
    except json.JSONDecodeError as e:
        print(f"Ошибка парсинга JSON: {e}")
        sys.exit(1)

    result = send_json(method, payload)
    print(json.dumps(result, ensure_ascii=False, indent=2))
