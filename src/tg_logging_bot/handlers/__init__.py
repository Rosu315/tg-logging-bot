"""Routers assembled into the dispatcher, in the order they should run."""

from aiogram import Router

from . import admin, backup, business, common, settings


def build_router() -> Router:
    router = Router(name="tg_logging_bot")
    # settings first: it claims the `?start=settings` deep link before /start does
    router.include_router(settings.router)
    router.include_router(admin.router)
    router.include_router(backup.router)
    router.include_router(common.router)
    router.include_router(business.router)
    return router


__all__ = ["build_router"]
