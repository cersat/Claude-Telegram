"""Ограничение размера bot.log: как только файл дорос до LIMIT, стираются его первые байты, остаются последние KEEP.

Настоящий logrotate так не умеет (он переименовывает файл или обнуляет целиком), поэтому обрезка сделана здесь.
Файл открыт у systemd на дозапись (StandardOutput=append:), поэтому пишем на месте: перезаписываем начало
хвостом и усекаем файл — дескриптор systemd остаётся валидным, а новые строки (O_APPEND) идут уже в новый конец."""

import os
import time

from bridge import consts

LIMIT = 100 * 1024     # размер, с которого начинается обрезка
KEEP = 50 * 1024       # сколько байт с конца остаётся
CHECK_EVERY = 30       # секунд между проверками


def trim_log(path, limit=LIMIT, keep=KEEP):
    """Возвращает сколько байт стёрто (0 — файл ещё меньше limit). Хвост выравнивается по границе строки."""
    try:
        if os.path.getsize(path) < limit:
            return 0
        with open(path, "r+b") as f:
            size = f.seek(0, os.SEEK_END)
            if size < limit:
                return 0
            f.seek(size - keep)
            tail = f.read()
            nl = tail.find(b"\n")
            if 0 <= nl < len(tail) - 1:
                tail = tail[nl + 1:]     # не начинаем с обрезанной посередине строки
            f.seek(0)
            f.write(tail)
            f.truncate(len(tail))
            return size - len(tail)
    except FileNotFoundError:
        return 0


def trim_loop():
    """Фоновый поток бота."""
    while True:
        try:
            erased = trim_log(consts.BOT_LOG_FILE)
            if erased:
                print(f"[logtrim] bot.log достиг {LIMIT // 1024} КБ — стёрто первые {erased // 1024} КБ")
        except Exception as e:
            print(f"[logtrim] ошибка: {e}")
        time.sleep(CHECK_EVERY)
