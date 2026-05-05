"""Tests for the /rck slash command wrapper in HermesCLI."""

import pytest
from unittest.mock import MagicMock, patch

from cli import HermesCLI
from hermes_cli.commands import resolve_command
from hermes_cli.rck import (
    ALLOWED_RCK_SUBCOMMANDS,
    build_rck_command,
    handle_rck_command,
    resolve_rck_command,
    resolve_rck_workspace,
    run_rck_subcommand,
)


def _make_cli(config=None):
    cli_obj = HermesCLI.__new__(HermesCLI)
    cli_obj.config = config or {}
    cli_obj.console = MagicMock()
    cli_obj._app = None
    return cli_obj


class TestRckCommandRegistry:
    def test_rck_is_registered(self):
        cmd = resolve_command("rck")
        assert cmd is not None
        assert cmd.name == "rck"
        assert cmd.cli_only is True


class TestRckModule:
    def test_allowlist_contains_expected_commands(self):
        assert ALLOWED_RCK_SUBCOMMANDS == {
            "current",
            "init",
            "status",
            "trace",
            "state",
            "anchor",
            "checkpoint",
            "inject",
        }

    def test_resolve_command_prefers_config_then_env_then_default(self, monkeypatch):
        monkeypatch.delenv("RCK_COMMAND", raising=False)
        assert resolve_rck_command({"rck": {"command": "rck-cli"}}) == "rck-cli"

        monkeypatch.setenv("RCK_COMMAND", "rck-env")
        assert resolve_rck_command({}) == "rck-env"

        monkeypatch.delenv("RCK_COMMAND", raising=False)
        assert resolve_rck_command({}) == "rck"

    def test_resolve_workspace_prefers_config_then_env_then_default(self, monkeypatch):
        monkeypatch.delenv("RCK_WORKSPACE", raising=False)
        assert resolve_rck_workspace({"rck": {"workspace": "/custom/workspace"}}) == "/custom/workspace"

        monkeypatch.setenv("RCK_WORKSPACE", "/tmp/rck-workspace")
        assert resolve_rck_workspace({}) == "/tmp/rck-workspace"

        monkeypatch.delenv("RCK_WORKSPACE", raising=False)
        assert resolve_rck_workspace({}) == "/home/rufus/.rck"

    def test_build_rck_command_uses_workspace_and_args(self):
        assert build_rck_command({"rck": {"command": "rck", "workspace": "/home/rufus/.rck"}}, "init", ["--dry-run"]) == [
            "rck",
            "--workspace",
            "/home/rufus/.rck",
            "init",
            "--dry-run",
        ]

    def test_run_rck_subcommand_invokes_subprocess_with_shell_false(self, monkeypatch):
        monkeypatch.delenv("RCK_COMMAND", raising=False)
        monkeypatch.delenv("RCK_WORKSPACE", raising=False)
        with patch("hermes_cli.rck.subprocess.run") as mock_run:
            mock_run.return_value = MagicMock(returncode=0, stdout="initialized\n", stderr="")
            result = run_rck_subcommand({"rck": {"command": "rck", "workspace": "/home/rufus/.rck"}}, "init")
        assert result.returncode == 0
        args, kwargs = mock_run.call_args
        assert args[0] == ["rck", "--workspace", "/home/rufus/.rck", "init"]
        assert kwargs["shell"] is False
        assert kwargs["capture_output"] is True
        assert kwargs["text"] is True
        assert kwargs["timeout"] == 60

    def test_run_rck_subcommand_rejects_disallowed_subcommand(self):
        with pytest.raises(ValueError, match="Unsupported RCK subcommand"):
            run_rck_subcommand({}, "rm")

    def test_run_rck_subcommand_missing_binary_returns_none(self):
        with patch("hermes_cli.rck.subprocess.run", side_effect=FileNotFoundError):
            assert run_rck_subcommand({}, "init") is None


class TestRckCommandDispatch:
    def test_rck_init_uses_workspace_and_external_cli(self, monkeypatch):
        cli_obj = _make_cli({"rck": {"command": "rck", "workspace": "/home/rufus/.rck"}})
        monkeypatch.delenv("RCK_COMMAND", raising=False)
        monkeypatch.delenv("RCK_WORKSPACE", raising=False)

        with patch("hermes_cli.rck_assisted.default_trace_id", return_value="rck-20260504-080910"), patch(
            "hermes_cli.rck_assisted.default_trace_label", return_value="RCK session 2026-05-04 08:09"
        ), patch("hermes_cli.rck.subprocess.run") as mock_run:
            mock_run.return_value = MagicMock(returncode=0, stdout="initialized\n", stderr="")
            cli_obj._handle_rck_command("/rck init")

        mock_run.assert_called_once()
        args, kwargs = mock_run.call_args
        assert args[0] == ["rck", "--workspace", "/home/rufus/.rck", "trace", "start", "rck-20260504-080910", "--label", "RCK session 2026-05-04 08:09"]
        assert kwargs["shell"] is False
        assert kwargs["capture_output"] is True
        assert kwargs["text"] is True
        assert kwargs["timeout"] == 60

    def test_rck_status_uses_env_fallbacks(self, monkeypatch):
        cli_obj = _make_cli()
        monkeypatch.setenv("RCK_COMMAND", "rck-cli")
        monkeypatch.setenv("RCK_WORKSPACE", "/tmp/rck-workspace")

        with patch("hermes_cli.rck.subprocess.run") as mock_run:
            mock_run.return_value = MagicMock(returncode=0, stdout="ok\n", stderr="")
            cli_obj._handle_rck_command("/rck status")

        mock_run.assert_called_once()
        args, _kwargs = mock_run.call_args
        assert args[0] == ["rck-cli", "--workspace", "/tmp/rck-workspace", "status"]

    def test_rck_state_add_passthrough_uses_subprocess(self):
        cli_obj = _make_cli({"rck": {"command": "rck", "workspace": "/home/rufus/.rck"}})

        with patch("hermes_cli.rck.subprocess.run") as mock_run:
            mock_run.return_value = MagicMock(returncode=0, stdout="StateId: state-1\n", stderr="")
            cli_obj._handle_rck_command('/rck state add trace-1 --title "Assisted RCK state" --kind rck.state --summary "hello"')

        mock_run.assert_called_once()
        args, kwargs = mock_run.call_args
        assert args[0] == [
            "rck",
            "--workspace",
            "/home/rufus/.rck",
            "state",
            "add",
            "trace-1",
            "--title",
            "Assisted RCK state",
            "--kind",
            "rck.state",
            "--summary",
            "hello",
        ]
        assert kwargs["shell"] is False

    def test_rck_checkpoint_add_passthrough_uses_subprocess(self):
        cli_obj = _make_cli({"rck": {"command": "rck", "workspace": "/home/rufus/.rck"}})

        with patch("hermes_cli.rck.subprocess.run") as mock_run:
            mock_run.return_value = MagicMock(returncode=0, stdout="StateId: state-1\nAnchorId: anchor-1\n", stderr="")
            cli_obj._handle_rck_command('/rck checkpoint add trace-1 --title "Assisted RCK checkpoint" --kind rck.checkpoint --summary "hello"')

        mock_run.assert_called_once()
        args, kwargs = mock_run.call_args
        assert args[0] == [
            "rck",
            "--workspace",
            "/home/rufus/.rck",
            "checkpoint",
            "add",
            "trace-1",
            "--title",
            "Assisted RCK checkpoint",
            "--kind",
            "rck.checkpoint",
            "--summary",
            "hello",
        ]
        assert kwargs["shell"] is False

    def test_rck_inject_assisted_uses_current_trace_id_and_trace_subcommand(self):
        cli_obj = _make_cli({"rck": {"command": "rck", "workspace": "/home/rufus/.rck"}})

        with patch("hermes_cli.rck_assisted.handle_rck_inject") as mock_inject:
            cli_obj._handle_rck_command("/rck inject")

        mock_inject.assert_called_once()

    def test_rck_inject_trace_passthrough_uses_subprocess(self):
        cli_obj = _make_cli({"rck": {"command": "rck", "workspace": "/home/rufus/.rck"}})

        with patch("hermes_cli.rck.subprocess.run") as mock_run:
            mock_run.return_value = MagicMock(returncode=0, stdout="# Trace Condensed\n", stderr="")
            cli_obj._handle_rck_command("/rck trace inject trace-1")

        mock_run.assert_called_once()
        args, kwargs = mock_run.call_args
        assert args[0] == ["rck", "--workspace", "/home/rufus/.rck", "trace", "inject", "trace-1"]
        assert kwargs["shell"] is False

    def test_rck_anchor_promote_passthrough_uses_subprocess(self):
        cli_obj = _make_cli({"rck": {"command": "rck", "workspace": "/home/rufus/.rck"}})

        with patch("hermes_cli.rck.subprocess.run") as mock_run:
            mock_run.return_value = MagicMock(returncode=0, stdout="AnchorId: anchor-1\n", stderr="")
            cli_obj._handle_rck_command('/rck anchor promote trace-1 --state-id state-1 --label "foo"')

        mock_run.assert_called_once()
        args, kwargs = mock_run.call_args
        assert args[0] == [
            "rck",
            "--workspace",
            "/home/rufus/.rck",
            "anchor",
            "promote",
            "trace-1",
            "--state-id",
            "state-1",
            "--label",
            "foo",
        ]
        assert kwargs["shell"] is False


    def test_rck_rejects_disallowed_subcommand(self):
        cli_obj = _make_cli()
        with patch("hermes_cli.rck.run_rck_subcommand") as mock_run:
            cli_obj._handle_rck_command("/rck rm -rf /")
        mock_run.assert_not_called()
        cli_obj.console.print.assert_called_once()
        assert "Unsupported RCK subcommand" in str(cli_obj.console.print.call_args)

    def test_rck_missing_binary_does_not_crash(self):
        cli_obj = _make_cli()
        with patch("hermes_cli.rck.subprocess.run", side_effect=FileNotFoundError):
            cli_obj._handle_rck_command("/rck init")
        cli_obj.console.print.assert_called_once()
        assert "RCK CLI not found" in str(cli_obj.console.print.call_args)

    def test_rck_init_requires_subcommand(self):
        cli_obj = _make_cli()
        with patch("hermes_cli.rck.run_rck_subcommand") as mock_run:
            cli_obj._handle_rck_command("/rck")
        mock_run.assert_not_called()
        cli_obj.console.print.assert_called_once()
        assert "Usage: /rck" in str(cli_obj.console.print.call_args)
