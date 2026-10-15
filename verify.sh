#!/usr/bin/env bash
# verify.sh — local + end-to-end verification of the bridge. Exit non-zero on failure.
set -uo pipefail
DIR="$(cd "$(dirname "$0")" && pwd)"
BRIDGE="${BRIDGE_URL:-http://127.0.0.1:8899}"
TOK="${BRIDGE_TOKEN:-$(cat "$DIR/.bridge-token" 2>/dev/null || true)}"
FAIL=0
ok(){ echo "  [ok] $1"; }
bad(){ echo "  [FAIL] $1"; FAIL=1; }

echo "=== 1. opencode serve (:4096) ==="
if curl -s -m 8 "$BRIDGE/../4096/api/health" >/dev/null 2>&1; then :; fi
curl -s -m 8 http://127.0.0.1:4096/api/health -o /dev/null -w '  serve health: %{http_code}\n' 2>/dev/null \
  && ok "opencode serve reachable" || bad "opencode serve down (run ./start-serve.sh)"

echo "=== 2. bridge health ==="
H=$(curl -s -m 8 "$BRIDGE/health")
echo "  $H"
echo "$H" | grep -q '"ok": *true' && ok "bridge up" || bad "bridge down (run ./start-bridge.sh)"

echo "=== 3. auth gate ==="
C=$(curl -s -m 8 -o /dev/null -w '%{http_code}' "$BRIDGE/v1/models")
[ "$C" = "200" ] && echo "  [warn] bridge has no token (open relay)" || [ "$C" = "401" ] && ok "token required ($C)" || bad "unexpected $C"

echo "=== 4. model list ==="
if [ -n "$TOK" ]; then
  N=$(curl -s -m 15 -H "Authorization: Bearer $TOK" "$BRIDGE/v1/models" \
      | python3 -c 'import sys,json;print(len(json.load(sys.stdin)["data"]))' 2>/dev/null)
  [ "${N:-0}" -ge 10 ] && ok "$N models exposed" || bad "only ${N:-0} models"
fi

echo "=== 5. chat completion ==="
if [ -n "$TOK" ]; then
  R=$(curl -s -m 240 -X POST "$BRIDGE/v1/chat/completions" -H "Authorization: Bearer $TOK" \
      -H 'Content-Type: application/json' \
      -d '{"model":"step-5-preview-free","messages":[{"role":"user","content":"reply with exactly: PONG"}]}')
  echo "  ${R:0:180}"
  echo "$R" | grep -q 'PONG' && ok "completion ok" || bad "completion failed"
fi

echo "=== 6. streaming ==="
if [ -n "$TOK" ]; then
  S=$(curl -s -N -m 240 -X POST "$BRIDGE/v1/chat/completions" -H "Authorization: Bearer $TOK" \
      -H 'Content-Type: application/json' \
      -d '{"model":"nemotron-3.5-lightning-free","stream":true,"messages":[{"role":"user","content":"say PONG"}]}')
  echo "$S" | grep -q 'data: \[DONE\]' && ok "stream terminated" || bad "stream malformed"
  echo "$S" | grep -q 'PONG' && ok "stream body ok" || bad "stream body empty"
fi

echo
[ "$FAIL" = "0" ] && echo "RESULT: PASS" || echo "RESULT: FAIL"
exit $FAIL
