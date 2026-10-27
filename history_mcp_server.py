#!/usr/bin/env python3
"""MCP-сервер (stdio) — коннектор для чтения ИСТОРИИ переписки других тем Telegram-бота
(не путать с памятью Claude Code — это отдельный журнал диалогов, который ведёт bot.py).

История живёт в TOPIC_HISTORY_DIR, один файл на тему: history_<topic_id>.txt, строки вида
"U|время|текст" (пользователь) / "C|время|текст" (Claude). Имена тем — из topics.json."""

import os
import json
from mcp.server.mcpserver import MCPServer

BRIDGE_DIR = os.path.dirname(os.path.abspath(__file__))
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


@mcp.tool()
def history_list() -> str:
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


@mcp.tool()
def history_read(topic: str, limit: int = 50) -> str:
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


if __name__ == "__main__":
    import asyncio
    asyncio.run(mcp.run_stdio_async())
