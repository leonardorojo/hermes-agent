"""Assisted session-state helpers for the Hermes ↔ RCK CLI seam.

This module keeps the lightweight operational pointers for `/rck current`
and `/rck init` separate from the passthrough wrapper in `hermes_cli.rck`.
The source of truth for traces still lives in the external RCK CLI.
"""

from __future__ import annotations

import json
import re
import shlex
from dataclasses import dataclass, replace
from datetime import datetime
from typing import Any, Callable, Mapping

from hermes_cli.rck import resolve_rck_command, resolve_rck_workspace, run_rck_subcommand

RCK_SESSION_META_KEY = "rck_session"


@dataclass(frozen=True)
class RckSessionState:
    workspace: str
    current_trace_id: str | None = None
    current_trace_label: str | None = None
    last_state_id: str | None = None
    last_anchor_id: str | None = None
    pending_injection: bool = False

    @classmethod
    def from_raw(cls, raw: Any, workspace: str) -> "RckSessionState":
        """Build state from a meta-store payload or fall back to defaults."""
        payload: Mapping[str, Any] | None = None
        if isinstance(raw, Mapping):
            payload = raw
        elif isinstance(raw, str) and raw.strip():
            try:
                decoded = json.loads(raw)
            except Exception:
                decoded = None
            if isinstance(decoded, Mapping):
                payload = decoded

        if not payload:
            return cls(workspace=workspace)

        return cls(
            workspace=str(payload.get("workspace") or workspace),
            current_trace_id=_empty_to_none(payload.get("current_trace_id")),
            current_trace_label=_empty_to_none(payload.get("current_trace_label")),
            last_state_id=_empty_to_none(payload.get("last_state_id")),
            last_anchor_id=_empty_to_none(payload.get("last_anchor_id")),
            pending_injection=bool(payload.get("pending_injection", False)),
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "workspace": self.workspace,
            "current_trace_id": self.current_trace_id,
            "current_trace_label": self.current_trace_label,
            "last_state_id": self.last_state_id,
            "last_anchor_id": self.last_anchor_id,
            "pending_injection": self.pending_injection,
        }


def _empty_to_none(value: Any) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    return text or None


def _format_optional(value: Any) -> str:
    text = _empty_to_none(value)
    return text if text is not None else "none"


def _state_db(cli: Any) -> Any:
    return getattr(cli, "_session_db", None)


def load_rck_session_state(cli: Any, workspace: str | None = None) -> RckSessionState:
    """Load the operational RCK state from cache or the session meta store."""
    resolved_workspace = workspace or resolve_rck_workspace(getattr(cli, "config", {}) or {})
    cached_state = getattr(cli, "_rck_session_state", None)
    if isinstance(cached_state, RckSessionState):
        if workspace is not None and cached_state.workspace != resolved_workspace:
            cached_state = replace(cached_state, workspace=resolved_workspace)
            setattr(cli, "_rck_session_state", cached_state)
        return cached_state

    session_db = _state_db(cli)
    raw = None
    if session_db is not None:
        try:
            raw = session_db.get_meta(RCK_SESSION_META_KEY)
        except Exception:
            raw = None

    state = RckSessionState.from_raw(raw, workspace=resolved_workspace)
    if workspace is not None and state.workspace != resolved_workspace:
        state = replace(state, workspace=resolved_workspace)

    setattr(cli, "_rck_session_state", state)
    return state


def save_rck_session_state(cli: Any, state: RckSessionState) -> None:
    """Persist the operational RCK state when the session store is available."""
    setattr(cli, "_rck_session_state", state)
    session_db = _state_db(cli)
    if session_db is None:
        return
    try:
        session_db.set_meta(RCK_SESSION_META_KEY, json.dumps(state.to_dict(), ensure_ascii=False, sort_keys=True))
    except Exception:
        # Best-effort only: operational state should never block the CLI.
        return


def derive_label_from_trace_id(trace_id: str) -> str:
    """Turn a trace identifier into a user-facing label."""
    cleaned = re.sub(r"[^A-Za-z0-9]+", " ", trace_id or "").strip()
    if not cleaned:
        return "RCK session"

    words: list[str] = []
    for token in cleaned.split():
        lower = token.lower()
        if lower == "rck":
            words.append("RCK")
        elif token.isdigit():
            words.append(token)
        elif token.isupper() and len(token) <= 6:
            words.append(token)
        else:
            words.append(token[:1].upper() + token[1:].lower())
    return " ".join(words)


def default_trace_id(now: datetime | None = None) -> str:
    now = now or datetime.now()
    return f"rck-{now:%Y%m%d-%H%M%S}"


def default_trace_label(now: datetime | None = None) -> str:
    now = now or datetime.now()
    return f"RCK session {now:%Y-%m-%d %H:%M}"


def format_rck_current_state(state: RckSessionState) -> str:
    lines = [
        "RCK session:",
        f"- workspace: {state.workspace}",
        f"- current_trace_id: {_format_optional(state.current_trace_id)}",
        f"- current_trace_label: {_format_optional(state.current_trace_label)}",
        f"- last_state_id: {_format_optional(state.last_state_id)}",
        f"- last_anchor_id: {_format_optional(state.last_anchor_id)}",
        f"- pending_injection: {'true' if state.pending_injection else 'false'}",
    ]
    return "\n".join(lines)


def handle_rck_current(cli: Any, cmd_original: str) -> None:
    """Handle `/rck current` without invoking the external RCK CLI."""
    del cmd_original  # Explicitly unused; current is state-only.
    state = load_rck_session_state(cli)
    cli._console_print(format_rck_current_state(state))


def _parse_init_command(cmd_original: str) -> tuple[str | None, str | None]:
    parts = shlex.split(cmd_original)
    tokens = parts[2:]
    trace_id: str | None = None
    label: str | None = None
    extras: list[str] = []
    i = 0
    while i < len(tokens):
        token = tokens[i]
        if token in {"--label", "-l"}:
            if i + 1 >= len(tokens):
                raise ValueError("Missing value for --label")
            label = tokens[i + 1]
            i += 2
            continue
        if token.startswith("-"):
            extras.append(token)
            i += 1
            continue
        if trace_id is None:
            trace_id = token
        else:
            extras.append(token)
        i += 1

    if extras:
        raise ValueError(f"Unsupported /rck init arguments: {' '.join(extras)}")
    return trace_id, label


def _parse_state_id(stdout: str) -> str | None:
    match = re.search(r"(?im)^\s*StateId:\s*(\S+)\s*$", stdout or "")
    if not match:
        return None
    return match.group(1).strip() or None


def _parse_anchor_id(stdout: str) -> str | None:
    match = re.search(r"(?im)^\s*AnchorId:\s*(\S+)\s*$", stdout or "")
    if not match:
        return None
    return match.group(1).strip() or None


def _parse_anchor_command(cmd_original: str) -> tuple[bool, str | None]:
    parts = shlex.split(cmd_original)
    tokens = parts[2:]
    if tokens and tokens[0] == "promote":
        return True, None
    label = " ".join(tokens).strip()
    return False, label or None


def _default_anchor_label() -> str:
    return "Assisted RCK anchor"


def _parse_state_command(cmd_original: str) -> tuple[bool, str | None]:
    parts = shlex.split(cmd_original)
    tokens = parts[2:]
    if tokens and tokens[0] == "add":
        return True, None
    summary = " ".join(tokens).strip()
    return False, summary or None


def _default_state_summary(state: RckSessionState) -> str:
    return "Assisted RCK state"



def _parse_checkpoint_command(cmd_original: str) -> tuple[bool, str | None]:
    parts = shlex.split(cmd_original)
    tokens = parts[2:]
    if tokens and tokens[0] == "add":
        return True, None
    summary = " ".join(tokens).strip()
    return False, summary or None



def _default_checkpoint_summary() -> str:
    return "Checkpoint captured from Hermes assisted /rck checkpoint command."



def _parse_checkpoint_ids(stdout: str) -> tuple[str | None, str | None]:
    state_id = _parse_state_id(stdout)
    anchor_id = _parse_anchor_id(stdout)
    return state_id, anchor_id



def handle_rck_state(cli: Any, cmd_original: str) -> None:
    """Handle `/rck state` as an assisted state snapshot helper."""
    try:
        parts = shlex.split(cmd_original)
        is_passthrough, summary = _parse_state_command(cmd_original)
    except ValueError as exc:
        cli._console_print(f"Invalid /rck command: {exc}")
        return

    if is_passthrough:
        result = run_rck_subcommand(getattr(cli, "config", {}) or {}, "state", parts[2:])
        if result is None:
            cli._console_print(f"RCK CLI not found: {resolve_rck_command(getattr(cli, 'config', {}) or {})}")
            return
        if result.stdout:
            cli._console_print(result.stdout.rstrip())
        if result.returncode != 0:
            stderr = (result.stderr or "").strip()
            if stderr:
                cli._console_print(f"RCK error: {stderr}")
            cli._console_print(f"RCK exited with code {result.returncode}")
        elif result.stderr:
            stderr = result.stderr.strip()
            if stderr:
                cli._console_print(f"RCK warning: {stderr}")
        return

    state = load_rck_session_state(cli)
    if not state.current_trace_id:
        cli._console_print("No active RCK trace. Run /rck init first.")
        return

    effective_summary = summary or _default_state_summary(state)
    result = run_rck_subcommand(
        getattr(cli, "config", {}) or {},
        "state",
        [
            "add",
            state.current_trace_id,
            "--title",
            "Assisted RCK state",
            "--kind",
            "rck.state",
            "--summary",
            effective_summary,
        ],
    )
    if result is None:
        cli._console_print(f"RCK CLI not found: {resolve_rck_command(getattr(cli, 'config', {}) or {})}")
        return

    if result.stdout:
        cli._console_print(result.stdout.rstrip())
    if result.returncode != 0:
        stderr = (result.stderr or "").strip()
        if stderr:
            cli._console_print(f"RCK error: {stderr}")
        cli._console_print(f"RCK exited with code {result.returncode}")
        return
    elif result.stderr:
        stderr = result.stderr.strip()
        if stderr:
            cli._console_print(f"RCK warning: {stderr}")

    state_id = _parse_state_id(result.stdout or "")
    if not state_id:
        cli._console_print("Warning: RCK state output did not include StateId.")
        return

    updated = replace(state, last_state_id=state_id)
    save_rck_session_state(cli, updated)
    if not result.stdout:
        cli._console_print(f"RCK state recorded: {state_id}")


def handle_rck_anchor(cli: Any, cmd_original: str) -> None:
    """Handle `/rck anchor` as an assisted anchor-promotion helper."""
    try:
        parts = shlex.split(cmd_original)
        is_passthrough, label = _parse_anchor_command(cmd_original)
    except ValueError as exc:
        cli._console_print(f"Invalid /rck command: {exc}")
        return

    if is_passthrough:
        result = run_rck_subcommand(getattr(cli, "config", {}) or {}, "anchor", parts[2:])
        if result is None:
            cli._console_print(f"RCK CLI not found: {resolve_rck_command(getattr(cli, 'config', {}) or {})}")
            return
        if result.stdout:
            cli._console_print(result.stdout.rstrip())
        if result.returncode != 0:
            stderr = (result.stderr or "").strip()
            if stderr:
                cli._console_print(f"RCK error: {stderr}")
            cli._console_print(f"RCK exited with code {result.returncode}")
        elif result.stderr:
            stderr = result.stderr.strip()
            if stderr:
                cli._console_print(f"RCK warning: {stderr}")
        return

    state = load_rck_session_state(cli)
    if not state.current_trace_id:
        cli._console_print("No active RCK trace. Run /rck init first.")
        return
    if not state.last_state_id:
        cli._console_print("No RCK state available. Run /rck state first.")
        return

    effective_label = label or _default_anchor_label()
    result = run_rck_subcommand(
        getattr(cli, "config", {}) or {},
        "anchor",
        [
            "promote",
            state.current_trace_id,
            "--state-id",
            state.last_state_id,
            "--label",
            effective_label,
        ],
    )
    if result is None:
        cli._console_print(f"RCK CLI not found: {resolve_rck_command(getattr(cli, 'config', {}) or {})}")
        return

    if result.stdout:
        cli._console_print(result.stdout.rstrip())
    if result.returncode != 0:
        stderr = (result.stderr or "").strip()
        if stderr:
            cli._console_print(f"RCK error: {stderr}")
        cli._console_print(f"RCK exited with code {result.returncode}")
        return
    elif result.stderr:
        stderr = result.stderr.strip()
        if stderr:
            cli._console_print(f"RCK warning: {stderr}")

    anchor_id = _parse_anchor_id(result.stdout or "")
    if not anchor_id:
        cli._console_print("Warning: RCK anchor output did not include AnchorId.")
        return

    updated = replace(state, last_anchor_id=anchor_id)
    save_rck_session_state(cli, updated)
    if not result.stdout:
        cli._console_print(f"RCK anchor recorded: {anchor_id}")



def handle_rck_checkpoint(cli: Any, cmd_original: str) -> None:
    """Handle `/rck checkpoint` as an assisted checkpoint capture helper."""
    try:
        parts = shlex.split(cmd_original)
        is_passthrough, summary = _parse_checkpoint_command(cmd_original)
    except ValueError as exc:
        cli._console_print(f"Invalid /rck command: {exc}")
        return

    if is_passthrough:
        result = run_rck_subcommand(getattr(cli, "config", {}) or {}, "checkpoint", parts[2:])
        if result is None:
            cli._console_print(f"RCK CLI not found: {resolve_rck_command(getattr(cli, 'config', {}) or {})}")
            return
        if result.stdout:
            cli._console_print(result.stdout.rstrip())
        if result.returncode != 0:
            stderr = (result.stderr or "").strip()
            if stderr:
                cli._console_print(f"RCK error: {stderr}")
            cli._console_print(f"RCK exited with code {result.returncode}")
        elif result.stderr:
            stderr = result.stderr.strip()
            if stderr:
                cli._console_print(f"RCK warning: {stderr}")
        return

    state = load_rck_session_state(cli)
    if not state.current_trace_id:
        cli._console_print("No active RCK trace. Run /rck init first.")
        return

    effective_summary = summary or _default_checkpoint_summary()
    result = run_rck_subcommand(
        getattr(cli, "config", {}) or {},
        "checkpoint",
        [
            "add",
            state.current_trace_id,
            "--title",
            "Assisted RCK checkpoint",
            "--kind",
            "rck.checkpoint",
            "--summary",
            effective_summary,
        ],
    )
    if result is None:
        cli._console_print(f"RCK CLI not found: {resolve_rck_command(getattr(cli, 'config', {}) or {})}")
        return

    if result.stdout:
        cli._console_print(result.stdout.rstrip())
    if result.returncode != 0:
        stderr = (result.stderr or "").strip()
        if stderr:
            cli._console_print(f"RCK error: {stderr}")
        cli._console_print(f"RCK exited with code {result.returncode}")
        return
    elif result.stderr:
        stderr = result.stderr.strip()
        if stderr:
            cli._console_print(f"RCK warning: {stderr}")

    state_id, anchor_id = _parse_checkpoint_ids(result.stdout or "")
    if not state_id and not anchor_id:
        cli._console_print("Warning: RCK checkpoint output did not include StateId or AnchorId.")
        return

    updated = state
    if state_id:
        updated = replace(updated, last_state_id=state_id)
    if anchor_id:
        updated = replace(updated, last_anchor_id=anchor_id)
    save_rck_session_state(cli, updated)

    if state_id and anchor_id:
        cli._console_print(f"RCK checkpoint recorded: StateId={state_id} AnchorId={anchor_id}")
    elif state_id:
        cli._console_print("Warning: RCK checkpoint output did not include AnchorId.")
    elif anchor_id:
        cli._console_print("Warning: RCK checkpoint output did not include StateId.")



def handle_rck_init(
    cli: Any,
    cmd_original: str,
    *,
    now_provider: Callable[[], datetime] | None = None,
) -> None:
    """Handle `/rck init` as a deterministic assisted trace-start helper."""
    try:
        trace_id, label = _parse_init_command(cmd_original)
    except ValueError as exc:
        cli._console_print(f"  {exc}")
        cli._console_print("  Usage: /rck init [trace-id] [--label <label>]")
        return

    now = now_provider() if now_provider else datetime.now()
    if trace_id is None:
        trace_id = default_trace_id(now)
    if label is None:
        label = derive_label_from_trace_id(trace_id) if cmd_original.strip() != "/rck init" else default_trace_label(now)
        if cmd_original.strip() == "/rck init" and not label:
            label = default_trace_label(now)

    result = run_rck_subcommand(
        getattr(cli, "config", {}) or {},
        "trace",
        ["start", trace_id, "--label", label],
    )
    if result is None:
        cli._console_print(f"  RCK CLI not found: {resolve_rck_command(getattr(cli, 'config', {}) or {})}")
        return

    if result.stdout:
        cli._console_print(result.stdout.rstrip())
    if result.returncode != 0:
        stderr = (result.stderr or "").strip()
        if stderr:
            cli._console_print(f"  RCK error: {stderr}")
        cli._console_print(f"  RCK exited with code {result.returncode}")
        return
    elif result.stderr:
        stderr = result.stderr.strip()
        if stderr:
            cli._console_print(f"  RCK warning: {stderr}")

    workspace = resolve_rck_workspace(getattr(cli, "config", {}) or {})
    state = load_rck_session_state(cli, workspace=workspace)
    updated = replace(
        state,
        workspace=workspace,
        current_trace_id=trace_id,
        current_trace_label=label,
        pending_injection=False,
    )
    save_rck_session_state(cli, updated)
    if not result.stdout:
        cli._console_print(f"  RCK trace started: {trace_id} ({label})")
