from secrets_local import TELEGRAM_BOT_TOKEN as BOT_TOKEN, GROUP_CHAT_ID as ALLOWED_CHAT_ID
import os

BRIDGE_DIR = os.path.dirname(os.path.abspath(__file__))
INBOX_FILE = f"{BRIDGE_DIR}/inbox.txt"
PENDING_FLAG = f"{BRIDGE_DIR}/pending.flag"
RESPONDED_FLAG = f"{BRIDGE_DIR}/responded.flag"

try:
    from secrets_local import BOT_API_BASE
except ImportError:  # старый secrets_local.py без этой строки — облачный Bot API
    BOT_API_BASE = "https://api.telegram.org"
