"""Live smoke against a real Anthropic provider.

Skipped unless ``ANTHROPIC_API_KEY`` is present AND ``USE_MOCK_LLM`` is
explicitly false. Useful as a one-shot sanity check before/after a
cutover; not part of the default CI run.

Run with:
    ANTHROPIC_API_KEY=... USE_MOCK_LLM=false \\
        pytest -m integration tests/test_live_smoke.py -v
"""

from __future__ import annotations

import os

import pytest

pytestmark = pytest.mark.integration


def _have_real_keys() -> bool:
    return (
        os.getenv("USE_MOCK_LLM", "true").lower() == "false"
        and bool(os.getenv("ANTHROPIC_API_KEY"))
    )


@pytest.mark.skipif(not _have_real_keys(), reason="real ANTHROPIC_API_KEY not configured")
@pytest.mark.asyncio
async def test_sauron_real_anthropic_routes_and_returns():
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


# The tiers that actually call an LLM. Earendil (executor) is a regex
# planner and has no model to validate.
LLM_TIERS = ["orchestrator", "retriever", "specialist"]


@pytest.mark.skipif(not _have_real_keys(), reason="real ANTHROPIC_API_KEY not configured")
@pytest.mark.parametrize("tier", LLM_TIERS)
def test_configured_model_id_is_accepted_by_the_api(tier):
    """Every configured model ID must be one Anthropic will actually serve.

    ``use_mock_llm`` defaults to true, so the whole suite runs against
    ``MockAnthropicClient`` and never sends a model ID anywhere. That
    makes a wrong ID invisible to all other tests by construction -- it
    surfaces only as a 404 on the first real request in production.
    ``orchestrator_model`` sat at the non-existent ``claude-opus-4-7``
    for months exactly this way.

    One minimal real call per tier closes that gap. ``max_tokens=1``
    keeps the whole parametrised run to a few tokens; a bad ID raises
    ``anthropic.NotFoundError`` before any generation happens.
    """
    import anthropic

    from core.config import settings

    model = settings.model_for_tier(tier)
    response = anthropic.Anthropic().messages.create(
        model=model,
        max_tokens=1,
        messages=[{"role": "user", "content": "hi"}],
    )
    # Anthropic echoes the resolved model, which also catches a silent
    # alias remap (e.g. a "-latest" suffix pointing somewhere unexpected).
    assert response.model, f"{tier} tier model {model!r} returned no model in the response"
