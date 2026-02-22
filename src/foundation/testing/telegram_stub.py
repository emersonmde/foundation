"""Stub MessagingAdapter for testing without a real Telegram bot."""

from __future__ import annotations

import asyncio
from dataclasses import dataclass, field

from foundation.telegram.adapter import (
    CallbackResult,
    KeyboardLayout,
    MessagingAdapter,
)


@dataclass
class SentMessage:
    """Record of a message sent through the stub."""

    message_id: str
    text: str
    buttons: KeyboardLayout | None = None
    parse_mode: str = "HTML"
    edited: bool = False


@dataclass
class StubAdapter(MessagingAdapter):
    """In-memory MessagingAdapter for tests.

    Records all sent messages and allows injecting callbacks/replies
    via asyncio.Future for testing async flows.

    Only one waiter is supported at a time for wait_for_callback (per message_id)
    and wait_for_reply. Concurrent calls raise RuntimeError.
    """

    sent_messages: list[SentMessage] = field(default_factory=list)
    _next_id: int = field(default=1, repr=False)
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
        msg_id = str(self._next_id)
        self._next_id += 1
        self.sent_messages.append(
            SentMessage(
                message_id=msg_id,
                text=text,
                buttons=buttons,
                parse_mode=parse_mode,
            )
        )
        return msg_id

    async def edit_message(
        self,
        message_id: str,
        text: str,
        *,
        buttons: KeyboardLayout | None = None,
        parse_mode: str = "HTML",
    ) -> None:
        for msg in self.sent_messages:
            if msg.message_id == message_id:
                msg.text = text
                msg.buttons = buttons
                msg.parse_mode = parse_mode
                msg.edited = True
                return
        raise ValueError(f"Message {message_id} not found")

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

    def inject_callback(self, message_id: str, data: str) -> bool:
        """Resolve a pending wait_for_callback. Returns True if a future was waiting."""
        future = self._callback_futures.get(message_id)
        if future and not future.done():
            future.set_result(
                CallbackResult(
                    callback_id=f"cb-{message_id}",
                    data=data,
                    message_id=message_id,
                )
            )
            return True
        return False

    def inject_reply(self, text: str) -> bool:
        """Resolve a pending wait_for_reply. Returns True if a future was waiting."""
        if self._reply_future and not self._reply_future.done():
            self._reply_future.set_result(text)
            return True
        return False

    def reset(self) -> None:
        """Clear all state."""
        self.sent_messages.clear()
        self._next_id = 1
        self._callback_futures.clear()
        self._reply_future = None
