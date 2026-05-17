"""Anthropic tool definitions and dispatch table for Sauron.

Each registered specialist is exposed as one Claude tool. Schemas use
the Anthropic native format: `{name, description, input_schema}`. The
dispatcher translates a tool_use block into an `AgentTask` and calls
the specialist's `BaseAgent.run` (see agents/base.py:25).
"""

from __future__ import annotations

from typing import Any

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

SAURON_TOOLS: list[dict[str, Any]] = [EARENDIL_TOOL, FINROD_TOOL, TOMBOMBADIL_TOOL]

TOOL_NAME_TO_SPECIALIST: dict[str, Specialist] = {
    "earendil_execute": "earendil",
    "finrod_query": "finrod",
    "tombombadil_chat": "tombombadil",
}


class UnknownToolError(ValueError):
    pass


async def dispatch_tool(
    name: str,
    tool_input: dict[str, Any],
    specialists: dict[Specialist, BaseAgent],
    parent_task_id: str,
) -> AgentResult:
    """Resolve a tool_use block to the corresponding specialist call."""
    specialist_name = TOOL_NAME_TO_SPECIALIST.get(name)
    if specialist_name is None:
        raise UnknownToolError(f"unknown tool: {name}")

    agent = specialists.get(specialist_name)
    if agent is None:
        raise UnknownToolError(f"specialist '{specialist_name}' not registered")

    sub_task = AgentTask(
        agent=specialist_name,
        type="sauron_tool_dispatch",
        payload={**tool_input, "parent_task_id": parent_task_id},
    )
    return await agent.run(sub_task)
