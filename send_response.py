#!/usr/bin/env python3
"""
Stop hook: если есть responded.flag — отправляет последний ответ Claude в Telegram.
"""

import sys
import json
import os
import requests

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from config import BOT_TOKEN, ALLOWED_CHAT_ID, BOT_API_BASE, RESPONDED_FLAG

if not os.path.exists(RESPONDED_FLAG):
    sys.exit(0)

try:
    data = json.load(sys.stdin)
    messages = data.get("messages", [])

    # Берём последнее сообщение от assistant
    last_response = ""
    for msg in reversed(messages):
        if msg.get("role") == "assistant":
            content = msg.get("content", "")
            if isinstance(content, list):
                parts = [c.get("text", "") for c in content if c.get("type") == "text"]
                last_response = "\n".join(parts).strip()
            elif isinstance(content, str):
                last_response = content.strip()
            break

    if last_response:
        # Проверяем лимит токенов
        if last_response.startswith("You've hit your limit"):
            last_response = "⚠️ Токены закончились. Попробуй позже."

        # Разбиваем на части если > 4096 символов (лимит Telegram)
        API = f"{BOT_API_BASE}/bot{BOT_TOKEN}"
        for i in range(0, len(last_response), 4096):
            chunk = last_response[i:i+4096]
            requests.post(f"{API}/sendMessage", json={
                "chat_id": ALLOWED_CHAT_ID,
                "text": chunk
            })

    os.remove(RESPONDED_FLAG)

except Exception as e:
    print(f"send_response error: {e}", file=sys.stderr)
    if os.path.exists(RESPONDED_FLAG):
        os.remove(RESPONDED_FLAG)

sys.exit(0)
