# Shared setup for agent script tests: a temp main checkout, a private tmux
# server, and a stub in place of `claude` that records its arguments.

setup_agent_env() {
  ROOT="$(cd "$BATS_TEST_DIRNAME/../.." && pwd)"
  S="$ROOT/scripts"
  export AGENT_MODELS_FILE="$ROOT/tests/scripts/fixtures/roster/models.yaml"
  export AGENT_ROLES_DIR="$ROOT/tests/scripts/fixtures/roster/roles"
  export AGENT_HOME="$BATS_TEST_TMPDIR/home"
  export AGENT_WT_ROOT="$BATS_TEST_TMPDIR/wt"
  export AGENT_TMUX_SOCKET="agent-test-$$-$BATS_TEST_NUMBER"
  export AGENT_BASE_REF=main AGENT_MERGE_REF=main
  export AGENT_SESSION=engineering-control AGENT_MAX_CLAUDE=3
  export STUB_LOG="$BATS_TEST_TMPDIR/claude-args"
  export AGENT_CLAUDE_CMD="$BATS_TEST_TMPDIR/claude-stub"
  export GIT_AUTHOR_NAME=t GIT_AUTHOR_EMAIL=t@t GIT_COMMITTER_NAME=t GIT_COMMITTER_EMAIL=t@t
  cat > "$AGENT_CLAUDE_CMD" <<'STUB'
#!/usr/bin/env bash
printf '%s\n' "$@" "--END--" >> "$STUB_LOG"
exec sleep 600
STUB
  chmod +x "$AGENT_CLAUDE_CMD"
  mkdir -p "$AGENT_HOME/.agent/tasks"
  git -C "$AGENT_HOME" init -q -b main
  git -C "$AGENT_HOME" commit -q --allow-empty -m init
}

teardown_agent_env() {
  tmux -L "$AGENT_TMUX_SOCKET" kill-server 2>/dev/null || true
}

# make_task <id> <owner> <status> <path,path> [dep,dep]
make_task() {
  local id="$1" owner="$2" status="$3" paths="$4" deps="${5:-}"
  {
    echo "id: $id"
    echo "title: Test task $id"
    echo "owner: $owner"
    echo "status: $status"
    echo "description: Fixture."
    echo "paths: [${paths}]"
    echo "depends_on: [${deps}]"
    echo "acceptance: [done]"
    echo "validation: [\"true\"]"
    echo "reviewers: [lead-engineer]"
    echo "blockers: []"
  } > "$AGENT_HOME/.agent/tasks/$id.yaml"
}

# Wait until the stub has been launched n times (it appends one --END-- per launch).
wait_for_stub() {
  local n="$1" i
  for i in $(seq 50); do
    [[ -f "$STUB_LOG" && "$(grep -c -- '--END--' "$STUB_LOG")" -ge "$n" ]] && return 0
    sleep 0.1
  done
  echo "stub launched fewer than $n times" >&2
  return 1
}

windows() { tmux -L "$AGENT_TMUX_SOCKET" list-windows -t "=$AGENT_SESSION" -F '#{window_name}'; }
