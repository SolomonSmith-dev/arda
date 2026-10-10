#!/usr/bin/env bash
# Attach to an agent window (switches client when already inside tmux).
# Usage: agent-attach.sh [task-id|window-name]   default: control
set -euo pipefail
# shellcheck source=scripts/lib.sh
source "$(dirname "${BASH_SOURCE[0]}")/lib.sh"
require_tools tmux

[[ $# -le 1 ]] || usage "agent-attach.sh [task-id|window-name]"
session_exists || die "session $AGENT_SESSION is not running; run scripts/agent-start.sh"
target="=$AGENT_SESSION:$(resolve_window "${1:-control}")"
if [[ "${AGENT_ATTACH_DRY_RUN:-0}" == 1 ]]; then
  echo "$target"
elif [[ -n "${TMUX:-}" ]]; then
  tmux_cmd switch-client -t "$target"
else
  tmux_cmd attach-session -t "$target"
fi
