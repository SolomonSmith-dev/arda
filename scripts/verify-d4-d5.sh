#!/usr/bin/env bash
# Verify D4 (Galadriel cron) + D5 (Milvus) on the deploy host.
# Run from the repo root after enabling the compose profiles.
# Exit 0 = checks look good; non-zero = something still missing.
set -euo pipefail

fail=0
ok() { printf 'OK  %s\n' "$*"; }
bad() { printf 'FAIL %s\n' "$*"; fail=1; }

echo "=== D4 Galadriel (cron profile) ==="
if docker compose ps galadriel 2>/dev/null | grep -qE 'running|Up'; then
  ok "galadriel container is running"
else
  bad "galadriel not running — enable with: docker compose --profile cron up -d"
fi

if docker compose exec -T redis redis-cli EXISTS tom_letterboxd_sync >/dev/null 2>&1 \
  || docker compose exec -T redis redis-cli KEYS 'cron:job:*' 2>/dev/null | grep -q .; then
  ok "cron job keys present in Redis (seeded job or watch-party)"
else
  # Soft signal: API lifespan should seed tom_letterboxd_sync; key name is cron:job:tom_letterboxd_sync
  if docker compose exec -T redis redis-cli EXISTS cron:job:tom_letterboxd_sync 2>/dev/null | grep -q 1; then
    ok "cron:job:tom_letterboxd_sync exists"
  else
    bad "no Letterboxd sync cron job found — restart api so lifespan seeds it, or run /sync as owner"
  fi
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
