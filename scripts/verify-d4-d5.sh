#!/usr/bin/env bash
# Verify D4 (Galadriel cron) + D5 (Milvus) on the deploy host.
# Run from the repo root after enabling the compose profiles.
# Exit 0 = checks look good; non-zero = something still missing.
set -euo pipefail

# galadriel and milvus sit behind compose profiles. Naming a profile-gated
# service as an argument to `compose ps` resolves against the *active* set,
# so without this the lookup can error and read as a spurious FAIL.
export COMPOSE_PROFILES=cron,milvus,discord,telegram

fail=0
ok() { printf 'OK  %s\n' "$*"; }
bad() { printf 'FAIL %s\n' "$*"; fail=1; }

echo "=== D4 Galadriel (cron profile) ==="
if docker compose ps galadriel 2>/dev/null | grep -qE 'running|Up'; then
  ok "galadriel container is running"
else
  bad "galadriel not running — enable with: docker compose --profile cron up -d"
fi

# `redis-cli EXISTS k` prints 0 or 1 and exits 0 either way, so testing its
# *exit status* is always true whenever Redis answers at all. Compare the
# printed value. The key is namespaced (agents/galadriel/store.py:17), so the
# bare name never existed.
seeded="$(docker compose exec -T redis redis-cli EXISTS cron:job:tom_letterboxd_sync 2>/dev/null | tr -d '\r')"
any_job="$(docker compose exec -T redis redis-cli --no-raw KEYS 'cron:job:*' 2>/dev/null | grep -c . || true)"

if [[ "$seeded" == "1" ]]; then
  ok "cron:job:tom_letterboxd_sync is seeded"
elif [[ "${any_job:-0}" -gt 0 ]]; then
  ok "no Letterboxd sync job, but ${any_job} other cron:job:* key(s) exist"
else
  bad "no cron:job:* keys at all — restart api so the lifespan seeds it, or run /sync as owner"
fi

echo
echo "=== D5 Milvus (milvus profile) ==="
if docker compose ps milvus 2>/dev/null | grep -qE 'running|Up'; then
  ok "milvus container is running"
else
  bad "milvus not running — enable with: docker compose --profile milvus up -d"
fi

# Compose-network hostname for API/Tom containers.
if grep -qE '^MILVUS_HOST=milvus' .env 2>/dev/null; then
  ok ".env has MILVUS_HOST=milvus"
else
  bad ".env should set MILVUS_HOST=milvus (compose DNS name)"
fi
if grep -qE '^USE_MOCK_EMBEDDER=false' .env 2>/dev/null; then
  ok ".env has USE_MOCK_EMBEDDER=false"
else
  bad ".env should set USE_MOCK_EMBEDDER=false for durable embeddings"
fi

echo
if [[ "$fail" -eq 0 ]]; then
  echo "All D4/D5 operator checks passed."
  exit 0
fi
echo "One or more checks failed — see docs/cutover.md and docs/tombombadil-memory.md."
exit 1
