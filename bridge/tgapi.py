"""Низкоуровневая отправка в Telegram: сообщения, правки, реакции, файлы, опросы, кэши."""

import contextlib
import time
import threading
import html as html_lib
import re
import requests
import os
import json
from secrets_local import GROUP_CHAT_ID
from bridge import claude_run, consts, core, keyboards, mdfmt


# Хранение последних отправленных сообщений для сопоставления реакций
sent_messages = {}  # message_id -> text snippet
message_chat_thread = {}  # message_id -> (chat_id, thread_id) — куда отвечать на реакцию к этому сообщению
MAX_SENT_CACHE = 50

# Кэш опросов: poll_id -> {question, options}
_poll_cache = {}


# ── Messaging ─────────────────────────────────────────────────────────────────

def track_chat_message(msg_id):
    """Добавляем ID сообщения в список для последующей очистки чата."""
    try:
        ids = []
        if os.path.exists(consts.CHAT_IDS_FILE):
            with open(consts.CHAT_IDS_FILE) as f:
                ids = json.load(f)
        ids.append(msg_id)
        # Храним последние 2000 ID
        if len(ids) > 2000:
            ids = ids[-2000:]
        with open(consts.CHAT_IDS_FILE, "w") as f:
            json.dump(ids, f)
    except Exception:
        pass

def load_last_message_ids():
    try:
        return json.load(open(consts.LAST_MSG_FILE))
    except Exception:
        return {}

def track_last_message(topic_id, msg_id):
    """Запоминаем ID последнего входящего сообщения в теме/чате — нужно чтобы
    понять, что пользователь отредактировал именно ПОСЛЕДНЕЕ сообщение."""
    try:
        key = str(topic_id) if topic_id is not None else "private"
        d = load_last_message_ids()
        d[key] = msg_id
        with open(consts.LAST_MSG_FILE, "w") as f:
            json.dump(d, f)
    except Exception:
        pass

def clear_chat_messages():
    """Удаляем все отслеживаемые сообщения из Telegram и очищаем список."""
    try:
        if not os.path.exists(consts.CHAT_IDS_FILE):
            return 0
        with open(consts.CHAT_IDS_FILE) as f:
            ids = json.load(f)
        deleted = 0
        for mid in ids:
            r = requests.post(f"{core.API}/deleteMessage", json={
                "chat_id": GROUP_CHAT_ID,
                "message_id": mid
            })
            if r.json().get("ok"):
                deleted += 1
        with open(consts.CHAT_IDS_FILE, "w") as f:
            json.dump([], f)
        return deleted
    except Exception:
        return 0

def cache_sent_message(msg_id, text, chat_id=None, thread_id=None):
    """Кэшируем отправленное сообщение для сопоставления с реакциями (и его тему/чат)."""
    global sent_messages
    sent_messages[msg_id] = text[:200]
    message_chat_thread[msg_id] = (chat_id, thread_id)
    track_chat_message(msg_id)
    # Чистим старые записи
    if len(sent_messages) > MAX_SENT_CACHE:
        oldest = sorted(sent_messages.keys())[:len(sent_messages) - MAX_SENT_CACHE]
        for k in oldest:
            del sent_messages[k]
            message_chat_thread.pop(k, None)

def _ensure_keyboard(reply_markup, chat_id, thread_id):
    """Если сообщение уходит без своей клавиатуры — прикрепляет основную reply-клавиатуру.
    Инлайн-клавиатуры (reveal-кнопки и т.п.) не трогает — у сообщения может быть только один
    reply_markup, приоритет за тем что явно передали.
    Прикрепляем на каждое такое сообщение, а не один раз на тему: Telegram-клиент позволяет
    пользователю свернуть/закрыть клавиатуру вручную, и тогда разовое прикрепление никогда
    больше её не покажет — бот думал бы, что она уже показана, хотя пользователь её не видит.
    selective=False: плейсхолдер "⏳", к которому это чаще всего цепляется, не является reply
    на чьё-то сообщение и никого не @упоминает — Telegram требует ровно это для selective-
    клавиатур, иначе просто не показывает её никому. Серверная проверка ALLOWED_USER_ID в
    handle_message всё равно не даст никому кроме Сергея реально ей пользоваться.

    ВАЖНО (найдено 2026-09-27): сообщение с прикреплённой reply-клавиатурой Telegram
    больше НЕЛЬЗЯ отредактировать через editMessageText — падает 400 "message can't be
    edited". Поэтому любой send_single(...), результат которого потом планируется
    редактировать (карточки инструментов live-стрима, плейсхолдер "🎤 Слушаю..." при
    транскрипции голосовых и т.п.), обязан передавать attach_keyboard=False — иначе
    редактирование будет молча (см. print в edit_message) проваливаться навсегда."""
    if reply_markup is not None:
        return reply_markup
    return keyboards.get_main_keyboard(topic_id=claude_run.chat_thread_to_topic_id(chat_id, thread_id), selective=False)

def _html_to_plain(chunk):
    """Убирает HTML-теги и раскодирует сущности — запасной вариант, когда Telegram отверг разметку."""
    return html_lib.unescape(re.sub(r"<[^>]+>", "", chunk))


_send_locks = {}
_send_locks_guard = threading.Lock()


def _chat_send_lock(chat_id):
    with _send_locks_guard:
        return _send_locks.setdefault(chat_id, threading.Lock())


def _send_with_retry(payload, attempts=4, max_wait=65, method="sendMessage"):
    """sendMessage с очередью по чату и повтором при 429. У Telegram лимит на сообщения в чат
    (≈20/мин в группе), при частых нажатиях кнопок он отвечает 429 с retry_after. Отправки в один чат
    идут строго по очереди (lock), а при 429 поток ждёт retry_after, не отпуская lock, — поэтому сообщения
    не теряются и не превращаются в сырой HTML, а доставляются по порядку, с задержкой."""
    d = {}
    with _chat_send_lock(payload.get("chat_id")):
        for attempt in range(attempts):
            try:
                d = requests.post(f"{core.API}/{method}", json=payload, timeout=(10, 30)).json()
            except requests.exceptions.RequestException as e:
                # сеть моргнула (таймаут/обрыв) — без этого поток зависал или падал, а сообщение терялось
                print(f"[send_single network] попытка {attempt + 1}: {e}")
                d = {"ok": False, "error_code": 0, "description": str(e)}
                if attempt == attempts - 1:
                    return d
                time.sleep(3)
                continue
            if d.get("ok") or d.get("error_code") != 429:
                return d
            wait = d.get("parameters", {}).get("retry_after", 3) + 1
            if wait > max_wait or attempt == attempts - 1:
                print(f"[send_single 429] сообщение не отправлено (retry_after={wait - 1}с)")
                return d
            time.sleep(wait)
    return d


def _send_single_text(text, reply_markup=None, chat_id=None, thread_id=None, reply_to=None, attach_keyboard=True):
    """Отправляет одно сообщение (с разбивкой по 4096 если нужно). Markdown-разметка
    парсится всегда. attach_keyboard=False — не подставлять авто-клавиатуру (для сообщений
    вроде плейсхолдера "⏳", которые почти сразу удаляются: reply-клавиатура, прикреплённая
    к удалённому сообщению, у части Telegram-клиентов из-за этого просто гаснет)."""
    target_chat = chat_id if chat_id is not None else GROUP_CHAT_ID
    if attach_keyboard:
        reply_markup = _ensure_keyboard(reply_markup, target_chat, thread_id)
    text = mdfmt.markdown_to_html(text)
    text = text.replace('\x01', '<i>').replace('\x02', '</i>')
    msg_id = None
    for i in range(0, len(text), 4096):
        chunk = text[i:i+4096]
        payload = {
            "chat_id": target_chat,
            "text": chunk,
            "parse_mode": "HTML",
        }
        if thread_id is not None:
            payload["message_thread_id"] = thread_id
        if reply_to is not None and i == 0:
            payload["reply_parameters"] = {"message_id": reply_to}
        if reply_markup and i + 4096 >= len(text):
            payload["reply_markup"] = reply_markup
        d = _send_with_retry(payload)
        if not d.get("ok") and "can't parse entities" in str(d.get("description", "")):
            # Telegram не принял разметку — шлём читаемый plain text (без видимых HTML-тегов)
            payload["text"] = _html_to_plain(chunk)
            payload.pop("parse_mode", None)
            d = _send_with_retry(payload)
        if d.get("ok"):
            mid = d["result"]["message_id"]
            if i == 0:
                msg_id = mid
            cache_sent_message(mid, text, chat_id=target_chat, thread_id=thread_id)
    return msg_id


# ── Rich-сообщения, таблицы, управление группой/темами (тулы mcp__telegram__*) ──

def _chat_link(chat_id, thread_id):
    """Ссылка t.me/c/... на тему супергруппы (для чатов вида -100XXXXXXXXXX)."""
    cid = str(chat_id)
    return f"https://t.me/c/{cid[4:]}/{thread_id}" if cid.startswith("-100") else None


def send_rich(markdown, chat_id=None, thread_id=None, reply_markup=None):
    """Отправляет rich-сообщение (Bot API 10.x sendRichMessage, поле rich_message.markdown: заголовки,
    таблицы, списки, цитаты, сворачиваемые блоки, формулы). Если Telegram отверг — шлёт обычным
    send_single, чтобы текст не пропал. Возвращает message_id."""
    target_chat = chat_id if chat_id is not None else GROUP_CHAT_ID
    markdown = str(markdown or "").strip()
    if not markdown:
        return None
    payload = {"chat_id": target_chat, "rich_message": {"markdown": markdown}}
    if thread_id is not None:
        payload["message_thread_id"] = thread_id
    if reply_markup:
        payload["reply_markup"] = reply_markup
    d = _send_with_retry(payload, method="sendRichMessage")
    if d.get("ok"):
        mid = d["result"]["message_id"]
        cache_sent_message(mid, markdown, chat_id=target_chat, thread_id=thread_id)
        return mid
    print(f"[send_rich] Telegram отверг rich-сообщение ({d.get('description')}) — шлю обычным текстом")
    return send_single(markdown, reply_markup, chat_id=target_chat, thread_id=thread_id)


def build_table_markdown(headers, rows, title="", align=None):
    """Собирает markdown-таблицу. align — список 'left'|'center'|'right' по колонкам (необязательно)."""
    headers = [str(h) for h in (headers or [])]
    if not headers:
        return ""
    n = min(len(headers), 20)  # лимит Telegram — 20 колонок
    headers = headers[:n]

    def cell(v):
        return str(v if v is not None else "").replace("|", "\\|").replace("\n", " ").strip()

    marks = {"left": ":---", "center": ":---:", "right": "---:"}
    aligns = [marks.get(str((align or [])[i]).lower(), "---") if i < len(align or []) else "---" for i in range(n)]
    lines = []
    if title:
        lines += [f"**{title}**", ""]
    lines.append("| " + " | ".join(cell(h) for h in headers) + " |")
    lines.append("|" + "|".join(aligns) + "|")
    for row in rows or []:
        row = list(row) if isinstance(row, (list, tuple)) else [row]
        row = (row + [""] * n)[:n]
        lines.append("| " + " | ".join(cell(c) for c in row) + " |")
    return "\n".join(lines)


def _bot_api(method, payload):
    """Вызов метода Bot API; возвращает dict ответа (ok=False + description при сбое)."""
    try:
        return requests.post(f"{core.API}/{method}", json=payload, timeout=(10, 30)).json()
    except requests.exceptions.RequestException as e:
        return {"ok": False, "description": str(e)}


def _report(chat_id, thread_id, text):
    send_single(text, chat_id=chat_id, thread_id=thread_id)


_PERM_KEYS = ("can_send_messages", "can_send_audios", "can_send_documents", "can_send_photos", "can_send_videos",
              "can_send_video_notes", "can_send_voice_notes", "can_send_polls", "can_send_other_messages",
              "can_add_web_page_previews", "can_react_to_messages", "can_edit_tag", "can_change_info",
              "can_invite_users", "can_pin_messages", "can_manage_topics")


def set_group_tool(ti, chat_id, thread_id):
    """Настройки группы: название, описание, права участников по умолчанию. Права ДОПОЛНЯЮТ текущие
    (setChatPermissions сам заменяет их целиком — пропущенные поля стали бы запретами)."""
    done, errors = [], []
    if ti.get("title"):
        d = _bot_api("setChatTitle", {"chat_id": chat_id, "title": str(ti["title"])[:128]})
        (done if d.get("ok") else errors).append(f"название: {'ок' if d.get('ok') else d.get('description')}")
    if ti.get("description") is not None:
        d = _bot_api("setChatDescription", {"chat_id": chat_id, "description": str(ti["description"])[:255]})
        (done if d.get("ok") else errors).append(f"описание: {'ок' if d.get('ok') else d.get('description')}")
    perms = ti.get("permissions")
    if isinstance(perms, dict) and perms:
        changes = {k: bool(v) for k, v in perms.items() if k in _PERM_KEYS}
        unknown = [k for k in perms if k not in _PERM_KEYS]
        cur = _bot_api("getChat", {"chat_id": chat_id})
        if not cur.get("ok"):
            errors.append(f"права: не удалось прочитать текущие ({cur.get('description')})")
        elif changes:
            merged = dict(cur["result"].get("permissions") or {})
            merged.update(changes)
            d = _bot_api("setChatPermissions", {"chat_id": chat_id, "permissions": merged,
                                                "use_independent_chat_permissions": True})
            (done if d.get("ok") else errors).append(
                "права: " + (", ".join(f"{k}={v}" for k, v in changes.items()) if d.get("ok") else str(d.get("description"))))
        if unknown:
            errors.append(f"права: неизвестные поля {unknown}")
    if not (done or errors):
        errors.append("не указано ни одной настройки")
    msg = "⚙️ Настройки группы:" + "".join(f"\n✅ {x}" for x in done) + "".join(f"\n❌ {x}" for x in errors)
    _report(chat_id, thread_id, msg)


def set_topic_tool(ti, chat_id, thread_id):
    """Настройки темы: название, иконка (custom emoji), открыть/закрыть. По умолчанию — текущая тема;
    ti['thread_id'] — другая. Без thread_id и вне темы работает на General."""
    target = ti.get("thread_id") or thread_id
    if target:
        target = int(target)
    general = not target
    done, errors = [], []

    def run(method, payload, label):
        d = _bot_api(method, payload)
        (done if d.get("ok") else errors).append(f"{label}: {'ок' if d.get('ok') else d.get('description')}")
        return d.get("ok")

    name = ti.get("name")
    if name or ti.get("icon_custom_emoji_id") is not None:
        if general:
            if name:
                run("editGeneralForumTopic", {"chat_id": chat_id, "name": str(name)[:128]}, "название General")
            if ti.get("icon_custom_emoji_id") is not None:
                errors.append("у General нельзя менять иконку")
        else:
            payload = {"chat_id": chat_id, "message_thread_id": target}
            if name:
                payload["name"] = str(name)[:128]
            if ti.get("icon_custom_emoji_id") is not None:
                payload["icon_custom_emoji_id"] = str(ti["icon_custom_emoji_id"])
            run("editForumTopic", payload, "название/иконка")
    state = ti.get("state")
    if state in ("open", "closed"):
        verb = ("reopen" if state == "open" else "close")
        method = f"{verb}GeneralForumTopic" if general else f"{verb}ForumTopic"
        payload = {"chat_id": chat_id} if general else {"chat_id": chat_id, "message_thread_id": target}
        run(method, payload, "открыта" if state == "open" else "закрыта")
    if not (done or errors):
        errors.append("не указано ни одной настройки")
    if name and any("название" in x and "ок" in x for x in done) and not general:
        from bridge import storage
        topics = storage.load_topics()
        topics[str(target)] = str(name)[:128]
        storage.save_topics(topics)
    msg = "🗂 Настройки темы:" + "".join(f"\n✅ {x}" for x in done) + "".join(f"\n❌ {x}" for x in errors)
    _report(chat_id, thread_id, msg)


_TOPIC_COLORS = (7322096, 16766590, 13338331, 9367192, 16749490, 16478047)


def create_topic_tool(ti, chat_id, thread_id):
    """Создаёт тему в форум-группе, если темы с таким названием ещё нет (по topics.json, без регистра)."""
    from bridge import storage
    name = str(ti.get("name", "")).strip()[:128]
    if not name:
        _report(chat_id, thread_id, "❌ Тема не создана: пустое название.")
        return
    for tid, tname in storage.load_topics().items():
        if str(tname).strip().casefold() == name.casefold():
            link = _chat_link(chat_id, tid)
            _report(chat_id, thread_id, f"ℹ️ Тема «{tname}» уже есть" + (f": {link}" if link else f" (№{tid})") + ".")
            return
    payload = {"chat_id": chat_id, "name": name}
    if int(ti.get("icon_color") or 0) in _TOPIC_COLORS:
        ti = dict(ti, icon_color=int(ti["icon_color"]))
        payload["icon_color"] = ti["icon_color"]
    if ti.get("icon_custom_emoji_id"):
        payload["icon_custom_emoji_id"] = str(ti["icon_custom_emoji_id"])
    d = _bot_api("createForumTopic", payload)
    if not d.get("ok"):
        _report(chat_id, thread_id, f"❌ Не удалось создать тему «{name}»: {d.get('description')}")
        return
    new_id = d["result"]["message_thread_id"]
    topics = storage.load_topics()
    topics[str(new_id)] = name
    storage.save_topics(topics)
    link = _chat_link(chat_id, new_id)
    _report(chat_id, thread_id, f"✅ Тема «{name}» создана" + (f": {link}" if link else f" (№{new_id})"))


_TABLE_SEP_RE = re.compile(r"^\s*\|?\s*:?-{3,}:?\s*(\|\s*:?-{3,}:?\s*)*\|?\s*$")
_SINGLE_STAR_RE = re.compile(r"(?<![\*\w])\*(?!\*)([^*\n]+?)(?<!\*)\*(?![\*\w])")


def split_tables(text):
    """Режет markdown-текст на сегменты [("text", ...), ("table", ...), ...]. Таблица — подряд идущие
    строки с | , где вторая строка — разделитель |---|---|. Таблицы внутри ``` блоков не трогаем."""
    lines = text.split("\n")
    segs, buf, i, in_code = [], [], 0, False

    def flush():
        if buf:
            segs.append(("text", "\n".join(buf)))
            buf.clear()

    while i < len(lines):
        ln = lines[i]
        if ln.strip().startswith("```"):
            in_code = not in_code
        if (not in_code and "|" in ln and i + 1 < len(lines) and _TABLE_SEP_RE.match(lines[i + 1])
                and "|" in lines[i + 1] and not _TABLE_SEP_RE.match(ln)):
            j = i + 2
            while j < len(lines) and "|" in lines[j] and lines[j].strip():
                j += 1
            flush()
            segs.append(("table", "\n".join(lines[i:j])))
            i = j
            continue
        buf.append(ln)
        i += 1
    flush()
    return [(k, v) for k, v in segs if v.strip()]


def send_single(text, reply_markup=None, chat_id=None, thread_id=None, reply_to=None, attach_keyboard=True):
    """Отправляет текст (markdown). Таблицы выделяются и уходят rich-сообщениями (sendRichMessage) —
    старый путь markdown→HTML таблицы не умеет и ломает; остальной текст — как раньше. Клавиатура/
    reply_markup цепляются к последнему сообщению. Возвращает message_id первого отправленного."""
    segs = split_tables(text) if isinstance(text, str) and "|" in text else []
    if not any(k == "table" for k, _ in segs):
        return _send_single_text(text, reply_markup, chat_id=chat_id, thread_id=thread_id,
                                 reply_to=reply_to, attach_keyboard=attach_keyboard)
    target_chat = chat_id if chat_id is not None else GROUP_CHAT_ID
    first_mid = None
    for n, (kind, chunk) in enumerate(segs):
        last = n == len(segs) - 1
        rm = reply_markup if last else None
        if kind == "text":
            mid = _send_single_text(chunk, rm, chat_id=chat_id, thread_id=thread_id,
                                    reply_to=reply_to if n == 0 else None,
                                    attach_keyboard=attach_keyboard and last)
        else:
            if last and attach_keyboard:
                rm = _ensure_keyboard(rm, target_chat, thread_id)
            mid = send_rich(_SINGLE_STAR_RE.sub(r"**\1**", chunk), chat_id=target_chat, thread_id=thread_id,
                            reply_markup=rm)
        if first_mid is None:
            first_mid = mid
    return first_mid


def set_reaction(chat_id, message_id, emoji=None, retries=2, _is_fallback=False):
    """Ставит или убирает реакцию на сообщение. emoji=None — убрать реакцию.
    Если Telegram отверг emoji (например невалидный для реакций) — реакцию снимаем,
    чтобы не оставлять "зависшую" 👀 навсегда."""
    reaction = [{"type": "emoji", "emoji": emoji}] if emoji else []
    for attempt in range(retries + 1):
        try:
            r = requests.post(f"{core.API}/setMessageReaction", json={
                "chat_id": chat_id,
                "message_id": message_id,
                "reaction": reaction,
                "is_big": False
            }, timeout=5)
            if r.json().get("ok"):
                return
            err = r.text[:200]
            print(f"[REACTION_SET] error (attempt {attempt+1}): {err}")
            if attempt < retries:
                time.sleep(1)
        except Exception as e:
            print(f"[REACTION_SET] exception (attempt {attempt+1}): {e}")
            if attempt < retries:
                time.sleep(1)
    if emoji and not _is_fallback:
        set_reaction(chat_id, message_id, None, retries=1, _is_fallback=True)

def is_single_emoji(text):
    """Проверяет, состоит ли текст ровно из одного эмодзи."""
    import unicodedata
    t = text.strip()
    if not t:
        return False
    # Собираем все символы, включая модификаторы и ZWJ-последовательности
    chars = list(t)
    # Убираем вариационные селекторы и ZWJ
    significant = [c for c in chars if c not in ('\ufe0f', '\u200d', '\ufe0e')]
    if len(significant) != 1:
        return False
    cat = unicodedata.category(significant[0])
    cp = ord(significant[0])
    return cat == 'So' or (0x1F000 <= cp <= 0x1FFFF) or (0x2600 <= cp <= 0x27BF)

def send_poll_tool(question, options, correct_option=0, explanation="", chat_id=None, thread_id=None):
    """Отправляет опрос по вызову тула telegram_send_poll. correct_option — номер верного
    варианта с 1 (0 или вне диапазона — обычный опрос без правильного ответа)."""
    target_chat = chat_id if chat_id is not None else GROUP_CHAT_ID
    options = [str(o).strip() for o in (options or []) if str(o).strip()]
    if len(options) < 2:
        return None
    try:
        correct_option = int(correct_option)
    except (TypeError, ValueError):
        correct_option = 0
    is_quiz = 1 <= correct_option <= len(options)
    payload = {
        "chat_id": target_chat,
        "question": str(question)[:300],
        "options": json.dumps([{"text": o[:100]} for o in options[:10]]),
        "is_anonymous": False,
        "is_closed": False,
    }
    if is_quiz:
        payload["type"] = "quiz"
        payload["correct_option_id"] = correct_option - 1
        if explanation:
            payload["explanation"] = str(explanation)[:200]
    if thread_id is not None:
        payload["message_thread_id"] = thread_id
    d = requests.post(f"{core.API}/sendPoll", data=payload, timeout=15).json()
    if not d.get("ok"):
        print(f"[send_poll_tool error] {d}")
        return None
    mid = d["result"]["message_id"]
    poll_id = d["result"]["poll"]["id"]
    _poll_cache[poll_id] = {"question": question, "options": options,
                             "chat_id": target_chat, "thread_id": thread_id}
    track_chat_message(mid)
    return mid


@contextlib.contextmanager
def chat_action(chat_id, thread_id, action="typing", interval=4.0):
    """Плашка «печатает… / отправляет фото…» (sendChatAction), пока выполняется блок with. Telegram гасит
    её через ~5 с или при отправке сообщения, поэтому запрос повторяется каждые `interval` секунд
    в фоновом потоке. Любые сетевые сбои игнорируются: плашка — украшение, ломать из-за неё ход нельзя."""
    stop = threading.Event()
    target = chat_id if chat_id is not None else GROUP_CHAT_ID

    def loop():
        payload = {"chat_id": target, "action": action}
        if thread_id is not None:
            payload["message_thread_id"] = thread_id
        while not stop.is_set():
            try:
                requests.post(f"{core.API}/sendChatAction", json=payload, timeout=5)
            except Exception:
                pass
            stop.wait(interval)

    t = threading.Thread(target=loop, daemon=True)
    t.start()
    try:
        yield
    finally:
        stop.set()


_FILE_SEND_METHODS = {
    "photo": ("sendPhoto", "photo"),
    "file": ("sendDocument", "document"),
    "audio": ("sendAudio", "audio"),
    "voice": ("sendVoice", "voice"),
    "video": ("sendVideo", "video"),
    "animation": ("sendAnimation", "animation"),
}

_FILE_CHAT_ACTIONS = {"photo": "upload_photo", "file": "upload_document", "audio": "upload_document",
                      "voice": "upload_voice", "video": "upload_video", "animation": "upload_video"}


def send_file_tool(kind, path, caption="", chat_id=None, thread_id=None):
    """Отправляет файл по вызову тула telegram_send_photo/file/audio/... Возвращает message_id
    или None (ошибка уже залогирована и показана в чате)."""
    target_chat = chat_id if chat_id is not None else GROUP_CHAT_ID
    method, field = _FILE_SEND_METHODS[kind]
    path = os.path.expanduser(str(path))
    if not os.path.isfile(path):
        send_single(f"⚠️ Не могу отправить файл — не найден: `{path}`", chat_id=target_chat,
                    thread_id=thread_id, attach_keyboard=False)
        return None
    data = {"chat_id": target_chat}
    if thread_id is not None:
        data["message_thread_id"] = thread_id
    if caption:
        data["caption"] = mdfmt.markdown_to_html(str(caption))[:1024]
        data["parse_mode"] = "HTML"
    try:
        with chat_action(target_chat, thread_id, _FILE_CHAT_ACTIONS.get(kind, "upload_document")), open(path, "rb") as f:
            # локальный Bot API отвечает только после того, как сам загрузил файл в Telegram: для сотен МБ это минуты
            d = requests.post(f"{core.API}/{method}", data=data, files={field: f},
                              timeout=(10, 1800 if consts.BOT_API_LOCAL else 120)).json()
    except Exception as e:
        d = {"ok": False, "description": str(e)}
    if not d.get("ok"):
        print(f"[send_file_tool error] {method} {path}: {d}")
        send_single(f"⚠️ Не удалось отправить `{os.path.basename(path)}`: {d.get('description', '?')}",
                    chat_id=target_chat, thread_id=thread_id, attach_keyboard=False)
        return None
    mid = d["result"]["message_id"]
    cache_sent_message(mid, caption or f"[{kind}: {os.path.basename(path)}]", chat_id=target_chat, thread_id=thread_id)
    return mid

def send_message(text, reply_markup=None, chat_id=None, thread_id=None, reply_to=None):
    """Отправляет одно сообщение (длинные автоматически режутся по 4096 в send_single)."""
    if text is None or not text.strip():
        return None
    return send_single(text.strip(), reply_markup, chat_id=chat_id, thread_id=thread_id, reply_to=reply_to)

def delete_message(mid, chat_id=None):
    requests.post(f"{core.API}/deleteMessage", json={
        "chat_id": chat_id if chat_id is not None else GROUP_CHAT_ID,
        "message_id": mid
    })

_edit_seq = {}
_edit_seq_lock = threading.Lock()

def _post_edit(payload, key, seq, attempt=0):
    # 429 — общий лимит Telegram на чат: ждём retry_after в фоне и повторяем,
    # но только если по этому сообщению не было более новой правки.
    try:
        r = requests.post(f"{core.API}/editMessageText", json=payload, timeout=10)
        j = r.json()
        if j.get("ok"):
            return
        if j.get("error_code") == 429 and attempt < 3:
            wait = j.get("parameters", {}).get("retry_after", 5) + 1
            print(f"[edit_message 429] retry {attempt + 1} через {wait}с")
            def _retry():
                with _edit_seq_lock:
                    if _edit_seq.get(key) != seq:
                        return
                _post_edit(payload, key, seq, attempt + 1)
            t = threading.Timer(wait, _retry)
            t.daemon = True
            t.start()
            return
        print(f"[edit_message error] {j}")
    except Exception as e:
        print(f"[edit_message exception] {e}")

def edit_message(mid, text, reply_markup=None, chat_id=None):
    try:
        cid = chat_id if chat_id is not None else GROUP_CHAT_ID
        payload = {
            "chat_id": cid,
            "message_id": mid,
            "text": mdfmt.markdown_to_html(text),
            "parse_mode": "HTML",
        }
        if reply_markup:
            payload["reply_markup"] = reply_markup
        key = (cid, mid)
        with _edit_seq_lock:
            seq = _edit_seq[key] = _edit_seq.get(key, 0) + 1
            if len(_edit_seq) > 2000:
                _edit_seq.clear()
                _edit_seq[key] = seq
        _post_edit(payload, key, seq)
    except Exception as e:
        print(f"[edit_message exception] {e}")

def answer_callback(callback_id, text=""):
    try:
        d = requests.post(f"{core.API}/answerCallbackQuery", json={
            "callback_query_id": callback_id,
            "text": text
        }, timeout=10).json()
        if not d.get("ok"):
            print(f"[CB] answerCallbackQuery отклонён: {d.get('description')}")
    except requests.exceptions.RequestException as e:
        print(f"[answer_callback network] {e}")
