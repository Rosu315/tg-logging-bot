"""Entry point: wire the bot, the dispatcher and the janitor together."""

from __future__ import annotations

import asyncio
import contextlib
import logging

from aiogram import Bot, Dispatcher
from aiogram.client.default import DefaultBotProperties
from aiogram.client.session.aiohttp import AiohttpSession
from aiogram.client.telegram import TelegramAPIServer
from aiogram.enums import ParseMode
from aiogram.types import BotCommand

from .config import Settings, load_settings
from .handlers import build_router
from .janitor import run_janitor
from .middlewares import DependenciesMiddleware
from .storage import Storage
from .web import run_web

log = logging.getLogger("tg_logging_bot")

COMMANDS = [
    BotCommand(command="start", description="Как подключить бота"),
    BotCommand(command="backup", description="Открыть веб-бэкап переписок"),
    BotCommand(command="settings", description="Настройки уведомлений"),
    BotCommand(command="status", description="Состояние подключения"),
    BotCommand(command="purge", description="Стереть сохранённые снимки"),
    BotCommand(command="help", description="Справка"),
]


def _log_background_failure(name: str, task: asyncio.Task[None]) -> None:
    if task.cancelled():
        return
    exc = task.exception()
    if exc is not None:
        log.error("background task %r died", name, exc_info=exc)


def build_dispatcher(storage: Storage, settings: Settings) -> Dispatcher:
    dispatcher = Dispatcher()
    dependencies = DependenciesMiddleware(storage, settings)
    dispatcher.update.outer_middleware(dependencies)
    dispatcher.include_router(build_router())
    return dispatcher


async def main() -> None:
    settings = load_settings()
    logging.basicConfig(
        level=getattr(logging, settings.log_level.upper(), logging.INFO),
        format="%(asctime)s %(levelname)-8s %(name)s: %(message)s",
    )

    storage = Storage(settings.db_path)
    await storage.connect()
    await storage.seed_allowlist(list(settings.allowed_usernames_set))

    session = None
    if settings.api_base_url:
        log.info("Using custom Bot API base URL: %s", settings.api_base_url)
        session = AiohttpSession(api=TelegramAPIServer.from_base(settings.api_base_url))

    bot = Bot(
        token=settings.bot_token.get_secret_value(),
        session=session,
        default=DefaultBotProperties(parse_mode=ParseMode.HTML),
    )
    dispatcher = build_dispatcher(storage, settings)

    janitor = asyncio.create_task(run_janitor(storage, settings))
    web_server = asyncio.create_task(run_web(storage, settings, bot))
    janitor.add_done_callback(lambda t: _log_background_failure("janitor", t))
    web_server.add_done_callback(lambda t: _log_background_failure("web_server", t))
    try:
        await bot.set_my_commands(COMMANDS)
        me = await bot.me()
        log.info("starting as @%s", me.username)
        await dispatcher.start_polling(
            bot,
            allowed_updates=dispatcher.resolve_used_update_types(),
        )
    finally:
        janitor.cancel()
        web_server.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await janitor
        with contextlib.suppress(asyncio.CancelledError):
            await web_server
        await storage.close()
        await bot.session.close()


def run() -> None:
    with contextlib.suppress(KeyboardInterrupt, SystemExit):
        asyncio.run(main())


if __name__ == "__main__":
    run()
