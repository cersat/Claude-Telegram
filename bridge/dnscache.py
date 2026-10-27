"""Кэш DNS внутри процесса бота + IPv4 в приоритете (IPv6-путь до Telegram теряет ~5% SYN,
а у requests нет таймаута подключения — запрос висел минутами). Системный резолвер (Tailscale, 100.100.100.100) иногда
подвисает ровно на 2с, а на одно нажатие кнопки приходится несколько запросов к Telegram —
задержки складывались, и ответ на нажатие просрочивался. Адреса кэшируются, а устаревшие
обновляются в фоне: запрос никогда не ждёт резолвер, если адрес уже известен."""

import socket
import threading
import time

REFRESH_AFTER = 300   # через сколько секунд обновлять запись в фоне
MAX_AGE = 3600        # старше — резолвим синхронно, как обычно

_orig_getaddrinfo = socket.getaddrinfo
_cache = {}
_refreshing = set()
_lock = threading.Lock()


def _resolve(*args, **kwargs):
    res = _orig_getaddrinfo(*args, **kwargs)
    # IPv4 первым (сортировка устойчивая): IPv6 остаётся запасным вариантом
    return sorted(res, key=lambda r: r[0] != socket.AF_INET)


def _refresh(key, args, kwargs):
    try:
        res = _resolve(*args, **kwargs)
        with _lock:
            _cache[key] = (time.time(), res)
    except OSError:
        pass  # оставляем старую запись
    finally:
        with _lock:
            _refreshing.discard(key)


def _cached_getaddrinfo(host, port, *args, **kwargs):
    if not isinstance(host, str) or host in ("localhost", "") or host.replace(".", "").isdigit() or ":" in host:
        return _orig_getaddrinfo(host, port, *args, **kwargs)
    key = (host, port, args, tuple(sorted(kwargs.items())))
    now = time.time()
    with _lock:
        entry = _cache.get(key)
        if entry and now - entry[0] < MAX_AGE:
            if now - entry[0] > REFRESH_AFTER and key not in _refreshing:
                _refreshing.add(key)
                threading.Thread(target=_refresh, args=(key, (host, port) + args, kwargs), daemon=True).start()
            return list(entry[1])
    res = _resolve(host, port, *args, **kwargs)
    with _lock:
        _cache[key] = (time.time(), res)
    return res


def install():
    """Подменяет socket.getaddrinfo (идемпотентно)."""
    if socket.getaddrinfo is not _cached_getaddrinfo:
        socket.getaddrinfo = _cached_getaddrinfo
