#!/usr/bin/env python3
"""MCP-сервер (stdio) — отдельный коннектор для чтения/записи памяти Claude,
вместо обращения к файлам памяти через generic Read/Write/Edit.

Память живёт в MEMORY_DIR: базовая память (base_memory.md), индекс (MEMORY.md)
и отдельные тематические .md файлы. Три тула ниже — единственный способ
трогать эту директорию через этот коннектор."""

import os
from mcp.server.mcpserver import MCPServer
from memory_store import MEMORY_DIR, INDEX_FILE, write_memory_file, ensure_memory_seed

ensure_memory_seed()

mcp = MCPServer("memory", instructions="""Долговременная память Claude в трёх тирах: base_memory.md (тир 1 — ядро фактов и правил,
читай первым), MEMORY.md (тир 2 — только индекс файлов, не сама память) и тематические .md файлы (тир 3).
memory_list — индекс, memory_read — прочитать файл, memory_write — записать/перезаписать файл целиком
(для нового файла передай description — строка добавится в индекс сама). Секреты в память не записывай.""")


def _safe_path(filename: str) -> str:
    """Не даёт выйти за пределы MEMORY_DIR (без поддиректорий, без ..)."""
    base = os.path.basename(filename)
    if not base.endswith(".md") or base != filename:
        raise ValueError(f"Недопустимое имя файла памяти: {filename!r} (только имя .md без пути)")
    return os.path.join(MEMORY_DIR, base)


@mcp.tool()
def memory_list() -> str:
    """💡 Список всех файлов памяти (тир 2 — индекс MEMORY.md, не сама память)."""
    try:
        with open(INDEX_FILE) as f:
            return f.read()
    except FileNotFoundError:
        return "MEMORY.md не найден."


@mcp.tool()
def memory_read(filename: str) -> str:
    """💡 Читает один файл памяти по имени (например 'base_memory.md')."""
    path = _safe_path(filename)
    with open(path) as f:
        return f.read()


@mcp.tool()
def memory_write(filename: str, content: str, description: str = "") -> str:
    """💡 Создаёт/перезаписывает файл памяти и добавляет строку в индекс MEMORY.md,
    если её там ещё нет (description — короткое однострочное описание для индекса)."""
    path = _safe_path(filename)
    is_new = not os.path.exists(path)
    write_memory_file(filename, content, description=description)
    return f"💡 Записано: {path}" + (" (добавлено в индекс)" if is_new and description else "")


if __name__ == "__main__":
    import asyncio
    asyncio.run(mcp.run_stdio_async())
