#!/bin/bash
# 9r-bridge launcher — detached, survives parent shell
set -uo pipefail
DIR=/root/9r-bridge
mkdir -p "$DIR"
TOKEN_FILE="$DIR/.bridge-token"
if [ ! -s "$TOKEN_FILE" ]; then
  python3 -c "import secrets;print(secrets.token_urlsafe(32))" > "$TOKEN_FILE"
  chmod 600 "$TOKEN_FILE"
fi
export BRIDGE_TOKEN="$(cat "$TOKEN_FILE")"
export OPENCODE_URL="http://127.0.0.1:4096"
export BRIDGE_HOST="0.0.0.0"
export BRIDGE_PORT="8899"
pkill -f 'bridge.py' 2>/dev/null
sleep 1
setsid nohup python3 "$DIR/bridge.py" >> "$DIR/bridge.log" 2>&1 < /dev/null &
sleep 2
echo "pid=$(pgrep -f 'bridge.py' | head -1)"
ss -tlnp 2>/dev/null | grep ':8899' || echo "NOT LISTENING"
echo "token: $(cat "$TOKEN_FILE")"
