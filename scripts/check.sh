#!/usr/bin/env bash
# The one gate. CI runs exactly this, and so does every agent before it says "done".
# Usage: scripts/check.sh
#        REMOTE=<user@host> scripts/check.sh
set -euo pipefail

# ssh and rsync can print key material, so their stderr is discarded.
# REMOTE is unset on the host so the copy runs the gate instead of syncing again.
run_remote() {
  local host="$1" root remote_dir gate=0
  local ssh_opts=(-o BatchMode=yes -o ConnectTimeout=15 -o StrictHostKeyChecking=accept-new)
  die() { echo "check: $*" >&2; exit 1; }

  [[ "$host" =~ ^[A-Za-z0-9][A-Za-z0-9._-]*(@[A-Za-z0-9][A-Za-z0-9._-]*)?$ ]] || die "bad host"
  root="$(git rev-parse --show-toplevel)"

  if ! remote_dir="$(ssh "${ssh_opts[@]}" "$host" 'mktemp -d /tmp/arda-check.XXXXXX' 2>/dev/null)"; then
    die "ssh failed"
  fi
  # A path we did not mint is not safe to delete.
  [[ "$remote_dir" =~ ^/tmp/arda-check\.[A-Za-z0-9]+$ ]] || die "ssh failed"

  if ! rsync -a \
    --filter=':- .gitignore' \
    --exclude .git \
    --exclude .env \
    -e "ssh ${ssh_opts[*]}" \
    "$root/" "${host}:${remote_dir}/" >/dev/null 2>&1; then
    ssh "${ssh_opts[@]}" "$host" "rm -rf ${remote_dir}" >/dev/null 2>&1 || true
    die "rsync failed"
  fi

  # Fresh tree has no .venv; CI installs the dev extra before the gate.
  # Non-interactive ssh PATH often omits ~/.local/bin where uv lives.
  set +e
  ssh "${ssh_opts[@]}" "$host" \
    "cd ${remote_dir} && git init -q && export PATH=\"\$HOME/.local/bin:\$PATH\" && uv sync --extra dev --frozen && exec env -u REMOTE bash scripts/check.sh"
  gate=$?
  set -e

  if ! ssh "${ssh_opts[@]}" "$host" "rm -rf ${remote_dir}" >/dev/null 2>&1; then
    [[ "$gate" -eq 0 ]] && die "ssh failed"
  fi
  exit "$gate"
}

if [[ -n "${REMOTE:-}" ]]; then
  run_remote "$REMOTE"
fi

cd "$(git rev-parse --show-toplevel)"

step() { echo; echo "==> $*"; }

step "ruff";            uv run ruff check .
# agents + core only: test doubles fake third-party shapes on purpose, typing them adds noise.
step "mypy";            uv run mypy agents core
step "pytest";          uv run pytest tests/ -q
step "evals (mock)";    uv run python scripts/run_evals.py --mode mock --check --no-write
step "README results";  uv run python scripts/update_readme.py --check

echo; echo "GATE PASS"
