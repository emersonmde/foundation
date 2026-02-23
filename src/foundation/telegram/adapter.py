"""TelegramAdapter — Telegram-backed MessagingAdapter implementation."""

from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass, field
from typing import TYPE_CHECKING

from foundation.messaging.adapter import (
    CallbackResult,
    KeyboardLayout,
    MessagingAdapter,
)
from foundation.telegram.formatting import split_message

if TYPE_CHECKING:
    from telegram import Bot, InlineKeyboardMarkup

logger = logging.getLogger(__name__)


def _build_markup(buttons: KeyboardLayout | None) -> InlineKeyboardMarkup | None:
    """Build an InlineKeyboardMarkup from a KeyboardLayout, or None."""
    if not buttons:
        return None
    from telegram import InlineKeyboardButton, InlineKeyboardMarkup

    return InlineKeyboardMarkup(
        [
            [InlineKeyboardButton(text=btn.text, callback_data=btn.callback_data) for btn in row]
            for row in buttons
        ]
    )


@dataclass
class TelegramAdapter(MessagingAdapter):
    """MessagingAdapter backed by a real Telegram bot."""

    bot: Bot
    chat_id: int
    _callback_futures: dict[str, asyncio.Future[CallbackResult]] = field(
        default_factory=dict, repr=False
    )
    _reply_future: asyncio.Future[str] | None = field(default=None, repr=False)

    async def send_message(
        self,
        text: str,
        *,
        buttons: KeyboardLayout | None = None,
        parse_mode: str = "HTML",
    ) -> str:
        if not text:
            raise ValueError("Cannot send empty message")

        reply_markup = _build_markup(buttons)
        chunks = split_message(text)
        last_message_id = ""
        for i, chunk in enumerate(chunks):
            # Only attach buttons to the last chunk
            markup = reply_markup if i == len(chunks) - 1 else None
            msg = await self.bot.send_message(
                chat_id=self.chat_id,
                text=chunk,
                parse_mode=parse_mode,
                reply_markup=markup,
            )
            last_message_id = str(msg.message_id)

        return last_message_id

    async def edit_message(
        self,
        message_id: str,
        text: str,
        *,
        buttons: KeyboardLayout | None = None,
        parse_mode: str = "HTML",
    ) -> None:
        await self.bot.edit_message_text(
            chat_id=self.chat_id,
            message_id=int(message_id),
            text=text,
            parse_mode=parse_mode,
            reply_markup=_build_markup(buttons),
        )

    async def wait_for_callback(
        self,
        message_id: str,
        *,
        timeout: float | None = None,
    ) -> CallbackResult:
        existing = self._callback_futures.get(message_id)
        if existing is not None and not existing.done():
            raise RuntimeError(f"wait_for_callback already active for message {message_id}")
        future: asyncio.Future[CallbackResult] = asyncio.get_running_loop().create_future()
        self._callback_futures[message_id] = future
        try:
            return await asyncio.wait_for(future, timeout=timeout)
        finally:
            self._callback_futures.pop(message_id, None)

    async def wait_for_reply(
        self,
        *,
        timeout: float | None = None,
    ) -> str:
        if self._reply_future is not None and not self._reply_future.done():
            raise RuntimeError("wait_for_reply already active — only one waiter at a time")
        future: asyncio.Future[str] = asyncio.get_running_loop().create_future()
        self._reply_future = future
        try:
            return await asyncio.wait_for(future, timeout=timeout)
        finally:
            self._reply_future = None

    def resolve_callback(self, message_id: str, result: CallbackResult) -> bool:
        """Called by handlers to fulfill a pending callback future. Returns True if resolved."""
        future = self._callback_futures.get(message_id)
        if future and not future.done():
            future.set_result(result)
            return True
        return False

    def resolve_reply(self, text: str) -> bool:
        """Called by handlers to fulfill a pending reply future. Returns True if resolved."""
        if self._reply_future and not self._reply_future.done():
            self._reply_future.set_result(text)
            return True
        return False
