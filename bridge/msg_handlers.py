"""Обработчики входящих сообщений."""

import threading
import requests
import os
import re
from secrets_local import ALLOWED_USER_ID, GROUP_CHAT_ID
from bridge import authlogin, chessgame, claude_run, consts, core, keyboards, mdfmt, mediaio, memui, procs, reminders, shared, storage, sysinfo, tgapi


# ── Handlers ──────────────────────────────────────────────────────────────────

def _remember_topic_name(thread_id, name):
    """Запоминает (или обновляет) название темы в topics.json, если оно изменилось."""
    if not thread_id or not name:
        return
    topics = storage.load_topics()
    if topics.get(str(thread_id)) != name:
        topics[str(thread_id)] = name
        storage.save_topics(topics)


def _saved(word, dest):
    """Фрагмент для уведомления Claude: куда сохранён файл, либо почему скачать не удалось."""
    return f"{word} в `{dest}`" if dest else f"НЕ СКАЧАН ({mediaio.last_download_error() or 'ошибка загрузки'})"


def _async_media(label, size, download, build_notify, chat_id, thread_id, topic_id, reply_kb, reply_to):
    """Скачивание вложения в отдельном потоке — иначе большой файл (локальный Bot API сначала целиком
    скачивает его у себя, минуты) блокирует опрос Telegram, и бот «зависает» для всех сообщений.
    Для файлов > 20 МБ показывает статус «Скачиваю…». Успех — уведомление Claude (build_notify(dest)),
    неудача — сообщение с причиной пользователю."""
    def work():
        status = None
        if size and size > mediaio.BOT_API_DOWNLOAD_LIMIT:
            status = tgapi.send_single(f"⏬ Скачиваю {label} ({size / 1048576:.0f} МБ), это может занять минуты…",
                                       chat_id=chat_id, thread_id=thread_id, attach_keyboard=False)
        dest = download()
        if status:
            tgapi.delete_message(status, chat_id=chat_id)
        if not dest:
            size_note = f" ({size / 1048576:.1f} МБ)" if size else ""
            tgapi.send_message(f"❌ Не удалось скачать {label}{size_note}: {mediaio.last_download_error()}",
                               reply_kb, chat_id=chat_id, thread_id=thread_id, reply_to=reply_to)
            return
        claude_run.run_claude_turn(build_notify(dest), chat_id=chat_id, thread_id=thread_id, topic_id=topic_id,
                                   telegram_reply_to=reply_to, reply_markup=reply_kb)
    threading.Thread(target=work, daemon=True).start()


def handle_forum_topic_created(msg):
    """Обрабатывает создание новой темы в форуме-группе."""
    topic_info = msg.get("forum_topic_created", {})
    topic_name = topic_info.get("name", "Без названия")
    chat = msg.get("chat", {})
    chat_id = chat.get("id")
    thread_id = msg.get("message_id")  # message_id сервисного сообщения = thread_id темы

    if chat_id != GROUP_CHAT_ID or not thread_id:
        return

    _remember_topic_name(thread_id, topic_name)

    topic_id = f"group_{thread_id}"
    hfile = storage.get_history_file(topic_id)
    if not os.path.exists(hfile):
        with open(hfile, "w") as f:
            f.write("")  # создаём пустой файл истории
        print(f"[TOPIC_CREATED] Создан файл истории для темы «{topic_name}» (thread_id={thread_id}): {hfile}")
    else:
        print(f"[TOPIC_CREATED] Файл истории для темы «{topic_name}» (thread_id={thread_id}) уже существует")

    creator = msg.get("from", {})
    creator_name = creator.get("first_name") or creator.get("username") or "кто-то"
    ts = core.now_local().strftime("%d.%m.%Y %H:%M")
    tgapi.send_message(
        f"🗂 Тема *{topic_name}* создана. История разговора начата.",
        chat_id=GROUP_CHAT_ID,
        thread_id=thread_id
    )


def handle_edited_message(msg):
    """Реагирует на отредактированное сообщение, только если это ПОСЛЕДНЕЕ сообщение в теме/чате —
    запускает обычную обработку с пометкой, что сообщение было изменено."""
    if msg.get("from", {}).get("is_bot", False):
        return
    from_id = msg.get("from", {}).get("id")
    source_chat = msg.get("chat", {})
    source_chat_id = source_chat.get("id")
    if from_id != ALLOWED_USER_ID and source_chat_id != GROUP_CHAT_ID:
        return

    msg_thread_id = msg.get("message_thread_id")
    edited_msg_id = msg.get("message_id")
    if not edited_msg_id:
        return

    if source_chat_id == GROUP_CHAT_ID:
        topic_id = f"group_{msg_thread_id}" if msg_thread_id else "group_general"
    else:
        topic_id = claude_run.chat_thread_to_topic_id(source_chat_id, None)
    key = str(topic_id)

    last_ids = tgapi.load_last_message_ids()
    if last_ids.get(key) != edited_msg_id:
        return  # отредактировано не последнее сообщение — игнорируем

    new_text = msg.get("text") or msg.get("caption") or ""
    if not new_text:
        return

    note = "[Системная информация: пользователь отредактировал своё последнее сообщение]\n"
    synthetic = dict(msg)
    if "caption" in synthetic:
        synthetic["caption"] = note + new_text
    else:
        synthetic["text"] = note + new_text
    handle_message(synthetic)


def handle_message(msg):

    # Обрабатываем создание темы до проверки прав
    if "forum_topic_created" in msg:
        handle_forum_topic_created(msg)
        return

    # Переименование темы — единственный способ узнать её новое название (Bot API не отдаёт имя темы по id)
    if "forum_topic_edited" in msg:
        if msg.get("chat", {}).get("id") == GROUP_CHAT_ID:
            _remember_topic_name(msg.get("message_thread_id"), msg["forum_topic_edited"].get("name"))
        return

    # Пропускаем сервисные уведомления (закреп, вступление, выход и т.д.)
    service_fields = ("pinned_message", "new_chat_members", "left_chat_member",
                      "new_chat_title", "new_chat_photo", "delete_chat_photo",
                      "group_chat_created", "supergroup_chat_created",
                      "migrate_to_chat_id", "migrate_from_chat_id",
                      "forum_topic_closed", "forum_topic_reopened", "forum_topic_edited",
                      "general_forum_topic_hidden", "general_forum_topic_unhidden")
    if any(f in msg for f in service_fields):
        return

    from_id = msg.get("from", {}).get("id")
    source_chat_early = msg.get("chat", {})
    source_chat_id_early = source_chat_early.get("id")

    # Игнорируем сообщения от ботов (включая самого себя)
    if msg.get("from", {}).get("is_bot", False):
        return

    # Разрешаем сообщения от всех в групповом чате,
    # но в личке — только от владельца
    if from_id != ALLOWED_USER_ID and source_chat_id_early != GROUP_CHAT_ID:
        return

    # Сообщение в теме обычно «отвечает» на служебное сообщение создания темы — из него берём название
    # (так узнаются темы, созданные до того, как бот начал запоминать названия)
    _topic_start = msg.get("reply_to_message") or {}
    if source_chat_id_early == GROUP_CHAT_ID and "forum_topic_created" in _topic_start:
        _remember_topic_name(_topic_start.get("message_id"), _topic_start["forum_topic_created"].get("name"))

    # Отслеживаем входящее сообщение для очистки чата
    if "message_id" in msg:
        tgapi.track_chat_message(msg["message_id"])

    text = msg.get("text", "")
    original_text = text  # без системной информации — для сохранения в историю
    photo = msg.get("photo")
    caption = msg.get("caption", "")
    document = msg.get("document")
    video = msg.get("video")
    audio = msg.get("audio")
    voice = msg.get("voice")
    video_note = msg.get("video_note")
    sticker = msg.get("sticker")
    animation = msg.get("animation")
    poll_in_msg = msg.get("poll")

    # Контекст чата (группа/канал) + определяем тему группы
    source_chat = msg.get("chat", {})
    source_chat_id = source_chat.get("id")
    source_chat_type = source_chat.get("type", "private")
    msg_thread_id = msg.get("message_thread_id")  # ID темы в форуме
    incoming_msg_id = msg.get("message_id")  # ID входящего сообщения для reply

    # Определяем цель ответа и topic_id для истории
    if source_chat_id == GROUP_CHAT_ID:
        tgt_chat_id = GROUP_CHAT_ID
        tgt_thread_id = msg_thread_id  # None = общая тема
        topic_id = f"group_{msg_thread_id}" if msg_thread_id else "group_general"
        reply_kb = keyboards.get_main_keyboard(topic_id=topic_id, selective=True) if from_id == ALLOWED_USER_ID else None
        tgt_reply_to = incoming_msg_id
    else:
        # Личка (и любой другой чат владельца) — отдельная «тема»: отвечаем в тот же чат,
        # у неё своя сессия --resume и своя история (topic_id "dm" / "chat_<id>")
        tgt_chat_id = source_chat_id
        tgt_thread_id = None
        topic_id = claude_run.chat_thread_to_topic_id(source_chat_id, None)
        reply_kb = keyboards.get_main_keyboard(topic_id=topic_id, selective=True) if from_id == ALLOWED_USER_ID else None
        tgt_reply_to = None

    # Команда остановки текущего выполнения Claude в этой теме/чате.
    # Не трекаем её как "последнее сообщение" — иначе правка реального последнего
    # сообщения (того, что обрабатывалось) перестаёт совпадать с last_ids и игнорируется.
    stop_cmd = original_text.strip().split("@")[0].lower() if original_text else ""
    if stop_cmd in ("/stop", "!стоп"):
        stopped = procs.stop_proc(procs.stream_key(topic_id))
        reply = "🛑 Остановил." if stopped else "Сейчас ничего не выполняется."
        tgapi.send_single(reply, chat_id=tgt_chat_id, thread_id=tgt_thread_id, reply_to=tgt_reply_to)
        return

    # Отменяет любой режим ожидания ввода (память/напоминание/создание темы),
    # запущенный нажатием кнопки — без этого единственный выход был прислать
    # что-то, что бот молча запишет как содержимое этого ожидания.
    if stop_cmd in ("/cancel", "!отмена"):
        cancelled = []
        if shared.pending_memory_add:
            cancelled.append("запись в память")
            shared.pending_memory_add = None
        if shared.pending_memory_edit:
            cancelled.append("редактирование записи памяти")
            shared.pending_memory_edit = None
        if shared.pending_memory_append:
            cancelled.append("дозапись в файл памяти")
            shared.pending_memory_append = None
        if shared.pending_reminder:
            cancelled.append("напоминание")
            shared.pending_reminder = None
            shared.pending_reminder_data = {}
        if shared.pending_create_topic:
            cancelled.append("создание темы")
            shared.pending_create_topic = None
        if authlogin.cancel():
            cancelled.append("вход в Claude")
        reply = f"❌ Отменено: {', '.join(cancelled)}." if cancelled else "Нечего отменять — нет ожидающего ввода."
        tgapi.send_single(reply, chat_id=tgt_chat_id, thread_id=tgt_thread_id, reply_to=tgt_reply_to)
        return

    # Ждём код перелогина Claude: следующее сообщение владельца в этом чате — это код из браузера.
    # Проверка раньше всего остального, чтобы код не попал в историю, лог и Claude.
    if from_id == ALLOWED_USER_ID and authlogin.handle_incoming(tgt_chat_id, tgt_thread_id, incoming_msg_id, original_text):
        return

    # В теме Gemma Arena, пока арена запущена, сообщения владельца выполняются как команды
    # в отдельной пользовательской Docker-песочнице вместо обычного вызова Claude.
    if (from_id == ALLOWED_USER_ID and msg_thread_id == consts.GEMMA_ARENA_THREAD_ID
            and original_text and original_text.strip() and sysinfo.gemma_arena_running()):
        cmd = original_text.strip()
        def run_gemma_cmd(_cid=tgt_chat_id, _tid=tgt_thread_id, _reply_to=tgt_reply_to):
            result = sysinfo.run_in_gemma_sandbox(cmd)
            display = f"$ {cmd}\n\n{result}"
            if len(display) > 3800:
                display = display[:3800] + "\n...(обрезано)"
            payload = {"chat_id": _cid, "text": display, "reply_to_message_id": _reply_to}
            if _tid is not None:
                payload["message_thread_id"] = _tid
            requests.post(f"{core.API}/sendMessage", json=payload)
        threading.Thread(target=run_gemma_cmd, daemon=True).start()
        return

    if incoming_msg_id:
        tgapi.track_last_message(topic_id, incoming_msg_id)

    if source_chat_type != "private":
        sender_info = msg.get("from", {})
        sender_uname = sender_info.get("username", "")
        sender_fname = sender_info.get("first_name", "")
        sender_display = f"@{sender_uname}" if sender_uname else sender_fname or f"id:{from_id}"
        if msg_thread_id:
            topics = storage.load_topics()
            topic_name = topics.get(str(msg_thread_id), str(msg_thread_id))
            thread_info = f", тема: «{topic_name}»"
        else:
            thread_info = ""
        chat_note = f"\n\n[Системная информация: сообщение от {sender_display}{thread_info}]"
        text = text + chat_note if text else chat_note

    # Если в bot.log есть хотя бы одна ошибка — не ждать пока владелец сам заметит и
    # пожалуется, а пометить это в системной информации на каждом ходу, независимо
    # от типа чата, пока лог не почистят/причину не исправят.
    err_count = sysinfo.count_log_errors()
    if err_count >= 1:
        last_err = sysinfo._log_error_cache["last"][:300]
        log_note = f"\n\n[Системная информация: в bot.log ошибок: {err_count}. Последняя: {last_err}]"
        text = text + log_note if text else log_note

    # Добавляем пометку об отправителе если это не владелец
    if from_id != ALLOWED_USER_ID:
        sender = msg.get("from", {})
        sender_name = f"{sender.get('first_name', '')} {sender.get('last_name', '')}".strip()
        sender_username = sender.get("username", "")
        sender_label = f"@{sender_username}" if sender_username else sender_name or f"id:{from_id}"
        sender_note = f"\n\n[Системная информация: это сообщение от другого участника группы — {sender_label} (user_id: `{from_id}`). Обращайся к нему по имени.]"
        text = text + sender_note if text else sender_note

    # Контекст пересланного сообщения
    forward_chat = msg.get("forward_from_chat")
    forward_origin = msg.get("forward_origin")
    if forward_chat:
        fwd_id = forward_chat.get("id", "")
        fwd_title = forward_chat.get("title") or forward_chat.get("username") or ""
        fwd_type = forward_chat.get("type", "")
        fwd_note = f"\n\n[Системная информация: это пересланное сообщение из чата «{fwd_title}» (тип: {fwd_type}, chat_id: `{fwd_id}`)]"
        text = text + fwd_note if text else fwd_note
    elif forward_origin:
        origin_type = forward_origin.get("type", "")
        origin_chat = forward_origin.get("chat")
        if origin_chat:
            fwd_id = origin_chat.get("id", "")
            fwd_title = origin_chat.get("title") or origin_chat.get("username") or ""
            fwd_type = origin_chat.get("type", "")
            fwd_note = f"\n\n[Системная информация: это пересланное сообщение из чата «{fwd_title}» (тип: {fwd_type}, chat_id: `{fwd_id}`)]"
            text = text + fwd_note if text else fwd_note
        elif origin_type == "user":
            origin_user = forward_origin.get("sender_user") or {}
            fwd_name = f"{origin_user.get('first_name', '')} {origin_user.get('last_name', '')}".strip()
            fwd_uid = origin_user.get("id", "")
            fwd_note = f"\n\n[Системная информация: это пересланное сообщение от пользователя {fwd_name} (user_id: `{fwd_uid}`)]"
            text = text + fwd_note if text else fwd_note
        elif origin_type == "hidden_user":
            sender_name = forward_origin.get("sender_user_name", "скрыт")
            fwd_note = f"\n\n[Системная информация: это пересланное сообщение от пользователя «{sender_name}» (скрытый профиль)]"
            text = text + fwd_note if text else fwd_note

    # Контекст ответа на сообщение
    reply_to = None
    replied = msg.get("reply_to_message")
    if replied:
        replied_text = replied.get("text") or replied.get("caption", "")
        if replied.get("photo") and not replied_text:
            replied_text = "[фото]"
        if replied_text:
            reply_to = replied_text[:500]  # ограничиваем длину

    # Альбом — несколько вложений одним логическим сообщением, буферизуем и ждём
    # остальные части вместо немедленной обработки каждой по отдельности.
    media_group_id = msg.get("media_group_id")
    if media_group_id and (document or photo or video or audio or animation):
        if document:
            item = {"type": "document", "file_id": document["file_id"],
                    "file_name": document.get("file_name", f"file_{document['file_id'][:8]}")}
        elif photo:
            item = {"type": "photo", "file_id": photo[-1]["file_id"]}
        elif video:
            item = {"type": "video", "file_id": video["file_id"]}
        elif audio:
            item = {"type": "audio", "file_id": audio["file_id"],
                    "file_name": audio.get("file_name", f"audio_{audio['file_id'][:8]}")}
        else:
            item = {"type": "animation", "file_id": animation["file_id"],
                    "file_name": animation.get("file_name", f"animation_{animation['file_id'][:8]}")}
        if caption:
            item["caption"] = caption
        ctx = {"tgt_chat_id": tgt_chat_id, "tgt_thread_id": tgt_thread_id, "topic_id": topic_id,
               "reply_kb": reply_kb, "tgt_reply_to": tgt_reply_to, "caption": caption or None}
        mediaio.handle_media_group_item(media_group_id, item, ctx)
        return

    # Документ — сохраняем в inbox
    if document:
        file_id = document["file_id"]
        file_name = document.get("file_name", f"file_{document['file_id'][:8]}")
        caption_note = f" Подпись к файлу: «{caption}»." if caption else ""
        _async_media("файл", document.get("file_size"), lambda: mediaio.download_document(file_id, file_name),
                     lambda d: f"[Системное уведомление] Пользователь прислал файл-документ, {_saved('сохранён', d)}.{caption_note} Сообщи ему об этом.",
                     tgt_chat_id, tgt_thread_id, topic_id, reply_kb, tgt_reply_to)
        return

    # Видео
    if video:
        file_id = video["file_id"]
        caption_text = caption or ""
        _async_media("видео", video.get("file_size"), lambda: mediaio.download_media(file_id, "video", ".mp4"),
                     lambda d: f"[Системное уведомление] Пользователь прислал видео, {_saved('сохранено', d)}. Подпись: «{caption_text}». Сообщи ему.",
                     tgt_chat_id, tgt_thread_id, topic_id, reply_kb, tgt_reply_to)
        return

    # Аудио
    if audio:
        file_id = audio["file_id"]
        fname = audio.get("file_name", f"audio_{audio['file_id'][:8]}")
        caption_note = f" Подпись к файлу: «{caption}»." if caption else ""
        _async_media("аудио-файл", audio.get("file_size"), lambda: mediaio.download_document(file_id, fname),
                     lambda d: f"[Системное уведомление] Пользователь прислал аудио-файл, {_saved('сохранён', d)}.{caption_note} Сообщи ему.",
                     tgt_chat_id, tgt_thread_id, topic_id, reply_kb, tgt_reply_to)
        return

    # Голосовое сообщение — транскрибируем локально через whisper.cpp
    if voice:
        file_id = voice["file_id"]
        def _run_voice(_cid=tgt_chat_id, _tid=tgt_thread_id, _kb=reply_kb, _topic=topic_id,
                       _rt=tgt_reply_to, _src_chat=source_chat_id, _inc_msg=incoming_msg_id, _reply_to=reply_to):
            # attach_keyboard=False: сообщение с reply-клавиатурой Telegram не даёт отредактировать,
            # а «Слушаю…» дальше заменяется результатом распознавания
            listening_id = tgapi.send_single("🎤 Слушаю...", chat_id=_cid, thread_id=_tid, attach_keyboard=False)
            dest = mediaio.download_media(file_id, "voice", ".ogg")
            transcribed = mediaio.transcribe_voice(dest) if dest else None
            if not transcribed:
                if listening_id:
                    why = "" if dest else f" Не скачалось: {mediaio.last_download_error()}"
                    tgapi.edit_message(listening_id, f"❌ Не удалось распознать голосовое сообщение.{why}", chat_id=_cid)
                return
            if listening_id:
                tgapi.edit_message(listening_id, f"🎤 _{transcribed}_", chat_id=_cid)
            voice_prompt = f"[Голосовое сообщение, распознано через whisper]\n{transcribed}"
            claude_run.run_claude_turn(voice_prompt, chat_id=_cid, thread_id=_tid, topic_id=_topic,
                                       ask_reply_to=_reply_to, telegram_reply_to=_rt, reply_markup=_kb,
                                       react_chat_id=_src_chat, react_msg_id=_inc_msg,
                                       save_user_history=f"[голос] {transcribed}", history_topic=_topic)
        threading.Thread(target=_run_voice, daemon=True).start()
        return

    # Кружочек (видео-сообщение)
    if video_note:
        file_id = video_note["file_id"]
        dest = mediaio.download_media(file_id, "videonote", ".mp4")
        notify = f"[Системное уведомление] Пользователь прислал видео-кружочек, {_saved('сохранён', dest)}. Сообщи ему."
        def _run_vn(n=notify, _cid=tgt_chat_id, _tid=tgt_thread_id, _kb=reply_kb, _topic=topic_id):
            claude_run.run_claude_turn(n, chat_id=_cid, thread_id=_tid, topic_id=_topic, reply_markup=_kb)
        threading.Thread(target=_run_vn, daemon=True).start()
        return

    # Стикер
    if sticker:
        file_id = sticker["file_id"]
        emoji = sticker.get("emoji", "")
        dest = mediaio.download_media(file_id, "sticker", ".webp")
        notify = f"[Системное уведомление] Пользователь прислал стикер {emoji}, {_saved('сохранён', dest)}. Сообщи ему."
        def _run_sticker(n=notify, _cid=tgt_chat_id, _tid=tgt_thread_id, _kb=reply_kb, _topic=topic_id):
            claude_run.run_claude_turn(n, chat_id=_cid, thread_id=_tid, topic_id=_topic, reply_markup=_kb)
        threading.Thread(target=_run_sticker, daemon=True).start()
        return

    # Анимация (GIF)
    if animation:
        file_id = animation["file_id"]
        fname = animation.get("file_name", f"animation_{animation['file_id'][:8]}")
        caption_note = f" Подпись: «{caption}»." if caption else ""
        _async_media("анимацию", animation.get("file_size"), lambda: mediaio.download_document(file_id, fname),
                     lambda d: f"[Системное уведомление] Пользователь прислал анимацию/GIF, {_saved('сохранена', d)}.{caption_note} Сообщи ему.",
                     tgt_chat_id, tgt_thread_id, topic_id, reply_kb, tgt_reply_to)
        return

    # Опрос в сообщении — кэшируем и реагируем
    if poll_in_msg:
        poll_id = poll_in_msg.get("id", "")
        question = poll_in_msg.get("question", "?")
        options = poll_in_msg.get("options", [])
        poll_type = poll_in_msg.get("type", "regular")
        allows_multiple = poll_in_msg.get("allows_multiple_answers", False)

        if poll_id:
            tgapi._poll_cache[poll_id] = {
                "question": question,
                "options": [o.get("text", "") for o in options],
                "message_id": msg.get("message_id"),
                "chat_id": source_chat_id,
                "thread_id": msg_thread_id,
            }

        return

    # Фото — скачиваем и анализируем
    if photo:
        file_id = photo[-1]["file_id"]  # берём максимальное разрешение
        user_text = caption if caption else "Что на этом изображении?"

        def run_claude_photo(_cid=tgt_chat_id, _tid=tgt_thread_id, _kb=reply_kb, _topic=topic_id,
                             _src_chat=source_chat_id, _inc_msg=incoming_msg_id):
            image_path = mediaio.download_photo(file_id)
            if image_path:
                tgapi.send_single(f"📥 Фото сохранено: `{image_path}`", _kb, chat_id=_cid, thread_id=_tid)
            claude_run.run_claude_turn(user_text, chat_id=_cid, thread_id=_tid, topic_id=_topic,
                             ask_reply_to=reply_to, image_path=image_path, reply_markup=_kb,
                             react_chat_id=_src_chat, react_msg_id=_inc_msg,
                             save_user_history=f"[фото] {user_text}", history_topic=_topic)

        threading.Thread(target=run_claude_photo, daemon=True).start()
        return

    # Неизвестный тип — определяем и сообщаем Claude
    known_types = {"text", "photo", "document", "video", "audio", "voice", "video_note", "sticker", "animation", "caption"}
    unknown_fields = [
        k for k in msg.keys()
        if k not in {"message_id", "from", "chat", "date", "reply_to_message",
                     "text", "photo", "document", "caption", "entities",
                     "media_group_id", "forward_from", "forward_date",
                     "forward_from_chat", "forward_from_message_id", "forward_origin",
                     "reply_markup", "via_bot", "video", "audio", "voice",
                     "video_note", "sticker", "animation", "message_thread_id",
                     "is_topic_message"}
    ]
    if not text and not photo and not document and not video and not audio and not voice and not video_note and not sticker and not animation and unknown_fields:
        msg_types = ", ".join(f"`{f}`" for f in unknown_fields)
        notify = f"[Системное уведомление] Пользователь прислал сообщение неизвестного типа: {msg_types}. Ответь пользователю, что ты получил сообщение такого типа, но пока не умеешь его обрабатывать."
        def run_unknown(_cid=tgt_chat_id, _tid=tgt_thread_id, _kb=reply_kb, _topic=topic_id):
            claude_run.run_claude_turn(notify, chat_id=_cid, thread_id=_tid, topic_id=_topic, reply_markup=_kb)
        threading.Thread(target=run_unknown, daemon=True).start()
        return

    if not original_text or original_text.startswith("/start"):
        tgapi.send_message("Привет! Я на связи, пиши мне.", reply_kb, chat_id=tgt_chat_id, thread_id=tgt_thread_id, reply_to=tgt_reply_to)
        return

    if original_text == "⏰ Напоминание":
        shared.pending_reminder = "new"
        shared.pending_memory_add = None
        tgapi.send_message(
            "⏰ Введи напоминание:\n`17:00 текст` — по времени суток\n`30м текст` — через 30 минут\n`2ч 15м текст` — через 2 ч 15 мин\n`1д текст` — через день\n\nПовторяющееся: `каждые 1ч текст`",
            reply_kb, chat_id=tgt_chat_id, thread_id=tgt_thread_id
        )
        return

    # Если ожидаем ввод для редактирования записи памяти
    if shared.pending_memory_edit:
        if from_id != ALLOWED_USER_ID:
            return  # только владелец может редактировать память
        fname = shared.pending_memory_edit
        shared.pending_memory_edit = None
        fpath = os.path.join(consts.MEMORY_DIR, fname)
        try:
            with open(fpath, "r") as f:
                raw = f.read()
            # Обновляем только тело (после frontmatter)
            if raw.strip().startswith("---"):
                parts = raw.strip().split("---", 2)
                frontmatter = "---" + parts[1] + "---\n\n"
            else:
                frontmatter = ""
            now = core.now_local().strftime("%Y-%m-%d")
            new_content = frontmatter + original_text + f"\n\n_(Обновлено через Telegram {now})_\n"
            with open(fpath, "w") as f:
                f.write(new_content)
            tgapi.send_message(f"✅ Запись обновлена:\n`{original_text[:100]}`", reply_kb, chat_id=tgt_chat_id, thread_id=tgt_thread_id, reply_to=tgt_reply_to)
        except Exception as e:
            tgapi.send_message(f"❌ Ошибка при сохранении: {e}", reply_kb, chat_id=tgt_chat_id, thread_id=tgt_thread_id, reply_to=tgt_reply_to)
        return

    # Если ожидаем ввод для дозаписи в существующий файл памяти
    if shared.pending_memory_append:
        if from_id != ALLOWED_USER_ID:
            return  # только владелец может дописывать в память
        fname = shared.pending_memory_append
        shared.pending_memory_append = None
        fpath = os.path.join(consts.MEMORY_DIR, fname)
        try:
            with open(fpath, "r") as f:
                raw = f.read()
        except Exception:
            raw = ""
        now = core.now_local().strftime("%Y-%m-%d")
        new_content = raw.rstrip() + f"\n\n{original_text}\n\n_(Дополнено через Telegram {now})_\n"
        with open(fpath, "w") as f:
            f.write(new_content)
        tgapi.send_message(f"✅ Дописано в `{fname}`:\n`{original_text[:100]}`", reply_kb, chat_id=tgt_chat_id, thread_id=tgt_thread_id, reply_to=tgt_reply_to)
        return

    # Если ожидаем ввод для памяти
    if shared.pending_memory_add:
        if from_id != ALLOWED_USER_ID:
            return  # только владелец может добавлять в память
        shared.pending_memory_add = None
        fpath = memui.save_memory(original_text)
        tgapi.send_message(f"✅ Запомнил:\n`{original_text[:100]}`", reply_kb, chat_id=tgt_chat_id, thread_id=tgt_thread_id, reply_to=tgt_reply_to)
        return

    # Если ожидаем ввод для напоминания
    if shared.pending_reminder == "new":
        shared.pending_reminder = None
        # Используем original_text чтобы избежать системных заметок с переносами строк
        reminder_input = original_text.strip()
        # Проверяем формат "каждые Xч/м/д текст"
        m_repeat = re.match(r'^каждые?\s+(.+)', reminder_input, re.IGNORECASE)
        if m_repeat:
            parsed = reminders.parse_reminder(m_repeat.group(1))
            if parsed is None:
                tgapi.send_message("❌ Не понял формат. Пример: `каждые 1ч Тесла`", reply_kb, chat_id=tgt_chat_id, thread_id=tgt_thread_id, reply_to=tgt_reply_to)
            else:
                repeat_secs, reminder_text = parsed
                reminders.schedule_reminder(repeat_secs, reminder_text, repeat_secs=repeat_secs, chat_id=tgt_chat_id, topic_id=tgt_thread_id)
                dur = reminders.format_duration(repeat_secs)
                tgapi.send_message(f"✅ Повторяющееся напоминание каждые *{dur}*:\n_{reminder_text}_", reply_kb, chat_id=tgt_chat_id, thread_id=tgt_thread_id, reply_to=tgt_reply_to)
        else:
            parsed = reminders.parse_reminder(reminder_input)
            if parsed is None:
                tgapi.send_message(
                    "❌ Не понял формат. Пример: `30м Тесла` или `каждые 2ч текст`",
                    reply_kb, chat_id=tgt_chat_id, thread_id=tgt_thread_id
                )
            else:
                secs, reminder_text = parsed
                reminders.schedule_reminder(secs, reminder_text, chat_id=tgt_chat_id, topic_id=tgt_thread_id)
                dur = reminders.format_duration(secs)
                tgapi.send_message(f"✅ Напоминание через *{dur}*:\n_{reminder_text}_", reply_kb, chat_id=tgt_chat_id, thread_id=tgt_thread_id, reply_to=tgt_reply_to)
        return

    # Если ожидаем название новой темы
    if shared.pending_create_topic:
        shared.pending_create_topic = None
        topic_name = original_text.strip()
        if not topic_name:
            tgapi.send_message("❌ Название не может быть пустым.", reply_kb, chat_id=tgt_chat_id, thread_id=tgt_thread_id)
            return
        resp = requests.post(f"{core.API}/createForumTopic", json={
            "chat_id": GROUP_CHAT_ID,
            "name": topic_name,
        })
        if resp.ok and resp.json().get("ok"):
            tgapi.send_message(f"✅ Тема *{topic_name}* создана!", reply_kb, chat_id=tgt_chat_id, thread_id=tgt_thread_id)
        else:
            err = resp.json().get("description", "неизвестная ошибка")
            tgapi.send_message(f"❌ Не удалось создать тему: {err}", reply_kb, chat_id=tgt_chat_id, thread_id=tgt_thread_id)
        return

    if original_text == "📤 Резервный":
        storage.set_bot_mode(topic_id, "passthrough")
        fresh_kb = keyboards.get_main_keyboard(topic_id=topic_id, selective=True)
        tgapi.send_message("📤 Резервный режим включён. Сообщения идут в Claude без обработки команд бота.", fresh_kb, chat_id=tgt_chat_id, thread_id=tgt_thread_id, reply_to=tgt_reply_to)
        return

    if original_text == "🤖 Claude":
        storage.set_bot_mode(topic_id, "claude")
        fresh_kb = keyboards.get_main_keyboard(topic_id=topic_id, selective=True)
        tgapi.send_message("🤖 Режим Claude включён.", fresh_kb, chat_id=tgt_chat_id, thread_id=tgt_thread_id, reply_to=tgt_reply_to)
        return

    if original_text == "🖥 Команды":
        storage.set_bot_mode(topic_id, "shell")
        shared.shell_cwd = consts.HOME
        fresh_kb = keyboards.get_main_keyboard(topic_id=topic_id, selective=True)
        tgapi.send_message(
            f"🖥 Командный режим включён.\n📁 `{shared.shell_cwd}`\n\nВводи любые bash-команды. Директория сохраняется между командами.\nДля выхода — кнопка «🤖 Claude».",
            fresh_kb, chat_id=tgt_chat_id, thread_id=tgt_thread_id
        )
        return

    if original_text == "📝 Память":
        def _send_picker(_cid=tgt_chat_id, _tid=tgt_thread_id, _rt=tgt_reply_to):
            tgapi.send_message("В какой файл писать?", memui.get_memory_pick_inline(), chat_id=_cid, thread_id=_tid, reply_to=_rt)
        threading.Thread(target=_send_picker, daemon=True).start()
        return

    if original_text == "📋 Мои записи":
        def _send_records(_cid=tgt_chat_id, _tid=tgt_thread_id):
            memui.send_memory_items(chat_id=_cid, thread_id=_tid)
        threading.Thread(target=_send_records, daemon=True).start()
        return

    if original_text == "➕ Создать тему":
        shared.pending_create_topic = True
        tgapi.send_message("➕ Введи название новой темы:", reply_kb, chat_id=tgt_chat_id, thread_id=tgt_thread_id, reply_to=tgt_reply_to)
        return

    if original_text == "🏓 Пинг":
        tgapi.send_message("Понг!", reply_kb, chat_id=tgt_chat_id, thread_id=tgt_thread_id, reply_to=tgt_reply_to)
        return

    if original_text == "⏱ Лимиты":
        def _run_limits(_cid=tgt_chat_id, _tid=tgt_thread_id, _kb=reply_kb, _rt=tgt_reply_to):
            tgapi.send_message(sysinfo.get_usage_text(), _kb, chat_id=_cid, thread_id=_tid, reply_to=_rt)
        threading.Thread(target=_run_limits, daemon=True).start()
        return

    if original_text == "🔄 Сброс":
        tgapi.send_message("Бот сброшен", reply_kb, chat_id=tgt_chat_id, thread_id=tgt_thread_id, reply_to=tgt_reply_to)
        claude_run.trigger_restart(tgt_chat_id, tgt_thread_id)
        return

    if original_text == "♟ Шахматы":
        chess_key = (tgt_chat_id, tgt_thread_id)
        chess_game = {
            'board': chessgame.chess_initial_board(),
            'turn': 'W',
            'selected': None,
            'valid_moves': [],
            'game_over': False,
            'status': '',
            'chat_id': tgt_chat_id,
            'moves': [],
        }
        kb = chessgame.chess_render_keyboard(chess_game)
        mid = tgapi.send_single(chessgame.chess_status_text(chess_game), kb, chat_id=tgt_chat_id, thread_id=tgt_thread_id)
        chess_game['msg_id'] = mid
        chessgame.chess_games[chess_key] = chess_game
        return

    if original_text == "🌀 Вентилятор":
        menu_key = (tgt_chat_id, tgt_thread_id)
        status = sysinfo.fan_status()
        existing = shared.fan_msg_ids.get(menu_key)
        if existing:
            r = requests.post(f"{core.API}/editMessageText", json={
                "chat_id": tgt_chat_id,
                "message_id": existing,
                "text": f"🌀 Вентилятор: {status}",
                "reply_markup": keyboards.FAN_INLINE
            })
            if not r.json().get("ok"):
                shared.fan_msg_ids[menu_key] = tgapi.send_message(f"🌀 Вентилятор: {status}", keyboards.FAN_INLINE, chat_id=tgt_chat_id, thread_id=tgt_thread_id)
        else:
            shared.fan_msg_ids[menu_key] = tgapi.send_message(f"🌀 Вентилятор: {status}", keyboards.FAN_INLINE, chat_id=tgt_chat_id, thread_id=tgt_thread_id)
        return

    if original_text == "🔌 Питание":
        menu_key = (tgt_chat_id, tgt_thread_id)
        existing = shared.power_msg_ids.get(menu_key)
        if existing:
            r = requests.post(f"{core.API}/editMessageText", json={
                "chat_id": tgt_chat_id,
                "message_id": existing,
                "text": "🔌 Управление питанием:",
                "reply_markup": keyboards.POWER_INLINE
            })
            if not r.json().get("ok"):
                shared.power_msg_ids[menu_key] = tgapi.send_message("🔌 Управление питанием:", keyboards.POWER_INLINE, chat_id=tgt_chat_id, thread_id=tgt_thread_id)
        else:
            shared.power_msg_ids[menu_key] = tgapi.send_message("🔌 Управление питанием:", keyboards.POWER_INLINE, chat_id=tgt_chat_id, thread_id=tgt_thread_id)
        return

    if original_text == "📊 Инфо":
        menu_key = (tgt_chat_id, tgt_thread_id)
        def _send_info(_key=menu_key):
            info_text = sysinfo.get_info_text()
            existing = shared.info_msg_ids.get(_key)
            if existing:
                try:
                    r = requests.post(f"{core.API}/editMessageText", json={
                        "chat_id": tgt_chat_id,
                        "message_id": existing,
                        "text": mdfmt.markdown_to_html(info_text),
                        "parse_mode": "HTML",
                        "reply_markup": keyboards.INFO_INLINE
                    }, timeout=10)
                    if not r.json().get("ok"):
                        shared.info_msg_ids[_key] = tgapi.send_single(info_text, keyboards.INFO_INLINE, chat_id=tgt_chat_id, thread_id=tgt_thread_id)
                except Exception:
                    shared.info_msg_ids[_key] = tgapi.send_single(info_text, keyboards.INFO_INLINE, chat_id=tgt_chat_id, thread_id=tgt_thread_id)
            else:
                shared.info_msg_ids[_key] = tgapi.send_single(info_text, keyboards.INFO_INLINE, chat_id=tgt_chat_id, thread_id=tgt_thread_id)
        threading.Thread(target=_send_info, daemon=True).start()
        return

    if original_text == "⚙️ Настройки":
        menu_key = (tgt_chat_id, tgt_thread_id)
        settings_text = keyboards.get_settings_text()
        existing = shared.settings_msg_ids.get(menu_key)
        if existing:
            r = requests.post(f"{core.API}/editMessageText", json={
                "chat_id": tgt_chat_id,
                "message_id": existing,
                "text": mdfmt.markdown_to_html(settings_text),
                "parse_mode": "HTML",
                "reply_markup": keyboards.get_settings_inline()
            })
            if not r.json().get("ok"):
                shared.settings_msg_ids[menu_key] = tgapi.send_single(settings_text, keyboards.get_settings_inline(), chat_id=tgt_chat_id, thread_id=tgt_thread_id)
        else:
            shared.settings_msg_ids[menu_key] = tgapi.send_single(settings_text, keyboards.get_settings_inline(), chat_id=tgt_chat_id, thread_id=tgt_thread_id)
        return

    if storage.get_bot_mode(topic_id) == "shell":
        cmd = original_text
        def run_shell(_cid=tgt_chat_id, _tid=tgt_thread_id, _kb=reply_kb):
            result = sysinfo.run_shell_command(cmd)
            # Отправляем без Markdown-разбора чтобы не ломать спецсимволы
            payload = {
                "chat_id": _cid,
                "text": result,
                "reply_markup": _kb
            }
            if _tid is not None:
                payload["message_thread_id"] = _tid
            r = requests.post(f"{core.API}/sendMessage", json=payload)
            d = r.json()
            if d.get("ok"):
                tgapi.cache_sent_message(d["result"]["message_id"], result)
        threading.Thread(target=run_shell, daemon=True).start()
        return

    if storage.get_bot_mode(topic_id) == "passthrough":
        # Передаём сообщение в Claude, но без обработки команд
        def run_passthrough(_cid=tgt_chat_id, _tid=tgt_thread_id, _kb=reply_kb, _topic=topic_id):
            claude_run.run_claude_turn(text, chat_id=_cid, thread_id=_tid, topic_id=_topic, ask_reply_to=reply_to,
                             reply_markup=_kb, save_user_history=original_text, history_topic=_topic)

        threading.Thread(target=run_passthrough, daemon=True).start()
        return

    print(f"[IN] {text}")

    def run_claude(_cid=tgt_chat_id, _tid=tgt_thread_id, _kb=reply_kb, _topic=topic_id,
                   _src_chat=source_chat_id, _inc_msg=incoming_msg_id):
        claude_run.run_claude_turn(text, chat_id=_cid, thread_id=_tid, topic_id=_topic, ask_reply_to=reply_to,
                         reply_markup=_kb, react_chat_id=_src_chat, react_msg_id=_inc_msg,
                         save_user_history=original_text, history_topic=_topic)

    threading.Thread(target=run_claude, daemon=True).start()
