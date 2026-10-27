#!/usr/bin/env python3
"""
UserPromptSubmit hook: если есть pending.flag — вставляет Telegram-сообщение в промпт.
"""

import sys
import json
import os

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from config import INBOX_FILE, PENDING_FLAG, RESPONDED_FLAG

data = json.load(sys.stdin)
prompt = data.get("prompt", "")

if os.path.exists(PENDING_FLAG) and os.path.exists(INBOX_FILE):
    try:
        tg_msg = open(INBOX_FILE).read().strip()
        if tg_msg:
            # Если у пользователя пустой промпт — заменяем на telegram-сообщение
            if not prompt.strip():
                new_prompt = f"[Telegram от владельца]: {tg_msg}"
            else:
                new_prompt = prompt  # промпт уже есть, не трогаем

            os.remove(PENDING_FLAG)
            with open(RESPONDED_FLAG, "w") as f:
                f.write("1")

            data["prompt"] = new_prompt
            print(json.dumps(data))
            sys.exit(0)
    except Exception as e:
        pass

print(json.dumps(data))
