#!/bin/bash
# opencode serve launcher for the 9router bridge.
# ISOLATED HOME (no AGENTS.md persona, no fox skills, no shared DB) + clean workspace
# => bridged completions stay raw model output, no persona bleed.
set -uo pipefail
DIR=/root/9r-bridge
WS="$DIR/workspace"
HOMEDIR="$DIR/home"
mkdir -p "$WS" "$HOMEDIR" "$HOMEDIR/.config/opencode" "$HOMEDIR/.local/share/opencode"
touch "$WS/.keep" "$HOMEDIR/.config/opencode/opencode.json"

cat > "$HOMEDIR/.config/opencode/opencode.json" <<'EOF'
{
  "$schema": "https://opencode.ai/config.json",
  "autoupdate": false,
  "instructions": []
}
EOF

# anchored pattern -> never matches this launcher's own /bin/bash cmdline
pkill -f '^opencode serve' 2>/dev/null
sleep 1

cd "$WS"
export HOME="$HOMEDIR"
export OPENCODE_DISABLE_PROJECT_CONFIG=true
export OPENCODE_DISABLE_AUTOUPDATE=true
export OPENCODE_CLIENT=cli

setsid nohup opencode serve --port 4096 --hostname 0.0.0.0 --print-logs \
  >> "$DIR/serve.log" 2>&1 < /dev/null &
sleep 5
pgrep -af '^opencode serve' | head -2
ss -tlnp 2>/dev/null | grep ':4096' || echo "NOT LISTENING"
