"""Строка диагностики после восстановления опроса Telegram.

Чистая функция без побочных эффектов: её можно прогнать на заранее сломанных входах,
не дожидаясь настоящего сбоя сети."""

import time

_KIND_NAMES = (("сообщений", ("message", "edited_message")), ("нажатий кнопок", ("callback_query",)),
               ("реакций", ("message_reaction",)), ("опросов", ("poll", "poll_answer", "poll_option_added")))


def _kinds(updates):
    """' (сообщений: 0, нажатий кнопок: 2, прочих: 1)' — что именно лежало в накопленной пачке: по одному
    числу «апдейтов» не отличить пропавшие нажатия кнопок от пропавших сообщений. Пустая пачка — ''."""
    if not updates:
        return ""
    parts, known = [], 0
    for title, keys in _KIND_NAMES:
        n = sum(1 for u in updates if any(k in u for k in keys))
        known += n
        if n or title in ("сообщений", "нажатий кнопок"):
            parts.append(f"{title}: {n}")
    other = len(updates) - known
    if other > 0:
        parts.append(f"прочих: {other}")
    return " (" + ", ".join(parts) + ")"


def format_recovery(updates, fail_run, failed_started, noticed, request_seconds, now_ts=None):
    """Строка '[poll recovered] ...' для первой удачной пачки после серии сбоев.

    Возраст считается по времени отправки самого сообщения в Telegram: накопленная пачка
    возвращается сразу и содержит сообщения, отправленные до сбоя, а живое приходит с возрастом 0."""
    now_ts = time.time() if now_ts is None else now_ts
    updates = updates or []
    dates = []
    for u in updates:
        msg = u.get("message") or u.get("edited_message") or {}
        d = msg.get("date")
        if isinstance(d, (int, float)):
            dates.append(d)
    if dates:
        age = f"{int(now_ts - min(dates))} с"
        if len(dates) > 1:
            # узкий разброс означает отправлено почти разом, широкий означает слали всё время простоя
            age += f" (новейшего {int(now_ts - max(dates))} с)"
    else:
        age = "нет данных"
    return (f"[poll recovered] сбоев подряд: {fail_run}, "
            f"первый неудачный запрос начат в {failed_started}, сбой замечен в {noticed}, "
            f"в первой пачке апдейтов: {len(updates)}{_kinds(updates)}, возраст старейшего сообщения: {age}, "
            f"последний запрос шёл {request_seconds:.1f} с")
