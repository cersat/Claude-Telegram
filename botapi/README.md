# Локальный Telegram Bot API

Бот работает через собственный сервер Bot API (docker), а не через облачный `api.telegram.org`:

- файлы до **2 ГБ** вместо 20 МБ (скачивание и отправка);
- сервер держит постоянное соединение с Telegram, у бота нет сетевых обрывов (по опыту — с ~1–2 в час до нуля);
- меньше зависимости от DNS/IPv6 на машине.

## Установка
`install.py` делает всё сам (шаг «Локальный Bot API»): спрашивает `api_id` и `api_hash` с <https://my.telegram.org>,
пишет их в `~/telegram_bot_api/server.env` (права 600), запускает `botapi/run.sh`, проверяет сервер,
делает `logOut` бота с облачного API (**после этого вернуться в облако можно только через 10 минут**)
и прописывает `BOT_API_BASE = "http://127.0.0.1:8081"` в `secrets_local.py`.

Нужны Docker (`curl -fsSL https://get.docker.com | sh`) и доступ пользователя к нему (`sudo usermod -aG docker $USER`).

## Вручную
```sh
cp botapi/server.env.example ~/telegram_bot_api/server.env   # заполнить, chmod 600
botapi/run.sh                                                 # (пере)создать контейнер
```
Вернуться на облако: `BOT_API_BASE = "https://api.telegram.org"` в `secrets_local.py`, остановить контейнер
(`docker rm -f telegram-bot-api`), перезапустить бота.

## Что где
- `bridge/consts.py` — `BOT_API_BASE`, `BOT_API_DATA_DIR` (где сервер хранит скачанные файлы), `BOT_API_LOCAL`.
- `bridge/mediaio.py` — в локальном режиме `getFile` возвращает путь на диске сервера; файл переносится в `inbox`.
- «Инфо» считает сетевые сбои и бота, и сервера (`docker logs telegram-bot-api`).

## Про «историю чата»
Bot API (и облачный, и локальный) не отдаёт ботам историю чата — только новые апдейты. Бот ведёт собственную историю
в `topic_histories/`. Чтобы читать прошлые сообщения, нужен пользовательский аккаунт (MTProto, например Telethon).
