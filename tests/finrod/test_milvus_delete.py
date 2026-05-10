"""Milvus delete-by-metadata coverage for PR 6.

The real Milvus client is heavy and not always present; we exercise
``MilvusStore.delete_by_metadata`` against a stub that records the
expression that would be sent to Milvus. This keeps the test fast and
deterministic while still verifying the predicate -> expression
translation our PR 3 ``/forget`` flow relies on.
"""

from __future__ import annotations

import pytest

from agents.finrod.store import MilvusStore


class _StubClient:
    def __init__(self, has: bool = True):
        self._has = has
        self.deleted: list[tuple[str, str]] = []

    # MilvusClient surface we hit during MilvusStore.__init__
    def has_collection(self, _name: str) -> bool:
        return self._has

    def list_collections(self):
        return ["finrod_docs"]

    def create_collection(self, **_kwargs):  # pragma: no cover
        return None

    def delete(self, *, collection_name: str, filter: str) -> dict:
        self.deleted.append((collection_name, filter))
        return {"delete_count": 3}

    def get_collection_stats(self, _name: str) -> dict:
        return {"row_count": 0}


@pytest.fixture
def store():
    client = _StubClient()
    return MilvusStore(client=client, collection="finrod_docs", dim=384)


def test_delete_by_metadata_dispatches_string_predicate(store):
    removed = store.delete_by_metadata({"viewer": "Solomon Smith", "kind": "tom_fact"})
    assert removed == 3
    sent = store._client.deleted
    assert len(sent) == 1
    expr = sent[0][1]
    assert 'viewer == "Solomon Smith"' in expr
    assert 'kind == "tom_fact"' in expr
    assert "&&" in expr


def test_delete_by_metadata_escapes_double_quotes(store):
    store.delete_by_metadata({"viewer": 'Name "with" quotes'})
    expr = store._client.deleted[0][1]
    assert 'Name \\"with\\" quotes' in expr


def test_delete_by_metadata_empty_predicate_is_safe(store):
    assert store.delete_by_metadata({}) == 0
    assert store._client.deleted == []


def test_delete_by_metadata_numeric_predicate(store):
    store.delete_by_metadata({"ts": 1715300000})
    assert "ts == 1715300000" in store._client.deleted[0][1]


def test_delete_by_metadata_handles_milvus_exception():
    class _Boomer(_StubClient):
        def delete(self, **_kwargs):
            raise RuntimeError("milvus unhappy")

    store = MilvusStore(client=_Boomer(), collection="finrod_docs", dim=384)
    # Errors translate to 0 deletions, not an exception, so /forget can
    # continue the sweep.
    assert store.delete_by_metadata({"viewer": "x"}) == 0
