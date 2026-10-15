#!/usr/bin/env bash
# Smoke test: chat completion through the node2 9Router -> oclocal bridge -> opencode free models
set -uo pipefail
B=http://127.0.0.1:20128
CJ=/tmp/smoke-cj.txt
PW='__PW__'
MODEL='__MODEL__'
rm -f $CJ
curl -s -m 10 -c $CJ -X POST $B/api/auth/login -H 'Content-Type: application/json' \
  -d "{\"password\":\"$PW\"}" -o /dev/null
KEY=$(curl -s -m 10 -b $CJ -X POST $B/api/keys -H 'Content-Type: application/json' \
  -d '{"name":"jack-oclocal"}' | python3 -c "import sys,json;print(json.load(sys.stdin).get('key',''))")
echo "keylen=${#KEY}"
echo "=== model: $MODEL ==="
curl -s -m 300 -X POST $B/v1/chat/completions -H "Authorization: Bearer $KEY" -H 'Content-Type: application/json' \
  -d "{\"model\":\"$MODEL\",\"messages\":[{\"role\":\"user\",\"content\":\"reply with exactly: ROUTER-OK\"}],\"max_tokens\":64}" \
  -w '\nHTTP %{http_code}\n' | head -c 900
rm -f $CJ
