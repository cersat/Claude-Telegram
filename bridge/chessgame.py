"""Шахматы: логика, рендер, Stockfish-анализ."""

import subprocess
import random
import shutil
from collections import Counter
import chess


# Состояние шахматной игры на каждую тему/чат (chat_id, thread_id) — раньше была одна
# глобальная переменная на все чаты, и игра в одной теме молча затиралась игрой в другой.
chess_games = {}


# ============================================================
# ШАХМАТЫ
# ============================================================
CHESS_UNICODE = {
    'WK': '♚', 'WQ': 'Ф', 'WR': 'Л', 'WB': 'С', 'WN': 'К', 'WP': 'П',
    'BK': '♔', 'BQ': 'ф', 'BR': 'л', 'BB': 'с', 'BN': 'к', 'BP': 'п',
}
CHESS_PIECE_NAMES = {
    'K': 'Король', 'Q': 'Ферзь', 'R': 'Ладья', 'B': 'Слон', 'N': 'Конь', 'P': 'Пешка'
}

def chess_initial_board():
    board = [[None]*8 for _ in range(8)]
    board[0] = ['BR','BN','BB','BQ','BK','BB','BN','BR']
    board[1] = ['BP','BP','BP','BP','BP','BP','BP','BP']
    board[6] = ['WP','WP','WP','WP','WP','WP','WP','WP']
    board[7] = ['WR','WN','WB','WQ','WK','WB','WN','WR']
    return board

def chess_color(piece):
    return piece[0] if piece else None

def chess_raw_moves(board, r, c):
    """Ходы без проверки шаха."""
    piece = board[r][c]
    if not piece: return []
    color, kind = piece[0], piece[1]
    opp = 'B' if color == 'W' else 'W'
    moves = []

    def ok(tr, tc):
        if not (0 <= tr < 8 and 0 <= tc < 8): return False
        t = board[tr][tc]
        return t is None or t[0] == opp

    if kind == 'P':
        d = -1 if color == 'W' else 1
        start = 6 if color == 'W' else 1
        nr = r + d
        if 0 <= nr < 8 and board[nr][c] is None:
            moves.append((nr, c))
            nr2 = r + 2*d
            if r == start and 0 <= nr2 < 8 and board[nr2][c] is None:
                moves.append((nr2, c))
        for dc in (-1, 1):
            tr, tc = r+d, c+dc
            if 0 <= tr < 8 and 0 <= tc < 8 and board[tr][tc] and board[tr][tc][0] == opp:
                moves.append((tr, tc))
    elif kind == 'N':
        for dr, dc in [(-2,-1),(-2,1),(-1,-2),(-1,2),(1,-2),(1,2),(2,-1),(2,1)]:
            if ok(r+dr, c+dc): moves.append((r+dr, c+dc))
    elif kind == 'K':
        for dr in (-1,0,1):
            for dc in (-1,0,1):
                if (dr or dc) and ok(r+dr, c+dc): moves.append((r+dr, c+dc))
    elif kind in ('R','B','Q'):
        dirs = []
        if kind in ('R','Q'): dirs += [(0,1),(0,-1),(1,0),(-1,0)]
        if kind in ('B','Q'): dirs += [(1,1),(1,-1),(-1,1),(-1,-1)]
        for dr, dc in dirs:
            tr, tc = r+dr, c+dc
            while 0 <= tr < 8 and 0 <= tc < 8:
                t = board[tr][tc]
                if t is None:
                    moves.append((tr, tc)); tr += dr; tc += dc
                elif t[0] == opp:
                    moves.append((tr, tc)); break
                else:
                    break
    return moves

def chess_in_check(board, color):
    king = next(((r,c) for r in range(8) for c in range(8) if board[r][c] == color+'K'), None)
    if not king: return False
    opp = 'B' if color == 'W' else 'W'
    return any(king in chess_raw_moves(board, r, c)
               for r in range(8) for c in range(8) if chess_color(board[r][c]) == opp)

def chess_legal_moves(board, r, c, castling=None):
    """Ходы с учётом шаха (castling — dict прав рокировки)."""
    piece = board[r][c]
    if not piece: return []
    color = piece[0]
    result = []
    for tr, tc in chess_raw_moves(board, r, c):
        nb = [row[:] for row in board]
        nb[tr][tc] = nb[r][c]
        nb[r][c] = None
        if not chess_in_check(nb, color):
            result.append((tr, tc))
    # Рокировка: доступна только для короля
    if castling and piece[1] == 'K':
        row = 7 if color == 'W' else 0
        if r == row and c == 4 and not chess_in_check(board, color):
            # Рокировка в сторону короля (короткая)
            if castling.get(color, {}).get('K'):
                if board[row][7] == color+'R' and board[row][5] is None and board[row][6] is None:
                    nb5 = [x[:] for x in board]; nb5[row][5] = color+'K'; nb5[row][4] = None
                    nb6 = [x[:] for x in board]; nb6[row][6] = color+'K'; nb6[row][4] = None
                    if not chess_in_check(nb5, color) and not chess_in_check(nb6, color):
                        result.append((row, 6))
            # Рокировка в сторону ферзя (длинная)
            if castling.get(color, {}).get('Q'):
                if board[row][0] == color+'R' and board[row][1] is None and board[row][2] is None and board[row][3] is None:
                    nb3 = [x[:] for x in board]; nb3[row][3] = color+'K'; nb3[row][4] = None
                    nb2 = [x[:] for x in board]; nb2[row][2] = color+'K'; nb2[row][4] = None
                    if not chess_in_check(nb3, color) and not chess_in_check(nb2, color):
                        result.append((row, 2))
    return result

def chess_all_legal(board, color, castling=None):
    return [(r,c,tr,tc)
            for r in range(8) for c in range(8)
            if chess_color(board[r][c]) == color
            for tr,tc in chess_legal_moves(board, r, c, castling)]

def chess_update_castling(castling, board, color, fr, fc, tr, tc):
    """Обновляет права рокировки после хода (fr,fc)->(tr,tc)."""
    piece = board[tr][tc]  # уже перемещена
    if piece and piece[1] == 'K':
        castling[color]['K'] = False
        castling[color]['Q'] = False
    # Если ладья ушла с начальной позиции
    row = 7 if color == 'W' else 0
    if fr == row and fc == 0: castling[color]['Q'] = False
    if fr == row and fc == 7: castling[color]['K'] = False
    # Если ладья противника была съедена
    opp = 'B' if color == 'W' else 'W'
    opp_row = 0 if color == 'W' else 7
    if tr == opp_row and tc == 0: castling[opp]['Q'] = False
    if tr == opp_row and tc == 7: castling[opp]['K'] = False

def chess_ai_move(board, castling=None):
    """Ход чёрных: предпочитает взятия ценных фигур."""
    values = {'P':1,'N':3,'B':3,'R':5,'Q':9,'K':0}
    moves = chess_all_legal(board, 'B', castling)
    if not moves: return None
    captures = [(r,c,tr,tc) for r,c,tr,tc in moves if board[tr][tc]]
    if captures:
        captures.sort(key=lambda m: values.get(board[m[2]][m[3]][1] if board[m[2]][m[3]] else 'P', 0), reverse=True)
        best_val = values.get(board[captures[0][2]][captures[0][3]][1], 0)
        best = [m for m in captures if values.get(board[m[2]][m[3]][1], 0) == best_val]
        return random.choice(best)
    return random.choice(moves)

def chess_render_keyboard(game):
    if game.get('pending_promotion'):
        return {'inline_keyboard': [[
            {'text': 'Ф Ферзь',  'callback_data': 'chess_promo_Q'},
            {'text': 'Л Ладья',  'callback_data': 'chess_promo_R'},
            {'text': 'С Слон',   'callback_data': 'chess_promo_B'},
            {'text': 'К Конь',   'callback_data': 'chess_promo_N'},
        ]]}
    board = game['board']
    selected = game.get('selected')
    valid = set(game.get('valid_moves', []))
    rows = []
    for r in range(8):
        row_btns = []
        for c in range(8):
            piece = board[r][c]
            text = CHESS_UNICODE.get(piece, '·')
            if (r, c) == selected:
                text = f'[{text}]'
            elif (r, c) in valid:
                text = f'×{text}' if piece else '○'
            row_btns.append({'text': text, 'callback_data': f'chess_{r}_{c}'})
        rows.append(row_btns)
    if game.get('game_over') and game.get('moves'):
        rows.append([
            {'text': '🔍 Разобрать партию', 'callback_data': 'chess_analyze'},
            {'text': '🔄 Новая игра', 'callback_data': 'chess_new'},
            {'text': '❌ Закрыть', 'callback_data': 'chess_close'},
        ])
    else:
        rows.append([
            {'text': '🏳 Сдаться', 'callback_data': 'chess_resign'},
            {'text': '🔄 Новая игра', 'callback_data': 'chess_new'},
            {'text': '❌ Закрыть', 'callback_data': 'chess_close'},
        ])
    return {'inline_keyboard': rows}

def chess_status_text(game):
    if game.get('game_over'):
        return f"♟ *Шахматы*\n{game.get('status','Игра окончена')}"
    if game.get('pending_promotion'):
        return '♟ *Шахматы*\nПешка дошла до конца! В кого превратить?'
    turn = game['turn']
    lines = ['♟ *Шахматы* — ты играешь белыми']
    sel = game.get('selected')
    if sel:
        piece = game['board'][sel[0]][sel[1]]
        pname = CHESS_PIECE_NAMES.get(piece[1] if piece else '', '')
        lines.append(f"Выбран: {CHESS_UNICODE.get(piece,'')} {pname} — выбери куда пойти")
    elif turn == 'W':
        lines.append('Выбери свою фигуру')
    else:
        lines.append('Хожу...')
    st = game.get('status','')
    if st: lines.append(st)
    return '\n'.join(lines)

def chess_apply_ai_move(game):
    """Применяет ход ИИ без обновления UI."""
    castling = game.get('castling', {'W': {'K': True, 'Q': True}, 'B': {'K': True, 'Q': True}})
    mv = chess_ai_move(game['board'], castling)
    if mv is None:
        if chess_in_check(game['board'], 'B'):
            game['status'] = '♚ Мат! Ты победил!'
        else:
            game['status'] = '🤝 Пат!'
        game['game_over'] = True
    else:
        r, c, tr, tc = mv
        board = game['board']
        promo = board[r][c] == 'BP' and tr == 7
        game.setdefault('moves', []).append(chess_sq(r, c) + chess_sq(tr, tc) + ('q' if promo else ''))
        # Рокировка ИИ: чёрный король ходит на 2 клетки
        if board[r][c] == 'BK' and abs(tc - c) == 2:
            board[tr][tc] = 'BK'
            board[r][c] = None
            if tc == 6:  # короткая рокировка
                board[0][5] = 'BR'; board[0][7] = None
            else:        # длинная рокировка
                board[0][3] = 'BR'; board[0][0] = None
        else:
            board[tr][tc] = board[r][c]
            board[r][c] = None
            if board[tr][tc] == 'BP' and tr == 7:
                board[tr][tc] = 'BQ'
        chess_update_castling(castling, board, 'B', r, c, tr, tc)
        game['turn'] = 'W'
        st = ''
        if chess_in_check(board, 'W'):
            if not chess_all_legal(board, 'W', castling):
                game['game_over'] = True
                game['status'] = '♔ Мат! Чёрные победили!'
                st = game['status']
            else:
                st = '⚠️ Шах!'
        elif not chess_all_legal(board, 'W', castling):
            game['game_over'] = True
            game['status'] = '🤝 Пат!'
            st = game['status']
        game['status'] = st
    game['selected'] = None
    game['valid_moves'] = []

def chess_sq(r, c):
    return chr(ord('a') + c) + str(8 - r)

def chess_stockfish_path():
    return shutil.which("stockfish") or "/usr/games/stockfish"

def chess_classify(loss, is_best):
    if loss <= 10:
        return '🌟 Лучший' if is_best else '✅ Отличный'
    if loss <= 40:
        return '👍 Хороший'
    if loss <= 90:
        return '🤔 Неточность'
    if loss <= 200:
        return '❌ Ошибка'
    return '‼️ Зевок'

def chess_stockfish_start():
    proc = subprocess.Popen(
        [chess_stockfish_path()],
        stdin=subprocess.PIPE, stdout=subprocess.PIPE,
        text=True, bufsize=1
    )
    proc.stdin.write("uci\n"); proc.stdin.flush()
    while True:
        line = proc.stdout.readline()
        if not line or line.strip() == "uciok":
            break
    proc.stdin.write("isready\n"); proc.stdin.flush()
    while True:
        line = proc.stdout.readline()
        if not line or line.strip() == "readyok":
            break
    return proc

def chess_stockfish_quit(proc):
    try:
        proc.stdin.write("quit\n"); proc.stdin.flush()
    except Exception:
        pass
    proc.terminate()

def chess_uci_eval(proc, fen, depth):
    """Оценка позиции движком. Возвращает (cp_с_точки_зрения_белых, лучший_ход_uci)."""
    proc.stdin.write(f"position fen {fen}\n"); proc.stdin.flush()
    proc.stdin.write(f"go depth {depth}\n"); proc.stdin.flush()
    last_score, is_mate, best_move = None, False, None
    while True:
        line = proc.stdout.readline()
        if not line:
            break
        line = line.strip()
        if line.startswith("info") and " score " in line:
            toks = line.split()
            if "score" in toks:
                si = toks.index("score")
                kind, val = toks[si+1], int(toks[si+2])
                last_score, is_mate = val, (kind == "mate")
        if line.startswith("bestmove"):
            parts = line.split()
            best_move = parts[1] if len(parts) > 1 and parts[1] != "(none)" else None
            break
    turn_white = " w " in fen
    if last_score is None:
        cp = 0
    elif is_mate:
        cp = 100000 - abs(last_score) * 100
        cp = cp if last_score > 0 else -cp
    else:
        cp = last_score
    cp_white = cp if turn_white else -cp
    return cp_white, best_move

def chess_analyze_game(moves_uci, depth=12):
    """Прогоняет список ходов (UCI) через Stockfish и оценивает каждый ход."""
    board = chess.Board()
    records = []
    proc = chess_stockfish_start()
    try:
        prev_cp, prev_best = chess_uci_eval(proc, board.fen(), depth)
        for uci in moves_uci:
            move = chess.Move.from_uci(uci)
            mover_white = board.turn == chess.WHITE
            try:
                san = board.san(move)
            except Exception:
                san = uci
            is_best = (prev_best == uci)
            board.push(move)
            if board.is_game_over():
                if board.is_checkmate():
                    cp_after = 100000 if mover_white else -100000
                else:
                    cp_after = 0
                next_best = None
            else:
                cp_after, next_best = chess_uci_eval(proc, board.fen(), depth)
            loss = (prev_cp - cp_after) if mover_white else (cp_after - prev_cp)
            loss = max(0, loss)
            records.append({
                'white': mover_white,
                'san': san,
                'loss': loss,
                'label': chess_classify(loss, is_best),
            })
            prev_cp = cp_after
            prev_best = next_best
    finally:
        chess_stockfish_quit(proc)
    return records

def chess_format_report(records):
    if not records:
        return "📊 *Разбор партии*\nХодов не было."
    lines = ["📊 *Разбор партии*", ""]
    i = 0
    move_no = 1
    while i < len(records):
        rec_w = records[i]
        parts = [f"{move_no}. {rec_w['san']} — {rec_w['label']}"]
        i += 1
        if i < len(records) and not records[i]['white']:
            rec_b = records[i]
            parts.append(f"{rec_b['san']} — {rec_b['label']}")
            i += 1
        lines.append('  '.join(parts))
        move_no += 1
    cnt = Counter(r['label'] for r in records)
    lines.append("")
    lines.append("*Итого:*")
    for label in ['🌟 Лучший', '✅ Отличный', '👍 Хороший', '🤔 Неточность', '❌ Ошибка', '‼️ Зевок']:
        if cnt.get(label):
            lines.append(f"{label}: {cnt[label]}")
    worst = max(records, key=lambda r: r['loss'])
    if worst['loss'] > 0:
        side = 'Белые' if worst['white'] else 'Чёрные'
        lines.append("")
        lines.append(f"Худший ход: {side} {worst['san']} (потеря {worst['loss']} centipawns)")
    text = '\n'.join(lines)
    if len(text) > 3900:
        text = text[:3900] + "\n…(обрезано)"
    return text
