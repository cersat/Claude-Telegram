"""Live-карточки инструментов, кнопки раскрытия, стрим-обработчик, действия mcp__telegram__*."""

import threading
from bridge import consts, keyboards, tgapi


def _inline_code(s):
    """Заворачивает сырой текст (путь/паттерн — короткий, однострочный) в markdown-инлайн-код
    (1 апостроф), чтобы markdown_to_html не трактовал `_`/`*`/`~` внутри как разметку.
    Обратные кавычки внутри сырого текста экранируются, иначе span оборвётся раньше."""
    return f"`{str(s).replace('`', chr(0x2018))}`"

def _code_block(s, lang=""):
    """Заворачивает команду/вывод (может быть длинным/многострочным) в markdown code fence
    (3 апострофа) — рендерится моноширинным блоком, а не однострочным инлайн-кодом.
    Тройные кавычки внутри экранируются, иначе fence оборвётся раньше."""
    body = str(s).replace('```', '``' + chr(0x2018))
    return f"```{lang}\n{body}\n```"

def format_tool_use(name, tool_input):
    """Человекочитаемое описание вызова инструмента для стрим-статуса.
    Read/Write/Edit по файлам памяти (MEMORY_DIR) — отдельный "коннектор памяти",
    помечается 💡 вместо обычного 📖/📝/✏️, чтобы отличать от чтения/записи прочих файлов.
    Возвращает (label, full) — full не None только если label обрезан (сейчас — длинная
    команда Bash), чтобы вызывающий код мог добавить кнопку "Полное сообщение"."""
    ti = tool_input or {}
    if name == "Bash":
        cmd = str(ti.get('command', ''))
        label = f"💻\n{_code_block(cmd[:100], 'bash')}"
        return label, (cmd if len(cmd) > 100 else None)
    if name in ("Read", "Write", "Edit"):
        file_path = ti.get('file_path', '')
        if file_path.startswith(consts.MEMORY_DIR):
            verb = {"Read": "читаю", "Write": "пишу", "Edit": "правлю"}[name]
            return f"💡 память: {verb} {_inline_code(file_path)}", None
        emoji = {"Read": "📖", "Write": "📝", "Edit": "✏️"}[name]
        return f"{emoji} {_inline_code(file_path)}", None
    if name in ("Grep", "Glob"):
        return f"🔍 {_inline_code(ti.get('pattern', ''))}", None
    if name == "WebFetch":
        return f"🌐 {_inline_code(ti.get('url', ''))}", None
    if name == "WebSearch":
        return f"🌐 {_inline_code(ti.get('query', ''))}", None
    if name in ("Task", "Agent"):
        return f"🤖 {ti.get('description', name)}", None
    if name.startswith("mcp__memory__"):
        labels = {
            "mcp__memory__memory_list": "Список памяти",
            "mcp__memory__memory_read": "Чтение памяти",
            "mcp__memory__memory_write": "Запись в память",
        }
        return f"💡 {labels.get(name, _inline_code(name))}", None
    if name.startswith("mcp__bot_scheduler__"):
        labels = {
            "mcp__bot_scheduler__schedule_once": "Планирую пробуждение",
            "mcp__bot_scheduler__schedule_daily": "Планирую ежедневное пробуждение",
            "mcp__bot_scheduler__list_scheduled": "Список пробуждений",
            "mcp__bot_scheduler__cancel_scheduled": "Отменяю пробуждение",
        }
        return f"⏰ {labels.get(name, _inline_code(name))}", None
    if name.startswith("mcp__history__"):
        labels = {
            "mcp__history__history_list": "Список тем",
            "mcp__history__history_read": f"Читаю историю: {ti.get('topic', '')}".rstrip(": "),
        }
        return f"📃 {labels.get(name, _inline_code(name))}", None
    if name.startswith("mcp__moltbook__"):
        labels = {
            "mcp__moltbook__moltbook_home": "Чек-ин",
            "mcp__moltbook__moltbook_feed": "Фид",
            "mcp__moltbook__moltbook_my_profile": "Мой профиль",
            "mcp__moltbook__moltbook_my_posts": "История моих постов",
            "mcp__moltbook__moltbook_my_comments": "История моих комментариев",
            "mcp__moltbook__moltbook_post": "Пост",
            "mcp__moltbook__moltbook_comments": "Комментарии",
            "mcp__moltbook__moltbook_create_post": f"Публикую: {ti.get('title', '')}",
            "mcp__moltbook__moltbook_create_comment": "Комментирую",
            "mcp__moltbook__moltbook_edit_post": "Редактирую пост",
            "mcp__moltbook__moltbook_edit_comment": "Редактирую комментарий",
            "mcp__moltbook__moltbook_upvote": "Апвоут",
            "mcp__moltbook__moltbook_downvote": "Даунвоут",
            "mcp__moltbook__moltbook_upvote_comment": "Апвоут комментарию",
            "mcp__moltbook__moltbook_downvote_comment": "Даунвоут комментарию",
            "mcp__moltbook__moltbook_verify": "Прохожу капчу",
            "mcp__moltbook__moltbook_search": f"Поиск: {ti.get('q', '')}".rstrip(": "),
            "mcp__moltbook__moltbook_profile": f"Профиль: {ti.get('name', '')}".rstrip(": "),
            "mcp__moltbook__moltbook_update_profile": "Обновляю профиль",
            "mcp__moltbook__moltbook_follow": f"Подписка: {ti.get('name', '')}".rstrip(": "),
            "mcp__moltbook__moltbook_unfollow": f"Отписка: {ti.get('name', '')}".rstrip(": "),
            "mcp__moltbook__moltbook_notifications": "Уведомления",
            "mcp__moltbook__moltbook_read_notification": "Отмечаю уведомление прочитанным",
            "mcp__moltbook__moltbook_read_all_notifications": "Отмечаю все уведомления прочитанными",
            "mcp__moltbook__moltbook_delete_post": "Удаляю пост",
        }
        return f"🦞 {labels.get(name, _inline_code(name))}", None
    if name.startswith("mcp__claude_ai_"):
        return _format_claude_ai_tool(name, ti), None
    return f"🔧 {_inline_code(name)}", None


_DRIVE_LABELS = {
    "search_files": "Поиск на Диске",
    "list_recent_files": "Недавние файлы Диска",
    "read_file_content": "Читаю файл с Диска",
    "download_file_content": "Скачиваю файл с Диска",
    "get_file_metadata": "Сведения о файле",
    "get_file_permissions": "Доступ к файлу",
    "create_file": "Создаю файл на Диске",
    "update_file": "Обновляю файл на Диске",
    "copy_file": "Копирую файл на Диске",
    "share_file": "Открываю доступ к файлу",
    "trash_file": "Удаляю файл в корзину",
}
_CLAUDE_AI_SERVICES = {
    # префикс коннектора -> (эмодзи, название, подписи действий)
    "Google_Drive": ("📁", "Google Drive", _DRIVE_LABELS),
    "Google_Calendar": ("📅", "Календарь", {
        "list_calendars": "Список календарей", "list_events": "События календаря", "get_event": "Читаю событие",
        "search_events": "Поиск событий", "create_event": "Создаю событие", "update_event": "Обновляю событие",
        "delete_event": "Удаляю событие", "respond_to_event": "Отвечаю на приглашение", "suggest_time": "Подбираю время"}),
    "Gmail": ("✉️", "Gmail", {
        "search_threads": "Поиск писем", "get_thread": "Читаю цепочку писем", "get_message": "Читаю письмо",
        "create_draft": "Создаю черновик", "update_draft": "Обновляю черновик", "delete_draft": "Удаляю черновик",
        "get_draft": "Читаю черновик", "list_drafts": "Список черновиков", "send_message": "Отправляю письмо",
        "reply": "Отвечаю на письмо", "forward": "Пересылаю письмо", "list_labels": "Список ярлыков"}),
    "Microsoft_Learn": ("📚", "Microsoft Learn", {
        "microsoft_docs_search": "Поиск в документации Microsoft", "microsoft_docs_fetch": "Читаю страницу Microsoft Learn",
        "microsoft_code_sample_search": "Поиск примеров кода Microsoft"}),
    "Claude_Docs": ("📄", "Claude Docs", {}),
}


def _format_claude_ai_tool(name, ti):
    """Подпись для коннекторов claude.ai (Google Drive/Calendar, Gmail и т.п.): имя вида
    mcp__claude_ai_<Сервис>__<действие>. Известным действиям — русские подписи, остальным —
    «Сервис: действие» без сырого имени тула. К подписи добавляется самый показательный аргумент
    (запрос, название, имя файла), если он есть."""
    body = name[len("mcp__claude_ai_"):]
    service, _, action = body.partition("__")
    emoji, title, labels = _CLAUDE_AI_SERVICES.get(service, ("🔌", service.replace("_", " "), {}))
    label = labels.get(action) or f"{title}: {action.replace('_', ' ')}"
    detail = ""
    for key in ("query", "q", "title", "name", "summary", "subject", "fileName", "file_name", "fileId", "file_id", "url"):
        v = ti.get(key)
        if isinstance(v, str) and v.strip():
            detail = v.strip()
            break
    if detail:
        label += f": {_inline_code(detail[:80])}"
    return f"{emoji} {label}"

# Кэш "развёрнутого" текста для кнопок "Полное сообщение"/"Посмотреть текст" под карточками
# live-стрима — сам текст слишком длинный/динамический для callback_data (лимит 64 байта).
_reveal_cache = {}
_reveal_counter = 0
_reveal_lock = threading.Lock()

def _cache_reveal(text):
    """Кладёт текст в кэш, возвращает короткий callback_data-ключ вида 'rvl_123'."""
    global _reveal_counter
    with _reveal_lock:
        _reveal_counter += 1
        key = f"rvl_{_reveal_counter}"
        _reveal_cache[key] = text
        if len(_reveal_cache) > 300:
            oldest = sorted(_reveal_cache.keys(), key=lambda k: int(k.split('_')[1]))[:len(_reveal_cache) - 300]
            for k in oldest:
                del _reveal_cache[k]
    return key


def _reveal_keyboard(full_key=None, result_key=None):
    """Строит inline-клавиатуру из кнопок раскрытия, которые применимы (может быть 0/1/2)."""
    row = []
    if full_key:
        row.append({"text": "📄 Полное сообщение", "callback_data": full_key})
    if result_key:
        row.append({"text": "📋 Посмотреть текст", "callback_data": result_key})
    return {"inline_keyboard": [row]} if row else None


# markdown-исходник текущего текста карточки live-стрима (chat_id, message_id) -> текст.
# Нужен чтобы кнопки "Полное сообщение"/"Посмотреть текст" дописывали раскрытый текст ПРЯМО
# в сообщение (editMessageText), а не слали новое — Telegram возвращает в cq["message"]["text"]
# уже отрендеренный plain text без markdown-разметки, реконструировать из него нельзя.
_card_state = {}
_card_state_lock = threading.Lock()

def _set_card_text(chat_id, mid, text):
    key = (chat_id, mid)
    with _card_state_lock:
        _card_state[key] = text
        if len(_card_state) > 300:
            for k in list(_card_state.keys())[:len(_card_state) - 300]:
                del _card_state[k]

def _get_card_text(chat_id, mid, fallback=""):
    return _card_state.get((chat_id, mid), fallback)


MOLTBOOK_POST_URL = "https://www.moltbook.com/post/{}"


def moltbook_post_link(name, result_text):
    """Ссылка на опубликованный пост Moltbook по результату тула, иначе None. Пост считается
    опубликованным после moltbook_verify (content_type == "post"); create_post с
    verification_status "pending" ещё не опубликован — ссылки нет, пока не пройдена капча."""
    if not name or not result_text:
        return None
    import json as _json
    try:
        data = _json.loads(result_text)
        if isinstance(data, dict) and isinstance(data.get("result"), str):  # обёртка MCP {"result": "<json>"}
            data = _json.loads(data["result"])
    except Exception:
        return None
    if not isinstance(data, dict) or not data.get("success"):
        return None
    if name.endswith("moltbook_verify") and data.get("content_type") == "post" and data.get("content_id"):
        return MOLTBOOK_POST_URL.format(data["content_id"])
    if name.endswith("moltbook_create_post"):
        post = data.get("post") or {}
        if post.get("id") and str(post.get("verification_status", "")).lower() not in ("pending", "failed", "rejected"):
            return MOLTBOOK_POST_URL.format(post["id"])
    return None


def _run_telegram_action(name, ti, chat_id, thread_id, react_target, turn_state):
    """Выполняет вызов тула mcp__telegram__* (см. telegram_mcp_server.py): сам тул ничего не
    делает, действие — здесь, в чат/тему текущего диалога."""
    action = name.split("__", 2)[-1]
    if action.startswith("telegram_"):
        action = action[len("telegram_"):]
    if action.startswith("send_") and action[len("send_"):] in tgapi._FILE_SEND_METHODS:
        tgapi.send_file_tool(action[len("send_"):], ti.get("path", ""), ti.get("caption", ""),
                       chat_id=chat_id, thread_id=thread_id)
    elif action == "send_poll":
        tgapi.send_poll_tool(ti.get("question", ""), ti.get("options", []), ti.get("correct_option", 0),
                       ti.get("explanation", ""), chat_id=chat_id, thread_id=thread_id)
    elif action == "react":
        if react_target:
            tgapi.set_reaction(react_target[0], react_target[1], ti.get("emoji") or None)
            turn_state["reacted"] = True
    elif action == "send_buttons":
        kb = keyboards.build_claude_buttons(ti.get("buttons"))
        text = str(ti.get("text", "")).strip() or "👇"
        tgapi.send_single(text, kb, chat_id=chat_id, thread_id=thread_id)
    elif action == "send_rich":
        tgapi.send_rich(ti.get("markdown", ""), chat_id=chat_id, thread_id=thread_id)
    elif action == "send_table":
        md = tgapi.build_table_markdown(ti.get("headers"), ti.get("rows"), ti.get("title", ""), ti.get("align"))
        if md:
            tgapi.send_rich(md, chat_id=chat_id, thread_id=thread_id)
        else:
            tgapi.send_single("❌ Таблица не отправлена: не заданы заголовки.", chat_id=chat_id, thread_id=thread_id)
    elif action == "set_group":
        tgapi.set_group_tool(ti, chat_id, thread_id)
    elif action == "set_topic":
        tgapi.set_topic_tool(ti, chat_id, thread_id)
    elif action == "create_topic":
        tgapi.create_topic_tool(ti, chat_id, thread_id)
    else:
        print(f"[tg_action] неизвестное действие: {name}")


def make_stream_handler(chat_id, thread_id, placeholder_id=None, react_target=None, turn_state=None):
    """Строит on_event-колбэк для ask_claude: каждый вызов инструмента и каждый
    промежуточный текстовый блок уходит отдельным сообщением в чат (а не правит
    одно "Думаю..."). Вызовы mcp__telegram__* выполняются как действия (файл, реакция,
    кнопки, опрос). react_target=(chat_id, message_id) — сообщение пользователя, на которое
    ставит реакцию telegram_react; turn_state — общий с run_claude_turn словарь
    ("reacted" — реакция уже поставлена, "texts" — live-тексты для истории).
    Возвращает (on_event, clear_placeholder, mark_stopped)."""
    state = {"placeholder": placeholder_id}
    if turn_state is None:
        turn_state = {}
    tool_msgs = {}

    def clear_placeholder():
        pid = state["placeholder"]
        if pid:
            tgapi.delete_message(pid, chat_id=chat_id)
            state["placeholder"] = None

    def on_event(kind, payload):
        clear_placeholder()
        if kind == "tool_start":
            label = payload["label"]
            full = payload.get("full")
            full_key = _cache_reveal(_code_block(full, "bash")) if full else None
            kb = _reveal_keyboard(full_key=full_key)
            mid = tgapi.send_single(label, kb, chat_id=chat_id, thread_id=thread_id, attach_keyboard=False)
            if mid and payload.get("id"):
                tool_msgs[payload["id"]] = {"mid": mid, "label": label, "full_key": full_key}
                _set_card_text(chat_id, mid, label)
        elif kind == "tool_done":
            entry = tool_msgs.pop(payload.get("id"), None)
            if entry:
                mid, label, full_key = entry["mid"], entry["label"], entry["full_key"]
                if payload.get("stopped"):
                    mark = "🛑"
                else:
                    mark = "✅" if payload.get("ok") else "⚠️"
                result_text = (payload.get("result_text") or "").strip()
                result_key = _cache_reveal(_code_block(result_text)) if result_text else None
                kb = _reveal_keyboard(full_key=full_key, result_key=result_key)
                text = f"{mark} {label}"
                post_link = moltbook_post_link(payload.get("name"), result_text) if payload.get("ok") else None
                if post_link:
                    text += f"\n🔗 Пост опубликован: {post_link}"
                tgapi.edit_message(mid, text, reply_markup=kb, chat_id=chat_id)
                _set_card_text(chat_id, mid, text)
        elif kind == "text":
            clean = payload.strip()
            if clean:
                turn_state.setdefault("texts", []).append(clean)
                tgapi.send_single(clean, chat_id=chat_id, thread_id=thread_id)
        elif kind == "tg_action":
            try:
                _run_telegram_action(payload["name"], payload["input"], chat_id, thread_id,
                                     react_target, turn_state)
            except Exception as e:
                print(f"[tg_action error] {payload.get('name')}: {e}")

    def mark_stopped():
        """Помечает все ещё не завершённые карточки инструментов как остановленные."""
        clear_placeholder()
        for entry in tool_msgs.values():
            kb = _reveal_keyboard(full_key=entry["full_key"])
            text = f"🛑 {entry['label']}"
            tgapi.edit_message(entry["mid"], text, reply_markup=kb, chat_id=chat_id)
            _set_card_text(chat_id, entry["mid"], text)
        tool_msgs.clear()

    return on_event, clear_placeholder, mark_stopped
