"""Regenerate the badge and Results blocks in README.md from committed eval JSON.

    uv run python scripts/update_readme.py           # rewrite README.md
    uv run python scripts/update_readme.py --check   # CI: fail if README is stale

Numbers come only from evals/results/*.json. The headline badge uses the newest
live result if one exists, otherwise the newest mock result, labelled as mock.
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from collections.abc import Callable
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
README = REPO / "README.md"
RESULTS = REPO / "evals" / "results"

Result = tuple[Path, dict]


def latest(mode: str) -> Result | None:
    files = sorted(RESULTS.glob(f"20??-??-??-{mode}.json"))
    return (files[-1], json.loads(files[-1].read_text())) if files else None


def pct(x: float) -> str:
    return f"{x * 100:.1f}%"


def cell(rep: dict | None, fn: Callable[[dict], str], na: str = "not run yet") -> str:
    try:
        return fn(rep) if rep else na
    except KeyError:
        return na


def badges(live: dict | None, mock: dict | None) -> str:
    rep, label = (live, "routing accuracy") if live else (mock, "routing accuracy (mock)")
    out = [
        "[![CI](https://github.com/SolomonSmith-dev/arda/actions/workflows/ci.yml/badge.svg)]"
        "(https://github.com/SolomonSmith-dev/arda/actions/workflows/ci.yml)",
        "![Python](https://img.shields.io/badge/python-3.12+-blue.svg)",
        "![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)",
    ]
    if rep and "routing" in rep:
        acc = pct(rep["routing"]["accuracy"]).replace("%", "%25")
        label_q = label.replace(" ", "_").replace("(", "%28").replace(")", "%29")
        colour = "blue" if live else "lightgrey"
        out.append(f"![{label}](https://img.shields.io/badge/{label_q}-{acc}-{colour})")
    return " ".join(out)


def results_block(live: Result | None, mock: Result | None) -> str:
    lr = live[1] if live else None
    mr = mock[1] if mock else None

    def row(name: str, fn: Callable[[dict], str]) -> str:
        return f"| {name} | {cell(mr, fn)} | {cell(lr, fn)} |"

    lines = [
        "| Metric | Mock baseline | Live |",
        "|---|---|---|",
        row(
            "Routing accuracy",
            lambda r: f"{pct(r['routing']['accuracy'])} (n={r['routing']['n_routing']})",
        ),
        row(
            "Prompt-injection block rate",
            lambda r: (
                f"{pct(r['routing']['injection']['block_rate'])} "
                f"(n={r['routing']['injection']['n']})"
            ),
        ),
        row("Retrieval recall@5", lambda r: pct(r["retrieval"]["recall_at_5"])),
        row("Retrieval MRR@10", lambda r: f"{r['retrieval']['mrr_at_10']:.3f}"),
        row(
            "Routing latency p50 / p95",
            lambda r: f"{r['routing']['latency_ms']['p50']} / {r['routing']['latency_ms']['p95']} ms",
        ),
        row("Cost per request", lambda r: f"${r['routing']['cost_per_request_usd']:.4f}"),
    ]
    src = []
    for tag, res in (("mock", mock), ("live", live)):
        if res:
            rel = res[0].relative_to(REPO)
            src.append(f"{tag}: [`{rel}`]({rel})")
    floor = ""
    if mr and "retrieval_floor" in mr:
        floor = (
            f" The chance floor for retrieval on this corpus is "
            f"{pct(mr['retrieval_floor']['recall_at_5'])} recall@5."
        )
    note = (
        f"Sources: {'; '.join(src)}.\n\n"
        "**How to read this.** The mock column measures the offline test doubles: a keyword router "
        'that cannot answer "none", and a lexical bag-of-words embedder. It is a regression '
        "baseline that CI checks for determinism, not a claim about Claude. The low injection "
        "block rate is the point: on the default stack the only guard against a shell-bound "
        "prompt is the router. Public demo mode closes that gap structurally. It refuses shell "
        "execution at the API router, inside the Earendil agent and in the worker, and "
        f"`tests/test_demo_mode.py` checks every route.{floor} The live column fills in when a "
        "run against the real API is committed (`scripts/run_evals.py --mode live`, hard spend "
        "cap, see [`evals/README.md`](evals/README.md))."
    )
    return "\n".join(lines) + "\n\n" + note


def replace_block(text: str, name: str, body: str) -> str:
    pat = re.compile(rf"(<!-- {name}:start -->\n).*?(\n<!-- {name}:end -->)", re.S)
    if not pat.search(text):
        sys.exit(f"README.md is missing the <!-- {name}:start/end --> markers")
    return pat.sub(lambda m: m.group(1) + body + m.group(2), text)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--check", action="store_true")
    args = ap.parse_args()
    live, mock = latest("live"), latest("mock")
    text = README.read_text()
    new = replace_block(text, "badges", badges(live[1] if live else None, mock[1] if mock else None))
    new = replace_block(new, "results", results_block(live, mock))
    if args.check:
        if new != text:
            print("FAIL: README.md results or badges are stale; run scripts/update_readme.py")
            return 1
        print("OK: README matches committed eval results")
        return 0
    README.write_text(new)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
