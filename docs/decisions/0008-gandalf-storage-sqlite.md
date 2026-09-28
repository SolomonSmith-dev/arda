# ADR 0008: Gandalf stores academic state in SQLite

**Date:** 2026-09-28
**Status:** Proposed

## Context

Gandalf (academic operations agent, see `docs/gandalf.md`) needs durable, queryable state with history: assignments, deadline changes, evidence, source records. Existing storage in ARDA is Redis (Earendil queue, Galadriel jobs) and SQLite (Sauron checkpointer at `.arda/`).

## Decision

Use SQLite at `settings.gandalf_db_path` (default `.arda/gandalf.sqlite`, gitignored), accessed through a small `store.py` module in the Galadriel style (pydantic models in, pydantic models out). Redis stays for scheduling only, through Galadriel.

## Alternatives considered

- **Redis** — matches Galadriel, but diffing, history and joins across course/assignment/evidence are awkward and Redis persistence is not the source of truth here. Rejected.
- **PostgreSQL** — the spec's original suggestion. Adds a service the repo does not run and the mock-by-default test story loses. Rejected until multi-user or remote access is needed.

## Consequences

- No new service; tests use a temp-file or `:memory:` SQLite, no keys needed.
- Schema is versioned by a `schema_version` table; migrations are plain SQL files.
- A move to Postgres later is contained to `store.py` if SQL stays portable.
