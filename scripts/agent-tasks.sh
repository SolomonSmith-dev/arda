#!/usr/bin/env bash
# List active tasks and blocked tasks (with their blockers).
# Usage: agent-tasks.sh
set -euo pipefail
# shellcheck source=scripts/lib.sh
source "$(dirname "${BASH_SOURCE[0]}")/lib.sh"
require_tools yq

[[ $# -eq 0 ]] || usage "agent-tasks.sh"
active=()
blocked=()
for f in "$(agent_home)"/.agent/tasks/*.yaml; do
  [[ -e "$f" && "$(basename "$f")" != _* ]] || continue
  line="$(yq -r '[.id, .owner, .status, .title] | join("\t")' "$f")"
  IFS=$'\t' read -r id owner status title <<< "$line"
  blockers="$(yq -r '(.blockers // []) | join("; ")' "$f")"
  row="$(printf '%-7s %-9s %-18s %s' "$id" "$owner" "$status" "$title")"
  if [[ "$status" == blocked || -n "$blockers" ]]; then
    blocked+=("$row -- blockers: ${blockers:-none listed}")
  elif [[ " assigned in_progress ready_for_review changes_requested verified " == *" $status "* ]]; then
    active+=("$row")
  fi
done

echo "ACTIVE"
if [[ ${#active[@]} -gt 0 ]]; then printf '  %s\n' "${active[@]}"; else echo "  (none)"; fi
echo "BLOCKED"
if [[ ${#blocked[@]} -gt 0 ]]; then printf '  %s\n' "${blocked[@]}"; else echo "  (none)"; fi
