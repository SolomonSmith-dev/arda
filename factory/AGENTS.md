# Factory working agreement

How agent work gets done on this repo. Modeled on the Court Rules software factory: a station
per concern, coordination through plain files, one gate. Keep this page true: change it in the
same PR as the station it describes.

## Stations

| Station | Where it lives |
|---|---|
| Intent | `factory/BACKLOG.md`. One row per task, with an acceptance check you can run. |
| Build | `factory/new-task.sh <slug>`: one worktree, one branch `task/<slug>`, one STATUS file. Hot files are claimed with `factory/claim.sh`. |
| Check | `scripts/check.sh`. One command. CI runs the same script. |
| Ship | A person merges. Agents never merge. |
| Learn | One line in `CLAUDE.md` whenever a session loses more than ten minutes to something it should have said. |

State that must be shared across worktrees (CLAIMS.md, STATUS files, REVIEW files) lives outside
the repo, in `~/.factory/arda` (override with `FACTORY_DIR`).

## Roles

- **Writer: Gemini CLI**, in `tmux` so it survives a closed terminal. Commits after every step
  and pushes the branch every few commits. A process that is reaped mid-step loses nothing that
  was committed.
- **Reviewer: a local Ollama model** (`factory/review.sh`). It did not write the code, which is
  the point. It must be a different family from the writer: Gemma is Google's, so it is refused.
- **QA: Claude.** Reproduces every claim before relaying it. See `factory/prompts/qa.md`.
- **Owner: you.** Direction, spending, secrets, merges.

## Rules every agent follows

1. Work only in your own worktree. Never edit the main checkout.
2. Claim a hot file before editing it, release it when the PR merges. Hot files: `README.md`,
   `CLAUDE.md`, `api/main.py`, `core/config.py`, `agents/finrod/agent.py`, `.github/workflows/ci.yml`.
3. Commit after every step. Small commits, clear messages.
4. Run `scripts/check.sh` before saying done. Quote its last lines. "Should pass" is not a result.
5. Write what you are running into `STATUS-<slug>.md`.
6. Mock by default. No network calls in the default test path.
7. Never commit secrets or `.env*`. Never write a key to a file.
8. No em dashes in files you write.
9. Do not merge, delete remote branches, or change CI without a backlog row that says to.

## The loop

Take the first TODO row. Do it in a worktree. Gate, review, QA, human merge, mark DONE. A row
that cannot be finished is marked BLOCKED with the specific reason, and work moves to the next
row. A finished agent run is not progress. A DONE row is.
