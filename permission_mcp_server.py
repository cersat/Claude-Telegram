#!/usr/bin/env python3
"""MCP-сервер (stdio) «perm» — спрашивает владельца в Telegram, можно ли выполнить инструмент.

Подключается к claude флагом `--permission-prompt-tool mcp__perm__approve`: когда Claude хочет вызвать
инструмент, которого нет в списке разрешённых (--allowedTools), он вместо диалога вызывает этот тул
с именем инструмента и его параметрами и ждёт ответ {"behavior": "allow"|"deny", ...}.

Как это работает (процесс MCP-сервера живёт отдельно от бота, поэтому общаемся через файлы):
  1. approve() пишет запрос perm_requests/<id>.req (с tool_use_id).
  2. Бот (bridge/permui.py) находит карточку этого вызова и вешает на неё кнопки ✅ Разрешить / ❌ Запретить
     (callback_data perm_<id>_a|d|r); нет карточки — шлёт отдельное сообщение.
  3. Нажатие (bridge/cb_handlers.py) пишет perm_requests/<id>.decision; approve() ждёт его до PERM_TIMEOUT
     секунд; нет ответа — запрет."""

import json
import os
import sys
import time
import uuid

from mcp.server.mcpserver import MCPServer

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
REQ_DIR = os.path.join(BASE_DIR, "perm_requests")
RULES_FILE = os.path.join(BASE_DIR, "permission_rules.json")
PERM_TIMEOUT = int(os.environ.get("PERM_TIMEOUT", "300"))
NEVER_ALWAYS = {"Bash"}  # «всегда разрешать» для Bash = полный доступ к shell: такой кнопки нет

mcp = MCPServer("perm", instructions="""Служебный коннектор: решает, можно ли Claude выполнить инструмент. Сам по себе
не вызывается моделью — его указывает флаг --permission-prompt-tool. Вопрос уходит владельцу в Telegram
кнопками «Разрешить / Запретить»; нет ответа за несколько минут — запрет.""")


def _load_rules():
    try:
        with open(RULES_FILE) as f:
            return set(json.load(f).get("tools", []))
    except Exception:
        return set()


def _describe(tool_name, tool_input):
    """Короткое описание вызова для сообщения с кнопками (то, что реально будет выполнено)."""
    ti = tool_input if isinstance(tool_input, dict) else {}
    if tool_name == "Bash":
        body = str(ti.get("command", ""))
    elif tool_name in ("Write", "Edit", "MultiEdit", "NotebookEdit"):
        body = str(ti.get("file_path") or ti.get("notebook_path") or "")
        extra = ti.get("content") or ti.get("new_string") or ""
        if extra:
            body += "\n---\n" + str(extra)
    else:
        body = json.dumps(ti, ensure_ascii=False, indent=1)
    return body[:1400] + ("…" if len(body) > 1400 else "")


def _allow(tool_input):
    # dict, а не строка: FastMCP строку заворачивает в {"result": ...}, а Claude ждёт сам объект решения
    return {"behavior": "allow", "updatedInput": tool_input if isinstance(tool_input, dict) else {}}


def _deny(message):
    return {"behavior": "deny", "message": message}


@mcp.tool()
def approve(tool_name: str, input: dict, tool_use_id: str = "") -> dict:
    """Решает, можно ли выполнить инструмент tool_name с параметрами input (вызывается самим Claude Code)."""
    dbg = os.environ.get("PERM_DEBUG_LOG")
    if dbg:
        with open(dbg, "a") as f:
            f.write(json.dumps({"tool_name": tool_name, "input": input, "tool_use_id": tool_use_id}, ensure_ascii=False) + "\n")
    auto = os.environ.get("PERM_TEST_AUTO")  # только для проверок без Telegram
    if auto:
        return _allow(input) if auto == "allow" else _deny("тестовый запрет")
    if tool_name not in NEVER_ALWAYS and tool_name in _load_rules():
        return _allow(input)

    chat_id = os.environ.get("BOT_CHAT_ID")
    thread_id = os.environ.get("BOT_THREAD_ID")
    if not chat_id:
        return _deny("Нет чата для запроса разрешения (BOT_CHAT_ID не задан) — запрещено.")

    rid = uuid.uuid4().hex[:12]
    os.makedirs(REQ_DIR, exist_ok=True)
    req_path = os.path.join(REQ_DIR, f"{rid}.req")
    dec_path = os.path.join(REQ_DIR, f"{rid}.decision")
    # Запрос лежит файлом; кнопки бот (bridge/permui.py) цепляет прямо к карточке вызова по tool_use_id
    # (а если карточки нет — шлёт отдельное сообщение). Сам MCP-сервер в Telegram ничего не пишет.
    tmp = req_path + ".tmp"
    with open(tmp, "w") as f:
        json.dump({"id": rid, "tool": tool_name, "tool_use_id": tool_use_id, "chat_id": chat_id,
                   "thread_id": thread_id, "t": time.time(), "desc": _describe(tool_name, input),
                   "always_ok": tool_name not in NEVER_ALWAYS}, f, ensure_ascii=False)
    os.replace(tmp, req_path)  # атомарно: бот не должен прочитать недописанный файл

    deadline = time.time() + PERM_TIMEOUT
    while time.time() < deadline:
        if os.path.exists(dec_path):
            try:
                with open(dec_path) as f:
                    decision = json.load(f)
            except Exception:
                time.sleep(0.2)
                continue
            _cleanup(rid)
            if decision.get("allow"):
                return _allow(input)
            return _deny("Владелец запретил этот вызов.")
        time.sleep(0.4)
    _cleanup(rid)
    return _deny(f"Владелец не ответил за {PERM_TIMEOUT // 60} минут — вызов запрещён.")


def _cleanup(rid):
    for ext in ("req", "decision"):
        try:
            os.unlink(os.path.join(REQ_DIR, f"{rid}.{ext}"))
        except FileNotFoundError:
            pass


if __name__ == "__main__":
    import asyncio
    asyncio.run(mcp.run_stdio_async())
