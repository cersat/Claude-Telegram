"""Система: вентилятор, инфо, лимиты, shell/python-команды, Gemma-песочница, счётчик ошибок лога."""

import time
import subprocess
import os
import re
from bridge import consts, core, shared, storage


FAN_PWM = "/sys/class/hwmon/hwmon2/pwm1"
FAN_ENABLE = "/sys/class/hwmon/hwmon2/pwm1_enable"

def fan_status():
    try:
        pwm = int(open(FAN_PWM).read().strip())
        enable = int(open(FAN_ENABLE).read().strip())
        pct = round(pwm / 255 * 100)
        mode = "авто" if enable == 2 else "ручной"
        try:
            rpm = int(open("/sys/class/hwmon/hwmon2/fan1_input").read().strip())
            rpm_str = f", {rpm} об/мин"
        except Exception:
            rpm_str = ""
        return f"режим: {mode}, PWM: {pwm}/255 ({pct}%){rpm_str}"
    except Exception as e:
        return f"ошибка: {e}"

THERMAL_ZONE_MODE = "/sys/class/thermal/thermal_zone0/mode"

def fan_set(enable, pwm=None):
    try:
        if enable == 2:
            # Авто: включаем thermal zone обратно
            subprocess.run(["sudo", "tee", THERMAL_ZONE_MODE], input="enabled", text=True, capture_output=True)
            subprocess.run(["sudo", "tee", FAN_ENABLE], input=str(enable), text=True, capture_output=True)
        else:
            # Ручной: отключаем thermal zone, чтобы не сбрасывала PWM
            subprocess.run(["sudo", "tee", THERMAL_ZONE_MODE], input="disabled", text=True, capture_output=True)
            if pwm is not None:
                subprocess.run(["sudo", "tee", FAN_PWM], input=str(pwm), text=True, capture_output=True)
            subprocess.run(["sudo", "tee", FAN_ENABLE], input=str(enable), text=True, capture_output=True)
            if pwm is not None:
                time.sleep(2)  # ждём обновления датчика оборотов
    except Exception as e:
        return f"ошибка: {e}"


def get_info_text():
    lines = ["📊 *Состояние системы*"]

    # Температура CPU
    try:
        temp_raw = subprocess.check_output(["vcgencmd", "measure_temp"], text=True, timeout=3).strip()
        temp = temp_raw.replace("temp=", "")
    except Exception:
        temp = "н/д"
    lines.append(f"🌡 Температура: `{temp}`")

    # Частота CPU
    try:
        freq_raw = subprocess.check_output(["vcgencmd", "measure_clock", "arm"], text=True, timeout=3).strip()
        freq_hz = int(freq_raw.split("=")[1])
        freq_mhz = freq_hz // 1_000_000
        lines.append(f"⚡ Частота CPU: `{freq_mhz} МГц`")
    except Exception:
        lines.append("⚡ Частота CPU: н/д")

    # Нагрузка CPU
    try:
        load = open("/proc/loadavg").read().split()
        lines.append(f"💻 Нагрузка (1/5/15 мин): `{load[0]}  {load[1]}  {load[2]}`")
    except Exception:
        lines.append("💻 Нагрузка: н/д")

    # ОЗУ
    try:
        mem = subprocess.check_output(["free", "-m"], text=True, timeout=3).splitlines()
        m = mem[1].split()
        total, used, free = m[1], m[2], m[3]
        pct = round(int(used) / int(total) * 100)
        lines.append(f"💾 ОЗУ: `{used}/{total} МБ` ({pct}%)")
    except Exception:
        lines.append("💾 ОЗУ: н/д")

    # Своп
    try:
        s = mem[2].split()
        swap_total, swap_used = s[1], s[2]
        if int(swap_total) > 0:
            swap_pct = round(int(swap_used) / int(swap_total) * 100)
            lines.append(f"🔄 Своп: `{swap_used}/{swap_total} МБ` ({swap_pct}%)")
        else:
            lines.append("🔄 Своп: выключен")
    except Exception:
        lines.append("🔄 Своп: н/д")

    # Диск (читаем через statvfs — мгновенно, без subprocess)
    try:
        st = os.statvfs("/")
        total_b = st.f_blocks * st.f_frsize
        free_b = st.f_bavail * st.f_frsize
        used_b = total_b - st.f_bfree * st.f_frsize
        def _fmt(b):
            if b >= 1 << 30:
                return f"{b / (1 << 30):.1f}G"
            return f"{b / (1 << 20):.0f}M"
        pct_d = round(used_b / total_b * 100) if total_b else 0
        lines.append(f"💿 Диск: `{_fmt(used_b)}/{_fmt(total_b)}` ({pct_d}%)")
    except Exception:
        lines.append("💿 Диск: н/д")

    # Uptime
    try:
        up_raw = open("/proc/uptime").read().split()[0]
        up_sec = int(float(up_raw))
        days, rem = divmod(up_sec, 86400)
        hours, rem = divmod(rem, 3600)
        mins = rem // 60
        parts = []
        if days:
            parts.append(f"{days}д")
        if hours:
            parts.append(f"{hours}ч")
        parts.append(f"{mins}мин")
        lines.append(f"⏱ Uptime: `{' '.join(parts)}`")
    except Exception:
        lines.append("⏱ Uptime: н/д")

    # Вентилятор
    lines.append(f"🌀 Вентилятор: {fan_status()}")

    # Размер лога
    try:
        log_bytes = os.path.getsize(consts.BOT_LOG_FILE)
        if log_bytes >= 1 << 20:
            log_size = f"{log_bytes / (1 << 20):.1f} МБ"
        else:
            log_size = f"{log_bytes / 1024:.0f} КБ"
        lines.append(f"📝 Лог: `{log_size}`")
    except Exception:
        lines.append("📝 Лог: н/д")

    # Ошибки в логе
    err_count = count_log_errors()
    mark = "⚠️ " if err_count > ERROR_LOG_THRESHOLD else ""
    lines.append(f"{mark}❗ Ошибок в логе: `{err_count}`")

    # Сетевые сбои (пассивно, без уведомлений)
    net_count, net_last = count_network_failures()
    api_count, api_last = count_api_failures()
    total = net_count + (api_count or 0)
    from datetime import datetime
    stamps = []
    for t in (net_last, api_last):
        if t:
            stamps.append(datetime.strptime(f"{core.now_local().year} {t}", "%Y %d.%m %H:%M"))
    last_part = f" (последний {max(stamps).strftime('%d.%m %H:%M')})" if stamps else ""
    api_note = " · Bot API недоступен для подсчёта" if api_count is None else ""
    lines.append(f"🌐 Сетевых сбоев за сутки: `{total}`{last_part}{api_note}")

    # Время обновления
    now = core.now_local().strftime("%H:%M:%S")
    lines.append(f"\n_Обновлено в {now}_")

    return "\n".join(lines)


def get_usage_text():
    """Запускает /usage через claude CLI — синтетический ответ самого Claude Code
    (не настоящий запрос к API, total_cost_usd=0, мгновенно), с процентом использования
    текущей 5-часовой сессии и недели — то, что реально показывает как близко к лимиту,
    в отличие от бинарного status=allowed/rejected из rate_limit_event."""
    try:
        model = storage.load_settings().get("model", "sonnet")
        result = subprocess.run(
            [consts.CLAUDE_BIN, "--dangerously-skip-permissions",
             "--model", model, "-p", "/usage"],
            capture_output=True, text=True, timeout=20,
            env={**os.environ, "HOME": consts.HOME}
        )
        text = result.stdout.strip()
        return f"⏱ {text}" if text else "⚠️ Не удалось получить данные об использовании."
    except Exception as e:
        return f"⚠️ Ошибка получения /usage: {e}"


def run_shell_command(cmd):
    """Выполняет shell-команду, сохраняя директорию. Возвращает текст ответа."""
    cmd = cmd.strip()

    # Обрабатываем cd отдельно — subprocess не меняет shell_cwd
    cd_match = re.match(r'^cd\s*(.*)', cmd)
    if cd_match:
        path = cd_match.group(1).strip()
        if not path or path == '~':
            new_dir = consts.HOME
        elif path.startswith('/'):
            new_dir = os.path.normpath(path)
        else:
            new_dir = os.path.normpath(os.path.join(shared.shell_cwd, path))
        if os.path.isdir(new_dir):
            shared.shell_cwd = new_dir
            return f"📁 {shared.shell_cwd}"
        else:
            return f"❌ Нет такой директории: {new_dir}"

    try:
        result = subprocess.run(
            cmd, shell=True, cwd=shared.shell_cwd,
            capture_output=True, text=True, timeout=60,
            executable='/bin/bash',
            env={**os.environ, "HOME": consts.HOME}
        )
        stdout = result.stdout
        stderr = result.stderr
        retcode = result.returncode

        output = ""
        if stdout:
            output += stdout
        if stderr:
            if output:
                output += "\n[stderr]\n"
            output += stderr

        if not output.strip():
            output = "(нет вывода)"

        # Обрезаем если слишком длинный
        if len(output) > 3800:
            output = "...(обрезано)...\n" + output[-3800:]

        status = "✅" if retcode == 0 else f"❌ код {retcode}"
        return f"$ {cmd}\n{status}\n\n{output.rstrip()}\n\n📁 {shared.shell_cwd}"
    except subprocess.TimeoutExpired:
        return f"$ {cmd}\n⏱ Таймаут (60с)"
    except Exception as e:
        return f"$ {cmd}\n❌ Ошибка: {e}"


def run_python_code(code):
    """Выполняет python-код кнопки (python3 -c) в shell_cwd, таймаут 60с. Возвращает текст вывода."""
    try:
        result = subprocess.run(
            ["python3", "-c", code], cwd=shared.shell_cwd,
            capture_output=True, text=True, timeout=60,
            env={**os.environ, "HOME": consts.HOME}
        )
        output = result.stdout
        if result.stderr:
            output += ("\n[stderr]\n" if output else "") + result.stderr
        if not output.strip():
            output = "(нет вывода)"
        if len(output) > 3800:
            output = "...(обрезано)...\n" + output[-3800:]
        status = "✅" if result.returncode == 0 else f"❌ код {result.returncode}"
        return f"🐍 python3\n{status}\n\n{output.rstrip()}"
    except subprocess.TimeoutExpired:
        return "🐍 python3\n⏱ Таймаут (60с)"
    except Exception as e:
        return f"🐍 python3\n❌ Ошибка: {e}"


GEMMA_ARENA_UNIT = "gemma-arena"


def gemma_arena_running():
    r = subprocess.run(["systemctl", "is-active", "--quiet", GEMMA_ARENA_UNIT])
    return r.returncode == 0


def run_in_gemma_sandbox(command):
    """Выполняет команду в отдельной пользовательской Docker-песочнице (тот же образ и /workspace,
    что видят агенты Gemma Arena), не мешая песочницам самих агентов."""
    d = os.path.join(consts.GEMMA_SANDBOX_ROOT, "agent_user")
    os.makedirs(d, exist_ok=True)
    os.makedirs(consts.GEMMA_WORKSPACE_DIR, exist_ok=True)
    try:
        result = subprocess.run(
            [
                "sudo", "docker", "run", "--rm",
                "--cpus=1",
                "-u", f"{os.getuid()}:{os.getgid()}",
                "-v", f"{d}:/sandbox",
                "-v", f"{consts.GEMMA_WORKSPACE_DIR}:/workspace",
                "-w", "/sandbox",
                consts.GEMMA_SANDBOX_IMAGE,
                "bash", "-c", command,
            ],
            capture_output=True, text=True, timeout=30,
        )
        lines = (result.stdout + result.stderr).splitlines()
        lines = [l for l in lines if "kernel does not support memory limit" not in l]
        out = "\n".join(lines).strip()
        return out if out else "(пусто)"
    except subprocess.TimeoutExpired:
        return "(команда не уложилась в 30 секунд)"
    except Exception as e:
        return f"(ошибка запуска песочницы: {e})"


ERROR_LOG_THRESHOLD = 100
_STAMP_RE = re.compile(r"^\[(\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2})\] ")
_NET_FAIL_RE = re.compile(r"Network error \(ignored\)|Network/socket error \(ignored\)|\[send_single network\]|\[answer_callback network\]")


def _strip_stamp(line):
    return _STAMP_RE.sub("", line, count=1)


def count_network_failures(hours=24):
    """Считает сетевые сбои в bot.log за последние hours часов (только строки с меткой времени).
    Возвращает (количество, время последнего 'HH:MM' или None)."""
    from datetime import datetime, timedelta
    border = core.now_local() - timedelta(hours=hours)
    count, last = 0, None
    try:
        with open(consts.BOT_LOG_FILE, "r", errors="ignore") as f:
            for line in f:
                m = _STAMP_RE.match(line)
                if not m or not _NET_FAIL_RE.search(line):
                    continue
                ts = datetime.strptime(m.group(1), "%Y-%m-%d %H:%M:%S")
                if ts >= border:
                    count += 1
                    last = ts
    except FileNotFoundError:
        pass
    return count, (last.strftime("%d.%m %H:%M") if last else None)

_log_error_cache = {"count": 0, "checked_at": 0, "last": ""}
_ERROR_LINE_RE = re.compile(
    r"^(\[[^\[\]]*\b(error|exception)\b[^\[\]]*\]"
    r"|\[REACTION_SET\] (error|exception)"
    r"|Error:"
    r"|Traceback \(most recent call last\)"
    r"|[\w.]*(Error|Exception)\b.*:)",
    re.IGNORECASE,
)

_API_FAIL_RE = re.compile(r"was closed: \[Error|Failed to connect to")
_API_TS_RE = re.compile(r"\]\[(\d{10})\.\d+\]\[")
_api_fail_cache = {"at": 0, "value": (0, None)}


def count_api_failures(hours=24):
    """Сетевые сбои самого локального Bot API (docker-контейнер telegram-bot-api, его связь с Telegram):
    строки лога «connection … was closed: [Error …]» и «Failed to connect to …». Контейнер пишет лог с
    --verbosity=2 (в нём есть и шум «CPU usage», его пропускаем). Возвращает (количество, 'дд.мм чч:мм' последнего
    или None); None вместо числа — контейнер недоступен. Кэш на 60 с: docker logs тяжелее чтения файла."""
    now = time.time()
    if now - _api_fail_cache["at"] < 60:
        return _api_fail_cache["value"]
    try:
        out = subprocess.run(["docker", "logs", "--since", f"{hours}h", "telegram-bot-api"],
                             capture_output=True, text=True, timeout=20, errors="ignore")
        text = out.stdout + out.stderr
        if out.returncode != 0 and not text.strip():
            raise RuntimeError("docker logs не отдал вывод")
    except Exception:
        value = (None, None)
    else:
        from datetime import datetime
        count, last = 0, None
        for line in text.splitlines():
            if not _API_FAIL_RE.search(line):
                continue
            count += 1
            m = _API_TS_RE.search(line)
            if m:
                last = int(m.group(1))
        value = (count, datetime.fromtimestamp(last).strftime("%d.%m %H:%M") if last else None)
    _api_fail_cache.update(at=now, value=value)
    return value


def count_log_errors():
    """Считает строки-ошибки в bot.log (не учитывая ожидаемые/уже обработанные
    'Network error (ignored)' и т.п.) — кэш на 60с, чтобы не перечитывать файл
    на каждое входящее сообщение."""
    now = time.time()
    if now - _log_error_cache["checked_at"] < 60:
        return _log_error_cache["count"]
    count = 0
    last = ""
    try:
        with open(consts.BOT_LOG_FILE, "r", errors="ignore") as f:
            for line in f:
                # "(ignored)" — ожидаемые сетевые. Ошибкой считается только строка, которая
                # НАЧИНАЕТСЯ как диагностика: иначе цитаты в [FINAL_TEXT]/эхо промптов
                # (где просто встречается слово error) счётчик принимал за новые ошибки
                if "(ignored)" in line or "Системная информация" in line:
                    continue
                if _ERROR_LINE_RE.match(_strip_stamp(line)):
                    count += 1
                    last = line.strip()
    except FileNotFoundError:
        pass
    _log_error_cache["count"] = count
    _log_error_cache["last"] = last
    _log_error_cache["checked_at"] = now
    return count
