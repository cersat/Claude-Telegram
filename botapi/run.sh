#!/bin/sh
# Запуск локального Telegram Bot API (docker-образ aiogram/telegram-bot-api, сервер tdlib/telegram-bot-api).
#
# Состояние живёт ВНЕ репозитория, в $BOT_API_DIR (по умолчанию ~/telegram_bot_api):
#   server.env  — TELEGRAM_API_ID / TELEGRAM_API_HASH (права 600; из my.telegram.org), создаёт install.py
#   data/       — рабочая папка сервера (там же лежат скачанные файлы — бот забирает их оттуда)
# Контейнер запускается от вашего пользователя (--user), чтобы бот читал скачанные файлы без sudo.
# Лог — уровень 2 (WARNING; сюда попадают обрывы связи с Telegram, их считает «Инфо») с ротацией 3x20 МБ.
set -e
D="${BOT_API_DIR:-$HOME/telegram_bot_api}"
NAME="${BOT_API_CONTAINER:-telegram-bot-api}"
PORT="${BOT_API_PORT:-8081}"
mkdir -p "$D/data"
[ -f "$D/server.env" ] || { echo "нет $D/server.env — создайте его (см. botapi/server.env.example) или запустите install.py" >&2; exit 1; }
docker rm -f "$NAME" >/dev/null 2>&1 || true
exec docker run -d --name "$NAME" --restart unless-stopped --user "$(id -u):$(id -g)" \
  --log-opt max-size=20m --log-opt max-file=3 \
  -p "127.0.0.1:$PORT:8081" --env-file "$D/server.env" -v "$D/data:/var/lib/telegram-bot-api" \
  --entrypoint sh aiogram/telegram-bot-api:latest \
  -c 'mkdir -p /tmp/tba && exec telegram-bot-api --api-id="$TELEGRAM_API_ID" --api-hash="$TELEGRAM_API_HASH" --local --dir=/var/lib/telegram-bot-api --temp-dir=/tmp/tba --http-port=8081 --verbosity=2'
