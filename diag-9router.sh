#!/usr/bin/env bash
# diagnostics for the node2 9Router: auth + provider-node + model discovery
set -uo pipefail
B=http://127.0.0.1:20128
CJ=/tmp/diag-cj.txt
PW='__PW__'
NID='__NID__'
rm -f $CJ

echo "=== auth/login ==="
curl -s -m 10 -c $CJ -X POST $B/api/auth/login -H 'Content-Type: application/json' \
  -d "{\"password\":\"$PW\"}" -o /tmp/d-login.json -w "HTTP %{http_code}\n"
head -c 300 /tmp/d-login.json; echo

echo "=== auth/status ==="
curl -s -m 10 -b $CJ "$B/api/auth/status" -w '\nHTTP %{http_code}\n' | head -c 300

echo "=== provider-nodes/{id} ==="
curl -s -m 15 -b $CJ "$B/api/provider-nodes/$NID" -o /tmp/d-node.json -w "HTTP %{http_code}\n"
head -c 700 /tmp/d-node.json; echo

echo "=== models (raw head) ==="
curl -s -m 30 -b $CJ "$B/api/models" -o /tmp/d-models.json -w "HTTP %{http_code}\n"
python3 - <<'PYEOF'
import json
d=json.load(open('/tmp/d-models.json'))
print('keys:', list(d.keys())[:15] if isinstance(d, dict) else type(d).__name__)
ms = d.get('models') or d.get('data') or []
print('n =', len(ms))
print('first3:', [str(m)[:70] for m in ms[:3]])
oc=[m for m in ms if 'oclocal' in str(m)]
print('oclocal:', len(oc), [str(m)[:70] for m in oc[:10]])
PYEOF

echo "=== node-scoped models ==="
curl -s -m 30 -b $CJ "$B/api/models?provider=$NID" -o /tmp/d-nm.json -w "HTTP %{http_code}\n"
head -c 500 /tmp/d-nm.json; echo

echo "=== chat completions direct to bridge with router key ==="
KEY=$(curl -s -m 10 -b $CJ -X POST $B/api/keys -H 'Content-Type: application/json' -d '{"name":"diag-1"}' | python3 -c "import sys,json;print(json.load(sys.stdin).get('key',''))" 2>/dev/null)
echo "keylen=${#KEY}"
curl -s -m 20 "$B/v1/models" -H "Authorization: Bearer $KEY" -o /tmp/d-v1.json -w "HTTP %{http_code}\n"
python3 - <<'PYEOF'
import json
try:
    d=json.load(open('/tmp/d-v1.json'))
except Exception as e:
    print('not json:', e); raise SystemExit
ms=d.get('data') or d.get('models') or []
print('v1 models n=',len(ms))
print('oclocal:', [m.get('id') for m in ms if isinstance(m,dict) and 'oclocal' in str(m.get('id'))][:12])
PYEOF
rm -f $CJ /tmp/d-login.json /tmp/d-node.json /tmp/d-models.json /tmp/d-nm.json /tmp/d-v1.json
