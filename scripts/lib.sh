# shellcheck shell=bash
# Shared helpers for scripts/agent-*.sh. Source it; do not execute it.
# Callers set their own shell options.

# shellcheck disable=SC2034  # constants are used by the scripts that source this file
TASK_STATES="planned assigned in_progress blocked ready_for_review changes_requested verified complete cancelled"
AGENT_SESSION="${AGENT_SESSION:-engineering-control}"
AGENT_MAX_CLAUDE="${AGENT_MAX_CLAUDE:-3}"
AGENT_CLAUDE_CMD="${AGENT_CLAUDE_CMD:-claude}"

# Exit codes: 1 check failed, 2 usage error, 3 policy refusal.
usage() { echo "usage: $*" >&2; exit 2; }
refuse() { echo "refused: $*" >&2; exit 3; }
die() { echo "error: $*" >&2; exit 1; }

# Directory holding this script's checkout (the worktree it runs in).
script_root() { cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd; }

# The main checkout. State lives only there, never in a worktree copy.
agent_home() {
  if [[ -n "${AGENT_HOME:-}" ]]; then
    echo "$AGENT_HOME"
    return
  fi
  local common
  common="$(git -C "$(script_root)" rev-parse --path-format=absolute --git-common-dir)"
  dirname "$common"
}

worktree_root() { echo "${AGENT_WT_ROOT:-$(agent_home)-wt}"; }

require_tools() {
  local t
  for t in "$@"; do
    command -v "$t" >/dev/null 2>&1 || die "'$t' not found; install it (brew install $t)"
  done
}

# tmux on a private socket when AGENT_TMUX_SOCKET is set (used by tests).
tmux_cmd() {
  if [[ -n "${AGENT_TMUX_SOCKET:-}" ]]; then
    tmux -L "$AGENT_TMUX_SOCKET" "$@"
  else
    tmux "$@"
  fi
}

log_activity() {
  local role="$1" task="$2" action="$3" detail="${4:-}"
  local dir
  dir="$(agent_home)/.agent/logs"
  mkdir -p "$dir"
  printf '%s %s %s %s %s\n' "$(date -u +%Y-%m-%dT%H:%M:%SZ)" "$role" "$task" "$action" "$detail" >> "$dir/activity.log"
}

task_file() { echo "$(agent_home)/.agent/tasks/$1.yaml"; }

# task_get <id> <yq-path>, e.g. task_get T-001 .status
task_get() { yq -r "$2 // \"\"" "$(task_file "$1")"; }

task_set_status() { S="$2" yq -i '.status = strenv(S)' "$(task_file "$1")"; }

# config_get <key> <default>: value from the main checkout's .agent/config.env
# (written by the agent-crew installer), else the default.
config_get() {
  local f v=""
  f="$(agent_home)/.agent/config.env"
  [[ -f "$f" ]] && v="$(sed -n "s/^$1=//p" "$f" | tail -n 1)"
  echo "${v:-$2}"
}

# Env wins, then .agent/config.env, then the defaults.
base_ref() { echo "${AGENT_BASE_REF:-$(config_get AGENT_BASE_REF origin/main)}"; }   # new worktrees branch from here
merge_ref() { echo "${AGENT_MERGE_REF:-$(config_get AGENT_MERGE_REF main)}"; }      # "merged" means reachable from here

models_file() { echo "${AGENT_MODELS_FILE:-$(script_root)/.agent/models.yaml}"; }
roles_dir() { echo "${AGENT_ROLES_DIR:-$(script_root)/.agent/roles}"; }
role_model() { yq -r ".roles.\"$1\".model // \"\"" "$(models_file)"; }
role_effort() { yq -r ".roles.\"$1\".effort // \"\"" "$(models_file)"; }

state_dir() {
  local d
  d="$(agent_home)/.agent/state"
  mkdir -p "$d"
  echo "$d"
}

locks_file() {
  local f
  f="$(state_dir)/ownership.yaml"
  [[ -f "$f" ]] || echo 'locks: []' > "$f"
  echo "$f"
}

# True when one path equals, or is inside, the other ("src/ui" vs "src/ui/app.py").
paths_overlap() {
  local a="${1%/}" b="${2%/}"
  [[ "$a" == "$b" || "$a" == "$b"/* || "$b" == "$a"/* ]]
}

lock_exists() { T="$1" yq -e '.locks[] | select(.task == strenv(T))' "$(locks_file)" >/dev/null 2>&1; }

lock_add() {
  T="$1" R="$2" W="$3" F="$(task_file "$1")" N="$(date -u +%Y-%m-%dT%H:%M:%SZ)" \
    yq -i '.locks += [{"task": strenv(T), "agent": strenv(R), "paths": load(strenv(F)).paths, "worktree": strenv(W), "since": strenv(N)}]' "$(locks_file)"
}

lock_release() { T="$1" yq -i 'del(.locks[] | select(.task == strenv(T)))' "$(locks_file)"; }

# Prints "<other-task> <their-path> <our-path>" for every overlap with another task's lock.
lock_conflicts() {
  local task="$1" other theirs ours
  local ours_list
  ours_list="$(yq -r '.paths[]' "$(task_file "$task")")"
  # shellcheck disable=SC2016  # $t in the yq expression below is a yq variable
  while IFS=$'\t' read -r other theirs; do
    [[ -z "$other" || "$other" == "$task" ]] && continue
    while IFS= read -r ours; do
      paths_overlap "$ours" "$theirs" && echo "$other $theirs $ours"
    done <<< "$ours_list"
  done < <(yq -r '.locks[] | .task as $t | .paths[] | $t + "\t" + .' "$(locks_file)")
  return 0
}

session_exists() { tmux_cmd has-session -t "=$AGENT_SESSION" 2>/dev/null; }

# One line per window: index, name, role, task, model, pane_dead, started (tab-separated).
list_windows() {
  session_exists || return 0
  tmux_cmd list-windows -t "=$AGENT_SESSION" \
    -F $'#{window_index}\t#{window_name}\t#{@role}\t#{@task}\t#{@model}\t#{pane_dead}\t#{@started}'
}

# Live Claude sessions: windows that carry a model and whose pane has not exited.
claude_count() { list_windows | awk -F'\t' '$5 != "" && $6 != "1"' | wc -l | tr -d ' '; }

# resolve_window <task-id|window-name|role>: prints the window index, or exits.
resolve_window() {
  local key="$1" matches
  matches="$(list_windows | awk -F'\t' -v k="$key" '$4 == k')"
  [[ -z "$matches" ]] && matches="$(list_windows | awk -F'\t' -v k="$key" '$2 == k')"
  [[ -z "$matches" ]] && matches="$(list_windows | awk -F'\t' -v k="$key" '$3 == k')"
  [[ -z "$matches" ]] && die "no agent window matches '$key' (see scripts/agent-status.sh)"
  if [[ "$(wc -l <<< "$matches" | tr -d ' ')" -gt 1 ]]; then
    usage "'$key' matches several windows: $(cut -f2 <<< "$matches" | tr '\n' ' ')- use a task id"
  fi
  cut -f1 <<< "$matches"
}

window_opt() { tmux_cmd show-options -wqv -t "=$AGENT_SESSION:$1" "$2"; }
