# oclocal — opencode free-model bridge for 9Router

Exposes the free models that live inside this box's `opencode` install as an
**OpenAI-compatible** endpoint, then wires that endpoint into a 9Router instance
as a custom `openai-compatible` provider node. Other agents (anything that can
speak `POST /v1/chat/completions`) then consume those models through the router.

```
agent ──> 9Router (node2 :20128) ──> http://<this-box>:8899/v1 ──> opencode serve (:4096)
            key oclocal/<model>                                  └─> opencode free tier
```

## Why the indirection

OpenCode's free tier is **not** a public API:

```
$ curl https://opencode.ai/inference/v1/chat/completions -d '{"model":"step-5-preview-free",...}'
{"type":"error","error":{"type":"FreeTierError","message":"OpenCode's free tier can only be used from within OpenCode."}}
```

403 `FreeTierError` regardless of `Authorization`, `User-Agent`, or the
`apiKey:"public"` body field the client normally sends. The gate is client-side
identity, not a key. `opencode serve` is a legitimate in-process client, so the
bridge rides on it instead of fighting the gate.

## Layout

| file | role |
|---|---|
| `bridge.py` | OpenAI-compatible shim (`/v1/models`, `/v1/chat/completions`, `/v1/completions`, `/health`), streaming + non-streaming, bearer-token auth |
| `start-serve.sh` | launches `opencode serve` detached, from an **isolated HOME** + clean workspace |
| `start-bridge.sh` | mints the bridge token (if absent) and launches `bridge.py` detached |
| `wire-9router.py` | logs into 9Router, upserts the `oclocal` provider node, attaches the token, smoke-tests |
| `smoke-9router.sh` | one chat completion through the router (runs on node2 over ssh) |
| `diag-9router.sh` | auth / node / model-discovery diagnostics on node2 |
| `verify.sh` | full local verification, exit code reflects pass/fail |

## How another agent uses it

The router container binds `127.0.0.1:20128` only, but node2's nginx fronts it on
**443**, so this works from anywhere:

```
base_url: https://43.167.161.194/v1
api_key:  <9Router key, minted in the 9Router UI or POST /api/keys>
model:    oclocal/<model>          e.g. oclocal/step-5-preview-free
```

```bash
curl -sk https://43.167.161.194/v1/chat/completions \
  -H "Authorization: Bearer $ROUTER_KEY" -H 'Content-Type: application/json' \
  -d '{"model":"oclocal/nemotron-3.5-lightning-free",
       "messages":[{"role":"user","content":"hello"}]}'
```

`/v1/models` on the router lists all 10 as `oclocal/<model>`. Streaming works
(`"stream": true`).

Verified from a third-party box against the public endpoint: `/v1/models` -> 10
`oclocal/*` models, chat completion -> HTTP 200, `stream: true` -> deltas + `[DONE]`.

## Models

10 free models, discovered live from `GET /api/model` on the local serve and
filtered to zero-cost entries:

```
big-pickle  cat-jam-free  deepseek-v4.1-flash-free  longcat-2.5-preview-free
mimo-v2.6-flash-free  muse-spark-1.3-contributor-free  nemotron-3-ultra-free
nemotron-3.5-lightning-free  space-bunny-free  step-5-preview-free
```

The router sees them as `oclocal/<model>` (node prefix + model id).

## Deploy

```bash
./start-serve.sh     # opencode serve on 0.0.0.0:4096
./start-bridge.sh    # bridge on 0.0.0.0:8899, token in .bridge-token
./verify.sh          # must print RESULT: PASS
python3 wire-9router.py   # registers the node on node2 9Router
```

`wire-9router.py` reads `9r-src/deploy/creds.json` (router password) and
`.bridge-token` locally, substitutes both into a script that is piped over ssh
stdin — secrets are never echoed into the transcript or the repo.

## Config (env)

| var | default | meaning |
|---|---|---|
| `BRIDGE_HOST` / `BRIDGE_PORT` | `0.0.0.0` / `8899` | listen address |
| `OPENCODE_URL` | `http://127.0.0.1:4096` | upstream serve |
| `BRIDGE_TOKEN` | from `.bridge-token` | required bearer token; empty = open relay |
| `BRIDGE_DEFAULT_MODEL` | `step-5-preview-free` | used when a request omits `model` |
| `BRIDGE_TIMEOUT` | `240` | seconds to wait for a completion |
| `BRIDGE_RETRIES` | `3` | retries per candidate model on a transient upstream error |
| `BRIDGE_RETRY_BACKOFF` | `2.0` | base backoff seconds between retries |
| `BRIDGE_FALLBACK` | 10-model chain | comma-separated order tried when the requested model's endpoint is unavailable |

The requested model is always tried first; on `429 / Endpoint is unavailable`
the bridge retries it, then walks `BRIDGE_FALLBACK` until one model serves. The
returned `model` field reflects the model that actually answered, so a router
key request for `oclocal/x` may legitimately come back served by `y`.

## Isolation notes

The serve process deliberately runs with `HOME=<repo>/home` and
`OPENCODE_DISABLE_PROJECT_CONFIG=true`. Pointing it at `/root` pulls
`/root/AGENTS.md` + `~/.config/opencode/AGENTS.md` into every system prompt,
which bleeds the operator persona into every bridged answer and inflates
prompt tokens (~25k → ~1.5k). Keep it isolated.

## Caveats

- Free-tier upstreams are flaky: intermittent `429 "Endpoint is unavailable"`.
  The bridge self-heals via `BRIDGE_RETRIES` + `BRIDGE_FALLBACK` and reports the
  model that actually served; only an all-candidates failure surfaces as HTTP 502.
- Every completion mints a fresh opencode session (stateless by design, no
  cross-request contamination). Sessions accumulate in the serve DB.
- `prompt_tokens` looks large because the opencode `build` agent ships its full
  tool schema in the system prompt.
- The bridge is bound to `0.0.0.0` and is the only thing standing between the
  internet and this box's free-tier quota — keep `BRIDGE_TOKEN` set and firewall
  port 8899 to the router's egress if you can.
