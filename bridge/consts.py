"""Пути и константы бота (все пути — относительно папки бота / домашней директории)."""

import os
import getpass


BRIDGE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))  # папка бота (рядом с bot.py)
HOME = os.path.expanduser("~")
USER = getpass.getuser()
CLAUDE_BIN = os.path.join(HOME, ".local", "bin", "claude")
GEMMA_DIR = os.path.join(HOME, "gemma_sandbox")
GEMMA_ARENA_LOG = os.path.join(GEMMA_DIR, "gemma_arena.log")

OFFSET_FILE = os.path.join(BRIDGE_DIR, "offset.txt")
HISTORY_FILE = os.path.join(BRIDGE_DIR, "history.txt")
SETTINGS_FILE = os.path.join(BRIDGE_DIR, "settings.json")
REMINDERS_FILE = os.path.join(BRIDGE_DIR, "reminders.json")
CHAT_IDS_FILE = os.path.join(BRIDGE_DIR, "chat_message_ids.json")
STARTUP_MSG_FILE = os.path.join(BRIDGE_DIR, "startup_msg_id.txt")
SHUTDOWN_STATUS_FILE = os.path.join(BRIDGE_DIR, "shutdown_status.json")
RESTART_LOG_FILE = os.path.join(BRIDGE_DIR, "restart_log.json")
BOT_LOG_FILE = os.path.join(BRIDGE_DIR, "bot.log")
RESTART_PROMPT_FILE = os.path.join(BRIDGE_DIR, "restart_prompt.txt")

GEMMA_ARENA_THREAD_ID = 3525
GEMMA_ARENA_SCRIPT = os.path.join(GEMMA_DIR, "gemma_arena.py")
GEMMA_SANDBOX_ROOT = os.path.join(GEMMA_DIR, "agents")
GEMMA_WORKSPACE_DIR = os.path.join(GEMMA_DIR, "shared")
GEMMA_SANDBOX_IMAGE = "gemma-sandbox:latest"
MEMORY_DIR = os.path.expanduser("~/.local/share/claude_memory")  # гарантированно существующий путь (XDG data dir), не завязан на slug-путь проекта Claude Code
INBOX_DIR = os.path.join(HOME, "inbox")
TOPIC_HISTORY_DIR = os.path.join(BRIDGE_DIR, "topic_histories")
INBOX_FILE = os.path.join(BRIDGE_DIR, "inbox.txt")
PENDING_FLAG = os.path.join(BRIDGE_DIR, "pending.flag")
TOPICS_FILE = os.path.join(BRIDGE_DIR, "topics.json")
TOPIC_SESSIONS_FILE = os.path.join(BRIDGE_DIR, "topic_sessions.json")
LAST_MSG_FILE = os.path.join(BRIDGE_DIR, "last_message_ids.json")
MAX_HISTORY = 20

os.makedirs(INBOX_DIR, exist_ok=True)
PROMPT_VERSIONS_FILE = os.path.join(BRIDGE_DIR, "topic_prompt_versions.json")


WHISPER_BIN = os.path.join(HOME, "whisper.cpp", "build", "bin", "whisper-cli")
WHISPER_MODEL = os.path.join(HOME, "whisper.cpp", "models", "ggml-small.bin")

HOME_DIR = os.path.expanduser("~")  # домашняя папка: тут запускается интерактивный claude для /login

# Локальный Bot API сервер (docker-контейнер telegram-bot-api, см. ~/telegram_bot_api): лимит скачивания
# файлов 2 ГБ вместо 20 МБ у облачного api.telegram.org. Вернуться на облако: BOT_API_BASE = "https://api.telegram.org"
# (после logOut на облаке повторный вход в облако возможен только через 10 минут). Значение — в secrets_local.py.
try:
    from secrets_local import BOT_API_BASE  # задаётся установщиком (install.py) в secrets_local.py
except ImportError:
    BOT_API_BASE = "https://api.telegram.org"
BOT_API_LOCAL = BOT_API_BASE != "https://api.telegram.org"
BOT_API_CONTAINER_DIR = "/var/lib/telegram-bot-api"                      # где сервер хранит файлы внутри контейнера
BOT_API_DATA_DIR = os.path.expanduser("~/telegram_bot_api/data")         # то же место на хосте

# Разрешения на вызов инструментов (см. permission_mcp_server.py): запросы и решения обмениваются файлами
PERM_DIR = os.path.join(BRIDGE_DIR, "perm_requests")
PERM_RULES_FILE = os.path.join(BRIDGE_DIR, "permission_rules.json")  # «всегда разрешать» (не для Bash)
# Что Claude делает без вопроса. Всё остальное (Bash, Write, Edit, WebFetch, коннекторы claude.ai…) идёт на
# подтверждение владельцу кнопками в чате. Bash(true) — разделитель сообщений, коннекторы бота — свои.
CLAUDE_ALLOWED_TOOLS = ["Read", "Glob", "Grep", "Bash(true)", "mcp__memory", "mcp__history", "mcp__moltbook",
                        "mcp__bot_scheduler", "mcp__telegram", "mcp__perm"]

# ── История из Telegram вместо файлов (bridge/tghistory.py) ──
HISTORY_INDEX_FILE = os.path.join(BRIDGE_DIR, "history_index.json")      # только id сообщений по темам (тексты — в Telegram)
HISTORY_CUTOFF_FILE = os.path.join(BRIDGE_DIR, "history_cutoff.json")   # «Очистить историю» = запомнить id, раньше которого не читаем


def telegram_api_credentials():
    """(api_id, api_hash) с my.telegram.org: из secrets_local.py (TELEGRAM_API_ID/TELEGRAM_API_HASH) или из
    server.env локального Bot API. (None, None) — не найдены."""
    try:
        import secrets_local
        if getattr(secrets_local, "TELEGRAM_API_ID", None) and getattr(secrets_local, "TELEGRAM_API_HASH", None):
            return int(secrets_local.TELEGRAM_API_ID), secrets_local.TELEGRAM_API_HASH
    except ImportError:
        pass
    try:
        env = dict(l.strip().split("=", 1) for l in open(os.path.expanduser("~/telegram_bot_api/server.env")) if "=" in l)
        return int(env["TELEGRAM_API_ID"]), env["TELEGRAM_API_HASH"]
    except Exception:
        return None, None


def _telethon_installed():
    try:
        import telethon  # noqa: F401
        return True
    except ImportError:
        return False


# Историю берём из Telegram, если есть api_id/api_hash и Telethon; иначе работает прежняя история на диске
HISTORY_FROM_TELEGRAM = telegram_api_credentials()[0] is not None and _telethon_installed()
if not HISTORY_FROM_TELEGRAM:
    os.makedirs(TOPIC_HISTORY_DIR, exist_ok=True)

# ── MCP-серверы бота (bridge/mcpguard.py): обязательны в КАЖДОМ запуске claude ──
EXPECTED_MCP = {
    "memory": "memory_mcp_server.py",
    "history": "history_mcp_server.py",
    "moltbook": "moltbook_mcp_server.py",
    "bot_scheduler": "bot_scheduler_mcp_server.py",
    "telegram": "telegram_mcp_server.py",
    "perm": "permission_mcp_server.py",
}
MCP_CONFIG_FILE = os.path.join(BRIDGE_DIR, "mcp_servers.json")   # генерируется ботом из EXPECTED_MCP
