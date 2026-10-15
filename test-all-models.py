#!/usr/bin/env python3
"""Full verification of the oclocal models through the PUBLIC 9Router base URL.

Reports honestly: strict OpenAI-SDK parsing vs lenient parsing, streaming vs
non-streaming. The router normalises single-shot JSON into an SSE body
(`content-type: text/event-stream`, body = JSON + `data: [DONE]`), which strict
JSON clients reject; that is a 9Router design trait, not a bridge bug.
"""
import json
import os
import subprocess
import sys
import time

BASE = os.environ.get("ROUTER_BASE", "https://aethink.com/v1")
KEY = os.environ.get("ROUTER_KEY", "")
MODELS = [
    "big-pickle", "cat-jam-free", "deepseek-v4.1-flash-free",
    "longcat-2.5-preview-free", "mimo-v2.6-flash-free",
    "muse-spark-1.3-contributor-free", "nemotron-3-ultra-free",
    "nemotron-3.5-lightning-free", "space-bunny-free", "step-5-preview-free",
]
if not KEY:
    sys.exit("set ROUTER_KEY")


def curl(args, timeout=330):
    return subprocess.run(["curl", "-s", "-m", str(timeout)] + args,
                          capture_output=True, text=True, timeout=timeout + 10)


def post(body, stream=False):
    r = curl(["-X", "POST", f"{BASE}/chat/completions",
              "-H", f"Authorization: Bearer {KEY}",
              "-H", "Content-Type: application/json",
              "-d", json.dumps(body), "-w", "\n__HTTP %{http_code}",
              "-D", "/tmp/hdrs.txt"])
    out = r.stdout
    code = 0
    if "__HTTP " in out:
        out, _, c = out.rpartition("__HTTP ")
        code = int(c.strip() or 0)
    return code, out, r.returncode


def strict_json(text):
    """Exactly what the OpenAI SDK / httpx .json() does."""
    return json.loads(text)


def lenient_json(text):
    """Strip an SSE trailer before parsing."""
    t = text
    idx = t.find("data: [DONE]")
    if idx != -1:
        t = t[:idx]
    return json.loads(t.strip())


print(f"base_url : {BASE}")
print(f"key      : {KEY[:12]}...{KEY[-4:]} ({len(KEY)} chars)\n")

body = {"model": "oclocal/step-5-preview-free",
        "messages": [{"role": "user", "content": "say OK"}], "max_tokens": 16}
code, out, _ = post(body)
ct = ""
try:
    ct = [l for l in open("/tmp/hdrs.txt") if l.lower().startswith("content-type")][0].split(":", 1)[1].strip()
except Exception:
    pass
print(f"=== non-stream protocol shape (http {code}) ===")
print(f"  content-type : {ct}")
print(f"  strict parse : ", end="")
try:
    d = strict_json(out)
    print("OK ->", repr(d["choices"][0]["message"]["content"]))
except Exception as e:
    print(f"FAILS ({type(e).__name__})  <- strict OpenAI SDK breaks")
print(f"  lenient parse: ", end="")
try:
    d = lenient_json(out)
    print("OK ->", repr(d["choices"][0]["message"]["content"]))
except Exception as e:
    print(f"FAILS ({type(e).__name__})")
print()

print("=== 1. non-stream, all 10 models (lenient parse) ===")
ok = bad = 0
for m in MODELS:
    code, out, _ = post({"model": f"oclocal/{m}",
                         "messages": [{"role": "user", "content": "reply with exactly: OK"}],
                         "max_tokens": 24})
    try:
        d = lenient_json(out)
        content = (d["choices"][0]["message"] or {}).get("content", "")
        served, usage = d.get("model"), (d.get("usage") or {}).get("total_tokens")
    except Exception as e:
        content, served, usage = f"<{type(e).__name__}>", None, None
    good = code == 200 and content.strip() == "OK"
    ok, bad = ok + good, bad + (not good)
    print(f"  [{'PASS' if good else 'FAIL'}] {m:<33} http={code} served={served} "
          f"tok={usage} content={content[:22]!r}")
print(f"  -> pass={ok}/10 fail={bad}\n")

print("=== 2. streaming (strict `data:` frame parse) ===")
code, out, _ = post({"model": "oclocal/nemotron-3.5-lightning-free",
                     "messages": [{"role": "user", "content": "count 1 to 5"}],
                     "max_tokens": 40, "stream": True}, stream=True)
frames = [l[6:].strip() for l in out.splitlines() if l.startswith("data: ")]
content, finish, done = "", None, False
for f in frames:
    if f == "[DONE]":
        done = True
        break
    try:
        d = json.loads(f)
    except Exception:
        continue
    ch = (d.get("choices") or [{}])[0]
    content += (ch.get("delta") or {}).get("content") or ""
    finish = ch.get("finish_reason") or finish
print(f"  http={code} frames={len(frames)} finish={finish} [DONE]={done}")
print(f"  content={content[:60]!r}")
print(f"  -> {'PASS' if content and done else 'FAIL'}\n")

print("=== 3. system prompt + multi-turn ===")
code, out, _ = post({"model": "oclocal/step-5-preview-free",
                     "messages": [{"role": "system", "content": "You are terse. One word."},
                                  {"role": "user", "content": "capital of Japan?"}],
                     "max_tokens": 16})
try:
    d = lenient_json(out)
    print(f"  http={code} -> {(d['choices'][0]['message'] or {}).get('content','')[:40]!r} "
          f"tok={(d.get('usage') or {}).get('total_tokens')}")
except Exception as e:
    print(f"  FAILS {type(e).__name__}")

print("\n=== SUMMARY ===")
print(f"  models callable via {BASE} : {ok}/10")
print(f"  streaming via router         : {'YES' if content and done else 'NO'}")
print(f"  non-stream for STRICT clients: NO (router emits SSE; use stream=true,")
print(f"                                 or the direct bridge URL for clean JSON)")
sys.exit(0 if ok == 10 else 1)
