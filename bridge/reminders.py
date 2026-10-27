"""Напоминания, планировщик джоб, «свободное время»."""

import time
import threading
import os
import json
import re
from datetime import datetime, timedelta
from secrets_local import GROUP_CHAT_ID
from scheduled_jobs_store import jobs_lock as _jobs_lock
from bridge import claude_run, consts, core, tgapi


# Активные напоминания: {id: {"text": ..., "fire_at": timestamp, "repeat_secs": int|None, "timer": Timer}}
active_reminders = {}
_reminder_lock = threading.Lock()
_reminder_id_counter = 0


def parse_reminder(text):
    """Парсит строку вида '30м Тесла', '2ч 15м позвонить', или '17:00 текст'.
    Возвращает (seconds, reminder_text) или None если не распознано."""
    text = text.strip()

    # Формат HH:MM текст
    m_time = re.match(r'^(\d{1,2}):(\d{2})\s+(.+)$', text)
    if m_time:
        hour, minute = int(m_time.group(1)), int(m_time.group(2))
        reminder_text = m_time.group(3).strip()
        if 0 <= hour <= 23 and 0 <= minute <= 59 and reminder_text:
            now = core.now_local()
            target = now.replace(hour=hour, minute=minute, second=0, microsecond=0)
            if target <= now:
                target += timedelta(days=1)
            return int((target - now).total_seconds()), reminder_text

    # Относительный формат: 30м, 2ч 15м, 1д и т.д.
    pattern = r'^(?:(\d+)\s*д\s*)?(?:(\d+)\s*ч\s*)?(?:(\d+)\s*м\s*)?(?:(\d+)\s*с\s*)?(.+)$'
    m = re.match(pattern, text, re.IGNORECASE)
    if not m:
        return None
    days = int(m.group(1) or 0)
    hours = int(m.group(2) or 0)
    mins = int(m.group(3) or 0)
    secs = int(m.group(4) or 0)
    reminder_text = m.group(5).strip()
    total = days * 86400 + hours * 3600 + mins * 60 + secs
    if total <= 0 or not reminder_text:
        return None
    return total, reminder_text


def format_duration(seconds):
    """Форматирует секунды в читаемую строку."""
    parts = []
    if seconds >= 86400:
        parts.append(f"{seconds // 86400}д")
        seconds %= 86400
    if seconds >= 3600:
        parts.append(f"{seconds // 3600}ч")
        seconds %= 3600
    if seconds >= 60:
        parts.append(f"{seconds // 60}м")
        seconds %= 60
    if seconds:
        parts.append(f"{seconds}с")
    return " ".join(parts)


def save_reminders_file():
    """Сохраняет персистентные данные напоминаний (без объектов Timer)."""
    with _reminder_lock:
        data = {}
        for rid, r in active_reminders.items():
            data[str(rid)] = {
                "text": r["text"],
                "fire_at": r["fire_at"],
                "repeat_secs": r["repeat_secs"],
                "chat_id": r.get("chat_id"),
                "topic_id": r.get("topic_id"),
            }
        with open(consts.REMINDERS_FILE, "w") as f:
            json.dump(data, f)


def _next_reminder_id():
    global _reminder_id_counter
    _reminder_id_counter += 1
    return _reminder_id_counter


def schedule_reminder(seconds, reminder_text, repeat_secs=None, rid=None, chat_id=None, topic_id=None):
    """Добавляет напоминание в словарь. Фоновый цикл reminder_loop его отправит."""
    if rid is None:
        rid = _next_reminder_id()
    fire_at = time.time() + seconds
    with _reminder_lock:
        active_reminders[rid] = {
            "text": reminder_text,
            "fire_at": fire_at,
            "repeat_secs": repeat_secs,
            "chat_id": chat_id,
            "topic_id": topic_id,
        }
    save_reminders_file()
    return rid


def cancel_reminder(rid):
    with _reminder_lock:
        active_reminders.pop(rid, None)
    save_reminders_file()


def reminder_loop():
    """Фоновый цикл: каждые 15 секунд проверяет и отправляет напоминания."""
    while True:
        time.sleep(15)
        now = time.time()
        to_fire = []
        with _reminder_lock:
            for rid, r in list(active_reminders.items()):
                if r["fire_at"] <= now:
                    to_fire.append((rid, r["text"], r["repeat_secs"], r.get("chat_id"), r.get("topic_id")))

        for rid, text, repeat_secs, chat_id, topic_id in to_fire:
            fire_time = datetime.fromtimestamp(
                active_reminders[rid]["fire_at"] if rid in active_reminders else now
            ).strftime("%H:%M")
            tgapi.send_message(f"⏰ *Напоминание ({fire_time}):* {text}", None, chat_id=chat_id, thread_id=topic_id)
            with _reminder_lock:
                if rid not in active_reminders:
                    continue
                if repeat_secs:
                    active_reminders[rid]["fire_at"] = time.time() + repeat_secs
                else:
                    del active_reminders[rid]
            save_reminders_file()


AUTH_RETRY_MAX = 3            # сколько раз повторять джобу, сорвавшуюся из-за ошибки входа
AUTH_RETRY_DELAY = 15 * 60    # пауза между повторами, секунд — время успеть перелогиниться


def _record_job_result(job, auth_failed):
    """Пишет в джобу итог запуска. Ошибка входа — это «пропущен», а не «выполнен»: слот не
    считается отработанным и повторяется (до AUTH_RETRY_MAX раз с паузой), потом сдаёмся до завтра."""
    stamp = core.now_local().strftime("%Y-%m-%d %H:%M")
    with _jobs_lock() as jobs:
        cur = next((jj for jj in jobs if jj.get("id") == job["id"]), None)
        if cur is None and auth_failed and job.get("kind") != "daily":
            cur = dict(job)          # одноразовая джоба уже вынута из очереди — вернём её для повтора
            jobs.append(cur)
        if cur is None:
            return
        if not auth_failed:
            cur["last_status"] = f"выполнен {stamp}"
            cur["auth_retries"] = 0
            cur.pop("retry_after", None)
            return
        n = cur.get("auth_retries", 0) + 1
        if n < AUTH_RETRY_MAX:
            cur["auth_retries"] = n
            cur["last_status"] = f"пропущен {stamp}: ошибка входа, попытка {n} из {AUTH_RETRY_MAX}"
            cur["retry_after"] = time.time() + AUTH_RETRY_DELAY
            if cur.get("kind") == "daily":
                cur["last_run_date"] = None          # слот не отработан — scheduler_loop повторит
            else:
                cur["fire_at"] = cur["retry_after"]
        else:
            cur["auth_retries"] = 0
            cur.pop("retry_after", None)
            cur["last_status"] = f"пропущен {stamp}: ошибка входа, повторы исчерпаны"
            if cur.get("kind") != "daily":
                jobs.remove(cur)                      # одноразовую после исчерпания повторов снимаем
            # у daily last_run_date остался сегодняшним — до завтра повторов не будет


def scheduler_loop():
    """Фоновый цикл: каждые 20 секунд проверяет scheduled_jobs.json (одноразовые
    и ежедневные пробуждения, созданные через bot_scheduler_mcp_server.py) и для
    просроченных запускает полноценный run_claude_turn() в теме джобы — то же
    самое, что обычное входящее сообщение, только без пользователя.

    Джоба помечается выполненной ОПТИМИСТИЧНО (до реального запуска), поэтому
    при сбое (например Pi на ночь теряет сеть/DNS — уже бывало) её нужно
    откатить, иначе она молча теряется на весь день/навсегда вместо повтора
    на следующем тике."""
    while True:
        time.sleep(20)
        now = time.time()
        today = core.now_local().strftime("%Y-%m-%d")
        hhmm = core.now_local().strftime("%H:%M")
        to_fire = []
        with _jobs_lock() as jobs:
            remaining = []
            for j in jobs:
                if j.get("kind") == "once":
                    if j.get("fire_at", 0) <= now:
                        to_fire.append(j)
                        continue
                    remaining.append(j)
                elif j.get("kind") == "daily":
                    # ">=", а не "==" — чтобы пропущенная из-за сбоя минута не
                    # хоронила джобу на весь день, а сработала при первой
                    # возможности после назначенного времени.
                    if (j.get("time") <= hhmm and j.get("last_run_date") != today
                            and now >= j.get("retry_after", 0)):
                        j["last_run_date"] = today
                        to_fire.append(j)
                    remaining.append(j)
                else:
                    remaining.append(j)
            jobs[:] = remaining

        for j in to_fire:
            def _run(job=j):
                try:
                    res = claude_run.run_claude_turn(
                        job["prompt"],
                        chat_id=job.get("chat_id"), thread_id=job.get("thread_id"),
                        topic_id=job.get("topic_id"), history_topic=job.get("topic_id"),
                        save_user_history=f"⏰ [Запланированное пробуждение] {job['prompt']}",
                    )
                    _record_job_result(job, res == claude_run.AUTH_FAILED)
                except Exception as e:
                    print(f"[scheduler_loop] джоба {job['id']} упала, откатываю для повтора: {e}")
                    with _jobs_lock() as jobs2:
                        if job.get("kind") == "daily":
                            for jj in jobs2:
                                if jj.get("id") == job["id"]:
                                    jj["last_run_date"] = None
                                    break
                        else:
                            if not any(jj.get("id") == job["id"] for jj in jobs2):
                                jobs2.append(job)
            threading.Thread(target=_run, daemon=True).start()


FREE_TIME_PROMPT = (
    "Свободное время — можешь делать что хочешь в рамках своих возможностей: "
    "почитай новости, напиши пост или комментарий на Moltbook, придумай и "
    "сделай смешной скрипт, приберись в домашней директории, попробуй что-то новое. "
    "Не обязательно писать подробный отчёт в чат — коротким сообщением о том, "
    "что сделал, достаточно."
)
FREE_TIME_SLOTS = [
    {"id": "free_time_3am", "time": "03:00"},
    {"id": "free_time_15h", "time": "15:00"},
]


def seed_free_time_job():
    """Создаёт ежедневные системные джобы 'свободное время' (см. FREE_TIME_SLOTS)
    в General (topic_id=group_general) — идемпотентно на каждый рестарт бота:
    уже существующим джобам (по id) только освежает время/промпт, не трогая
    last_run_date, чтобы не потерять отметку о сегодняшнем запуске."""
    with _jobs_lock() as jobs:
        by_id = {j.get("id"): j for j in jobs}
        for slot in FREE_TIME_SLOTS:
            existing = by_id.get(slot["id"])
            if existing:
                existing["time"] = slot["time"]
                existing["prompt"] = FREE_TIME_PROMPT
            else:
                jobs.append({
                    "id": slot["id"],
                    "chat_id": GROUP_CHAT_ID, "thread_id": None, "topic_id": "group_general",
                    "prompt": FREE_TIME_PROMPT, "kind": "daily",
                    "time": slot["time"], "last_run_date": None,
                    "created_at": time.time(), "created_by": "system",
                })


def load_reminders_on_start():
    """Восстанавливает напоминания из файла после перезапуска."""
    global _reminder_id_counter
    if not os.path.exists(consts.REMINDERS_FILE):
        return
    try:
        with open(consts.REMINDERS_FILE) as f:
            data = json.load(f)
        for rid_str, r in data.items():
            rid = int(rid_str)
            if rid > _reminder_id_counter:
                _reminder_id_counter = rid
            with _reminder_lock:
                active_reminders[rid] = {
                    "text": r["text"],
                    "fire_at": r["fire_at"],
                    "repeat_secs": r.get("repeat_secs"),
                    "chat_id": r.get("chat_id"),
                    "topic_id": r.get("topic_id"),
                }
    except Exception as e:
        print(f"Ошибка загрузки напоминаний: {e}")
