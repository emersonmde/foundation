# Research: Telegram Bot Integration

## Library Choice: python-telegram-bot v20+

Chosen over aiogram v3 for: built-in ConversationHandler (multi-step approval flows), persistence layer, JobQueue (scheduled checks), AIORateLimiter, and more English-language community support.

## Integration Pattern: Manual Lifecycle

`Application.run_polling()` blocks and takes ownership of the event loop — unsuitable for a daemon that also manages subprocesses and database operations.

The manual lifecycle pattern integrates cleanly with an existing asyncio loop:

```python
# All three are non-blocking coroutines that return immediately
await app.initialize()              # Fetch bot info, init persistence
await app.start()                   # Start job queue and update processing
await app.updater.start_polling()   # Start background long-polling task

# ... daemon runs here, event loop shared with subprocess management ...

# Graceful shutdown (reverse order)
await app.updater.stop()
await app.stop()
await app.shutdown()
```

`start_polling()` spawns a background `asyncio.Task` that long-polls Telegram's `getUpdates` endpoint. Updates go into an internal `asyncio.Queue` and are dispatched to handlers cooperatively. No threads involved.

## Inline Keyboards for Approvals

```python
keyboard = InlineKeyboardMarkup([
    [
        InlineKeyboardButton("Approve", callback_data="a:req_id"),
        InlineKeyboardButton("Reject", callback_data="r:req_id"),
    ],
    [
        InlineKeyboardButton("Details", callback_data="d:req_id"),
        InlineKeyboardButton("Modify", callback_data="m:req_id"),
    ],
])

await bot.send_message(chat_id=ADMIN_ID, text="...", reply_markup=keyboard, parse_mode="HTML")
```

Callbacks handled via `CallbackQueryHandler` with regex pattern matching:

```python
app.add_handler(CallbackQueryHandler(handle_approve, pattern=r"^a:"))
app.add_handler(CallbackQueryHandler(handle_reject, pattern=r"^r:"))
```

**Important:** Always call `query.answer()` first in callback handlers (removes loading spinner). Must be called within ~30 seconds.

## Key Limits

| Limit | Value |
|---|---|
| Message text | 4096 UTF-8 characters |
| Caption | 1024 characters |
| `callback_data` | 1-64 bytes |
| Messages to one chat | ~1/sec (burst up to ~20) |
| Messages globally | ~30/sec across all chats |

### callback_data Strategy

64 bytes is tight. Store state server-side, reference by short ID:

```python
pending_approvals: dict[str, ApprovalRequest] = {}
approval_id = str(uuid.uuid4())[:8]
pending_approvals[approval_id] = ApprovalRequest(...)
# callback_data: "a:abc12345" (11 bytes)
```

### Long Message Handling

Split on newlines, send as multiple messages. For plan reviews: send a summary first, offer "Show full details" button that sends the rest.

## Formatting

**Use HTML, not MarkdownV2.** MarkdownV2 requires escaping 20+ special characters, which is error-prone with dynamic content.

```python
await bot.send_message(
    chat_id=ADMIN_ID,
    text=(
        "<b>Plan Review: Add auth module</b>\n\n"
        "<i>Task:</i> <code>task-42</code>\n"
        "<i>Files:</i> 3 modified, 1 created\n\n"
        "<pre>1. Create auth middleware\n"
        "2. Add JWT token validation\n"
        "3. Update route handlers</pre>"
    ),
    parse_mode="HTML",
    reply_markup=keyboard,
)
```

Always escape user/agent-generated content with `html.escape()`.

## Authentication

Lock bot to a single Telegram user ID:

```python
AUTHORIZED_USER_ID = 123456789  # from config

async def check_auth(update: Update, context) -> bool:
    if update.effective_user.id != AUTHORIZED_USER_ID:
        return False  # silently ignore unauthorized users
    return True
```

Apply as a filter on all handlers, or use a middleware-style check.

## Gotchas

1. **Signal handler conflicts:** Don't use `run_polling()` — it installs its own SIGINT/SIGTERM handlers that clobber the daemon's.

2. **Blocking calls in handlers:** Any synchronous operation blocks the entire event loop. Use `await asyncio.to_thread()` for sync DB queries, or use an async DB driver.

3. **TaskGroup cancellation:** If using `asyncio.TaskGroup` and one task fails, ALL tasks are cancelled. For resilience, prefer manual `asyncio.create_task()` with explicit cancellation.

4. **Subprocess output streaming:** Buffer and batch. Don't send every line. Edit existing messages at most 1-2 times/sec. Truncate to 4000 chars.

## Daemon Integration Pattern

```python
class Daemon:
    async def run(self):
        loop = asyncio.get_running_loop()
        for sig in (signal.SIGINT, signal.SIGTERM):
            loop.add_signal_handler(sig, self._stop.set)

        self.app = Application.builder().token(TOKEN).build()
        self._register_handlers()

        await self.app.initialize()
        await self.app.start()
        await self.app.updater.start_polling(drop_pending_updates=True)

        try:
            tasks = [
                asyncio.create_task(self._orchestrator_loop()),
                asyncio.create_task(self._stop.wait()),
            ]
            await asyncio.wait(tasks, return_when=asyncio.FIRST_COMPLETED)
        finally:
            for t in tasks:
                t.cancel()
            await self.app.updater.stop()
            await self.app.stop()
            await self.app.shutdown()
```
