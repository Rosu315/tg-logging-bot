"""/backup — a one-tap link into the web viewer over the snapshot archive.

The link is a signed, time-limited token (see ``..tokens``), not a login:
the viewer trusts it exactly as long as it's valid and for exactly the
owner it was issued to. Tapping /backup again always mints a fresh one.
"""

from __future__ import annotations

from aiogram import F, Router
from aiogram.filters import Command
from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup, Message

from ..config import Settings
from ..storage import Storage
from ..texts import t
from ..tokens import issue_token
from .admin import is_authorized

router = Router(name="backup")
router.message.filter(F.chat.type == "private")


@router.message(Command("backup"))
async def cmd_backup(message: Message, storage: Storage, settings: Settings) -> None:
    username = message.from_user.username if message.from_user else None
    if not await is_authorized(storage, settings, username):
        return

    prefs = await storage.get_prefs(message.chat.id)
    if not settings.backup_public_url:
        await message.answer(t(prefs.language, "backup_not_configured"))
        return

    secret = await storage.get_or_create_web_secret()
    token = issue_token(message.chat.id, secret, settings.backup_link_ttl_minutes * 60)
    url = f"{settings.backup_public_url.rstrip('/')}/?token={token}"

    keyboard = InlineKeyboardMarkup(
        inline_keyboard=[[InlineKeyboardButton(text=t(prefs.language, "backup_button"), url=url)]]
    )
    await message.answer(
        t(prefs.language, "backup_intro", minutes=settings.backup_link_ttl_minutes),
        reply_markup=keyboard,
    )
