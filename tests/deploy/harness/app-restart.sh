#!/bin/bash
# (Re)start the real demo app in mock mode on 127.0.0.1:5001, as `compose up/restart` would.
[ -f "$H/state/app.pid" ] && kill "$(cat "$H/state/app.pid")" 2>/dev/null
for _ in $(seq 1 50); do /usr/bin/curl -sf 127.0.0.1:5001/health >/dev/null || break; sleep 0.2; done
cd "$HARNESS_APP_DIR" || exit 1
env DEMO_MODE=true USE_MOCK_LLM=true ARDA_API_KEY=harness \
    DEMO_TRUST_CF_HEADER="$(cat "$H/state/trust" 2>/dev/null || echo true)" \
    setsid "$HARNESS_PYTHON" -m uvicorn api.main:app --host 127.0.0.1 --port 5001 \
    >> "$H/state/app.log" 2>&1 < /dev/null &
echo $! > "$H/state/app.pid"
for _ in $(seq 1 120); do /usr/bin/curl -sf 127.0.0.1:5001/health >/dev/null && exit 0; sleep 0.5; done
exit 1
