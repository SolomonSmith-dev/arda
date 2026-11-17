#!/usr/bin/env bash
# Take the public demo down, the reverse of deploy.sh. Run on the home server:
#   bash ~/arda-demo/deploy/demo/teardown.sh
#
# Order: stop public traffic first (tunnel service), then delete the tunnel, then
# the DNS record (manual: cloudflared cannot delete DNS records, and deleting a
# tunnel leaves its CNAME behind), then the app. Every step checks its result and
# is skipped when already done, so a rerun after a failure finishes the job.
#
# It only touches what deploy.sh made: the cloudflared-arda-demo service,
# /etc/cloudflared/arda-demo.yml and the demo's own Cloudflare login. Another
# tunnel on this host (cloudflared.service, config.yml, cert.pem) is never read
# or changed, and a demo config that points at another tunnel is refused.
set -euo pipefail
{
HOST="arda.solomonsmith.dev"
TUNNEL="arda-demo"
PROJECT="arda-demo"
CONF="/etc/cloudflared/arda-demo.yml"
UNIT="cloudflared-arda-demo"
CERT="$HOME/.cloudflared/arda-demo-cert.pem"
GREP=/usr/bin/grep

REPO="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
cd "$REPO"
compose() { docker compose -p "$PROJECT" -f docker-compose.yml -f deploy/demo/docker-compose.demo.yml "$@"; }
step() { printf '\n== %s\n' "$*"; }
pass() { printf 'PASS: %s\n' "$*"; }
skip() { printf 'SKIP (already done): %s\n' "$*"; }
fail() { printf 'FAIL: %s\n' "$*" >&2; exit 1; }

[ "$(id -u)" != 0 ] || fail "run as your normal user; the script calls sudo where it needs it"

echo "This takes https://$HOST offline and deletes the $TUNNEL tunnel and container."
read -rp "Type $TUNNEL to continue: " ans
[ "$ans" = "$TUNNEL" ] || fail "not confirmed"

# Every cloudflared command below uses the demo login, never the default one.
export TUNNEL_ORIGIN_CERT="$CERT"

# ---------------------------------------------------------------- tunnel id
TUNNEL_ID=""
if command -v cloudflared >/dev/null && test -s "$CERT"; then
  out=$(cloudflared tunnel list -o json) || fail "cloudflared tunnel list failed (login expired?)"
  TUNNEL_ID=$(printf '%s' "$out" | python3 -c '
import json, sys
ids = [t["id"] for t in (json.load(sys.stdin) or []) if t.get("name") == sys.argv[1]]
print(ids[0] if ids else "")' "$TUNNEL")
fi
# Fall back to the installed config, in case the tunnel was already deleted.
if [ -z "$TUNNEL_ID" ] && sudo test -f "$CONF"; then
  TUNNEL_ID=$(sudo sed -n 's/^tunnel: \([0-9a-f-]\{36\}\)$/\1/p' "$CONF")
fi
echo "tunnel id: ${TUNNEL_ID:-none found}"

ours_config() {
  [ -n "$TUNNEL_ID" ] && sudo $GREP -qx "tunnel: $TUNNEL_ID" "$CONF" \
    && sudo $GREP -qx "  - hostname: $HOST" "$CONF"
}

# ---------------------------------------------------------------- 1. service
step "1. Stop and remove the $UNIT service"
if ! sudo test -f "/etc/systemd/system/$UNIT.service"; then
  skip "no $UNIT service"
else
  sudo systemctl disable --now "$UNIT" >/dev/null 2>&1 || true
  sudo rm -f "/etc/systemd/system/$UNIT.service"
  sudo systemctl daemon-reload
  [ "$(systemctl is-active "$UNIT" 2>/dev/null || true)" != active ] || fail "$UNIT still running"
  pass "service stopped and removed"
fi

# ---------------------------------------------------------------- 2. config
step "2. Remove $CONF and the tunnel credentials"
if ! sudo test -f "$CONF"; then
  skip "no $CONF"
elif ! ours_config; then
  fail "$CONF is not the $TUNNEL tunnel's; leaving it alone"
else
  sudo rm -f "$CONF"
  pass "config removed"
fi
if [ -n "$TUNNEL_ID" ] && sudo test -f /etc/cloudflared/"$TUNNEL_ID".json; then
  sudo rm -f /etc/cloudflared/"$TUNNEL_ID".json
  pass "credentials copy removed"
fi

# ---------------------------------------------------------------- 3. tunnel
step "3. Delete the tunnel"
listed() { local l; l=$(cloudflared tunnel list -o json 2>/dev/null) || return 1; case "$l" in *"\"$TUNNEL_ID\""*) return 0;; *) return 1;; esac; }
if [ -z "$TUNNEL_ID" ] || ! test -s "$CERT" || ! listed; then
  skip "tunnel $TUNNEL not on the account"
else
  # Clears connections left by an unclean stop; delete refuses while any remain.
  cloudflared tunnel cleanup "$TUNNEL" >/dev/null 2>&1 || true
  cloudflared tunnel delete "$TUNNEL" \
    || fail "delete refused (still connected elsewhere?). Check: TUNNEL_ORIGIN_CERT=$CERT cloudflared tunnel info $TUNNEL"
  ! listed || fail "tunnel still listed"
  pass "tunnel deleted (this also removed ~/.cloudflared/$TUNNEL_ID.json)"
fi

# ---------------------------------------------------------------- 4. dns
step "4. Delete the DNS record (manual)"
doh() {
  curl -s -H 'accept: application/dns-json' "https://cloudflare-dns.com/dns-query?name=$HOST&type=CNAME" \
    | python3 -c 'import json,sys; d=json.load(sys.stdin); print(d.get("Status"), len(d.get("Answer") or []))' 2>/dev/null \
    || echo "? ?"
}
# A proxied CNAME is flattened at the edge, so ask for A records too.
resolves() {
  local a
  a=$(curl -s -H 'accept: application/dns-json' "https://cloudflare-dns.com/dns-query?name=$HOST&type=A" \
      | python3 -c 'import json,sys; print(len(json.load(sys.stdin).get("Answer") or []))' 2>/dev/null || echo "?")
  [ "$a" != 0 ]
}
if ! resolves; then
  skip "$HOST no longer resolves"
else
  cat <<HELP
cloudflared cannot delete DNS records. In the Cloudflare dashboard:
  solomonsmith.dev > DNS > Records > the CNAME "arda" (target ${TUNNEL_ID:-<id>}.cfargotunnel.com)
  > Edit > Delete > Delete
HELP
  read -rp "Type yes when the record is deleted: " ans
  [ "$ans" = yes ] || fail "not confirmed"
  gone=0
  # Proxied records have a 300 s TTL, so 1.1.1.1 may answer from cache for 5 minutes.
  echo "Waiting for 1.1.1.1 to stop resolving $HOST (up to 6 minutes)..."
  for _ in $(seq 1 72); do if ! resolves; then gone=1; break; fi; sleep 5; done
  [ "$gone" = 1 ] || fail "$HOST still resolves on 1.1.1.1 after 6 minutes ($(doh)); check the record is deleted, then rerun"
  pass "$HOST no longer resolves"
fi

# ---------------------------------------------------------------- 5. app
step "5. Stop and remove the demo container and image"
if [ -z "$(compose ps -aq 2>/dev/null)" ] && ! docker image inspect arda-demo:latest >/dev/null 2>&1; then
  skip "no container or image"
else
  compose down --remove-orphans
  [ -z "$(compose ps -aq 2>/dev/null)" ] || fail "containers still present"
  # Only the demo's own tag. A production ARDA uses arda:<version> and is untouched.
  if docker image inspect arda-demo:latest >/dev/null 2>&1; then docker image rm arda-demo:latest >/dev/null; fi
  pass "container and arda-demo:latest image removed"
fi

# ---------------------------------------------------------------- 6. secrets
step "6. Local secrets"
if [ -f .env.demo ]; then
  read -rp "Delete .env.demo (holds the Anthropic key)? Type yes to delete, anything else keeps it: " ans
  if [ "$ans" = yes ]; then rm -f .env.demo; pass ".env.demo deleted"; else echo "kept .env.demo"; fi
else
  skip "no .env.demo"
fi

cat <<'DONE'

The demo is down. Left for you, outside this server:
  - Anthropic Console: delete the demo key (it is the one thing that can still spend money).
  - Cloudflare > Security > WAF > Rate limiting rules: delete "arda-demo ask" (Free has one slot).
  - Optional: rm ~/.cloudflared/arda-demo-cert.pem, the demo's Cloudflare login
    (any other cloudflared login on this server is a different file).
  - README: set the Live demo line back to "not deployed".
DONE
exit 0
}
