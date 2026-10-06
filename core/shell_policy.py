"""Allowlist for Earendil shell commands.

Earendil used to run whatever string reached it, so the only thing between a
prompt-injected request and a shell was the router. This module is the second
wall. A command runs only if, after whitespace normalisation, it exactly
matches an entry in ``ALLOWED_COMMANDS``, and an allowlisted command is run
as an argv list with no shell, so metacharacters have nothing to act on.

Checked in two places: when a task is enqueued (fast refusal for the caller)
and again in the worker (so a task already sitting in Redis is still refused).

``EARENDIL_ALLOW_ANY_COMMAND=true`` restores the old behaviour for a private,
trusted deployment. It is off by default.
"""

from __future__ import annotations

import shlex
from typing import Any

from core.config import settings

ALLOWED_COMMANDS: tuple[str, ...] = (
    "uptime",
    "df -h",
    "free -m",
    "whoami",
    "pwd",
    "ls -la",
    "date",
    "hostname",
    "uname -a",
)
_MAX_ECHO = 80


class CommandNotAllowedError(ValueError):
    """The command is not in the Earendil allowlist."""


def normalize(command: str) -> str:
    return " ".join(str(command).split())


def check_command(command: str) -> str:
    """Return the normalised command, or raise if it is not allowlisted."""
    norm = normalize(command)
    if settings.earendil_allow_any_command:
        return norm
    if norm not in ALLOWED_COMMANDS:
        shown = norm[:_MAX_ECHO] + ("..." if len(norm) > _MAX_ECHO else "")
        raise CommandNotAllowedError(f"refused: command not in the Earendil allowlist: {shown!r}")
    return norm


def argv_for(command: str) -> list[str] | None:
    """argv for an allowlisted command, or None when the escape hatch is the only reason it runs."""
    norm = normalize(command)
    return shlex.split(norm) if norm in ALLOWED_COMMANDS else None


def check_task(task: dict[str, Any]) -> None:
    """Validate a queue task. Only system/run_command tasks carry a command."""
    if task.get("type") == "system" and task.get("action") == "run_command":
        check_command((task.get("payload") or {}).get("command", ""))
