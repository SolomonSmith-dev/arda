"""Operating principles loaded into specialist system prompts.

The human-readable source-of-truth lives at `docs/agent-conduct.md`.
Reading it at import time keeps the doc and runtime injection in sync —
edit the markdown to change behavior.
"""

from __future__ import annotations

from pathlib import Path

_DOC = Path(__file__).resolve().parent.parent / "docs" / "agent-conduct.md"

CONDUCT_PROMPT: str = _DOC.read_text(encoding="utf-8")
