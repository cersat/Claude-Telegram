#!/usr/bin/env python3
"""
Вызывает claude -p с системным уведомлением и отправляет ответ в Telegram чат.
Запускается из bot.py в фоне при старте и ошибках.
"""

import sys
import subprocess
import requests
import os

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from config import BOT_TOKEN, ALLOWED_CHAT_ID, BOT_API_BASE

API = f"{BOT_API_BASE}/bot{BOT_TOKEN}"
CLAUDE_BIN = os.path.expanduser("~/.local/bin/claude")


def send_to_chat(text: str):
    for i in range(0, len(text), 4096):
        chunk = text[i:i+4096]
        requests.post(f"{API}/sendMessage", json={
            "chat_id": ALLOWED_CHAT_ID,
            "text": chunk
        })


def main():
    if len(sys.argv) < 2:
        print("Usage: auto_respond.py <message>", file=sys.stderr)
        sys.exit(1)

    message = sys.argv[1]

    try:
        result = subprocess.run(
            [CLAUDE_BIN, "-p", "--bare", message],
            capture_output=True,
            text=True,
            timeout=120,
            env={**os.environ, "HOME": os.path.expanduser("~")}
        )
        response = result.stdout.strip()
        if response:
            send_to_chat(response)
        elif result.stderr:
            send_to_chat(f"⚠️ Ошибка claude: {result.stderr[:500]}")
    except subprocess.TimeoutExpired:
        send_to_chat("⚠️ Claude не ответил за 120 секунд")
    except Exception as e:
        send_to_chat(f"⚠️ auto_respond ошибка: {e}")


if __name__ == "__main__":
    main()
