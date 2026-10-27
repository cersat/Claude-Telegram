#!/usr/bin/env python3
"""MCP-сервер (stdio) — замена встроенного ScheduleWakeup, но говорящий с
ботом напрямую: джоба не резюмирует харнес-сессию, а заставляет bot.py
запустить полноценный run_claude_turn() (с живым стримом карточек) в ТОЙ ЖЕ
теме/чате, откуда был вызван этот тул.

Какая тема — понимает из переменных окружения BOT_CHAT_ID/BOT_THREAD_ID/
BOT_TOPIC_ID, которые bot.py прокидывает в subprocess при каждом запуске
claude (см. ask_claude() в bot.py). Сами джобы лежат в scheduled_jobs.json —
их читает и исполняет фоновый scheduler_loop() в bot.py."""

import os
import time
from datetime import datetime

from mcp.server.mcpserver import MCPServer
from scheduled_jobs_store import add_job, cancel_job, list_jobs, new_job_id


mcp = MCPServer("bot_scheduler", instructions="""Планирует пробуждения ЭТОГО Telegram-бота (bot.py) — не путать
со встроенным харнесс-инструментом ScheduleWakeup: тот просто резюмирует текущую CLI-сессию
и ничего не пишет в Telegram. Эти тулы вместо этого заставляют bot.py запустить полноценный
ход Claude (с живым стримом карточек) прямо в теме, откуда был вызван этот тул — используй их
для любых отложенных/периодических задач в контексте этого бота, а не ScheduleWakeup.""")


def _current_topic():
    chat_id = os.environ.get("BOT_CHAT_ID")
    thread_id = os.environ.get("BOT_THREAD_ID")
    topic_id = os.environ.get("BOT_TOPIC_ID") or None
    chat_id = int(chat_id) if chat_id not in (None, "", "None") else None
    thread_id = int(thread_id) if thread_id not in (None, "", "None") else None
    return chat_id, thread_id, topic_id


@mcp.tool()
def schedule_once(delay_seconds: int, prompt: str) -> str:
    """⏰ Планирует одноразовое пробуждение через delay_seconds секунд — bot.py
    запустит полноценный ход Claude (с этим prompt) прямо в текущей теме,
    как если бы пришло обычное сообщение. Замена встроенного ScheduleWakeup —
    но не резюмирует эту сессию, а создаёт новый ход в чате пользователя."""
    chat_id, thread_id, topic_id = _current_topic()
    if chat_id is None:
        return "⏰ Не удалось определить текущую тему (нет BOT_CHAT_ID) — джоба не создана."
    job = {
        "id": new_job_id(),
        "chat_id": chat_id, "thread_id": thread_id, "topic_id": topic_id,
        "prompt": prompt, "kind": "once",
        "fire_at": time.time() + max(1, int(delay_seconds)),
        "created_at": time.time(), "created_by": "claude",
    }
    add_job(job)
    fire_dt = datetime.fromtimestamp(job["fire_at"]).strftime("%H:%M:%S")
    return f"⏰ Джоба {job['id']} создана — сработает в {fire_dt} в этой теме."


@mcp.tool()
def schedule_daily(time_hhmm: str, prompt: str) -> str:
    """⏰ Планирует ежедневное пробуждение в time_hhmm (формат "HH:MM", часовой
    пояс — системный) — каждый день в это время bot.py запускает Claude с этим
    prompt в текущей теме. Используй для регулярных автономных задач."""
    chat_id, thread_id, topic_id = _current_topic()
    if chat_id is None:
        return "⏰ Не удалось определить текущую тему (нет BOT_CHAT_ID) — джоба не создана."
    try:
        hh, mm = time_hhmm.strip().split(":")
        hh, mm = int(hh), int(mm)
        assert 0 <= hh <= 23 and 0 <= mm <= 59
    except Exception:
        return f"⏰ Неверный формат времени {time_hhmm!r}, ожидается 'HH:MM'."
    job = {
        "id": new_job_id(),
        "chat_id": chat_id, "thread_id": thread_id, "topic_id": topic_id,
        "prompt": prompt, "kind": "daily",
        "time": f"{hh:02d}:{mm:02d}", "last_run_date": None,
        "created_at": time.time(), "created_by": "claude",
    }
    add_job(job)
    return f"⏰ Ежедневная джоба {job['id']} создана — каждый день в {job['time']} в этой теме."


@mcp.tool()
def list_scheduled() -> str:
    """⏰ Список запланированных джоб (одноразовых и ежедневных) в текущей теме."""
    chat_id, thread_id, _ = _current_topic()
    if chat_id is None:
        return "⏰ Не удалось определить текущую тему."
    jobs = list_jobs(chat_id=chat_id, thread_id=thread_id)
    if not jobs:
        return "⏰ В этой теме нет запланированных джоб."
    lines = []
    for j in jobs:
        if j["kind"] == "once":
            when = datetime.fromtimestamp(j["fire_at"]).strftime("%Y-%m-%d %H:%M:%S")
            lines.append(f"- {j['id']} [once @ {when}]: {j['prompt'][:80]}")
        else:
            status = f" | последний запуск: {j['last_status']}" if j.get("last_status") else ""
            lines.append(f"- {j['id']} [daily @ {j['time']}{status}]: {j['prompt'][:80]}")
    return "\n".join(lines)


@mcp.tool()
def cancel_scheduled(job_id: str) -> str:
    """⏰ Отменяет джобу по id (только из этой же темы)."""
    chat_id, thread_id, _ = _current_topic()
    ok = cancel_job(job_id, chat_id=chat_id, thread_id=thread_id)
    return f"⏰ Джоба {job_id} отменена." if ok else f"⏰ Джоба {job_id} не найдена в этой теме."


if __name__ == "__main__":
    import asyncio
    asyncio.run(mcp.run_stdio_async())
