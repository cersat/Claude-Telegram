"""Обработчики опросов и реакций."""

import threading
from secrets_local import ALLOWED_USER_ID, GROUP_CHAT_ID
from bridge import claude_run, tgapi


def handle_poll_update(poll):
    """Обрабатывает обновление опроса (агрегированные голоса)."""
    poll_id = poll.get("id", "")
    question = poll.get("question", "?")
    total = poll.get("total_voter_count", 0)
    options = poll.get("options", [])
    is_closed = poll.get("is_closed", False)

    # Кэшируем данные опроса, сохраняя chat_id/thread_id, если уже были известны
    # (сами обновления опроса их не содержат — только question/options/voter_count)
    prev = tgapi._poll_cache.get(poll_id, {})
    if poll_id:
        tgapi._poll_cache[poll_id] = {"question": question, "options": [o.get("text", "") for o in options],
                                 "chat_id": prev.get("chat_id"), "thread_id": prev.get("thread_id")}

    # Пропускаем событие создания опроса (0 голосов, не закрыт)
    if total == 0 and not is_closed:
        return

    if is_closed:
        status = "завершён"
    elif total > 0:
        status = f"проголосовали ({total})"
    else:
        status = "отзыв голоса"

    lines = [f"[Опрос — {status}] «{question}»"]
    for opt in options:
        pct = int(opt["voter_count"] / total * 10) if total > 0 else 0
        bar = "▓" * pct + "░" * (10 - pct) if total > 0 else "░░░░░░░░░░"
        lines.append(f"  {opt['text']}: {opt['voter_count']} {bar}")

    poll_info = "\n".join(lines)
    poll_chat_id = prev.get("chat_id")
    poll_topic_id = claude_run.chat_thread_to_topic_id(poll_chat_id, prev.get("thread_id")) if poll_chat_id else None

    def run(_cid=poll_chat_id, _tid=prev.get("thread_id"), _topic=poll_topic_id):
        claude_run.run_claude_turn(poll_info, chat_id=_cid, thread_id=_tid, topic_id=_topic, save_user_history=poll_info, history_topic=_topic)

    threading.Thread(target=run, daemon=True).start()


def handle_poll_answer(update):
    """Обрабатывает ответ на неанонимный опрос (конкретный пользователь)."""
    poll_id = update.get("poll_id", "")
    option_ids = update.get("option_ids", [])
    user = update.get("user", {})
    name = user.get("first_name", "Кто-то")
    username = user.get("username", "")
    if username:
        name = f"{name} (@{username})"

    # Получаем данные опроса из кэша
    cached = tgapi._poll_cache.get(poll_id, {})
    question = cached.get("question", "")
    opts = cached.get("options", [])

    if option_ids:
        chosen = [opts[i] for i in option_ids if i < len(opts)] if opts else [str(i+1) for i in option_ids]
        chosen_str = ", ".join(f"«{c}»" for c in chosen)
        if question:
            msg = f"[{name} проголосовал в опросе «{question}», выбрал: {chosen_str}]"
        else:
            msg = f"[{name} проголосовал в опросе, выбранные варианты: {chosen_str}]"
    else:
        if question:
            msg = f"[{name} отозвал голос в опросе «{question}»]"
        else:
            msg = f"[{name} отозвал голос в опросе]"

    poll_chat_id = cached.get("chat_id")
    poll_thread_id = cached.get("thread_id")
    poll_topic_id = claude_run.chat_thread_to_topic_id(poll_chat_id, poll_thread_id) if poll_chat_id else None

    def run(_cid=poll_chat_id, _tid=poll_thread_id, _topic=poll_topic_id):
        claude_run.run_claude_turn(msg, chat_id=_cid, thread_id=_tid, topic_id=_topic, save_user_history=msg, history_topic=_topic)

    threading.Thread(target=run, daemon=True).start()


def handle_poll_option_added(update):
    """Обрабатывает добавление нового варианта в опрос."""
    poll = update.get("poll", {})
    poll_id = poll.get("id", "")
    question = poll.get("question", "?")
    options = poll.get("options", [])
    option_index = update.get("option_index", len(options) - 1)

    # Обновляем кэш, сохраняя chat_id/thread_id (в этом апдейте их нет)
    prev = tgapi._poll_cache.get(poll_id, {})
    if poll_id:
        tgapi._poll_cache[poll_id] = {
            "question": question,
            "options": [o.get("text", "") for o in options],
            "chat_id": prev.get("chat_id"),
            "thread_id": prev.get("thread_id"),
        }

    new_option_text = options[option_index].get("text", "?") if option_index < len(options) else "?"

    user_info = update.get("added_by_user", {})
    name = user_info.get("first_name", "Кто-то")
    username = user_info.get("username", "")
    if username:
        name = f"{name} (@{username})"

    opts_lines = "\n".join(f"  {i+1}. {o.get('text','')}" for i, o in enumerate(options))
    msg = (
        f"[{name} добавил новый вариант в опрос «{question}»]\n"
        f"Новый вариант: «{new_option_text}»\n"
        f"Все варианты теперь:\n{opts_lines}"
    )

    poll_chat_id = prev.get("chat_id")
    poll_thread_id = prev.get("thread_id")
    poll_topic_id = claude_run.chat_thread_to_topic_id(poll_chat_id, poll_thread_id) if poll_chat_id else None

    def run(_cid=poll_chat_id, _tid=poll_thread_id, _topic=poll_topic_id):
        claude_run.run_claude_turn(msg, chat_id=_cid, thread_id=_tid, topic_id=_topic, save_user_history=msg, history_topic=_topic)

    threading.Thread(target=run, daemon=True).start()


def handle_reaction(update):
    """Обрабатывает реакции на сообщения."""
    print(f"[REACTION_RAW] {update}")
    from_id = update.get("user", {}).get("id")
    if from_id != ALLOWED_USER_ID:
        print(f"[REACTION] blocked: from_id={from_id}, expected={ALLOWED_USER_ID}")
        return

    msg_id = update.get("message_id")
    old = [r.get("emoji", "") for r in update.get("old_reaction", []) if r.get("type") == "emoji"]
    new = [r.get("emoji", "") for r in update.get("new_reaction", []) if r.get("type") == "emoji"]

    added = [e for e in new if e not in old]
    removed = [e for e in old if e not in new]

    if not added and not removed:
        return

    msg_snippet = tgapi.sent_messages.get(msg_id, "")
    if msg_snippet:
        msg_context = f" на сообщение «{msg_snippet}»"
    else:
        msg_context = ""

    if added and not removed:
        reaction_text = f"поставил реакцию {''.join(added)}{msg_context}"
    elif removed and not added:
        reaction_text = f"убрал реакцию {''.join(removed)}{msg_context}"
    else:
        reaction_text = f"изменил реакцию с {''.join(removed)} на {''.join(added)}{msg_context}"

    print(f"[REACTION] {reaction_text}")

    # Определяем тему/чат, в которых было отправлено сообщение с реакцией — иначе ответ
    # всегда уходил бы в General
    react_chat_id, react_thread_id = tgapi.message_chat_thread.get(msg_id, (None, None))
    if react_chat_id == GROUP_CHAT_ID:
        react_topic_id = f"group_{react_thread_id}" if react_thread_id else "group_general"
    elif react_chat_id is not None:
        react_topic_id = claude_run.chat_thread_to_topic_id(react_chat_id, None)  # личка / другой чат
    else:
        react_topic_id = "group_general"  # не нашли в кэше — считаем General

    def run_claude_reaction(_topic=react_topic_id, _cid=react_chat_id, _tid=react_thread_id):
        claude_run.run_claude_turn(f"[Пользователь {reaction_text}]", chat_id=_cid, thread_id=_tid, topic_id=_topic,
                         save_user_history=f"[реакция] {reaction_text}", history_topic=_topic)

    threading.Thread(target=run_claude_reaction, daemon=True).start()
