# Research: Messaging Platform Alternatives

## Decision: Telegram

After evaluating five platforms, Telegram was selected for the best combination of bot API quality, UX, and development speed. The messaging layer will be designed as a pluggable adapter so alternatives can be added later.

## Comparison Matrix

| Feature | Telegram | Signal (signalbot) | ntfy (self-hosted) | Matrix (matrix-nio) | Custom PWA |
|---|---|---|---|---|---|
| **Inline buttons** | Native, excellent | None (text-only) | HTTP callback (max 3) | Reactions only | Full control |
| **Free-text replies** | Native | Native | No | Native | Native |
| **Rich formatting** | HTML, code blocks | Basic markdown | Title + body only | HTML | Full control |
| **Push notifications** | Native | Native | iOS/Android app | Via Element app | Web Push API |
| **E2E encryption** | No (server-side only) | Yes | TLS (self-hosted) | Optional | TLS (self-hosted) |
| **Setup complexity** | Create bot via BotFather | Docker + phone number | Single binary | Homeserver + DB | Build it |
| **Python async** | python-telegram-bot v20 | signalbot v0.24.1 | Plain HTTP (aiohttp) | matrix-nio | FastAPI + WebSocket |
| **API stability** | Official, very stable | Reverse-engineered, may break | Stable | Mature but complex | We maintain it |
| **Infrastructure** | None (Telegram hosts) | Docker container | Single Go binary | Synapse server | FastAPI server |

## Platform Details

### Telegram (Selected)

**Strengths:**
- Best bot API by far — inline keyboards, callback queries, rich HTML formatting
- Zero infrastructure (Telegram hosts the bot server)
- python-telegram-bot v20+ is mature, async-native, well-documented
- ConversationHandler for multi-step flows, built-in persistence, JobQueue, rate limiter

**Weakness:**
- Not E2E encrypted for bot chats. Plan summaries and code details route through Telegram's servers.
- Acceptable for personal/open-source work. For proprietary code, consider adding a Signal or PWA adapter later.

### Signal (via signalbot)

- `signalbot` v0.24.1 (PyPI, Feb 2026, MIT license) wraps `signal-cli-rest-api`
- Requires a Docker container running the Java-based `signal-cli` with a REST API wrapper
- Requires registering a dedicated phone number for the bot
- **No inline keyboards or buttons** — interactions are text-only ("reply 1 to approve, 2 to reject")
- E2E encrypted, but the bridge reverse-engineers Signal's protocol and could break with updates
- Async Python support via `signalbot`

### ntfy

- Self-hosted push notification service (single Go binary)
- Supports **action buttons** (up to 3) that trigger HTTP callbacks back to your server
- Button types: `view` (open URL), `http` (POST/GET to URL), `broadcast` (Android intent), `copy`
- **Cannot handle free-text conversations** — push-only, no reply capability
- Subscribe to topics via JSON stream, SSE, WebSocket, or polling
- No authentication beyond topic name obscurity (with self-hosted, can add auth)
- Could work as a notification layer alongside another conversation system

### Matrix (Element)

- Open protocol, self-hostable (Synapse homeserver)
- `matrix-nio` is a mature async Python library
- Rich HTML formatting, E2E encryption support
- **No native inline buttons** — reactions are the closest analog
- Requires running a Matrix homeserver: significant ops overhead for single-user
- E2E encryption in bots requires managing encryption key stores (complex)

### Custom PWA

- Maximum control — custom buttons, UI, push notifications via Web Push API
- Fully self-hosted, TLS encrypted, nothing leaves your infrastructure
- Requires building the notification UI (FastAPI + WebSocket + HTML/JS frontend)
- Most work to build, but most flexible long-term

## Adapter Architecture

The messaging layer should define a simple interface:

```python
class MessagingAdapter(Protocol):
    async def send_message(self, text: str, buttons: list[Button] | None = None) -> str:
        """Send a message. Returns message ID."""
        ...

    async def edit_message(self, message_id: str, text: str, buttons: list[Button] | None = None) -> None:
        """Edit a previously sent message."""
        ...

    async def wait_for_callback(self, message_id: str, timeout: float | None = None) -> CallbackResult:
        """Wait for a button press on a message."""
        ...

    async def wait_for_reply(self, message_id: str, timeout: float | None = None) -> str:
        """Wait for a free-text reply to a message."""
        ...
```

This decouples the orchestrator from the specific messaging platform. Telegram is the first implementation; others can be added by implementing the same interface.
