# ADR 0001: Decision Log Format

**Date:** 2026-04-30
**Status:** Accepted

## Context

ARDA's architecture has many "could-go-either-way" choices (sync vs async Redis, LangGraph vs raw runnables, in-repo tests vs separate test repo, etc.). Without a record, six months from now neither I nor any reader of the codebase can reconstruct *why* a given path was taken. That makes future refactors dangerous (you might undo a decision whose original constraint still applies).

## Decision

Decisions live in `docs/decisions/` inside the repo, one file per decision, numbered sequentially: `NNNN-short-slug.md`. They follow the standard ADR (Architecture Decision Record) shape: **Context** (the problem) → **Decision** (what was chosen) → **Alternatives considered** (what was rejected and why) → **Consequences** (what changes downstream). Status field is one of `Proposed`, `Accepted`, `Superseded by NNNN`, `Deprecated`.

## Alternatives considered

- **`~/System/context-bank/originals/arda-decisions-*.md`** — Solomon's private notes folder. Rejected because the decisions are about *this codebase* and become disconnected from the code if they live elsewhere.
- **Inline comments in code** — Doesn't survive refactors. Comments rot when the code they describe moves.
- **Commit messages alone** — Findable via `git log --grep` but not browsable. ADR files give a table of contents and survive squash/rebase.

## Consequences

- Every non-obvious choice in Phase 2+ gets an ADR.
- Phase 5 README links to `docs/decisions/` as evidence of considered architecture (portfolio value).
- ADRs are immutable once Accepted — superseded by writing a new ADR that references the old one, never by editing.
