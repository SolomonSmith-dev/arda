from __future__ import annotations

import os

os.environ.setdefault("ARDA_API_KEY", "test-arda-key-ci")

import pytest


def pytest_collection_modifyitems(config, items):
    skip_phase4 = pytest.mark.skip(reason="phase 4: needs real Redis/Discord/Milvus")
    for item in items:
        if "phase4" in item.keywords:
            item.add_marker(skip_phase4)
