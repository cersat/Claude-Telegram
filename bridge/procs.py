"""Реестр процессов Claude и блокировки по темам (для /stop)."""

import threading
import subprocess


# Реестр запущенных процессов claude (для команды /stop)
running_procs = {}
running_procs_lock = threading.Lock()

def stream_key(topic_id):
    return f"topic_{topic_id}" if topic_id is not None else "dm"

# Лок на тему/ЛС — не даёт двум сообщениям (например фото + подпись, присланным
# вместе одним альбомом) одновременно дёрнуть `claude --resume` одной и той же
# сессии: CLI не поддерживает конкурентный resume, из-за чего один из запусков
# терял сообщение целиком. Теперь такие запуски сериализуются в очередь.
topic_locks = {}
topic_locks_guard = threading.Lock()

def get_topic_lock(topic_id):
    key = stream_key(topic_id)
    with topic_locks_guard:
        lock = topic_locks.get(key)
        if lock is None:
            lock = threading.Lock()
            topic_locks[key] = lock
        return lock

def register_proc(key, proc):
    with running_procs_lock:
        running_procs[key] = proc

def unregister_proc(key, proc):
    with running_procs_lock:
        if running_procs.get(key) is proc:
            del running_procs[key]

def stop_proc(key):
    """Останавливает выполняющийся процесс claude по ключу. Возвращает True если что-то остановил."""
    with running_procs_lock:
        proc = running_procs.get(key)
    if proc and proc.poll() is None:
        proc._user_stopped = True
        try:
            proc.terminate()
            proc.wait(timeout=3)
        except subprocess.TimeoutExpired:
            proc.kill()
        except Exception:
            pass
        return True
    return False
