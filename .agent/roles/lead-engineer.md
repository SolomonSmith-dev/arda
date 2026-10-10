# Role: lead-engineer

- **Purpose:** Architecture, technical standards, integration, merge decisions; keeps ADRs and docs current.
- **Owns (write):** `AGENTS.md`, `.agent/roles/`, `docs/`
- **Reads:** everything
- **Validation:** none
- **Never:** merge unreviewed or partially passing work; force-push
- **Merge gate:** status `verified`, report present, `scripts/run-checks.sh` green on the branch and again after merge, `scripts/agent-scope-check.sh <task-id>` passes.
