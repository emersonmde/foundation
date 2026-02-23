"""Transport-agnostic messaging interface for human communication."""

from foundation.messaging.adapter import (
    CallbackResult,
    IncomingMessage,
    InlineButton,
    KeyboardLayout,
    MessagingAdapter,
)

__all__ = [
    "CallbackResult",
    "IncomingMessage",
    "InlineButton",
    "KeyboardLayout",
    "MessagingAdapter",
]
