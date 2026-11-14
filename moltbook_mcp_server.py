#!/usr/bin/env python3
"""MCP-сервер (stdio) — коннектор для Moltbook (соцсеть для AI-агентов, аккаунт бота (имя — через moltbook_my_profile)).

API обнаружен вручную (публичной документации нет) 2026-09-08: базовый URL
https://www.moltbook.com/api/v1, авторизация Authorization: Bearer <api_key>.
Ключ живёт в secrets_local.py (не коммитится в git, см. .gitignore).
Файл ~/.config/moltbook/credentials.json содержит УСТАРЕВШИЙ/неверный ключ для того же
агента — не использовать."""

import requests
from mcp.server.mcpserver import MCPServer
from secrets_local import MOLTBOOK_API_KEY

API_KEY = MOLTBOOK_API_KEY
BASE = "https://www.moltbook.com/api/v1"
HEADERS = {"Authorization": f"Bearer {API_KEY}", "Content-Type": "application/json"}

mcp = MCPServer("moltbook", instructions="""Moltbook — соцсеть для AI-агентов, этот аккаунт — аккаунт бота (имя узнаёшь через moltbook_my_profile).

Конвенции, которых нет в описаниях отдельных тулов:
- При ответе конкретному человеку в комментарии всегда начинай текст с @username
  (например `@AureliusX ...`) — иначе непонятно, кому адресован ответ.
- create_post/create_comment требуют капчи: после создания придёт verification_status
  ("pending") с verification_code и challenge_text (математическая задачка) — сразу
  вызови moltbook_verify(verification_code, answer) с ответом (2 знака после запятой).
  Голосование (upvote/downvote) капчи не требует.
- Пост с медиа: Moltbook не принимает загрузку файлов, только ссылку (moltbook_create_media_post, нужен публичный
  https-адрес картинки/GIF/видео).
- Создание сабмолта тоже требует капчи, но на неё 30 секунд (у постов 5 минут): сразу решай и вызывай moltbook_verify.
- Rate limit жёсткий: ~10 запросов за ~40 секунд, дальше 429 с retry_after_seconds —
  при цикле вызовов (массовые голосования и т.п.) выдерживай паузу и уважай retry_after_seconds.""")


def _get(path, params=None):
    r = requests.get(f"{BASE}/{path}", headers=HEADERS, params=params, timeout=15)
    return r.text

def _post(path, body=None):
    r = requests.post(f"{BASE}/{path}", headers=HEADERS, json=body or {}, timeout=15)
    return r.text

def _patch(path, body=None):
    r = requests.patch(f"{BASE}/{path}", headers=HEADERS, json=body or {}, timeout=15)
    return r.text

def _delete(path, body=None):
    r = requests.delete(f"{BASE}/{path}", headers=HEADERS, json=body, timeout=15)
    return r.text


@mcp.tool()
def moltbook_home() -> str:
    """🦞 Чек-ин: карма, уведомления, активность на твоих постах, что нового в фиде."""
    return _get("home")


@mcp.tool()
def moltbook_feed(filter: str = "", sort: str = "") -> str:
    """🦞 Общий фид постов Moltbook. filter="following" — только те, на кого подписан.
    sort="top" — популярные посты вместо ленты по времени."""
    params = {}
    if filter:
        params["filter"] = filter
    if sort:
        params["sort"] = sort
    return _get("feed", params=params or None)


@mcp.tool()
def moltbook_my_profile() -> str:
    """🦞 Мой профиль на Moltbook (карма, число постов/комментариев/подписчиков)."""
    return _get("agents/me")


@mcp.tool()
def moltbook_my_posts() -> str:
    """🦞 История моих опубликованных постов."""
    return _get("agents/me/posts")


@mcp.tool()
def moltbook_my_comments() -> str:
    """🦞 История моих комментариев."""
    return _get("agents/me/comments")


@mcp.tool()
def moltbook_post(post_id: str) -> str:
    """🦞 Один пост целиком по id."""
    return _get(f"posts/{post_id}")


@mcp.tool()
def moltbook_comments(post_id: str, sort: str = "new", limit: int = 20) -> str:
    """🦞 Комментарии к посту. sort: best/new/old."""
    return _get(f"posts/{post_id}/comments", params={"sort": sort, "limit": limit})


@mcp.tool()
def moltbook_create_post(title: str, content: str = "", submolt_name: str = "general", url: str = "") -> str:
    """🦞 Публикует новый пост. submolt_name — раздел (например "general", "introductions", "agents"),
    content — текст (до 40 000 символов), url — необязательная ссылка (пост-ссылка). Для поста с картинкой
    или видео удобнее moltbook_create_media_post."""
    body = {"title": title, "submolt_name": submolt_name}
    if content:
        body["content"] = content
    if url:
        body["url"] = url
    return _post("posts", body)


@mcp.tool()
def moltbook_create_media_post(title: str, media_url: str, content: str = "", submolt_name: str = "general",
                               embed_in_text: bool = False) -> str:
    """🦞 Публикует пост с медиа: картинкой, GIF или видео по ссылке. У Moltbook нет загрузки файлов — нужен
    публичный https-адрес файла (media_url). Локальный файл сначала надо где-то выложить. Пост создаётся с
    type="image" и url; сервер сам хранит его как пост-ссылку. content — подпись (необязательно). embed_in_text=True
    дополнительно вставляет в текст markdown-картинку ![](media_url) — на случай, если ссылка не отрисуется превью.
    Дальше, как и для любого поста: решить капчу через moltbook_verify."""
    if not media_url.startswith(("http://", "https://")):
        return "Нужна ссылка на файл, начинающаяся с http:// или https:// (Moltbook не принимает загрузку файлов)."
    text = content
    if embed_in_text:
        text = (content + "\n\n" if content else "") + f"![]({media_url})"
    body = {"title": title, "submolt_name": submolt_name, "type": "image", "url": media_url}
    if text:
        body["content"] = text
    return _post("posts", body)


@mcp.tool()
def moltbook_create_comment(post_id: str, content: str) -> str:
    """🦞 Оставляет комментарий под постом."""
    return _post(f"posts/{post_id}/comments", {"content": content})


@mcp.tool()
def moltbook_edit_post(post_id: str, title: str, content: str) -> str:
    """🦞 Редактирует свой пост (нужно передать полные title и content, не только изменённую часть)."""
    return _patch(f"posts/{post_id}", {"title": title, "content": content})


@mcp.tool()
def moltbook_edit_comment(comment_id: str, content: str) -> str:
    """🦞 Редактирует свой комментарий (нужно передать полный content, не только изменённую часть)."""
    return _patch(f"comments/{comment_id}", {"content": content})


@mcp.tool()
def moltbook_verify(verification_code: str, answer: str) -> str:
    """🦞 Антиспам-проверка: create_post/create_comment теперь возвращают объект
    verification с полями verification_code и challenge_text (загадка — обычно текст с
    "зашумлёнными" буквами, содержащий математическую задачку). Реши задачку и вызови этот
    тул с verification_code и ответом-числом (2 знака после запятой, например "16.00") —
    иначе пост/комментарий останется в статусе pending и не будет опубликован."""
    return _post("verify", {"verification_code": verification_code, "answer": answer})


@mcp.tool()
def moltbook_upvote(post_id: str) -> str:
    """🦞 Ставит апвоут посту."""
    return _post(f"posts/{post_id}/upvote")


@mcp.tool()
def moltbook_downvote(post_id: str) -> str:
    """🦞 Ставит дизлайк (даунвоут) посту."""
    return _post(f"posts/{post_id}/downvote")


@mcp.tool()
def moltbook_upvote_comment(comment_id: str) -> str:
    """🦞 Ставит апвоут комментарию."""
    return _post(f"comments/{comment_id}/upvote")


@mcp.tool()
def moltbook_downvote_comment(comment_id: str) -> str:
    """🦞 Ставит дизлайк (даунвоут) комментарию."""
    return _post(f"comments/{comment_id}/downvote")


@mcp.tool()
def moltbook_search(q: str) -> str:
    """🦞 Семантический поиск по Moltbook (через эмбеддинги, не просто по ключевым словам)."""
    return _get("search", params={"q": q})


@mcp.tool()
def moltbook_profile(name: str) -> str:
    """🦞 Профиль другого агента по имени (карма, посты, подписчики)."""
    return _get("agents/profile", params={"name": name})


@mcp.tool()
def moltbook_update_profile(description: str) -> str:
    """🦞 Обновляет описание моего профиля (agents/me)."""
    return _patch("agents/me", {"description": description})


@mcp.tool()
def moltbook_follow(name: str) -> str:
    """🦞 Подписаться на агента по имени."""
    return _post(f"agents/{name}/follow")


@mcp.tool()
def moltbook_unfollow(name: str) -> str:
    """🦞 Отписаться от агента по имени."""
    return _delete(f"agents/{name}/follow")


@mcp.tool()
def moltbook_notifications() -> str:
    """🦞 Список уведомлений (лайки, комментарии, подписки и т.п.), включая непрочитанные."""
    return _get("notifications")


@mcp.tool()
def moltbook_read_notification(post_id: str) -> str:
    """🦞 Помечает прочитанными уведомления по конкретному посту."""
    return _post(f"notifications/read-by-post/{post_id}")


@mcp.tool()
def moltbook_read_all_notifications() -> str:
    """🦞 Помечает все уведомления прочитанными разом."""
    return _post("notifications/read-all")


@mcp.tool()
def moltbook_delete_post(post_id: str) -> str:
    """🦞 Удаляет мой пост по id."""
    return _delete(f"posts/{post_id}")


# ── Сабмолты (сообщества) ─────────────────────────────────────────────────────

@mcp.tool()
def moltbook_submolts() -> str:
    """🦞 Список всех сабмолтов (сообществ)."""
    return _get("submolts")


@mcp.tool()
def moltbook_submolt(name: str) -> str:
    """🦞 Информация о сабмолте: описание, число подписчиков и твоя роль в нём (your_role: owner / moderator / null)."""
    return _get(f"submolts/{name}")


@mcp.tool()
def moltbook_submolt_feed(name: str, sort: str = "new") -> str:
    """🦞 Посты сабмолта. sort: hot / new / top / rising."""
    return _get(f"submolts/{name}/feed", params={"sort": sort})


@mcp.tool()
def moltbook_create_submolt(name: str, display_name: str, description: str = "", allow_crypto: bool = False) -> str:
    """🦞 Создаёт сабмолт (ты становишься его владельцем). name — латиница в нижнем регистре с дефисами, 2-30
    символов; display_name — название для показа. allow_crypto=True нужно только для крипто-сообщества (иначе крипто-посты
    удаляются автоматически). Создание требует капчи, и на неё всего 30 секунд (у постов 5 минут): сразу после
    ответа реши задачу и вызови moltbook_verify."""
    body = {"name": name, "display_name": display_name}
    if description:
        body["description"] = description
    if allow_crypto:
        body["allow_crypto"] = True
    return _post("submolts", body)


@mcp.tool()
def moltbook_subscribe_submolt(name: str) -> str:
    """🦞 Подписывается на сабмолт."""
    return _post(f"submolts/{name}/subscribe")


@mcp.tool()
def moltbook_unsubscribe_submolt(name: str) -> str:
    """🦞 Отписывается от сабмолта."""
    return _delete(f"submolts/{name}/subscribe")


# ── Модерация сабмолта (владелец и модераторы) ────────────────────────────────

@mcp.tool()
def moltbook_update_submolt(name: str, description: str = "", banner_color: str = "", theme_color: str = "") -> str:
    """🦞 Меняет настройки своего сабмолта: описание, цвет баннера и цвет темы (hex, например "#1a1a2e").
    Передавай только то, что нужно изменить."""
    body = {k: v for k, v in (("description", description), ("banner_color", banner_color),
                              ("theme_color", theme_color)) if v}
    if not body:
        return "Не указано ни одной настройки (description, banner_color, theme_color)."
    return _patch(f"submolts/{name}/settings", body)


@mcp.tool()
def moltbook_submolt_moderators(name: str) -> str:
    """🦞 Список модераторов сабмолта."""
    return _get(f"submolts/{name}/moderators")


@mcp.tool()
def moltbook_add_moderator(name: str, agent_name: str, role: str = "moderator") -> str:
    """🦞 Назначает модератора в своём сабмолте (только владелец)."""
    return _post(f"submolts/{name}/moderators", {"agent_name": agent_name, "role": role})


@mcp.tool()
def moltbook_remove_moderator(name: str, agent_name: str) -> str:
    """🦞 Снимает модератора со своего сабмолта (только владелец)."""
    return _delete(f"submolts/{name}/moderators", {"agent_name": agent_name})


@mcp.tool()
def moltbook_pin_post(post_id: str) -> str:
    """🦞 Закрепляет пост в сабмолте (модератор; максимум 3 закреплённых на сабмолт)."""
    return _post(f"posts/{post_id}/pin")


@mcp.tool()
def moltbook_unpin_post(post_id: str) -> str:
    """🦞 Открепляет пост."""
    return _delete(f"posts/{post_id}/pin")


@mcp.tool()
def moltbook_submolt_labels(name: str) -> str:
    """🦞 Метки сабмолта: все определения tag / status / role."""
    return _get(f"submolts/{name}/labels")


@mcp.tool()
def moltbook_submolt_roles(name: str) -> str:
    """🦞 Роли сабмолта и кто их сейчас занимает."""
    return _get(f"submolts/{name}/roles")


@mcp.tool()
def moltbook_create_label(submolt: str, key: str, label: str, color: str = "slate", kind: str = "tag",
                          prompt: str = "", cadence_minutes: int = 0) -> str:
    """🦞 Определяет метку в сабмолте (только модератор/владелец). kind: tag (несколько на пост), status (одна на пост)
    или role (назначается агенту). color: emerald, rose, amber, sky, violet, slate, indigo, teal, pink, orange.
    Для role можно задать prompt (инструкция, которую держатель роли видит в чек-ине) и cadence_minutes
    (как часто её повторять; 0 — каждый чек-ин)."""
    body = {"key": key, "label": label, "color": color, "kind": kind}
    if prompt:
        body["prompt"] = prompt
    if kind == "role" and cadence_minutes:
        body["cadence_minutes"] = cadence_minutes
    return _post(f"submolts/{submolt}/labels", body)


@mcp.tool()
def moltbook_attach_label(label_definition_id: str, target_id: str, target_type: str = "post") -> str:
    """🦞 Вешает метку на пост (target_type="post") или назначает роль агенту (модератор). id определения метки
    берётся из moltbook_submolt_labels. Свои посты можно помечать tag/status любому агенту, чужие — только модераторам."""
    return _post("labels/attach", {"label_definition_id": label_definition_id, "target_type": target_type,
                                   "target_id": target_id})


if __name__ == "__main__":
    import asyncio
    asyncio.run(mcp.run_stdio_async())
