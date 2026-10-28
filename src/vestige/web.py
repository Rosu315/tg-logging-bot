"""The backup viewer: a small aiohttp app, run inside the bot's own process.

It serves the snapshot archive — the same table the deletion/edit alerts are
built from — as a scrollable, Telegram-export-like chat window. Every request
carries a short-lived signed ``token`` (see ``tokens.py``), issued by the
``/backup`` command; there is no login form, no session and no cookie, which
is the whole point: a fresh link is one tap away in the bot and an old one
simply stops working.

Deliberately bound to loopback only (``WEB_HOST=127.0.0.1``) — a tunnel
(cloudflared) is what's meant to expose this, never the process itself.
"""

from __future__ import annotations

import asyncio
import logging
import mimetypes
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import quote

from aiogram import Bot
from aiogram.exceptions import TelegramAPIError
from aiohttp import web

from .config import Settings
from .render import MEDIA_NAMES, esc
from .storage import Snapshot, Storage
from .tokens import verify_token

log = logging.getLogger("vestige.web")

PAGE_LIMIT = 200


# --------------------------------------------------------------------------- app


async def run_web(storage: Storage, settings: Settings, bot: Bot) -> None:
    log.info("backup viewer starting up")
    secret = await storage.get_or_create_web_secret()

    app = web.Application()
    app["storage"] = storage
    app["settings"] = settings
    app["bot"] = bot
    app["secret"] = secret
    app.router.add_get("/", handle_chat_list)
    app.router.add_get("/chat/{connection_id}/{chat_id}", handle_chat)
    app.router.add_get("/media/{connection_id}/{chat_id}/{message_id}", handle_media)

    runner = web.AppRunner(app, access_log=None)
    await runner.setup()
    site = web.TCPSite(runner, settings.web_host, settings.web_port)
    await site.start()
    log.info("backup viewer listening on %s:%s", settings.web_host, settings.web_port)
    try:
        await asyncio.Event().wait()
    finally:
        await runner.cleanup()


def _authorize(request: web.Request) -> int:
    owner_id = verify_token(request.query.get("token", ""), request.app["secret"])
    if owner_id is None:
        raise web.HTTPForbidden(text=EXPIRED_PAGE, content_type="text/html")
    return owner_id


# ------------------------------------------------------------------------ routes


async def handle_chat_list(request: web.Request) -> web.Response:
    owner_id = _authorize(request)
    storage: Storage = request.app["storage"]
    token = request.query["token"]
    chats = await storage.list_chats_for_owner(owner_id)
    return web.Response(text=render_chat_list(chats, token), content_type="text/html")


async def handle_chat(request: web.Request) -> web.Response:
    owner_id = _authorize(request)
    storage: Storage = request.app["storage"]
    token = request.query["token"]
    connection_id = request.match_info["connection_id"]
    chat_id = int(request.match_info["chat_id"])
    before_raw = request.query.get("before")
    before = int(before_raw) if before_raw else None

    snapshots = await storage.list_snapshots_for_chat(
        owner_id, connection_id, chat_id, before=before, limit=PAGE_LIMIT
    )
    has_more = len(snapshots) == PAGE_LIMIT
    title = await storage.peer_title(owner_id, connection_id, chat_id)
    html = render_chat(snapshots, title, connection_id, chat_id, token, has_more=has_more)
    return web.Response(text=html, content_type="text/html")


async def handle_media(request: web.Request) -> web.StreamResponse:
    owner_id = _authorize(request)
    storage: Storage = request.app["storage"]
    bot: Bot = request.app["bot"]
    connection_id = request.match_info["connection_id"]
    chat_id = int(request.match_info["chat_id"])
    message_id = int(request.match_info["message_id"])

    snapshot = await storage.get_snapshot_for_owner(owner_id, connection_id, chat_id, message_id)
    if snapshot is None or not (snapshot.file_id or snapshot.file_path):
        raise web.HTTPNotFound()

    content_type = _guess_content_type(snapshot)

    if snapshot.file_path and Path(snapshot.file_path).exists():
        return web.FileResponse(Path(snapshot.file_path), headers={"Content-Type": content_type})

    if not snapshot.file_id:
        raise web.HTTPNotFound()
    try:
        buf = await bot.download(snapshot.file_id)
    except (TelegramAPIError, OSError):
        log.warning("media fetch failed for %s/%s/%s", connection_id, chat_id, message_id)
        buf = None
    if buf is None:
        raise web.HTTPGone(text="Файл больше недоступен в Telegram.")
    return web.Response(body=buf.read(), content_type=content_type)


def _guess_content_type(snapshot: Snapshot) -> str:
    kind = snapshot.media_type
    static = {
        "photo": "image/jpeg",
        "video": "video/mp4",
        "video_note": "video/mp4",
        "animation": "video/mp4",
        "voice": "audio/ogg",
        "audio": "audio/mpeg",
        "sticker": "image/webp",
    }
    if kind in static:
        return static[kind]
    name = (snapshot.extra or {}).get("file_name")
    if name:
        guessed, _ = mimetypes.guess_type(name)
        if guessed:
            return guessed
    return "application/octet-stream"


# ---------------------------------------------------------------------- rendering

STYLE = """
* { box-sizing: border-box; }
html, body { margin: 0; height: 100%; background: #e6ebee;
  font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, Helvetica,
    Arial, sans-serif; }
body { color: #1c1e21; }
a { color: #3390ec; text-decoration: none; }
.topbar { position: sticky; top: 0; z-index: 10; background: #ffffff;
  border-bottom: 1px solid #d8dee3; padding: 12px 16px; display: flex;
  align-items: center; gap: 10px; }
.topbar .title { font-weight: 600; font-size: 16px; overflow: hidden;
  text-overflow: ellipsis; white-space: nowrap; }
.topbar .back { font-size: 20px; line-height: 1; color: #3390ec; }
.wrap { max-width: 720px; margin: 0 auto; min-height: 100%; background: #e6ebee; }

.chat-list { padding: 4px 0; }
.chat-row { display: flex; align-items: center; gap: 12px; padding: 12px 16px;
  background: #fff; border-bottom: 1px solid #eef1f3; }
.chat-row .avatar { width: 44px; height: 44px; border-radius: 50%; background: #3390ec;
  color: #fff; display: flex; align-items: center; justify-content: center;
  font-size: 18px; font-weight: 600; flex-shrink: 0; }
.chat-row .meta { flex: 1; min-width: 0; }
.chat-row .name { font-weight: 600; font-size: 15px; overflow: hidden;
  text-overflow: ellipsis; white-space: nowrap; }
.chat-row .sub { color: #8b98a3; font-size: 13px; margin-top: 2px; }
.empty { padding: 40px 20px; text-align: center; color: #8b98a3; }

.messages { padding: 14px 10px 24px; display: flex; flex-direction: column; gap: 2px; }
.date-sep { align-self: center; background: #ffffffcc; color: #6b7680; font-size: 12px;
  padding: 4px 12px; border-radius: 12px; margin: 10px 0; }
.load-more { align-self: center; margin: 4px 0 14px; font-size: 13px; }
.row { display: flex; }
.row.out { justify-content: flex-end; }
.row.in { justify-content: flex-start; }
.bubble { max-width: 78%; padding: 7px 10px 6px; border-radius: 12px; font-size: 14.5px;
  line-height: 1.35; position: relative; box-shadow: 0 1px 1px rgba(0,0,0,.05); }
.bubble.in { background: #ffffff; border-bottom-left-radius: 3px; }
.bubble.out { background: #d6ecff; border-bottom-right-radius: 3px; }
.bubble .time { display: block; text-align: right; font-size: 11px; color: #8b98a3;
  margin-top: 2px; }
.bubble .muted { color: #8b98a3; }
.bubble-media { display: block; max-width: 100%; max-height: 360px; border-radius: 8px;
  margin: 2px 0; }
.bubble audio { width: 240px; margin: 2px 0; }
.doc-chip { display: inline-flex; align-items: center; gap: 6px; background: #f0f3f5;
  border-radius: 8px; padding: 8px 10px; margin: 2px 0; font-size: 13.5px; color: #1c1e21; }
.footer-note { text-align: center; color: #8b98a3; font-size: 12px; padding: 16px; }
.badges { display: flex; flex-wrap: wrap; gap: 4px; margin-bottom: 4px; }
.badge { display: inline-block; font-size: 11px; font-weight: 600; padding: 2px 7px;
  border-radius: 8px; }
.badge.deleted { background: #fde2e2; color: #c0392b; }
.badge.edited { background: #fff3cd; color: #8a6400; }
.badge.once { background: #ffe8d6; color: #b5531c; }
.prev-text { color: #9aa3ab; text-decoration: line-through; font-size: 13px; margin-bottom: 2px; }
.bubble.is-deleted { border: 1px solid #f3c6c6; }
"""

EXPIRED_PAGE = (
    "<!doctype html><html><head><meta charset='utf-8'>"
    "<meta name='viewport' content='width=device-width, initial-scale=1'>"
    f"<title>Vestige backup</title><style>{STYLE}</style></head>"
    "<body><div class='wrap'><div class='empty'>"
    "🔒 Ссылка недействительна или истекла.<br>Откройте /backup в боте ещё раз.<br><br>"
    "🔒 This link is invalid or has expired.<br>Open /backup in the bot again."
    "</div></div></body></html>"
)


def _page(title: str, body: str) -> str:
    return (
        "<!doctype html><html><head><meta charset='utf-8'>"
        "<meta name='viewport' content='width=device-width, initial-scale=1'>"
        f"<title>{esc(title)}</title><style>{STYLE}</style></head>"
        f"<body><div class='wrap'>{body}</div></body></html>"
    )


def _initial(title: str) -> str:
    return (title.lstrip("@") or "?")[0].upper()


def _relative_time(ts: int) -> str:
    moment = datetime.fromtimestamp(ts, tz=timezone.utc).astimezone()
    return moment.strftime("%d.%m.%Y %H:%M")


def render_chat_list(chats: list[dict], token: str) -> str:
    if not chats:
        rows = "<div class='empty'>Пока ничего не сохранено.<br>Nothing captured yet.</div>"
    else:
        items = []
        for chat in chats:
            href = (
                f"/chat/{quote(chat['connection_id'])}/{chat['chat_id']}"
                f"?token={quote(token)}"
            )
            items.append(
                f"<a class='chat-row' href='{href}'>"
                f"<div class='avatar'>{esc(_initial(chat['title']))}</div>"
                f"<div class='meta'>"
                f"<div class='name'>{esc(chat['title'])}</div>"
                f"<div class='sub'>{chat['count']} сообщений · "
                f"{_relative_time(chat['last_sent_at'])}</div>"
                f"</div></a>"
            )
        rows = "<div class='chat-list'>" + "".join(items) + "</div>"

    body = (
        "<div class='topbar'><div class='title'>📦 Бэкап Vestige</div></div>"
        f"{rows}"
        "<div class='footer-note'>Снимки хранятся 14 дней — это рабочий архив, не постоянный.</div>"
    )
    return _page("Vestige backup", body)


def render_chat(
    snapshots: list[Snapshot],
    title: str,
    connection_id: str,
    chat_id: int,
    token: str,
    *,
    has_more: bool,
) -> str:
    back_href = f"/?token={quote(token)}"

    if not snapshots:
        content = "<div class='empty'>Нет сохранённых сообщений.</div>"
    else:
        parts: list[str] = []
        if has_more:
            more_href = (
                f"/chat/{quote(connection_id)}/{chat_id}"
                f"?token={quote(token)}&before={snapshots[0].sent_at}"
            )
            parts.append(f"<a class='load-more' href='{more_href}'>↑ Загрузить раньше</a>")

        last_date: str | None = None
        for snap in snapshots:
            day = datetime.fromtimestamp(snap.sent_at, tz=timezone.utc).astimezone().strftime(
                "%d.%m.%Y"
            )
            if day != last_date:
                parts.append(f"<div class='date-sep'>{day}</div>")
                last_date = day
            parts.append(_render_message(snap, connection_id, token))

        content = "<div class='messages'>" + "".join(parts) + "</div>"

    body = (
        "<div class='topbar'>"
        f"<a class='back' href='{back_href}'>‹</a>"
        f"<div class='title'>{esc(title)}</div>"
        "</div>"
        f"{content}"
    )
    return _page(title, body)


def _render_message(snap: Snapshot, connection_id: str, token: str) -> str:
    cls = "out" if snap.is_outgoing else "in"
    blocks: list[str] = []

    badges: list[str] = []
    if snap.is_view_once:
        badges.append("<span class='badge once'>🔥 одноразовое / view-once</span>")
    if snap.deleted_at:
        badges.append("<span class='badge deleted'>🗑 удалено / deleted</span>")
    elif snap.edited_at:
        badges.append("<span class='badge edited'>✏️ изменено / edited</span>")
    if badges:
        blocks.append("<div class='badges'>" + "".join(badges) + "</div>")

    if snap.edited_at and snap.prev_text:
        prev = esc(snap.prev_text).replace("\n", "<br>")
        blocks.append(f"<div class='prev-text'>{prev}</div>")

    if snap.text:
        blocks.append(esc(snap.text).replace("\n", "<br>"))

    media_html = _render_media(snap, connection_id, token)
    if media_html:
        blocks.append(media_html)

    if not blocks:
        blocks.append("<span class='muted'>—</span>")

    time_str = datetime.fromtimestamp(snap.sent_at, tz=timezone.utc).astimezone().strftime("%H:%M")
    inner = "".join(f"<div>{block}</div>" for block in blocks)
    bubble_cls = f"bubble {cls}" + (" is-deleted" if snap.deleted_at else "")
    return (
        f"<div class='row {cls}'>"
        f"<div class='{bubble_cls}'>{inner}<span class='time'>{time_str}</span></div>"
        f"</div>"
    )


def _render_media(snap: Snapshot, connection_id: str, token: str) -> str:
    if not snap.media_type:
        return ""
    url = f"/media/{quote(connection_id)}/{snap.chat_id}/{snap.message_id}?token={quote(token)}"
    kind = snap.media_type

    if kind == "photo":
        return (
            f"<a href='{url}' target='_blank'>"
            f"<img class='bubble-media' src='{url}' loading='lazy'></a>"
        )
    if kind in ("video", "video_note"):
        return f"<video class='bubble-media' controls src='{url}'></video>"
    if kind == "animation":
        return f"<video class='bubble-media' controls loop muted src='{url}'></video>"
    if kind in ("voice", "audio"):
        return f"<audio controls src='{url}'></audio>"

    label = (snap.extra or {}).get("file_name") or MEDIA_NAMES.get(kind, kind)
    return f"<a class='doc-chip' href='{url}' target='_blank'>📎 {esc(label)}</a>"
