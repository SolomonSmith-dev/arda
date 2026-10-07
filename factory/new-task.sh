#!/usr/bin/env bash
# Start a task: one git worktree, one branch, one STATUS file.
# Usage: factory/new-task.sh <slug> [base-branch=main]
# State lives OUTSIDE the repo (default ~/.factory/arda) so every worktree sees the same
# CLAIMS.md and STATUS files. Override with FACTORY_DIR.
set -euo pipefail

slug="${1:?usage: factory/new-task.sh <slug> [base-branch]}"
base="${2:-main}"
[[ "$slug" =~ ^[a-z0-9][a-z0-9-]*$ ]] || { echo "slug must be lowercase letters, digits, hyphens" >&2; exit 2; }

repo="$(git rev-parse --show-toplevel)"
fdir="${FACTORY_DIR:-$HOME/.factory/arda}"
wt="$fdir/wt/$slug"
mkdir -p "$fdir/wt"
touch "$fdir/CLAIMS.md"

git -C "$repo" fetch -q origin "$base"
git -C "$repo" worktree add -q -b "task/$slug" "$wt" "origin/$base"

cat > "$fdir/STATUS-$slug.md" <<STATUS
# STATUS: $slug
- branch: task/$slug
- worktree: $wt
- started: $(date -u +%Y-%m-%dT%H:%MZ)
- backlog row:
- now running:
- claims:
- done when: scripts/check.sh prints GATE PASS and the row's acceptance check passes
STATUS

echo "worktree: $wt"
echo "status:   $fdir/STATUS-$slug.md"
echo "next:     tmux new -s task-$slug -c $wt     # then start the writer and give it factory/prompts/writer.md"
