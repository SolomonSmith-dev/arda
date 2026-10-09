#!/usr/bin/env bash
# The one gate. CI runs exactly this, and so does every agent before it says "done".
# Usage: scripts/check.sh            (all steps, stops at the first failure)
set -euo pipefail
cd "$(git rev-parse --show-toplevel)"

step() { echo; echo "==> $*"; }

step "ruff";            uv run ruff check .
# agents + core only: test doubles fake third-party shapes on purpose, typing them adds noise.
step "mypy";            uv run mypy agents core
step "pytest";          uv run pytest tests/ -q
step "evals (mock)";    uv run python scripts/run_evals.py --mode mock --check --no-write
step "README results";  uv run python scripts/update_readme.py --check

echo; echo "GATE PASS"
