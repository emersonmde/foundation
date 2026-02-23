"""MessagingAdapter ABC and shared types."""

from __future__ import annotations

import time
from abc import ABC, abstractmethod
from dataclasses import dataclass


@dataclass(frozen=True)
class InlineButton:
    """A single inline keyboard button."""

    text: str
    callback_data: str


@dataclass(frozen=True)
class CallbackResult:
    """Result from a callback query."""

    callback_id: str
    data: str
    message_id: str


KeyboardLayout = list[list[InlineButton]]


@dataclass(frozen=True)
class IncomingMessage:
    """A message from a frontend destined for the orchestrator."""

    text: str
    timestamp: float

    @classmethod
    def from_text(cls, text: str) -> IncomingMessage:
        """Create an IncomingMessage from raw user text."""
        return cls(text=text, timestamp=time.monotonic())


class MessagingAdapter(ABC):
    """Abstract interface for human communication.

    Only one waiter is supported at a time for wait_for_callback (per message_id)
    and wait_for_reply. Concurrent calls raise RuntimeError.
    """

    @abstractmethod
    async def send_message(
        self,
        text: str,
        *,
        buttons: KeyboardLayout | None = None,
        parse_mode: str = "HTML",
    ) -> str:
        """Send a message and return its ID."""

    @abstractmethod
    async def edit_message(
        self,
        message_id: str,
        text: str,
        *,
        buttons: KeyboardLayout | None = None,
        parse_mode: str = "HTML",
    ) -> None:
        """Edit an existing message."""

    @abstractmethod
    async def wait_for_callback(
        self,
        message_id: str,
        *,
        timeout: float | None = None,
    ) -> CallbackResult:
        """Wait for a callback query on a specific message."""

    @abstractmethod
    async def wait_for_reply(
        self,
        *,
        timeout: float | None = None,
    ) -> str:
        """Wait for the next text reply from the user."""
