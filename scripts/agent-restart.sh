#!/usr/bin/env bash
# Restart the agent on a task, keeping its lock and worktree.
# Usage: agent-restart.sh <task-id> [--escalate]
#   --escalate  restart on the escalation model (.agent/models.yaml); use after two
#               failed reviews or one failed debugging pass.
set -euo pipefail
# shellcheck source=scripts/lib.sh
source "$(dirname "${BASH_SOURCE[0]}")/lib.sh"
require_tools tmux yq

[[ $# -ge 1 && $# -le 2 ]] || usage "agent-restart.sh <task-id> [--escalate]"
task="$1"
escalate=0
if [[ $# -eq 2 ]]; then
  [[ "$2" == --escalate ]] || usage "agent-restart.sh <task-id> [--escalate]"
  escalate=1
fi
[[ -f "$(task_file "$task")" ]] || die "no task file for $task"
role="$(task_get "$task" .owner)"

idx="$(list_windows | awk -F'\t' -v t="$task" '$4 == t {print $1; exit}')"
if [[ -n "$idx" ]]; then
  tmux_cmd kill-window -t "=$AGENT_SESSION:$idx"
fi
log_activity "$role" "$task" restart "escalate=$escalate"
AGENT_ESCALATE="$escalate" exec "$(dirname "${BASH_SOURCE[0]}")/agent-start.sh" "$role" "$task"
