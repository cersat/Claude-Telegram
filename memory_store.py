#!/usr/bin/env python3
"""Общая логика записи файла памяти + индекса MEMORY.md — единственное место,
где это на самом деле происходит. Используется и MCP-коннектором
(memory_mcp_server.py, memory_write — Claude сам собирает полный markdown с
frontmatter и передаёт сюда), и Telegram-кнопкой «Память» в bot.py (save_memory
собирает frontmatter в том же формате: name/description/metadata.type — раньше
у кнопки была своя отдельная реализация с другой, нестандартной схемой
frontmatter, и оба пути расходились)."""

import os
import re

MEMORY_DIR = os.path.expanduser("~/.local/share/claude_memory")  # гарантированно существующий путь (XDG data dir), не завязан на slug-путь проекта Claude Code
INDEX_FILE = os.path.join(MEMORY_DIR, "MEMORY.md")


def write_memory_file(filename, content, description=""):
    """Создаёт/перезаписывает файл памяти (только имя, без пути — basename на
    всякий случай) и, если файл новый и есть description, добавляет строку в
    индекс MEMORY.md (заголовок берётся из frontmatter-поля name:)."""
    path = os.path.join(MEMORY_DIR, os.path.basename(filename))
    is_new = not os.path.exists(path)
    with open(path, "w") as f:
        f.write(content)

    if is_new and description:
        title_match = re.search(r'^name:\s*(.+)$', content, re.MULTILINE)
        title = title_match.group(1).strip() if title_match else os.path.basename(filename)
        line = f"- [{title}]({os.path.basename(filename)}) — {description}\n"
        try:
            with open(INDEX_FILE, "a") as f:
                f.write(line)
        except FileNotFoundError:
            pass

    return path


_SEED_INDEX = """# Memory Index

Это тир 2 схемы памяти — список файлов, а НЕ сама память (описания однострочные, для навигации).
Сначала читается [base_memory.md](base_memory.md) — тир 1, ядро всегда актуальных фактов и правил.

- [БАЗОВАЯ ПАМЯТЬ](base_memory.md) — ядро правил и фактов, схема памяти, как читать/писать память — читать первой
"""

_SEED_BASE = """---
name: base_memory
description: Тир 1 — ядро памяти: схема, правила ведения, самые важные факты. Читать первой.
---

# Базовая память

## Схема памяти (три тира)

1. **Тир 1 — этот файл** (`base_memory.md`): всегда актуальное ядро — важнейшие факты о пользователе и правила
   работы. Короткий. Читай его в начале работы в новой теме.
2. **Тир 2 — индекс `MEMORY.md`**: просто список файлов с однострочными описаниями (это НЕ сама память).
   Подгружается в контекст автоматически.
3. **Тир 3 — отдельные `.md` файлы** по темам: подробности, история, справочники.

## Как читать и писать

Через MCP-коннектор `memory` (тулы `memory_list`, `memory_read`, `memory_write`) — не через общие Read/Write.
`memory_write(filename, content, description)` перезаписывает файл целиком; для нового файла индекс `MEMORY.md`
обновляется сам (передай description). Для точечной правки длинного файла можно использовать и Edit.

## Что записывать

- Факты о пользователе и его предпочтения, поправки к твоему поведению («не делай так») и подтверждённые
  удачные подходы — с причиной (почему) и тем, когда это применять.
- Нестандартные решения по проекту, которых не видно из кода.
- НЕ записывать: секреты (токены, ключи, пароли), то, что и так видно из кода/git, одноразовые детали задачи.

## Факты и правила

(пока пусто — дополняй по мере работы)
"""


def ensure_memory_seed():
    """Идемпотентно создаёт MEMORY_DIR и (если их нет) стартовые MEMORY.md и base_memory.md —
    чтобы на чистой установке коннектор памяти и кнопка «Память» работали, а Claude с первого запуска
    получал схему памяти. Уже существующие файлы не трогает."""
    os.makedirs(MEMORY_DIR, exist_ok=True)
    for path, content in ((INDEX_FILE, _SEED_INDEX), (os.path.join(MEMORY_DIR, "base_memory.md"), _SEED_BASE)):
        if not os.path.exists(path):
            with open(path, "w") as f:
                f.write(content)
