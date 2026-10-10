#!/usr/bin/env bash
# Cheap checks through Jev (typesafe/jev-1.13 on OpenRouter). Jev returns labels and
# probabilities only; an error or an unsure answer always escalates, never passes.
# Usage: jev-check.sh report <report.md>   exit 0 pass, 1 fail, 4 escalate
#        jev-check.sh failure <test.log>   advisory label, always exit 0
# Env:   AGENT_JEV_CMD replaces the jev.py call (used by tests).
set -euo pipefail
# shellcheck source=scripts/lib.sh
source "$(dirname "${BASH_SOURCE[0]}")/lib.sh"
require_tools jq

presets="$(script_root)/.agent/jev"
jev_py="$HOME/.claude/skills/claude-x-jev/scripts/jev.py"
fields=("Task ID" "Agent" "Status" "Summary" "Files changed" "Commands run" "Tests run"
  "Test results" "Security impact" "Assumptions" "Risks" "Blockers" "Handoff" "Recommended next action")

jev() {
  if [[ -n "${AGENT_JEV_CMD:-}" ]]; then
    "$AGENT_JEV_CMD" "$@"
  else
    [[ -f "$jev_py" ]] || { echo "error: $jev_py not found" >&2; return 1; }
    python3 -I "$jev_py" "$@"
  fi
}

# field_value <file> <label>: value on the label's line plus any continuation lines.
field_value() {
  awk -v label="$2" '
    BEGIN { n = split("Task ID|Agent|Status|Summary|Files changed|Commands run|Tests run|Test results|Security impact|Assumptions|Risks|Blockers|Handoff|Recommended next action", L, "|") }
    function is_label(s,   i) { for (i = 1; i <= n; i++) if (index(s, L[i] ":") == 1) return 1; return 0 }
    index($0, label ":") == 1 { on = 1; v = substr($0, length(label) + 2); next }
    on && is_label($0) { on = 0 }
    on { v = v "\n" $0 }
    END { gsub(/^[ \t\n]+|[ \t\n]+$/, "", v); printf "%s", v }
  ' "$1"
}

has_field() { grep -q "^$2:" "$1"; }

check_report() {
  local f="$1" label p err out errf
  errf="$(mktemp)"
  trap 'rm -f "$errf"' RETURN
  for label in "${fields[@]}"; do
    has_field "$f" "$label" || { echo "FAIL missing field '$label'"; exit 1; }
    [[ -n "$(field_value "$f" "$label")" ]] || { echo "FAIL empty field '$label' (write 'none' if nothing applies)"; exit 1; }
  done
  echo "ok all ${#fields[@]} fields present"

  if ! out="$(jq -n --arg status "$(field_value "$f" Status)" --arg tests_run "$(field_value "$f" "Tests run")" \
      --arg test_results "$(field_value "$f" "Test results")" '{status: $status, tests_run: $tests_run, test_results: $test_results}' \
      | jev ask --preset "$presets/report-evidence.json" --input - 2>"$errf")"; then
    echo "verdict: escalate (Jev unavailable: $(tail -n 1 "$errf"))"
    exit 4
  fi
  err="$(jq -r '._error // empty' <<< "$out" 2>/dev/null || echo "unparseable Jev output")"
  [[ -z "$err" ]] || { echo "verdict: escalate (Jev error: $err)"; exit 4; }
  p="$(jq -r '.evidence // empty' <<< "$out")"
  [[ -n "$p" ]] || { echo "verdict: escalate (no evidence answer in Jev output)"; exit 4; }
  if awk -v p="$p" 'BEGIN { exit !(p >= 0.8) }'; then
    echo "verdict: pass (p=$p that test results support the claimed status)"
  elif awk -v p="$p" 'BEGIN { exit !(p < 0.3) }'; then
    echo "verdict: fail (p=$p; test results do not support the claimed status)"
    exit 1
  else
    echo "verdict: escalate (p=$p; read the report yourself)"
    exit 4
  fi
}

label_failure() {
  local f="$1" out label conf
  if ! out="$(tail -c 6000 "$f" | jq -Rs '{output: .}' | jev ask --preset "$presets/test-failure.json" --input - 2>/dev/null)" \
      || [[ -n "$(jq -r '._error // empty' <<< "$out" 2>/dev/null)" ]]; then
    echo "cause: unavailable (Jev did not answer; read the log)"
    return 0
  fi
  label="$(jq -r '.cause // "unknown"' <<< "$out")"
  conf="$(jq -r '.cause_conf // "?"' <<< "$out")"
  echo "cause: $label (confidence $conf)"
  if awk -v c="$conf" 'BEGIN { exit !(c + 0 < 0.7) }'; then echo "low confidence: treat as a hint only"; fi
}

[[ $# -eq 2 && ( "$1" == report || "$1" == failure ) ]] || usage "jev-check.sh report <report.md> | failure <test.log>"
[[ -f "$2" ]] || die "no such file: $2"
case "$1" in
  report) check_report "$2" ;;
  failure) label_failure "$2" ;;
  *) usage "jev-check.sh report <report.md> | failure <test.log>" ;;
esac
