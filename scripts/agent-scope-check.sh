#!/usr/bin/env bash
# Merge gate: every file a task branch changed must sit inside the task's paths.
# Usage: agent-scope-check.sh <task-id>   Exit 0 in scope, 1 out of scope.
set -euo pipefail
# shellcheck source=scripts/lib.sh
source "$(dirname "${BASH_SOURCE[0]}")/lib.sh"
require_tools git yq

[[ $# -eq 1 ]] || usage "agent-scope-check.sh <task-id>"
task="$1"
home="$(agent_home)"
branch="agent/$task"
[[ -f "$(task_file "$task")" ]] || die "no task file for $task"
git -C "$home" show-ref --verify --quiet "refs/heads/$branch" || die "no branch $branch"

base="$(git -C "$home" merge-base "$(merge_ref)" "$branch")"
changed="$(git -C "$home" diff --name-only "$base" "$branch")"
[[ -n "$changed" ]] || { echo "no changes on $branch"; exit 0; }
allowed="$(yq -r '.paths[]' "$(task_file "$task")")"

failed=0
while IFS= read -r f; do
  case "$f" in
    .claude/*|.agent/state/*|.agent/logs/*)
      echo "FAIL $f: local tooling state must never be committed"; failed=1; continue ;;
  esac
  ok=0
  while IFS= read -r p; do
    p="${p%/}"
    [[ "$f" == "$p" || "$f" == "$p"/* ]] && { ok=1; break; }
  done <<< "$allowed"
  if [[ $ok == 1 ]]; then echo "ok   $f"; else echo "FAIL $f: outside task paths"; failed=1; fi
done <<< "$changed"
exit "$failed"
