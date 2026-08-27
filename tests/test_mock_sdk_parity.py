"""The mocks must not accept parameters the real Anthropic SDK rejects.

`temperature=0.2` shipped to production and broke every Tom Bombadil reply:
the SDK dropped that parameter from `Messages.create` in 1.x, but both mocks
took `**kwargs` and swallowed it, so 400+ tests passed against a call the real
client raises `TypeError` on.

A mock that accepts more than the real client cannot fail on the mismatch it
exists to model. These tests introspect the *installed* SDK, so they also catch
the SDK removing a parameter we still pass -- without needing an API key or a
network call, which is what makes them CI-safe.
"""

from __future__ import annotations

import inspect

import pytest

from agents._anthropic_mock import MockAnthropicChatClient, MockAnthropicClient


def _real_create_params() -> set[str]:
    import anthropic

    sig = inspect.signature(anthropic.AsyncAnthropic(api_key="x").messages.create)
    return set(sig.parameters)


def _mock_create_params(client) -> set[str]:
    sig = inspect.signature(client.messages.create)
    return {n for n, p in sig.parameters.items() if p.kind is not p.VAR_KEYWORD}


@pytest.mark.parametrize(
    "client",
    [MockAnthropicClient(), MockAnthropicChatClient()],
    ids=["tool_use_mock", "chat_mock"],
)
def test_mock_signature_is_a_subset_of_the_real_sdk(client):
    extra = _mock_create_params(client) - _real_create_params()
    assert not extra, (
        f"{type(client).__name__} accepts {sorted(extra)}, which the installed "
        "anthropic SDK does not. Either the SDK dropped it (stop passing it) "
        "or it was never real (remove it from the mock)."
    )


@pytest.mark.parametrize(
    "client",
    [MockAnthropicClient(), MockAnthropicChatClient()],
    ids=["tool_use_mock", "chat_mock"],
)
def test_mocks_do_not_swallow_unknown_kwargs(client):
    """`**kwargs` is what let the bad parameter through. Neither mock may have it."""
    sig = inspect.signature(client.messages.create)
    var_kw = [n for n, p in sig.parameters.items() if p.kind is p.VAR_KEYWORD]
    assert not var_kw, (
        f"{type(client).__name__}.messages.create takes {var_kw}; that hides "
        "parameter mismatches with the real SDK."
    )


def test_the_parameters_we_actually_send_still_exist_in_the_sdk():
    """Catches the reverse drift: the SDK removing something we pass."""
    real = _real_create_params()
    for name in ("model", "system", "messages", "max_tokens", "tools"):
        assert name in real, f"anthropic SDK no longer accepts {name!r}"
    # `temperature` is the one that bit us: present in 0.x, gone in 1.x. The
    # subset tests above are what actually enforce correctness; this is just a
    # breadcrumb so a future SDK restoring it is a deliberate decision rather
    # than an accident. anthropic is pinned to 1.x in pyproject precisely so
    # this stays stable between dev and prod.
    if "temperature" in real:  # pragma: no cover - depends on installed SDK
        pytest.skip(
            "installed SDK accepts `temperature` again; revisit the note in "
            "agents/tombombadil/agent.py before re-adding it"
        )
