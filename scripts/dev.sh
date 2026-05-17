#!/usr/bin/env bash
# Local dev loop: redis in docker, API on the host with --reload.
set -euo pipefail

cd "$(dirname "$0")/.."

if ! command -v docker >/dev/null 2>&1; then
  echo "docker not found; install Docker Desktop / OrbStack first." >&2
  exit 1
fi

docker compose up -d redis

if [ ! -d ".venv" ]; then
  python3.12 -m venv .venv
  ./.venv/bin/pip install --upgrade pip
  ./.venv/bin/pip install -e '.[dev]'
fi

# shellcheck disable=SC1091
source .venv/bin/activate
exec uvicorn api.main:app --host 0.0.0.0 --port 5000 --reload
