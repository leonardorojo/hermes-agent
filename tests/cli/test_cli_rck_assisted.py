"""Tests for the assisted /rck session-state seam."""

from __future__ import annotations

import json
from dataclasses import replace
from datetime import datetime
from unittest.mock import MagicMock, patch

from cli import HermesCLI
from hermes_cli.rck_assisted import (
    RCK_SESSION_META_KEY,
    RckSessionState,
    default_trace_id,
    derive_label_from_trace_id,
    format_rck_current_state,
    handle_rck_current,
    handle_rck_init,
    load_rck_session_state,
    save_rck_session_state,
)


class FakeSessionDB:
    def __init__(self, raw: str | None = None):
        self.raw = raw
        self.values: dict[str, str] = {}

    def get_meta(self, key: str):
        if key == RCK_SESSION_META_KEY:
            return self.raw
        return None

    def set_meta(self, key: str, value: str):
        if key == RCK_SESSION_META_KEY:
            self.raw = value
        self.values[key] = value


def _make_cli(config=None, session_db=None):
    cli_obj = HermesCLI.__new__(HermesCLI)
    cli_obj.config = config or {}
    cli_obj.console = MagicMock()
    cli_obj._app = None
    cli_obj._session_db = session_db
    cli_obj._rck_session_state = None
    cli_obj._console_print = MagicMock()
    return cli_obj


class TestRckStateHelpers:
    def test_derive_label_from_trace_id(self):
        assert derive_label_from_trace_id("my-feature") == "My Feature"
        assert derive_label_from_trace_id("rck-123") == "RCK 123"

    def test_default_trace_id_is_timestamp_based(self):
        now = datetime(2026, 5, 4, 8, 9, 10)
        assert default_trace_id(now) == "rck-20260504-080910"

    def test_format_current_state_renders_expected_shape(self):
        state = RckSessionState(
            workspace="/home/rufus/.rck",
            current_trace_id="my-feature",
            current_trace_label="My Feature",
            last_state_id=None,
            last_anchor_id="anchor-1",
            pending_injection=True,
        )
        output = format_rck_current_state(state)
        assert "RCK session:" in output
        assert "- workspace: /home/rufus/.rck" in output
        assert "- current_trace_id: my-feature" in output
        assert "- current_trace_label: My Feature" in output
        assert "- last_state_id: none" in output
        assert "- last_anchor_id: anchor-1" in output
        assert "- pending_injection: true" in output

    def test_load_and_save_round_trip_through_meta_store(self):
        db = FakeSessionDB()
        cli = _make_cli(session_db=db)
        state = RckSessionState(
            workspace="/home/rufus/.rck",
            current_trace_id="trace-1",
            current_trace_label="Trace 1",
            last_state_id="state-1",
            last_anchor_id=None,
            pending_injection=False,
        )

        save_rck_session_state(cli, state)
        loaded = load_rck_session_state(cli)

        assert loaded == state
        assert json.loads(db.raw) == state.to_dict()

    def test_load_uses_workspace_override_when_cache_is_empty(self):
        db = FakeSessionDB()
        cli = _make_cli(session_db=db)
        loaded = load_rck_session_state(cli, workspace="/custom/workspace")
        assert loaded.workspace == "/custom/workspace"


class TestRckCurrent:
    def test_current_with_no_state_shows_none_values(self):
        cli = _make_cli()

        handle_rck_current(cli, "/rck current")

        cli._console_print.assert_called_once()
        output = cli._console_print.call_args.args[0]
        assert "- workspace: /home/rufus/.rck" in output
        assert "- current_trace_id: none" in output
        assert "- current_trace_label: none" in output
        assert "- last_state_id: none" in output
        assert "- last_anchor_id: none" in output
        assert "- pending_injection: false" in output

    def test_current_reads_persisted_state(self):
        db = FakeSessionDB(
            raw=json.dumps(
                {
                    "workspace": "/tmp/rck",
                    "current_trace_id": "my-feature",
                    "current_trace_label": "My Feature",
                    "last_state_id": "state-1",
                    "last_anchor_id": "anchor-1",
                    "pending_injection": True,
                }
            )
        )
        cli = _make_cli(session_db=db)

        handle_rck_current(cli, "/rck current")

        output = cli._console_print.call_args.args[0]
        assert "- workspace: /tmp/rck" in output
        assert "- current_trace_id: my-feature" in output
        assert "- current_trace_label: My Feature" in output
        assert "- last_state_id: state-1" in output
        assert "- last_anchor_id: anchor-1" in output
        assert "- pending_injection: true" in output


class TestRckInit:
    def test_init_with_trace_id_and_label_calls_trace_start(self):
        db = FakeSessionDB()
        cli = _make_cli({"rck": {"workspace": "/home/rufus/.rck"}}, db)

        with patch("hermes_cli.rck_assisted.run_rck_subcommand") as mock_run:
            mock_run.return_value = MagicMock(returncode=0, stdout="", stderr="")
            handle_rck_init(cli, '/rck init my-feature --label "My Feature"', now_provider=lambda: datetime(2026, 5, 4, 8, 9, 10))

        mock_run.assert_called_once()
        args, kwargs = mock_run.call_args
        assert args[1] == "trace"
        assert args[2] == ["start", "my-feature", "--label", "My Feature"]
        assert kwargs == {}
        saved = json.loads(db.raw)
        assert saved["current_trace_id"] == "my-feature"
        assert saved["current_trace_label"] == "My Feature"
        assert saved["workspace"] == "/home/rufus/.rck"

    def test_init_derives_label_from_trace_id(self):
        db = FakeSessionDB()
        cli = _make_cli({}, db)

        with patch("hermes_cli.rck_assisted.run_rck_subcommand") as mock_run:
            mock_run.return_value = MagicMock(returncode=0, stdout="", stderr="")
            handle_rck_init(cli, "/rck init my-feature", now_provider=lambda: datetime(2026, 5, 4, 8, 9, 10))

        args, _kwargs = mock_run.call_args
        assert args[2] == ["start", "my-feature", "--label", "My Feature"]
        assert json.loads(db.raw)["current_trace_label"] == "My Feature"

    def test_init_without_args_uses_deterministic_defaults(self):
        db = FakeSessionDB()
        cli = _make_cli({}, db)
        fixed_now = datetime(2026, 5, 4, 8, 9, 10)

        with patch("hermes_cli.rck_assisted.run_rck_subcommand") as mock_run:
            mock_run.return_value = MagicMock(returncode=0, stdout="", stderr="")
            handle_rck_init(cli, "/rck init", now_provider=lambda: fixed_now)

        args, _kwargs = mock_run.call_args
        assert args[2][0:2] == ["start", "rck-20260504-080910"]
        assert args[2][2:] == ["--label", "RCK session 2026-05-04 08:09"]
        saved = json.loads(db.raw)
        assert saved["current_trace_id"] == "rck-20260504-080910"
        assert saved["current_trace_label"] == "RCK session 2026-05-04 08:09"

    def test_init_missing_binary_does_not_persist_state(self):
        db = FakeSessionDB()
        cli = _make_cli({}, db)

        with patch("hermes_cli.rck_assisted.resolve_rck_command", return_value="rck"), patch(
            "hermes_cli.rck_assisted.run_rck_subcommand", return_value=None
        ) as mock_run:
            handle_rck_init(cli, "/rck init my-feature", now_provider=lambda: datetime(2026, 5, 4, 8, 9, 10))

        mock_run.assert_called_once()
        assert db.raw is None
        cli._console_print.assert_called_once()
        assert "RCK CLI not found" in str(cli._console_print.call_args)


class TestRckCliDispatcher:
    def test_cli_dispatches_current_to_assisted_path(self):
        cli = _make_cli()
        with patch("hermes_cli.rck_assisted.handle_rck_current") as mock_current, patch(
            "hermes_cli.rck.handle_rck_command"
        ) as mock_passthrough:
            cli._handle_rck_command("/rck current")

        mock_current.assert_called_once()
        mock_passthrough.assert_not_called()

    def test_cli_dispatches_init_to_assisted_path(self):
        cli = _make_cli()
        with patch("hermes_cli.rck_assisted.handle_rck_init") as mock_init, patch(
            "hermes_cli.rck.handle_rck_command"
        ) as mock_passthrough:
            cli._handle_rck_command("/rck init my-feature")

        mock_init.assert_called_once()
        mock_passthrough.assert_not_called()

    def test_passthrough_trace_list_still_uses_subprocess(self):
        cli = _make_cli({"rck": {"command": "rck", "workspace": "/home/rufus/.rck"}})
        with patch("hermes_cli.rck.subprocess.run") as mock_run:
            mock_run.return_value = MagicMock(returncode=0, stdout="trace list\n", stderr="")
            cli._handle_rck_command("/rck trace list")

        mock_run.assert_called_once()
        args, kwargs = mock_run.call_args
        assert args[0] == ["rck", "--workspace", "/home/rufus/.rck", "trace", "list"]
        assert kwargs["shell"] is False

    def test_invalid_subcommand_still_rejected(self):
        cli = _make_cli()
        with patch("hermes_cli.rck.run_rck_subcommand") as mock_run:
            cli._handle_rck_command("/rck rm -rf /")

        mock_run.assert_not_called()
        cli._console_print.assert_called_once()
        assert "Unsupported RCK subcommand" in str(cli._console_print.call_args)
