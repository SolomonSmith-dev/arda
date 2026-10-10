#!/usr/bin/env bash
# shellcheck disable=SC2317,SC2329  # check_* functions are called indirectly as "check_$c" (code differs by shellcheck version)
# Run project checks. Every check runs even if an earlier one fails.
# Usage: run-checks.sh [ruff mypy pytest shellcheck bats tasks]...   (default: all)
# Exit 0 all passed, 1 any failed, 2 unknown check.
# Rendered by agent-crew from the approved plan: stack checks first, then the agent-system checks.
set -uo pipefail
# shellcheck source=scripts/lib.sh
source "$(dirname "${BASH_SOURCE[0]}")/lib.sh"

root="$(script_root)"
cd "$root" || die "cannot cd to $root"
all="ruff mypy pytest shellcheck bats tasks"
checks="${*:-$all}"
for c in $checks; do
  [[ " $all " == *" $c "* ]] || usage "run-checks.sh [$all]... (unknown check '$c')"
done

check_ruff() { uv run ruff check .; }
check_mypy() { uv run mypy agents core; }
check_pytest() { uv run pytest tests/ -q; }
check_shellcheck() { shellcheck -x scripts/*.sh; }
check_bats() { bats tests/scripts; }
check_tasks() {
  local dir="${AGENT_TASKS_DIR:-$root/.agent/tasks}" files=()
  local f
  for f in "$dir"/*.yaml; do
    [[ -e "$f" && "$(basename "$f")" != _* ]] && files+=("$f")
  done
  [[ ${#files[@]} -eq 0 ]] && { echo "no task files in $dir"; return 0; }
  "$root/scripts/task-validate.sh" "${files[@]}"
}

failed=0
summary=()
for c in $checks; do
  echo "== $c"
  if "check_$c"; then
    summary+=("PASS $c")
  else
    summary+=("FAIL $c")
    failed=1
  fi
done
echo "=="
printf '%s\n' "${summary[@]}"
exit "$failed"
