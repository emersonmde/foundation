"""Tests for configuration loading."""

from __future__ import annotations

from pathlib import Path

import pytest

from foundation.config import load_config


def _write_toml(tmp_path: Path, content: str) -> Path:
    p = tmp_path / "config.toml"
    p.write_text(content)
    return p


class TestLoadConfig:
    @pytest.fixture(autouse=True)
    def _isolate_telegram_env(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """Prevent real .env values from leaking into config tests."""
        monkeypatch.setenv("FOUNDATION_TELEGRAM_TOKEN", "")
        monkeypatch.setenv("FOUNDATION_TELEGRAM_USER_ID", "0")

    def test_loads_example_config(self, tmp_path: Path) -> None:
        """The example config file should parse successfully."""
        cfg_path = Path(__file__).parents[2] / "config.toml"
        config = load_config(cfg_path)
        assert config.claude.cli_command == "claude"
        assert config.foundation.log_level == "INFO"

    def test_expands_tilde_in_paths(self, tmp_path: Path) -> None:
        p = _write_toml(
            tmp_path,
            """
[foundation]
data_dir = "~/data"
log_dir = "~/logs"
""",
        )
        config = load_config(p)
        assert str(config.foundation.data_dir).startswith("/")
        assert "~" not in str(config.foundation.data_dir)

    def test_defaults_applied(self, tmp_path: Path) -> None:
        p = _write_toml(
            tmp_path,
            """
[foundation]
data_dir = "/tmp/data"
log_dir = "/tmp/logs"
""",
        )
        config = load_config(p)
        assert config.claude.cli_command == "claude"
        assert config.claude.model == "sonnet"
        assert config.usage.daily_token_budget == 0

    def test_missing_data_dir_raises(self, tmp_path: Path) -> None:
        p = _write_toml(
            tmp_path,
            """
[foundation]
log_dir = "/tmp/logs"
""",
        )
        with pytest.raises(ValueError, match="data_dir is required"):
            load_config(p)

    def test_missing_log_dir_raises(self, tmp_path: Path) -> None:
        p = _write_toml(
            tmp_path,
            """
[foundation]
data_dir = "/tmp/data"
""",
        )
        with pytest.raises(ValueError, match="log_dir is required"):
            load_config(p)

    def test_invalid_log_level_raises(self, tmp_path: Path) -> None:
        p = _write_toml(
            tmp_path,
            """
[foundation]
data_dir = "/tmp/data"
log_dir = "/tmp/logs"
log_level = "TRACE"
""",
        )
        with pytest.raises(ValueError, match="log_level"):
            load_config(p)

    def test_negative_max_turns_raises(self, tmp_path: Path) -> None:
        p = _write_toml(
            tmp_path,
            """
[foundation]
data_dir = "/tmp/data"
log_dir = "/tmp/logs"

[claude]
max_turns = -1
""",
        )
        with pytest.raises(ValueError, match="max_turns"):
            load_config(p)

    def test_missing_file_raises(self) -> None:
        with pytest.raises(FileNotFoundError):
            load_config(Path("/nonexistent/config.toml"))

    def test_custom_cli_command(self, tmp_path: Path) -> None:
        p = _write_toml(
            tmp_path,
            """
[foundation]
data_dir = "/tmp/data"
log_dir = "/tmp/logs"

[claude]
cli_command = "python -m foundation.testing.claude_stub"
""",
        )
        config = load_config(p)
        assert config.claude.cli_command == "python -m foundation.testing.claude_stub"

    def test_custom_values(self, tmp_path: Path) -> None:
        p = _write_toml(
            tmp_path,
            """
[foundation]
data_dir = "/tmp/data"
log_dir = "/tmp/logs"
log_level = "DEBUG"

[usage]
daily_token_budget = 1000
""",
        )
        config = load_config(p)
        assert config.foundation.log_level == "DEBUG"
        assert config.usage.daily_token_budget == 1000

    def test_telegram_defaults(self, tmp_path: Path) -> None:
        p = _write_toml(
            tmp_path,
            """
[foundation]
data_dir = "/tmp/data"
log_dir = "/tmp/logs"
""",
        )
        config = load_config(p)
        assert config.telegram.user_id == 0
        assert config.telegram.bot_token == ""

    def test_telegram_user_id_from_env(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setenv("FOUNDATION_TELEGRAM_USER_ID", "98765")
        p = _write_toml(
            tmp_path,
            """
[foundation]
data_dir = "/tmp/data"
log_dir = "/tmp/logs"
""",
        )
        config = load_config(p)
        assert config.telegram.user_id == 98765

    def test_telegram_token_from_env(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setenv("FOUNDATION_TELEGRAM_TOKEN", "bot123:abc")
        p = _write_toml(
            tmp_path,
            """
[foundation]
data_dir = "/tmp/data"
log_dir = "/tmp/logs"
""",
        )
        config = load_config(p)
        assert config.telegram.bot_token == "bot123:abc"

    def test_telegram_invalid_user_id_raises(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setenv("FOUNDATION_TELEGRAM_USER_ID", "not_a_number")
        p = _write_toml(
            tmp_path,
            """
[foundation]
data_dir = "/tmp/data"
log_dir = "/tmp/logs"
""",
        )
        with pytest.raises(ValueError, match="FOUNDATION_TELEGRAM_USER_ID"):
            load_config(p)

    def test_telegram_negative_user_id_raises(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setenv("FOUNDATION_TELEGRAM_USER_ID", "-1")
        p = _write_toml(
            tmp_path,
            """
[foundation]
data_dir = "/tmp/data"
log_dir = "/tmp/logs"
""",
        )
        with pytest.raises(ValueError, match="FOUNDATION_TELEGRAM_USER_ID"):
            load_config(p)
