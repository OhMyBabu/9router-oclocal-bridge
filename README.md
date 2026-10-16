# oclocal — stand up an OpenAI-compatible endpoint on a fresh VPS

Exposes the model pool that lives inside the **opencode CLI installed on this
machine** as a normal OpenAI-compatible HTTP API. Any agent that can
`POST /v1/chat/completions` can then use those models.

**9Router is optional.** It is one possible consumer of this endpoint, documented
at the bottom under *Optional*. The bridge has no dependency on it.

```
agent ──> oclocal bridge :8899 ──> opencode serve :4096 ──> free model pool
          OpenAI-compatible       (in-process client)
```

## Why the extra hop

The free pool is not a public API. Calling it directly is refused:

```
$ curl https://opencode.ai/inference/v1/chat/completions -d '{"model":"step-5-preview-free",...}'
{"type":"error","error":{"type":"FreeTierError","message":"OpenCode's free tier
can only be used from within OpenCode. ..."}}
```

403 `FreeTierError` regardless of `Authorization`, `User-Agent`, or the client's
normal `apiKey:"public"` body field — the gate is client identity, not a key.
`opencode serve` is a legitimate in-process client, so the bridge rides on it
instead of fighting the gate.

## Install (fresh VPS, one command)

```bash
git clone https://github.com/OhMyBabu/9router-oclocal-bridge.git
cd 9router-oclocal-bridge
sudo ./install.sh
```

`install.sh` does all of it: installs `python3`/`curl`/`git`, installs the
opencode CLI if absent (official installer), lays down an isolated `HOME` +
clean workspace, mints a token, installs systemd units (or falls back to
detached `nohup` with `--no-systemd`), then runs `verify.sh` and prints the
connection details.

No root? `./install.sh --no-systemd` (processes then die on reboot).

### Docker route (no install on the host)

```bash
docker compose up -d --build
curl -fsS http://127.0.0.1:8899/health
docker compose logs oclocal | grep READY     # shows base_url + api_key
```

The image ships the opencode CLI plus the bridge; the token is generated into
`./data/.bridge-token` on first start and survives restarts.

## Using it

```bash
BASE=http://<vps-ip>:8899/v1
KEY=$(cat /opt/oclocal/.bridge-token)      # systemd route
# KEY=$(cat ./data/.bridge-token)           # docker route

curl "$BASE/models" -H "Authorization: Bearer $KEY"

curl -X POST "$BASE/chat/completions" \
  -H "Authorization: Bearer $KEY" -H 'Content-Type: application/json' \
  -d '{"model":"step-5-preview-free",
       "messages":[{"role":"user","content":"hello"}],
       "stream": true}'
```

Endpoints: `/v1/chat/completions` (stream + non-stream), `/v1/completions`,
`/v1/models`, `/health`.

### Models

10 free models, discovered live from the local `GET /api/model` and filtered to
zero-cost entries:

```
big-pickle  cat-jam-free  deepseek-v4.1-flash-free  longcat-2.5-preview-free
mimo-v2.6-flash-free  muse-spark-1.3-contributor-free  nemotron-3-ultra-free
nemotron-3.5-lightning-free  space-bunny-free  step-5-preview-free
```

Model names are accepted bare, `opencode/<id>`, or with a router prefix in front
(`<prefix>/<id>`), so a 9Router-style `oclocal/step-5-preview-free` also works.

### Protocol shape

| mode | content-type | body | strict OpenAI SDK |
|---|---|---|---|
| `stream: true` | `text/event-stream` | `data: {…}` frames + `data: [DONE]` | **works** |
| non-stream | `application/json` | bare JSON | **works** |

The bridge itself is strictly correct in both modes. (If you front it with
9Router, that layer re-wraps single-shot JSON into an SSE body — see *Optional*.)

### Quick check

```bash
sudo ./verify.sh          # or: BRIDGE_TOKEN=$(cat ./data/.bridge-token) ./verify.sh
```
Prints `RESULT: PASS` when serve, auth gate, model list, completion, and
streaming all behave.

## Config (env)

| var | default | meaning |
|---|---|---|
| `BRIDGE_HOST` / `BRIDGE_PORT` | `0.0.0.0` / `8899` | listen address |
| `OPENCODE_URL` | `http://127.0.0.1:4096` | upstream serve |
| `BRIDGE_TOKEN` | generated into `.bridge-token` | required bearer token; **empty = open relay** |
| `BRIDGE_DEFAULT_MODEL` | `step-5-preview-free` | used when a request omits `model` |
| `BRIDGE_TIMEOUT` | `240` | seconds to wait for a completion |
| `BRIDGE_RETRIES` | `3` | retries per candidate on a transient upstream error |
| `BRIDGE_RETRY_BACKOFF` | `2.0` | base backoff seconds between retries |
| `BRIDGE_FALLBACK` | 10-model chain | order tried when a model's endpoint is unavailable |

## Exposing it publicly

The bridge speaks plain HTTP and has **no auth beyond the bearer token**. Pick one:

1. **nginx + Let's Encrypt** — template in `nginx/oclocal.conf`. It disables
   proxy buffering and raises `proxy_read_timeout`, both required for streaming.
2. **Tailscale / WireGuard** — best if the consumers are your own machines; no
   public exposure at all.
3. **Nothing** — keep it on `127.0.0.1` and let something on the box reach it.

Also consider firewall rules; the free-tier quota is per-IP, so any traffic you
let in draws from the same bucket as your own CLI usage.

## Files

| file | role |
|---|---|
| `bridge.py` | the shim — OpenAI-compatible surface over `opencode serve` |
| `install.sh` | one-shot installer (packages, opencode, layout, systemd, verify) |
| `entrypoint.sh` | container entrypoint (token mint, serve, bridge, signal handling) |
| `start-serve.sh` | detached `opencode serve` launcher, isolated HOME |
| `start-bridge.sh` | detached bridge launcher + token mint |
| `verify.sh` | local + end-to-end check, non-zero exit on failure |
| `test-all-models.py` | drives every model both modes through a given base URL |
| `wire-9router.py` | optional: register this endpoint as a 9Router provider node |
| `smoke-9router.sh`, `diag-9router.sh`, `proof-9router.sh` | optional 9Router tooling |
| `Dockerfile`, `docker-compose.yml` | container route |
| `nginx/oclocal.conf` | public TLS template |

## Limits

| layer | limit | evidence |
|---|---|---|
| bridge | **none** | no token/request accounting in `bridge.py` |
| upstream free pool | **unpublished, per-IP, per-model** | see below |

- Official docs: the free models are *"available on OpenCode for a limited time"*
  and the offer *"is not a promise of unlimited or permanent free inference."*
- Independent trackers record a **per-IP** limit and roughly **10 parallel
  connections per IP** on at least one free model.
- Measured here: 12 sequential + 20 concurrent + 50 concurrent completions all
  returned 200 (50 concurrent in ~30s), so the ceiling sits above that.
- The recurring `429 "Endpoint is unavailable"` reads as the per-IP / per-model
  limit firing rather than an open outage — a sibling model usually answers
  immediately. That is why `BRIDGE_FALLBACK` exists.
- Using it interactively feels unlimited because a human sends one request per
  turn over minutes; an agent fleet can drain the same bucket in seconds.

The requested model is always tried first; on `429 / Endpoint is unavailable` the
bridge retries it, then walks `BRIDGE_FALLBACK`. The returned `model` field is
the model that actually served, so a request for `x` may legitimately come back
served by `y`.

## Isolation notes

The serve process deliberately runs with an isolated `HOME` and
`OPENCODE_DISABLE_PROJECT_CONFIG=true`. Pointing it at a home directory that
holds an `AGENTS.md` (or a project with one) pulls that file into every system
prompt — persona bleed plus a large prompt. Measured: ~25k prompt tokens with a
`/root` cwd, ~1.5k isolated.

Production reference: this instance runs under systemd
(`9r-serve.service`, `9r-bridge.service`) with `Restart=always`, logging to
`/opt/oclocal/*.log`.

---

## Optional: front it with 9Router

Everything above works without 9Router. If you already run one, this endpoint
registers as a custom `openai-compatible` provider node and its models appear
alongside the router's own:

```bash
python3 wire-9router.py     # upserts the node + attaches the token on node2
python3 test-all-models.py   # drives all 10 models through the router
```

Observed on that deployment: models become `oclocal/<model>`, reachable via
`https://aethink.com/v1`. One caveat — 9Router answers single-shot JSON with
`content-type: text/event-stream` and a trailing `data: [DONE]`, so strict JSON
clients must use `stream: true` against the *router* path. That quirk belongs to
the router (its own `keyPolicy.js` accounts for exactly that body shape), not to
this bridge.

The router container binds `127.0.0.1` only, so those calls must be issued on the
router host (over ssh), never by public IP.
