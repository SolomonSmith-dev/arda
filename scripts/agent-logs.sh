#!/usr/bin/env bash
# Save each agent pane's last 2000 lines to .agent/logs/, then show recent activity.
# Usage: agent-logs.sh [task-id|window-name]   default: every window
set -euo pipefail
# shellcheck source=scripts/lib.sh
source "$(dirname "${BASH_SOURCE[0]}")/lib.sh"
require_tools tmux

[[ $# -le 1 ]] || usage "agent-logs.sh [task-id|window-name]"
dir="$(agent_home)/.agent/logs"
mkdir -p "$dir"
stamp="$(date -u +%Y%m%dT%H%M%SZ)"

if session_exists; then
  if [[ $# -eq 1 ]]; then
    idxs="$(resolve_window "$1")"
  else
    idxs="$(list_windows | cut -f1)"
  fi
  for idx in $idxs; do
    name="$(tmux_cmd display-message -p -t "=$AGENT_SESSION:$idx" '#{window_name}')"
    out="$dir/$name-$stamp.log"
    tmux_cmd capture-pane -p -J -S -2000 -t "=$AGENT_SESSION:$idx" > "$out"
    echo "saved $out"
  done
else
  echo "session $AGENT_SESSION is not running; no panes to capture"
fi

echo "== recent activity"
if [[ -f "$dir/activity.log" ]]; then tail -n 20 "$dir/activity.log"; else echo "(none)"; fi
