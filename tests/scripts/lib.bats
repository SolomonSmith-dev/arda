#!/usr/bin/env bats

load helpers

setup() {
  setup_agent_env
  unset AGENT_BASE_REF AGENT_MERGE_REF
  # shellcheck source=/dev/null
  source "$S/lib.sh"
}
teardown() { teardown_agent_env; }

@test "refs default to origin/main and main without config.env" {
  [ "$(base_ref)" = origin/main ]
  [ "$(merge_ref)" = main ]
}

@test "config.env sets the refs" {
  printf 'AGENT_BASE_REF=master\nAGENT_MERGE_REF=master\n' > "$AGENT_HOME/.agent/config.env"
  [ "$(base_ref)" = master ]
  [ "$(merge_ref)" = master ]
}

@test "env beats config.env" {
  printf 'AGENT_BASE_REF=master\n' > "$AGENT_HOME/.agent/config.env"
  AGENT_BASE_REF=origin/trunk
  [ "$(base_ref)" = origin/trunk ]
}

@test "models file and roles dir honor overrides" {
  [ "$(models_file)" = "$AGENT_MODELS_FILE" ]
  [ "$(roles_dir)" = "$AGENT_ROLES_DIR" ]
  unset AGENT_MODELS_FILE AGENT_ROLES_DIR
  [[ "$(models_file)" == */.agent/models.yaml ]]
  [[ "$(roles_dir)" == */.agent/roles ]]
}
