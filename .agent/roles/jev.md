# Role: jev

Not a Claude session. Jev (`typesafe/jev-1.13` on OpenRouter, via `~/.claude/skills/claude-x-jev/scripts/jev.py`) returns labels and probabilities only. It cannot read files, run commands or write code.

- **Used for:** `scripts/jev-check.sh report <file>` (does the report's test evidence support its claimed status) and `scripts/jev-check.sh failure <log>` (label a failing test run).
- **Rule:** a Jev error or low confidence means `escalate` to the control session, never `pass`.
- **Data:** sends report and test output text to OpenRouter (approved 2026-10-08, D3). Never send finance, health or secret content.
