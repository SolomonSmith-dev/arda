from __future__ import annotations

import fakeredis
import pytest

from agents.tombombadil import memory
from agents.tombombadil.identity import Tier, Viewer


@pytest.fixture
def r():
    return fakeredis.FakeRedis(decode_responses=True)


def _viewer(canonical: str | None = "Brian", discord_id: str = "222", tier: Tier = Tier.REGULAR) -> Viewer:
    return Viewer(
        discord_id=discord_id,
        discord_name=canonical or "Stranger",
        canonical_name=canonical,
        tier=tier,
    )


def test_append_then_recent_roundtrip(r):
    v = _viewer()
    memory.append_turn(r, "tom:hist:ch:42", v, "user", "hello")
    memory.append_turn(r, "tom:hist:ch:42", v, "assistant", "hi back")

    turns = memory.recent_turns(r, "tom:hist:ch:42")
    assert [t.role for t in turns] == ["user", "assistant"]
    assert [t.content for t in turns] == ["hello", "hi back"]
    assert all(t.viewer == "Brian" for t in turns)


def test_append_respects_max_turns_cap(r):
    v = _viewer()
    scope = "tom:hist:ch:cap"
    cap = memory.HISTORY_MAX_TURNS * 2
    for i in range(cap + 5):
        memory.append_turn(r, scope, v, "user" if i % 2 == 0 else "assistant", f"msg {i}")
    turns = memory.recent_turns(r, scope, limit=cap)
    assert len(turns) == cap
    assert turns[0].content == "msg 5"  # first 5 trimmed off


def test_append_refreshes_ttl(r):
    v = _viewer()
    memory.append_turn(r, "tom:hist:ch:ttl", v, "user", "first")
    ttl_before = r.ttl("tom:hist:ch:ttl")
    assert 0 < ttl_before <= memory.HISTORY_TTL_SECONDS

    # Backdate the key to force a TTL change on next push.
    r.expire("tom:hist:ch:ttl", 10)
    memory.append_turn(r, "tom:hist:ch:ttl", v, "user", "second")
    ttl_after = r.ttl("tom:hist:ch:ttl")
    assert ttl_after > 100


def test_dm_and_channel_scopes_isolated(r):
    v = _viewer()
    memory.append_turn(r, "tom:hist:ch:club", v, "user", "channel-msg")
    memory.append_turn(r, "tom:hist:dm:222", v, "user", "dm-msg")

    ch = memory.recent_turns(r, "tom:hist:ch:club")
    dm = memory.recent_turns(r, "tom:hist:dm:222")
    assert [t.content for t in ch] == ["channel-msg"]
    assert [t.content for t in dm] == ["dm-msg"]


def test_history_scope_key_picks_dm_namespace():
    class _Channel:
        type = "DMChannel.private"
        id = 99
    class _Author:
        id = 222

    msg = type("Msg", (), {"channel": _Channel(), "author": _Author()})()
    assert memory.history_scope_key(msg) == "tom:hist:dm:222"


def test_history_scope_key_picks_channel_namespace():
    class _Channel:
        type = "text"
        id = 12345
    class _Author:
        id = 222

    msg = type("Msg", (), {"channel": _Channel(), "author": _Author()})()
    assert memory.history_scope_key(msg) == "tom:hist:ch:12345"


def test_prefs_roundtrip(r):
    memory.set_pref(r, "222", "suppress_films", "1")
    memory.set_pref(r, "222", "preferred_tone", "laconic")
    prefs = memory.get_prefs(r, "222")
    assert prefs["suppress_films"] == "1"
    assert prefs["preferred_tone"] == "laconic"


def test_set_pref_rejects_unknown_key(r):
    with pytest.raises(ValueError):
        memory.set_pref(r, "222", "nonsense", "value")


def test_clear_prefs_removes_all(r):
    memory.set_pref(r, "222", "suppress_films", "1")
    memory.clear_prefs(r, "222")
    assert memory.get_prefs(r, "222") == {}


def test_append_with_empty_content_is_noop(r):
    v = _viewer()
    memory.append_turn(r, "tom:hist:ch:empty", v, "user", "   ")
    assert memory.recent_turns(r, "tom:hist:ch:empty") == []


def test_append_rejects_invalid_role(r):
    v = _viewer()
    with pytest.raises(ValueError):
        memory.append_turn(r, "tom:hist:ch:bad", v, "system", "noop")
