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

def _delete(path):
    r = requests.delete(f"{BASE}/{path}", headers=HEADERS, timeout=15)
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
def moltbook_create_post(title: str, content: str, submolt_name: str = "general") -> str:
    """🦞 Публикует новый пост. submolt_name — раздел (например "general", "introductions", "agents")."""
    return _post("posts", {"title": title, "content": content, "submolt_name": submolt_name})


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


if __name__ == "__main__":
    import asyncio
    asyncio.run(mcp.run_stdio_async())
