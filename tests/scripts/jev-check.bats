#!/usr/bin/env bats

setup() {
  ROOT="$(cd "$BATS_TEST_DIRNAME/../.." && pwd)"
  J="$ROOT/scripts/jev-check.sh"
  FX="$BATS_TEST_DIRNAME/fixtures"
  export STUB_IN="$BATS_TEST_TMPDIR/jev-stdin" STUB_ARGS="$BATS_TEST_TMPDIR/jev-args"
  export AGENT_JEV_CMD="$BATS_TEST_TMPDIR/jev-stub"
  cat > "$AGENT_JEV_CMD" <<'STUB'
#!/usr/bin/env bash
printf '%s\n' "$@" > "$STUB_ARGS"
cat > "$STUB_IN"
echo "[jev] 1 items · 0.4s wall · 0 errors" >&2   # real jev.py writes this summary to stderr
[[ -n "${STUB_JEV_EXIT:-}" ]] && { echo "error: stub failure" >&2; exit "$STUB_JEV_EXIT"; }
echo "$STUB_JEV_ROW"
STUB
  chmod +x "$AGENT_JEV_CMD"
}

@test "report missing a field fails without calling Jev" {
  run "$J" report "$FX/report-missing.md"
  [ "$status" -eq 1 ]
  [[ "$output" == *"missing field 'Risks'"* ]]
  [ ! -f "$STUB_IN" ]
}

@test "report with an empty field fails without calling Jev" {
  run "$J" report "$FX/report-empty.md"
  [ "$status" -eq 1 ]
  [[ "$output" == *"empty field 'Blockers'"* ]]
  [ ! -f "$STUB_IN" ]
}

@test "high evidence probability passes and sends status and test results" {
  export STUB_JEV_ROW='{"_id": "0", "evidence": 0.95}'
  run "$J" report "$FX/report-good.md"
  [ "$status" -eq 0 ]
  [[ "$output" == *"verdict: pass"* ]]
  grep -q '"status": *"ready_for_review"' "$STUB_IN"
  grep -q '12 passed in 0.03s' "$STUB_IN"
  grep -q 'report-evidence.json' "$STUB_ARGS"
}

@test "low evidence probability fails" {
  export STUB_JEV_ROW='{"_id": "0", "evidence": 0.1}'
  run "$J" report "$FX/report-good.md"
  [ "$status" -eq 1 ]
  [[ "$output" == *"verdict: fail"* ]]
}

@test "middle probability escalates" {
  export STUB_JEV_ROW='{"_id": "0", "evidence": 0.5}'
  run "$J" report "$FX/report-good.md"
  [ "$status" -eq 4 ]
  [[ "$output" == *"verdict: escalate"* ]]
}

@test "Jev command failure escalates, never passes" {
  export STUB_JEV_EXIT=1
  run "$J" report "$FX/report-good.md"
  [ "$status" -eq 4 ]
  [[ "$output" == *"verdict: escalate"*"Jev unavailable"* ]]
}

@test "Jev row with an error escalates" {
  export STUB_JEV_ROW='{"_id": "0", "_error": "HTTP 402"}'
  run "$J" report "$FX/report-good.md"
  [ "$status" -eq 4 ]
  [[ "$output" == *"HTTP 402"* ]]
}

@test "failure labelling prints the label and confidence" {
  printf 'E   DiceError: invalid dice expression\nFAILED tests/x.py\n' > "$BATS_TEST_TMPDIR/fail.log"
  export STUB_JEV_ROW='{"_id": "0", "cause": "test-bug", "cause_conf": 0.83}'
  run "$J" failure "$BATS_TEST_TMPDIR/fail.log"
  [ "$status" -eq 0 ]
  [[ "$output" == *"cause: test-bug (confidence 0.83)"* ]]
  grep -q 'DiceError' "$STUB_IN"
}

@test "failure labelling is advisory: Jev down is not an error" {
  printf 'boom\n' > "$BATS_TEST_TMPDIR/fail.log"
  export STUB_JEV_EXIT=1
  run "$J" failure "$BATS_TEST_TMPDIR/fail.log"
  [ "$status" -eq 0 ]
  [[ "$output" == *"cause: unavailable"* ]]
}

@test "unknown subcommand is a usage error" {
  run "$J" nonsense x
  [ "$status" -eq 2 ]
}
