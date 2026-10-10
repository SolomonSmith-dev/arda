#!/usr/bin/env bash
# Start the control session, or one worker agent on one task.
# Usage: agent-start.sh                    start session: control + observer windows
#        agent-start.sh <role> <task-id>   start a worker in its own worktree
# Env:   AGENT_ESCALATE=1 uses the escalation model from .agent/models.yaml.
# Exit 0 started, 1 error, 2 usage, 3 policy refusal.
set -euo pipefail
# shellcheck source=scripts/lib.sh
source "$(dirname "${BASH_SOURCE[0]}")/lib.sh"
require_tools git tmux yq

home="$(agent_home)"
root="$(script_root)"

set_window_opts() {
  local role="$2" task="$3" model="$4" t="=$AGENT_SESSION:$1"
  tmux_cmd set-option -w -t "$t" remain-on-exit on
  tmux_cmd set-option -w -t "$t" @role "$role"
  tmux_cmd set-option -w -t "$t" @task "$task"
  tmux_cmd set-option -w -t "$t" @model "$model"
  tmux_cmd set-option -w -t "$t" @started "$(date +%s)"
}

start_session() {
  if session_exists; then
    echo "session $AGENT_SESSION already running"
    return
  fi
  local model effort idx
  model="$(role_model control)"
  effort="$(role_effort control)"
  idx="$(tmux_cmd new-session -d -P -F '#{window_index}' -s "$AGENT_SESSION" -n control -c "$home" \
    "$AGENT_CLAUDE_CMD" --model "$model" --effort "$effort" --name control)"
  set_window_opts "$idx" control "" "$model"
  # shellcheck disable=SC2016  # $0 is expanded by the inner bash, not here
  idx="$(tmux_cmd new-window -d -P -F '#{window_index}' -t "=$AGENT_SESSION:" -n observer -c "$home" \
    bash -c 'while true; do clear; "$0"; sleep 5; done' "$root/scripts/agent-status.sh")"
  set_window_opts "$idx" observer "" ""
  log_activity control - session-start "$model/$effort"
  echo "started session $AGENT_SESSION (control: $model/$effort, observer)"
}

start_worker() {
  local role="$1" task="$2" model effort tf owner status dep ds count conflicts
  model="$(role_model "$role")"
  effort="$(role_effort "$role")"
  [[ -n "$model" ]] || usage "agent-start.sh <role> <task-id> (unknown role '$role'; see .agent/models.yaml)"
  [[ "$role" != control ]] || usage "control is started by agent-start.sh with no arguments"

  tf="$(task_file "$task")"
  [[ -f "$tf" ]] || refuse "no task file $tf"
  # Precedence: escalation > the task's own model/effort > the role default.
  [[ -n "$(task_get "$task" .model)" ]] && model="$(task_get "$task" .model)"
  [[ -n "$(task_get "$task" .effort)" ]] && effort="$(task_get "$task" .effort)"
  if [[ "${AGENT_ESCALATE:-0}" == 1 ]]; then
    model="$(yq -r '.escalation.model' "$(models_file)")"
    effort="$(yq -r '.escalation.effort' "$(models_file)")"
  fi
  "$root/scripts/task-validate.sh" "$tf" >/dev/null || refuse "task $task is invalid; run scripts/task-validate.sh $tf"
  owner="$(task_get "$task" .owner)"
  [[ "$owner" == "$role" ]] || refuse "task $task is owned by '$owner', not '$role'"
  status="$(task_get "$task" .status)"
  case "$status" in
    assigned|in_progress|changes_requested) ;;
    *) refuse "task $task is '$status'; only assigned, in_progress or changes_requested tasks can start" ;;
  esac
  for dep in $(yq -r '.depends_on // [] | .[]' "$tf"); do
    ds=missing
    [[ -f "$(task_file "$dep")" ]] && ds="$(task_get "$dep" .status)"
    [[ "$ds" == complete ]] || refuse "dependency $dep is '$ds', not complete"
  done

  session_exists || refuse "session $AGENT_SESSION is not running; run scripts/agent-start.sh with no arguments first"
  if list_windows | awk -F'\t' -v t="$task" '$4 == t && $6 != "1"' | grep -q .; then
    refuse "task $task already has a live window"
  fi
  # A dead window for this task (agent exited) is replaced, not counted.
  list_windows | awk -F'\t' -v t="$task" '$4 == t {print $1}' | while read -r idx; do
    tmux_cmd kill-window -t "=$AGENT_SESSION:$idx"
  done
  count="$(claude_count)"
  (( count < AGENT_MAX_CLAUDE )) || refuse "$count of $AGENT_MAX_CLAUDE Claude sessions are live; stop one first (scripts/agent-stop.sh)"
  conflicts="$(lock_conflicts "$task")"
  [[ -z "$conflicts" ]] || refuse "path lock conflict (other-task their-path our-path):
$conflicts"

  local wt branch="agent/$task" made_wt=0 made_branch=0 made_lock=0
  wt="$(worktree_root)/$task"
  if [[ -d "$wt" ]]; then
    [[ "$(git -C "$wt" rev-parse --abbrev-ref HEAD)" == "$branch" ]] || refuse "$wt exists but is not on $branch"
  else
    mkdir -p "$(worktree_root)"
    if git -C "$home" show-ref --verify --quiet "refs/heads/$branch"; then
      git -C "$home" worktree add -q "$wt" "$branch"
    else
      git -C "$home" worktree add -q -b "$branch" "$wt" "$(base_ref)"
      made_branch=1
    fi
    made_wt=1
  fi
  if ! lock_exists "$task"; then
    lock_add "$task" "$role" "$wt"
    made_lock=1
  fi
  rollback() {
    [[ $made_lock == 1 ]] && lock_release "$task"
    [[ $made_wt == 1 ]] && git -C "$home" worktree remove --force "$wt"
    [[ $made_branch == 1 ]] && git -C "$home" branch -q -D "$branch"
    echo "error: start failed; rolled back" >&2
  }
  trap rollback ERR

  local report="$home/.agent/reports/$task.md" prompt idx name="$role-$task"
  prompt="You are the $role agent for task $task. Read AGENTS.md, then $home/.agent/roles/$role.md and $tf. Work only in this worktree ($wt) and only on the paths the task lists. When done: run the task's validation commands and scripts/run-checks.sh, commit on branch $branch (never push), write your report to $report using $home/.agent/reports/_template.md, then say READY_FOR_REVIEW and stop."
  idx="$(tmux_cmd new-window -d -P -F '#{window_index}' -t "=$AGENT_SESSION:" -n "$name" -c "$wt" \
    "$AGENT_CLAUDE_CMD" --model "$model" --effort "$effort" --name "$name" "$prompt")"
  set_window_opts "$idx" "$role" "$task" "$model"
  trap - ERR

  [[ "$status" == assigned ]] && task_set_status "$task" in_progress
  log_activity "$role" "$task" start "$model/$effort $wt"
  echo "started $name ($model/$effort) in $wt"
}

case $# in
  0) start_session ;;
  2) start_worker "$1" "$2" ;;
  *) usage "agent-start.sh | agent-start.sh <role> <task-id>" ;;
esac
