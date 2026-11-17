"""История переписки из самого Telegram, а не из файлов на диске.

В Bot API нет метода «прочитать историю» (а у MTProto getHistory боту закрыт: BOT_METHOD_INVALID — проверено,
это запрет на стороне Telegram, правкой Bot API-сервера его не обойти). Зато боту доступно чтение сообщений
ПО ID (channels.getMessages), даже очень старых. Поэтому история темы собирается так: берём блоки по 100 идущих
подряд id группы от самого свежего вниз и оставляем сообщения нужной темы.

Подключение — Telethon с входом по токену бота (отдельная MTProto-сессия, файл tg_history_<имя>.session; у бота
может быть несколько сессий, Bot API-серверу она не мешает). Нужны api_id и api_hash (my.telegram.org)."""

import asyncio
import atexit
import json
import os
import re
import threading

import logging

from bridge import consts

logging.getLogger("telethon").setLevel(logging.WARNING)     # не засорять stdout/stderr (MCP-сервер говорит по stdio)

MAX_SCAN = 3000          # сколько id (сообщений группы) просматривать назад в поисках записей темы
BLOCK = 100              # столько id за один запрос (лимит Telegram)
TIMEOUT = 40             # секунд на всю операцию чтения
# карточки инструментов и служебные сообщения бота: завершённые (✅/⚠️/🛑 …) и ещё идущие (значок вызова без отметки)
_NOISE = re.compile(r"^(?:(?:✅|⚠️|🛑|⏳|🎤|🔐|♾|⌛|⏱)\s|(?:💻|📖|📝|✏️|🔍|🌐|🤖|💡|⏰|📃|🦞|🔧|📁|✉️|📅|📚|📄|🔌)(?:\s|$))")
_THINK = re.compile(r"^Думал [^\n]*\n+")


class _Reader:
    """Одна MTProto-сессия бота в отдельном потоке с собственным asyncio-циклом (бот и MCP-сервер — разные процессы,
    у каждого своя сессия: один auth key нельзя использовать из двух процессов одновременно)."""

    def __init__(self, name):
        self.name = name
        self._loop = None
        self._client = None
        self._lock = threading.Lock()

    def _start(self):
        from telethon import TelegramClient
        api_id, api_hash = consts.telegram_api_credentials()
        from secrets_local import TELEGRAM_BOT_TOKEN
        self._loop = asyncio.new_event_loop()
        threading.Thread(target=self._loop.run_forever, daemon=True).start()
        # Файл сессии — только у бота (он один). У остальных (MCP-серверы: по процессу на каждый запуск Claude, часто
        # параллельно) сессия в памяти: общий файл SQLite блокировался бы ("database is locked"), а один auth key нельзя
        # держать в нескольких процессах.
        if self.name == "bot":
            session = os.path.join(consts.BRIDGE_DIR, "tg_history_bot")
        else:
            from telethon.sessions import StringSession
            session = StringSession()

        async def connect():
            c = TelegramClient(session, api_id, api_hash, flood_sleep_threshold=0)  # FloodWait — ошибкой, не сном
            await c.start(bot_token=TELEGRAM_BOT_TOKEN)
            return c
        self._client = asyncio.run_coroutine_threadsafe(connect(), self._loop).result(TIMEOUT)
        atexit.register(self.close)

    def close(self):
        """Корректно закрывает соединение при выходе (иначе Telethon сыплет «Task was destroyed but it is pending»)."""
        if self._client is not None and self._loop is not None and self._loop.is_running():
            try:
                asyncio.run_coroutine_threadsafe(self._client.disconnect(), self._loop).result(5)
            except Exception:
                pass
            self._client = None

    def run(self, coro_fn):
        """Выполняет coro_fn(client) в цикле читателя; ошибки подключения пробрасываются вызывающему."""
        with self._lock:
            if self._client is None:
                self._start()
        fut = asyncio.run_coroutine_threadsafe(coro_fn(self._client), self._loop)
        try:
            return fut.result(TIMEOUT)
        except Exception:
            fut.cancel()      # не оставляем зависшее чтение работать в фоне
            raise


_readers = {}


def reader(name="bot"):
    if name not in _readers:
        _readers[name] = _Reader(name)
    return _readers[name]


def available():
    return consts.HISTORY_FROM_TELEGRAM


# ── сообщения → строки истории ────────────────────────────────────────────────

def _topic_of(m):
    """thread_id темы форума, в которой лежит сообщение (None — General)."""
    rt = getattr(m, "reply_to", None)
    if rt is not None and getattr(rt, "forum_topic", False):
        return rt.reply_to_top_id or rt.reply_to_msg_id
    return None


def _line(m):
    """'U|2026-10-27 18:56|текст' / 'C|…' — формат прежних файлов истории (его понимает build_prompt) или None."""
    if getattr(m, "action", None) is not None:
        return None
    text = (getattr(m, "message", "") or "").strip()
    if not text:
        kind = type(getattr(m, "media", None)).__name__.replace("MessageMedia", "").lower()
        text = f"[{kind}]" if kind and kind != "nonetype" else ""
    if not text:
        return None
    out = bool(getattr(m, "out", False))
    if out:
        text = _THINK.sub("", text, count=1).strip()
        if _NOISE.match(text) or not text:
            return None
        text = text[:300]
    else:
        text = text[:500]
    ts = m.date.astimezone().strftime("%Y-%m-%d %H:%M")
    return f"{'C' if out else 'U'}|{ts}|{text.replace(chr(10), ' ')}"


async def _get(client, entity, ids):
    """get_messages с одним ожиданием FLOOD_WAIT (до 20 с): лимит частоты общий для аккаунта бота, его же
    использует фоновая раскладка индекса."""
    from telethon import errors
    for attempt in (0, 1, 2):
        try:
            return await client.get_messages(entity, ids=ids)
        except errors.FloodWaitError as e:
            if e.seconds > 20 or attempt == 2:
                raise
            await asyncio.sleep(e.seconds + 1)


async def _fetch(client, entity, ids):
    """Сообщения по списку id (блоками по 100) → [(id, строка)] только для записей, пригодных для истории."""
    out = []
    for k in range(0, len(ids), BLOCK):
        for m in await _get(client, entity, ids[k:k + BLOCK]):
            if m is None or getattr(m, "empty", False):
                continue
            line = _line(m)
            if line:
                out.append((m.id, line))
    return out


async def _read(client, chat_id, thread_id, topic_id, limit, hi, floor, exclude_id=None):
    """Берёт id темы из индекса с конца блоками по 100, пока не наберётся limit записей: большая часть id — карточки
    инструментов бота (в историю не входят), поэтому нужно заранее неизвестное число блоков."""
    entity = await client.get_input_entity(chat_id)
    ids = [i for i in index(topic_id) if floor < i <= hi and i != exclude_id]
    got = []
    while ids and len(got) < limit:
        chunk, ids = ids[-BLOCK:], ids[:-BLOCK]
        got.extend(await _fetch(client, entity, chunk))
    got.sort()
    return [line for _, line in got[-limit:]]


# ── индекс id сообщений по темам (содержимого нет — только номера; тексты хранит Telegram) ──
_index_lock = threading.Lock()
_index_cache = None
INDEX_CAP = 400          # сколько последних id помнить на тему


_index_mtime = None


def _index_load():
    """Индекс из файла; перечитывается, если файл изменил другой процесс (бот и MCP-сервер — разные процессы)."""
    global _index_cache, _index_mtime
    try:
        mt = os.path.getmtime(consts.HISTORY_INDEX_FILE)
    except FileNotFoundError:
        mt = None
    if _index_cache is None or mt != _index_mtime:
        raw = _load_json(consts.HISTORY_INDEX_FILE)
        # _meta*: служебное состояние (словарь); испорченное (не словарь) сбрасываем — достройка начнёт этот блок заново
        _index_cache = {k: ((v if isinstance(v, dict) else {}) if k.startswith("_meta") else sorted(set(v)))
                        for k, v in raw.items()}
        _index_mtime = mt
    return _index_cache


def index(topic_id):
    """Известные id сообщений темы. Бот держит индекс в памяти и сам дописывает; другие процессы (MCP) читают файл."""
    with _index_lock:
        return list(_index_load().get(str(topic_id), []))


def _write_index(idx):
    global _index_mtime
    tmp = consts.HISTORY_INDEX_FILE + ".tmp"
    with open(tmp, "w") as f:
        json.dump(idx, f)
    os.replace(tmp, consts.HISTORY_INDEX_FILE)
    _index_mtime = os.path.getmtime(consts.HISTORY_INDEX_FILE)


def note_many(topic_id, ids):
    with _index_lock:
        idx = _index_load()
        cur = set(idx.get(str(topic_id), []))
        cur.update(int(i) for i in ids)
        idx[str(topic_id)] = sorted(cur)[-INDEX_CAP:]
        _write_index(idx)


def note(topic_id, msg_id):
    """Бот запоминает id каждого входящего и исходящего сообщения темы (вызывается из tgapi)."""
    if consts.HISTORY_FROM_TELEGRAM and topic_id is not None and msg_id:
        try:
            note_many(topic_id, [msg_id])
        except Exception as e:
            print(f"[tghistory] индекс: {e}")


# ── соответствие topic_id бота ↔ (чат, тема) и служебное состояние ────────────

def resolve(topic_id):
    """'group_1978' → (chat_id, 1978); 'group_general' → (chat_id, None); 'dm' → (id владельца, None); остальное → None."""
    from secrets_local import ALLOWED_USER_ID, GROUP_CHAT_ID
    t = str(topic_id)
    if t == "dm":
        return ALLOWED_USER_ID, None      # личный чат с владельцем: сообщения лежат в «ящике» самого бота, читаются по id
    if t == "group_general":
        return GROUP_CHAT_ID, None
    if t.startswith("group_") and t[6:].isdigit():
        return GROUP_CHAT_ID, int(t[6:])
    return None


def _load_json(path):
    try:
        with open(path) as f:
            return json.load(f)
    except Exception:
        return {}


def newest_id(topic_id=None):
    """Самый свежий известный id сообщения: темы (последнее входящее) или группы в целом."""
    last = _load_json(consts.LAST_MSG_FILE)
    if topic_id is not None and str(topic_id) in last:
        return int(last[str(topic_id)])
    return max([int(v) for v in last.values()] + [0])


def cutoff(topic_id):
    return int(_load_json(consts.HISTORY_CUTOFF_FILE).get(str(topic_id), 0))


def set_cutoff(topic_id, msg_id):
    data = _load_json(consts.HISTORY_CUTOFF_FILE)
    data[str(topic_id)] = int(msg_id)
    with open(consts.HISTORY_CUTOFF_FILE, "w") as f:
        json.dump(data, f)


def get_lines(topic_id, limit=60, exclude_id=None, name="bot"):
    """Последние limit записей темы (старые сверху). exclude_id — id сообщения, которое сейчас обрабатывается (оно уже
    есть в промпте, в историю не входит). Ошибка сети/Telegram → [] (бот работает дальше без истории, а не падает).
    Откуда id: индекс (см. ниже) — бот пополняет его вживую, а старые сообщения один раз раскладывает backfill_loop()."""
    target = resolve(topic_id)
    if target is None:
        return []
    chat_id, thread_id = target
    hi = 10 ** 12   # всё известное, вплоть до самого свежего
    try:
        return reader(name).run(lambda c: _read(c, chat_id, thread_id, topic_id, limit, hi, cutoff(topic_id), exclude_id))
    except Exception as e:
        print(f"[tghistory] не удалось прочитать историю {topic_id}: {type(e).__name__}: {e}")
        return []


# ── разовая раскладка старых сообщений по темам (фон бота) ─────────────────────

def _topic_key(m):
    th = _topic_of(m)
    return "group_general" if th is None else f"group_{th}"


async def _backfill_block(client, chat_id, cur, key_fn):
    entity = await client.get_input_entity(chat_id)
    lo = max(1, cur - BLOCK + 1)
    by_topic = {}
    for m in await client.get_messages(entity, ids=list(range(lo, cur + 1))):
        if m is not None and not getattr(m, "empty", False):
            by_topic.setdefault(key_fn(m), []).append(m.id)
    return lo, by_topic


def backfill_loop():
    """Фоновый поток бота: один раз проходит id сверху вниз и раскладывает их в индекс — сообщения группы по темам и
    личный чат с владельцем (по блоку в секунду; FLOOD_WAIT пережидает). Прогресс хранится в индексе
    (_meta / _meta_dm .next) — после перезапуска идёт дальше. Новые сообщения индексируются вживую (note)."""
    import time
    from telethon import errors
    from secrets_local import ALLOWED_USER_ID, GROUP_CHAT_ID
    if not consts.HISTORY_FROM_TELEGRAM:
        return
    targets = [("_meta", GROUP_CHAT_ID, _topic_key), ("_meta_dm", ALLOWED_USER_ID, lambda m: "dm")]
    time.sleep(15)                       # дать боту стартовать
    while True:
        try:
            with _index_lock:
                idx = _index_load()
                metas = {k: idx.get(k, {}) for k, _, _ in targets}
            todo = [(k, cid, fn) for k, cid, fn in targets if not metas[k].get("done")]
            if not todo:
                print("[tghistory] индекс сообщений построен (группа и ЛС)")
                return
            meta_key, chat_id, key_fn = todo[0]
            nxt = metas[meta_key].get("next")
            if nxt is None:
                nxt = newest_id() + 300
            lo, by_topic = reader("bot").run(lambda c: _backfill_block(c, chat_id, nxt, key_fn))
            for topic, ids in by_topic.items():
                note_many(topic, ids)
            with _index_lock:
                idx = _index_load()
                idx[meta_key] = {"next": lo - 1, "done": lo <= 1}
                _write_index(idx)
            time.sleep(1.0)
        except errors.FloodWaitError as e:
            time.sleep(e.seconds + 1)
        except Exception as e:
            print(f"[tghistory] backfill: {type(e).__name__}: {e}")
            time.sleep(30)
