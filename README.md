# Claude-Telegram

*A Telegram bot that gives you Claude Code in a chat: every topic of a forum group is its own long-lived session, with live tool cards, files, voice, scheduled wake-ups and MCP connectors. Built for a Raspberry Pi, works on any Linux box.*

Telegram-бот, который превращает чат (или форум-группу с темами) в удобный интерфейс к **Claude Code**. Каждое сообщение
запускает `claude -p` в режиме `stream-json`, а вы в реальном времени видите, что делает Claude: карточки вызовов
инструментов, промежуточный текст, результат. У каждой темы форума своя сессия (`--resume`) и своя история.

> ⚠️ **Безопасность.** Claude запускается с `--dangerously-skip-permissions`, то есть может выполнять любые команды от
> имени пользователя, под которым работает бот. Бот отвечает только владельцу (`ALLOWED_USER_ID`) и в вашей группе
> (`GROUP_CHAT_ID`). Не добавляйте бота в чужие группы и не запускайте его от root. Ставьте на машину, которой готовы доверить
> такие права.

## Возможности

- **Live-стрим.** Каждый вызов инструмента — отдельная карточка со статусом ✅/⚠️/🛑, кнопки «Полное сообщение» и
  «Посмотреть текст». Плашка «печатает…» на время хода, `/stop` (или `!стоп`) останавливает выполнение, `/cancel` (`!отмена`) —
  любой режим ожидания ввода.
- **Темы форума и личка.** Сессия `--resume` и история на каждую тему; личка с ботом — такая же отдельная «тема».
- **Медиа.** Фото, документы, видео, аудио, GIF, альбомы (склеиваются в одно сообщение), файлы до 2 ГБ (с локальным Bot API).
  Голосовые распознаются локально через [whisper.cpp](https://github.com/ggerganov/whisper.cpp).
- **Rich-сообщения Telegram.** Таблицы из обычного markdown уходят через `sendRichMessage`; Claude может слать заголовки,
  списки, сворачиваемые блоки и таблицы отдельным тулом.
- **MCP-коннекторы** (регистрирует `install.py`):
  - `telegram` — файлы, опросы, реакции, инлайн-кнопки, rich-сообщения и таблицы, управление группой и темами;
  - `memory` — долговременная память Claude в markdown-файлах (с индексом);
  - `history` — чтение истории других тем;
  - `bot_scheduler` — отложенные и ежедневные пробуждения Claude в нужной теме (замена `ScheduleWakeup`);
  - `moltbook` — соцсеть для ИИ-агентов Moltbook (необязательно).
- **Локальный Bot API** в Docker ([botapi/](botapi/README.md)): лимит файлов 2 ГБ и практически нет сетевых обрывов.
- **Перелогин из Telegram.** Если OAuth-сессия Claude Code истекла, бот сам открывает `/login` в tmux и присылает ссылку;
  следующим сообщением вы отправляете код из браузера.
- **Служебное.** Кнопка «Инфо» (температура, нагрузка, диск, счётчики ошибок и сетевых сбоев), напоминания, перезапуск бота
  кнопкой, шахматы, режим «Резервный» (сообщения без обработки команд).

## Требования

- Linux с systemd, Python 3.10+ (`pip install -r requirements.txt`: `requests`, `python-chess`, `mcp`).
- [Claude Code](https://code.claude.com) (нативный установщик, `~/.local/bin/claude`) с активной подпиской или API-ключом.
- Токен бота от [@BotFather](https://t.me/BotFather); для форум-группы — группа с включёнными темами, где бот админ.
- Необязательно: `tmux` (автоперелогин), `ffmpeg` + `cmake`/`g++` (голосовые через whisper.cpp), Docker (локальный Bot API).

## Установка

```sh
git clone git@github.com:cersat/Claude-Telegram.git
cd Claude-Telegram
python3 install.py
```

Установщик по шагам: проверит токен, предложит поднять локальный Bot API (нужны `api_id`/`api_hash` с
[my.telegram.org](https://my.telegram.org)), поймает ваш `user_id` и `chat_id` группы (вы напишете боту, а потом добавите его в группу),
создаст `secrets_local.py`, поставит зависимости, соберёт whisper.cpp, зарегистрирует systemd-службу
`claude-telegram` и MCP-коннекторы. Шаги безопасно запускать повторно.

## Структура

```
bot.py                    точка входа: опрос Telegram (getUpdates) и диспетчер
bridge/                   модули бота
  consts.py core.py       пути и константы, токен и адрес Bot API
  msg_handlers.py         входящие сообщения и вложения
  cb_handlers.py          нажатия инлайн-кнопок
  upd_handlers.py         реакции, опросы
  claude_run.py           запуск claude, stream-json, сессии
  cardview.py             карточки инструментов, действия коннектора telegram
  tgapi.py mdfmt.py       отправка в Telegram, markdown → HTML
  mediaio.py              загрузка медиа, альбомы, whisper
  authlogin.py            перелогин Claude через tmux
  reminders.py            напоминания и планировщик
  sysinfo.py              «Инфо», счётчики ошибок
*_mcp_server.py           MCP-коннекторы (memory, history, moltbook, bot_scheduler, telegram)
botapi/                   запуск локального Bot API в Docker
install.py                установщик
secrets_local.py          секреты и ID — создаётся установщиком, в git не попадает
```

Рабочее состояние (история тем, сессии, настройки, логи, `topics.json`) лежит рядом с `bot.py` и в `.gitignore`: список
файлов репозитория — белый список, новый файл бота нужно явно добавить в `.gitignore` строкой `!имя_файла`.

## Лицензия

[MIT](LICENSE)
