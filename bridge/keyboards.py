"""Клавиатуры, инлайн-меню и кнопки Claude (telegram_send_buttons)."""

from bridge import storage


def get_main_keyboard(topic_id=None, selective=False):
    mode = storage.get_bot_mode(topic_id)
    if mode == "claude":
        last_row = [{"text": "📤 Резервный"}, {"text": "🖥 Команды"}]
    elif mode == "passthrough":
        last_row = [{"text": "🤖 Claude"}, {"text": "🖥 Команды"}]
    else:  # shell
        last_row = [{"text": "🤖 Claude"}, {"text": "📤 Резервный"}]
    kb = {
        "keyboard": [
            [{"text": "🔄 Сброс"}, {"text": "🏓 Пинг"}, {"text": "📊 Инфо"}],
            [{"text": "🌀 Вентилятор"}, {"text": "🔌 Питание"}, {"text": "⚙️ Настройки"}],
            [{"text": "📝 Память"}, {"text": "📋 Мои записи"}, {"text": "⏰ Напоминание"}],
            [{"text": "⏱ Лимиты"}],
            [{"text": "➕ Создать тему"}, {"text": "♟ Шахматы"}],
            last_row
        ],
        "resize_keyboard": True,
        "selective": selective,
        "persistent": not selective
    }
    return kb

# Алиасы claude CLI (--model fable/opus/sonnet/haiku) — каждый сам резолвится в последнюю
# версию модели этого семейства, поэтому конфиг не хранит конкретные версии-константы.
MODEL_LABELS = {
    "fable": "Fable",
    "sonnet": "Sonnet",
    "opus": "Opus",
    "haiku": "Haiku",
}

def get_settings_inline():
    s = storage.load_settings()
    style = s["style"]
    model = s.get("model", "sonnet")
    style_label = "📝 Подробный" if style == "detailed" else "✂️ Краткий"
    style_toggle = "style_detailed" if style == "brief" else "style_brief"
    style_next = "📝 Подробный →" if style == "brief" else "✂️ Краткий →"

    def model_btn(mid, label):
        mark = " ✓" if model == mid else ""
        return {"text": f"{label}{mark}", "callback_data": f"settings_model_{mid}"}

    return {
        "inline_keyboard": [
            [{"text": f"Стиль: {style_label}", "callback_data": "settings_noop"},
             {"text": style_next, "callback_data": style_toggle}],
            [{"text": "🤖 Модель:", "callback_data": "settings_noop"}],
            [model_btn("fable", "Fable"), model_btn("sonnet", "Sonnet")],
            [model_btn("opus", "Opus"), model_btn("haiku", "Haiku")],
            [{"text": "🗑 Очистить историю", "callback_data": "settings_clear_history"}],
        ]
    }

POWER_INLINE = {
    "inline_keyboard": [
        [
            {"text": "🔁 Перезагрузить", "callback_data": "power_reboot_confirm"},
            {"text": "⛔ Выключить", "callback_data": "power_off_confirm"},
        ],
        [
            {"text": "⏱ Отложить выключение", "callback_data": "power_delayed_off"},
        ],
        [
            {"text": "❌ Отмена", "callback_data": "power_cancel"},
        ]
    ]
}

DELAYED_OFF_INLINE = {
    "inline_keyboard": [
        [
            {"text": "5 мин", "callback_data": "power_delayed_5"},
            {"text": "15 мин", "callback_data": "power_delayed_15"},
            {"text": "30 мин", "callback_data": "power_delayed_30"},
        ],
        [
            {"text": "1 час", "callback_data": "power_delayed_60"},
            {"text": "2 часа", "callback_data": "power_delayed_120"},
        ],
        [
            {"text": "❌ Отмена", "callback_data": "power_cancel"},
        ]
    ]
}

REBOOT_CONFIRM_INLINE = {
    "inline_keyboard": [[
        {"text": "✅ Да, перезагрузить", "callback_data": "power_reboot_do"},
        {"text": "❌ Отмена", "callback_data": "power_cancel"},
    ]]
}

POWEROFF_CONFIRM_INLINE = {
    "inline_keyboard": [[
        {"text": "✅ Да, выключить", "callback_data": "power_off_do"},
        {"text": "❌ Отмена", "callback_data": "power_cancel"},
    ]]
}

# Реестр инлайн-кнопок от Claude
_claude_buttons = {}  # cb_key -> спецификация кнопки (см. _button_spec)
_claude_btn_counter = 0

def _next_btn_key():
    global _claude_btn_counter
    _claude_btn_counter += 1
    return f"ccb_{_claude_btn_counter}"

BUTTON_ACTIONS = ("restart", "stop", "reset_session", "clear_log", "info", "limits")

def _button_spec(btn):
    """Превращает описание кнопки из тула telegram_send_buttons в спецификацию для _claude_buttons
    (или None, если кнопка некорректна). Тип определяется по полям: code → код (bash/python),
    action → действие бота (BUTTON_ACTIONS; у restart необязателен prompt), prompt → запуск Claude
    с этим промптом (необязательный model — алиас модели только для этого запуска)."""
    label = str(btn.get("label", "")).strip()
    if not label:
        return None
    code = str(btn.get("code") or "").strip()
    action = str(btn.get("action") or "").strip()
    prompt = str(btn.get("prompt") or "").strip()
    if code:
        lang = str(btn.get("lang") or "bash").strip().lower()
        if lang in ("py", "python3"):
            lang = "python"
        if lang not in ("bash", "python"):
            return None
        return {"kind": "code", "label": label, "code": code, "lang": lang}
    if action:
        if action == "__bot_restart__":
            action = "restart"
        if action not in BUTTON_ACTIONS:
            return None
        return {"kind": "action", "label": label, "action": action, "prompt": prompt}
    if prompt:
        model = str(btn.get("model") or "").strip().lower()
        return {"kind": "prompt", "label": label, "prompt": prompt,
                "model": model if model in MODEL_LABELS else None}
    return None

def build_claude_buttons(rows):
    """Строит inline_keyboard из аргумента buttons тула telegram_send_buttons:
    список рядов, ряд — список описаний кнопок (см. _button_spec). Возвращает inline_keyboard | None."""
    inline_keyboard = []
    for row_def in rows or []:
        row = []
        for btn in row_def if isinstance(row_def, list) else [row_def]:
            spec = _button_spec(btn) if isinstance(btn, dict) else None
            if not spec:
                continue
            cb_key = _next_btn_key()
            _claude_buttons[cb_key] = spec
            row.append({"text": spec["label"], "callback_data": cb_key})
        if row:
            inline_keyboard.append(row)

    if not inline_keyboard:
        return None

    # Чистим старые кнопки (держим последние 200)
    if len(_claude_buttons) > 200:
        oldest = sorted(_claude_buttons.keys(), key=lambda k: int(k.split('_')[1]))[:len(_claude_buttons) - 200]
        for k in oldest:
            del _claude_buttons[k]

    return {"inline_keyboard": inline_keyboard}

FAN_INLINE = {
    "inline_keyboard": [
        [
            {"text": "🔃 Авто", "callback_data": "fan_auto"},
            {"text": "⏹ Стоп", "callback_data": "fan_0"},
            {"text": "💨 50%", "callback_data": "fan_50"},
            {"text": "💨 100%", "callback_data": "fan_100"},
        ]
    ]
}

INFO_INLINE = {
    "inline_keyboard": [
        [{"text": "🔄 Обновить", "callback_data": "info_refresh"},
         {"text": "⏰ Напоминания", "callback_data": "info_reminders"}],
        [{"text": "🧹 Очистить лог", "callback_data": "info_clear_log"}]
    ]
}


STARTUP_INLINE = {
    "inline_keyboard": [
        [
            {"text": "📊 Инфо", "callback_data": "nav_info"},
            {"text": "🌀 Вентилятор", "callback_data": "nav_fan"},
            {"text": "🔌 Питание", "callback_data": "nav_power"},
        ],
        [
            {"text": "⚙️ Настройки", "callback_data": "nav_settings"},
            {"text": "📝 Память", "callback_data": "nav_memory"},
            {"text": "📋 Мои записи", "callback_data": "nav_records"},
        ],
        [
            {"text": "⏰ Напоминания", "callback_data": "nav_reminders"},
            {"text": "🏓 Пинг", "callback_data": "nav_ping"},
            {"text": "🔄 Сброс", "callback_data": "nav_reset"},
        ],
        [
            {"text": "⏱ Лимиты", "callback_data": "nav_limits"},
        ],
        [
            {"text": "🤖 Claude", "callback_data": "nav_mode_claude"},
            {"text": "📤 Резервный", "callback_data": "nav_mode_passthrough"},
            {"text": "🖥 Команды", "callback_data": "nav_mode_shell"},
        ],
        [
            {"text": "➕ Создать тему", "callback_data": "nav_create_topic"},
        ],
    ]
}
SECTIONS_NAV_INLINE = STARTUP_INLINE
