#!/usr/bin/env bash
# Health check for the public demo, run from outside the server (laptop, cloud
# session, phone shell). Spends no model tokens: it never sends a real question.
#
#   bash deploy/demo/check.sh                 # checks https://arda.solomonsmith.dev
#   bash deploy/demo/check.sh https://host    # another deployment
#   NO_EDGE=1 bash deploy/demo/check.sh       # skip the Cloudflare burst check
#
# Prints one line per check and exits non-zero if any check fails.
set -uo pipefail

BASE="${1:-https://arda.solomonsmith.dev}"
fails=0
ok()  { printf 'PASS  %s\n' "$*"; }
bad() { printf 'FAIL  %s\n' "$*"; fails=$((fails + 1)); }
has() { case "$1" in *"$2"*) return 0;; *) return 1;; esac; }
get() { curl -s --max-time 20 "$@"; }

# 1. Status: up, live (real Claude, not mock), shell off, corpus loaded.
st=$(get -w $'\n%{http_code}' "$BASE/demo/status") || st=$'\n000'
code=${st##*$'\n'}; body=${st%$'\n'*}
if [ "$code" != 200 ]; then
  bad "GET /demo/status returned HTTP $code (530 or 1033 page = tunnel down; 000 = DNS or network)"
else
  has "$body" '"mode":"live"' && ok "mode live" || bad "mode is not live (mock fallback: key missing or USE_MOCK_LLM on)"
  has "$body" '"shell_execution":"disabled"' && ok "shell execution disabled" || bad "status does not report shell_execution disabled"
  has "$body" '"corpus_chunks":22' && ok "corpus loaded (22 chunks)" || bad "corpus_chunks is not 22"
  left=$(printf '%s' "$body" | python3 -c 'import json,sys; print(json.load(sys.stdin).get("tokens_remaining_today","?"))' 2>/dev/null || echo "?")
  printf 'INFO  tokens remaining today: %s\n' "$left"
fi

# 2. The page loads.
c=$(get -o /dev/null -w '%{http_code}' "$BASE/") || c=000
[ "$c" = 200 ] && ok "page / loads" || bad "GET / returned HTTP $c"

# 3. Shell and private routes are refused.
for route in "POST /execute" "POST /execute/wait" "POST /query" "POST /agents/earendil/run" "POST /memory/ingest"; do
  m=${route%% *}; p=${route#* }
  c=$(get -o /dev/null -w '%{http_code}' -X "$m" "$BASE$p" -H 'content-type: application/json' -d '{}') || c=000
  [ "$c" = 403 ] && ok "$route refused (403)" || bad "$route returned HTTP $c, expected 403"
done

# 4. The Cloudflare rate-limit rule still blocks bursts. Empty messages are
# rejected (422) before the model, so this costs no tokens. An edge block is a
# 429 that is not the app's JSON. Blocks this address for 10 s.
if [ "${NO_EDGE:-0}" != 1 ]; then
  edge=0
  for _ in $(seq 1 3); do
    for _ in $(seq 1 15); do
      r=$(get -o /dev/null -w '%{http_code} %{content_type}' -X POST "$BASE/demo/ask" \
            -H 'content-type: application/json' -d '{"message":""}') || r=000
      case "$r" in "429 application/json"*) ;; 429*) edge=1 ;; esac
    done
    [ "$edge" = 1 ] && break
    sleep 3
  done
  [ "$edge" = 1 ] && ok "Cloudflare edge rule blocks bursts" || bad "no edge block after 45 fast requests (rule removed or disabled?)"
fi

if [ "$fails" = 0 ]; then echo "ALL PASS  $BASE"; else echo "$fails FAILED  $BASE"; fi
exit $(( fails > 0 ))
