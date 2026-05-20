"""Live smoke against real Groq + Gemini providers.

Skipped unless both API keys are present in the environment AND
USE_MOCK_LLM is explicitly false. Useful as a one-shot sanity check
before/after the Mac Mini cutover; not part of the default CI run.

Run with:
    GEMINI_API_KEY=... GROQ_API_KEY=... USE_MOCK_LLM=false \\
        pytest -m integration tests/test_live_smoke.py -v
"""

from __future__ import annotations

import os

import pytest

pytestmark = pytest.mark.integration


def _have_real_keys() -> bool:
    return (
        os.getenv("USE_MOCK_LLM", "true").lower() == "false"
        and bool(os.getenv("GEMINI_API_KEY"))
        and bool(os.getenv("GROQ_API_KEY"))
    )


@pytest.mark.skipif(not _have_real_keys(), reason="real GEMINI/GROQ keys not configured")
@pytest.mark.asyncio
async def test_sauron_real_gemini_routes_and_returns():
    from llama_index.core import MockEmbedding
    from llama_index.core.llms import MockLLM

    from agents.earendil.agent import Earendil
    from agents.finrod.agent import Finrod
    from agents.finrod.embeddings import EMBED_DIM
    from agents.sauron.agent import Sauron
    from agents.tombombadil.agent import TomBombadil
    from core.models import AgentTask, TaskStatus

    sauron = Sauron(
        specialists={
            "earendil": Earendil(),
            "finrod": Finrod(
                llm=MockLLM(max_tokens=64),
                embed_model=MockEmbedding(embed_dim=EMBED_DIM),
            ),
            "tombombadil": TomBombadil(),
        }
    )
    task = AgentTask(agent="sauron", type="execute", payload={"message": "what is ARDA"})
    result = await sauron.run(task)
    # We don't assert on content — just that the call completes and a real
    # provider returned something the orchestrator could wrap.
    assert result.status in (TaskStatus.COMPLETED, TaskStatus.QUEUED)
    assert result.error is None
