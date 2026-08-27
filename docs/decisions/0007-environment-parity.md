# 0007 — Test environments must be provably faithful to production

- **Status:** Accepted
- **Date:** 2026-08-27
- **Related:** [0003-mock-llm-location.md](0003-mock-llm-location.md) (mock-by-default)

## Context

On 2026-08-27 the deploy host was reconciled onto `main` after roughly two
months of drift. The reconcile surfaced a cluster of bugs that a passing test
suite had never had any chance of catching. Four of them shared one cause:
**the environment the tests ran in differed from the one production ran in.**

| Bug | What the tests saw | What production saw |
|---|---|---|
| `orchestrator_model = "claude-opus-4-7"` | `MockAnthropicClient`, no model ID ever sent | a 404 on the first real call |
| `temperature=0.2` on Tom's chat call | a mock taking `**kwargs`, which swallowed it | `TypeError`, every reply failed |
| `temperature` inside llama-index | `MockLLM`, never reaches the SDK | empty synthesis on every query |
| numpy 2.x | arm64 dev machines | `RuntimeError` on a 2008 x86 CPU |

The `temperature` case is the sharpest. `pyproject.toml` pinned
`anthropic>=0.40` with no upper bound, so a local checkout resolved 0.100.0
while a fresh Docker build resolved 1.1.0. `temperature` is valid on 0.x and
was removed from `Messages.create` in 1.x. The parameter genuinely worked in
the author's environment and raised in production. Nobody was careless; an
unbounded dependency had quietly forked the two environments.

Mock-by-default (ADR 0003) is still right. 425 tests running with no API keys
and no cost is worth keeping. But it defines a blind spot with a precise
shape: **anything whose only validator is the remote API accepting it.** Model
IDs, parameter names, endpoint paths, auth header names. Nothing in CI ever
makes a call that could reject them.

## Decision

Three rules, each with an enforcing test rather than a convention.

### 1. A mock may not accept more than the thing it replaces

A mock with `**kwargs` cannot fail on a mismatch — its permissiveness *is* the
defect. Mock signatures are strict subsets of the real ones.

Enforced by `tests/test_mock_sdk_parity.py`, which introspects the *installed*
SDK and asserts the mocks accept nothing it rejects, take no `**kwargs`, and
that the parameters we send still exist. It needs no API key or network, so it
runs in ordinary CI.

### 2. Dependencies that define a wire contract get an upper bound

An unbounded pin lets dev and prod diverge silently across a major version.
`anthropic` is bounded, with the reason and the revisit condition written at
the pin. Same for `numpy<2`, bounded by the deploy host's CPU baseline rather
than by an API.

### 3. Contract checks that need real credentials are marked, not skipped

`tests/test_live_smoke.py` makes one minimal real call per LLM tier, gated on
`USE_MOCK_LLM=false` plus a real key. It is inert by default and self-activates
when it can do its job. It is deliberately **not** `phase4`, because
`tests/conftest.py` unconditionally skips that marker — such a test could never
run at all.

## Consequences

- Upgrading a wire-contract dependency now means deliberately moving a bound.
  That is the point: SDK majors change signatures.
- The parity test fails when an SDK removes something we pass, which is the
  reverse drift and the harder direction to notice.
- Real-credential coverage stays opt-in. A deploy that flips `USE_MOCK_LLM` for
  the first time is still the riskiest moment in this system, and the live
  smoke test only helps if someone runs it.

## Not covered

This does not address third-party incompatibility between two pinned packages.
`llama-index-llms-anthropic` forwards `temperature` unconditionally, so Finrod
breaks on anthropic 1.x no matter what ARDA passes. That is why the bound is
`<1` and not `<2`. No test here would have predicted it; it was found by
driving a real query against the deploy host, which remains the only way to
learn some things.
