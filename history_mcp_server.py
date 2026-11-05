#!/usr/bin/env python3
"""MCP-сервер (stdio) — коннектор для чтения ИСТОРИИ переписки других тем Telegram-бота
(не путать с памятью Claude Code — это отдельный журнал диалогов, который ведёт bot.py).

История читается у самого Telegram (bridge/tghistory.py): по ID сообщений, которые бот индексирует по темам;
строки вида "U|время|текст" (пользователь) / "C|время|текст" (Claude). Имена тем — из topics.json.
Если api_id/api_hash или Telethon не настроены (consts.HISTORY_FROM_TELEGRAM = False), работает прежняя история
на диске: TOPIC_HISTORY_DIR, один файл на тему history_<topic_id>.txt. Личные сообщения (ЛС) читаются так же, как темы:
по ID из «ящика» бота."""

import os
import sys
import json
from mcp.server.mcpserver import MCPServer

BRIDGE_DIR = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, BRIDGE_DIR)
from bridge import consts, tghistory
TOPIC_HISTORY_DIR = os.path.join(BRIDGE_DIR, "topic_histories")
TOPICS_FILE = os.path.join(BRIDGE_DIR, "topics.json")

mcp = MCPServer("history", instructions="""Сырая история переписки тем Telegram-бота (кто что написал, по темам) — не путать с памятью.
history_list — какие темы есть (названия), history_read — прочитать историю темы (по названию или номеру).
Нужна, когда надо узнать, что обсуждалось в другой теме.""")


def _load_topics():
    try:
        with open(TOPICS_FILE) as f:
            return json.load(f)
    except Exception:
        return {}


def _resolve_topic_id(topic):
    """Принимает имя темы ('Бот'), номер темы ('1978'), topic_id ('group_1978') или
    'general'/'общий' — возвращает topic_id в формате имени файла, либо None если не нашли."""
    topic = topic.strip()
    if topic.lower() in ("general", "общий", "главная", "группа"):
        return "group_general"
    if topic.lower() in ("dm", "лс", "личка", "личные сообщения"):
        return "dm"
    if topic.isdigit() and os.path.exists(os.path.join(TOPIC_HISTORY_DIR, f"history_group_{topic}.txt")):
        return f"group_{topic}"
    if os.path.exists(os.path.join(TOPIC_HISTORY_DIR, f"history_{topic}.txt")):
        return topic
    topics = _load_topics()
    for thread_id, name in topics.items():
        if name.lower() == topic.lower():
            return f"group_{thread_id}"
    if topic.lower().startswith("тема №") and topic[len("тема №"):].strip().isdigit():
        return f"group_{topic[len('тема №'):].strip()}"
    if topic.isdigit():
        return f"group_{topic}"
    return None


def _history_list_disk() -> str:
    """📃 Список тем с историей переписки: название темы и число записей. Для чтения передай название
    (или номер, если у темы название неизвестно) в history_read."""
    topics = _load_topics()
    names = {tid: name for tid, name in topics.items()}
    try:
        files = sorted(os.listdir(TOPIC_HISTORY_DIR))
    except FileNotFoundError:
        return "Директория истории не найдена."
    entries = {}  # ключ темы -> [название, число записей]; старые файлы без префикса group_ сливаются
    for fname in files:
        if not fname.startswith("history_") or not fname.endswith(".txt"):
            continue
        tid = fname[len("history_"):-len(".txt")]
        key = tid[len("group_"):] if tid.startswith("group_") else tid
        if key == "dm":
            name = "Личные сообщения"
        elif key == "general":
            name = "General"
        elif key in names:
            name = names[key]
        elif key.isdigit():
            name = f"Тема №{key}"
        else:
            name = tid
        try:
            with open(os.path.join(TOPIC_HISTORY_DIR, fname)) as f:
                n_lines = sum(1 for _ in f)
        except Exception:
            n_lines = 0
        if key in entries:
            entries[key][1] += n_lines
        else:
            entries[key] = [name, n_lines]
    lines = [f"{name} — {n} записей" for name, n in entries.values()]
    return "\n".join(lines) if lines else "Историй пока нет."


def _history_read_disk(topic: str, limit: int = 50) -> str:
    """📃 Читает историю переписки указанной темы (имя темы, номер темы, topic_id или 'general').
    limit — сколько последних записей вернуть (по умолчанию 50)."""
    topic_id = _resolve_topic_id(topic)
    if topic_id is None:
        return f"Тема {topic!r} не найдена. Используй history_list чтобы посмотреть доступные."
    path = os.path.join(TOPIC_HISTORY_DIR, f"history_{topic_id}.txt")
    try:
        with open(path) as f:
            lines = [l for l in f.read().strip().split("\n") if l]
    except FileNotFoundError:
        return f"История для темы {topic_id!r} пуста или не найдена."
    if limit and limit > 0:
        lines = lines[-limit:]
    return "\n".join(lines)



def _resolve_telegram_topic(topic):
    """Имя/номер/'general'/'лс' → topic_id бота ('group_1978', 'group_general', 'dm') или None."""
    t = topic.strip()
    low = t.lower()
    if low in ("general", "общий", "главная", "группа"):
        return "group_general"
    if low in ("dm", "лс", "личка", "личные сообщения"):
        return "dm"
    if t.startswith("group_"):
        return t
    if t.isdigit():
        return f"group_{t}"
    for thread_id, name in _load_topics().items():
        if name.lower() == low:
            return f"group_{thread_id}"
    if low.startswith("тема №") and low[len("тема №"):].strip().isdigit():
        return f"group_{low[len('тема №'):].strip()}"
    return None


@mcp.tool()
def history_list() -> str:
    """📃 Список тем, историю которых можно прочитать: название темы и сколько её сообщений уже проиндексировано.
    Для чтения передай название (или номер, если у темы название неизвестно) в history_read."""
    if not consts.HISTORY_FROM_TELEGRAM:
        return _history_list_disk()
    lines = []
    for thread_id, name in _load_topics().items():
        n = len(tghistory.index(f"group_{thread_id}"))
        lines.append(f"{name} — тема №{thread_id}, сообщений в индексе: {n}")
    lines.append(f"General — сообщений в индексе: {len(tghistory.index('group_general'))}")
    lines.append(f"Личные сообщения (ЛС) — сообщений в индексе: {len(tghistory.index('dm'))}")
    return "\n".join(lines)


@mcp.tool()
def history_read(topic: str, limit: int = 50) -> str:
    """📃 Читает историю переписки указанной темы (имя темы, номер темы, topic_id или 'general') прямо из Telegram.
    limit — сколько последних записей вернуть (по умолчанию 50). Карточки инструментов бота в историю не входят."""
    if not consts.HISTORY_FROM_TELEGRAM:
        return _history_read_disk(topic, limit)
    topic_id = _resolve_telegram_topic(topic)
    if topic_id is None:
        return f"Тема {topic!r} не найдена. Используй history_list чтобы посмотреть доступные."
    lines = tghistory.get_lines(topic_id, limit if limit and limit > 0 else 50, name="mcp")
    if not lines:
        return (f"Записей в теме {topic_id!r} нет: она пуста, ещё не проиндексирована (индекс строится в фоне "
                f"после запуска бота) или Telegram сейчас недоступен.")
    return "\n".join(lines)


if __name__ == "__main__":
    import asyncio
    asyncio.run(mcp.run_stdio_async())
