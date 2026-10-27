#!/usr/bin/env python3
"""Отправляет сообщение в Telegram владельцу бота. Использование: python3 send.py "текст" """

import sys
import os
import requests
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from config import BOT_TOKEN, ALLOWED_CHAT_ID, BOT_API_BASE

def send(text):
    API = f"{BOT_API_BASE}/bot{BOT_TOKEN}"
    r = requests.post(f"{API}/sendMessage", json={
        "chat_id": ALLOWED_CHAT_ID,
        "text": text
    })
    return r.json()

if __name__ == "__main__":
    if len(sys.argv) < 2:
        print("Usage: python3 send.py 'message'")
        sys.exit(1)
    result = send(" ".join(sys.argv[1:]))
    print(result)
