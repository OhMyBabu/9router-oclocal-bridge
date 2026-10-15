#!/usr/bin/env bash
# Proof-of-use: an outside agent hitting the 9Router with its own API key.
# Runs on node2 (router is loopback-bound). Exercises every oclocal model + streaming.
set -uo pipefail
B=http://127.0.0.1:20128
CJ=/tmp/proof-cj.txt
PW='__PW__'
rm -f $CJ

echo "=== login (agent #1 gets its own router key) ==="
curl -s -m 10 -c $CJ -X POST $B/api/auth/login -H 'Content-Type: application/json' \
  -d "{\"password\":\"$PW\"}" -o /dev/null -w "login HTTP %{http_code}\n"

KEY=$(curl -s -m 10 -b $CJ -X POST $B/api/keys -H 'Content-Type: application/json' \
  -d '{"name":"agent-outside-proof"}' | python3 -c "import sys,json;print(json.load(sys.stdin).get('key',''))")
echo "router key len=${#KEY}"
[ -z "$KEY" ] && { echo "NO KEY"; exit 1; }

echo "=== models visible to that agent ==="
curl -s -m 20 "$B/v1/models" -H "Authorization: Bearer $KEY" | python3 -c "
import sys,json
ms=json.load(sys.stdin).get('data',[])
oc=[m['id'] for m in ms if 'oclocal' in m['id']]
print('total models:',len(ms),'| oclocal:',len(oc))
for m in oc: print('   ',m)
"

echo "=== chat completion per model (non-stream) ==="
for M in step-5-preview-free nemotron-3.5-lightning-free mimo-v2.6-flash-free muse-spark-1.3-contributor-free; do
  R=$(curl -s -m 300 -X POST $B/v1/chat/completions -H "Authorization: Bearer $KEY" -H 'Content-Type: application/json' \
    -d "{\"model\":\"oclocal/$M\",\"messages\":[{\"role\":\"user\",\"content\":\"reply with exactly: OK-$M\"}],\"max_tokens\":64}")
  printf '  %-30s %s\n' "$M" "$(printf '%s' "$R" | head -c 200)"
done

echo "=== streaming completion ==="
curl -s -N -m 300 -X POST $B/v1/chat/completions -H "Authorization: Bearer $KEY" -H 'Content-Type: application/json' \
  -d '{"model":"oclocal/nemotron-3-ultra-free","stream":true,"messages":[{"role":"user","content":"say STREAM-OK"}]}' \
  | head -c 300
rm -f $CJ
