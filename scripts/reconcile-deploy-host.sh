#!/usr/bin/env bash
# Reconcile the ARDA deploy host onto origin/main.
#
# Context: home-server (100.112.3.116, /home/solomon/Code/arda-stack/arda)
# has been running claude/pr-6-hardening for months. That tree predates the
# LangGraph orchestrator, the LlamaIndex Finrod migration, CI, and the whole
# integration suite. Nothing on it is unique -- see AGENTS.md "Deploy host
# reality" -- but switching branches under a live stack still deserves a
# backup, a preflight, and a rollback path rather than a hand-typed sequence.
#
# Run ON the deploy host, from the repo root:
#     ./scripts/reconcile-deploy-host.sh --dry-run  # narrate the plan, change nothing
#     ./scripts/reconcile-deploy-host.sh            # prompts before changing anything
#     ./scripts/reconcile-deploy-host.sh --yes      # non-interactive
#
# --dry-run still runs every read-only check (docker present, .env has a key,
# which profiles are up, how far behind the host is, which commits would be
# left behind) and prints each mutating command it would run, prefixed
# "would:". Nothing is created, checked out, rebuilt, or stashed.
#
# Exit 0 = host is on main, stack is up, /health answers.
set -euo pipefail

REMOTE_URL="https://github.com/SolomonSmith-dev/arda.git"
TARGET_REF="main"
API_URL="http://localhost:5000"
ASSUME_YES=0
DRY_RUN=0
for arg in "$@"; do
  case "$arg" in
    --yes|-y)    ASSUME_YES=1 ;;
    --dry-run|-n) DRY_RUN=1 ;;
    -h|--help)   sed -n '2,20p' "$0"; exit 0 ;;
    *)           printf 'unknown argument: %s (try --help)\n' "$arg" >&2; exit 2 ;;
  esac
done

step() { printf '\n=== %s ===\n' "$*"; }
ok()   { printf 'OK    %s\n' "$*"; }
warn() { printf 'WARN  %s\n' "$*"; }
die()  { printf 'FAIL  %s\n' "$*" >&2; exit 1; }

# Every state-changing command goes through run(). Under --dry-run it is
# printed and skipped, so the dry run exercises the same control flow as a
# real run rather than a separate narration path that can drift from it.
run() {
  if [[ "$DRY_RUN" -eq 1 ]]; then
    printf 'would: %s\n' "$*"
    return 0
  fi
  "$@"
}

confirm() {
  [[ "$DRY_RUN" -eq 1 ]] && { printf 'would ask: %s\n' "$1"; return 0; }
  [[ "$ASSUME_YES" -eq 1 ]] && return 0
  local reply
  read -r -p "$1 [y/N] " reply
  [[ "$reply" == "y" || "$reply" == "Y" ]]
}

cd "$(dirname "$0")/.."

# --- 1. Preflight -----------------------------------------------------------
step "Preflight"

command -v docker >/dev/null 2>&1 || die "docker not found"
docker compose version >/dev/null 2>&1 || die "docker compose v2 not available"
git rev-parse --git-dir >/dev/null 2>&1 || die "not a git repository: $PWD"
ok "docker + git present in $PWD"

# The stack fails closed on a missing key: core/config.py builds the Settings
# singleton at import time, so an absent ARDA_API_KEY is not a 401 at runtime,
# it is a crash before the app object exists. Check BEFORE we touch anything.
[[ -f .env ]] || die ".env missing. Copy .env.example and set ARDA_API_KEY first."
if grep -qE '^ARDA_API_KEY=.+' .env; then
  ok ".env defines a non-empty ARDA_API_KEY"
else
  die ".env has no non-empty ARDA_API_KEY. The API will crash at import without it."
fi

# The .env on a long-lived host can predate the code being deployed. This
# host's was written before the Anthropic pivot: it still set gemini /
# llama model overrides and stored the key as CLAUDE_API_KEY, which
# core/config.py does not read. Deploying main over that yields a bot
# running on MockAnthropicClient, or 404s once the key name is fixed --
# in both cases silently, because nothing crashes. Check before switching.
# Must always succeed: `x="$(env_val KEY)"` propagates the substitution's
# status, so a grep miss on an absent key would trip `set -e` and kill the
# script mid-preflight without printing anything.
env_val() { grep -E "^$1=" .env 2>/dev/null | head -1 | cut -d= -f2- || true; }

if [[ "$(env_val USE_MOCK_LLM)" == "false" ]]; then
  if [[ -n "$(env_val ANTHROPIC_API_KEY)" ]]; then
    ok ".env has ANTHROPIC_API_KEY and USE_MOCK_LLM=false"
  else
    warn "USE_MOCK_LLM=false but ANTHROPIC_API_KEY is unset or empty."
    if [[ -n "$(env_val CLAUDE_API_KEY)" ]]; then
      warn "  A CLAUDE_API_KEY is present. core/config.py reads ANTHROPIC_API_KEY;"
      warn "  rename it or the agents fall back to the mock client in production."
    fi
    die "fix the LLM key before deploying, or set USE_MOCK_LLM=true deliberately"
  fi
fi

# Anthropic is the sole provider after ADR 0006. A model override naming a
# retired provider is sent verbatim to the Anthropic API and 404s.
for var in ORCHESTRATOR_MODEL RETRIEVER_MODEL SPECIALIST_MODEL EXECUTOR_MODEL; do
  val="$(env_val "$var")"
  [[ -z "$val" ]] && continue
  if [[ "$val" == *gemini* || "$val" == *llama* || "$val" == *gpt* || "$val" == *mixtral* ]]; then
    warn "${var}=${val} names a retired provider (ADR 0006: Anthropic only)."
    warn "  Remove the override so the code default applies, or set a claude-* id."
    STALE_MODELS=1
  fi
done
if [[ "${STALE_MODELS:-0}" -eq 1 ]]; then
  die "stale model overrides in .env would 404 against the Anthropic API"
fi
ok "no stale model overrides in .env"

# The export CSVs are mounted read-only by compose, but nothing reads them
# unless LETTERBOXD_EXPORT_DIR points at the in-container path. Without it
# Tom answers from the small seed catalogue and reports no ratings.
if [[ -d data/letterboxd ]] && compgen -G "data/letterboxd/*.csv" >/dev/null; then
  if [[ -n "$(env_val LETTERBOXD_EXPORT_DIR)" ]]; then
    ok ".env points LETTERBOXD_EXPORT_DIR at the mounted export"
  else
    warn "data/letterboxd holds CSVs but LETTERBOXD_EXPORT_DIR is unset, so the"
    warn "  merge never runs and Tom sees only the seed catalogue. Suggested:"
    warn "  LETTERBOXD_EXPORT_DIR=/app/data/letterboxd"
  fi
fi

CURRENT_REF="$(git rev-parse --abbrev-ref HEAD)"
CURRENT_SHA="$(git rev-parse --short HEAD)"
ok "currently on ${CURRENT_REF} (${CURRENT_SHA})"

if [[ "$CURRENT_REF" == "$TARGET_REF" ]]; then
  warn "already on ${TARGET_REF}; this run will fast-forward and rebuild only."
fi

# --- 2. Capture the profiles that are actually up ---------------------------
# `docker compose ps` only reports services in the *currently selected*
# profiles, which is empty by default. Read the live container list instead so
# the rebuild does not silently drop galadriel/tombombadil/gwaihir.
step "Detecting active compose profiles"

docker ps >/dev/null 2>&1 || die "docker daemon is not reachable; start it before reconciling"

RUNNING="$(docker ps --format '{{.Names}}')"
PROFILE_ARGS=()
for pair in "galadriel:cron" "tombombadil:discord" "gwaihir:telegram" "milvus:milvus"; do
  svc="${pair%%:*}"; prof="${pair##*:}"
  if grep -q -- "-${svc}-" <<<"$RUNNING"; then
    PROFILE_ARGS+=(--profile "$prof")
    ok "profile '${prof}' is active (${svc} running)"
  fi
done
[[ ${#PROFILE_ARGS[@]} -eq 0 ]] && warn "no optional profiles running; base services only"

# --- 3. Back up the current tree --------------------------------------------
step "Backup"

BACKUP="prod-backup-$(date +%Y%m%d-%H%M%S)"
run git branch "$BACKUP"
[[ "$DRY_RUN" -eq 0 ]] && ok "current HEAD saved as local branch ${BACKUP}"
echo "      rollback: git checkout ${BACKUP} && docker compose ${PROFILE_ARGS[*]-} up -d --build"

if [[ -n "$(git status --porcelain)" ]]; then
  warn "working tree is dirty:"
  git status --short | sed 's/^/      /'
  confirm "Stash these local changes?" || die "aborted; commit or stash manually, then re-run"
  run git stash push -u -m "reconcile-deploy-host ${BACKUP}"
  ok "stashed (restore with: git stash pop)"
else
  ok "working tree clean"
fi

# --- 4. Fetch target --------------------------------------------------------
# The repo is public, so this needs no credentials. The host cannot push, and
# this script never tries to.
step "Fetching ${TARGET_REF}"

git fetch --quiet "$REMOTE_URL" "$TARGET_REF" || die "fetch failed (network? tailscale?)"
TARGET_SHA="$(git rev-parse --short FETCH_HEAD)"
ok "fetched ${TARGET_REF} at ${TARGET_SHA}"

BEHIND="$(git rev-list --count HEAD..FETCH_HEAD)"
AHEAD="$(git rev-list --count FETCH_HEAD..HEAD)"
echo "      this host is ${BEHIND} behind / ${AHEAD} ahead of ${TARGET_REF}"

if [[ "$AHEAD" -gt 0 ]]; then
  warn "${AHEAD} commit(s) exist here and not on ${TARGET_REF}:"
  git log --oneline FETCH_HEAD..HEAD | sed 's/^/      /'
  echo "      They are preserved on ${BACKUP}. Per AGENTS.md these are superseded."
  confirm "Proceed and leave those commits behind?" || die "aborted; nothing changed"
fi

# --- 5. Switch --------------------------------------------------------------
step "Switching to ${TARGET_REF}"

confirm "Check out ${TARGET_REF} (${TARGET_SHA}) and rebuild the stack?" \
  || die "aborted; nothing changed"

run git checkout -B "$TARGET_REF" FETCH_HEAD
[[ "$DRY_RUN" -eq 0 ]] && ok "now on ${TARGET_REF} ($(git rev-parse --short HEAD))"

# --- 6. Rebuild -------------------------------------------------------------
step "Rebuilding"

run docker compose "${PROFILE_ARGS[@]}" up -d --build
[[ "$DRY_RUN" -eq 0 ]] && ok "compose up completed"

# --- 7. Health --------------------------------------------------------------
step "Health check"

if [[ "$DRY_RUN" -eq 1 ]]; then
  echo "would: poll ${API_URL}/health for 30s, then ${API_URL}/agents/health with the .env key"
  echo
  echo "=== Dry run complete. Nothing was changed. ==="
  echo "Re-run without --dry-run to apply."
  exit 0
fi

for attempt in $(seq 1 30); do
  if curl -fsS "${API_URL}/health" >/dev/null 2>&1; then
    ok "/health responded after ${attempt}s"
    curl -fsS "${API_URL}/health" | sed 's/^/      /'
    break
  fi
  [[ "$attempt" -eq 30 ]] && {
    warn "/health did not respond within 30s. Recent api logs:"
    docker compose logs --tail 40 api | sed 's/^/      /'
    die "stack is unhealthy. Roll back: git checkout ${BACKUP} && docker compose ${PROFILE_ARGS[*]} up -d --build"
  }
  sleep 1
done

KEY="$(grep -E '^ARDA_API_KEY=' .env | head -1 | cut -d= -f2-)"
if curl -fsS -H "x-api-key: ${KEY}" "${API_URL}/agents/health" >/dev/null 2>&1; then
  ok "/agents/health authenticated successfully"
else
  warn "/agents/health did not authenticate; check ARDA_API_KEY in .env"
fi

# --- 8. Next ----------------------------------------------------------------
step "Done"
echo "Host is on ${TARGET_REF}. Backup branch: ${BACKUP}"
echo
echo "Next: ./scripts/verify-d4-d5.sh    # now present on this host for the first time"
echo "Then: close issue #22 if the D5 checks pass."
