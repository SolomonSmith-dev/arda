#!/usr/bin/env bash
# Stop one agent window, or everything.
# Usage: agent-stop.sh <task-id|window-name>   stop one window
#        agent-stop.sh --all                   stop every agent and the session
# Locks and worktrees are released only for complete or cancelled tasks, and a
# worktree is removed only when clean (and, for complete tasks, merged).
set -euo pipefail
# shellcheck source=scripts/lib.sh
source "$(dirname "${BASH_SOURCE[0]}")/lib.sh"
require_tools git tmux yq

home="$(agent_home)"

# Release lock and worktree for a finished task; otherwise keep both and say why.
cleanup_task() {
  local task="$1" status wt branch
  [[ -f "$(task_file "$task")" ]] || return 0
  status="$(task_get "$task" .status)"
  wt="$(worktree_root)/$task"
  branch="agent/$task"
  case "$status" in
    complete|cancelled) ;;
    *) echo "kept lock and worktree for $task (status $status)"; return 0 ;;
  esac
  lock_release "$task"
  [[ -d "$wt" ]] || return 0
  if [[ -n "$(git -C "$wt" status --porcelain)" ]]; then
    echo "kept worktree $wt: uncommitted changes"
  elif [[ "$status" == complete ]] && ! git -C "$home" merge-base --is-ancestor "$branch" "$(merge_ref)"; then
    echo "kept worktree $wt: $branch is not merged into $(merge_ref)"
  else
    git -C "$home" worktree remove "$wt"
    if [[ "$status" == complete ]]; then
      git -C "$home" branch -q -d "$branch"
      echo "removed worktree and branch for $task"
    else
      echo "removed worktree for $task (branch $branch kept)"
    fi
  fi
}

stop_window() {
  local idx="$1" role task
  role="$(window_opt "$idx" @role)"
  task="$(window_opt "$idx" @task)"
  tmux_cmd kill-window -t "=$AGENT_SESSION:$idx"
  log_activity "${role:-?}" "${task:--}" stop
  echo "stopped ${role:-window $idx}${task:+ ($task)}"
  [[ -n "$task" ]] && cleanup_task "$task"
  return 0
}

[[ $# -eq 1 ]] || usage "agent-stop.sh <task-id|window-name> | --all"
session_exists || die "session $AGENT_SESSION is not running"

if [[ "$1" == --all ]]; then
  list_windows | awk -F'\t' '$4 != "" {print $1}' | while read -r idx; do stop_window "$idx"; done
  tmux_cmd kill-session -t "=$AGENT_SESSION"
  log_activity control - session-stop
  echo "stopped session $AGENT_SESSION"
else
  stop_window "$(resolve_window "$1")"
fi
