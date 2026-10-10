#!/usr/bin/env bats

setup() {
  ROOT="$(cd "$BATS_TEST_DIRNAME/../.." && pwd)"
  V="$ROOT/scripts/task-validate.sh"
  FX="$BATS_TEST_DIRNAME/fixtures"
  export AGENT_ROLES_DIR="$FX/roster/roles"
}

@test "valid task passes" {
  run "$V" "$FX/good/T-900.yaml"
  [ "$status" -eq 0 ]
  [[ "$output" == *"ok T-900.yaml"* ]]
}

@test "missing field, bad status, unknown owner, empty paths are each reported" {
  run "$V" "$FX/bad/T-901.yaml"
  [ "$status" -eq 1 ]
  [[ "$output" == *"missing required field 'acceptance'"* ]]
  [[ "$output" == *"invalid status 'doing'"* ]]
  [[ "$output" == *"unknown owner 'wizard'"* ]]
  [[ "$output" == *"'paths' must be a non-empty list"* ]]
}

@test "id must match filename" {
  run "$V" "$FX/bad/T-902.yaml"
  [ "$status" -eq 1 ]
  [[ "$output" == *"id 'T-999' does not match filename"* ]]
}

@test "invalid YAML is reported, not crashed on" {
  run "$V" "$FX/bad/T-903.yaml"
  [ "$status" -eq 1 ]
  [[ "$output" == *"not valid YAML"* ]]
}

@test "one bad file among good ones fails the run" {
  run "$V" "$FX/good/T-900.yaml" "$FX/bad/T-902.yaml"
  [ "$status" -eq 1 ]
  [[ "$output" == *"ok T-900.yaml"* ]]
}

@test "no arguments is a usage error" {
  run "$V"
  [ "$status" -eq 2 ]
  [[ "$output" == *"usage:"* ]]
}

@test "run-checks tasks check fails on a bad tasks dir" {
  AGENT_TASKS_DIR="$FX/bad" run "$ROOT/scripts/run-checks.sh" tasks
  [ "$status" -eq 1 ]
  [[ "$output" == *"FAIL tasks"* ]]
}

@test "run-checks tasks check passes on a good tasks dir" {
  AGENT_TASKS_DIR="$FX/good" run "$ROOT/scripts/run-checks.sh" tasks
  [ "$status" -eq 0 ]
  [[ "$output" == *"PASS tasks"* ]]
}

@test "run-checks rejects an unknown check name" {
  run "$ROOT/scripts/run-checks.sh" nonsense
  [ "$status" -eq 2 ]
}
