"""Кнопки разрешения вызова инструментов (режим «Права: спрашивать»).

permission_mcp_server.py кладёт запрос в perm_requests/<id>.req. Здесь фоновый поток находит карточку этого вызова
(по tool_use_id, см. cardview.register_card) и вешает на неё кнопки ✅ Разрешить / ❌ Запретить — вопрос и команда
остаются в одном сообщении. Если карточки нет (вызов субагента, сбой) — через несколько секунд шлётся отдельное
сообщение с описанием вызова."""

import html
import json
import os
import time
import threading

from bridge import cardview, consts, core, tgapi

CARD_WAIT = 6.0     # сколько секунд ждать появления карточки, прежде чем слать отдельное сообщение
STALE_AFTER = 900   # запросы старше этого (секунд) при старте и в цикле считаем мёртвыми и удаляем
_handled = set()
_lock = threading.Lock()


def perm_keyboard(rid, always_ok):
    rows = [[{"text": "✅ Разрешить", "callback_data": f"perm_{rid}_a"},
             {"text": "❌ Запретить", "callback_data": f"perm_{rid}_d"}]]
    if always_ok:
        rows.append([{"text": "♾ Всегда разрешать этот инструмент", "callback_data": f"perm_{rid}_r"}])
    return rows


def _attach_to_card(req, card):
    """Добавляет строки с кнопками разрешения к кнопкам раскрытия карточки (они остаются)."""
    base = cardview._reveal_keyboard(full_key=card.get("full_key")) or {"inline_keyboard": []}
    kb = {"inline_keyboard": perm_keyboard(req["id"], req.get("always_ok", True)) + base["inline_keyboard"]}
    text = cardview._get_card_text(card["chat_id"], card["mid"])
    if not text:
        return False
    tgapi.edit_message(card["mid"], text, reply_markup=kb, chat_id=card["chat_id"])
    return True


def _send_standalone(req):
    """Запасной путь: карточка не нашлась — отдельное сообщение. Текст начинается с «🔐», по нему
    обработчик нажатия отличает его от карточки."""
    chat_id = int(req["chat_id"]) if req.get("chat_id") else None
    if chat_id is None:
        return
    thread_id = int(req["thread_id"]) if req.get("thread_id") else None
    text = (f"🔐 <b>Разрешить вызов?</b>\n<b>{html.escape(req['tool'])}</b>\n"
            f"<pre>{html.escape(req.get('desc', ''))}</pre>")
    payload = {"chat_id": chat_id, "text": text, "parse_mode": "HTML",
               "reply_markup": {"inline_keyboard": perm_keyboard(req["id"], req.get("always_ok", True))}}
    if thread_id:
        payload["message_thread_id"] = thread_id
    import requests
    try:
        requests.post(f"{core.API}/sendMessage", json=payload, timeout=(10, 20))
    except Exception as e:
        print(f"[permui] не удалось отправить запрос разрешения: {e}")


def _scan_once():
    try:
        names = [n for n in os.listdir(consts.PERM_DIR) if n.endswith(".req")]
    except FileNotFoundError:
        return
    now = time.time()
    for name in names:
        path = os.path.join(consts.PERM_DIR, name)
        try:
            with open(path) as f:
                req = json.load(f)
        except Exception:
            continue                      # файл ещё пишется или уже удалён
        rid = req.get("id")
        if now - req.get("t", now) > STALE_AFTER:
            for ext in ("req", "decision"):
                try:
                    os.unlink(os.path.join(consts.PERM_DIR, f"{rid}.{ext}"))
                except FileNotFoundError:
                    pass
            continue
        with _lock:
            if rid in _handled:
                continue
        card = cardview.find_card(req.get("tool_use_id")) if req.get("tool_use_id") else None
        if card and _attach_to_card(req, card):
            pass
        elif now - req.get("t", now) < CARD_WAIT:
            continue                      # карточка могла просто ещё не появиться — подождём
        else:
            _send_standalone(req)
        with _lock:
            _handled.add(rid)
            if len(_handled) > 500:
                _handled.clear()


def watch_loop():
    """Фоновый поток бота: ищет новые запросы разрешений и показывает кнопки."""
    os.makedirs(consts.PERM_DIR, exist_ok=True)
    while True:
        try:
            _scan_once()
        except Exception as e:
            print(f"[permui] ошибка цикла: {e}")
        time.sleep(0.4)
