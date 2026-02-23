"""CLI entry points: health-check, dump-state, run-task, run."""

from __future__ import annotations

import argparse
import asyncio
import sys
from pathlib import Path

from foundation.config import load_config
from foundation.logging import setup_logging


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="foundation",
        description="Foundation — autonomous development orchestrator",
    )
    parser.add_argument(
        "--config",
        type=Path,
        default=Path("config.toml"),
        help="Path to config file (default: config.toml)",
    )

    sub = parser.add_subparsers(dest="command")

    sub.add_parser("health-check", help="Verify config, DB, and claude CLI")
    sub.add_parser("dump-state", help="Print tasks and usage summary")

    run = sub.add_parser("run-task", help="Invoke claude -p with a prompt")
    run.add_argument("prompt", help="The prompt to send to claude")

    sub.add_parser("telegram-test", help="Start bot, send greeting, wait for interaction")
    sub.add_parser("run", help="Start the orchestrator daemon")

    return parser


async def _health_check(config_path: Path) -> int:
    """Verify config loads, DB opens, claude CLI is reachable."""
    from foundation.db.connection import connect
    from foundation.db.schema import apply_schema

    print(f"Loading config from {config_path}...")
    try:
        config = load_config(config_path)
    except Exception as e:
        print(f"FAIL: Config error: {e}")
        return 1
    print(f"  OK: data_dir={config.foundation.data_dir}")

    db_path = config.foundation.data_dir / "foundation.db"
    print(f"Opening database at {db_path}...")
    try:
        db = await connect(db_path)
        await apply_schema(db)
        await db.close()
    except Exception as e:
        print(f"FAIL: Database error: {e}")
        return 1
    print("  OK: Schema applied")

    print(f"Checking claude CLI ({config.claude.cli_command})...")
    try:
        from foundation.claude.cli import invoke

        result = await invoke(
            "Respond with exactly: ok",
            cli_command=config.claude.cli_command,
            model=config.claude.model,
            permission_mode="plan",
            max_turns=1,
        )
        if result.return_code != 0:
            print(f"FAIL: CLI exited with code {result.return_code}")
            return 1
        print(f"  OK: session_id={result.session_id}")
    except FileNotFoundError:
        print(f"FAIL: '{config.claude.cli_command}' not found")
        return 1
    except Exception as e:
        print(f"FAIL: CLI error: {e}")
        return 1

    print("\nAll checks passed.")
    return 0


async def _dump_state(config_path: Path) -> int:
    """Print tasks and usage summary."""
    from foundation.db.connection import connect
    from foundation.db.schema import apply_schema
    from foundation.db.tasks import list_tasks
    from foundation.db.usage import get_usage_summary

    try:
        config = load_config(config_path)
    except Exception as e:
        print(f"FAIL: Config error: {e}")
        return 1

    db_path = config.foundation.data_dir / "foundation.db"
    try:
        db = await connect(db_path)
    except Exception as e:
        print(f"FAIL: Database error: {e}")
        return 1

    try:
        await apply_schema(db)

        print("=== Tasks ===")
        tasks = await list_tasks(db)
        if not tasks:
            print("  (none)")
        for task in tasks:
            print(f"  [{task.status:^18}] {task.title} ({task.id[:8]}...)")

        print("\n=== Usage Summary ===")
        summary = await get_usage_summary(db)
        print(f"  Records: {summary.record_count}")
        print(f"  Input tokens:  {summary.total_input_tokens:,}")
        print(f"  Output tokens: {summary.total_output_tokens:,}")
        print(f"  Total duration: {summary.total_duration_ms:,}ms")
    except Exception as e:
        print(f"FAIL: {e}")
        return 1
    finally:
        await db.close()
    return 0


async def _run_task(config_path: Path, prompt: str) -> int:
    """Invoke claude -p and print the result."""
    from foundation.claude.cli import invoke
    from foundation.db.connection import connect
    from foundation.db.schema import apply_schema
    from foundation.db.tasks import create_task, update_task_status
    from foundation.db.usage import record_usage

    config = load_config(config_path)
    logger = setup_logging(config.foundation.log_level, config.foundation.log_dir)

    db_path = config.foundation.data_dir / "foundation.db"
    db = await connect(db_path)
    try:
        await apply_schema(db)

        task = await create_task(db, prompt[:100], prompt)
        logger.info("Created task %s: %s", task.id[:8], task.title)

        await update_task_status(db, task.id, "executing")

        try:
            result = await invoke(
                prompt,
                cli_command=config.claude.cli_command,
                model=config.claude.model,
                permission_mode=config.claude.execute_permission_mode,
                max_turns=config.claude.max_turns,
            )
        except Exception as e:
            await update_task_status(db, task.id, "failed", error_message=str(e))
            logger.error("Task %s failed: %s", task.id[:8], e)
            return 1

        await record_usage(
            db,
            task_id=task.id,
            session_id=result.session_id,
            category="coding",
            model=config.claude.model,
            input_tokens=result.usage.input_tokens,
            output_tokens=result.usage.output_tokens,
            cache_read_tokens=result.usage.cache_read_input_tokens,
            cache_creation_tokens=result.usage.cache_creation_input_tokens,
            duration_ms=result.duration_ms,
        )

        if result.status == "success":
            await update_task_status(
                db,
                task.id,
                "complete",
                session_id=result.session_id,
                result_text=result.result_text,
            )
            print(f"Session: {result.session_id}")
            print(f"Status:  {result.status}")
            print(f"Tokens:  {result.usage.input_tokens} in / {result.usage.output_tokens} out")
            print(f"Duration: {result.duration_ms}ms")
            print(f"\n--- Result ---\n{result.result_text}")
        else:
            await update_task_status(
                db,
                task.id,
                "failed",
                session_id=result.session_id,
                error_message=result.result_text,
            )
            print(
                f"FAILED (exit code {result.return_code}): {result.result_text}", file=sys.stderr
            )

        return 0 if result.status == "success" else 1
    finally:
        await db.close()


async def _telegram_test(config_path: Path) -> int:
    """Start bot, send greeting with inline keyboard, wait for one interaction."""
    import signal

    from foundation.db.connection import connect
    from foundation.db.schema import apply_schema
    from foundation.messaging.adapter import CallbackResult, InlineButton
    from foundation.telegram.bot import TelegramBot

    try:
        config = load_config(config_path)
    except Exception as e:
        print(f"FAIL: Config error: {e}")
        return 1

    if not config.telegram.bot_token:
        print("FAIL: FOUNDATION_TELEGRAM_TOKEN not set (check .env file)")
        return 1

    if config.telegram.user_id == 0:
        print("FAIL: telegram.user_id not configured in config.toml")
        return 1

    db_path = config.foundation.data_dir / "foundation.db"
    db_path.parent.mkdir(parents=True, exist_ok=True)
    db = await connect(db_path)
    try:
        await apply_schema(db)
        bot = TelegramBot(config.telegram, db)

        # Handle SIGINT/SIGTERM
        loop = asyncio.get_running_loop()
        for sig in (signal.SIGINT, signal.SIGTERM):
            loop.add_signal_handler(sig, bot.request_shutdown)

        await bot.start()
        print("Bot started. Sending greeting...")

        adapter = bot.adapter
        msg_id = await adapter.send_message(
            "<b>Foundation</b> telegram-test\n\nBot is running. Tap a button or send a reply.",
            buttons=[
                [
                    InlineButton(text="Working!", callback_data="test_ok"),
                    InlineButton(text="Test callback", callback_data="test_cb"),
                ]
            ],
        )
        print(f"Greeting sent (message_id={msg_id}). Waiting for interaction...")

        # Wait for either a callback or a reply
        done, pending = await asyncio.wait(
            [
                asyncio.create_task(adapter.wait_for_callback(msg_id, timeout=120)),
                asyncio.create_task(adapter.wait_for_reply(timeout=120)),
            ],
            return_when=asyncio.FIRST_COMPLETED,
        )
        for task in pending:
            task.cancel()
        await asyncio.gather(*pending, return_exceptions=True)

        for task in done:
            try:
                result = task.result()
                if isinstance(result, CallbackResult):
                    print(f"Got callback: {result.data}")
                    await adapter.edit_message(
                        msg_id, f"Callback received: <code>{result.data}</code>"
                    )
                elif isinstance(result, str):
                    print(f"Got reply: {result}")
                    await adapter.send_message(f"Echo: {result}")
            except TimeoutError:
                print("Timed out waiting for interaction.")

        print("Test complete. Shutting down...")
        await bot.stop()
    finally:
        await db.close()

    return 0


async def _run_daemon(config_path: Path) -> int:
    """Start the orchestrator daemon with Telegram frontend."""
    import signal

    from foundation.db.connection import connect
    from foundation.db.schema import apply_schema
    from foundation.messaging.adapter import IncomingMessage
    from foundation.orchestrator import Orchestrator
    from foundation.telegram.bot import TelegramBot

    try:
        config = load_config(config_path)
    except Exception as e:
        print(f"FAIL: Config error: {e}")
        return 1

    if not config.telegram.bot_token:
        print("FAIL: FOUNDATION_TELEGRAM_TOKEN not set (check .env file)")
        return 1

    if config.telegram.user_id == 0:
        print("FAIL: telegram.user_id not configured in config.toml")
        return 1

    logger = setup_logging(config.foundation.log_level, config.foundation.log_dir)
    logger.info("Foundation starting...")

    db_path = config.foundation.data_dir / "foundation.db"
    db_path.parent.mkdir(parents=True, exist_ok=True)
    db = await connect(db_path)

    try:
        await apply_schema(db)

        incoming_queue: asyncio.Queue[IncomingMessage] = asyncio.Queue()
        bot = TelegramBot(config.telegram, db, incoming_queue=incoming_queue)
        orchestrator = Orchestrator(
            claude_config=config.claude,
            db=db,
            messaging=bot.adapter,
            incoming_queue=incoming_queue,
        )

        # Signal handlers for graceful shutdown
        loop = asyncio.get_running_loop()

        _shutdown_task: asyncio.Task[None] | None = None

        def handle_signal() -> None:
            nonlocal _shutdown_task
            if _shutdown_task is not None:
                return
            logger.info("Shutdown signal received")
            bot.request_shutdown()
            _shutdown_task = loop.create_task(orchestrator.shutdown())

        for sig in (signal.SIGINT, signal.SIGTERM):
            loop.add_signal_handler(sig, handle_signal)

        await bot.start()
        logger.info("Foundation running — Telegram + Orchestrator")

        try:
            await orchestrator.run()
        finally:
            await bot.stop()

    finally:
        await db.close()

    logger.info("Foundation stopped")
    return 0


def main(argv: list[str] | None = None) -> None:
    """Main entry point."""
    parser = _build_parser()
    args = parser.parse_args(argv)

    if args.command == "health-check":
        sys.exit(asyncio.run(_health_check(args.config)))
    elif args.command == "dump-state":
        sys.exit(asyncio.run(_dump_state(args.config)))
    elif args.command == "run-task":
        sys.exit(asyncio.run(_run_task(args.config, args.prompt)))
    elif args.command == "telegram-test":
        sys.exit(asyncio.run(_telegram_test(args.config)))
    elif args.command == "run":
        sys.exit(asyncio.run(_run_daemon(args.config)))
    else:
        parser.print_help()
        sys.exit(1)


if __name__ == "__main__":
    main()
