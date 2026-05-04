"""Hermes ↔ RCK CLI integration helpers.

This module keeps the RCK integration isolated so the main CLI can stay
rebase-friendly. It only resolves configuration, validates the allowlist,
and shells out to the external RCK CLI.
"""

from __future__ import annotations

from dataclasses import dataclass
import os
import shlex
import subprocess
from collections.abc import Mapping, Sequence
from typing import Any

ALLOWED_RCK_SUBCOMMANDS = {
    "current",
    "init",
    "status",
    "trace",
    "state",
    "anchor",
    "checkpoint",
    "inject",
}


@dataclass(frozen=True)
class RckResult:
    returncode: int
    stdout: str = ""
    stderr: str = ""


def _read_rck_section(config: Any) -> Mapping[str, Any]:
    if not config:
        return {}
    if isinstance(config, Mapping):
        section = config.get("rck") or {}
        return section if isinstance(section, Mapping) else {}
    return {}


def resolve_rck_command(config: Any = None) -> str:
    """Resolve the external RCK command name from config/env/default."""
    rck_cfg = _read_rck_section(config)
    command = str(rck_cfg.get("command") or "").strip()
    if command:
        return command

    env_cmd = os.environ.get("RCK_COMMAND", "").strip()
    if env_cmd:
        return env_cmd

    return "rck"


def resolve_rck_workspace(config: Any = None) -> str:
    """Resolve the RCK workspace path from config/env/default."""
    rck_cfg = _read_rck_section(config)
    workspace = str(rck_cfg.get("workspace") or "").strip()
    if workspace:
        return workspace

    env_workspace = os.environ.get("RCK_WORKSPACE", "").strip()
    if env_workspace:
        return env_workspace

    return "/home/rufus/.rck"


def build_rck_command(config: Any, subcommand: str, extra_args: Sequence[str] | None = None) -> list[str]:
    """Build the external RCK invocation."""
    cmd = [resolve_rck_command(config), "--workspace", resolve_rck_workspace(config), subcommand]
    if extra_args:
        cmd.extend(extra_args)
    return cmd


def run_rck_subcommand(
    config: Any,
    subcommand: str,
    extra_args: Sequence[str] | None = None,
    *,
    timeout: int = 60,
) -> RckResult | None:
    """Run an allowlisted RCK subcommand via subprocess.run."""
    if subcommand not in ALLOWED_RCK_SUBCOMMANDS:
        raise ValueError(f"Unsupported RCK subcommand: {subcommand}")

    cmd = build_rck_command(config, subcommand, extra_args)

    try:
        result = subprocess.run(
            cmd,
            shell=False,
            capture_output=True,
            text=True,
            timeout=timeout,
            check=False,
        )
    except FileNotFoundError:
        return None
    except subprocess.TimeoutExpired:
        raise

    return RckResult(
        returncode=result.returncode,
        stdout=(result.stdout or ""),
        stderr=(result.stderr or ""),
    )


def handle_rck_command(cli: Any, cmd: str) -> None:
    """Parse and execute /rck [subcommand] [args...]."""
    try:
        parts = shlex.split(cmd)
    except ValueError as exc:
        cli._console_print(f"  Invalid /rck command: {exc}")
        return

    if len(parts) < 2:
        cli._console_print("  Usage: /rck current|init|status|trace|state|anchor|checkpoint|inject")
        return

    subcommand = parts[1].lstrip("/")
    args = parts[2:]

    if subcommand == "current":
        from hermes_cli.rck_assisted import handle_rck_current

        handle_rck_current(cli, cmd)
        return
    if subcommand == "init":
        from hermes_cli.rck_assisted import handle_rck_init

        handle_rck_init(cli, cmd)
        return
    if subcommand == "state":
        if args and args[0] == "add":
            result = run_rck_subcommand(getattr(cli, "config", {}) or {}, subcommand, args)
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
            elif result.stderr:
                stderr = result.stderr.strip()
                if stderr:
                    cli._console_print(f"  RCK warning: {stderr}")
            return

        from hermes_cli.rck_assisted import handle_rck_state

        handle_rck_state(cli, cmd)
        return

    if subcommand == "anchor":
        if args and args[0] == "promote":
            result = run_rck_subcommand(getattr(cli, "config", {}) or {}, subcommand, args)
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
            elif result.stderr:
                stderr = result.stderr.strip()
                if stderr:
                    cli._console_print(f"  RCK warning: {stderr}")
            return

        from hermes_cli.rck_assisted import handle_rck_anchor

        handle_rck_anchor(cli, cmd)
        return

    if subcommand not in ALLOWED_RCK_SUBCOMMANDS:
        cli._console_print(f"  Unsupported RCK subcommand: {subcommand}")
        return

    try:
        result = run_rck_subcommand(getattr(cli, "config", {}) or {}, subcommand, args)
    except subprocess.TimeoutExpired:
        cli._console_print(f"  RCK command timed out: {' '.join(build_rck_command(getattr(cli, 'config', {}) or {}, subcommand, args))}")
        return
    except Exception as exc:
        cli._console_print(f"  Failed to run RCK: {exc}")
        return

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
    elif result.stderr:
        stderr = result.stderr.strip()
        if stderr:
            cli._console_print(f"  RCK warning: {stderr}")
