"""Обработчик нажатий инлайн-кнопок."""

import time
import threading
import requests
import subprocess
import os
from datetime import datetime
from secrets_local import ALLOWED_USER_ID
from bridge import cardview, chessgame, claude_run, consts, core, keyboards, mdfmt, memui, procs, reminders, shared, storage, sysinfo, tgapi


def _handle_permission(cq, data, cq_id, chat_id, msg_id):
    """a — разрешить, d — запретить, r — разрешить и запомнить инструмент (кроме Bash). Решение кладётся файлом
    для MCP-сервера, который ждёт его; сообщение правится без кнопок. Нет файла запроса — он уже истёк."""
    import json as _json
    try:
        _, rid, action = data.split("_", 2)
    except ValueError:
        tgapi.answer_callback(cq_id, "Непонятная кнопка")
        return
    req_path = os.path.join(consts.PERM_DIR, f"{rid}.req")
    dec_path = os.path.join(consts.PERM_DIR, f"{rid}.decision")
    shown = cq.get("message", {}).get("text", "")
    rest = shown.split("\n", 1)[1] if "\n" in shown else ""
    standalone = shown.startswith("🔐")  # отдельное сообщение-запасной вариант; иначе кнопки висят на карточке вызова

    def _edit(title):
        if standalone:
            requests.post(f"{core.API}/editMessageText", json={
                "chat_id": chat_id, "message_id": msg_id, "text": (title + ("\n" + rest if rest else ""))[:4000]
            }, timeout=10)
            return
        # карточка: текст не трогаем (там уже команда), убираем только строки разрешения — кнопки раскрытия остаются;
        # итог ✅/⚠️ карточка получит сама, когда инструмент отработает
        rows = [r for r in cq.get("message", {}).get("reply_markup", {}).get("inline_keyboard", [])
                if not any(str(b.get("callback_data", "")).startswith("perm_") for b in r)]
        requests.post(f"{core.API}/editMessageReplyMarkup", json={
            "chat_id": chat_id, "message_id": msg_id, "reply_markup": {"inline_keyboard": rows}}, timeout=10)

    if not (rid.isalnum() and os.path.exists(req_path)):
        tgapi.answer_callback(cq_id, "Запрос уже устарел")
        _edit("⌛ Запрос устарел (время вышло или Claude остановлен)")
        return
    try:
        with open(req_path) as f:
            tool = _json.load(f).get("tool", "?")
    except Exception:
        tool = "?"
    allow = action in ("a", "r")
    if action == "r" and tool != "Bash":
        try:
            with open(consts.PERM_RULES_FILE) as f:
                rules = _json.load(f)
        except Exception:
            rules = {}
        tools = set(rules.get("tools", []))
        tools.add(tool)
        rules["tools"] = sorted(tools)
        with open(consts.PERM_RULES_FILE, "w") as f:
            _json.dump(rules, f, ensure_ascii=False, indent=2)
    tmp = dec_path + ".tmp"
    with open(tmp, "w") as f:
        _json.dump({"allow": allow, "always": action == "r"}, f)
    os.replace(tmp, dec_path)  # атомарно: MCP-сервер не должен прочитать недописанный файл
    tgapi.answer_callback(cq_id, "Разрешено" if allow else "Запрещено")
    _edit(("♾ Разрешено и запомнено" if action == "r" else "✅ Разрешено") if allow else "❌ Запрещено")


def handle_callback_safe(cq):
    """Обёртка над handle_callback: логирует каждое нажатие, время обработки и исключения
    (раньше сбой кнопки не оставлял в логе никаких следов)."""
    data = cq.get("data", "")
    t0 = time.time()
    print(f"[CB] нажатие: {data[:60]}")
    try:
        handle_callback(cq)
    except Exception as e:
        import traceback
        print(f"[CB] ОШИБКА в обработчике {data[:60]}: {e}\n{traceback.format_exc()}")
        try:
            tgapi.answer_callback(cq.get("id"), "⚠️ Ошибка обработки кнопки")
        except Exception:
            pass
    dt = time.time() - t0
    if dt > 3:
        print(f"[CB] медленная кнопка {data[:60]}: {dt:.1f}с")


def handle_callback(cq):
    from_id = cq.get("from", {}).get("id")
    if from_id != ALLOWED_USER_ID:
        requests.post(f"{core.API}/answerCallbackQuery", json={
            "callback_query_id": cq["id"],
            "text": "⛔ Только для владельца бота",
            "show_alert": True
        })
        return
    data = cq.get("data", "")
    cq_id = cq["id"]
    msg_id = cq["message"]["message_id"]
    cq_chat_id = cq["message"]["chat"]["id"]
    cq_thread_id = cq["message"].get("message_thread_id")

    if data.startswith("perm_"):
        # Ответ на вопрос «Разрешить вызов?» от permission_mcp_server.py: perm_<id>_a|d|r
        _handle_permission(cq, data, cq_id, cq_chat_id, msg_id)
        return

    if data.startswith("rvl_"):
        # Кнопки "Полное сообщение"/"Посмотреть текст" под карточками live-стрима —
        # дописывают раскрытый текст ПРЯМО в карточку (editMessageText), не новым сообщением.
        revealed = cardview._reveal_cache.get(data)
        if revealed is None:
            tgapi.answer_callback(cq_id, "⚠️ Текст больше не доступен (устарел)")
            return
        tgapi.answer_callback(cq_id)
        base_text = cardview._get_card_text(cq_chat_id, msg_id, fallback=cq["message"].get("text", ""))
        new_text = f"{base_text}\n\n{revealed}" if base_text else revealed
        current_markup = cq["message"].get("reply_markup", {}) or {}
        new_rows = []
        for row in current_markup.get("inline_keyboard", []):
            new_row = [b for b in row if b.get("callback_data") != data]
            if new_row:
                new_rows.append(new_row)
        if len(mdfmt.markdown_to_html(new_text)) > 4096:
            # Раскрытый текст не влезает в карточку (лимит Telegram на editMessageText) —
            # карточку не трогаем (кроме кнопок), раскрытый текст шлём отдельным сообщением
            # с чанкованием (как обычные длинные ответы).
            tgapi.edit_message(msg_id, base_text, reply_markup={"inline_keyboard": new_rows}, chat_id=cq_chat_id)
            tgapi.send_single(revealed, chat_id=cq_chat_id, thread_id=cq_thread_id)
        else:
            cardview._set_card_text(cq_chat_id, msg_id, new_text)
            tgapi.edit_message(msg_id, new_text, reply_markup={"inline_keyboard": new_rows}, chat_id=cq_chat_id)
        return

    if data == "nav_sections":
        tgapi.answer_callback(cq_id)
        requests.post(f"{core.API}/editMessageText", json={
            "chat_id": cq_chat_id,
            "message_id": msg_id,
            "text": "📂 Выбери раздел:",
            "reply_markup": keyboards.SECTIONS_NAV_INLINE
        })
        return

    if data.startswith("nav_"):
        tgapi.answer_callback(cq_id)
        section = data[4:]  # info / fan / power / settings / memory / records / reminders / ping / reset / mode_*
        if section == "info":
            def _nav_info():
                info_text = sysinfo.get_info_text()
                tgapi.send_single(info_text, keyboards.INFO_INLINE, chat_id=cq_chat_id, thread_id=cq_thread_id)
            threading.Thread(target=_nav_info, daemon=True).start()
        elif section == "fan":
            status = sysinfo.fan_status()
            shared.fan_msg_ids[(cq_chat_id, cq_thread_id)] = tgapi.send_message(f"🌀 Вентилятор: {status}", keyboards.FAN_INLINE, chat_id=cq_chat_id, thread_id=cq_thread_id)
        elif section == "power":
            shared.power_msg_ids[(cq_chat_id, cq_thread_id)] = tgapi.send_message("🔌 Управление питанием:", keyboards.POWER_INLINE, chat_id=cq_chat_id, thread_id=cq_thread_id)
        elif section == "settings":
            settings_text = keyboards.get_settings_text()
            shared.settings_msg_ids[(cq_chat_id, cq_thread_id)] = tgapi.send_single(settings_text, keyboards.get_settings_inline(), chat_id=cq_chat_id, thread_id=cq_thread_id)
        elif section == "memory":
            def _send_picker(_cid=cq_chat_id, _tid=cq_thread_id):
                tgapi.send_message("В какой файл писать?", memui.get_memory_pick_inline(), chat_id=_cid, thread_id=_tid)
            threading.Thread(target=_send_picker, daemon=True).start()
        elif section == "records":
            def _send_records(_cid=cq_chat_id, _tid=cq_thread_id):
                memui.send_memory_items(chat_id=_cid, thread_id=_tid)
            threading.Thread(target=_send_records, daemon=True).start()
        elif section == "reminders":
            import time as _time
            with reminders._reminder_lock:
                rems = dict(reminders.active_reminders)
            add_btn = [{"text": "➕ Добавить напоминание", "callback_data": "reminder_add"}]
            if not rems:
                tgapi.send_single("⏰ Активных напоминаний нет.", {"inline_keyboard": [add_btn]}, chat_id=cq_chat_id, thread_id=cq_thread_id)
            else:
                lines = ["⏰ *Активные напоминания:*\n"]
                inline_rows = []
                for rid2, r in sorted(rems.items()):
                    remaining = max(0, int(r["fire_at"] - _time.time()))
                    dur = reminders.format_duration(remaining) if remaining > 0 else "сейчас"
                    fire_at_str = datetime.fromtimestamp(r["fire_at"]).strftime("%H:%M")
                    repeat_info = f" (каждые {reminders.format_duration(r['repeat_secs'])})" if r["repeat_secs"] else ""
                    lines.append(f"• _{r['text']}_ — в {fire_at_str} (через {dur}){repeat_info}")
                    inline_rows.append([{"text": f"❌ {r['text'][:30]}", "callback_data": f"reminder_cancel_{rid2}"}])
                inline_rows.append(add_btn)
                tgapi.send_single("\n".join(lines), {"inline_keyboard": inline_rows}, chat_id=cq_chat_id, thread_id=cq_thread_id)
        elif section == "create_topic":
            shared.pending_create_topic = True
            tgapi.send_message("➕ Введи название новой темы:", chat_id=cq_chat_id, thread_id=cq_thread_id)
        elif section == "ping":
            tgapi.send_message("🏓 Понг!", chat_id=cq_chat_id, thread_id=cq_thread_id)
        elif section == "limits":
            def _nav_limits(_cid=cq_chat_id, _tid=cq_thread_id):
                tgapi.send_message(sysinfo.get_usage_text(), chat_id=_cid, thread_id=_tid)
            threading.Thread(target=_nav_limits, daemon=True).start()
        elif section == "reset":
            tgapi.answer_callback(cq_id, "Перезапускаю бота...")
            tgapi.send_message("🔄 Перезапускаю бота...", chat_id=cq_chat_id, thread_id=cq_thread_id)
            claude_run.trigger_restart(cq_chat_id, cq_thread_id)
        elif section == "mode_claude":
            storage.set_bot_mode(claude_run.chat_thread_to_topic_id(cq_chat_id, cq_thread_id), "claude")
            tgapi.send_message("🤖 Режим *Claude* включён.", chat_id=cq_chat_id, thread_id=cq_thread_id)
        elif section == "mode_passthrough":
            storage.set_bot_mode(claude_run.chat_thread_to_topic_id(cq_chat_id, cq_thread_id), "passthrough")
            tgapi.send_message("📤 *Резервный* режим включён. Сообщения идут в Claude без обработки команд.", chat_id=cq_chat_id, thread_id=cq_thread_id)
        elif section == "mode_shell":
            storage.set_bot_mode(claude_run.chat_thread_to_topic_id(cq_chat_id, cq_thread_id), "shell")
            shared.shell_cwd = consts.HOME
            tgapi.send_message(f"🖥 *Командный* режим включён.\n📁 `{shared.shell_cwd}`\n\nВводи bash-команды. Для выхода нажми «🤖 Claude».", chat_id=cq_chat_id, thread_id=cq_thread_id)
        return

    if data.startswith("chess_") or data in ("chess_resign", "chess_new", "chess_close"):
        tgapi.answer_callback(cq_id)
        chess_key = (cq_chat_id, cq_thread_id)
        chess_game = chessgame.chess_games.get(chess_key)

        if data == "chess_new":
            chess_game = {
                'board': chessgame.chess_initial_board(),
                'turn': 'W',
                'selected': None,
                'valid_moves': [],
                'game_over': False,
                'status': '',
                'castling': {'W': {'K': True, 'Q': True}, 'B': {'K': True, 'Q': True}},
                'chat_id': cq_chat_id,
                'msg_id': msg_id,
                'moves': [],
            }
            chessgame.chess_games[chess_key] = chess_game
            kb = chessgame.chess_render_keyboard(chess_game)
            requests.post(f"{core.API}/editMessageText", json={
                "chat_id": cq_chat_id,
                "message_id": msg_id,
                "text": mdfmt.markdown_to_html(chessgame.chess_status_text(chess_game)),
                "parse_mode": "HTML",
                "reply_markup": kb
            }, timeout=10)
            return

        if data == "chess_close":
            chessgame.chess_games.pop(chess_key, None)
            requests.post(f"{core.API}/deleteMessage", json={
                "chat_id": cq_chat_id,
                "message_id": msg_id
            }, timeout=10)
            return

        if data == "chess_resign":
            if chess_game:
                chess_game['game_over'] = True
                chess_game['status'] = '🏳 Ты сдался. Чёрные победили!'
                chess_game['selected'] = None
                chess_game['valid_moves'] = []
                kb = chessgame.chess_render_keyboard(chess_game)
                requests.post(f"{core.API}/editMessageText", json={
                    "chat_id": cq_chat_id,
                    "message_id": msg_id,
                    "text": mdfmt.markdown_to_html(chessgame.chess_status_text(chess_game)),
                    "parse_mode": "HTML",
                    "reply_markup": kb
                }, timeout=10)
            return

        # Выбор фигуры для превращения пешки
        if data.startswith("chess_promo_"):
            if chess_game and chess_game.get('pending_promotion') and chess_game.get('turn') == 'promo':
                piece_map = {'Q': 'WQ', 'R': 'WR', 'B': 'WB', 'N': 'WN'}
                choice = data.split('_')[-1]
                if choice in piece_map:
                    pr, pc = chess_game['pending_promotion']
                    chess_game['board'][pr][pc] = piece_map[choice]
                    chess_game.pop('pending_promotion')
                    prefix = chess_game.pop('pending_move_prefix', '')
                    chess_game.setdefault('moves', []).append(prefix + choice.lower())
                    chess_game['turn'] = 'B'
                    board = chess_game['board']
                    if chessgame.chess_in_check(board, 'B'):
                        if not chessgame.chess_all_legal(board, 'B'):
                            chess_game['game_over'] = True
                            chess_game['status'] = '♚ Мат! Ты победил!'
                            chess_game['turn'] = 'done'
                        else:
                            chess_game['status'] = '⚠️ Шах!'
                    elif not chessgame.chess_all_legal(board, 'B'):
                        chess_game['game_over'] = True
                        chess_game['status'] = '🤝 Пат!'
                        chess_game['turn'] = 'done'
                    else:
                        chess_game['status'] = ''
                    # ИИ ходит сразу
                    if chess_game['turn'] == 'B' and not chess_game.get('game_over'):
                        chessgame.chess_apply_ai_move(chess_game)
                    kb = chessgame.chess_render_keyboard(chess_game)
                    requests.post(f"{core.API}/editMessageText", json={
                        "chat_id": cq_chat_id,
                        "message_id": msg_id,
                        "text": mdfmt.markdown_to_html(chessgame.chess_status_text(chess_game)),
                        "parse_mode": "HTML",
                        "reply_markup": kb
                    }, timeout=10)
            return

        if data == "chess_analyze":
            if not chess_game or not chess_game.get('moves'):
                tgapi.send_message("Нет ходов для анализа", chat_id=cq_chat_id, thread_id=cq_thread_id)
                return
            moves_copy = list(chess_game['moves'])
            target_chat, target_thread = cq_chat_id, cq_thread_id
            tgapi.send_message("⏳ Разбираю партию через Stockfish...", chat_id=target_chat, thread_id=target_thread)
            def run_chess_analysis():
                try:
                    records = chessgame.chess_analyze_game(moves_copy)
                    report = chessgame.chess_format_report(records)
                except Exception as e:
                    report = f"⚠️ Не получилось разобрать партию: {e}"
                tgapi.send_message(report, chat_id=target_chat, thread_id=target_thread)
            threading.Thread(target=run_chess_analysis, daemon=True).start()
            return

        # Клик по клетке доски: chess_<r>_<c>
        if chess_game is None or chess_game.get('game_over'):
            return
        parts = data.split('_')
        if len(parts) != 3:
            return
        try:
            r, c = int(parts[1]), int(parts[2])
        except ValueError:
            return

        board = chess_game['board']
        selected = chess_game.get('selected')
        valid = chess_game.get('valid_moves', [])
        turn = chess_game['turn']

        if turn not in ('W',):
            return  # ждём хода ИИ или превращения

        castling = chess_game.get('castling', {'W': {'K': True, 'Q': True}, 'B': {'K': True, 'Q': True}})

        if selected and (r, c) in valid:
            sr, sc = selected
            # Рокировка белых: король ходит на 2 клетки
            if board[sr][sc] == 'WK' and abs(c - sc) == 2:
                board[r][c] = 'WK'
                board[sr][sc] = None
                if c == 6:  # короткая рокировка
                    board[7][5] = 'WR'; board[7][7] = None
                else:       # длинная рокировка
                    board[7][3] = 'WR'; board[7][0] = None
            else:
                board[r][c] = board[sr][sc]
                board[sr][sc] = None
            chessgame.chess_update_castling(castling, board, 'W', sr, sc, r, c)
            move_uci = chessgame.chess_sq(sr, sc) + chessgame.chess_sq(r, c)
            # Превращение пешки — спрашиваем пользователя
            if board[r][c] == 'WP' and r == 0:
                chess_game['pending_promotion'] = (r, c)
                chess_game['pending_move_prefix'] = move_uci
                chess_game['selected'] = None
                chess_game['valid_moves'] = []
                chess_game['status'] = ''
                chess_game['turn'] = 'promo'
                kb = chessgame.chess_render_keyboard(chess_game)
                requests.post(f"{core.API}/editMessageText", json={
                    "chat_id": cq_chat_id,
                    "message_id": msg_id,
                    "text": mdfmt.markdown_to_html(chessgame.chess_status_text(chess_game)),
                    "parse_mode": "HTML",
                    "reply_markup": kb
                }, timeout=10)
                return
            chess_game.setdefault('moves', []).append(move_uci)
            chess_game['selected'] = None
            chess_game['valid_moves'] = []
            # Проверить шах/мат/пат после хода белых
            if chessgame.chess_in_check(board, 'B'):
                if not chessgame.chess_all_legal(board, 'B', castling):
                    chess_game['game_over'] = True
                    chess_game['status'] = '♔ Мат! Ты победил!'
                    chess_game['turn'] = 'done'
                else:
                    chess_game['status'] = '⚠️ Шах!'
                    chess_game['turn'] = 'B'
            elif not chessgame.chess_all_legal(board, 'B', castling):
                chess_game['game_over'] = True
                chess_game['status'] = '🤝 Пат!'
                chess_game['turn'] = 'done'
            else:
                chess_game['status'] = ''
                chess_game['turn'] = 'B'
            # Ход ИИ сразу, одно обновление доски вместо двух
            if chess_game['turn'] == 'B' and not chess_game.get('game_over'):
                chessgame.chess_apply_ai_move(chess_game)
            # Одно обновление для хода белых + ответа ИИ
            kb = chessgame.chess_render_keyboard(chess_game)
            requests.post(f"{core.API}/editMessageText", json={
                "chat_id": cq_chat_id,
                "message_id": msg_id,
                "text": mdfmt.markdown_to_html(chessgame.chess_status_text(chess_game)),
                "parse_mode": "HTML",
                "reply_markup": kb
            }, timeout=10)
        else:
            # Выбор фигуры
            piece = board[r][c]
            if piece and chessgame.chess_color(piece) == 'W':
                moves = chessgame.chess_legal_moves(board, r, c, castling)
                chess_game['selected'] = (r, c)
                chess_game['valid_moves'] = moves
                if not moves:
                    chess_game['status'] = '⚠️ Эта фигура не может ходить'
                else:
                    chess_game['status'] = ''
            else:
                chess_game['selected'] = None
                chess_game['valid_moves'] = []
                chess_game['status'] = ''
            kb = chessgame.chess_render_keyboard(chess_game)
            requests.post(f"{core.API}/editMessageText", json={
                "chat_id": cq_chat_id,
                "message_id": msg_id,
                "text": mdfmt.markdown_to_html(chessgame.chess_status_text(chess_game)),
                "parse_mode": "HTML",
                "reply_markup": kb
            }, timeout=10)
        return

    if data.startswith("ccb_"):
        spec = keyboards._claude_buttons.get(data)
        if spec is None:
            tgapi.answer_callback(cq_id, "Кнопка устарела")
            return
        btn_topic_id = claude_run.chat_thread_to_topic_id(cq_chat_id, cq_thread_id)
        kind = spec["kind"]

        if kind == "action":
            action = spec["action"]
            if action == "restart":
                tgapi.answer_callback(cq_id, "Перезапускаю бота...")
                tgapi.send_message("🔄 Перезапускаю бота...", None, chat_id=cq_chat_id, thread_id=cq_thread_id)
                claude_run.trigger_restart(cq_chat_id, cq_thread_id, spec.get("prompt", ""))
            elif action == "stop":
                stopped = procs.stop_proc(procs.stream_key(btn_topic_id))
                tgapi.answer_callback(cq_id, "🛑 Остановил." if stopped else "Сейчас ничего не выполняется.")
            elif action == "reset_session":
                if btn_topic_id is None:
                    tgapi.answer_callback(cq_id, "Для этого чата нет отдельной сессии")
                else:
                    storage.reset_history(btn_topic_id)
                    tgapi.answer_callback(cq_id, "🧹 Сессия и история темы сброшены")
            elif action == "clear_log":
                open(consts.BOT_LOG_FILE, "w").close()
                sysinfo._log_error_cache.update(count=0, last="", checked_at=time.time())
                tgapi.answer_callback(cq_id, "🧹 Лог очищен")
            elif action == "info":
                tgapi.answer_callback(cq_id)
                threading.Thread(target=lambda: tgapi.send_single(sysinfo.get_info_text(), keyboards.INFO_INLINE, chat_id=cq_chat_id,
                                                            thread_id=cq_thread_id), daemon=True).start()
            elif action == "limits":
                tgapi.answer_callback(cq_id)
                threading.Thread(target=lambda: tgapi.send_message(sysinfo.get_usage_text(), chat_id=cq_chat_id,
                                                             thread_id=cq_thread_id), daemon=True).start()
            return

        tgapi.answer_callback(cq_id)
        if kind == "code":
            def run_btn_code():
                if spec["lang"] == "python":
                    out = sysinfo.run_python_code(spec["code"])
                else:
                    out = sysinfo.run_shell_command(spec["code"])
                tgapi.send_single(cardview._code_block(out), chat_id=cq_chat_id, thread_id=cq_thread_id)
            threading.Thread(target=run_btn_code, daemon=True).start()
            return

        # kind == "prompt": запускаем Claude с промптом кнопки (не с её названием),
        # при необходимости — на другой модели только для этого запуска
        def run_btn_claude():
            model = spec.get("model")
            if model:
                tgapi.send_single(f"🔀 Этот запрос — на модели {keyboards.MODEL_LABELS[model]}", chat_id=cq_chat_id,
                            thread_id=cq_thread_id, attach_keyboard=False)
            claude_run.run_claude_turn(f"[Нажата кнопка «{spec['label']}». Её промпт:]\n{spec['prompt']}",
                             chat_id=cq_chat_id, thread_id=cq_thread_id, topic_id=btn_topic_id,
                             save_user_history=f"[кнопка «{spec['label']}»] {spec['prompt'][:150]}",
                             history_topic=btn_topic_id, model=model)
        threading.Thread(target=run_btn_claude, daemon=True).start()
        return

    if data == "mem_new":
        shared.pending_memory_add = True
        tgapi.answer_callback(cq_id)
        tgapi.delete_message(msg_id)
        tgapi.send_message("Что хочешь запомнить?", chat_id=cq_chat_id, thread_id=cq_thread_id)
        return

    if data.startswith("mem_pick_"):
        fname = data[len("mem_pick_"):] + ".md"
        shared.pending_memory_append = fname
        tgapi.answer_callback(cq_id)
        tgapi.delete_message(msg_id)
        tgapi.send_message(f"Допиши в `{fname}`:", chat_id=cq_chat_id, thread_id=cq_thread_id)
        return

    if data.startswith("mem_del_"):
        fname = data[len("mem_del_"):] + ".md"
        fpath = os.path.join(consts.MEMORY_DIR, fname)
        tgapi.answer_callback(cq_id, "Удалено")
        try:
            os.remove(fpath)
        except Exception:
            pass
        memui.remove_from_memory_index(fname)
        tgapi.delete_message(msg_id)
        return

    if data.startswith("mem_edit_"):
        fname = data[len("mem_edit_"):] + ".md"
        fpath = os.path.join(consts.MEMORY_DIR, fname)
        try:
            with open(fpath, "r") as f:
                raw = f.read()
            body = raw.strip()
            if body.startswith("---"):
                parts = body.split("---", 2)
                body = parts[2].strip() if len(parts) >= 3 else body
        except Exception:
            body = ""
        shared.pending_memory_edit = fname
        tgapi.answer_callback(cq_id)
        tgapi.delete_message(msg_id)
        tgapi.send_single(f"✏️ Текущий текст:\n\n{body}\n\nВведи новый текст:", None, chat_id=cq_chat_id, thread_id=cq_thread_id)
        return

    if data.startswith("settings_") or data in ("style_brief", "style_detailed"):
        shared.settings_msg_ids[(cq_chat_id, cq_thread_id)] = msg_id
        s = storage.load_settings()
        changed = True

        if data == "settings_noop":
            tgapi.answer_callback(cq_id)
            return
        elif data == "settings_clear_history":
            storage.reset_history(claude_run.chat_thread_to_topic_id(cq_chat_id, cq_thread_id))
            tgapi.answer_callback(cq_id, "История очищена")
        elif data in ("settings_perm_allow_all", "settings_perm_ask"):
            s["permissions"] = "ask" if data.endswith("_ask") else "allow_all"
            storage.save_settings(s)
            tgapi.answer_callback(cq_id, "Права: спрашивать" if s["permissions"] == "ask" else "Права: разрешать всё")
        elif data == "style_brief":
            s["style"] = "brief"
            storage.save_settings(s)
            tgapi.answer_callback(cq_id, "Стиль: Краткий")
        elif data == "style_detailed":
            s["style"] = "detailed"
            storage.save_settings(s)
            tgapi.answer_callback(cq_id, "Стиль: Подробный")
        elif data.startswith("settings_model_"):
            new_model = data[len("settings_model_"):]
            if new_model not in keyboards.MODEL_LABELS:
                tgapi.answer_callback(cq_id, "Неизвестная модель")
                return
            s["model"] = new_model
            storage.save_settings(s)
            label = keyboards.MODEL_LABELS[new_model]
            tgapi.answer_callback(cq_id, f"Модель: {label}. Перезапускаю...")
            requests.post(f"{core.API}/editMessageText", json={
                "chat_id": cq_chat_id,
                "message_id": msg_id,
                "text": mdfmt.markdown_to_html(f"⚙️ *Настройки Claude*\n\n🤖 Модель переключена на *{label}*\n🔄 Перезапускаю..."),
                "parse_mode": "HTML",
            })
            claude_run.trigger_restart(cq_chat_id, cq_thread_id)
            return
        else:
            tgapi.answer_callback(cq_id)
            return

        settings_text = keyboards.get_settings_text()
        requests.post(f"{core.API}/editMessageText", json={
            "chat_id": cq_chat_id,
            "message_id": msg_id,
            "text": mdfmt.markdown_to_html(settings_text),
            "parse_mode": "HTML",
            "reply_markup": keyboards.get_settings_inline()
        })
        return

    if data == "info_refresh":
        shared.info_msg_ids[(cq_chat_id, cq_thread_id)] = msg_id
        tgapi.answer_callback(cq_id, "Обновляю...")
        def _refresh_info(_cid=cq_chat_id):
            tgapi.edit_message(msg_id, sysinfo.get_info_text(), reply_markup=keyboards.INFO_INLINE, chat_id=_cid)
        threading.Thread(target=_refresh_info, daemon=True).start()
        return

    if data == "info_clear_log":
        shared.info_msg_ids[(cq_chat_id, cq_thread_id)] = msg_id
        try:
            open(consts.BOT_LOG_FILE, "w").close()
            sysinfo._log_error_cache["count"] = 0
            sysinfo._log_error_cache["last"] = ""
            sysinfo._log_error_cache["checked_at"] = time.time()
            tgapi.answer_callback(cq_id, "🧹 Лог очищен")
        except Exception as e:
            tgapi.answer_callback(cq_id, f"Ошибка: {e}")
            return
        def _refresh_info(_cid=cq_chat_id):
            tgapi.edit_message(msg_id, sysinfo.get_info_text(), reply_markup=keyboards.INFO_INLINE, chat_id=_cid)
        threading.Thread(target=_refresh_info, daemon=True).start()
        return

    if data == "info_reminders":
        import time as _time
        with reminders._reminder_lock:
            rems = dict(reminders.active_reminders)
        if not rems:
            tgapi.answer_callback(cq_id, "Нет активных напоминаний")
            return
        lines = ["⏰ *Активные напоминания:*\n"]
        inline_rows = []
        for rid, r in sorted(rems.items()):
            remaining = max(0, int(r["fire_at"] - _time.time()))
            dur = reminders.format_duration(remaining) if remaining > 0 else "сейчас"
            fire_at_str = datetime.fromtimestamp(r["fire_at"]).strftime("%H:%M")
            repeat_info = f" (каждые {reminders.format_duration(r['repeat_secs'])})" if r["repeat_secs"] else ""
            lines.append(f"• _{r['text']}_ — в {fire_at_str} (через {dur}){repeat_info}")
            inline_rows.append([{"text": f"❌ {r['text'][:30]}", "callback_data": f"reminder_cancel_{rid}"}])
        inline_rows.append([{"text": "« Назад", "callback_data": "info_refresh"}])
        requests.post(f"{core.API}/editMessageText", json={
            "chat_id": cq_chat_id,
            "message_id": msg_id,
            "text": mdfmt.markdown_to_html("\n".join(lines)),
            "parse_mode": "HTML",
            "reply_markup": {"inline_keyboard": inline_rows}
        })
        tgapi.answer_callback(cq_id)
        return

    if data == "reminder_add":
        shared.pending_reminder = "new"
        tgapi.answer_callback(cq_id)
        tgapi.send_message(
            "⏰ Введи напоминание:\n`17:00 текст` — по времени суток\n`30м текст` — через 30 минут\n`2ч 15м текст` — через 2 ч 15 мин\n`1д текст` — через день\n\nПовторяющееся: `каждые 1ч текст`",
            None, chat_id=cq_chat_id, thread_id=cq_thread_id
        )
        return

    if data.startswith("reminder_cancel_"):
        rid = int(data.split("_")[-1])
        reminders.cancel_reminder(rid)
        tgapi.answer_callback(cq_id, "Напоминание удалено")
        # Обновляем список
        import time as _time
        with reminders._reminder_lock:
            rems = dict(reminders.active_reminders)
        if not rems:
            requests.post(f"{core.API}/editMessageText", json={
                "chat_id": cq_chat_id,
                "message_id": msg_id,
                "text": "⏰ Активных напоминаний нет.",
                "parse_mode": "HTML",
                "reply_markup": {"inline_keyboard": [[{"text": "« Назад", "callback_data": "info_refresh"}]]}
            })
        else:
            lines = ["⏰ *Активные напоминания:*\n"]
            inline_rows = []
            for rid2, r in sorted(rems.items()):
                remaining = max(0, int(r["fire_at"] - _time.time()))
                dur = reminders.format_duration(remaining) if remaining > 0 else "сейчас"
                fire_at_str = datetime.fromtimestamp(r["fire_at"]).strftime("%H:%M")
                repeat_info = f" (каждые {reminders.format_duration(r['repeat_secs'])})" if r["repeat_secs"] else ""
                lines.append(f"• _{r['text']}_ — в {fire_at_str} (через {dur}){repeat_info}")
                inline_rows.append([{"text": f"❌ {r['text'][:30]}", "callback_data": f"reminder_cancel_{rid2}"}])
            inline_rows.append([{"text": "« Назад", "callback_data": "info_refresh"}])
            requests.post(f"{core.API}/editMessageText", json={
                "chat_id": cq_chat_id,
                "message_id": msg_id,
                "text": mdfmt.markdown_to_html("\n".join(lines)),
                "parse_mode": "HTML",
                "reply_markup": {"inline_keyboard": inline_rows}
            })
        return

    if data.startswith("power_"):
        if data == "power_cancel":
            tgapi.answer_callback(cq_id, "Отменено")
            tgapi.delete_message(msg_id)
            shared.power_msg_ids.pop((cq_chat_id, cq_thread_id), None)
        elif data == "power_reboot_confirm":
            tgapi.answer_callback(cq_id)
            requests.post(f"{core.API}/editMessageText", json={
                "chat_id": cq_chat_id,
                "message_id": msg_id,
                "text": "🔁 Перезагрузить Raspberry Pi?",
                "reply_markup": keyboards.REBOOT_CONFIRM_INLINE
            })
        elif data == "power_off_confirm":
            tgapi.answer_callback(cq_id)
            requests.post(f"{core.API}/editMessageText", json={
                "chat_id": cq_chat_id,
                "message_id": msg_id,
                "text": "⛔ Выключить Raspberry Pi?",
                "reply_markup": keyboards.POWEROFF_CONFIRM_INLINE
            })
        elif data == "power_reboot_do":
            tgapi.answer_callback(cq_id, "Перезагружаю...")
            requests.post(f"{core.API}/editMessageText", json={
                "chat_id": cq_chat_id,
                "message_id": msg_id,
                "text": "🔁 Перезагрузка...",
            })
            shared.power_msg_ids.pop((cq_chat_id, cq_thread_id), None)
            subprocess.Popen(["sudo", "reboot"])
        elif data == "power_off_do":
            tgapi.answer_callback(cq_id, "Выключаю...")
            requests.post(f"{core.API}/editMessageText", json={
                "chat_id": cq_chat_id,
                "message_id": msg_id,
                "text": "⛔ Выключение...",
            })
            shared.power_msg_ids.pop((cq_chat_id, cq_thread_id), None)
            subprocess.Popen(["sudo", "poweroff"])
        elif data == "power_delayed_off":
            tgapi.answer_callback(cq_id)
            requests.post(f"{core.API}/editMessageText", json={
                "chat_id": cq_chat_id,
                "message_id": msg_id,
                "text": "⏱ Через сколько выключить?",
                "reply_markup": keyboards.DELAYED_OFF_INLINE
            })
        elif data.startswith("power_delayed_") and data[14:].isdigit():
            minutes = int(data[14:])
            if shared.delayed_off_timer and shared.delayed_off_timer.is_alive():
                shared.delayed_off_timer.cancel()
            def do_poweroff(_tid=cq_thread_id):
                tgapi.send_message("⛔ Отложенное выключение: время вышло, выключаю...", thread_id=_tid)
                subprocess.Popen(["sudo", "poweroff"])
            shared.delayed_off_timer = threading.Timer(minutes * 60, do_poweroff)
            shared.delayed_off_timer.start()
            tgapi.answer_callback(cq_id, f"Выключу через {minutes} мин")
            requests.post(f"{core.API}/editMessageText", json={
                "chat_id": cq_chat_id,
                "message_id": msg_id,
                "text": f"⏱ Выключение через {minutes} мин.\nДля отмены нажми «Отмена» ниже.",
                "reply_markup": {"inline_keyboard": [[
                    {"text": "❌ Отменить выключение", "callback_data": "power_delayed_cancel"}
                ]]}
            })
            shared.power_msg_ids.pop((cq_chat_id, cq_thread_id), None)
        elif data == "power_delayed_cancel":
            if shared.delayed_off_timer and shared.delayed_off_timer.is_alive():
                shared.delayed_off_timer.cancel()
                shared.delayed_off_timer = None
                tgapi.answer_callback(cq_id, "Отложенное выключение отменено")
                requests.post(f"{core.API}/editMessageText", json={
                    "chat_id": cq_chat_id,
                    "message_id": msg_id,
                    "text": "✅ Отложенное выключение отменено.",
                })
            else:
                tgapi.answer_callback(cq_id, "Нет активного таймера")
                tgapi.delete_message(msg_id)
        return

    if data == "gemma_arena_continue":
        if sysinfo.gemma_arena_running():
            tgapi.answer_callback(cq_id, "Арена уже запущена")
            return
        tgapi.answer_callback(cq_id, "Продолжаю арену…")
        stop_file = os.path.join(consts.GEMMA_DIR, "STOP")
        if os.path.exists(stop_file):
            os.remove(stop_file)
        # Через systemd-run (не Popen!) — отдельный cgroup, чтобы перезапуск бота
        # (claude-telegram, KillMode=control-group) не убивал арену за компанию.
        subprocess.run([
            "sudo", "systemd-run", f"--unit={sysinfo.GEMMA_ARENA_UNIT}", "--collect",
            f"--working-directory={consts.GEMMA_DIR}",
            f"--uid={consts.USER}", f"--gid={consts.USER}",
            f"--property=StandardOutput=append:{consts.GEMMA_ARENA_LOG}",
            f"--property=StandardError=append:{consts.GEMMA_ARENA_LOG}",
            "python3", consts.GEMMA_ARENA_SCRIPT,
        ])
        return

    if data == "gemma_arena_go":
        tgapi.answer_callback(cq_id, "Стартуем!")
        open(os.path.join(consts.GEMMA_DIR, "START"), "w").close()
        return

    if data == "fan_auto":
        sysinfo.fan_set(2)
        text = "🔃 Авто режим включён"
    elif data == "fan_0":
        sysinfo.fan_set(1, 0)
        text = "⏹ Вентилятор остановлен"
    elif data == "fan_50":
        sysinfo.fan_set(1, 128)
        text = "💨 Вентилятор: 50%"
    elif data == "fan_100":
        sysinfo.fan_set(1, 255)
        text = "💨 Вентилятор: 100%"
    else:
        tgapi.answer_callback(cq_id)
        return

    tgapi.answer_callback(cq_id, text)
    # Игнорируем нажатия на устаревшие сообщения
    _cur_fan_msg = shared.fan_msg_ids.get((cq_chat_id, cq_thread_id))
    if _cur_fan_msg and msg_id != _cur_fan_msg:
        return
    status = sysinfo.fan_status()
    requests.post(f"{core.API}/editMessageText", json={
        "chat_id": cq_chat_id,
        "message_id": msg_id,
        "text": f"🌀 Вентилятор: {status}",
        "reply_markup": keyboards.FAN_INLINE
    })
