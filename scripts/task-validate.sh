#!/usr/bin/env bash
# Validate task files against the schema in .agent/tasks/_template.yaml.
# Usage: task-validate.sh <task.yaml>...   Exit 0 all valid, 1 any invalid, 2 usage.
set -euo pipefail
# shellcheck source=scripts/lib.sh
source "$(dirname "${BASH_SOURCE[0]}")/lib.sh"

[[ $# -gt 0 ]] || usage "task-validate.sh <task.yaml>..."
require_tools yq

roles_dir="$(roles_dir)"
required="id title owner status description paths acceptance validation reviewers"
failed=0

validate() {
  local f="$1" name errors=() k status owner id
  name="$(basename "$f")"
  if ! yq -e '.' "$f" >/dev/null 2>&1; then
    echo "FAIL $name: not valid YAML"
    return 1
  fi
  for k in $required; do
    yq -e ".$k" "$f" >/dev/null 2>&1 || errors+=("missing required field '$k'")
  done
  for k in paths acceptance validation reviewers; do
    if yq -e ".$k" "$f" >/dev/null 2>&1 && [[ "$(yq ".$k | length" "$f")" -eq 0 ]]; then
      errors+=("'$k' must be a non-empty list")
    fi
  done
  status="$(yq -r '.status // ""' "$f")"
  if [[ -n "$status" && " $TASK_STATES " != *" $status "* ]]; then
    errors+=("invalid status '$status'")
  fi
  owner="$(yq -r '.owner // ""' "$f")"
  if [[ -n "$owner" && ! -f "$roles_dir/$owner.md" ]]; then
    errors+=("unknown owner '$owner' (no .agent/roles/$owner.md)")
  fi
  id="$(yq -r '.id // ""' "$f")"
  if [[ -n "$id" && "$id.yaml" != "$name" ]]; then
    errors+=("id '$id' does not match filename")
  fi
  if [[ ${#errors[@]} -gt 0 ]]; then
    for k in "${errors[@]}"; do echo "FAIL $name: $k"; done
    return 1
  fi
  echo "ok $name"
}

for f in "$@"; do
  validate "$f" || failed=1
done
exit "$failed"
