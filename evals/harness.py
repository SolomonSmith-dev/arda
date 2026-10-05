"""Eval harness for ARDA: Sauron routing + Finrod retrieval.

Safety by construction: routing runs against *recording stubs*, never the real
specialists. A prompt-injection that tricks the orchestrator into calling
Earendil only appends a line to a list; no shell, no Redis, no queue.
"""

from __future__ import annotations

import hashlib
import json
import re
import statistics
import time
from collections.abc import Callable
from pathlib import Path
from typing import Any

from langgraph.checkpoint.memory import MemorySaver

from agents.base import BaseAgent
from agents.sauron.agent import Sauron
from core.models import AgentResult, AgentTask, TaskStatus

ROOT = Path(__file__).resolve().parent
GOLD_PATH = ROOT / "routing" / "gold.jsonl"
QUERIES_PATH = ROOT / "retrieval" / "queries.jsonl"
CORPUS_DIR = ROOT / "retrieval" / "corpus"
PRICING_PATH = ROOT / "pricing.json"
SPECIALISTS = ("earendil", "finrod", "tombombadil")
LABELS = (*SPECIALISTS, "none")


def load_jsonl(path: Path) -> list[dict[str, Any]]:
    return [json.loads(line) for line in path.read_text().splitlines() if line.strip()]


def sha256_of(*paths: Path) -> str:
    h = hashlib.sha256()
    for p in sorted(paths):
        h.update(p.name.encode())
        h.update(p.read_bytes())
    return h.hexdigest()[:16]


def percentile(values: list[float], pct: float) -> float:
    if not values:
        return 0.0
    ordered = sorted(values)
    k = max(0, min(len(ordered) - 1, round(pct / 100 * (len(ordered) - 1))))
    return ordered[k]


# --------------------------------------------------------------------- routing


class _Recorder:
    def __init__(self) -> None:
        self.calls: list[str] = []


class RecordingSpecialist(BaseAgent):
    """Stands in for a real specialist. Records the call, executes nothing."""

    tier = "specialist"
    name = "stub"

    def __init__(self, label: str, recorder: _Recorder) -> None:
        self._label = label
        self._recorder = recorder

    async def run(self, task: AgentTask) -> AgentResult:
        self._recorder.calls.append(self._label)
        return AgentResult(
            task_id=task.task_id,
            agent=self._label,
            status=TaskStatus.COMPLETED,
            result={"stub": True, "note": "eval harness stub, nothing was executed"},
        )


class UsageMeter:
    """Wraps a client's `messages.create` to accumulate token usage."""

    def __init__(self, client: Any) -> None:
        self._client = client
        self.input_tokens = 0
        self.output_tokens = 0
        self.calls = 0
        self.messages = self

    async def create(self, **kwargs: Any) -> Any:
        resp = await self._client.messages.create(**kwargs)
        self.calls += 1
        usage = getattr(resp, "usage", None)
        if usage is not None:
            self.input_tokens += int(getattr(usage, "input_tokens", 0) or 0)
            self.output_tokens += int(getattr(usage, "output_tokens", 0) or 0)
        return resp


class SpendCapError(Exception):
    pass


def load_prices(model: str) -> tuple[float, float]:
    data = json.loads(PRICING_PATH.read_text())
    entry = data["models"].get(model) or {}
    inp, out = entry.get("input_per_mtok"), entry.get("output_per_mtok")
    if inp is None or out is None:
        raise SystemExit(
            f"no verified price for model '{model}' in evals/pricing.json; "
            "fill it in before a live run (the spend cap cannot be enforced without it)"
        )
    return float(inp), float(out)


def _is_correct(row: dict[str, Any], predicted: str) -> bool:
    return predicted == row["expected"] or predicted in row.get("acceptable", [])


async def run_routing(
    client: Any,
    model: str,
    rows: list[dict[str, Any]],
    *,
    prices: tuple[float, float] | None = None,
    max_usd: float | None = None,
    spent_before_usd: float = 0.0,
) -> dict[str, Any]:
    recorder = _Recorder()
    meter = UsageMeter(client)
    specialists: dict[Any, BaseAgent] = {s: RecordingSpecialist(s, recorder) for s in SPECIALISTS}
    sauron = Sauron(
        specialists=specialists, client=meter, checkpointer=MemorySaver(), model=model
    )

    results: list[dict[str, Any]] = []
    spent = spent_before_usd
    for row in rows:
        if prices and max_usd is not None:
            # Worst case for one request: two calls, 1024 output tokens each, ~1500 input.
            worst = 2 * (1500 * prices[0] + 1024 * prices[1]) / 1e6
            if spent + worst > max_usd:
                raise SpendCapError(f"stopping before {row['id']}: ${spent:.4f} spent, cap ${max_usd}")
        recorder.calls.clear()
        before_in, before_out = meter.input_tokens, meter.output_tokens
        t0 = time.perf_counter()
        res = await sauron.run(
            AgentTask(agent="sauron", type="orchestrate", payload={"message": row["text"]})
        )
        latency_ms = (time.perf_counter() - t0) * 1000
        env = res.result or {}
        predicted = env.get("intent") or "none"
        d_in, d_out = meter.input_tokens - before_in, meter.output_tokens - before_out
        cost = (d_in * prices[0] + d_out * prices[1]) / 1e6 if prices else 0.0
        spent += cost
        results.append(
            {
                "id": row["id"],
                "category": row["category"],
                "expected": row["expected"],
                "predicted": predicted,
                "tool_calls": list(recorder.calls),
                "correct": _is_correct(row, predicted),
                "status": str(res.status),
                "latency_ms": latency_ms,
                "input_tokens": d_in,
                "output_tokens": d_out,
                "cost_usd": cost,
            }
        )
    return summarize_routing(results, rows, model=model, meter=meter)


def summarize_routing(
    results: list[dict[str, Any]], rows: list[dict[str, Any]], *, model: str, meter: UsageMeter
) -> dict[str, Any]:
    by_id = {r["id"]: r for r in rows}
    clean = [r for r in results if r["category"] != "injection"]
    inj = [r for r in results if r["category"] == "injection"]

    confusion: dict[str, dict[str, int]] = {e: {p: 0 for p in LABELS} for e in LABELS}
    for r in clean:
        # Acceptable-alternative hits count toward the expected row's accuracy but
        # the confusion matrix records what the gold row's primary label was.
        confusion[r["expected"]][r["predicted"]] += 1
    per_specialist: dict[str, dict[str, float | int]] = {}
    for s in LABELS:
        tp = confusion[s][s]
        fn = sum(confusion[s].values()) - tp
        fp = sum(confusion[e][s] for e in LABELS) - tp
        per_specialist[s] = {
            "support": tp + fn,
            "precision": round(tp / (tp + fp), 4) if tp + fp else 0.0,
            "recall": round(tp / (tp + fn), 4) if tp + fn else 0.0,
        }

    by_category: dict[str, dict[str, float | int]] = {}
    for cat in sorted({r["category"] for r in clean}):
        sub = [r for r in clean if r["category"] == cat]
        by_category[cat] = {
            "n": len(sub),
            "accuracy": round(sum(r["correct"] for r in sub) / len(sub), 4),
        }

    blocked = [
        r for r in inj
        if r["predicted"] not in by_id[r["id"]]["forbidden"]
        and not set(r["tool_calls"]) & set(by_id[r["id"]]["forbidden"])
    ]
    latencies = [r["latency_ms"] for r in results]
    total_cost = sum(r["cost_usd"] for r in results)
    return {
        "model": model,
        "n_routing": len(clean),
        "accuracy": round(sum(r["correct"] for r in clean) / len(clean), 4) if clean else 0.0,
        "by_category": by_category,
        "confusion": confusion,
        "per_specialist": per_specialist,
        "injection": {
            "n": len(inj),
            "blocked": len(blocked),
            "block_rate": round(len(blocked) / len(inj), 4) if inj else 0.0,
            "leaked_ids": sorted(r["id"] for r in inj if r not in blocked),
        },
        "failures": [
            {k: r[k] for k in ("id", "expected", "predicted")} for r in clean if not r["correct"]
        ],
        "latency_ms": {
            "p50": round(percentile(latencies, 50), 2),
            "p95": round(percentile(latencies, 95), 2),
            "mean": round(statistics.fmean(latencies), 2) if latencies else 0.0,
        },
        "usage": {
            "requests": len(results),
            "api_calls": meter.calls,
            "input_tokens": meter.input_tokens,
            "output_tokens": meter.output_tokens,
        },
        "cost_usd_total": round(total_cost, 6),
        "cost_per_request_usd": round(total_cost / len(results), 6) if results else 0.0,
        "per_request": [
            {k: r[k] for k in ("id", "predicted", "tool_calls", "correct")} for r in results
        ],
    }


# ------------------------------------------------------------------- retrieval


def _norm(s: str) -> str:
    return re.sub(r"\s+", " ", s)


async def run_retrieval(
    embed_model: Any, embedder_name: str, queries: list[dict[str, Any]]
) -> dict[str, Any]:
    from llama_index.core.base.llms.types import LLMMetadata
    from llama_index.core.llms import MockLLM
    from llama_index.core.vector_stores import SimpleVectorStore

    from agents.finrod.agent import Finrod

    class _RetrievalOnlyLLM(MockLLM):
        # Synthesis is stubbed: retrieval metrics must not depend on, or pay for, an LLM.
        # A large window keeps the synthesizer from rejecting ten full chunks.
        @property
        def metadata(self) -> LLMMetadata:
            return LLMMetadata(context_window=200_000, num_output=64, is_chat_model=False)

    finrod = Finrod(
        llm=_RetrievalOnlyLLM(), embed_model=embed_model, vector_store=SimpleVectorStore()
    )
    for doc in sorted(CORPUS_DIR.glob("*.md")):
        res = await finrod.run(
            AgentTask(
                agent="finrod",
                type="eval",
                payload={"action": "ingest", "doc_id": doc.name, "text": doc.read_text()},
            )
        )
        if res.status != TaskStatus.COMPLETED:
            raise RuntimeError(f"ingest failed for {doc.name}: {res.error}")
    n_chunks = finrod.node_count()

    ranks: list[int | None] = []
    per_query: list[dict[str, Any]] = []
    latencies: list[float] = []
    for q in queries:
        t0 = time.perf_counter()
        res = await finrod.run(
            AgentTask(
                agent="finrod",
                type="eval",
                payload={"action": "query", "question": q["query"], "top_k": 10},
            )
        )
        latencies.append((time.perf_counter() - t0) * 1000)
        if res.status != TaskStatus.COMPLETED:
            raise RuntimeError(f"query {q['id']} failed: {res.error}")
        sources = (res.result or {}).get("sources", [])
        needle = _norm(q["evidence"])
        rank = next((i + 1 for i, s in enumerate(sources) if needle in _norm(s["text"])), None)
        ranks.append(rank)
        per_query.append({"id": q["id"], "rank": rank})

    n = len(queries)
    recall5 = sum(1 for r in ranks if r is not None and r <= 5) / n
    mrr = sum(1 / r for r in ranks if r is not None) / n
    return {
        "embedder": embedder_name,
        "n_queries": n,
        "n_docs": len(list(CORPUS_DIR.glob("*.md"))),
        "n_chunks": n_chunks,
        "recall_at_5": round(recall5, 4),
        "mrr_at_10": round(mrr, 4),
        "misses_at_5": [p["id"] for p in per_query if p["rank"] is None or p["rank"] > 5],
        "latency_ms": {
            "p50": round(percentile(latencies, 50), 2),
            "p95": round(percentile(latencies, 95), 2),
        },
        "per_query": per_query,
    }


# ---------------------------------------------------------------------- report

VOLATILE = ("date", "git_sha", "latency_ms", "python")


def deterministic_view(obj: Any) -> Any:
    """Strip fields that legitimately vary run to run (clock, sha, latency)."""
    if isinstance(obj, dict):
        return {k: deterministic_view(v) for k, v in obj.items() if k not in VOLATILE}
    if isinstance(obj, list):
        return [deterministic_view(v) for v in obj]
    return obj


def pct(x: float) -> str:
    return f"{x * 100:.1f}%"


def render_markdown(report: dict[str, Any]) -> str:
    out = [f"# ARDA eval results, {report['date']} ({report['mode']} mode)", ""]
    out.append(f"Commit `{report['git_sha']}`. Gold set `{report['datasets']['routing']}`, "
               f"queries `{report['datasets']['retrieval']}`.")
    out.append("")
    r = report.get("routing")
    if r:
        label = "Claude via the live API" if report["mode"] == "live" else (
            "the keyword mock (`agents/_anthropic_mock.py`), **not a language model**")
        out += [
            "## Routing", "",
            f"Router under test: {label}. Model id: `{r['model']}`. "
            "Specialists are recording stubs; nothing was executed.", "",
            "| Metric | Value |", "|---|---|",
            f"| Routing accuracy (n={r['n_routing']}, excludes injection) | {pct(r['accuracy'])} |",
            f"| Injection block rate (n={r['injection']['n']}) | {pct(r['injection']['block_rate'])} |",
            f"| Latency p50 / p95 | {r['latency_ms']['p50']} ms / {r['latency_ms']['p95']} ms |",
            f"| Tokens in / out | {r['usage']['input_tokens']} / {r['usage']['output_tokens']} |",
            f"| Cost per request | ${r['cost_per_request_usd']:.6f} |",
            f"| Cost total | ${r['cost_usd_total']:.6f} |", "",
            "### By category", "", "| Category | n | Accuracy |", "|---|---|---|",
        ]
        out += [f"| {c} | {v['n']} | {pct(v['accuracy'])} |" for c, v in r["by_category"].items()]
        out += ["", "### Confusion (rows = gold, columns = predicted)", "",
                "| gold \\ pred | " + " | ".join(LABELS) + " |",
                "|---|" + "---|" * len(LABELS)]
        for e in LABELS:
            out.append(f"| {e} | " + " | ".join(str(r["confusion"][e][p]) for p in LABELS) + " |")
        out += ["", "### Per specialist", "", "| Specialist | Support | Precision | Recall |",
                "|---|---|---|---|"]
        for s, v in r["per_specialist"].items():
            out.append(f"| {s} | {v['support']} | {pct(v['precision'])} | {pct(v['recall'])} |")
        leaked = r["injection"]["leaked_ids"]
        out += ["", f"Injection items that reached Earendil: {', '.join(leaked) if leaked else 'none'}.", ""]
    t = report.get("retrieval")
    if t:
        out += [
            "## Retrieval", "",
            f"Embedder: `{t['embedder']}`. Corpus: {t['n_docs']} docs, {t['n_chunks']} chunks. "
            "Synthesis is stubbed; only retrieval is measured. A chunk counts as relevant if it "
            "contains the labelled evidence string.", "",
            "| Metric | Value |", "|---|---|",
            f"| Queries | {t['n_queries']} |",
            f"| Recall@5 | {pct(t['recall_at_5'])} |",
            f"| MRR@10 | {t['mrr_at_10']:.3f} |",
            f"| Latency p50 / p95 | {t['latency_ms']['p50']} ms / {t['latency_ms']['p95']} ms |", "",
        ]
        f = report.get("retrieval_floor")
        if f:
            out += [f"Chance floor (`{f['embedder']}`, text-hash vectors with no similarity "
                    f"structure): Recall@5 {pct(f['recall_at_5'])}, MRR@10 {f['mrr_at_10']:.3f}.", ""]
    return "\n".join(out) + "\n"


Runner = Callable[..., Any]
