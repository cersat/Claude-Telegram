"""Перелогин Claude Code прямо из Telegram.

Когда CLI отвечает ошибкой авторизации («OAuth session expired…»), бот сам поднимает в tmux интерактивный
`claude`, запускает /login, вытаскивает со экрана ссылку и присылает её в чат. Следующее сообщение
владельца в этом чате считается кодом из браузера: оно вставляется в tmux, а сообщение с кодом удаляется.
После «Login successful» tmux-сессия закрывается."""

import re
import subprocess
import threading
import time

from bridge import consts, shared, tgapi

SESSION = "bot_login"
PENDING_TTL = 15 * 60  # сколько живёт ожидание кода

_AUTH_ERR_RE = re.compile(
    r"failed to authenticate|oauth session expired|oauth token has expired|please run /login"
    r"|invalid api key|authentication_error|could not be refreshed",
    re.IGNORECASE,
)
_URL_RE = re.compile(r"https://\S*oauth/authorize\S*")
_lock = threading.Lock()
# Код из браузера — один «слой» без пробелов: буквы/цифры и - _ # . ~ + / = % : не короче 20 символов.
# Всё остальное (обычная фраза, команда) кодом не считается: его не удаляют и не вставляют в tmux.
_CODE_RE = re.compile(r"^[A-Za-z0-9_\-#.~+/=%:]{20,}$")


def looks_like_code(text):
    return bool(text) and bool(_CODE_RE.match(text.strip()))


def is_auth_error(text):
    """Похож ли ответ CLI на ошибку авторизации (короткое сообщение об ошибке, а не обычный ответ)."""
    return bool(text) and len(text) < 400 and bool(_AUTH_ERR_RE.search(text))


def _tmux(*args, timeout=15):
    try:
        return subprocess.run(["tmux", *args], capture_output=True, text=True, timeout=timeout)
    except Exception as e:  # tmux не установлен и т.п.
        return subprocess.CompletedProcess(args, 1, "", str(e))


def _screen():
    r = _tmux("capture-pane", "-t", SESSION, "-p", "-J")
    return r.stdout if r.returncode == 0 else ""


def _wait_for(pattern, timeout, poll=0.7):
    """Ждёт появления pattern (regex) на экране tmux; возвращает экран или None."""
    end = time.time() + timeout
    while time.time() < end:
        scr = _screen()
        if re.search(pattern, scr):
            return scr
        time.sleep(poll)
    return None


def _kill():
    _tmux("kill-session", "-t", SESSION)


def _extract_url(screen):
    """URL на экране разорван переносами строк приложения: склеиваем строки от 'https://' до 'Paste code'."""
    lines = screen.split("\n")
    start = next((i for i, l in enumerate(lines) if "https://" in l and "oauth" in l), None)
    if start is None:
        return None
    parts = []
    for l in lines[start:]:
        if "Paste code" in l or not l.strip():
            break
        parts.append(l.strip())
    m = _URL_RE.search("".join(parts))
    return m.group(0) if m else None


def _open_login_url():
    """Запускает claude в tmux, доходит до /login и возвращает (url, экран_для_диагностики)."""
    _kill()
    r = _tmux("new-session", "-d", "-s", SESSION, "-c", consts.HOME_DIR,
              "-x", "800", "-y", "50",
              "env -u CLAUDE_CODE_OAUTH_TOKEN -u ANTHROPIC_API_KEY claude")
    if r.returncode != 0:
        return None, f"tmux: {r.stderr.strip()}"
    scr = _wait_for(r"Yes, I trust this folder|Try \"|shortcuts|auto mode", 25)
    if scr and "Yes, I trust this folder" in scr:
        _tmux("send-keys", "-t", SESSION, "Down", "Enter")
        scr = _wait_for(r"Try \"|shortcuts|auto mode", 15)
    if not scr:
        return None, _screen()
    _tmux("send-keys", "-t", SESSION, "/login", "Enter")
    scr = _wait_for(r"Select login method|Browser didn't open", 15)
    if not scr:
        return None, _screen()
    if "Select login method" in scr:
        _tmux("send-keys", "-t", SESSION, "Enter")  # 1. Claude account with subscription
        scr = _wait_for(r"Paste code here", 20)
        if not scr:
            return None, _screen()
    url = _extract_url(scr)
    return url, scr


def _say(state, text):
    tgapi.send_single(text, chat_id=state["chat_id"], thread_id=state["thread_id"])


def begin(chat_id, thread_id):
    """Вызывается при ошибке авторизации: поднимает /login в tmux и шлёт ссылку в чат. Идемпотентна:
    пока ожидание кода не истекло, повторная ошибка (из другого запуска) новую сессию не создаёт."""
    with _lock:
        cur = shared.pending_login
        if cur and time.time() - cur["t"] < PENDING_TTL:
            _say(cur, "🔑 Вход в Claude ещё не завершён — пришли код из браузера (или /cancel).") if (
                cur["chat_id"] != chat_id or cur["thread_id"] != thread_id) else None
            return
        shared.pending_login = {"chat_id": chat_id, "thread_id": thread_id, "t": time.time(), "busy": True}
    state = shared.pending_login
    _say(state, "🔑 Сессия Claude истекла. Готовлю вход…")

    def work():
        url, scr = _open_login_url()
        if not url:
            tail = "\n".join([l for l in (scr or "").split("\n") if l.strip()][-8:])
            with _lock:
                shared.pending_login = None
            _kill()
            _say(state, "❌ Не удалось получить ссылку для входа.\n```\n" + tail[:1500] + "\n```")
            return
        state["busy"] = False
        state["t"] = time.time()
        _say(state, "🔑 *Нужен перелогин.* Открой ссылку, войди в аккаунт и пришли мне следующим сообщением "
                    "код из браузера. Сообщение с кодом я удалю. Отмена: /cancel.\n\n" + url)

    threading.Thread(target=work, daemon=True).start()


def cancel():
    """Отменяет ожидание кода. Возвращает True, если оно было."""
    with _lock:
        had = shared.pending_login is not None
        shared.pending_login = None
    if had:
        _kill()
    return had


def handle_incoming(chat_id, thread_id, message_id, text):
    """Если ждём код и сообщение пришло в тот же чат/тему — вставляет его в tmux. Возвращает True,
    когда сообщение поглощено (дальше его обрабатывать нельзя: это секрет)."""
    state = shared.pending_login
    if not state or state["chat_id"] != chat_id or state["thread_id"] != thread_id:
        return False
    if time.time() - state["t"] > PENDING_TTL:
        cancel()
        return False
    if state.get("busy") or not text or not text.strip():
        return state.get("busy", False)
    if not looks_like_code(text):
        # не код: ничего не удаляем и в tmux не вставляем; Claude сейчас всё равно не отвечает (вход истёк)
        _say(state, "🔑 Это не похоже на код входа (одно слово без пробелов, от 20 символов). "
                    "Пришли код из браузера или /cancel.")
        return True
    code = text.strip()
    with _lock:
        shared.pending_login = None
    try:
        tgapi.delete_message(message_id, chat_id=chat_id)  # код — секрет, убираем из чата
    except Exception:
        pass

    def work():
        _tmux("send-keys", "-t", SESSION, "-l", code)
        _tmux("send-keys", "-t", SESSION, "Enter")
        scr = _wait_for(r"Login successful|Login failed|OAuth error|[Ii]nvalid|expired", 45)
        ok = bool(scr and "Login successful" in scr)
        tail = "\n".join([l for l in (scr or _screen()).split("\n") if l.strip()][-6:])
        _kill()
        if ok:
            _say(state, "✅ Вход выполнен. Можно писать дальше (последний запрос повтори).")
        else:
            _say(state, "❌ Вход не удался.\n```\n" + tail[:1200] + "\n```\nПовтори запрос — ссылка придёт снова.")

    threading.Thread(target=work, daemon=True).start()
    return True
