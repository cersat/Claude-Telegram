"""Лимит подписки Claude: распознавание ответа «You've hit your … limit · resets …», разбор времени сброса и
автоповтор запроса после сброса.

Когда лимит исчерпан, claude -p отдаёт одной строкой «You've hit your session limit · resets 8pm (Asia/Tbilisi)»
(для недельного лимита — «weekly», со словами «resets Sep 19, 2am (…)»). Раньше бот узнавал только фразу
«You've hit your limit», поэтому английский оригинал уходил в чат как обычный ответ, а запрос терялся."""

import re
import threading
import time
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

from scheduled_jobs_store import add_job, jobs_lock, new_job_id

MAX_RETRIES = 3          # сколько раз повторять запрос, упёршийся в лимит
RETRY_DELAY = 60         # через сколько секунд после сброса лимита повторять запрос
RETRY_MARK = "[Автоповтор"
_LIMIT_RE = re.compile(r"^\s*You['’]ve hit your (?:\w+ )?limit", re.IGNORECASE)
_RESET_RE = re.compile(r"resets\s+(?:([A-Za-z]{3})\s+(\d{1,2}),\s*)?(\d{1,2})(?::(\d{2}))?\s*([ap]m)\s*\(([^)]+)\)",
                       re.IGNORECASE)
_notice_lock = threading.Lock()
_last_notice_ts = 0


def is_limit_message(text):
    return bool(text) and len(text) < 300 and bool(_LIMIT_RE.match(text))


def parse_reset(text, now=None):
    """Момент сброса лимита (unix-время) из текста ответа или None, если разобрать не удалось."""
    m = _RESET_RE.search(text or "")
    if not m:
        return None
    mon, day, hour, minute, ampm, tzname = m.groups()
    try:
        tz = ZoneInfo(tzname.strip())
        now_dt = datetime.fromtimestamp(now, tz) if now else datetime.now(tz)
        hour = int(hour) % 12 + (12 if ampm.lower() == "pm" else 0)
        minute = int(minute or 0)
        if mon:
            month = datetime.strptime(mon.title(), "%b").month
            dt = datetime(now_dt.year, month, int(day), hour, minute, tzinfo=tz)
            if dt <= now_dt:
                dt = dt.replace(year=now_dt.year + 1)
        else:
            dt = now_dt.replace(hour=hour, minute=minute, second=0, microsecond=0)
            if dt <= now_dt:
                dt += timedelta(days=1)
        return dt.timestamp()
    except Exception:
        return None


def _retry_number(prompt):
    m = re.match(r"\[Автоповтор (\d+)/", prompt or "")
    return int(m.group(1)) if m else 0


def schedule_retry(prompt, reset_ts, chat_id, thread_id, topic_id):
    """Ставит одноразовую джобу, которая повторит запрос после сброса лимита. Возвращает (поставлено, номер_попытки)."""
    n = _retry_number(prompt) + 1
    if n > MAX_RETRIES:
        return False, n
    original = re.sub(r"^\[Автоповтор \d+/\d+[^\]]*\]\n", "", prompt or "")
    text = (f"{RETRY_MARK} {n}/{MAX_RETRIES}: запрос не выполнился из-за лимита Claude, лимит уже сброшен — "
            f"выполни его сейчас]\n{original}")
    with jobs_lock() as jobs:
        for j in jobs:  # тот же запрос в той же теме уже ждёт повтора — второй раз не ставим
            if (j.get("chat_id") == chat_id and j.get("thread_id") == thread_id
                    and j.get("created_by") == "limit_retry" and original in j.get("prompt", "")):
                return True, n
    fire_at = (reset_ts + RETRY_DELAY) if reset_ts else time.time() + 30 * 60
    add_job({"id": new_job_id(), "chat_id": chat_id, "thread_id": thread_id, "topic_id": topic_id,
             "prompt": text, "kind": "once", "fire_at": fire_at, "created_at": time.time(),
             "created_by": "limit_retry"})
    return True, n


def notice_text(reset_ts, queued, attempt):
    """Сообщение пользователю вместо английского оригинала: когда лимит сбросится и когда запрос повторится."""
    if not queued:
        return f"⚠️ Лимит Claude исчерпан. Автоповторы закончились (уже {MAX_RETRIES}), повтори запрос вручную."
    if reset_ts:
        fmt = lambda ts: datetime.fromtimestamp(ts).strftime("%H:%M %d.%m")
        return (f"⚠️ Лимит Claude исчерпан, сбросится в {fmt(reset_ts)}. "
                f"Повторю этот запрос в {fmt(reset_ts + RETRY_DELAY)} (через минуту после сброса).")
    return "⚠️ Лимит Claude исчерпан, время сброса неизвестно. Повторю этот запрос примерно через 30 минут."


def should_notify():
    """Не чаще раза в 5 минут: при лимите подряд могут упасть несколько запросов."""
    global _last_notice_ts
    with _notice_lock:
        now = time.time()
        if now - _last_notice_ts < 300:
            return False
        _last_notice_ts = now
        return True
