"""Everything that arrives through a business connection.

Four updates matter here:

``business_connection``      the bot was added to / removed from an account
``business_message``         a new message in one of the account's chats
``edited_business_message``  someone edited a message we already know
``deleted_business_messages``someone deleted messages — ids only, no content
"""

from __future__ import annotations

import logging

from aiogram import Bot, Router
from aiogram.types import BusinessConnection, BusinessMessagesDeleted, Message

from ..capture import build_snapshot, capture_edit, capture_message
from ..config import Settings
from ..media import cache_to_disk, extract_media, is_one_time, send_media
from ..render import chat_title, deleted_card, edited_card, once_card
from ..storage import Connection, Snapshot, Storage
from ..texts import t

log = logging.getLogger(__name__)

router = Router(name="business")

# Telegram caps captions at 1024 characters; longer cards go out as a separate message.
CAPTION_LIMIT = 1024


@router.business_connection()
async def on_connection(
    event: BusinessConnection, bot: Bot, storage: Storage, settings: Settings
) -> None:
    rights = event.rights
    is_allowed = await storage.is_allowed(event.user.username)

    connection = Connection(
        connection_id=event.id,
        owner_id=event.user.id,
        owner_chat_id=event.user_chat_id,
        owner_username=event.user.username,
        is_enabled=event.is_enabled and is_allowed,
        can_read=bool(getattr(rights, "can_read_messages", False)),
        can_delete_sent=bool(getattr(rights, "can_delete_sent_messages", False)),
    )
    await storage.save_connection(connection)
    prefs = await storage.get_prefs(connection.owner_id)

    if not event.is_enabled:
        await storage.disable_connection(event.id)
        await storage.forget_owner(connection.owner_id)
        await bot.send_message(connection.owner_chat_id, t(prefs.language, "disconnected"))
        return

    if not is_allowed:
        log.warning(
            "rejected business connection from unauthorized user @%s (id=%s)",
            event.user.username,
            event.user.id,
        )
        await bot.send_message(connection.owner_chat_id, t(prefs.language, "not_authorized"))
        return

    await bot.send_message(connection.owner_chat_id, t(prefs.language, "connected"))
    if not connection.can_read:
        await bot.send_message(connection.owner_chat_id, t(prefs.language, "no_read_right"))


@router.business_message()
async def on_business_message(
    message: Message, bot: Bot, storage: Storage, settings: Settings
) -> None:
    connection = await _resolve(storage, message.business_connection_id)
    if connection is None:
        return

    await capture_message(bot, storage, settings, message, connection)
    await _maybe_rescue_one_time(message, bot, storage, settings, connection)


@router.edited_business_message()
async def on_edited_business_message(
    message: Message, bot: Bot, storage: Storage, settings: Settings
) -> None:
    connection = await _resolve(storage, message.business_connection_id)
    if connection is None:
        return

    old = await storage.get_snapshot(connection.connection_id, message.chat.id, message.message_id)
    # Re-snapshot first: even if the owner muted edit alerts, a later deletion
    # should show the newest text.
    await capture_edit(bot, storage, settings, message, connection, old)

    prefs = await storage.get_prefs(connection.owner_id)
    if not prefs.notify_edited:
        return
    is_own = bool(message.from_user and message.from_user.id == connection.owner_id)
    if is_own and not prefs.include_own:
        return
    if old is not None and (old.text or None) == (message.text or message.caption or None):
        return  # media-only edit with identical text: nothing to show

    await bot.send_message(
        connection.owner_chat_id,
        edited_card(old, message, prefs.language),
        parse_mode="HTML",
    )


@router.deleted_business_messages()
async def on_deleted_business_messages(
    event: BusinessMessagesDeleted, bot: Bot, storage: Storage, settings: Settings
) -> None:
    connection = await _resolve(storage, event.business_connection_id)
    if connection is None:
        return

    prefs = await storage.get_prefs(connection.owner_id)
    snapshots = await storage.mark_deleted(
        connection.connection_id, event.chat.id, list(event.message_ids)
    )
    if not prefs.notify_deleted:
        return

    chat_name = chat_title(event.chat)
    for snapshot in snapshots:
        if snapshot.is_outgoing and not prefs.include_own:
            continue
        await _deliver(bot, connection.owner_chat_id, snapshot, prefs.language, chat_name)


# --------------------------------------------------------------------------- helpers


async def _resolve(storage: Storage, connection_id: str | None) -> Connection | None:
    if not connection_id:
        return None
    connection = await storage.get_connection(connection_id)
    if connection is None or not connection.is_enabled:
        log.debug("update from unknown or disabled connection %s", connection_id)
        return None

    # Re-checked against the *current* allowlist on every event, not just at
    # connection time, so an admin removing someone via /deny takes effect
    # immediately without needing that person to reconnect.
    if not await storage.is_allowed(connection.owner_username):
        log.warning(
            "ignoring update from connection %s: @%s is no longer in the allowlist",
            connection_id,
            connection.owner_username,
        )
        return None
    return connection


async def _deliver(
    bot: Bot, chat_id: int, snapshot: Snapshot, language: str, chat_name: str
) -> None:
    """Send one deletion card, with the media attached when there is any."""
    card = deleted_card(snapshot, language, chat_name)

    if snapshot.media_type:
        caption = card if len(card) <= CAPTION_LIMIT else None
        sent = await send_media(
            bot,
            chat_id,
            kind=snapshot.media_type,
            file_id=snapshot.file_id,
            file_path=snapshot.file_path,
            caption=caption,
        )
        if sent is not None:
            if caption is None:
                await bot.send_message(chat_id, card, parse_mode="HTML")
            return
        card = f"{card}\n\n{t(language, 'media_lost')}"

    await bot.send_message(chat_id, card, parse_mode="HTML")


async def _maybe_rescue_one_time(
    message: Message, bot: Bot, storage: Storage, settings: Settings, connection: Connection
) -> None:
    """Save a view-once file when the owner replies to it before opening it.

    Telegram delivers the replied-to message inside the reply, so the file is
    still reachable at this moment — once the owner opens it, it is gone.
    """
    original = message.reply_to_message
    if original is None:
        log.info("rescue: reply_to_message is None (Telegram did not embed the original)")
        return
    is_owner_reply = bool(message.from_user and message.from_user.id == connection.owner_id)
    if not is_owner_reply:
        log.info("rescue: reply is not from the owner (from_user=%s)", message.from_user)
        return

    media = extract_media(original)
    if media is None:
        log.info(
            "rescue: no extractable media on original (content_type=%s)", original.content_type
        )
        return
    if original.from_user and original.from_user.id == connection.owner_id:
        log.info("rescue: original is the owner's own file, skipping")
        return  # the owner's own file, nothing to rescue

    prefs = await storage.get_prefs(connection.owner_id)
    if not prefs.capture_once:
        log.info("rescue: capture_once disabled in prefs")
        return
    one_time = is_one_time(original)
    log.info(
        "rescue: media=%s one_time_detected=%s capture_any_media=%s",
        media.kind,
        one_time,
        prefs.capture_any_media,
    )
    if not (one_time or prefs.capture_any_media):
        log.info("rescue: not flagged one-time and capture_any_media is off, skipping")
        return

    # Telegram refuses to let a bot re-send a self-destructing file by its
    # file_id ("can't use file of type SelfDestructingPhoto as Photo"), so
    # this is our one chance to pull the actual bytes down before the
    # reference is gone for good.
    try:
        file_path = await cache_to_disk(
            bot, media, settings.media_dir, settings.max_cache_file_bytes
        )
    except Exception:
        log.exception("rescue: cache_to_disk raised")
        file_path = None
    log.info("rescue: cache_to_disk -> %s", file_path)

    if file_path is not None:
        # The original message usually already has its own snapshot (from
        # on_business_message, when it first arrived) — patch the cached
        # copy onto it so the backup viewer can still show the file once the
        # self-destructing file_id itself stops working.
        #
        # Keyed by file_unique_id, not message_id: for view-once media,
        # Telegram's embedded reply_to_message carries a message_id that
        # does not match the id the original was actually stored under
        # (confirmed by testing — looks like a placeholder), while
        # file_unique_id, tied to the file itself, still does.
        affected = await storage.set_file_path_by_unique_id(
            connection.connection_id,
            message.chat.id,
            media.file_unique_id,
            file_path,
            mark_view_once=one_time,
        )
        if affected == 0:
            # Fallback for the ordinary (non-view-once) case, where the
            # embedded id has always matched.
            affected = await storage.set_snapshot_file_path(
                connection.connection_id,
                message.chat.id,
                original.message_id,
                file_path,
                mark_view_once=one_time,
            )
        if affected == 0:
            # True view-once content, it turns out, is never delivered to
            # the bot as its own business_message update at all — the
            # embedded copy inside this reply is the only trace of it that
            # ever reaches us. Nothing to patch; store it from scratch.
            rescued = build_snapshot(original, connection)
            rescued.file_path = file_path
            rescued.is_view_once = one_time
            await storage.put_snapshot(rescued)
            await storage.upsert_peer(
                connection.connection_id, message.chat.id, chat_title(original.chat)
            )
            affected = 1
        log.info(
            "rescue: file_path patch affected %d row(s) "
            "(connection=%s chat=%s file_unique_id=%s embedded_message_id=%s)",
            affected,
            connection.connection_id,
            message.chat.id,
            media.file_unique_id,
            original.message_id,
        )

    card = once_card(original, prefs.language, media.kind, one_time=one_time)
    caption = card if len(card) <= CAPTION_LIMIT else None
    sent = await send_media(
        bot,
        connection.owner_chat_id,
        kind=media.kind,
        file_id=media.file_id,
        file_path=file_path,
        caption=caption,
    )
    if sent is None:
        await bot.send_message(
            connection.owner_chat_id,
            f"{card}\n\n{t(prefs.language, 'media_lost')}",
            parse_mode="HTML",
        )
    elif caption is None:
        await bot.send_message(connection.owner_chat_id, card, parse_mode="HTML")
