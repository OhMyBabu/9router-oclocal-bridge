#!/usr/bin/env bash
# install.sh — standalone installer for the oclocal bridge.
#
# What it does, on a FRESH VPS, with no 9Router anywhere:
#   1. ensures python3 / curl / git
#   2. installs the opencode CLI if absent (official installer) — this is the
#      ONLY hard dependency; it is what legitimately holds the free tier
#   3. lays down an isolated HOME + clean workspace (no AGENTS.md bleed)
#   4. mints a bridge token
#   5. installs systemd units (falls back to detached nohup if systemd is absent)
#   6. runs verify.sh
#
# 9Router is NOT required. It is one optional consumer of this endpoint — see
# README.md "Optional: front it with 9Router".
#
# Usage:  sudo ./install.sh            # systemd route (recommended)
#         ./install.sh --no-systemd    # nohup route, no root needed
set -euo pipefail

DIR="$(cd "$(dirname "$0")" && pwd)"
NO_SYSTEMD=0
[ "${1:-}" = "--no-systemd" ] && NO_SYSTEMD=1

log(){ printf '\n\033[1m== %s\033[0m\n' "$*"; }
die(){ printf '\n\033[31mFATAL: %s\033[0m\n' "$*" >&2; exit 1; }

# ---------------------------------------------------------------- 0. preflight
log "0/6 preflight"
OS="$(uname -s)"; ARCH="$(uname -m)"
echo "  os=$OS arch=$ARCH"
[ "$OS" = "Linux" ] || die "only Linux is supported (got $OS)"

SUDO=""
if [ "$(id -u)" -ne 0 ]; then
  if command -v sudo >/dev/null 2>&1; then SUDO="sudo"; else die "run as root or install sudo"; fi
fi
$SUDO true 2>/dev/null || true

# ---------------------------------------------------------------- 1. packages
log "1/6 system packages"
NEED=()
command -v python3 >/dev/null 2>&1 || NEED+=(python3)
command -v curl    >/dev/null 2>&1 || NEED+=(curl)
command -v git     >/dev/null 2>&1 || NEED+=(git)
if [ "${#NEED[@]}" -gt 0 ]; then
  echo "  installing: ${NEED[*]}"
  if command -v apt-get >/dev/null 2>&1; then
    $SUDO env DEBIAN_FRONTEND=noninteractive apt-get update -qq
    $SUDO env DEBIAN_FRONTEND=noninteractive apt-get install -y -qq "${NEED[@]}" || die "apt install failed"
  elif command -v dnf >/dev/null 2>&1; then
    $SUDO dnf install -y -q "${NEED[@]}" || die "dnf install failed"
  elif command -v apk >/dev/null 2>&1; then
    $SUDO apk add --no-cache "${NEED[@]}" || die "apk add failed"
  else
    die "no supported package manager; install ${NEED[*]} manually"
  fi
else
  echo "  all present"
fi
python3 -c 'import json,http.server,urllib.request' || die "python3 stdlib check failed"

# ---------------------------------------------------------------- 2. opencode
log "2/6 opencode CLI (the free-tier holder)"
if command -v opencode >/dev/null 2>&1; then
  echo "  found: $(command -v opencode)  $(opencode --version 2>/dev/null | head -1)"
else
  echo "  not found -> installing from opencode.ai"
  curl -fsSL https://opencode.ai/install | bash || die "opencode install failed"
  export PATH="$HOME/.opencode/bin:$PATH"
  command -v opencode >/dev/null 2>&1 || die "opencode installed but not on PATH; add ~/.opencode/bin to PATH"
  echo "  installed: $(command -v opencode)"
fi
opencode serve --help >/dev/null 2>&1 || die "this opencode build has no 'serve' subcommand"

# ---------------------------------------------------------------- 3. layout
log "3/6 layout + isolated HOME"
RT="${OCLOCAL_RUNTIME:-/opt/oclocal}"
$SUDO mkdir -p "$RT/home/.config/opencode" "$RT/home/.local/share/opencode" "$RT/workspace"
$SUDO cp -f "$DIR/bridge.py" "$DIR/verify.sh" "$RT/" 2>/dev/null || true
$SUDO cp -f "$DIR/start-serve.sh" "$DIR/start-bridge.sh" "$RT/" 2>/dev/null || true
$SUDO chmod +x "$RT"/*.sh 2>/dev/null || true

cat > /tmp/oclocal-cfg.json <<'EOF'
{
  "$schema": "https://opencode.ai/config.json",
  "autoupdate": false,
  "instructions": []
}
EOF
$SUDO cp -f /tmp/oclocal-cfg.json "$RT/home/.config/opencode/opencode.json"
$SUDO touch "$RT/workspace/.keep"
echo "  runtime dir: $RT"

# token
TOKEN_FILE="$RT/.bridge-token"
if [ ! -s "$TOKEN_FILE" ]; then
  python3 -c "import secrets;print(secrets.token_urlsafe(32))" > /tmp/oclocal-tok
  $SUDO cp -f /tmp/oclocal-tok "$TOKEN_FILE"
  rm -f /tmp/oclocal-tok
fi
$SUDO chmod 600 "$TOKEN_FILE"
echo "  token: $TOKEN_FILE"

# ---------------------------------------------------------------- 4. services
log "4/6 process supervision"
PORT="${OCLOCAL_PORT:-8899}"
SERVE_PORT="${OCLOCAL_SERVE_PORT:-4096}"

write_unit(){
  local name="$1" path="$2"
  $SUDO tee "$path" >/dev/null <<EOF
[Unit]
Description=oclocal $name
After=network-online.target
Wants=network-online.target

[Service]
Type=simple
WorkingDirectory=$RT/workspace
Environment=HOME=$RT/home
Environment=OPENCODE_DISABLE_PROJECT_CONFIG=true
Environment=OPENCODE_DISABLE_AUTOUPDATE=true
Environment=OPENCODE_CLIENT=cli
Environment=OPENCODE_URL=http://127.0.0.1:$SERVE_PORT
Environment=BRIDGE_HOST=0.0.0.0
Environment=BRIDGE_PORT=$PORT
EnvironmentFile=-$RT/bridge.env
ExecStart=$3
Restart=always
RestartSec=3
NoNewPrivileges=true
StandardOutput=append:$RT/$name.log
StandardError=append:$RT/$name.log

[Install]
WantedBy=multi-user.target
EOF
}

if [ "$NO_SYSTEMD" -eq 0 ] && command -v systemctl >/dev/null 2>&1 && [ -d /run/systemd/system ]; then
  write_unit "9r-serve"  "/etc/systemd/system/9r-serve.service"  "opencode serve --port $SERVE_PORT --hostname 0.0.0.0 --print-logs"
  write_unit "9r-bridge" "/etc/systemd/system/9r-bridge.service" "/usr/bin/env python3 $RT/bridge.py"
  $SUDO systemctl daemon-reload
  $SUDO systemctl enable --now 9r-serve 9r-bridge
  echo "  systemd units installed and started"
  sleep 6
else
  echo "  systemd unavailable -> detached nohup route"
  (
    cd "$RT/workspace"
    HOME="$RT/home" OPENCODE_DISABLE_PROJECT_CONFIG=true OPENCODE_DISABLE_AUTOUPDATE=true \
      setsid nohup opencode serve --port "$SERVE_PORT" --hostname 0.0.0.0 --print-logs \
      >> "$RT/9r-serve.log" 2>&1 < /dev/null &
  )
  sleep 5
  (
    cd "$RT"
    HOME="$RT/home" BRIDGE_TOKEN="$(cat "$TOKEN_FILE")" OPENCODE_URL="http://127.0.0.1:$SERVE_PORT" \
      BRIDGE_HOST=0.0.0.0 BRIDGE_PORT="$PORT" \
      setsid nohup python3 "$RT/bridge.py" >> "$RT/9r-bridge.log" 2>&1 < /dev/null &
  )
  echo "  started detached (dies on reboot — re-run install.sh)"
fi

# bridge.env for the bridge unit to pick up the token
$SUDO tee "$RT/bridge.env" >/dev/null <<EOF
BRIDGE_TOKEN=$(cat "$TOKEN_FILE")
EOF
$SUDO chmod 600 "$RT/bridge.env"

# ---------------------------------------------------------------- 5. verify
log "5/6 verification"
sleep 3
FAILED=0
for i in 1 2 3 4 5 6 7 8 9 10; do
  if curl -s -m 5 "http://127.0.0.1:$PORT/health" 2>/dev/null | grep -q '"ok": *true'; then break; fi
  sleep 3
done
curl -s -m 10 "http://127.0.0.1:$PORT/health" || true; echo
BRIDGE_URL="http://127.0.0.1:$PORT" BRIDGE_TOKEN="$(cat "$TOKEN_FILE")" \
  bash "$DIR/verify.sh" || FAILED=1

# ---------------------------------------------------------------- 6. report
log "6/6 done"
IP="$(curl -s -m 8 https://api.ipify.org 2>/dev/null || echo '<vps-ip>')"
cat <<EOF

  oclocal bridge is LIVE on this VPS.

    base_url : http://$IP:$PORT/v1
    api_key  : $(cat "$TOKEN_FILE")
    models   : curl -H "Authorization: Bearer $(cat "$TOKEN_FILE")" http://$IP:$PORT/v1/models

  Plain HTTP. For anything beyond localhost, put TLS in front — see
  README.md "Exposing it publicly" (nginx + Let's Encrypt or Tailscale).

  9Router is optional. Consumers that speak OpenAI can point straight at the
  base_url above.
EOF
[ "$FAILED" -eq 0 ] || die "verify.sh reported failures — check $RT/*.log"
exit 0
