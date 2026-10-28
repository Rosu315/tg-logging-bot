"""Turning live business messages into snapshots we can resurrect later."""

from __future__ import annotations

import logging
import time

from aiogram import Bot
from aiogram.types import Message

from .config import Settings
from .media import cache_to_disk, extract_media
from .render import chat_title, user_title
from .storage import Connection, Snapshot, Storage

log = logging.getLogger(__name__)


def build_snapshot(message: Message, connection: Connection) -> Snapshot:
    media = extract_media(message)
    sender = message.from_user
    extra: dict[str, object] = {}
    if message.has_media_spoiler:
        extra["has_media_spoiler"] = True
    if media and media.file_name:
        extra["file_name"] = media.file_name
    return Snapshot(
        connection_id=connection.connection_id,
        chat_id=message.chat.id,
        message_id=message.message_id,
        owner_id=connection.owner_id,
        sender_id=sender.id if sender else None,
        sender_name=user_title(sender),
        is_outgoing=bool(sender and sender.id == connection.owner_id),
        sent_at=int(message.date.timestamp()),
        saved_at=int(time.time()),
        text=message.text or message.caption,
        entities=[e.model_dump(exclude_none=True) for e in message.entities or []],
        media_type=media.kind if media else None,
        file_id=media.file_id if media else None,
        file_unique_id=media.file_unique_id if media else None,
        extra=extra,
    )


async def _cache_if_enabled(
    bot: Bot, settings: Settings, message: Message, snapshot: Snapshot
) -> None:
    if settings.cache_media and snapshot.file_id:
        media = extract_media(message)
        if media is not None:
            snapshot.file_path = await cache_to_disk(
                bot, media, settings.media_dir, settings.max_cache_file_bytes
            )


async def capture_message(
    bot: Bot,
    storage: Storage,
    settings: Settings,
    message: Message,
    connection: Connection,
) -> Snapshot:
    """Store a snapshot of ``message``, optionally pulling the media onto disk."""
    snapshot = build_snapshot(message, connection)
    await _cache_if_enabled(bot, settings, message, snapshot)
    await storage.put_snapshot(snapshot)
    await storage.upsert_peer(connection.connection_id, message.chat.id, chat_title(message.chat))
    return snapshot


async def capture_edit(
    bot: Bot,
    storage: Storage,
    settings: Settings,
    message: Message,
    connection: Connection,
    old: Snapshot | None,
) -> Snapshot:
    """Like ``capture_message``, but keeps a before/after of the edit.

    ``message.chat`` always identifies the business-chat peer regardless of
    who sent the message, so the chat title is refreshed here too.
    """
    snapshot = build_snapshot(message, connection)

    if old is not None and (old.text or None) != (snapshot.text or None):
        snapshot.prev_text = old.text
        snapshot.edited_at = int(time.time())
    elif old is not None:
        # Re-capture without a real text change (e.g. a caption-only tweak
        # Telegram still reports as an edit): keep whatever edit marker the
        # message already had rather than erasing it.
        snapshot.prev_text = old.prev_text
        snapshot.edited_at = old.edited_at

    await _cache_if_enabled(bot, settings, message, snapshot)
    await storage.put_snapshot(snapshot)
    await storage.upsert_peer(connection.connection_id, message.chat.id, chat_title(message.chat))
    return snapshot
