"""Admin-only: who may connect this bot via Telegram Business.

Everything here is gated on ``settings.is_admin(username)`` — a single
Telegram @username (``ADMIN_USERNAME``) is allowed to manage the allowlist
from chat, via /users, /allow and /deny. Everyone else gets silence: these
commands simply do nothing for a non-admin, so their existence does not
invite poking at them.
"""

from __future__ import annotations

from aiogram import Bot, F, Router
from aiogram.filters import Command
from aiogram.types import (
    BotCommand,
    BotCommandScopeChat,
    CallbackQuery,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    Message,
)

from ..config import Settings
from ..storage import Storage
from ..texts import t

router = Router(name="admin")
router.message.filter(F.chat.type == "private")

CALLBACK_PREFIX = "adm"

# Shown in the Telegram command menu only for ADMIN_USERNAME's own chat with
# the bot (via BotCommandScopeChat) — everyone else's menu stays unchanged,
# so the existence of these commands isn't advertised to regular users.
ADMIN_COMMANDS = [
    BotCommand(command="users", description="Кто может подключать бота"),
    BotCommand(command="allow", description="Разрешить username"),
    BotCommand(command="deny", description="Запретить username"),
]


async def ensure_admin_menu(bot: Bot, chat_id: int) -> None:
    base = await bot.get_my_commands()
    await bot.set_my_commands([*base, *ADMIN_COMMANDS], scope=BotCommandScopeChat(chat_id=chat_id))


async def is_authorized(storage: Storage, settings: Settings, username: str | None) -> bool:
    """The admin is always authorized; everyone else must be on the allowlist.

    Used to gate the *plain* private-chat commands (/start, /settings,
    /status, /purge) — those fire for anyone who opens a DM with the bot,
    completely independently of the Telegram Business connection allowlist,
    which only governs whose business messages get captured.
    """
    return settings.is_admin(username) or await storage.is_allowed(username)


def _users_keyboard(usernames: list[str]) -> InlineKeyboardMarkup:
    rows = [
        [InlineKeyboardButton(text=f"❌ @{name}", callback_data=f"{CALLBACK_PREFIX}:rm:{name}")]
        for name in usernames
    ]
    return InlineKeyboardMarkup(inline_keyboard=rows)


def _users_body(language: str, usernames: list[str]) -> str:
    listing = "\n".join(f"• @{name}" for name in usernames) if usernames else "—"
    return t(language, "users_list", users=listing)


@router.message(Command("users"))
async def cmd_users(message: Message, bot: Bot, storage: Storage, settings: Settings) -> None:
    username = message.from_user.username if message.from_user else None
    if not settings.is_admin(username):
        return
    await ensure_admin_menu(bot, message.chat.id)
    prefs = await storage.get_prefs(message.chat.id)
    usernames = await storage.list_allowed()
    await message.answer(
        _users_body(prefs.language, usernames), reply_markup=_users_keyboard(usernames)
    )


@router.message(Command("allow"))
async def cmd_allow(message: Message, storage: Storage, settings: Settings) -> None:
    username = message.from_user.username if message.from_user else None
    if not settings.is_admin(username):
        return
    prefs = await storage.get_prefs(message.chat.id)
    arg = (message.text or "").partition(" ")[2].strip()
    if not arg:
        await message.answer(t(prefs.language, "allow_usage"))
        return
    added = await storage.add_allowed(arg)
    key = "user_added" if added else "user_already_added"
    await message.answer(t(prefs.language, key, user=arg.lstrip("@").lower()))


@router.message(Command("deny"))
async def cmd_deny(message: Message, storage: Storage, settings: Settings) -> None:
    username = message.from_user.username if message.from_user else None
    if not settings.is_admin(username):
        return
    prefs = await storage.get_prefs(message.chat.id)
    arg = (message.text or "").partition(" ")[2].strip()
    if not arg:
        await message.answer(t(prefs.language, "deny_usage"))
        return
    removed = await storage.remove_allowed(arg)
    key = "user_removed" if removed else "user_not_found"
    await message.answer(t(prefs.language, key, user=arg.lstrip("@").lower()))


@router.callback_query(F.data.startswith(f"{CALLBACK_PREFIX}:rm:"))
async def cb_remove(callback: CallbackQuery, storage: Storage, settings: Settings) -> None:
    username = callback.from_user.username
    if not settings.is_admin(username):
        await callback.answer()
        return

    _, _, target = (callback.data or "").split(":", 2)
    await storage.remove_allowed(target)
    usernames = await storage.list_allowed()
    prefs = await storage.get_prefs(callback.from_user.id)

    if isinstance(callback.message, Message):
        await callback.message.edit_text(
            _users_body(prefs.language, usernames), reply_markup=_users_keyboard(usernames)
        )
    await callback.answer(t(prefs.language, "user_removed", user=target))
