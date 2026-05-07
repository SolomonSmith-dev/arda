"""Typed state for the Sauron LangGraph orchestrator.

Each StateGraph node receives an ArdaState and returns a partial dict
that gets merged via reducers (default: replace; `messages` appends).
"""

from __future__ import annotations

import operator
from typing import Annotated, Any, TypedDict


class ArdaState(TypedDict, total=False):
    # Identity
    thread_id: str
    task_id: str

    # Inputs
    user_message: str

    # Anthropic-shaped conversation history. The `add` reducer (operator.add)
    # appends new messages emitted by each node rather than replacing.
    messages: Annotated[list[dict[str, Any]], operator.add]

    # Pending tool_use blocks emitted by the most recent agent_step.
    # Cleared by tool_dispatch after it has consumed them.
    pending_tool_uses: list[dict[str, Any]]

    # Captured for AgentResult-shape compatibility with the legacy planner.
    intent: str | None
    last_specialist_result: dict[str, Any] | None

    # Terminal text from Claude when stop_reason == "end_turn".
    final_text: str | None

    # Error string captured by any node; surfaces in the final AgentResult.
    error: str | None

    # Loop counter; the conditional edge halts when this hits MAX_ITERATIONS.
    iterations: int
