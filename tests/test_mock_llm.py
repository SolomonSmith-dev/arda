from __future__ import annotations

import pytest

from agents._mock_llm import MockLLM


def test_invoke_returns_content_attribute():
    llm = MockLLM(model="test-model")
    resp = llm.invoke("hello world")
    assert hasattr(resp, "content")
    assert resp.content.startswith("[mock:test-model]")
    assert "hello world" in resp.content


def test_invoke_truncates_long_prompts():
    llm = MockLLM()
    long = "x" * 500
    resp = llm.invoke(long)
    assert len(resp.content) < 200


def test_invoke_handles_non_string_prompt():
    llm = MockLLM()
    resp = llm.invoke(["msg1", "msg2"])
    assert "msg1" in resp.content


@pytest.mark.asyncio
async def test_ainvoke_matches_invoke():
    llm = MockLLM(model="m")
    sync = llm.invoke("ping")
    async_ = await llm.ainvoke("ping")
    assert sync.content == async_.content
