#!/usr/bin/env python3
"""Установщик Telegram-bridge бота.

Не привязан к пользователю/пути: все пути бот считает от папки с bot.py и домашней
директории (bridge/consts.py), systemd-юнит генерируется под текущего пользователя.
Не относится к установке: Gemma Arena (см. ниже) ожидается в ~/gemma_sandbox.

Делает по порядку:
  1. Спрашивает токен Telegram-бота (и проверяет его через getMe).
  2. Локальный Telegram Bot API (docker, необязательно, но рекомендуется): файлы до 2 ГБ и нет сетевых
     обрывов. Спрашивает api_id/api_hash с my.telegram.org, запускает botapi/run.sh, делает logOut с облака.
  3. Спрашивает Moltbook API-ключ (необязательно).
  4. Просит написать боту в личку и добавить его в группу(ы) — временным
     поллингом ловит user_id (из личного сообщения) и chat_id группы (из
     сообщения в группе или из события добавления бота в группу).
  5. Генерирует secrets_local.py (включая BOT_API_BASE).
  6. Ставит python-зависимости (requirements.txt).
  7. Собирает whisper.cpp из исходников (для транскрипции голосовых) и
     скачивает модель small, если ещё не собрано.
  8. Проверяет что установлен Claude Code (сам не ставит — просит сделать
     вручную по официальной инструкции, если не найден).
  9. Регистрирует и запускает systemd-службу.
  10. Создаёт память Claude (~/.local/share/claude_memory: базовая память + индекс, симлинк из
     ~/.claude/projects/<slug>/memory) и подключает MCP-коннекторы
     (memory/history/moltbook/bot_scheduler/telegram).
  11. Запускает fill_kabuddi.py (ритуал).

Gemma Arena (~/gemma_sandbox) сюда сознательно не входит — это
отдельный проект, не часть бота, установщик его не трогает вообще.

Безопасно перезапускать — уже сделанные шаги (секреты, служба, коннекторы,
whisper.cpp) просто спросят подтверждение или пропустят себя, если уже
сделаны, вместо того чтобы затереть/пересобрать молча.
"""

import getpass
import json
import os
import shutil
import subprocess
import sys
import time
import urllib.error
import urllib.request

BRIDGE_DIR = os.path.dirname(os.path.abspath(__file__))
SECRETS_FILE = os.path.join(BRIDGE_DIR, "secrets_local.py")
SERVICE_NAME = "claude-telegram"
SERVICE_PATH = f"/etc/systemd/system/{SERVICE_NAME}.service"
CLAUDE_BIN = os.path.expanduser("~/.local/bin/claude")
CLOUD_API = "https://api.telegram.org"
API_BASE = CLOUD_API                      # меняется на локальный адрес, если поднят свой Bot API
BOT_API_DIR = os.path.expanduser("~/telegram_bot_api")   # server.env и data/ — вне репозитория (должен совпадать с bridge/consts.py)
BOT_API_LOCAL_URL = "http://127.0.0.1:8081"


def step(n, total, title):
    print(f"\n=== [{n}/{total}] {title} ===")


def ask(prompt, allow_empty=False, default=None):
    suffix = f" [{default}]" if default else ""
    while True:
        val = input(f"{prompt}{suffix}: ").strip()
        if not val and default is not None:
            return default
        if val or allow_empty:
            return val
        print("Пусто — введи значение или Ctrl+C для отмены.")


def confirm(prompt, default_yes=True):
    hint = "[Y/n]" if default_yes else "[y/N]"
    val = input(f"{prompt} {hint}: ").strip().lower()
    if not val:
        return default_yes
    return val in ("y", "yes", "д", "да")


def http_json(url, params=None, timeout=25):
    if params:
        url = url + "?" + "&".join(f"{k}={urllib.request.quote(str(v))}" for k, v in params.items())
    with urllib.request.urlopen(url, timeout=timeout) as r:
        return json.loads(r.read().decode())


def get_me(token):
    try:
        data = http_json(f"{API_BASE}/bot{token}/getMe", timeout=15)
    except (urllib.error.URLError, TimeoutError) as e:
        print(f"❌ Не удалось связаться с Telegram: {e}")
        return None
    if not data.get("ok"):
        print(f"❌ Токен не работает: {data.get('description')}")
        return None
    return data["result"]


def wait_for_ids(token, bot_username, timeout_minutes=10):
    api = f"{API_BASE}/bot{token}"
    offset = 0
    user_id, group_id, group_title = None, None, None
    deadline = time.time() + timeout_minutes * 60

    print(f"\nНапиши что-нибудь боту @{bot_username} в личные сообщения,")
    print("и добавь его в нужную группу (или супергруппу) — жду события...")
    print("(Ctrl+C чтобы прервать и ввести id вручную)\n")

    while time.time() < deadline and (user_id is None or group_id is None):
        try:
            data = http_json(f"{api}/getUpdates", params={
                "offset": offset, "timeout": 20,
                "allowed_updates": json.dumps(["message", "my_chat_member"]),
            }, timeout=25)
        except (urllib.error.URLError, TimeoutError):
            continue
        for upd in data.get("result", []):
            offset = upd["update_id"] + 1
            msg = upd.get("message")
            if msg:
                chat = msg.get("chat", {})
                if chat.get("type") == "private" and user_id is None:
                    user_id = msg["from"]["id"]
                    who = msg["from"].get("username") or msg["from"].get("first_name") or user_id
                    print(f"✅ user_id получен: {user_id} (от @{who})")
                elif chat.get("type") in ("group", "supergroup") and group_id is None:
                    group_id = chat["id"]
                    group_title = chat.get("title", "")
                    print(f"✅ group_id получен: {group_id} ({group_title})")
            cm = upd.get("my_chat_member")
            if cm and group_id is None:
                chat = cm.get("chat", {})
                if chat.get("type") in ("group", "supergroup"):
                    group_id = chat["id"]
                    group_title = chat.get("title", "")
                    print(f"✅ Бот добавлен в группу: {group_id} ({group_title})")
    return user_id, group_id


def pip_install(requirements_file):
    cmd = [sys.executable, "-m", "pip", "install", "-r", requirements_file]
    r = subprocess.run(cmd + ["--break-system-packages"], capture_output=True, text=True)
    if r.returncode != 0:
        r = subprocess.run(cmd, capture_output=True, text=True)
    return r.returncode == 0, (r.stdout + r.stderr)


def build_whisper():
    """Собирает whisper.cpp из исходников и качает модель small (CMake Release,
    GGML_NATIVE=ON сам подбирает оптимизации под текущий CPU, ничего
    специфичного вручную не требуется). Пути должны совпадать с
    WHISPER_BIN/WHISPER_MODEL в bot.py."""
    home = os.path.expanduser("~")
    wdir = os.path.join(home, "whisper.cpp")
    bin_path = os.path.join(wdir, "build", "bin", "whisper-cli")
    model_path = os.path.join(wdir, "models", "ggml-small.bin")

    if os.path.exists(bin_path) and os.path.exists(model_path):
        print(f"✅ whisper.cpp уже собран: {bin_path}")
        return True

    print("Ставлю системные зависимости (git, cmake, build-essential, ffmpeg)...")
    subprocess.run(["sudo", "apt-get", "install", "-y", "git", "cmake", "build-essential", "ffmpeg"], check=False)

    if not os.path.exists(wdir):
        print("Клонирую whisper.cpp...")
        r = subprocess.run(["git", "clone", "https://github.com/ggerganov/whisper.cpp.git", wdir])
        if r.returncode != 0:
            print("❌ Не удалось склонировать whisper.cpp.")
            return False

    print("Собираю (cmake Release, может занять несколько минут)...")
    r = subprocess.run(["cmake", "-B", "build", "-DCMAKE_BUILD_TYPE=Release"], cwd=wdir)
    if r.returncode != 0:
        print("❌ cmake configure упал.")
        return False
    r = subprocess.run(["cmake", "--build", "build", "-j", str(os.cpu_count() or 2), "--config", "Release"], cwd=wdir)
    if r.returncode != 0:
        print("❌ Сборка упала.")
        return False

    if not os.path.exists(model_path):
        print("Скачиваю модель small (~465МБ)...")
        r = subprocess.run(["bash", "./models/download-ggml-model.sh", "small"], cwd=wdir)
        if r.returncode != 0:
            print("❌ Не удалось скачать модель.")
            return False

    ok = os.path.exists(bin_path) and os.path.exists(model_path)
    print("✅ whisper.cpp готов." if ok else "❌ Собралось, но бинарник/модель не на ожидаемом месте — проверь вручную.")
    return ok


def setup_memory():
    """Сеет память (MEMORY.md + base_memory.md) и делает симлинк с автопамяти Claude Code
    (~/.claude/projects/<slug пути проекта>/memory) на неё — иначе Claude Code писал бы в свою
    папку, а коннектор memory и кнопка «Память» бота — в другую, и память разъехалась бы."""
    sys.path.insert(0, BRIDGE_DIR)
    import re
    import memory_store
    memory_store.ensure_memory_seed()
    print(f"Память: {memory_store.MEMORY_DIR}")
    slug = re.sub(r"[^A-Za-z0-9]", "-", BRIDGE_DIR)
    link = os.path.expanduser(f"~/.claude/projects/{slug}/memory")
    os.makedirs(os.path.dirname(link), exist_ok=True)
    if os.path.islink(link):
        print("Симлинк автопамяти Claude Code уже есть.")
        return
    if os.path.isdir(link):
        for name in os.listdir(link):
            dst = os.path.join(memory_store.MEMORY_DIR, name)
            if not os.path.exists(dst):
                shutil.copy2(os.path.join(link, name), dst)
        backup = f"{link}.bak-{int(time.time())}"
        os.rename(link, backup)
        print(f"Старая папка автопамяти перенесена в {backup} (недостающие файлы скопированы в общую память).")
    os.symlink(memory_store.MEMORY_DIR, link)
    print(f"Симлинк автопамяти Claude Code: {link} -> {memory_store.MEMORY_DIR}")


def setup_local_botapi(token):
    """Поднимает локальный Bot API (docker) и переводит на него бота. Возвращает True, если бот теперь работает
    через локальный сервер (тогда API_BASE уже переключён), иначе False — остаёмся на облаке."""
    global API_BASE
    print("Локальный Bot API: файлы до 2 ГБ вместо 20 МБ и стабильная связь (в облаке бот ловил 1–2 обрыва в час).")
    print("Нужны Docker и api_id/api_hash с https://my.telegram.org (API development tools).")
    if not confirm("Поднять локальный Bot API?", default_yes=True):
        return False
    if not shutil.which("docker"):
        print("❌ Docker не найден. Установите: curl -fsSL https://get.docker.com | sh  (и: sudo usermod -aG docker $USER)")
        return False
    if subprocess.run(["docker", "info"], capture_output=True).returncode != 0:
        print("❌ Docker не отвечает (демон не запущен или у пользователя нет доступа: sudo usermod -aG docker $USER).")
        return False
    os.makedirs(os.path.join(BOT_API_DIR, "data"), exist_ok=True)
    os.chmod(BOT_API_DIR, 0o700)
    env_file = os.path.join(BOT_API_DIR, "server.env")
    if os.path.exists(env_file) and not confirm(f"{env_file} уже есть — оставить его?", default_yes=True):
        os.unlink(env_file)
    if not os.path.exists(env_file):
        api_id = ask("api_id (число)")
        api_hash = ask("api_hash")
        with open(env_file, "w") as f:
            f.write(f"TELEGRAM_API_ID={api_id}\nTELEGRAM_API_HASH={api_hash}\nTELEGRAM_LOCAL=1\n")
        os.chmod(env_file, 0o600)
        print(f"✅ Записано: {env_file}")
    run_sh = os.path.join(BRIDGE_DIR, "botapi", "run.sh")
    print("Запускаю контейнер (первый раз образ скачивается, это минута-две)…")
    r = subprocess.run(["sh", run_sh], capture_output=True, text=True, env={**os.environ, "BOT_API_DIR": BOT_API_DIR})
    if r.returncode != 0:
        print(f"❌ Не удалось запустить контейнер: {(r.stderr or r.stdout).strip()[-400:]}")
        return False
    for _ in range(20):
        time.sleep(1.5)
        try:
            if http_json(f"{BOT_API_LOCAL_URL}/bot{token}/getMe", timeout=8).get("ok"):
                break
        except Exception:
            pass
    else:
        print("❌ Локальный сервер не ответил на getMe (смотрите: docker logs telegram-bot-api).")
        return False
    print("✅ Локальный Bot API отвечает.")
    print("Дальше нужно выйти из облачного Bot API (logOut): после этого бот работает ТОЛЬКО через локальный")
    print("сервер, а вернуться в облако можно не раньше чем через 10 минут.")
    if not confirm("Сделать logOut сейчас?", default_yes=True):
        print("Пропущено — остаёмся на облачном Bot API (локальный сервер запущен, но не используется).")
        return False
    try:
        res = http_json(f"{CLOUD_API}/bot{token}/logOut", timeout=20)
        if not res.get("ok"):
            print(f"⚠️  logOut вернул: {res} — возможно бот уже вышел из облака, продолжаю.")
    except Exception as e:
        print(f"⚠️  logOut не удался ({e}) — продолжаю, локальный сервер всё равно рабочий.")
    API_BASE = BOT_API_LOCAL_URL
    return True


def main():
    total = 11
    print("Установщик Telegram-bridge бота")
    print(f"Директория: {BRIDGE_DIR}")

    # 1. Токен бота
    step(1, total, "Токен Telegram-бота")
    if os.path.exists(SECRETS_FILE) and not confirm(f"{SECRETS_FILE} уже существует — перегенерировать секреты?", default_yes=False):
        bot_token = None
    else:
        while True:
            bot_token = ask("Токен бота (от @BotFather)")
            me = get_me(bot_token)
            if me:
                bot_username = me["username"]
                print(f"✅ Токен рабочий: @{bot_username}")
                break

    # 2. Локальный Bot API
    local_botapi = False
    if bot_token is not None:
        step(2, total, "Локальный Telegram Bot API (docker)")
        local_botapi = setup_local_botapi(bot_token)

    # 3. Moltbook
    step(3, total, "Moltbook API-ключ (необязательно)")
    moltbook_key = None
    if bot_token is not None:
        moltbook_key = ask("Moltbook API-ключ (Enter чтобы пропустить)", allow_empty=True) or None

    # 3. user_id / group_id
    user_id = group_id = None
    if bot_token is not None:
        step(4, total, "Определение user_id и group_id")
        user_id, group_id = wait_for_ids(bot_token, bot_username)
        if user_id is None:
            user_id = ask("Не поймали user_id автоматически — введи вручную (или Enter чтобы пропустить)", allow_empty=True) or None
        if group_id is None:
            group_id = ask("Не поймали group_id автоматически — введи вручную (или Enter чтобы пропустить)", allow_empty=True) or None

    # 4. secrets_local.py
    if bot_token is not None:
        step(5, total, "Генерация secrets_local.py")
        lines = [
            '"""Локальные секреты — этот файл НЕ коммитится в git (см. .gitignore).',
            'Живёт только на самой машине. При переносе на новую машину создать заново вручную."""',
            "",
            f'TELEGRAM_BOT_TOKEN = "{bot_token}"',
            f'MOLTBOOK_API_KEY = "{moltbook_key or ""}"',
            "",
            "# Личные Telegram ID — не секрет в смысле доступа, но не должны палиться",
            "# в публичном коде, если репозиторий когда-нибудь станет открытым.",
            f"ALLOWED_USER_ID = {user_id or 0}",
            f"GROUP_CHAT_ID = {group_id or 0}",
            "",
            '# Адрес Bot API: локальный сервер (docker, см. botapi/README.md) или облако',
            f'BOT_API_BASE = "{API_BASE}"',
            "",
        ]
        with open(SECRETS_FILE, "w") as f:
            f.write("\n".join(lines))
        os.chmod(SECRETS_FILE, 0o600)
        print(f"✅ Записано: {SECRETS_FILE}")
        if not user_id or not group_id:
            print("⚠️  user_id или group_id не заполнены — поправь secrets_local.py вручную перед запуском.")

    # 5. Зависимости
    step(6, total, "Python-зависимости")
    req_file = os.path.join(BRIDGE_DIR, "requirements.txt")
    ok, log = pip_install(req_file)
    print("✅ Зависимости установлены" if ok else f"❌ Ошибка pip install:\n{log[-2000:]}")

    # 6. whisper.cpp (транскрипция голосовых)
    step(7, total, "whisper.cpp (голосовые сообщения)")
    build_whisper()

    # 7. Claude Code
    step(8, total, "Claude Code")
    if shutil.which("claude") or os.path.exists(CLAUDE_BIN):
        print("✅ Claude Code уже установлен.")
    else:
        print("❌ Claude Code не найден.")
        print("Установи по официальной инструкции: https://claude.com/claude-code")
        input("Нажми Enter когда установишь и войдёшь в аккаунт (claude login)...")

    # 7. systemd
    step(9, total, "Systemd-служба")
    if shutil.which("systemctl") and confirm("Зарегистрировать и запустить systemd-службу?"):
        unit = f"""[Unit]
Description=Claude Telegram Bot
After=network-online.target docker.service
Wants=network-online.target

[Service]
Type=simple
User={getpass.getuser()}
WorkingDirectory={BRIDGE_DIR}
ExecStart={sys.executable} {os.path.join(BRIDGE_DIR, "bot.py")}
Restart=always
RestartSec=10
Environment=HOME={os.path.expanduser("~")}
Environment=PYTHONUNBUFFERED=1
StandardOutput=append:{os.path.join(BRIDGE_DIR, "bot.log")}
StandardError=append:{os.path.join(BRIDGE_DIR, "bot.log")}

[Install]
WantedBy=multi-user.target
"""
        subprocess.run(["sudo", "tee", SERVICE_PATH], input=unit, text=True,
                       stdout=subprocess.DEVNULL, check=False)
        subprocess.run(["sudo", "systemctl", "daemon-reload"], check=False)
        subprocess.run(["sudo", "systemctl", "enable", SERVICE_NAME], check=False)
        subprocess.run(["sudo", "systemctl", "restart", SERVICE_NAME], check=False)
        r = subprocess.run(["systemctl", "is-active", "--quiet", SERVICE_NAME])
        print("✅ Служба запущена." if r.returncode == 0 else "⚠️  Служба не стартовала — смотри `journalctl -u claude-telegram`.")
    else:
        print("Пропущено.")

    # 8. MCP-коннекторы
    step(10, total, "Память и MCP-коннекторы")
    setup_memory()
    if shutil.which("claude") or os.path.exists(CLAUDE_BIN):
        claude_cmd = shutil.which("claude") or CLAUDE_BIN
        connectors = {
            "memory": "memory_mcp_server.py",
            "history": "history_mcp_server.py",
            "moltbook": "moltbook_mcp_server.py",
            "bot_scheduler": "bot_scheduler_mcp_server.py",
            "telegram": "telegram_mcp_server.py",
        }
        for name, script in connectors.items():
            subprocess.run([claude_cmd, "mcp", "remove", name], capture_output=True)
            r = subprocess.run(
                [claude_cmd, "mcp", "add", name, "--", sys.executable, os.path.join(BRIDGE_DIR, script)],
                capture_output=True, text=True, cwd=BRIDGE_DIR,
            )
            print(f"{'✅' if r.returncode == 0 else '❌'} {name}")
    else:
        print("Пропущено — Claude Code не найден.")

    # 9. Ритуал
    step(11, total, "Кабудди-буда")
    kabuddi = os.path.join(BRIDGE_DIR, "fill_kabuddi.py")
    if os.path.exists(kabuddi):
        subprocess.run([sys.executable, kabuddi], cwd=BRIDGE_DIR)

    print("\n=== Готово ===")
    print(f"Директория бота: {BRIDGE_DIR}")
    print(f"Секреты: {SECRETS_FILE}")
    print(f"Статус службы: sudo systemctl status {SERVICE_NAME}")


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        print("\nПрервано.")
        sys.exit(1)
