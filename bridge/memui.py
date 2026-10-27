"""Память в Telegram: кнопки «Память»/«Мои записи»."""

import os
import re
import memory_store
from bridge import consts, core, tgapi


# ============================================================


def remove_from_memory_index(fname):
    """Удаляет строку с fname из MEMORY.md."""
    memory_index = os.path.join(consts.MEMORY_DIR, "MEMORY.md")
    try:
        with open(memory_index, "r") as f:
            lines = f.readlines()
        lines = [l for l in lines if fname not in l]
        with open(memory_index, "w") as f:
            f.writelines(lines)
    except Exception:
        pass


def get_memory_items():
    """Возвращает список (fname, title, description) для ВСЕХ файлов памяти —
    прямо из индекса MEMORY.md (та же информация, что видит memory_list), а не
    по старому делению на 4 типа/префиксу имени файла tg_{type}_ — оно не
    отражало реальный объём памяти, т.к. почти все настоящие файлы (не
    созданные этой самой кнопкой) называются произвольно."""
    try:
        with open(os.path.join(consts.MEMORY_DIR, "MEMORY.md")) as f:
            idx = f.read()
    except Exception:
        return []
    items = []
    for m in re.finditer(r'^- \[(.*?)\]\((.*?)\)\s*[—-]\s*(.*)$', idx, re.MULTILINE):
        title, fname, desc = m.group(1).strip(), m.group(2).strip(), m.group(3).strip()
        items.append((fname, title, desc))
    return items


def get_memory_pick_inline():
    """Инлайн-клавиатура выбора файла памяти для дозаписи — плюс кнопка создать
    новый. Пишем в файлы так же, как это делает сам Claude: либо дописываем в
    существующий, либо заводим новый."""
    rows = []
    for fname, title, desc in get_memory_items():
        safe = fname.replace(".md", "")
        rows.append([{"text": title[:60], "callback_data": f"mem_pick_{safe}"}])
    rows.append([{"text": "➕ Новый файл", "callback_data": "mem_new"}])
    return {"inline_keyboard": rows}


def send_memory_items(chat_id=None, thread_id=None):
    """Отправляет каждую запись памяти отдельным сообщением с кнопками
    Удалить/Изменить."""
    items = get_memory_items()
    if not items:
        tgapi.send_single("📋 Записей пока нет.", chat_id=chat_id, thread_id=thread_id)
        return
    for fname, title, desc in items:
        text = f"*{title}*\n\n{desc}"
        # Кодируем fname: заменяем спецсимволы, оставляем только ASCII-safe часть
        safe = fname.replace(".md", "")
        kb = {"inline_keyboard": [[
            {"text": "🗑 Удалить", "callback_data": f"mem_del_{safe}"},
            {"text": "✏️ Изменить", "callback_data": f"mem_edit_{safe}"},
        ]]}
        tgapi.send_single(text, kb, chat_id=chat_id, thread_id=thread_id)


def save_memory(content):
    """Сохраняет запись в память через memory_store.write_memory_file() — так же,
    как сам Claude пишет через MCP-коннектор memory_write: просто файл с
    name/description, без деления по типам (кнопка пишет в файлы памяти точно
    так же, как это делает сам Claude)."""
    import re
    os.makedirs(consts.MEMORY_DIR, exist_ok=True)

    # Генерируем имя файла из первых слов контента — без служебного префикса,
    # чтобы не отличаться от файлов, которые пишет сам Claude.
    slug = re.sub(r"[^\w\-]", "_", content[:30].strip().lower())
    slug = re.sub(r"_+", "_", slug).strip("_") or "note"
    fname = f"{slug}.md"

    now = core.now_local().strftime("%Y-%m-%d")
    description = content[:80].strip().replace("\n", " ")

    file_content = f"""---
name: {content[:60].strip().replace(chr(10), ' ')}
description: {description}
---

{content}

_(Сохранено через Telegram {now})_
"""
    return memory_store.write_memory_file(fname, file_content, description=description)
