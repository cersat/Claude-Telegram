#!/usr/bin/env python3
"""
Telegram-бот: получает сообщения от владельца (ALLOWED_USER_ID),
спрашивает Claude CLI и отвечает в Telegram.

Точка входа. Остальная логика — в пакете bridge/ (см. bridge/*.py).
"""

import sys
import time
import threading
import requests
import os
import json
import signal
from secrets_local import GROUP_CHAT_ID
import memory_store
from bridge import cb_handlers, claude_run, consts, core, dnscache, keyboards, lifecycle, logstamp, msg_handlers, permui, pollstats, reminders, storage, tgapi, upd_handlers


def main():
    logstamp.install()  # метки времени в каждой строке лога
    dnscache.install()  # кэш DNS: резолвер иногда подвисает на 2с
    # Обработчик штатного завершения (SIGTERM от systemd)
    def _sigterm_handler(signum, frame):
        print("Received SIGTERM, shutting down cleanly.")
        lifecycle.write_shutdown_status(clean=True)
        lifecycle.append_restart_log("stop_clean")
        sys.exit(0)
    signal.signal(signal.SIGTERM, _sigterm_handler)

    requests.post(f"{core.API}/setMyCommands", json={
        "commands": [{"command": "stop", "description": "Прервать текущее выполнение Claude"}]
    })

    reminders.load_reminders_on_start()
    memory_store.ensure_memory_seed()
    reminders.seed_free_time_job()
    threading.Thread(target=reminders.reminder_loop, daemon=True).start()
    threading.Thread(target=reminders.scheduler_loop, daemon=True).start()
    threading.Thread(target=permui.watch_loop, daemon=True).start()  # кнопки разрешений на карточках вызовов

    # Проверяем предыдущее завершение — нештатный перезапуск?
    was_unclean, prev_shutdown_time = lifecycle.check_previous_shutdown()

    # Помечаем текущий запуск как "ещё не завершился штатно"
    lifecycle.write_shutdown_status(clean=False)
    # Записываем событие старта в журнал перезапусков
    if was_unclean:
        log_event = "start_unclean" if prev_shutdown_time else "start_first"
    else:
        log_event = "start_clean"
    lifecycle.append_restart_log(log_event, prev_shutdown_time)

    # Удаляем предыдущее стартовое сообщение
    prev_startup_mid = lifecycle.load_startup_msg_id()
    if prev_startup_mid:
        requests.post(f"{core.API}/deleteMessage", json={
            "chat_id": GROUP_CHAT_ID,
            "message_id": prev_startup_mid
        })
    # Отправляем стартовое сообщение с reply keyboard
    startup_mid = tgapi.send_message("✅ Бот запущен и готов к работе!", keyboards.get_main_keyboard(topic_id="group_general"), chat_id=GROUP_CHAT_ID)
    if startup_mid:
        lifecycle.save_startup_msg_id(startup_mid)
    # Уведомляем Claude о запуске — он ответит прямо в чат
    def _startup_response():
        custom_prompt = None
        restart_chat_id, restart_thread_id = GROUP_CHAT_ID, None
        if os.path.exists(consts.RESTART_PROMPT_FILE):
            try:
                with open(consts.RESTART_PROMPT_FILE) as f:
                    data = json.load(f)
                custom_prompt = (data.get("prompt") or "").strip() or None
                restart_chat_id = data.get("chat_id") or GROUP_CHAT_ID
                restart_thread_id = data.get("thread_id")
            except Exception as e:
                print(f"[_startup_response] не удалось прочитать {consts.RESTART_PROMPT_FILE}: {e}")
            try:
                os.remove(consts.RESTART_PROMPT_FILE)
            except Exception:
                pass

        if custom_prompt:
            msg = f"[Системное уведомление]\n🚀 Бот перезапустился (по твоей же кнопке с промптом). {custom_prompt}"
        elif was_unclean:
            if prev_shutdown_time:
                msg = (
                    f"[Системное уведомление]\n"
                    f"⚠️ Бот перезапустился после нештатного завершения. "
                    f"Последняя запись о работе: {prev_shutdown_time}. "
                    f"Причина неизвестна — возможно малинка перезагрузилась или бот упал. "
                    f"Сообщи об этом пользователю и скажи что готов к работе."
                )
            else:
                msg = (
                    "[Системное уведомление]\n"
                    "⚠️ Бот запустился впервые или после нештатного завершения — файла статуса не было. "
                    "Сообщи пользователю что это первый запуск или малинка перезагрузилась без штатного выключения бота. "
                    "Скажи что готов к работе."
                )
        else:
            msg = "[Системное уведомление]\n🚀 Бот только что перезапустился. Напиши короткое подтверждение что готов к работе."

        restart_topic_id = claude_run.chat_thread_to_topic_id(restart_chat_id, restart_thread_id)
        claude_run.run_claude_turn(msg, chat_id=restart_chat_id, thread_id=restart_thread_id,
                         topic_id=restart_topic_id, history_topic=restart_topic_id)
    threading.Thread(target=_startup_response, daemon=True).start()
    print(f"Bot started. offset={storage.get_offset()}")

    # самопроверка при каждом старте: строка восстановления на синтетической пачке с датами,
    # чтобы вызов и импорт выполнялись до настоящего сбоя, а не во время него
    try:
        _t = time.time()
        print("[startup selftest] " + pollstats.format_recovery(
            [{"message": {"date": int(_t) - 120}}, {"message": {"date": int(_t) - 5}}],
            1, "00:00:00", "00:00:35", 35.0))
    except Exception as selftest_err:
        print(f"[startup selftest] строка восстановления не собралась: {selftest_err}")

    last_error_notify = 0  # троттлинг: не чаще раза в 60 секунд
    poll_fail_run = 0      # сколько сетевых сбоев опроса подряд
    poll_fail_since = None
    poll_started = core.now_local()   # когда начался текущий запрос getUpdates
    poll_fail_started = None          # когда начался ПЕРВЫЙ неудавшийся запрос серии

    while True:
        try:
            offset = storage.get_offset()
            poll_started = core.now_local()
            resp = requests.get(f"{core.API}/getUpdates", params={
                "offset": offset,
                "timeout": 30,
                "allowed_updates": json.dumps(["message", "edited_message", "callback_query", "message_reaction", "poll", "poll_answer", "poll_option_added"])
            }, timeout=35)
            data = resp.json()

            if poll_fail_run:
                try:
                    print(pollstats.format_recovery(
                        data.get("result", []), poll_fail_run, poll_fail_started, poll_fail_since,
                        (core.now_local() - poll_started).total_seconds()))
                except Exception as diag_err:
                    # диагностика не должна останавливать обработку апдейтов
                    print(f"[poll recovered] диагностику записать не удалось: {diag_err}")
                poll_fail_run = 0

            for update in data.get("result", []):
                storage.save_offset(update["update_id"] + 1)

                if "message" in update:
                    msg_handlers.handle_message(update["message"])
                elif "edited_message" in update:
                    msg_handlers.handle_edited_message(update["edited_message"])
                elif "callback_query" in update:
                    # в отдельном потоке: медленная/зависшая кнопка не должна глушить опрос апдейтов
                    threading.Thread(target=cb_handlers.handle_callback_safe,
                                     args=(update["callback_query"],), daemon=True).start()
                elif "message_reaction" in update:
                    upd_handlers.handle_reaction(update["message_reaction"])
                elif "poll" in update:
                    upd_handlers.handle_poll_update(update["poll"])
                elif "poll_answer" in update:
                    upd_handlers.handle_poll_answer(update["poll_answer"])
                elif "poll_option_added" in update:
                    upd_handlers.handle_poll_option_added(update["poll_option_added"])

        except (requests.exceptions.Timeout, requests.exceptions.ConnectionError,
                requests.exceptions.ReadTimeout, requests.exceptions.ConnectTimeout,
                requests.exceptions.ChunkedEncodingError, requests.exceptions.ProxyError,
                requests.exceptions.JSONDecodeError) as e:
            if not poll_fail_run:
                poll_fail_since = core.now_local().strftime('%H:%M:%S')
                poll_fail_started = poll_started.strftime('%H:%M:%S')
            poll_fail_run += 1
            print(f"Network error (ignored): {e}")
            time.sleep(5)
        except Exception as e:
            import socket
            if isinstance(e, (OSError, socket.timeout, TimeoutError, ConnectionError,
                              ConnectionResetError, ConnectionAbortedError, BrokenPipeError)):
                if not poll_fail_run:
                    poll_fail_since = core.now_local().strftime('%H:%M:%S')
                    poll_fail_started = poll_started.strftime('%H:%M:%S')
                poll_fail_run += 1
                print(f"Network/socket error (ignored): {e}")
                time.sleep(5)
                continue
            print(f"Error: {e}")
            now_ts = time.time()
            if now_ts - last_error_notify > 60:
                last_error_notify = now_ts
                _err_msg = f"[Системное уведомление]\n⚠️ В боте возникла ошибка:\n{e}\n\nПосмотри {consts.BOT_LOG_FILE} и пофикси, потом отпишись в чат что сделал."
                def _error_response(m=_err_msg):
                    claude_run.run_claude_turn(m)
                threading.Thread(target=_error_response, daemon=True).start()
            time.sleep(5)


if __name__ == "__main__":
    main()
