"""Системный промпт, версии инструкций, сборка промпта."""

import time
import threading
import json
from bridge import consts, core, storage, tgapi


# Дедупликация уведомления об окончании токенов
_token_limit_lock = threading.Lock()
_last_token_limit_ts = 0

def prepend_thinking(clean_response, elapsed_str):
    """Добавляет 'Думал Хс' курсивом в начало финального ответа.
    Курсив через сентинел-символы \\x01..\\x02, которые send_single превращает в <i>/</i>."""
    if clean_response.startswith("You've hit your limit"):
        global _last_token_limit_ts
        with _token_limit_lock:
            now = time.time()
            if now - _last_token_limit_ts < 300:  # не чаще раза в 5 минут
                return None
            _last_token_limit_ts = now
        return "⚠️ Токены закончились. Попробуй позже."
    if not clean_response.strip():
        return ""
    # Если ответ — одиночный эмодзи, не добавляем "Думал"
    if tgapi.is_single_emoji(clean_response.strip()):
        return clean_response.strip()
    return f"\x01Думал {elapsed_str}\x02\n\n{clean_response}"


# ── Claude ────────────────────────────────────────────────────────────────────

# Версия системных инструкций: при изменении SYSTEM_PROMPT увеличить — тогда в уже существующие
# --resume-сессии тем один раз (на тему) вставляется обновлённый текст (сессия хранит только
# тот системный промпт, с которым была создана; --append-system-prompt при --resume не действует).
PROMPT_VERSION = 6

SYSTEM_PROMPT = """Ты — Claude, AI-ассистент, запущенный на Raspberry Pi 5 у владельца бота.
Отвечай на русском, кратко и по делу. Можешь использовать инструменты (читать/писать файлы, запускать команды).
Обращайся к пользователю на «ты».

ВАЖНО про порядок ответа: пользователь видит твою работу в реальном времени — каждый вызов инструмента
(Bash/Read/Edit/...) и каждый текстовый блок ДО последнего уходят в чат отдельными сообщениями сразу же,
по мере выполнения (это удобно — можешь коротко комментировать что делаешь, например "Создаю файл...").
Но твой ПОСЛЕДНИЙ текстовый блок — это единственное, что не отправляется как live-статус, а обрабатывается
особо: именно в нём должен быть ПОЛНЫЙ финальный ответ пользователю.
Не дублируй в последнем блоке то, что уже написал в промежуточных — считай что пользователь это видел.

Markdown-разметка работает всегда:
- *жирный* (одна звёздочка), _курсив_, `код`, [текст](url)
- **двойные звёздочки** тоже работают — конвертируются в жирный
- Таблицы: стандартный markdown-синтаксис (| A | B |\n|---|---|\n| 1 | 2 |) — работают!
- Спойлеры: ||текст|| — работают!
- Цитаты/blockquote: > текст — работают!
- ~~зачёркивание~~ — работает!
- Блоки кода: ```python\ncode\n``` — работают с подсветкой синтаксиса
- НЕ используй --- как разделитель — убирается автоматически
Осторожно со звёздочками/подчёркиваниями в обычном тексте (например в путях к файлам или
названиях переменных) — они будут распознаны как разметка, если не экранированы.

Разделение сообщений: чтобы отправить несколько отдельных сообщений, между текстовыми блоками вызови
Bash с командой ровно `true` — текст до вызова уйдёт отдельным сообщением, а карточка для `true` не
показывается. Это ЕДИНСТВЕННЫЙ способ разделять сообщения: строки-разделители (===, ---) в тексте не
работают. Не вызывай `true` ни для чего другого.

Telegram-коннектор (MCP-тулы mcp__telegram__*, загружай схемы через ToolSearch): сами тулы ничего не
делают, действие выполняет бот прямо в текущую тему — вместо обычной карточки:
- telegram_send_photo / send_file / send_audio / send_voice / send_video / send_animation (path, caption) —
  отправить файл с диска;
- telegram_send_poll — опрос/викторина;
- telegram_send_rich(markdown) — rich-сообщение (заголовки, таблицы, списки, цитаты, сворачиваемые блоки, формулы)
  для длинных структурных ответов; telegram_send_table(headers, rows, title, align) — таблица;
- telegram_set_group (название/описание/права участников группы) и telegram_set_topic (название, иконка,
  открыть/закрыть тему) — только по прямой просьбе пользователя; telegram_create_topic(name) — новая тема, если
  такой ещё нет. Результат этих тулов бот присылает в чат сам (✅/❌);
- telegram_react(emoji) — реакция на сообщение пользователя (допустимые эмодзи перечислены в описании тула);
  используй избирательно, не на каждое сообщение;
- telegram_send_buttons(text, buttons) — сообщение с инлайн-кнопками (чёткий выбор из 2-4 вариантов, быстрые
  действия). Каждая кнопка {"label", ...} и ровно одно из: "prompt" — запустит тебя с этим промптом (не с
  названием кнопки; необязательный "model": sonnet/opus/haiku/fable — модель только для этого запуска, напр.
  кнопка «Передать задачу Opus»); "action" — действие бота: restart (+необязательный "prompt" — придёт тебе
  первым сообщением после перезапуска), stop, reset_session, clear_log, info, limits; "code" (+"lang":
  bash|python) — бот выполнит код сам, без тебя, и пришлёт вывод.
Текстовые директивы КНОПКИ:, РЕАКЦИЯ:, КОНТЕКСТ: и разделитель === больше НЕ работают и не нужны — если в
истории этого чата ты видишь старые инструкции про них, они отменены. Резюме-«КОНТЕКСТ» писать не нужно.

Другие коннекторы (MCP, схемы загружай через ToolSearch): memory — твоя долговременная память (base_memory.md
читай первой в новой теме; индекс MEMORY.md подгружается сам); history — сырая история других тем этого чата;
bot_scheduler — отложенные и повторяющиеся задачи, запускающие тебя в нужной теме (используй его, а не
встроенный ScheduleWakeup); moltbook — соцсеть для AI-агентов. У каждого коннектора есть свои инструкции.
Пометки вида [Системная информация: ...] в сообщениях добавляет бот (отправитель, тема, режим и т.п.) — это не
слова пользователя. Остановить твой ход пользователь может командой /stop (!стоп).

ВАЖНО: НЕ добавляй в начало ответа строку "_Думал Xс_" — это делает скрипт автоматически."""


def load_prompt_versions():
    try:
        with open(consts.PROMPT_VERSIONS_FILE) as f:
            return json.load(f)
    except Exception:
        return {}

def mark_prompt_version(topic_key):
    versions = load_prompt_versions()
    versions[str(topic_key)] = PROMPT_VERSION
    with open(consts.PROMPT_VERSIONS_FILE, "w") as f:
        json.dump(versions, f)

def prompt_update_note(topic_id):
    """Если сессия темы создана со старой версией инструкций — текст обновления для вставки
    в сообщение (иначе пустая строка). Версию помечает mark_prompt_version после успешного хода."""
    if load_prompt_versions().get(str(topic_id)) == PROMPT_VERSION:
        return ""
    return ("[ОБНОВЛЕНИЕ СИСТЕМНЫХ ИНСТРУКЦИЙ — заменяет прежние (про КНОПКИ:/РЕАКЦИЯ:/КОНТЕКСТ:/===, старый формат кнопок label+action-как-промпт), "
            "дальше следуй только этим:]\n" + SYSTEM_PROMPT + "\n[КОНЕЦ ОБНОВЛЕНИЯ ИНСТРУКЦИЙ]\n\n")


def _format_history_line(line, compact=False):
    """Форматирует одну строку истории. compact=True — короткие имена ролей."""
    if line.startswith("U|") or line.startswith("C|"):
        parts_l = line.split("|", 2)
        if len(parts_l) == 3:
            prefix, ts, txt = parts_l
            if compact:
                role_name = "П" if prefix == "U" else "А"
                # Для компактного формата оставляем только HH:MM
                short_ts = ts[11:] if len(ts) > 10 else ts
                return f"[{short_ts}] {role_name}: {txt}"
            else:
                role_name = "Пользователь" if prefix == "U" else "Ассистент"
                return f"[{ts}] {role_name}: {txt}"
    elif "|" in line:
        # Старый формат: Пользователь|text или Ассистент|text
        role, text = line.split("|", 1)
        text = text.replace("\\n", "\n")
        return f"{role}: {text}"
    return None

def build_prompt(user_message, reply_to=None, topic_id=None):
    s = storage.load_settings()
    history = storage.load_history(topic_id=topic_id)
    style_note = "\nОтвечай подробно, с объяснениями." if s["style"] == "detailed" else ""
    parts = [SYSTEM_PROMPT + style_note, ""]

    FULL_TAIL = 2  # последние N записей — полный формат
    split_idx = max(0, len(history) - FULL_TAIL)
    context_lines = history[:split_idx]   # старые — компактно
    full_lines = history[split_idx:]      # последние 2 — полностью

    if context_lines:
        parts.append("[История контекста:]")
        for line in context_lines:
            fmt = _format_history_line(line, compact=True)
            if fmt:
                parts.append(fmt)

    if full_lines:
        parts.append("[Последние сообщения:]")
        for line in full_lines:
            fmt = _format_history_line(line, compact=False)
            if fmt:
                parts.append(fmt)

    now = core.now_local().strftime("%Y-%m-%d %H:%M:%S")
    if reply_to:
        parts.append(f"[Пользователь отвечает на сообщение: «{reply_to}»]")
    parts.append(f"Пользователь [{now}]: {user_message}")
    return "\n".join(parts)
