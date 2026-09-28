# Gandalf: academic operations agent

Status: spec, nothing implemented. Branch: `claude/agent-gandalf`.

Gandalf keeps a source-grounded record of school work: assignments, deadlines, lectures, readings, announcements. It detects changes between scans and produces a daily briefing. The database is the source of truth; the model only extracts, classifies, synthesizes and plans.

## 1. Non-negotiables

- Read-only against the LMS. No submitting, messaging, deleting or editing course data, ever, in v1.
- Official data comes only from a source. Missing evidence stores `UNKNOWN`, never a guess.
- Conflicting sources keep both values and flag `CONFLICT`. Never auto-resolved.
- Failures are states (`ACCESS_FAILED`, `AUTH_REQUIRED`, `NEEDS_REVIEW`, `DEADLINE_UNCERTAIN`), never fabricated content.
- An official deadline and a recommended personal deadline are separate fields; the plan never writes the former.
- Only authorized accounts and material. No bypassing paywalls, DRM or school access controls.

## 2. Fit with ARDA

| Concern | Choice |
|---|---|
| Agent | `agents/gandalf/`, `GandalfAgent(BaseAgent)`, `name="gandalf"`, `tier="specialist"` |
| Contract | `run(AgentTask) -> AgentResult`, `task.type` selects the operation |
| Orchestration | Registered in `api/main.py` lifespan; new entry in `SPECIALIST_TOOL_MAP` in `agents/sauron/tools.py` |
| Scheduling | Galadriel cron job (`agentTurn`) triggers the daily scan and briefing |
| Storage | SQLite, see ADR 0008 |
| Delivery | Gwaihir (Telegram) via Galadriel `announce` delivery |
| LLM | Anthropic via the existing builder pattern (`llm.py`); `MockAnthropicClient` when `use_mock_llm` |
| Tests | Mock-by-default, no network; LMS is a fake adapter |

Module layout:

```
agents/gandalf/
  agent.py        GandalfAgent, task.type dispatch
  models.py       pydantic records + enums
  store.py        SQLite access, migrations
  adapters/
    base.py       LMSAdapter protocol
    canvas.py     Canvas REST adapter (phase 1)
    fake.py       fixture adapter for tests
  extract.py      LLM extraction -> AcademicItem (schema-validated)
  validate.py     deadline validation, timezone handling
  diff.py         NEW/UNCHANGED/UPDATED/REMOVED/CONFLICT
  briefing.py     briefing assembly
  llm.py          client builder
migrations/       0001_init.sql ...
```

## 3. Task types (`AgentTask.type`)

| type | effect | autonomy |
|---|---|---|
| `scan` | Pull from adapters, extract, validate, diff, store | read-only |
| `briefing` | Build briefing from stored state | read-only |
| `list_items` | Query items by course/type/date | read-only |
| `show_item` | One item with evidence and change history | read-only |
| `resolve_conflict` | Record the user's chosen value | user-initiated |

Result envelope (stable, like Sauron's): `result = {"operation": ..., "data": ..., "needs_review": [...]}`.

## 4. Data model

```
courses(id, lms_id, name, term, source_url, created_at)
items(id, course_id, type, title, description, official_due_at, available_at,
      tz, points, weight, submission_type, status, source_id, source_url,
      confidence, discovered_at, updated_at, removed_at)
      -- type: ASSIGNMENT QUIZ EXAM LECTURE READING ANNOUNCEMENT GRADE SYLLABUS_CHANGE OTHER
      -- status: ACTIVE, COMPLETED (user-set), REMOVED, plus flags below
item_flags(item_id, flag, detail, raised_at, cleared_at)
      -- flag: CONFLICT NEEDS_REVIEW DEADLINE_UNCERTAIN ACCESS_FAILED AUTH_REQUIRED
evidence(id, item_id, field, quote, source_id, captured_at)
      -- exact source text backing a field; deadline rows are mandatory
item_changes(id, item_id, field, old_value, new_value, source_id, detected_at)
sources(id, adapter, kind, url, content_hash, fetched_at, raw_path)
scan_runs(id, started_at, finished_at, adapter, outcome, counts_json, error)
plan_entries(id, item_id, recommended_start_at, recommended_due_at, est_minutes, rationale, created_at)
      -- separate table so official dates cannot be overwritten by planning
schema_version(version)
```

Rules:
- Identity key: `(course_id, lms_id)` when the LMS provides an id; otherwise normalized title + type + course, flagged `NEEDS_REVIEW` on ambiguity. This is the duplicate guard.
- Times stored as UTC plus the source `tz`. A date with no time is `DEADLINE_UNCERTAIN`, not midnight.
- Posted date, available date and due date are distinct fields.
- `raw_path` keeps the fetched payload under `.arda/gandalf/raw/` so any fact is re-checkable.

## 5. Scan workflow (deterministic code, model only at step 3)

```
DISCOVER  adapter.fetch(course) -> raw payloads, hashed; skip unchanged hashes
CLASSIFY  structured LMS fields first; LLM only for free-text items
EXTRACT   LLM -> AcademicItem JSON, validated by pydantic; invalid -> NEEDS_REVIEW
VALIDATE  every extracted date must appear in the raw payload text; tz preserved;
          else DEADLINE_UNCERTAIN
COMPARE   diff against stored item -> NEW | UNCHANGED | UPDATED | REMOVED | CONFLICT
STORE     write item, evidence, item_changes in one transaction
```

Notes:
- Canvas exposes structured due dates via API; prefer those over LLM extraction. The model handles descriptions and announcements.
- REMOVED is only recorded after two consecutive scans miss the item, to avoid flapping on transient failures. Nothing is deleted; `removed_at` is set.
- CONFLICT arises when two sources (for example Canvas page vs syllabus vs email) give different official dates. Both stay in `evidence`; the item is flagged until `resolve_conflict`.
- An adapter failure marks the scan outcome and raises the matching flag; existing stored data is untouched.

## 6. Briefing

Sections in order: Urgent (24h), Upcoming (7d, with a 3d sub-band), New, Changes (old -> new with source), Learning, Plan, Needs Review. Every line carries a source URL. Plan lines are labeled `RECOMMENDED`; deadlines are labeled `OFFICIAL`. Empty sections render as one line, not omitted, so silence is distinguishable from a failure.

## 7. Autonomy policy

| Class | Examples | Rule |
|---|---|---|
| Read-only | scan, extract, briefing, save notes | automatic |
| Reversible internal | tags, internal tasks, plan entries | automatic |
| External or consequential | submit, email, LMS message, drop, delete, edit official records | not implemented; would need explicit per-action confirmation |

The adapter interface is read-only by construction (no write methods exist).

## 8. Config additions (`core/config.py`)

`gandalf_db_path` (default `.arda/gandalf.sqlite`), `gandalf_canvas_base_url`, `gandalf_canvas_token` (env only, never persisted or logged), `gandalf_default_tz`, `gandalf_scan_courses` (allowlist of course ids).

## 9. Phases

1. **MVP**: Canvas adapter, assignments/quizzes/exams, SQLite store, diff, briefing, Galadriel job. Fake adapter plus fixtures for tests.
2. Announcements, calendar sync (read Google Calendar for conflicts; writes need confirmation).
3. Readings (PDF parse, Finrod for retrieval).
4. Lecture transcripts (captions first, transcription fallback).
5. Study planner (workload estimates, subtasks, flashcards).
6. Cross-course RAG through Finrod.
7. Any autonomy beyond read-only, by ADR.

## 10. MVP acceptance tests

- Re-scan of identical fixtures yields all `UNCHANGED`, zero new rows.
- Changed due date yields `UPDATED` with an `item_changes` row and briefing entry old -> new.
- Two sources with different dates yield `CONFLICT`, both values kept, no silent pick.
- Date-only or missing due time yields `DEADLINE_UNCERTAIN`.
- Extracted date absent from raw payload is rejected to `NEEDS_REVIEW`.
- Adapter raising auth error yields `AUTH_REQUIRED`, stored items unchanged.
- Item missing from one scan is not removed; missing from two is `REMOVED`.
- Sauron tool round trip returns the stable envelope.
- Token never appears in logs, DB or raw payloads.

## 11. Open decisions

- Which LMS at your school (Canvas assumed).
- Whether school email is a source in phase 2.
- Where raw payloads and notes live long term (default `.arda/gandalf/`).
