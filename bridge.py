#!/usr/bin/env python3
"""
9router <-> opencode serve bridge.

Exposes an OpenAI-compatible surface (/v1/chat/completions, /v1/models) backed by
the local headless `opencode serve` instance (default http://127.0.0.1:4096),
which is what actually unlocks OpenCode's free-tier models.

Stateless by design: every /v1/chat/completions call mints a fresh opencode session.
"""
import json
import os
import sys
import time
import threading
import urllib.error
import urllib.request
import uuid
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

OPENCODE_URL = os.environ.get("OPENCODE_URL", "http://127.0.0.1:4096").rstrip("/")
LISTEN_HOST = os.environ.get("BRIDGE_HOST", "0.0.0.0")
LISTEN_PORT = int(os.environ.get("BRIDGE_PORT", "8899"))
BRIDGE_TOKEN = os.environ.get("BRIDGE_TOKEN", "")
DEFAULT_MODEL = os.environ.get("BRIDGE_DEFAULT_MODEL", "step-5-preview-free")
FALLBACK_MODELS = [
    "big-pickle",
    "cat-jam-free",
    "deepseek-v4.1-flash-free",
    "longcat-2.5-preview-free",
    "mimo-v2.6-flash-free",
    "muse-spark-1.3-contributor-free",
    "nemotron-3-ultra-free",
    "nemotron-3.5-lightning-free",
    "space-bunny-free",
    "step-5-preview-free",
]
PROMPT_TIMEOUT = float(os.environ.get("BRIDGE_TIMEOUT", "240"))
POLL_INTERVAL = 0.35
RETRIES = int(os.environ.get("BRIDGE_RETRIES", "3"))
RETRY_BACKOFF = float(os.environ.get("BRIDGE_RETRY_BACKOFF", "2.0"))
# tried in order when a model's upstream endpoint answers 429 / unavailable
FALLBACK_CHAIN = [
    m.strip()
    for m in os.environ.get(
        "BRIDGE_FALLBACK",
        "step-5-preview-free,nemotron-3.5-lightning-free,nemotron-3-ultra-free,"
        "mimo-v2.6-flash-free,deepseek-v4.1-flash-free,longcat-2.5-preview-free,"
        "muse-spark-1.3-contributor-free,space-bunny-free,cat-jam-free,big-pickle",
    ).split(",")
    if m.strip()
]

_lock = threading.Lock()
_models_cache = {"at": 0.0, "models": []}


def _req(method, path, body=None, timeout=30):
    url = f"{OPENCODE_URL}{path}"
    data = json.dumps(body).encode() if body is not None else None
    r = urllib.request.Request(url, data=data, method=method)
    if data is not None:
        r.add_header("Content-Type", "application/json")
    try:
        with urllib.request.urlopen(r, timeout=timeout) as resp:
            raw = resp.read()
            return resp.status, (json.loads(raw) if raw else {})
    except urllib.error.HTTPError as e:
        raw = e.read()
        try:
            return e.code, json.loads(raw) if raw else {}
        except Exception:
            return e.code, {"raw": raw.decode("utf-8", "replace")[:500]}
    except Exception as e:
        return 0, {"error": f"{type(e).__name__}: {e}"}


def list_models():
    global _models_cache
    with _lock:
        if _models_cache["models"] and time.time() - _models_cache["at"] < 60:
            return _models_cache["models"]
    status, body = _req("GET", "/api/model", timeout=15)
    models = []
    if status == 200:
        items = body.get("data", [])
        if isinstance(items, list):
            for m in items:
                if not isinstance(m, dict):
                    continue
                if m.get("providerID") != "opencode":
                    continue
                if m.get("status") not in (None, "active"):
                    continue
                mid = m.get("id")
                if not mid:
                    continue
                cost = m.get("cost") or [{}]
                c = cost[0] if isinstance(cost, list) and cost else {}
                free = (c.get("input", 1) or 0) == 0 and (c.get("output", 1) or 0) == 0
                if not free:
                    continue
                models.append(mid)
    if not models:
        models = list(FALLBACK_MODELS)
    models = sorted(set(models))
    with _lock:
        _models_cache = {"at": time.time(), "models": models}
    return models


def normalize_model(name):
    """Accept any of: `id`, `opencode/id`, `<routerprefix>/opencode/id`, `<routerprefix>/id`."""
    if not name:
        return DEFAULT_MODEL
    parts = [p for p in name.split("/") if p]
    if not parts:
        return DEFAULT_MODEL
    if parts[0] == "opencode" and len(parts) >= 2:
        return parts[1]
    if len(parts) >= 2 and parts[1] == "opencode":
        return parts[2] if len(parts) >= 3 else DEFAULT_MODEL
    return parts[-1]


def flatten_messages(messages):
    """OpenAI messages -> single prompt string for opencode."""
    out = []
    for m in messages:
        role = m.get("role", "user")
        c = m.get("content", "")
        if isinstance(c, list):
            parts = []
            for p in c:
                if isinstance(p, dict) and p.get("type") == "text":
                    parts.append(p.get("text", ""))
                elif isinstance(p, str):
                    parts.append(p)
            c = "\n".join(x for x in parts if x)
        if not isinstance(c, str):
            c = json.dumps(c)
        c = c.strip()
        if not c:
            continue
        if role == "system":
            out.append(f"[system]\n{c}")
        elif role == "assistant":
            out.append(f"[assistant]\n{c}")
        else:
            out.append(f"[user]\n{c}")
    return "\n\n".join(out) if out else ""


def create_session(model, agent=None):
    body = {"model": {"providerID": "opencode", "id": model}}
    if agent:
        body["agent"] = agent
    status, resp = _req("POST", "/api/session", body, timeout=30)
    if status != 200:
        raise RuntimeError(f"session create failed: {status} {resp}")
    return resp["data"]["id"]


def send_prompt(session_id, text):
    status, resp = _req(
        "POST", f"/api/session/{session_id}/prompt", {"prompt": {"text": text}}, timeout=60
    )
    if status != 200:
        raise RuntimeError(f"prompt failed: {status} {resp}")
    return resp["data"]["id"]


def fetch_messages(session_id):
    status, resp = _req("GET", f"/api/session/{session_id}/message", timeout=30)
    if status != 200:
        return []
    return resp.get("data", [])


def wait_assistant(session_id, user_msg_id, deadline):
    """Poll until an assistant message that comes after user_msg_id is complete."""
    seen = set()
    while time.time() < deadline:
        msgs = fetch_messages(session_id)
        if msgs:
            found = None
            for m in msgs:
                if m.get("type") == "assistant":
                    found = m
                    break
            if found:
                key = (found.get("id"),)
                if key not in seen:
                    seen.add(key)
                if found.get("finish") or found.get("time", {}).get("completed"):
                    return found
                if found.get("error"):
                    return found
        time.sleep(POLL_INTERVAL)
    return None


def assistant_text(m):
    parts = []
    for c in m.get("content", []) or []:
        if isinstance(c, dict) and c.get("type") == "text":
            parts.append(c.get("text", ""))
    return "".join(parts)


def usage_of(m):
    t = m.get("tokens") or {}
    inp = int(t.get("input", 0) or 0)
    out = int(t.get("output", 0) or 0)
    tot = t.get("total")
    total = int(tot) if tot is not None else inp + out
    return {
        "prompt_tokens": inp,
        "completion_tokens": out,
        "total_tokens": total,
    }


class Handler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"
    server_version = "9r-bridge/1.0"

    def log_message(self, fmt, *args):
        sys.stderr.write("[bridge] %s - %s\n" % (self.address_string(), fmt % args))

    def _auth_ok(self):
        if not BRIDGE_TOKEN:
            return True
        h = self.headers.get("Authorization", "")
        return h == f"Bearer {BRIDGE_TOKEN}" or h == BRIDGE_TOKEN

    def _json(self, code, obj):
        body = json.dumps(obj).encode()
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        try:
            self.wfile.write(body)
        except Exception:
            pass

    def _sse(self, obj):
        data = f"data: {json.dumps(obj)}\n\n".encode()
        try:
            self.wfile.write(data)
            self.wfile.flush()
        except Exception:
            pass

    def do_GET(self):
        path = self.path.split("?", 1)[0]
        if path in ("/", "/health", "/healthz"):
            return self._json(200, {"ok": True, "upstream": OPENCODE_URL, "models": len(list_models())})
        if path in ("/v1/models", "/models"):
            if not self._auth_ok():
                return self._json(401, {"error": {"message": "invalid token", "type": "auth"}})
            now = int(time.time())
            data = [
                {
                    "id": m,
                    "object": "model",
                    "created": now,
                    "owned_by": "opencode",
                }
                for m in list_models()
            ]
            return self._json(200, {"object": "list", "data": data})
        return self._json(404, {"error": {"message": f"no route {path}", "type": "not_found"}})

    def do_POST(self):
        path = self.path.split("?", 1)[0]
        if path == "/v1/chat/completions":
            if not self._auth_ok():
                return self._json(401, {"error": {"message": "invalid token", "type": "auth"}})
            try:
                n = int(self.headers.get("Content-Length", "0"))
                payload = json.loads(self.rfile.read(n) or b"{}")
            except Exception as e:
                return self._json(400, {"error": {"message": f"bad json: {e}", "type": "invalid_request"}})
            return self.chat(payload)
        if path in ("/v1/completions", "/completions"):
            if not self._auth_ok():
                return self._json(401, {"error": {"message": "invalid token", "type": "auth"}})
            try:
                n = int(self.headers.get("Content-Length", "0"))
                payload = json.loads(self.rfile.read(n) or b"{}")
            except Exception as e:
                return self._json(400, {"error": {"message": f"bad json: {e}", "type": "invalid_request"}})
            msgs = [{"role": "user", "content": payload.get("prompt", "")}]
            payload["messages"] = msgs
            return self.chat(payload)
        return self._json(404, {"error": {"message": f"no route {path}", "type": "not_found"}})

    # ---------- core ----------
    def _chain(self, model):
        """Requested model first, then the fallback chain (deduped)."""
        out = [model]
        for m in FALLBACK_CHAIN:
            if m not in out:
                out.append(m)
        return out

    def chat(self, payload):
        model = normalize_model(payload.get("model"))
        stream = bool(payload.get("stream"))
        agent = payload.get("agent") or payload.get("user")
        text = flatten_messages(payload.get("messages") or [])
        if not text and payload.get("prompt"):
            text = str(payload["prompt"])
        if not text:
            return self._json(400, {"error": {"message": "no messages", "type": "invalid_request"}})

        agent = agent if isinstance(agent, str) and not agent.startswith("usr") else None
        chain = self._chain(model)

        # ---- acquire a live session: retry across models on upstream 429/unavailable
        attempts = []
        sid = uid = None
        served = model
        for mi, cand in enumerate(chain):
            for attempt in range(1, RETRIES + 1):
                try:
                    sid = create_session(cand, agent)
                    uid = send_prompt(sid, text)
                    served = cand
                    break
                except Exception as e:
                    msg = str(e)
                    transient = any(
                        k in msg for k in ("429", "unavailable", "Unavailable", "timed out", "Timeout")
                    )
                    attempts.append(f"{cand}#{attempt}:{msg[-120:]}")
                    if not transient or attempt == RETRIES:
                        break
                    time.sleep(RETRY_BACKOFF * attempt + (time.time() % 1))
            if uid:
                break
            # whole chain exhausted for this candidate -> next model
            if mi + 1 < len(chain):
                continue

        if not uid:
            return self._json(
                502,
                {
                    "error": {
                        "message": "all upstream models failed; attempts: " + " | ".join(attempts[-6:]),
                        "type": "upstream_error",
                    }
                },
            )

        deadline = time.time() + PROMPT_TIMEOUT
        cmpl_id = "chatcmpl-" + uuid.uuid4().hex[:24]
        created = int(time.time())

        if not stream:
            m = wait_assistant(sid, uid, deadline)
            if not m:
                return self._json(
                    504,
                    {"error": {"message": f"no completion within {PROMPT_TIMEOUT}s", "type": "timeout"}},
                )
            if m.get("error"):
                # upstream died mid-flight -> inline retry on the remaining chain
                err = json.dumps(m.get("error"))[:800]
                idx = chain.index(served) if served in chain else 0
                for cand in chain[idx + 1:]:
                    try:
                        rsid = create_session(cand, agent)
                        ruid = send_prompt(rsid, text)
                    except Exception:
                        continue
                    rm = wait_assistant(rsid, ruid, time.time() + PROMPT_TIMEOUT)
                    if rm and not rm.get("error"):
                        return self._json(
                            200,
                            {
                                "id": cmpl_id,
                                "object": "chat.completion",
                                "created": created,
                                "model": cand,
                                "choices": [
                                    {
                                        "index": 0,
                                        "message": {"role": "assistant", "content": assistant_text(rm)},
                                        "finish_reason": "stop",
                                    }
                                ],
                                "usage": usage_of(rm),
                            },
                        )
                return self._json(502, {"error": {"message": err, "type": "upstream_error"}})
            return self._json(
                200,
                {
                    "id": cmpl_id,
                    "object": "chat.completion",
                    "created": created,
                    "model": served,
                    "choices": [
                        {
                            "index": 0,
                            "message": {"role": "assistant", "content": assistant_text(m)},
                            "finish_reason": "stop",
                        }
                    ],
                    "usage": usage_of(m),
                },
            )

        # streaming: relay text as it grows
        self.send_response(200)
        self.send_header("Content-Type", "text/event-stream")
        self.send_header("Cache-Control", "no-cache")
        self.send_header("Connection", "keep-alive")
        self.send_header("Transfer-Encoding", "chunked")
        self.end_headers()
        self.close_connection = False

        sent = 0
        final_usage = {"prompt_tokens": 0, "completion_tokens": 0, "total_tokens": 0}
        finish_reason = "stop"
        last_ping = time.time()
        while time.time() < deadline:
            msgs = fetch_messages(sid)
            cur = None
            for m in msgs:
                if m.get("type") == "assistant":
                    cur = m
                    break
            if cur:
                txt = assistant_text(cur)
                if len(txt) > sent:
                    delta = txt[sent:]
                    sent = len(txt)
                    self._write_chunk(json.dumps({
                        "id": cmpl_id,
                        "object": "chat.completion.chunk",
                        "created": created,
                        "model": served,
                        "choices": [{"index": 0, "delta": {"content": delta}, "finish_reason": None}],
                    }).encode() + b"\n\n")
                final_usage = usage_of(cur)
                if cur.get("finish") or cur.get("time", {}).get("completed") or cur.get("error"):
                    finish_reason = "stop"
                    break
            if time.time() - last_ping > 10:
                self._write_chunk(b": ping\n\n")
                last_ping = time.time()
            time.sleep(POLL_INTERVAL)

        self._write_chunk(json.dumps({
            "id": cmpl_id,
            "object": "chat.completion.chunk",
            "created": created,
            "model": served,
            "choices": [{"index": 0, "delta": {}, "finish_reason": finish_reason}],
            "usage": final_usage,
        }).encode() + b"\n\n")
        self._write_chunk(b"data: [DONE]\n\n")
        self._write_chunk(b"")

    def _write_chunk(self, data):
        try:
            self.wfile.write(b"%x\r\n" % len(data) + data + b"\r\n")
            self.wfile.flush()
        except Exception:
            self.close_connection = True


def main():
    srv = ThreadingHTTPServer((LISTEN_HOST, LISTEN_PORT), Handler)
    srv.daemon_threads = True
    sys.stderr.write(f"[bridge] listening on http://{LISTEN_HOST}:{LISTEN_PORT} upstream={OPENCODE_URL}\n")
    sys.stderr.write(f"[bridge] auth={'ON' if BRIDGE_TOKEN else 'OFF'} default_model={DEFAULT_MODEL}\n")
    srv.serve_forever()


if __name__ == "__main__":
    main()
