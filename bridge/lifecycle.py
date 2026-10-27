"""Запуск/остановка бота: статус выключения, лог рестартов."""

import os
import json
from bridge import consts, core


def load_startup_msg_id():
    try:
        with open(consts.STARTUP_MSG_FILE) as f:
            return int(f.read().strip())
    except:
        return None


def save_startup_msg_id(mid):
    try:
        with open(consts.STARTUP_MSG_FILE, "w") as f:
            f.write(str(mid))
    except:
        pass


def write_shutdown_status(clean: bool):
    """Записывает статус завершения бота."""
    try:
        data = {
            "clean": clean,
            "time": core.now_local().strftime("%Y-%m-%d %H:%M:%S")
        }
        with open(consts.SHUTDOWN_STATUS_FILE, "w") as f:
            json.dump(data, f)
    except Exception as e:
        print(f"[write_shutdown_status] Ошибка: {e}")


def append_restart_log(event: str, prev_shutdown_time: str | None = None):
    """Добавляет запись в журнал перезапусков бота.
    event: 'start_clean', 'start_unclean', 'start_first', 'stop_clean'
    """
    try:
        try:
            with open(consts.RESTART_LOG_FILE) as f:
                log = json.load(f)
        except (FileNotFoundError, json.JSONDecodeError):
            log = []
        entry = {"event": event, "time": core.now_local().strftime("%Y-%m-%d %H:%M:%S")}
        if prev_shutdown_time:
            entry["prev_stop"] = prev_shutdown_time
        log.append(entry)
        # Храним не более 200 последних записей
        if len(log) > 200:
            log = log[-200:]
        with open(consts.RESTART_LOG_FILE, "w") as f:
            json.dump(log, f, ensure_ascii=False, indent=2)
    except Exception as e:
        print(f"[append_restart_log] Ошибка: {e}")


def check_previous_shutdown():
    """
    Возвращает (was_unclean: bool, shutdown_time: str|None).
    Нештатный перезапуск = файла нет ИЛИ clean == False.
    """
    if not os.path.exists(consts.SHUTDOWN_STATUS_FILE):
        return True, None
    try:
        with open(consts.SHUTDOWN_STATUS_FILE) as f:
            data = json.load(f)
        if not data.get("clean", False):
            return True, data.get("time")
        return False, data.get("time")
    except:
        return True, None
