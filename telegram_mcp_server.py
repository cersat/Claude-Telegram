#!/usr/bin/env python3
"""MCP-сервер (stdio) — "коннектор" отправки в Telegram.

Сам сервер ничего никуда не отправляет: тулы только принимают аргументы и возвращают "ok".
Настоящую работу делает bot.py — он видит вызов mcp__telegram__* в stream-json потоке
ответа Claude и вместо обычной карточки инструмента выполняет действие (отправляет фото,
файл, ставит реакцию и т.д.) в ту же тему/чат, где идёт диалог. Без bot.py тулы бесполезны."""

from mcp.server.mcpserver import MCPServer

mcp = MCPServer("telegram", instructions="""Эти тулы управляют Telegram-чатом, в котором идёт диалог.
Сами они ничего не делают — действие выполняет бот-мост, который перехватывает вызов.
Путь к файлу (path) — абсолютный путь на машине, где запущен бот; файл должен существовать.
Вызывай тул в тот момент, когда нужно, чтобы результат появился в чате: порядок сообщений
сохраняется (текст, написанный до вызова, уйдёт раньше).""")

_OK = "ok"


@mcp.tool()
def telegram_send_photo(path: str, caption: str = "") -> str:
    """📎 Отправляет картинку в чат. path — абсолютный путь к файлу, caption — необязательная подпись (markdown)."""
    return _OK


@mcp.tool()
def telegram_send_file(path: str, caption: str = "") -> str:
    """📎 Отправляет любой файл как документ. path — абсолютный путь, caption — необязательная подпись."""
    return _OK


@mcp.tool()
def telegram_send_audio(path: str, caption: str = "") -> str:
    """📎 Отправляет аудиофайл (музыка/запись) с плеером. path — абсолютный путь, caption — необязательная подпись."""
    return _OK


@mcp.tool()
def telegram_send_voice(path: str, caption: str = "") -> str:
    """📎 Отправляет голосовое сообщение (лучше всего .ogg/opus). path — абсолютный путь, caption — необязательная подпись."""
    return _OK


@mcp.tool()
def telegram_send_video(path: str, caption: str = "") -> str:
    """📎 Отправляет видео. path — абсолютный путь, caption — необязательная подпись."""
    return _OK


@mcp.tool()
def telegram_send_animation(path: str, caption: str = "") -> str:
    """📎 Отправляет GIF/анимацию. path — абсолютный путь, caption — необязательная подпись."""
    return _OK


@mcp.tool()
def telegram_send_poll(question: str, options: list[str], correct_option: int = 0, explanation: str = "") -> str:
    """📎 Отправляет опрос-викторину. options — варианты ответа (2-10), correct_option — номер верного
    варианта с 1 (0 — обычный опрос без правильного ответа), explanation — пояснение после ответа (до 200 символов)."""
    return _OK


@mcp.tool()
def telegram_react(emoji: str) -> str:
    """📎 Ставит реакцию на сообщение пользователя, на которое ты сейчас отвечаешь. Telegram принимает
    ТОЛЬКО эти эмодзи (остальные, включая 😂 😄 😅 🙂 😊 ✅ ❌ ⭐ 💖 🌹, будут отвергнуты):
    👍 👎 ❤ 🔥 🥰 👏 😁 🤔 🤯 😱 🤬 😢 🎉 🤩 🤮 💩 🙏 👌 🕊 🤡 🥱 🥴 😍 🐳 ❤‍🔥 🌚 🌭 💯 🤣 ⚡ 🍌 🏆 💔 🤨 😐
    🍓 🍾 💋 🖕 😈 😴 😭 🤓 👻 👨‍💻 👀 🎃 🙈 😇 😨 🤝 ✍ 🤗 🫡 🎅 🎄 ☃ 💅 🤪 🗿 🆒 💘 🙉 🦄 😘 💊 🙊 😎 👾
    🤷‍♂ 🤷 🤷‍♀ 😡. Не злоупотребляй — не на каждое сообщение."""
    return _OK


@mcp.tool()
def telegram_send_buttons(text: str, buttons: list[list[dict]]) -> str:
    """📎 Отправляет сообщение с инлайн-кнопками. text — текст сообщения (markdown). buttons — список рядов
    (кнопки одного ряда рисуются рядом), кнопка — объект с "label" (текст на кнопке) и РОВНО ОДНИМ из:
      "prompt": "..."  — при нажатии бот запускает тебя с этим промптом (именно промптом, а не названием
                         кнопки). Необязательный "model": "sonnet"|"opus"|"haiku"|"fable" — модель только для
                         этого запуска (настройка не меняется), например для сложной задачи — "opus".
      "action": "..."  — действие бота без запуска тебя: "restart" (перезапуск бота; необязательный
                         "prompt" — придёт тебе первым сообщением после перезапуска), "stop" (остановить
                         выполнение в этой теме), "reset_session" (сбросить сессию и историю темы),
                         "clear_log" (очистить лог бота), "info" (показать Инфо), "limits" (лимиты).
      "code": "..."    — бот сам выполнит код (без тебя) и пришлёт вывод; "lang": "bash" (по умолчанию) или
                         "python". Таймаут 60с.
    Пример: [[{"label": "Разобрать на Opus", "prompt": "Реши задачу X аккуратно", "model": "opus"},
              {"label": "Место на диске", "code": "df -h /", "lang": "bash"}],
             [{"label": "Перезапустить", "action": "restart"}]]"""
    return _OK


@mcp.tool()
def telegram_send_rich(markdown: str) -> str:
    """📎 Отправляет rich-сообщение (Bot API 10, sendRichMessage). markdown — текст в «Rich Markdown»: заголовки
    # ... ######, **жирный**, *курсив*, ~~зачёркнутый~~, ==выделенный==, ||спойлер||, `код` и ```блоки кода```,
    списки (- / 1. / - [ ] задачи), > цитаты, таблицы (| A | B | с |:---|---:| под заголовком), сноски [^id],
    формулы $x^2$ и $$...$$, сворачиваемые блоки <details><summary>Заголовок</summary>текст</details>,
    <u>подчёркнутый</u>, <sup>, <sub>, ссылки [текст](url). До 32768 символов. Подходит для длинных структурных
    ответов (отчёты, сравнения, документы); для короткой реплики пиши обычным текстом."""
    return _OK


@mcp.tool()
def telegram_send_table(headers: list[str], rows: list[list[str]], title: str = "", align: list[str] | None = None) -> str:
    """📎 Отправляет таблицу как rich-сообщение. headers — заголовки колонок (до 20), rows — строки (списки значений;
    короткие строки дополняются пустыми ячейками), title — необязательный жирный заголовок над таблицей,
    align — необязательное выравнивание колонок: "left" | "center" | "right" на каждую колонку.
    Символ | в ячейках экранируется сам. Внутри ячеек можно писать markdown (**жирный**, `код`)."""
    return _OK


@mcp.tool()
def telegram_set_group(title: str = "", description: str | None = None, permissions: dict[str, bool] | None = None) -> str:
    """📎 Меняет настройки ГРУППЫ (чата, в котором идёт диалог): title — название (1-128 символов),
    description — описание (до 255, пустая строка очищает), permissions — права участников по умолчанию:
    только те поля, что нужно изменить (остальные сохраняются): can_send_messages, can_send_audios,
    can_send_documents, can_send_photos, can_send_videos, can_send_video_notes, can_send_voice_notes,
    can_send_polls, can_send_other_messages, can_add_web_page_previews, can_react_to_messages, can_edit_tag,
    can_change_info, can_invite_users, can_pin_messages, can_manage_topics. Нужны права администратора у бота.
    Меняй только по прямой просьбе пользователя: это затрагивает всех участников группы. Результат бот
    пришлёт в чат сообщением (✅/❌)."""
    return _OK


@mcp.tool()
def telegram_set_topic(name: str = "", icon_custom_emoji_id: str | None = None, state: str = "", thread_id: int = 0) -> str:
    """📎 Меняет настройки ТЕМЫ форума: name — новое название (1-128), icon_custom_emoji_id — id кастомного эмодзи
    иконки (пустая строка убирает иконку; у General иконку менять нельзя), state — "open" или "closed"
    (открыть/закрыть тему), thread_id — номер темы (0 — текущая тема; в General — General). Укажи только то,
    что нужно изменить. Результат бот пришлёт в чат сообщением (✅/❌). Удаления тем нет намеренно."""
    return _OK


@mcp.tool()
def telegram_create_topic(name: str, icon_color: int = 0, icon_custom_emoji_id: str = "") -> str:
    """📎 Создаёт новую тему в группе, ЕСЛИ темы с таким названием ещё нет (сравнение без учёта регистра;
    если есть — бот сообщит и пришлёт ссылку на существующую). name — название (1-128), icon_color —
    необязательный цвет иконки, ровно одно из: 7322096, 16766590, 13338331, 9367192, 16749490, 16478047,
    icon_custom_emoji_id — необязательная иконка. Результат бот пришлёт в чат со ссылкой на тему."""
    return _OK


if __name__ == "__main__":
    import asyncio
    asyncio.run(mcp.run_stdio_async())
