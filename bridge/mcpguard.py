"""Страховка от «пропажи» MCP-серверов бота.

Зачем. Начиная с недавних версий Claude Code, `claude -p --resume` завершается с кодом 1 и отказывается принимать ввод,
если в прошлом запуске остался отложенный вызов инструмента MCP-сервера, а самого сервера в новом запуске нет. Кроме
того, без сервера модель молча остаётся без его инструментов (telegram, memory, …). Раньше серверы жили только в
регистрации `claude mcp add` (~/.claude.json), которую пишут все процессы claude сразу, и в первые секунды хода часть
серверов была «pending».

Что делаем:
1. Список обязательных серверов (consts.EXPECTED_MCP) и файл mcp_servers.json, который бот сам генерирует из него и
   передаёт в КАЖДЫЙ запуск claude (--mcp-config): серверы подключаются независимо от регистрации и папки запуска.
2. В начале каждого хода читаем событие system/init: если обязательный сервер «failed»/«needs-auth»/отсутствует —
   чиним (пересоздаём конфиг и регистрацию), останавливаем этот запуск и повторяем.
3. При старте бота проверяем, что скрипты серверов на месте и регистрация цела."""

import json
import os
import subprocess
import sys
import time
import threading

from bridge import consts

OK_STATUSES = ("connected", "pending")      # pending — ещё подключается; вызов инструмента подождёт его (до 10 с)
_notice_lock = threading.Lock()
_last_notice = 0.0


def expected_scripts():
    return {name: os.path.join(consts.BRIDGE_DIR, script) for name, script in consts.EXPECTED_MCP.items()}


def write_config():
    """Генерирует mcp_servers.json (атомарно). Возвращает путь."""
    cfg = {"mcpServers": {name: {"type": "stdio", "command": sys.executable, "args": [path]}
                          for name, path in expected_scripts().items()}}
    tmp = consts.MCP_CONFIG_FILE + ".tmp"
    with open(tmp, "w") as f:
        json.dump(cfg, f, indent=2)
    os.replace(tmp, consts.MCP_CONFIG_FILE)
    return consts.MCP_CONFIG_FILE


def config_flags():
    """Флаги для claude: наш набор серверов в каждый запуск."""
    if not os.path.exists(consts.MCP_CONFIG_FILE):
        write_config()
    return ["--mcp-config", consts.MCP_CONFIG_FILE]


def init_problems(init_event):
    """Что не так с обязательными серверами по событию system/init: {имя: статус}. Пусто — всё в порядке."""
    status = {s.get("name"): s.get("status") for s in (init_event.get("mcp_servers") or [])}
    return {name: status.get(name, "missing") for name in consts.EXPECTED_MCP
            if status.get(name, "missing") not in OK_STATUSES}


def missing_scripts():
    return [name for name, path in expected_scripts().items() if not os.path.isfile(path)]


def registered_names():
    """Имена серверов, зарегистрированных для папки бота в ~/.claude.json (local-область)."""
    try:
        with open(os.path.expanduser("~/.claude.json")) as f:
            proj = json.load(f).get("projects", {}).get(consts.BRIDGE_DIR, {})
        return set((proj.get("mcpServers") or {}).keys())
    except Exception:
        return set()


def repair(problems=None):
    """Пересоздаёт mcp_servers.json и недостающую регистрацию (claude mcp add). Возвращает список действий."""
    actions = [f"пересоздан {os.path.basename(write_config())}"]
    have = registered_names()
    for name, path in expected_scripts().items():
        if name not in have and os.path.isfile(path):
            r = subprocess.run([consts.CLAUDE_BIN, "mcp", "add", name, "--", sys.executable, path],
                               capture_output=True, text=True, cwd=consts.BRIDGE_DIR, timeout=60)
            actions.append(f"регистрация {name}: {'ok' if r.returncode == 0 else r.stderr.strip()[:80]}")
    return actions


def notify(text):
    """Сообщение владельцу о проблеме с MCP: не чаще раза в 10 минут."""
    global _last_notice
    with _notice_lock:
        if time.time() - _last_notice < 600:
            return
        _last_notice = time.time()
    try:
        from bridge import tgapi
        tgapi.send_single(text)
    except Exception as e:
        print(f"[mcpguard] не удалось уведомить: {e}")


def startup_check():
    """При старте бота: конфиг на месте, скрипты целы, регистрация цела (иначе чиним). Пишет итог в лог."""
    write_config()
    bad = missing_scripts()
    if bad:
        print(f"[mcpguard] ВНИМАНИЕ: нет файлов MCP-серверов: {bad}")
        notify(f"⚠️ У бота нет файлов MCP-серверов: {', '.join(bad)}. Без них Claude теряет инструменты.")
    absent = [n for n in consts.EXPECTED_MCP if n not in registered_names()]
    if absent:
        print(f"[mcpguard] нет регистрации {absent} — восстанавливаю: {repair()}")
    else:
        print(f"[mcpguard] MCP-серверы на месте: {', '.join(consts.EXPECTED_MCP)}")
