# Backlog

Status: TODO, DOING, DONE, or BLOCKED with the specific reason. Update the row in the same PR
that finishes it. Every row has an acceptance check someone can run.

| ID | Task | Acceptance check | Status |
|---|---|---|---|
| F-1 | Live eval run on `claude-haiku-4-5-20251001` | `evals/results/<date>-live.json` committed, `cost_usd_total` <= 5, `scripts/update_readme.py --check` passes | BLOCKED: needs the owner's `ANTHROPIC_API_KEY` and verified prices in `evals/pricing.json` (`docs/live-eval-checklist.md`) |
| F-2 | Deploy the public demo | every done check in `docs/deploy-demo.md` prints PASS | BLOCKED: needs the owner's server and Cloudflare access |
| F-6 | `factory/status.sh`: one line per `STATUS-*.md` (slug, branch, minutes since last commit, `now running`), flagging STALE after 30 min with no commit. Port of agent-crew `payload/core/scripts/agent-status.sh` | a test in `tests/test_factory.py` with a fake `FACTORY_DIR` prints STALE for an old branch and not for a fresh one; `scripts/check.sh` passes | TODO |
| F-3 | Parameterised Earendil allowlist (`ls <path>` limited to set roots) | tests refuse `..` escapes and absolute paths outside the roots; `scripts/check.sh` passes | TODO |
| F-4 | `scripts/check.sh` remote switch: `REMOTE=<host>` runs the gate on the sleeper | `REMOTE=<host> scripts/check.sh` ends in GATE PASS on the host, and exits non-zero when rsync or ssh fails | TODO |
| F-5 | Retake `docs/img/demo-page.png` against the live demo | image shows the `live: Claude` badge | BLOCKED: depends on F-2 |
| F-7 | `factory/scope-check.sh <slug>`: fail when `task/<slug>` changes files outside the paths listed in `STATUS-<slug>.md`. Port of agent-crew `payload/core/scripts/agent-scope-check.sh` | a test fails on an out-of-scope file and passes when every changed file is listed; `scripts/check.sh` passes | TODO |
| F-8 | Rúmil audit survives a Finrod restart (#68): ingest via `Rumil.run` into a `Finrod(persist_dir=tmp_path, embed_model=HashEmbedding())`, build a fresh `Finrod` on the same dir, query it | a test in `tests/rumil/test_agent.py` finds `github-audit:<user>:<day>` in the reloaded Finrod's `sources`; `scripts/check.sh` passes | TODO |
| F-9 | Verify the GitHub audit cron on the deploy host (#70) | on a host reconciled with `scripts/reconcile-deploy-host.sh`: `docker compose --profile cron` up, `redis-cli --scan --pattern 'cron:job:*'` lists `cron:job:rumil_github_audit` exactly once after two API restarts, and one Telegram message arrives at the next 08:00 PT | BLOCKED: needs the owner's server access (same gate as F-2) |
