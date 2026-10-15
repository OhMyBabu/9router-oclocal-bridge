#!/usr/bin/env python3
"""
Wire THIS box's opencode free-model bridge into the node2 9Router as a custom
OpenAI-compatible provider node.

Bridge:  http://43.163.110.101:8899/v1   (token auth, see /root/9r-bridge/.bridge-token)
Router:  https://127.0.0.1:20128 on jack-vps (loopback-bound container, driven over ssh)

Secrets are read from local files and substituted into the remote script —
never echoed into the transcript.
"""
import json
import pathlib
import re
import subprocess
import sys

HERE = pathlib.Path("/root/9r-bridge")
CREDS = pathlib.Path("/root/9r-src/deploy/creds.json")
creds = json.loads(CREDS.read_text())
PW = creds["INITIAL_PASSWORD"]
BRIDGE_TOKEN = (HERE / ".bridge-token").read_text().strip()
BRIDGE_URL = "http://43.163.110.101:8899/v1"
PREFIX = "oclocal"
NAME = "LTX Local Free (opencode)"

REMOTE = r"""
set -uo pipefail
B=http://127.0.0.1:20128
CJ=/tmp/9r-oc-cj.txt
PW='__PW__'
TOK='__TOK__'
BASE='__BASE__'
PREFIX='__PREFIX__'
NAME='__NAME__'
rm -f $CJ /tmp/login.json /tmp/nodes.json /tmp/node.json /tmp/conn.json /tmp/plist.json /tmp/models.json

echo "=== 1. login ==="
curl -sk -c $CJ -X POST $B/api/auth/login -H 'Content-Type: application/json' \
  -d "{\"password\":\"$PW\"}" -o /tmp/login.json -w "HTTP %{http_code}\n"
python3 -c "import json;d=json.load(open('/tmp/login.json'));print('auth:',d.get('authenticated', d.get('ok', 'see body')))" 2>/dev/null || head -c 200 /tmp/login.json

echo "=== 2. existing provider nodes ==="
curl -sk -b $CJ "$B/api/provider-nodes" -o /tmp/nodes.json -w "HTTP %{http_code}\n"
python3 - <<'PYEOF'
import json
try:
    d=json.load(open('/tmp/nodes.json'))
except Exception as e:
    print('parse fail',e); raise SystemExit
items = d.get('nodes') or d.get('data') or (d if isinstance(d,list) else [])
print('count:',len(items))
for n in items:
    print(' -',n.get('id'),'|',n.get('prefix'),'|',n.get('name'),'|',n.get('baseUrl'),'|',n.get('type'))
PYEOF

echo "=== 3. upsert node ==="
EXISTS=$(python3 -c "
import json
d=json.load(open('/tmp/nodes.json'))
items = d.get('nodes') or d.get('data') or (d if isinstance(d,list) else [])
for n in items:
    if n.get('prefix')=='__PREFIX__': print(n.get('id',''))
")
if [ -n "$EXISTS" ]; then
  echo "node exists: $EXISTS -> patch"
  curl -sk -b $CJ -X PATCH $B/api/provider-nodes/$EXISTS -H 'Content-Type: application/json' \
    -d "{\"baseUrl\":\"$BASE\",\"name\":\"$NAME\"}" -o /tmp/node.json -w "HTTP %{http_code}\n"
else
  curl -sk -b $CJ -X POST $B/api/provider-nodes -H 'Content-Type: application/json' \
    -d "{\"name\":\"$NAME\",\"prefix\":\"$PREFIX\",\"apiType\":\"chat\",\"baseUrl\":\"$BASE\",\"type\":\"openai-compatible\"}" \
    -o /tmp/node.json -w "HTTP %{http_code}\n"
fi
head -c 400 /tmp/node.json; echo
NID=$(python3 -c "import json;print(json.load(open('/tmp/node.json')).get('node',{}).get('id',''))" 2>/dev/null)
echo "NODE_ID=$NID"

echo "=== 4. attach token ==="
curl -sk -b $CJ -X POST $B/api/providers -H 'Content-Type: application/json' \
  -d "{\"provider\":\"$NID\",\"apiKey\":\"$TOK\",\"name\":\"$NAME\"}" -o /tmp/conn.json -w "HTTP %{http_code}\n"
head -c 300 /tmp/conn.json; echo

echo "=== 5. provider connections ==="
curl -sk -b $CJ "$B/api/providers" -o /tmp/plist.json -w "HTTP %{http_code}\n"
python3 - <<'PYEOF'
import json
d=json.load(open('/tmp/plist.json'))
items = d.get('connections') or d.get('providers') or []
if isinstance(items,list):
    for c in items:
        print(' -',c.get('id'),'|',c.get('provider'),'|',c.get('name'),'| active:',c.get('isActive'),'| status:',c.get('testStatus'))
else:
    print('raw:',json.dumps(d)[:300])
PYEOF

echo "=== 6. router models ==="
curl -sk -b $CJ "$B/api/models" -o /tmp/models.json -w "HTTP %{http_code}\n"
python3 - <<'PYEOF'
import json
d=json.load(open('/tmp/models.json'))
ms = d.get('models') or d.get('data') or []
print('count:',len(ms))
loc=[m for m in ms if isinstance(m,dict) and 'oclocal' in str(m.get('id',''))+str(m.get('provider',''))] if ms and isinstance(ms[0],dict) else [m for m in ms if 'oclocal' in str(m)]
print('oclocal models:',len(loc))
for m in loc[:15]: print('  -', m if isinstance(m,str) else (m.get('id') or m.get('name')))
PYEOF

echo "=== 7. smoke test through router ==="
KEY=$(curl -sk -b $CJ -X POST $B/api/keys -H 'Content-Type: application/json' -d '{"name":"jack-oclocal-test"}' | python3 -c "import sys,json;print(json.load(sys.stdin).get('key',''))" 2>/dev/null)
echo "test key len: ${#KEY}"
MODEL=$(python3 - <<'PYEOF'
import json
d=json.load(open('/tmp/models.json'))
ms = d.get('models') or d.get('data') or []
loc=[m for m in ms if 'oclocal' in str(m)]
print((loc[0] if isinstance(loc[0],str) else loc[0].get('id')) if loc else '')
PYEOF
)
echo "MODEL=$MODEL"
if [ -n "$MODEL" ] && [ -n "$KEY" ]; then
  curl -sk -m 180 -X POST $B/v1/chat/completions -H "Authorization: Bearer $KEY" -H 'Content-Type: application/json' \
    -d "{\"model\":\"$MODEL\",\"messages\":[{\"role\":\"user\",\"content\":\"reply with exactly: ROUTER-OK\"}],\"max_tokens\":32}" \
    -w '\nHTTP %{http_code}\n' | head -c 600
fi
rm -f $CJ /tmp/login.json /tmp/nodes.json /tmp/node.json /tmp/conn.json /tmp/plist.json /tmp/models.json
"""

REMOTE = (
    REMOTE.replace("__PW__", PW)
    .replace("__TOK__", BRIDGE_TOKEN)
    .replace("__BASE__", BRIDGE_URL)
    .replace("__PREFIX__", PREFIX)
    .replace("__NAME__", NAME)
)

r = subprocess.run(
    ["ssh", "-o", "BatchMode=yes", "-o", "StrictHostKeyChecking=no",
     "-o", "UserKnownHostsFile=/dev/null", "-o", "ConnectTimeout=15",
     "jack-vps", "bash -s"],
    input=REMOTE, text=True, capture_output=True, timeout=900,
)
out = r.stdout + r.stderr
out = out.replace(
    "Warning: Permanently added '43.167.161.194' (ED25519) to the list of known hosts.\n", "")
print(out)
sys.exit(0 if r.returncode == 0 else 1)
