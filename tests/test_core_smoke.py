from __future__ import annotations

import json

import pytest

from agents.base import BaseAgent
from core.config import Settings, settings
from core.models import AgentResult, AgentTask, HealthStatus, TaskStatus
from core.redis_client import RESULT_TTL_SECONDS, TASK_QUEUE_KEY, task_result_key


def test_settings_load():
    assert isinstance(settings.use_mock_llm, bool)
    assert settings.redis_port == 6379
    assert settings.milvus_port == 19530


def test_model_router_default():
    s = Settings(use_mock_llm=False)
    assert "claude" in s.model_for_tier("orchestrator")
    assert "llama" in s.model_for_tier("executor")
    assert s.provider_for_tier("orchestrator") == "anthropic"
    assert s.provider_for_tier("executor") == "groq"


def test_provider_mock_when_use_mock_llm_true():
    s = Settings(use_mock_llm=True)
    for tier in ("orchestrator", "executor", "retriever", "specialist"):
        assert s.provider_for_tier(tier) == "mock"


def test_redis_keys():
    assert TASK_QUEUE_KEY == "task_queue"
    assert RESULT_TTL_SECONDS == 300
    assert task_result_key("abc") == "task:abc"


def test_models_construct_and_roundtrip():
    task = AgentTask(agent="earendil", type="system", payload={"cmd": "uptime"})
    assert task.task_id
    raw = task.model_dump_json()
    parsed = json.loads(raw)
    assert parsed["agent"] == "earendil"

    result = AgentResult(
        task_id=task.task_id,
        agent="earendil",
        status=TaskStatus.COMPLETED,
        result={"stdout": "ok"},
        duration_ms=42,
    )
    assert result.status == TaskStatus.COMPLETED
    assert result.error is None


def test_health_status_construct():
    h = HealthStatus(agent="sauron", status="healthy", model="gemini-2.5-flash", provider="google")
    assert h.latency_ms is None


@pytest.mark.asyncio
async def test_base_agent_subclass_runs():
    class Dummy(BaseAgent):
        tier = "executor"
        name = "dummy"

        async def run(self, task: AgentTask) -> AgentResult:
            return AgentResult(
                task_id=task.task_id,
                agent=self.name,
                status=TaskStatus.COMPLETED,
                result={"echo": task.payload},
            )

    d = Dummy()
    task = AgentTask(agent="dummy", type="test", payload={"x": 1})
    result = await d.run(task)
    assert result.status == TaskStatus.COMPLETED
    assert result.result == {"echo": {"x": 1}}

    health = await d.health()
    assert health.agent == "dummy"
    assert "llama" in health.model


def test_logger_smokes():
    from core.logging import get_logger, new_trace_id

    new_trace_id()
    log = get_logger("smoke")
    log.info("hello", x=1)
