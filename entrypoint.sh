#!/usr/bin/env bash
# entrypoint.sh — container mode: mint token (first run), start serve, start bridge.
set -euo pipefail
DATA=/data
TOKEN_FILE="$DATA/.bridge-token"
log(){ printf '[entrypoint] %s\n' "$*"; }

mkdir -p "$DATA/home/.config/opencode" "$DATA/workspace"
touch "$DATA/workspace/.keep"

if [ ! -s "$TOKEN_FILE" ]; then
  log "minting bridge token"
  python3 -c "import secrets;print(secrets.token_urlsafe(32))" > "$TOKEN_FILE"
  chmod 600 "$TOKEN_FILE"
  log "token written to $TOKEN_FILE (mounted volume — survives restarts)"
fi
export BRIDGE_TOKEN="$(cat "$TOKEN_FILE")"

cleanup(){ log "shutting down"; kill 0 2>/dev/null || true; }
trap cleanup TERM INT

log "starting opencode serve on 127.0.0.1:4096"
opencode serve --port 4096 --hostname 127.0.0.1 --print-logs &
SERVE_PID=$!

for i in $(seq 1 30); do
  curl -fsS -m 3 http://127.0.0.1:4096/api/health >/dev/null 2>&1 && break
  sleep 1
done
curl -fsS -m 3 http://127.0.0.1:4096/api/health >/dev/null 2>&1 \
  || log "WARN serve not healthy yet; bridge will retry"

log "starting bridge on 0.0.0.0:${BRIDGE_PORT}"
python3 /app/bridge.py &
BRIDGE_PID=$!

# surface the token in logs once, so `docker compose logs` shows it
log "READY  base_url=http://<host>:${BRIDGE_PORT}/v1"
log "READY  api_key=${BRIDGE_TOKEN}"

wait -n "$SERVE_PID" "$BRIDGE_PID"
log "a child exited; tearing down container"
kill "$SERVE_PID" "$BRIDGE_PID" 2>/dev/null || true
exit 1
