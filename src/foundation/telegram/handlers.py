"""Telegram command handlers and auth filter."""

from __future__ import annotations

import logging
from typing import Any

import aiosqlite
from telegram import Message, Update
from telegram.ext import (
    Application,
    CallbackQueryHandler,
    CommandHandler,
    ContextTypes,
    MessageHandler,
    filters,
)

from foundation.db.tasks import list_tasks
from foundation.db.usage import get_usage_summary
from foundation.telegram.adapter import CallbackResult, TelegramAdapter
from foundation.telegram.formatting import format_help, format_status

logger = logging.getLogger(__name__)

# Keys for context.bot_data
_KEY_ADAPTER = "adapter"
_KEY_DB = "db"
_KEY_USER_ID = "user_id"


class _AuthFilter(filters.BaseFilter):
    """Filter that only passes messages from the authorized user."""

    def __init__(self, user_id: int) -> None:
        super().__init__()
        self._user_id = user_id

    def filter(self, message: object) -> bool:
        if not isinstance(message, Message):
            return False
        user = message.from_user
        if user is None:
            return False
        return user.id == self._user_id


def auth_filter(user_id: int) -> filters.BaseFilter:
    """Create a filter that only passes messages from the given user ID."""
    return _AuthFilter(user_id)


async def handle_start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Handle /start command."""
    if update.effective_message is None:
        return
    await update.effective_message.reply_text(
        "<b>Foundation</b> is online.\n\nSend /help for available commands.",
        parse_mode="HTML",
    )


async def handle_help(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Handle /help command."""
    if update.effective_message is None:
        return
    await update.effective_message.reply_text(format_help(), parse_mode="HTML")


async def handle_status(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Handle /status command — show task and usage summary."""
    if update.effective_message is None:
        return

    db: aiosqlite.Connection = context.bot_data[_KEY_DB]
    tasks = await list_tasks(db)
    usage = await get_usage_summary(db)
    await update.effective_message.reply_text(format_status(tasks, usage), parse_mode="HTML")


async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Handle free-text messages from the authorized user."""
    if update.effective_message is None or update.effective_message.text is None:
        return

    adapter: TelegramAdapter = context.bot_data[_KEY_ADAPTER]
    text = update.effective_message.text

    # Check if there's a pending wait_for_reply
    if adapter.resolve_reply(text):
        logger.debug("Resolved pending reply with: %s", text[:50])
        return

    # Default: acknowledge the message
    await update.effective_message.reply_text(
        f"Received: {text[:100]}",
        parse_mode=None,
    )


async def handle_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Handle inline keyboard callback queries."""
    query = update.callback_query
    if query is None:
        return

    # Auth check — callback queries bypass message filters
    authorized_user_id: int = context.bot_data[_KEY_USER_ID]
    if query.from_user is None or query.from_user.id != authorized_user_id:
        uid = query.from_user.id if query.from_user else "unknown"
        logger.warning("Unauthorized callback from user %s — ignoring", uid)
        await query.answer()
        return

    # Always answer the callback to clear the loading indicator
    await query.answer()

    adapter: TelegramAdapter = context.bot_data[_KEY_ADAPTER]
    data = query.data or ""

    if not isinstance(query.message, Message):
        logger.warning("Callback query %s has no accessible message", query.id)
        return
    message_id = str(query.message.message_id)

    result = CallbackResult(
        callback_id=str(query.id),
        data=data,
        message_id=message_id,
    )

    if adapter.resolve_callback(message_id, result):
        logger.debug("Resolved callback for message %s: %s", message_id, data)
    else:
        logger.warning("No pending future for callback on message %s", message_id)


async def _log_unauthorized(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Log and ignore messages from unauthorized users."""
    user = update.effective_user
    uid = user.id if user else "unknown"
    logger.warning("Unauthorized message from user %s — ignoring", uid)


def register_handlers(
    app: Application[Any, Any, Any, Any, Any, Any],
    user_id: int,
    adapter: TelegramAdapter,
    db: aiosqlite.Connection,
) -> None:
    """Wire up all handlers with auth filtering."""
    auth = auth_filter(user_id)

    app.bot_data[_KEY_ADAPTER] = adapter
    app.bot_data[_KEY_DB] = db
    app.bot_data[_KEY_USER_ID] = user_id

    app.add_handler(CommandHandler("start", handle_start, filters=auth))
    app.add_handler(CommandHandler("help", handle_help, filters=auth))
    app.add_handler(CommandHandler("status", handle_status, filters=auth))
    app.add_handler(MessageHandler(auth & filters.TEXT & ~filters.COMMAND, handle_message))
    app.add_handler(CallbackQueryHandler(handle_callback))
    app.add_handler(MessageHandler(~auth & filters.ALL, _log_unauthorized))
