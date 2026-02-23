"""TelegramBot — Application lifecycle management."""

from __future__ import annotations

import asyncio
import logging
from typing import Any

import aiosqlite
from telegram.ext import Application

from foundation.config import TelegramConfig
from foundation.messaging.adapter import IncomingMessage
from foundation.telegram.adapter import TelegramAdapter
from foundation.telegram.handlers import register_handlers

logger = logging.getLogger(__name__)


class TelegramBot:
    """Manages the Telegram bot application lifecycle.

    Usage:
        bot = TelegramBot(config, db)
        await bot.start()
        # ... use bot.adapter to send messages ...
        await bot.stop()

    Or use run() which blocks until request_shutdown() is called.
    """

    def __init__(
        self,
        config: TelegramConfig,
        db: aiosqlite.Connection,
        incoming_queue: asyncio.Queue[IncomingMessage] | None = None,
    ) -> None:
        self._config = config
        self._db = db
        self._incoming_queue = incoming_queue
        self._shutdown_event = asyncio.Event()

        self._app: Application[Any, Any, Any, Any, Any, Any] = (
            Application.builder().token(config.bot_token).build()
        )
        self._adapter = TelegramAdapter(
            bot=self._app.bot,
            chat_id=config.user_id,
        )

    @property
    def adapter(self) -> TelegramAdapter:
        """The messaging adapter for sending messages."""
        return self._adapter

    async def start(self) -> None:
        """Initialize and start the bot (polling mode)."""
        await self._app.initialize()
        register_handlers(
            self._app, self._config.user_id, self._adapter, self._db, self._incoming_queue
        )
        await self._app.start()

        if self._app.updater is None:
            raise RuntimeError("Application has no updater")
        await self._app.updater.start_polling(drop_pending_updates=True)
        logger.info("Telegram bot started (polling)")

    async def stop(self) -> None:
        """Stop the bot and clean up."""
        try:
            if self._app.updater and self._app.updater.running:
                await self._app.updater.stop()
        finally:
            try:
                if self._app.running:
                    await self._app.stop()
            finally:
                await self._app.shutdown()
                logger.info("Telegram bot stopped")

    async def run(self) -> None:
        """Start the bot and block until shutdown is requested."""
        await self.start()
        try:
            await self._shutdown_event.wait()
        finally:
            await self.stop()

    def request_shutdown(self) -> None:
        """Signal the bot to shut down."""
        self._shutdown_event.set()
