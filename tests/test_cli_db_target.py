"""Tests ensuring CLI commands resolve database target consistently."""
from pathlib import Path
import os
import sys

from click.testing import CliRunner

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import cli.commands as cli_commands


class _DummyConn:
    def close(self):
        return None


def test_init_db_command_uses_database_target(monkeypatch):
    runner = CliRunner()
    captured: dict[str, str] = {}

    monkeypatch.delenv("DATABASE_URL", raising=False)
    monkeypatch.setattr(cli_commands.config, "DATABASE_URL", "postgresql://example/ilcf")
    monkeypatch.setattr(cli_commands.config, "DATABASE_TARGET", "/tmp/legacy-target.db")
    monkeypatch.setattr(cli_commands.config, "DATABASE_PATH", "/tmp/legacy.db")

    def _fake_init_db(target):
        captured["target"] = target

    monkeypatch.setattr(cli_commands, "init_db", _fake_init_db)

    result = runner.invoke(cli_commands.cli, ["init-db"])
    assert result.exit_code == 0
    assert captured["target"] == "postgresql://example/ilcf"


def test_refresh_analytics_command_uses_database_target(monkeypatch):
    runner = CliRunner()
    captured: dict[str, str] = {}

    monkeypatch.delenv("DATABASE_URL", raising=False)
    monkeypatch.setattr(cli_commands.config, "DATABASE_URL", "postgresql://example/ilcf")
    monkeypatch.setattr(cli_commands.config, "DATABASE_TARGET", "/tmp/legacy-target.db")
    monkeypatch.setattr(cli_commands.config, "DATABASE_PATH", "/tmp/legacy.db")

    def _fake_get_db(target):
        captured["target"] = target
        return _DummyConn()

    monkeypatch.setattr(cli_commands, "get_db", _fake_get_db)
    monkeypatch.setattr(cli_commands, "refresh_analytics_materialized", lambda _conn: {"ok": 1})

    result = runner.invoke(cli_commands.cli, ["refresh-analytics", "--skip-snapshot"])
    assert result.exit_code == 0
    assert captured["target"] == "postgresql://example/ilcf"


def test_cli_commands_do_not_hardcode_database_path_targets():
    source = Path(cli_commands.__file__).read_text(encoding="utf-8")
    assert "get_db(config.DATABASE_PATH)" not in source
    assert "init_db(config.DATABASE_PATH)" not in source


def test_db_target_prefers_environment_database_url(monkeypatch):
    monkeypatch.setenv("DATABASE_URL", "postgresql://env/ilcf")
    monkeypatch.setattr(cli_commands.config, "DATABASE_URL", "postgresql://config/ilcf")
    monkeypatch.setattr(cli_commands.config, "DATABASE_TARGET", "/tmp/legacy-target.db")
    monkeypatch.setattr(cli_commands.config, "DATABASE_PATH", "/tmp/legacy.db")
    assert cli_commands._db_target() == "postgresql://env/ilcf"


def test_db_target_falls_back_to_legacy_path_when_no_urls(monkeypatch):
    monkeypatch.delenv("DATABASE_URL", raising=False)
    monkeypatch.setattr(cli_commands.config, "DATABASE_URL", "")
    monkeypatch.setattr(cli_commands.config, "DATABASE_TARGET", "")
    monkeypatch.setattr(cli_commands.config, "DATABASE_PATH", "/tmp/legacy.db")
    assert cli_commands._db_target() == "/tmp/legacy.db"
