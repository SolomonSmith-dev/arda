"""Anthropic tool definitions and dispatch table for Sauron.

Each registered specialist is exposed as one Claude tool. Schemas use
the Anthropic native format: `{name, description, input_schema}`. The
dispatcher translates a tool_use block into an `AgentTask` and calls
the specialist's `BaseAgent.run` (see agents/base.py:25).
"""

from __future__ import annotations

from typing import Any, cast

from agents.base import BaseAgent
from agents.sauron.planner import Specialist
from core.models import AgentResult, AgentTask

EARENDIL_TOOL: dict[str, Any] = {
    "name": "earendil_execute",
    "description": (
        "Execute a shell command on the macOS executor (Earendil). Use for "
        "system inspection (uptime, df, free, ls), file operations, or any "
        "request that maps to a shell invocation. The `message` is forwarded "
        "as natural language to Earendil's planner."
    ),
    "input_schema": {
        "type": "object",
        "properties": {
            "message": {
                "type": "string",
                "description": "Natural-language description of the command to run.",
            }
        },
        "required": ["message"],
    },
}

FINROD_TOOL: dict[str, Any] = {
    "name": "finrod_query",
    "description": (
        "Query the Finrod knowledge base (RAG over ingested docs / memory). "
        "Use for factual questions, summarization of stored context, or "
        "recalling previously-ingested information."
    ),
    "input_schema": {
        "type": "object",
        "properties": {
            "question": {
                "type": "string",
                "description": "The user's question, verbatim.",
            }
        },
        "required": ["question"],
    },
}

TOMBOMBADIL_TOOL: dict[str, Any] = {
    "name": "tombombadil_chat",
    "description": (
        "Tom Bombadil — film club specialist. Use to log films and ratings "
        "('Film: X, Rating: Y'), discuss cinema, or chat about directors / "
        "movies. Persists film state to Redis."
    ),
    "input_schema": {
        "type": "object",
        "properties": {
            "message": {
                "type": "string",
                "description": "The film note, rating entry, or chat message.",
            }
        },
        "required": ["message"],
    },
}

# Single source of truth: specialist name -> tool schema.
# Adding a new specialist requires only a new entry here.
SPECIALIST_TOOL_MAP: dict[Specialist, dict[str, Any]] = {
    "earendil": EARENDIL_TOOL,
    "finrod": FINROD_TOOL,
    "tombombadil": TOMBOMBADIL_TOOL,
}

# Derived constants kept for backward compatibility with direct importers.
SAURON_TOOLS: list[dict[str, Any]] = list(SPECIALIST_TOOL_MAP.values())
TOOL_NAME_TO_SPECIALIST: dict[str, Specialist] = {
    schema["name"]: name for name, schema in SPECIALIST_TOOL_MAP.items()
}


class UnknownToolError(ValueError):
    pass


async def dispatch_tool(
    name: str,
    tool_input: dict[str, Any],
    specialists: dict[Specialist, BaseAgent],
    parent_task_id: str,
    tool_name_to_specialist: dict[str, str] | None = None,
) -> AgentResult:
    """Resolve a tool_use block to the corresponding specialist call."""
    _map = (
        tool_name_to_specialist if tool_name_to_specialist is not None else TOOL_NAME_TO_SPECIALIST
    )
    specialist_name = _map.get(name)
    if specialist_name is None:
        raise UnknownToolError(f"unknown tool: {name}")

    # `tool_name_to_specialist` is an open dict[str, str] so tests can inject
    # arbitrary maps; narrow before indexing the typed specialist registry.
    if specialist_name not in SPECIALIST_TOOL_MAP:
        raise UnknownToolError(f"specialist '{specialist_name}' not registered")
    agent = specialists.get(cast(Specialist, specialist_name))
    if agent is None:
        raise UnknownToolError(f"specialist '{specialist_name}' not registered")

    sub_task = AgentTask(
        agent=specialist_name,
        type="sauron_tool_dispatch",
        payload={**tool_input, "parent_task_id": parent_task_id},
    )
    return await agent.run(sub_task)
