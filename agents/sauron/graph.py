"""Sauron LangGraph orchestrator.

Two nodes: `agent_step` calls Claude with the registered tools and
captures any `tool_use` blocks; `tool_dispatch` resolves each tool_use
into a specialist call (BaseAgent.run) and feeds the AgentResult back
as a `tool_result` content block. The conditional edge loops until
Claude stops emitting tool_use (or MAX_ITERATIONS is hit).
"""

from __future__ import annotations

import json
from typing import Any

from langgraph.checkpoint.base import BaseCheckpointSaver
from langgraph.graph import END, START, StateGraph

from agents.base import BaseAgent
from agents.sauron.planner import Specialist
from agents.sauron.state import ArdaState
from agents.sauron.tools import (
    SPECIALIST_TOOL_MAP,
    UnknownToolError,
    dispatch_tool,
)
from core.logging import get_logger

log = get_logger("agents.sauron.graph")

MAX_ITERATIONS = 6


def _build_system_prompt(tools: list[dict[str, Any]]) -> str:
    lines = "\n".join(f"  - {t['name']}: {t['description'][:80].rstrip()}" for t in tools)
    count = len(tools)
    return (
        "You are Sauron, the orchestrator of the ARDA multi-agent system. "
        f"You have {count} specialist tool{'s' if count != 1 else ''}:\n"
        f"{lines}\n"
        "Choose exactly one tool that matches the user's intent. After you "
        "receive the tool_result, write a brief one-sentence summary and stop."
    )


def _block_get(block: Any, key: str, default: Any = None) -> Any:
    """Read a field from either an Anthropic SDK block (attr) or a dict."""
    if isinstance(block, dict):
        return block.get(key, default)
    return getattr(block, key, default)


def build_sauron_graph(
    *,
    specialists: dict[Specialist, BaseAgent],
    client: Any,
    checkpointer: BaseCheckpointSaver,
    model: str,
    max_iterations: int = MAX_ITERATIONS,
) -> Any:
    _tools: list[dict[str, Any]] = [
        SPECIALIST_TOOL_MAP[name] for name in specialists if name in SPECIALIST_TOOL_MAP
    ]
    _tool_map: dict[str, str] = {
        SPECIALIST_TOOL_MAP[name]["name"]: name
        for name in specialists
        if name in SPECIALIST_TOOL_MAP
    }
    _system_prompt = _build_system_prompt(_tools)

    async def agent_step(state: ArdaState) -> dict[str, Any]:
        try:
            response = await client.messages.create(
                model=model,
                system=_system_prompt,
                tools=_tools,
                messages=state["messages"],
                max_tokens=1024,
            )
        except Exception as e:  # noqa: BLE001
            log.error("sauron_anthropic_call_failed", task_id=state.get("task_id"), exception=str(e))
            return {
                "error": f"anthropic_call_failed: {e}",
                "iterations": state.get("iterations", 0) + 1,
            }

        assistant_content: list[dict[str, Any]] = []
        pending_tool_uses: list[dict[str, Any]] = []
        final_text: str | None = state.get("final_text")

        for block in response.content:
            btype = _block_get(block, "type")
            if btype == "tool_use":
                tu = {
                    "type": "tool_use",
                    "id": _block_get(block, "id"),
                    "name": _block_get(block, "name"),
                    "input": _block_get(block, "input") or {},
                }
                assistant_content.append(tu)
                pending_tool_uses.append(tu)
            elif btype == "text":
                text = _block_get(block, "text", "")
                assistant_content.append({"type": "text", "text": text})
                final_text = text

        log.info(
            "sauron_agent_step",
            task_id=state.get("task_id"),
            stop_reason=getattr(response, "stop_reason", None),
            tool_uses=len(pending_tool_uses),
            iteration=state.get("iterations", 0) + 1,
        )

        return {
            "messages": [{"role": "assistant", "content": assistant_content}],
            "pending_tool_uses": pending_tool_uses,
            "final_text": final_text,
            "iterations": state.get("iterations", 0) + 1,
        }

    async def tool_dispatch(state: ArdaState) -> dict[str, Any]:
        pending = state.get("pending_tool_uses", [])
        tool_result_blocks: list[dict[str, Any]] = []
        intent = state.get("intent")
        last_result = state.get("last_specialist_result")
        error: str | None = None

        for tu in pending:
            try:
                result = await dispatch_tool(
                    name=tu["name"],
                    tool_input=tu["input"],
                    specialists=specialists,
                    parent_task_id=state.get("task_id", ""),
                    tool_name_to_specialist=_tool_map,
                )
                if intent is None:
                    intent = _tool_map.get(tu["name"])
                last_result = result.model_dump(mode="json")
                tool_result_blocks.append({
                    "type": "tool_result",
                    "tool_use_id": tu["id"],
                    "content": json.dumps(last_result),
                })
                log.info(
                    "sauron_tool_dispatch",
                    task_id=state.get("task_id"),
                    tool=tu["name"],
                    specialist_status=last_result.get("status"),
                )
            except UnknownToolError as e:
                error = str(e)
                tool_result_blocks.append({
                    "type": "tool_result",
                    "tool_use_id": tu["id"],
                    "content": f"error: {e}",
                    "is_error": True,
                })
            except Exception as e:  # noqa: BLE001
                error = str(e)
                tool_result_blocks.append({
                    "type": "tool_result",
                    "tool_use_id": tu["id"],
                    "content": f"error: {e}",
                    "is_error": True,
                })

        return {
            "messages": [{"role": "user", "content": tool_result_blocks}],
            "pending_tool_uses": [],
            "intent": intent,
            "last_specialist_result": last_result,
            "error": error,
        }

    def route_after_agent(state: ArdaState) -> str:
        if state.get("error"):
            return END
        if state.get("pending_tool_uses") and state.get("iterations", 0) < max_iterations:
            return "tool_dispatch"
        return END

    builder: StateGraph = StateGraph(ArdaState)
    builder.add_node("agent_step", agent_step)
    builder.add_node("tool_dispatch", tool_dispatch)
    builder.add_edge(START, "agent_step")
    builder.add_conditional_edges(
        "agent_step",
        route_after_agent,
        {"tool_dispatch": "tool_dispatch", END: END},
    )
    builder.add_edge("tool_dispatch", "agent_step")

    return builder.compile(checkpointer=checkpointer)
