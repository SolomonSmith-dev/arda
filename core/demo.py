"""Demo-mode guard shared by every layer that can reach a shell.

Shell execution is refused in three independent places so that no single
missed route re-opens it: the API router (api/demo.py middleware), the Earendil
agent and its enqueue helper, and the worker that drains the queue.
"""

from __future__ import annotations

from core.config import settings

REFUSAL = "refused: shell execution is disabled in demo mode"


class ShellDisabledError(RuntimeError):
    """Raised when something tries to enqueue or run a shell command in demo mode."""


def assert_shell_allowed() -> None:
    if settings.demo_mode:
        raise ShellDisabledError(REFUSAL)
