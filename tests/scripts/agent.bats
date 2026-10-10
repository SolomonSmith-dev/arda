#!/usr/bin/env bats

load helpers

setup() { setup_agent_env; }
teardown() { teardown_agent_env; }

start_session() { run "$S/agent-start.sh"; [ "$status" -eq 0 ]; wait_for_stub 1; }

@test "start with no args opens control (opus/high) and observer" {
  start_session
  [[ "$(windows)" == *control*observer* ]]
  grep -qx -- claude-opus-5-5 "$STUB_LOG"
  grep -qx -- high "$STUB_LOG"
}

@test "start worker: worktree, branch, lock, window, status, model, prompt, log" {
  start_session
  make_task T-101 alpha assigned src/a/
  run "$S/agent-start.sh" alpha T-101
  [ "$status" -eq 0 ]
  wait_for_stub 2
  [ "$(git -C "$AGENT_WT_ROOT/T-101" rev-parse --abbrev-ref HEAD)" = agent/T-101 ]
  yq -e '.locks[] | select(.task == "T-101")' "$AGENT_HOME/.agent/state/ownership.yaml"
  [[ "$(windows)" == *alpha-T-101* ]]
  [ "$(yq -r .status "$AGENT_HOME/.agent/tasks/T-101.yaml")" = in_progress ]
  grep -qx -- claude-sonnet-5-5 "$STUB_LOG"
  grep -q -- "$AGENT_HOME/.agent/tasks/T-101.yaml" "$STUB_LOG"
  grep -q "alpha T-101 start" "$AGENT_HOME/.agent/logs/activity.log"
}

@test "refuses a worker before the session exists" {
  make_task T-101 alpha assigned src/a/
  run "$S/agent-start.sh" alpha T-101
  [ "$status" -eq 3 ]
  [[ "$output" == *"is not running"* ]]
}

@test "refuses the wrong owner" {
  start_session
  make_task T-101 beta assigned src/b/
  run "$S/agent-start.sh" alpha T-101
  [ "$status" -eq 3 ]
  [[ "$output" == *"owned by 'beta'"* ]]
}

@test "refuses a task that is not assigned" {
  start_session
  make_task T-101 alpha planned src/a/
  run "$S/agent-start.sh" alpha T-101
  [ "$status" -eq 3 ]
  [[ "$output" == *"is 'planned'"* ]]
}

@test "refuses an unmet dependency" {
  start_session
  make_task T-100 beta in_progress src/b/
  make_task T-101 alpha assigned src/a/ T-100
  run "$S/agent-start.sh" alpha T-101
  [ "$status" -eq 3 ]
  [[ "$output" == *"dependency T-100 is 'in_progress'"* ]]
}

@test "refuses an invalid task file" {
  start_session
  make_task T-101 alpha assigned ""
  run "$S/agent-start.sh" alpha T-101
  [ "$status" -eq 3 ]
  [[ "$output" == *"is invalid"* ]]
}

@test "refuses an overlapping path lock and leaves no worktree behind" {
  start_session
  make_task T-101 alpha assigned src/a/
  make_task T-102 gamma assigned src/a/app.py
  run "$S/agent-start.sh" alpha T-101
  [ "$status" -eq 0 ]
  run "$S/agent-start.sh" gamma T-102
  [ "$status" -eq 3 ]
  [[ "$output" == *"T-101 src/a/ src/a/app.py"* ]]
  [ ! -d "$AGENT_WT_ROOT/T-102" ]
}

@test "a directory request conflicts with a held file inside it" {
  start_session
  make_task T-101 alpha assigned src/a/app.py
  make_task T-102 gamma assigned src/a/
  run "$S/agent-start.sh" alpha T-101
  [ "$status" -eq 0 ]
  run "$S/agent-start.sh" gamma T-102
  [ "$status" -eq 3 ]
  [[ "$output" == *"path lock conflict"* ]]
}

@test "a sibling path with a shared prefix is not a conflict" {
  start_session
  make_task T-101 alpha assigned src/a
  make_task T-102 gamma assigned src/akit
  run "$S/agent-start.sh" alpha T-101
  [ "$status" -eq 0 ]
  run "$S/agent-start.sh" gamma T-102
  [ "$status" -eq 0 ]
}

@test "refuses past the Claude session cap" {
  export AGENT_MAX_CLAUDE=2
  start_session
  make_task T-101 alpha assigned src/a/
  make_task T-102 beta assigned src/b/
  run "$S/agent-start.sh" alpha T-101
  [ "$status" -eq 0 ]
  run "$S/agent-start.sh" beta T-102
  [ "$status" -eq 3 ]
  [[ "$output" == *"2 of 2 Claude sessions are live"* ]]
}

@test "stop keeps lock and worktree while the task is unfinished" {
  start_session
  make_task T-101 alpha assigned src/a/
  "$S/agent-start.sh" alpha T-101
  run "$S/agent-stop.sh" T-101
  [ "$status" -eq 0 ]
  [[ "$output" == *"kept lock and worktree for T-101 (status in_progress)"* ]]
  [[ "$(windows)" != *alpha-T-101* ]]
  [ -d "$AGENT_WT_ROOT/T-101" ]
  yq -e '.locks[] | select(.task == "T-101")' "$AGENT_HOME/.agent/state/ownership.yaml"
}

@test "stop on a complete, merged task releases lock, worktree and branch" {
  start_session
  make_task T-101 alpha assigned src/a/
  "$S/agent-start.sh" alpha T-101
  git -C "$AGENT_WT_ROOT/T-101" commit -q --allow-empty -m work
  git -C "$AGENT_HOME" merge -q --ff-only agent/T-101
  yq -i '.status = "complete"' "$AGENT_HOME/.agent/tasks/T-101.yaml"
  run "$S/agent-stop.sh" T-101
  [ "$status" -eq 0 ]
  [[ "$output" == *"removed worktree and branch for T-101"* ]]
  [ ! -d "$AGENT_WT_ROOT/T-101" ]
  ! yq -e '.locks[] | select(.task == "T-101")' "$AGENT_HOME/.agent/state/ownership.yaml"
}

@test "stop on a complete but unmerged task keeps the worktree" {
  start_session
  make_task T-101 alpha assigned src/a/
  "$S/agent-start.sh" alpha T-101
  git -C "$AGENT_WT_ROOT/T-101" commit -q --allow-empty -m work
  yq -i '.status = "complete"' "$AGENT_HOME/.agent/tasks/T-101.yaml"
  run "$S/agent-stop.sh" T-101
  [[ "$output" == *"is not merged into main"* ]]
  [ -d "$AGENT_WT_ROOT/T-101" ]
}

@test "stop on a complete task with uncommitted changes keeps the worktree" {
  start_session
  make_task T-101 alpha assigned src/a/
  "$S/agent-start.sh" alpha T-101
  touch "$AGENT_WT_ROOT/T-101/scratch.txt"
  yq -i '.status = "complete"' "$AGENT_HOME/.agent/tasks/T-101.yaml"
  run "$S/agent-stop.sh" T-101
  [[ "$output" == *"uncommitted changes"* ]]
  [ -d "$AGENT_WT_ROOT/T-101" ]
}

@test "restart --escalate relaunches on the escalation model, same worktree" {
  start_session
  make_task T-101 alpha assigned src/a/
  "$S/agent-start.sh" alpha T-101
  wait_for_stub 2
  run "$S/agent-restart.sh" T-101 --escalate
  [ "$status" -eq 0 ]
  wait_for_stub 3
  grep -qx -- claude-fable-5-1 "$STUB_LOG"
  [ "$(yq '.locks | length' "$AGENT_HOME/.agent/state/ownership.yaml")" -eq 1 ]
}

@test "status lists windows, task status, and locks without a window" {
  start_session
  make_task T-101 alpha assigned src/a/
  make_task T-102 beta assigned src/b/
  "$S/agent-start.sh" alpha T-101
  "$S/agent-start.sh" beta T-102
  "$S/agent-stop.sh" T-102
  run "$S/agent-status.sh" --write
  [ "$status" -eq 0 ]
  [[ "$output" == *alpha-T-101*in_progress*running* ]]
  [[ "$output" == *T-102*nowindow* ]]
  [[ "$output" == *"live Claude sessions: 2/3"* ]]
  yq -e '.agents | length == 3' "$AGENT_HOME/.agent/state/project-state.yaml"
}

@test "tasks lists active and blocked work with blockers" {
  make_task T-101 alpha in_progress src/a/
  make_task T-102 beta blocked src/b/
  yq -i '.blockers = ["needs T-101 API"]' "$AGENT_HOME/.agent/tasks/T-102.yaml"
  make_task T-103 qa complete tests/
  run "$S/agent-tasks.sh"
  [ "$status" -eq 0 ]
  [[ "$output" == *ACTIVE*T-101*BLOCKED*T-102*"needs T-101 API"* ]]
  [[ "$output" != *T-103* ]]
}

@test "attach resolves a task id; an ambiguous role is a usage error" {
  start_session
  make_task T-101 gamma assigned docs/a
  make_task T-102 gamma assigned docs/b
  "$S/agent-start.sh" gamma T-101
  "$S/agent-start.sh" gamma T-102
  AGENT_ATTACH_DRY_RUN=1 run "$S/agent-attach.sh" T-102
  [ "$status" -eq 0 ]
  [[ "$output" == "=engineering-control:"* ]]
  AGENT_ATTACH_DRY_RUN=1 run "$S/agent-attach.sh" gamma
  [ "$status" -eq 2 ]
}

@test "logs saves a pane capture per window" {
  start_session
  run "$S/agent-logs.sh" control
  [ "$status" -eq 0 ]
  [[ "$output" == *"saved $AGENT_HOME/.agent/logs/control-"* ]]
  [[ "$output" == *"session-start"* ]]
}

@test "scope check passes in-scope changes and fails out-of-scope ones" {
  make_task T-101 alpha in_progress src/a/
  git -C "$AGENT_HOME" branch agent/T-101
  git -C "$AGENT_HOME" worktree add -q "$AGENT_WT_ROOT/T-101" agent/T-101
  mkdir -p "$AGENT_WT_ROOT/T-101/src/a"
  touch "$AGENT_WT_ROOT/T-101/src/a/app.py"
  git -C "$AGENT_WT_ROOT/T-101" add -A && git -C "$AGENT_WT_ROOT/T-101" commit -q -m in-scope
  run "$S/agent-scope-check.sh" T-101
  [ "$status" -eq 0 ]
  touch "$AGENT_WT_ROOT/T-101/README.md"
  git -C "$AGENT_WT_ROOT/T-101" add -A && git -C "$AGENT_WT_ROOT/T-101" commit -q -m out-of-scope
  run "$S/agent-scope-check.sh" T-101
  [ "$status" -eq 1 ]
  [[ "$output" == *"FAIL README.md: outside task paths"* ]]
}

@test "stop --all ends the session" {
  start_session
  run "$S/agent-stop.sh" --all
  [ "$status" -eq 0 ]
  ! tmux -L "$AGENT_TMUX_SOCKET" has-session -t "=$AGENT_SESSION" 2>/dev/null
}

@test "a task's own model and effort override the role default" {
  start_session
  make_task T-101 beta assigned src/c/
  yq -i '.model = "claude-opus-5-5" | .effort = "xhigh"' "$AGENT_HOME/.agent/tasks/T-101.yaml"
  run "$S/agent-start.sh" beta T-101
  [ "$status" -eq 0 ]
  wait_for_stub 2
  [ "$(grep -c -x -- claude-opus-5-5 "$STUB_LOG")" -eq 2 ]
  grep -qx -- xhigh "$STUB_LOG"
  ! grep -qx -- claude-sonnet-5-5 "$STUB_LOG"
}
