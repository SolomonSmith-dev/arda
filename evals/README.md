# ARDA evals

Two suites, one runner: `uv run python scripts/run_evals.py --mode mock|live`.

## What is measured

**Routing** (`routing/gold.jsonl`, 70 hand-written requests). Sauron gets each request; the metric is which specialist it calls first, or `none` if it answers in text. Categories: shell, knowledge, film, ambiguous (a second answer is accepted), chitchat (expected `none`), and 16 prompt-injection attempts to reach Earendil's shell. Routing accuracy excludes the injection rows. Injection block rate is the share of injection rows where Earendil was never called.

**Retrieval** (`retrieval/queries.jsonl`, 38 queries over `retrieval/corpus/`). The corpus is a frozen copy of ARDA's own docs, so labels do not drift when the docs change. A retrieved chunk is relevant if it contains the labelled `evidence` string. Metrics are recall@5 and MRR@10.

## Mock vs live

| | mock | live |
|---|---|---|
| Router | keyword classifier in `agents/_anthropic_mock.py` | Claude through the Anthropic API |
| Embedder | `LexicalHashEmbedding`, a deterministic bag-of-words hash | `all-MiniLM-L6-v2` (needs `--extra full` and HuggingFace access) |
| Cost | $0 | priced from `pricing.json`, capped by `--max-usd` |
| Use | CI regression check | the numbers you quote |

Read the mock routing numbers as a regression baseline for the mock, not as a statement about Claude. The mock cannot answer `none`, so chitchat scores 0% and most injection rows reach Earendil. Those are the honest properties of a keyword router and are the reason Phase 3 adds a demo mode that refuses shell execution at the router.

The stock test embedders (`MockEmbedding`, `HashEmbedding`) carry no similarity structure. The report prints `HashEmbedding` as the chance floor so recall numbers have a reference point.

## Safety

Routing runs against recording stubs. If an injection reaches the Earendil tool, the stub appends to a list. No shell, Redis or queue is touched, in either mode.

## Spend control (live)

- `pricing.json` ships with null prices. Live mode refuses to start until the active model has a verified price, because the cap cannot be enforced without one.
- Before every request the runner checks worst-case cost against `--max-usd` (default 5), counting everything already in `results/spend-ledger.jsonl`.
- Each live run appends tokens and estimated cost to the ledger and records them in the result JSON.

## CI

`scripts/run_evals.py --mode mock --check` runs the mock suites twice, fails if the two runs differ, and fails if they differ from `results/baseline-mock.json` (latency, date and commit are excluded). After an intended change to the gold set, corpus or mock, run with `--update-baseline` and commit the file.

The corpus files are copies with em dashes replaced by hyphens, per repo style. Do not edit them casually; change a doc, then recopy and relabel.
