"""Shared test fixtures and configuration."""

from __future__ import annotations

import sys
from collections.abc import AsyncIterator
from pathlib import Path

import aiosqlite
import pytest

from foundation.config import ClaudeConfig, Config, FoundationConfig, TelegramConfig, UsageConfig
from foundation.db.connection import connect
from foundation.db.schema import apply_schema


def pytest_addoption(parser: pytest.Parser) -> None:
    parser.addoption(
        "--run-smoke",
        action="store_true",
        default=False,
        help="Run smoke tests that require real claude CLI and burn tokens",
    )


def pytest_configure(config: pytest.Config) -> None:
    config.addinivalue_line("markers", "smoke: requires real claude CLI and burns tokens")


def pytest_collection_modifyitems(config: pytest.Config, items: list[pytest.Item]) -> None:
    if not config.getoption("--run-smoke"):
        skip_smoke = pytest.mark.skip(reason="need --run-smoke to run")
        for item in items:
            if "smoke" in item.keywords:
                item.add_marker(skip_smoke)


@pytest.fixture
def stub_command() -> str:
    """Get the command to invoke the Claude CLI stub."""
    return f"{sys.executable} -m foundation.testing.claude_stub"


@pytest.fixture
def fixture_dir() -> Path:
    """Path to test fixture files."""
    return (Path(__file__).parent / "fixtures" / "stream-json").resolve()


@pytest.fixture
def test_config(tmp_path: Path, stub_command: str) -> Config:
    """A test configuration using the stub CLI and temp directories."""
    return Config(
        foundation=FoundationConfig(
            data_dir=tmp_path / "data",
            log_dir=tmp_path / "logs",
            log_level="DEBUG",
        ),
        claude=ClaudeConfig(
            cli_command=stub_command,
            model="sonnet",
        ),
        usage=UsageConfig(),
        telegram=TelegramConfig(user_id=12345, bot_token="test-token-123"),
    )


@pytest.fixture
async def db(tmp_path: Path) -> AsyncIterator[aiosqlite.Connection]:
    """A temp SQLite database with schema applied."""
    db_path = tmp_path / "test.db"
    conn = await connect(db_path)
    await apply_schema(conn)
    yield conn
    await conn.close()
