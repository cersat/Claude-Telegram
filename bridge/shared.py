"""Общее изменяемое состояние (ожидания ввода, cwd командного режима, id меню)."""

from bridge import consts


# Состояние ожидания ввода памяти: None или True (ожидаем текст для нового файла)
pending_memory_add = None
# Состояние редактирования записи памяти: None или имя файла (заменить тело целиком)
pending_memory_edit = None
# Состояние дозаписи в существующий файл памяти: None или имя файла (дописать в конец)
pending_memory_append = None
# Состояние ожидания ввода напоминания: None, "new", или "repeat_interval" (тогда pending_reminder_data содержит {secs, text})
pending_reminder = None
pending_reminder_data = {}
# Состояние ожидания названия новой темы: None или True
pending_create_topic = None
shell_cwd = consts.HOME  # рабочая директория для командного режима
# ID текущего сообщения с меню/инфо, отдельно на каждую тему/чат (chat_id, thread_id) —
# раньше это были одиночные глобальные переменные, из-за чего нажатие кнопки в одной
# теме могло молча отредактировать чужое сообщение, оставшееся от другой темы.
fan_msg_ids = {}
info_msg_ids = {}
power_msg_ids = {}
settings_msg_ids = {}
delayed_off_timer = None  # Таймер отложенного выключения

# Ожидание кода перелогина Claude (см. bridge/authlogin.py): None или {chat_id, thread_id, t, busy}
pending_login = None
