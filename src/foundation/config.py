"""TOML configuration loading with frozen dataclasses."""

from __future__ import annotations

import tomllib
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class FoundationConfig:
    """Core foundation settings."""

    data_dir: Path
    log_dir: Path
    log_level: str = "INFO"


@dataclass(frozen=True)
class ClaudeConfig:
    """Claude CLI invocation settings."""

    cli_command: str = "claude"
    model: str = "sonnet"
    plan_permission_mode: str = "plan"
    execute_permission_mode: str = "bypassPermissions"
    max_turns: int = 0


@dataclass(frozen=True)
class UsageConfig:
    """Token budget settings."""

    daily_token_budget: int = 0
    weekly_token_budget: int = 0


@dataclass(frozen=True)
class Config:
    """Top-level configuration."""

    foundation: FoundationConfig
    claude: ClaudeConfig
    usage: UsageConfig


_VALID_LOG_LEVELS = {"DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"}


def load_config(path: Path) -> Config:
    """Load and validate configuration from a TOML file.

    Raises:
        FileNotFoundError: If the config file doesn't exist.
        ValueError: If required fields are missing or invalid.
        tomllib.TOMLDecodeError: If the TOML is malformed.
    """
    with open(path, "rb") as f:
        raw = tomllib.load(f)

    foundation_raw = raw.get("foundation", {})
    claude_raw = raw.get("claude", {})
    usage_raw = raw.get("usage", {})

    # Validate and expand paths
    data_dir_str = foundation_raw.get("data_dir")
    if not data_dir_str:
        raise ValueError("foundation.data_dir is required")
    data_dir = Path(data_dir_str).expanduser()

    log_dir_str = foundation_raw.get("log_dir")
    if not log_dir_str:
        raise ValueError("foundation.log_dir is required")
    log_dir = Path(log_dir_str).expanduser()

    log_level = foundation_raw.get("log_level", "INFO").upper()
    if log_level not in _VALID_LOG_LEVELS:
        raise ValueError(
            f"foundation.log_level must be one of {sorted(_VALID_LOG_LEVELS)}, got '{log_level}'"
        )

    max_turns = claude_raw.get("max_turns", 0)
    if isinstance(max_turns, bool) or not isinstance(max_turns, int) or max_turns < 0:
        raise ValueError(f"claude.max_turns must be a non-negative integer, got {max_turns!r}")

    daily_budget = usage_raw.get("daily_token_budget", 0)
    if isinstance(daily_budget, bool) or not isinstance(daily_budget, int) or daily_budget < 0:
        raise ValueError(f"usage.daily_token_budget must be non-negative, got {daily_budget!r}")

    weekly_budget = usage_raw.get("weekly_token_budget", 0)
    if isinstance(weekly_budget, bool) or not isinstance(weekly_budget, int) or weekly_budget < 0:
        raise ValueError(f"usage.weekly_token_budget must be non-negative, got {weekly_budget!r}")

    # Build ClaudeConfig from raw dict, falling back to dataclass defaults
    claude_fields = {k: v for k, v in claude_raw.items() if k in ClaudeConfig.__dataclass_fields__}
    claude_fields["max_turns"] = max_turns

    return Config(
        foundation=FoundationConfig(
            data_dir=data_dir,
            log_dir=log_dir,
            log_level=log_level,
        ),
        claude=ClaudeConfig(**claude_fields),
        usage=UsageConfig(
            daily_token_budget=daily_budget,
            weekly_token_budget=weekly_budget,
        ),
    )
