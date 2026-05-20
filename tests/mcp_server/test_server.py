"""Tests for the MCP server wiring (mcp_server/server.py).

The server's tools call the unified API at ``settings.arda_api_url``.
These tests verify the wiring: tool functions go through the configured
base URL, send the API key, hit the right paths, and project responses
into the JSON shape Claude sees. The httpx client is patched per-test
so no real network goes out.
"""

from __future__ import annotations

import json
from typing import Any

import httpx
import pytest

from core.config import settings
from mcp_server import server as mcp_server


def _route_handler(routes: dict[tuple[str, str], Any]) -> httpx.MockTransport:
    """Build a mock httpx transport that dispatches by (method, path).

    Each value is either a callable taking (httpx.Request) -> httpx.Response
    or a dict that becomes a JSON 200 response.
    """

    def handler(request: httpx.Request) -> httpx.Response:
        key = (request.method, request.url.path)
        match = routes.get(key)
        if match is None:
            return httpx.Response(404, json={"unmatched": str(request.url)})
        if callable(match):
            return match(request)
        return httpx.Response(200, json=match)

    return httpx.MockTransport(handler)


@pytest.fixture
def patched_client(monkeypatch):
    """Replace mcp_server.server.client with a function building a fresh
    httpx.Client backed by a per-test MockTransport. Tests get back a
    factory: pass a routes dict, get a client wired to it.
    """
    base_url = settings.arda_api_url
    headers = mcp_server.HEADERS

    def install(routes: dict[tuple[str, str], Any]) -> None:
        monkeypatch.setattr(
            mcp_server,
            "client",
            httpx.Client(
                base_url=base_url,
                headers=headers,
                transport=_route_handler(routes),
            ),
        )

    return install


def test_client_uses_unified_api_url():
    """The base URL must come from settings.arda_api_url -- not the
    deleted legacy earendil_host."""
    assert str(mcp_server.client.base_url).rstrip("/") == settings.arda_api_url.rstrip("/")


def test_client_sends_arda_api_key_header():
    assert mcp_server.HEADERS["x-api-key"] == settings.arda_api_key


def test_arda_execute_no_poll_returns_queued(patched_client):
    patched_client({
        ("POST", "/task"): {"status": "queued", "task_id": "abc123", "executor": "earendil_worker"},
    })
    params = mcp_server.ExecuteInput(command="uptime", poll=False)
    body = json.loads(mcp_server.arda_execute(params))
    assert body == {"status": "queued", "task_id": "abc123"}


def test_arda_execute_polls_until_completed(patched_client):
    """When poll=True the tool drives /task -> /result until completion."""
    calls = {"result": 0}

    def result_handler(_request: httpx.Request) -> httpx.Response:
        calls["result"] += 1
        if calls["result"] >= 2:
            return httpx.Response(
                200,
                json={"task_id": "t1", "status": "completed", "result": {"stdout": "ok"}, "error": None},
            )
        return httpx.Response(
            200, json={"task_id": "t1", "status": "running", "result": None, "error": None}
        )

    patched_client({
        ("POST", "/task"): {"status": "queued", "task_id": "t1", "executor": "earendil_worker"},
        ("GET", "/result/t1"): result_handler,
    })
    params = mcp_server.ExecuteInput(command="uptime", poll=True, poll_timeout=5)
    body = json.loads(mcp_server.arda_execute(params))
    assert body["status"] == "completed"
    assert body["task_id"] == "t1"
    assert body["result"] == {"stdout": "ok"}


def test_arda_execute_propagates_failed_result(patched_client):
    patched_client({
        ("POST", "/task"): {"status": "queued", "task_id": "t1", "executor": "earendil_worker"},
        ("GET", "/result/t1"): {
            "task_id": "t1", "status": "failed", "result": None, "error": "command not found",
        },
    })
    params = mcp_server.ExecuteInput(command="bogus", poll=True, poll_timeout=5)
    body = json.loads(mcp_server.arda_execute(params))
    assert body["status"] == "failed"
    assert body["error"] == "command not found"


def test_arda_query_passes_through_to_unified_api(patched_client):
    captured = {}

    def query_handler(request: httpx.Request) -> httpx.Response:
        captured["body"] = json.loads(request.content)
        return httpx.Response(200, json={"api": "online", "redis": "connected", "queue_depth": 0})

    patched_client({("POST", "/query"): query_handler})
    params = mcp_server.QueryInput(type="system", action="status")
    body = json.loads(mcp_server.arda_query(params))
    assert body["api"] == "online"
    assert captured["body"] == {"type": "system", "action": "status"}


def test_arda_plan_forwards_message(patched_client):
    patched_client({
        ("POST", "/plan"): {
            "message": "uptime",
            "intent": "earendil",
            "subtasks": [{"specialist": "earendil", "payload": {"message": "uptime"}}],
        },
    })
    params = mcp_server.PlanInput(message="uptime")
    body = json.loads(mcp_server.arda_plan(params))
    assert body["intent"] == "earendil"


def test_arda_status_combines_health_and_query(patched_client):
    patched_client({
        ("GET", "/health"): {"status": "online", "agent": "earendil", "version": "0.3.0"},
        ("POST", "/query"): {"api": "online", "redis": "connected", "queue_depth": 7},
    })
    body = json.loads(mcp_server.arda_status())
    assert body["api"] == "online"
    assert body["api_version"] == "0.3.0"
    assert body["queue_depth"] == 7


def test_arda_execute_handles_connection_error(patched_client):
    def connect_error(_request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("nope")

    patched_client({("POST", "/task"): connect_error})
    params = mcp_server.ExecuteInput(command="uptime", poll=False)
    body = json.loads(mcp_server.arda_execute(params))
    assert body["error"] == "Cannot reach ARDA API."


def test_all_four_tools_are_registered():
    """The MCP tool registry must expose all four tools by name."""
    tool_names = {t.name for t in mcp_server.mcp._tool_manager.list_tools()}
    assert tool_names == {"arda_execute", "arda_query", "arda_plan", "arda_status"}
