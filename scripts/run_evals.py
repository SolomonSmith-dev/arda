"""Run the ARDA eval suites (routing + retrieval) in mock or live mode.

    uv run python scripts/run_evals.py --mode mock
    uv run python scripts/run_evals.py --mode mock --check          # CI: determinism + regression
    uv run python scripts/run_evals.py --mode live --max-usd 5      # needs ANTHROPIC_API_KEY

Writes evals/results/<date>-<mode>.json and .md. Live mode appends every run to
evals/results/spend-ledger.jsonl and refuses to exceed --max-usd across all runs.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import platform
import subprocess
import sys
from datetime import UTC, datetime
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO))
# Settings requires a key to import; the harness never calls anything with it.
os.environ.setdefault("ARDA_API_KEY", "eval-harness-placeholder")

from evals import harness  # noqa: E402

RESULTS = REPO / "evals" / "results"
BASELINE = RESULTS / "baseline-mock.json"
LEDGER = RESULTS / "spend-ledger.jsonl"


def git_sha() -> str:
    try:
        return subprocess.check_output(
            ["git", "rev-parse", "--short", "HEAD"], cwd=REPO, text=True
        ).strip()
    except Exception:  # noqa: BLE001
        return "unknown"


def ledger_total() -> float:
    if not LEDGER.exists():
        return 0.0
    return sum(json.loads(line)["cost_usd"] for line in LEDGER.read_text().splitlines() if line)


async def build_report(args: argparse.Namespace) -> dict:
    gold = harness.load_jsonl(harness.GOLD_PATH)
    queries = harness.load_jsonl(harness.QUERIES_PATH)
    corpus_files = sorted(harness.CORPUS_DIR.glob("*.md"))
    report: dict = {
        "schema": 1,
        "date": args.date,
        "mode": args.mode,
        "git_sha": git_sha(),
        "python": platform.python_version(),
        "datasets": {
            "routing": harness.sha256_of(harness.GOLD_PATH),
            "retrieval": harness.sha256_of(harness.QUERIES_PATH, *corpus_files),
        },
    }

    if args.suite in ("all", "routing"):
        if args.mode == "mock":
            from agents._anthropic_mock import MockAnthropicClient

            client, model, prices = MockAnthropicClient(model="mock"), "mock", None
        else:
            key = os.environ.get("ANTHROPIC_API_KEY", "")
            if not key:
                raise SystemExit("live mode needs ANTHROPIC_API_KEY in the environment")
            import anthropic

            from core.config import settings

            client = anthropic.AsyncAnthropic(api_key=key)
            model = args.model or settings.orchestrator_model
            prices = harness.load_prices(model)
        spent_before = ledger_total() if args.mode == "live" else 0.0
        if args.mode == "live" and spent_before >= args.max_usd:
            raise SystemExit(f"ledger already at ${spent_before:.2f}, cap ${args.max_usd}")
        report["routing"] = await harness.run_routing(
            client, model, gold, prices=prices, max_usd=args.max_usd, spent_before_usd=spent_before
        )

    if args.suite in ("all", "retrieval"):
        from agents._llama_index_mock import HashEmbedding

        if args.mode == "mock":
            from agents.finrod.lexical import LexicalHashEmbedding

            embed, name = LexicalHashEmbedding(), "lexical-hash-2048 (offline baseline)"
        else:
            try:
                from llama_index.embeddings.huggingface import HuggingFaceEmbedding
            except ImportError:
                raise SystemExit("live retrieval needs `uv sync --extra dev --extra full`") from None
            embed = HuggingFaceEmbedding(model_name="sentence-transformers/all-MiniLM-L6-v2")
            name = "sentence-transformers/all-MiniLM-L6-v2"
        report["retrieval"] = await harness.run_retrieval(embed, name, queries)
        report["retrieval_floor"] = await harness.run_retrieval(
            HashEmbedding(), "hash-embedding (chance floor)", queries
        )
    return report


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--mode", choices=["mock", "live"], default="mock")
    ap.add_argument("--suite", choices=["all", "routing", "retrieval"], default="all")
    ap.add_argument("--date", default=datetime.now(UTC).date().isoformat())
    ap.add_argument("--model", default=None, help="live routing model id override")
    ap.add_argument("--max-usd", type=float, default=5.0)
    ap.add_argument("--check", action="store_true", help="compare against the mock baseline")
    ap.add_argument("--update-baseline", action="store_true")
    ap.add_argument("--no-write", action="store_true")
    args = ap.parse_args()

    report = asyncio.run(build_report(args))

    if args.check:
        if args.mode != "mock":
            raise SystemExit("--check is mock-only")
        second = asyncio.run(build_report(args))
        a, b = harness.deterministic_view(report), harness.deterministic_view(second)
        if a != b:
            print("FAIL: two mock runs differ (non-deterministic)")
            return 1
        base = json.loads(BASELINE.read_text())
        if a != base:
            print("FAIL: mock results differ from evals/results/baseline-mock.json")
            print("If the change is intended, rerun with --update-baseline and commit it.")
            return 1
        print("OK: mock evals deterministic and match baseline")
        return 0

    if args.update_baseline:
        BASELINE.write_text(json.dumps(harness.deterministic_view(report), indent=2) + "\n")
        print(f"wrote {BASELINE.relative_to(REPO)}")

    if not args.no_write:
        RESULTS.mkdir(parents=True, exist_ok=True)
        stem = RESULTS / f"{args.date}-{args.mode}"
        stem.with_suffix(".json").write_text(json.dumps(report, indent=2) + "\n")
        stem.with_suffix(".md").write_text(harness.render_markdown(report))
        print(f"wrote {stem.relative_to(REPO)}.json and .md")
        if args.mode == "live" and "routing" in report:
            u = report["routing"]["usage"]
            with LEDGER.open("a") as f:
                f.write(json.dumps({
                    "date": args.date, "model": report["routing"]["model"],
                    "requests": u["requests"], "input_tokens": u["input_tokens"],
                    "output_tokens": u["output_tokens"],
                    "cost_usd": report["routing"]["cost_usd_total"],
                }) + "\n")
    print(harness.render_markdown(report))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
