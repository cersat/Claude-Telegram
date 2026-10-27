"""Загрузка медиа, альбомы, транскрипция голосовых (whisper)."""

import shutil
import time
import threading
import requests
import subprocess
import os
from datetime import datetime
from bridge import claude_run, consts, core


_err = threading.local()
BOT_API_DOWNLOAD_LIMIT = 20 * 1024 * 1024  # Bot API не отдаёт ботам файлы больше 20 МБ


def last_download_error():
    """Причина последней неудачной загрузки в этом потоке — по-русски, для сообщения пользователю."""
    return getattr(_err, "text", "")


def _humanize(desc):
    d = (desc or "").lower()
    if "file is too big" in d:
        return "файл больше 20 МБ — Telegram не отдаёт такие файлы ботам"
    if "wrong file_id" in d or "file_id" in d:
        return "Telegram не нашёл файл (устаревший file_id)"
    return desc or "неизвестная ошибка"


def _fetch_file(file_id, attempts=3):
    """Скачивает файл Telegram по file_id: (file_path, bytes) или (None, None); причину неудачи
    см. last_download_error(). Таймауты и повторы обязательны: без них один подвисший коннект
    терял часть альбома. Ответ Telegram с ok=false (файл слишком большой и т.п.) — не повторяем."""
    _err.text = ""
    last = None
    for attempt in range(attempts):
        try:
            # локальный Bot API на getFile сначала целиком скачивает файл у себя (для 1 ГБ — минуты), поэтому
            # ждём ответ долго и при таймауте не повторяем (повтор только плодил бы запросы)
            r = requests.get(f"{core.API}/getFile", params={"file_id": file_id},
                             timeout=(10, 3600 if consts.BOT_API_LOCAL else 20)).json()
            if not r.get("ok"):
                desc = r.get("description", "")
                print(f"[download] getFile отклонён: {desc}")
                _err.text = _humanize(desc)
                return None, None
            file_path = r["result"]["file_path"]
            if consts.BOT_API_LOCAL and file_path.startswith("/"):
                # Локальный Bot API: сервер уже скачал файл на диск и отдаёт абсолютный путь (в контейнере);
                # перекладываем в host-путь и забираем файл целиком, без прогона через память.
                host_path = os.path.join(consts.BOT_API_DATA_DIR, os.path.relpath(file_path, consts.BOT_API_CONTAINER_DIR))
                if not os.path.exists(host_path):
                    raise FileNotFoundError(f"сервер Bot API не положил файл: {host_path}")
                return file_path, host_path
            url = f"https://api.telegram.org/file/bot{core.BOT_TOKEN}/{file_path}"
            resp = requests.get(url, timeout=(10, 60))
            resp.raise_for_status()
            return file_path, resp.content
        except Exception as e:
            last = e
            print(f"[download] попытка {attempt + 1}/{attempts} не удалась: {e}")
            if consts.BOT_API_LOCAL and isinstance(e, requests.exceptions.ReadTimeout):
                break
            time.sleep(1)
    print(f"[download] не удалось скачать {file_id[:12]}…: {last}")
    _err.text = f"сетевая ошибка ({last})"
    return None, None


def _store(source, dest):
    """Кладёт скачанное в dest: source — bytes или путь к файлу, который скачал локальный Bot API
    (файл переносится, а не копируется: иначе он остался бы на диске сервера навсегда)."""
    if isinstance(source, str):
        shutil.move(source, dest)
    else:
        with open(dest, "wb") as f:
            f.write(source)


def download_photo(file_id):
    """Скачивает фото из Telegram, сохраняет в INBOX_DIR и возвращает путь."""
    try:
        file_path, data = _fetch_file(file_id)
        if data is None:
            return None
        suffix = "." + file_path.split(".")[-1] if "." in file_path else ".jpg"
        ts = datetime.now().strftime("%Y%m%d_%H%M%S")
        dest = os.path.join(consts.INBOX_DIR, f"photo_{ts}{suffix}")
        counter = 1
        while os.path.exists(dest):
            dest = os.path.join(consts.INBOX_DIR, f"photo_{ts}_{counter}{suffix}")
            counter += 1
        _store(data, dest)
        return dest
    except Exception as e:
        return None


def download_document(file_id, file_name):
    """Скачивает документ из Telegram, сохраняет в inbox."""
    try:
        file_path, data = _fetch_file(file_id)
        if data is None:
            return None
        # Уникальное имя если файл уже есть
        dest = os.path.join(consts.INBOX_DIR, file_name)
        base, ext = os.path.splitext(file_name)
        counter = 1
        while os.path.exists(dest):
            dest = os.path.join(consts.INBOX_DIR, f"{base}_{counter}{ext}")
            counter += 1
        _store(data, dest)
        return dest
    except Exception as e:
        return None


def download_media(file_id, prefix, default_ext=""):
    """Скачивает любой медиафайл из Telegram, сохраняет в inbox."""
    try:
        file_path, data = _fetch_file(file_id)
        if data is None:
            return None
        suffix = "." + file_path.split(".")[-1] if "." in file_path else default_ext
        ts = datetime.now().strftime("%Y%m%d_%H%M%S")
        dest = os.path.join(consts.INBOX_DIR, f"{prefix}_{ts}{suffix}")
        counter = 1
        while os.path.exists(dest):
            dest = os.path.join(consts.INBOX_DIR, f"{prefix}_{ts}_{counter}{suffix}")
            counter += 1
        _store(data, dest)
        return dest
    except Exception as e:
        return None


# ── Альбомы (несколько файлов одним "сообщением") ──────────────────────────
# Telegram шлёт каждое вложение альбома отдельным апдейтом с одинаковым media_group_id,
# а подпись прикрепляет только к одному из них. Без буферизации бот отвечал бы на
# каждый файл отдельным сообщением/вызовом Claude вместо одного связного ответа.
# Альбомы больше 10 файлов Telegram режет на несколько групп с РАЗНЫМИ media_group_id (16 фото =
# 10 + 6), поэтому копим по (чат, тема), а не по media_group_id: группы одной отправки сливаются.
MEDIA_GROUP_DEBOUNCE = 3.0  # секунд ожидания следующей части альбома (и следующей группы)
media_groups = {}
media_groups_lock = threading.Lock()
_flushed_groups = {}  # media_group_id -> время последней отправки части (для лога о запоздавших частях)


def handle_media_group_item(media_group_id, item, ctx):
    """Копит части альбома по (chat, thread) и планирует flush через debounce-таймер,
    который сбрасывается при каждой новой части. media_group_id нарочно не в ключе."""
    key = (ctx["tgt_chat_id"], ctx["tgt_thread_id"])
    with media_groups_lock:
        group = media_groups.get(key)
        if group is None:
            late = _flushed_groups.get(media_group_id)
            if late is not None:
                # альбом уже отправлялся, а части приходят дальше: для бота это новая группа и новый ход
                print(f"[album split] часть альбома {media_group_id} пришла через {time.time() - late:.1f} с после предыдущей отправки (debounce {MEDIA_GROUP_DEBOUNCE} с)")
            group = {"items": [], "ctx": ctx, "timer": None}
            media_groups[key] = group
        group["items"].append(item)
        if item.get("caption"):
            group["ctx"]["caption"] = item["caption"]
        if group["timer"]:
            group["timer"].cancel()
        timer = threading.Timer(MEDIA_GROUP_DEBOUNCE, flush_media_group, args=[key])
        timer.daemon = True
        group["timer"] = timer
        timer.start()


def flush_media_group(key):
    with media_groups_lock:
        group = media_groups.pop(key, None)
        if group:
            now = time.time()
            _flushed_groups[key[2]] = now
            for gid in [g for g, t in _flushed_groups.items() if now - t > 3600]:
                del _flushed_groups[gid]
    if not group:
        return
    items = group["items"]
    ctx = group["ctx"]
    print(f"[album] отправка группы {key[2]}: частей {len(items)}")

    lines = []
    for it in items:
        typ = it["type"]
        if typ == "photo":
            dest = download_photo(it["file_id"])
            lines.append(f"- фото, сохранено в `{dest}`" if dest else f"- фото (не удалось скачать: {last_download_error()})")
        elif typ == "video":
            dest = download_media(it["file_id"], "video", ".mp4")
            lines.append(f"- видео, сохранено в `{dest}`" if dest else f"- видео (не удалось скачать: {last_download_error()})")
        else:  # document / audio / animation — все скачиваются одинаково
            dest = download_document(it["file_id"], it["file_name"])
            label = {"document": "файл", "audio": "аудио-файл", "animation": "анимация/GIF"}[typ]
            lines.append(f"- {label} `{it['file_name']}`, сохранён в `{dest}`" if dest
                         else f"- {label} `{it['file_name']}` (не удалось скачать: {last_download_error()})")

    caption_note = f" Подпись к альбому: «{ctx['caption']}»." if ctx.get("caption") else ""
    notify = (f"[Системное уведомление] Пользователь прислал альбом из {len(items)} файлов одним сообщением:\n"
              + "\n".join(lines) + f"\n{caption_note} Сообщи ему об этом одним ответом.")
    claude_run.run_claude_turn(notify, chat_id=ctx["tgt_chat_id"], thread_id=ctx["tgt_thread_id"],
                     topic_id=ctx["topic_id"], telegram_reply_to=ctx.get("tgt_reply_to"),
                     reply_markup=ctx.get("reply_kb"))

def transcribe_voice(ogg_path):
    """Конвертирует .ogg в .wav 16кГц и транскрибирует локально через whisper.cpp."""
    wav_path = ogg_path.rsplit(".", 1)[0] + ".wav"
    try:
        subprocess.run(
            ["ffmpeg", "-y", "-i", ogg_path, "-ar", "16000", "-ac", "1", wav_path],
            capture_output=True, timeout=30
        )
        result = subprocess.run(
            [consts.WHISPER_BIN, "-m", consts.WHISPER_MODEL, "-f", wav_path, "-l", "ru", "-nt", "-np"],
            capture_output=True, text=True, timeout=60
        )
        text = result.stdout.strip()
        return text or None
    except Exception as e:
        print(f"[transcribe_voice] Ошибка: {e}")
        return None
    finally:
        if os.path.exists(wav_path):
            os.unlink(wav_path)
