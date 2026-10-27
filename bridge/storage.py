"""Хранилище: настройки, режимы тем, топики/сессии, история, offset."""

import os
import json
from bridge import consts, core


def notify_claude_inbox(message: str):
    """Отправляет системное уведомление в Claude Code CLI через inbox."""
    try:
        with open(consts.INBOX_FILE, "w") as f:
            f.write(message)
        with open(consts.PENDING_FLAG, "w") as f:
            f.write("1")
    except Exception as e:
        print(f"[notify_claude_inbox] Ошибка: {e}")

def load_topics():
    """Загружает маппинг thread_id -> название темы."""
    if os.path.exists(consts.TOPICS_FILE):
        with open(consts.TOPICS_FILE) as f:
            return json.load(f)
    return {}

def save_topics(topics):
    with open(consts.TOPICS_FILE, "w") as f:
        json.dump(topics, f, ensure_ascii=False, indent=2)

def load_topic_sessions():
    try:
        with open(consts.TOPIC_SESSIONS_FILE) as f:
            return json.load(f)
    except:
        return {}

def save_topic_sessions(sessions):
    with open(consts.TOPIC_SESSIONS_FILE, "w") as f:
        json.dump(sessions, f, indent=2)

def reset_topic_session(topic_id):
    """Удаляет сохранённую сессию для темы — следующий вызов создаст новую."""
    sessions = load_topic_sessions()
    key = str(topic_id)
    if key in sessions:
        del sessions[key]
        save_topic_sessions(sessions)

DEFAULT_SETTINGS = {
    "style": "brief",  # "brief" или "detailed"
    # "sonnet"/"opus"/"haiku"/"fable" — алиасы claude CLI, сами резолвятся в последнюю
    # версию модели (не нужно вручную обновлять при выходе новых версий).
    "model": "sonnet",
}

def load_settings():
    try:
        return {**DEFAULT_SETTINGS, **json.load(open(consts.SETTINGS_FILE))}
    except:
        return dict(DEFAULT_SETTINGS)

def save_settings(s):
    with open(consts.SETTINGS_FILE, "w") as f:
        json.dump(s, f)

bot_modes = {}  # topic_id -> "claude"/"passthrough"/"shell" — режим свой для каждой темы/ЛС

def get_bot_mode(topic_id):
    return bot_modes.get(topic_id, "claude")

def set_bot_mode(topic_id, mode):
    bot_modes[topic_id] = mode


# ── Offset ──────────────────────────────────────────────────────────────────

def get_offset():
    try:
        return int(open(consts.OFFSET_FILE).read().strip())
    except:
        return 0

def save_offset(offset):
    with open(consts.OFFSET_FILE, "w") as f:
        f.write(str(offset))


# ── History ───────────────────────────────────────────────────────────────────

def get_history_file(topic_id=None):
    """Возвращает путь к файлу истории: основной или для конкретной темы группы."""
    if topic_id is None:
        return consts.HISTORY_FILE
    return os.path.join(consts.TOPIC_HISTORY_DIR, f"history_{topic_id}.txt")

def load_history(topic_id=None, limit=None):
    """Загружает историю. limit=None — полная история, limit=N — последние N записей."""
    try:
        lines = open(get_history_file(topic_id)).read().strip().split("\n")
        lines = [l for l in lines if l]
        if limit is not None:
            lines = lines[-limit:]
        return lines
    except:
        return []

def save_history(role, text, topic_id=None):
    """Дописывает сообщение в конец файла истории."""
    hfile = get_history_file(topic_id)
    ts = core.now_local().strftime("%Y-%m-%d %H:%M")
    safe = text.replace("\n", " ").strip()
    prefix = "U" if role == "Пользователь" else "C"
    with open(hfile, "a") as f:
        f.write(f"{prefix}|{ts}|{safe}\n")

def reset_history(topic_id=None):
    with open(get_history_file(topic_id), "w") as f:
        f.write("")
    if topic_id is not None:
        reset_topic_session(topic_id)
