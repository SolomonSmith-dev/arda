from __future__ import annotations

import pytest


def pytest_collection_modifyitems(config, items):
    skip_phase4 = pytest.mark.skip(reason="phase 4: needs real Redis/Discord/Milvus")
    for item in items:
        if "phase4" in item.keywords:
            item.add_marker(skip_phase4)
