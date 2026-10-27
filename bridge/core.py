"""Базовое: токен и URL Telegram API. Время — по системному часовому поясу."""

from datetime import datetime
from secrets_local import TELEGRAM_BOT_TOKEN
from bridge import consts


def now_local():
    return datetime.now()

BOT_TOKEN = TELEGRAM_BOT_TOKEN
API = f"{consts.BOT_API_BASE}/bot{BOT_TOKEN}"
