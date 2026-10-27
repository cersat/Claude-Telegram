"""Запуск Claude: стриминг stream-json, ask_claude, run_claude_turn, рестарт."""

import time
import threading
import subprocess
import os
import json
import uuid
from secrets_local import GROUP_CHAT_ID
from bridge import cardview, consts, core, procs, prompting, storage, tgapi


STOPPED_SENTINEL = "🛑 Остановлено пользователем."
AUTH_FAILED = "__auth_failed__"  # run_claude_turn вернул это, если ход не состоялся из-за ошибки входа


def run_claude_streaming(cmd, on_event=None, proc_key=None, extra_env=None):
    """Запускает claude с --output-format stream-json и разбирает поток событий live.
    on_event(kind, payload) вызывается по мере поступления:
      kind="tool_start", payload={"id", "label", "full"}       — начался вызов инструмента;
                                                                   "full" — необрезанный текст,
                                                                   если label был обрезан, иначе None
      kind="tool_done",  payload={"id", "ok", "stopped", "result_text", "name"} — инструмент отработал;
                                                                   "result_text" — что он вернул
      kind="text",       payload=str                           — промежуточный текстовый блок
                                                                   (не последний — финальный ответ
                                                                   в live-события не попадает)
      kind="tg_action",  payload={"name", "input"}             — вызов тула mcp__telegram__* (сам тул
                                                                   ничего не делает — действие выполняет
                                                                   бот); карточка для него не рисуется
    Bash-вызов ровно "true" — разделитель сообщений: текст до него уходит отдельным сообщением
    (flush_pending), а карточка не показывается ни для start, ни для done.
    Регистрирует процесс под proc_key в running_procs, чтобы его можно было прервать
    командой /stop. Возвращает (response, returncode)."""
    proc = subprocess.Popen(
        cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True,
        bufsize=1, env={**os.environ, "HOME": consts.HOME, **(extra_env or {})}
    )
    if proc_key is not None:
        procs.register_proc(proc_key, proc)

    stderr_lines = []
    def drain_stderr():
        for line in proc.stderr:
            stderr_lines.append(line)
    threading.Thread(target=drain_stderr, daemon=True).start()

    final_text = None
    last_text = ""
    last_text_sent = [False]  # последний текстовый блок уже ушёл live (не финальный)
    had_output = [False]      # был хотя бы один вызов инструмента или live-текст
    pending_text = [None]
    tool_names = {}  # id -> исходное имя инструмента (нужно в tool_result, где имени уже нет)
    silent_ids = set()  # id вызовов без карточки (Bash true, mcp__telegram__*)

    def flush_pending():
        if pending_text[0] and on_event:
            on_event("text", pending_text[0])
            last_text_sent[0] = True
            had_output[0] = True
        pending_text[0] = None

    for line in proc.stdout:
        line = line.strip()
        if not line:
            continue
        try:
            d = json.loads(line)
        except ValueError:
            continue
        if d.get("parent_tool_use_id") is not None:
            # Событие принадлежит вложенному под-агенту (Task/Agent tool), а не текущему
            # ходу верхнего уровня — не стримим чужую переписку как свою.
            continue
        etype = d.get("type")
        if etype == "assistant":
            for block in d.get("message", {}).get("content", []):
                btype = block.get("type")
                if btype == "text":
                    flush_pending()
                    pending_text[0] = block.get("text", "")
                    last_text = block.get("text", "")
                    last_text_sent[0] = False
                elif btype == "tool_use":
                    flush_pending()
                    had_output[0] = True
                    tname = block.get("name", "?")
                    tinput = block.get("input") or {}
                    tool_names[block.get("id")] = tname
                    if tname.startswith("mcp__telegram__"):
                        silent_ids.add(block.get("id"))
                        if on_event:
                            on_event("tg_action", {"name": tname, "input": tinput})
                    elif tname == "Bash" and str(tinput.get("command", "")).strip() == "true":
                        silent_ids.add(block.get("id"))
                    elif on_event:
                        label, full = cardview.format_tool_use(tname, tinput)
                        on_event("tool_start", {
                            "id": block.get("id"),
                            "label": label,
                            "full": full,
                        })
        elif etype == "user":
            for block in d.get("message", {}).get("content", []):
                if block.get("type") == "tool_result" and on_event:
                    tool_use_id = block.get("tool_use_id")
                    if tool_use_id in silent_ids:
                        continue
                    # Task/Agent возвращают служебные метаданные (agentId, инструкции
                    # SendMessage для продолжения, usage) прямо в tool_result — они помечены
                    # харнесом как "internal, never show to user", поэтому кнопку "Посмотреть
                    # текст" для них не показываем вовсе.
                    if tool_names.get(tool_use_id) in ("Task", "Agent"):
                        result_text = ""
                    else:
                        raw_content = block.get("content")
                        if isinstance(raw_content, list):
                            result_text = "\n".join(
                                c.get("text", "") for c in raw_content
                                if isinstance(c, dict) and c.get("type") == "text"
                            )
                        elif isinstance(raw_content, str):
                            result_text = raw_content
                        else:
                            result_text = ""
                    on_event("tool_done", {
                        "id": block.get("tool_use_id"),
                        "ok": not block.get("is_error", False),
                        # Если к этому моменту /stop уже вызван — принудительный обрыв
                        # инструмента харнесом выглядит как обычная ошибка (is_error=True),
                        # хотя на деле это следствие остановки, а не реальный сбой.
                        "stopped": getattr(proc, "_user_stopped", False),
                        "result_text": result_text,
                        "name": tool_names.get(tool_use_id, ""),
                    })
        elif etype == "result":
            final_text = d.get("result")

    proc.wait()
    if proc_key is not None:
        procs.unregister_proc(proc_key, proc)
    user_stopped = getattr(proc, "_user_stopped", False)
    stderr_text = "".join(stderr_lines)
    if (final_text or "").strip():
        response = final_text.strip()
    elif last_text.strip() and not last_text_sent[0]:
        response = last_text.strip()
    elif had_output[0] and proc.returncode == 0:
        response = ""  # всё уже ушло live (текст/тулы), финального текста нет
    else:
        response = stderr_text.strip() or "Нет ответа"
    if user_stopped and not (final_text or "").strip():
        response = STOPPED_SENTINEL
    return response, proc.returncode, user_stopped


def ask_claude(user_message, image_path=None, reply_to=None, on_event=None, topic_id=None,
                chat_id=None, thread_id=None, model=None):
    """Запускает Claude и возвращает ответ.
    Для топиков использует --resume чтобы продолжать сессию между сообщениями.
    chat_id/thread_id прокидываются в env subprocess'а (BOT_CHAT_ID/BOT_THREAD_ID/
    BOT_TOPIC_ID) — их читает bot_scheduler_mcp_server.py, чтобы знать в какую
    тему планировать пробуждение."""
    extra_env = {
        "BOT_CHAT_ID": str(chat_id) if chat_id is not None else "",
        "BOT_THREAD_ID": str(thread_id) if thread_id is not None else "",
        "BOT_TOPIC_ID": str(topic_id) if topic_id is not None else "",
    }
    if image_path:
        image_note = f"\n\n[К сообщению прикреплено изображение: {image_path} — используй Read tool чтобы посмотреть его]"
        effective_message = user_message + image_note
    else:
        effective_message = user_message

    # Для тем группы используем --resume / --session-id
    session_id = None
    use_resume = False
    if topic_id is not None:
        sessions = storage.load_topic_sessions()
        topic_key = str(topic_id)
        if topic_key in sessions:
            session_id = sessions[topic_key]
            use_resume = True
        else:
            session_id = str(uuid.uuid4())
            sessions[topic_key] = session_id
            storage.save_topic_sessions(sessions)

    try:
        model = model or storage.load_settings().get("model", "sonnet")
        stream_flags = ["--output-format", "stream-json", "--verbose"]

        if use_resume and session_id:
            # Продолжаем существующую сессию — передаём только новое сообщение
            now = core.now_local().strftime("%Y-%m-%d %H:%M:%S")
            if reply_to:
                prompt_text = f"[Пользователь отвечает на: «{reply_to}»]\nПользователь [{now}]: {effective_message}"
            else:
                prompt_text = f"Пользователь [{now}]: {effective_message}"
            prompt_text = prompting.prompt_update_note(topic_id) + prompt_text
            cmd = [
                consts.CLAUDE_BIN, "--dangerously-skip-permissions",
                "--model", model, *stream_flags,
                "--resume", session_id,
                "-p", prompt_text
            ]
        else:
            # Первое сообщение или нет темы — полный промпт
            prompt = prompting.build_prompt(effective_message, reply_to=reply_to, topic_id=topic_id)
            if session_id:
                cmd = [
                    consts.CLAUDE_BIN, "--dangerously-skip-permissions",
                    "--model", model, *stream_flags,
                    "--session-id", session_id,
                    "-p", prompt
                ]
            else:
                cmd = [
                    consts.CLAUDE_BIN, "--dangerously-skip-permissions",
                    "--model", model, *stream_flags,
                    "-p", prompt
                ]

        proc_key = procs.stream_key(topic_id)
        response, returncode, stopped_by_user = run_claude_streaming(cmd, on_event=on_event, proc_key=proc_key, extra_env=extra_env)

        # Если --resume провалился (и это не ручная остановка) — сбрасываем сессию и повторяем с новой
        from bridge import authlogin
        if use_resume and returncode != 0 and not stopped_by_user and not authlogin.is_auth_error(response):
            print(f"[RESUME FAIL] topic={topic_id} session={session_id} rc={returncode}")
            new_sid = str(uuid.uuid4())
            sessions = storage.load_topic_sessions()
            sessions[str(topic_id)] = new_sid
            storage.save_topic_sessions(sessions)
            prompt = prompting.build_prompt(effective_message, reply_to=reply_to, topic_id=topic_id)
            cmd = [
                consts.CLAUDE_BIN, "--dangerously-skip-permissions",
                "--model", model, *stream_flags,
                "--session-id", new_sid,
                "-p", prompt
            ]
            response, returncode, stopped_by_user = run_claude_streaming(cmd, on_event=on_event, proc_key=proc_key, extra_env=extra_env)

        if topic_id is not None and returncode == 0:
            prompting.mark_prompt_version(topic_id)  # сессия теперь знает актуальные инструкции
        return response
    except Exception as e:
        return f"Ошибка: {e}"
    finally:
        if image_path and os.path.exists(image_path):
            os.unlink(image_path)


def chat_thread_to_topic_id(chat_id, thread_id):
    """Единообразно превращает (chat_id, thread_id) в topic_id для истории/сессий —
    та же схема, что использует handle_message при разборе входящих сообщений."""
    if chat_id == GROUP_CHAT_ID:
        return f"group_{thread_id}" if thread_id else "group_general"
    if chat_id is None:
        return None
    # Личка — «отдельная тема» (своя сессия --resume и история); любой другой чат — тоже своя
    return "dm" if chat_id > 0 else f"chat_{abs(chat_id)}"


def trigger_restart(chat_id, thread_id, prompt=""):
    """Перезапускает сервис бота, запоминая откуда пришёл рестарт (chat_id/thread_id)
    и необязательный промпт — после старта _startup_response() отправит ответ именно
    в эту тему/чат, а не всегда в General."""
    try:
        with open(consts.RESTART_PROMPT_FILE, "w") as f:
            json.dump({"prompt": prompt, "chat_id": chat_id, "thread_id": thread_id}, f)
    except Exception as e:
        print(f"[trigger_restart] не удалось сохранить {consts.RESTART_PROMPT_FILE}: {e}")
    subprocess.Popen(["sudo", "systemctl", "restart", "claude-telegram"])


def run_claude_turn(prompt, chat_id=None, thread_id=None, topic_id=None,
                     ask_reply_to=None, telegram_reply_to=None, image_path=None,
                     reply_markup=None, react_chat_id=None, react_msg_id=None,
                     save_user_history=None, history_topic=None, model=None):
    """Единая точка запуска Claude для ЛЮБОГО обращения к нему (сообщение, кнопка,
    реакция, опрос, старт бота и т.п.) — всегда через --output-format stream-json
    (см. ask_claude/run_claude_streaming), с live-карточками вызовов инструментов в
    правильном чате/теме, действиями mcp__telegram__* (файлы/реакция/кнопки/опрос), префиксом
    "Думал Xc" и сохранением истории. model — алиас модели только для этого хода (вместо
    настройки). Если пользователь остановил через /stop — карточки помечаются
    🛑 и ничего больше не отправляется (возвращает None). Иначе возвращает финальный текст."""
    _cid = chat_id if chat_id is not None else GROUP_CHAT_ID
    hist_topic = history_topic if history_topic is not None else topic_id

    if react_chat_id and react_msg_id:
        tgapi.set_reaction(react_chat_id, react_msg_id, "👀")

    topic_lock = procs.get_topic_lock(topic_id)
    queued = topic_lock.locked()  # в теме уже идёт ход Claude — этот запрос ждёт своей очереди
    placeholder_id = tgapi.send_single("⏳ В очереди — Claude ещё отвечает на предыдущее сообщение..." if queued else "⏳",
                                       chat_id=_cid, thread_id=thread_id, attach_keyboard=False)
    turn_state = {"reacted": False, "texts": []}
    react_target = (react_chat_id, react_msg_id) if react_chat_id and react_msg_id else None
    on_event, clear_placeholder, mark_stopped = cardview.make_stream_handler(
        _cid, thread_id, placeholder_id, react_target=react_target, turn_state=turn_state)

    t0 = time.time()
    with topic_lock, tgapi.chat_action(_cid, thread_id, "typing"):  # «печатает…» на время хода; плейсхолдер ⏳ остаётся
        response = ask_claude(prompt, image_path=image_path, reply_to=ask_reply_to, on_event=on_event,
                               topic_id=topic_id, chat_id=_cid, thread_id=thread_id, model=model)
    clear_placeholder()
    from bridge import authlogin  # локально: authlogin импортирует tgapi, который импортирует этот модуль
    if authlogin.is_auth_error(response):
        print(f"[AUTH] ошибка авторизации Claude: {response[:120]!r} — запускаю перелогин через tmux")
        authlogin.begin(_cid, thread_id)
        if react_target:
            tgapi.set_reaction(react_chat_id, react_msg_id, None)
        return AUTH_FAILED
    print(f"[FINAL_TEXT len={len(response) if response else 0}] {response[:200]!r}")

    if response == STOPPED_SENTINEL:
        mark_stopped()
        if react_chat_id and react_msg_id:
            tgapi.set_reaction(react_chat_id, react_msg_id, None)
        return None

    elapsed = time.time() - t0
    elapsed_str = f"{elapsed:.0f}с" if elapsed < 60 else f"{int(elapsed)//60}м {int(elapsed)%60}с"
    full_response = prompting.prepend_thinking(response, elapsed_str)

    if save_user_history is not None:
        storage.save_history("Пользователь", save_user_history, topic_id=hist_topic)
    history_text = " ".join(turn_state["texts"] + ([response] if response else []))[:300]
    storage.save_history("Ассистент", history_text or "[действие без текста]", topic_id=hist_topic)

    if react_target and not turn_state["reacted"]:
        tgapi.set_reaction(react_chat_id, react_msg_id, None)  # снять 👀

    sent_mid = tgapi.send_message(full_response, reply_markup, chat_id=_cid, thread_id=thread_id, reply_to=telegram_reply_to)
    print(f"[FINAL_SEND] mid={sent_mid} chat={_cid} thread={thread_id} full_response_len={len(full_response) if full_response else 0}")
    return full_response
