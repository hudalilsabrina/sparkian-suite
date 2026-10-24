#!/usr/bin/env python3
"""
Sparkian → OpenAI-compatible adapter for 9router.

9router speaks OpenAI; Sparkian speaks its own /v1/chat format. This adapter
bridges them so 9router can route `sparkian/*` model names to Sparkian accounts.

  9router  →  POST /v1/chat/completions (OpenAI)
  adapter  →  Firebase signInWithPassword → idToken (via residential proxy bridge)
  adapter  →  POST api.sparkian.com/v1/chat (Sparkian SSE via proxy)
  adapter  →  translate SSE back to OpenAI format
  9router  ←  OpenAI SSE / JSON

Auth: each 9router connection stores `email:password` as the apiKey.
The adapter parses it, signs in (caching the idToken ~50 min), and forwards.
"""

import json
import sys
import time
import urllib.error
import urllib.request
import uuid
from http.server import HTTPServer, BaseHTTPRequestHandler

FB_KEY_URL = "https://sparkian.com/__/firebase/init.json"
ITK = "https://identitytoolkit.googleapis.com/v1"
API = "https://api.sparkian.com"
PROXY = "http://127.0.0.1:10801"
UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/133.0.0.0 Safari/537.36")

# model slug mapping: sparkian/* → Sparkian internal slug
MODEL_MAP = {
    "sparkian/claude-haiku-5.5": "anthropic/claude-haiku-5.5",
    "sparkian/gemini-3.8-flash": "google/gemini-3.8-flash",
    "sparkian/deepseek-v4-flash": "deepseek/deepseek-v4-flash",
    "sparkian/qwen3.8-flash": "alibaba/qwen3.8-flash",
    "sparkian/gemini-3.1-flash-image": "google/gemini-3.1-flash-lite-image",
}

_fb_key = None
_token_cache = {}  # email → (idToken, expires_at, workspace_slug)


def get_opener():
    if PROXY:
        return urllib.request.build_opener(
            urllib.request.ProxyHandler({"http": PROXY, "https": PROXY}))
    return urllib.request.build_opener()


def get_fb_key():
    global _fb_key
    if _fb_key:
        return _fb_key
    try:
        with open("/tmp/fbkey.txt") as f:
            k = f.read().strip()
            if k:
                _fb_key = k
                return _fb_key
    except Exception:
        pass

    req = urllib.request.Request(FB_KEY_URL, headers={"User-Agent": UA})
    opener = get_opener()
    with opener.open(req, timeout=15) as r:
        _fb_key = json.loads(r.read())["apiKey"]
    return _fb_key


def firebase_signin(email, password):
    """Sign in and return (idToken, workspace_slug). Cached ~50 min."""
    now = time.time()
    cached = _token_cache.get(email)
    if cached and cached[1] > now + 120:
        return cached[0], cached[2]

    key = get_fb_key()
    body = json.dumps({
        "returnSecureToken": True, "email": email,
        "password": password, "clientType": "CLIENT_TYPE_WEB",
    }).encode()
    req = urllib.request.Request(
        f"{ITK}/accounts:signInWithPassword?key={key}", data=body, method="POST",
        headers={"Content-Type": "application/json", "User-Agent": UA,
                 "Origin": "https://sparkian.com", "Referer": "https://sparkian.com/"})
    opener = get_opener()
    with opener.open(req, timeout=30) as r:
        d = json.loads(r.read())

    tok = d["idToken"]
    expires_in = int(d.get("expiresIn", 3600))

    # bootstrap workspace
    b2 = json.dumps({}).encode()
    r2 = urllib.request.Request(
        f"{API}/auth/session", data=b2, method="POST",
        headers={"Authorization": f"Bearer {tok}", "Content-Type": "application/json",
                 "User-Agent": UA, "Origin": "https://sparkian.com"})
    ws = ""
    try:
        with opener.open(r2, timeout=30) as resp:
            ctx = json.loads(resp.read())
            ws = ((ctx.get("context") or {}).get("workspace") or {}).get("slug", "")
    except Exception:
        pass

    if not ws:
        ws = email.split("@")[0]

    _token_cache[email] = (tok, now + expires_in, ws)
    return tok, ws


def openai_to_sparkian(body, token, ws_slug):
    """Convert an OpenAI chat completion request to Sparkian's format."""
    model = body.get("model", "sparkian/claude-haiku-5.5")
    slug = MODEL_MAP.get(model, model.replace("sparkian/", "", 1))
    if "/" not in slug:
        slug = "anthropic/claude-haiku-5.5"

    messages = body.get("messages", [])
    system_msgs = [m for m in messages if m.get("role") == "system"]
    user_msgs = [m for m in messages if m.get("role") in ("user", "assistant")]
    
    parts = []
    # If system messages exist, prepend them into instructions
    if system_msgs:
        sys_text = "\n\n".join(
            (m.get("content", "") if isinstance(m.get("content"), str) else " ".join(p.get("text", "") for p in m.get("content", [])))
            for m in system_msgs
        )
        if sys_text:
            parts.append({"type": "text", "text": f"[System Instructions]:\n{sys_text}\n\n"})

    last = user_msgs[-1] if user_msgs else (messages[-1] if messages else {})
    content = last.get("content", "")
    if isinstance(content, list):
        content = " ".join(p.get("text", "") for p in content if p.get("type") == "text")

    if content:
        parts.append({"type": "text", "text": content})

    if not parts:
        parts.append({"type": "text", "text": "Hello"})

    return {
        "action": "send",
        "surface": "chat",
        "client_timezone": "America/Chicago",
        "conversation_id": None,
        "expected_leaf_message_id": None,
        "idempotency_key": str(uuid.uuid4()),
        "model_slug": slug,
        "parent_message_id": None,
        "workspace_slug": ws_slug,
        "parts": parts,
        "tools_mode": "auto",
    }


def parse_sparkian_sse(raw):
    """Extract text deltas from a Sparkian SSE stream."""
    lines = raw.split("\n")
    text_parts = []
    for line in lines:
        if not line.startswith("data:"):
            continue
        payload = line[5:].strip()
        if not payload or payload == "[DONE]":
            continue
        try:
            d = json.loads(payload)
        except Exception:
            continue
        t = d.get("type", "")
        if t == "text-delta":
            text_parts.append(d.get("delta", d.get("textDelta", "")))
        elif t == "custom" and d.get("kind") == "ai-sdk.text-delta":
            text_parts.append(d.get("data", {}).get("textDelta", ""))
        elif "text" in d and isinstance(d["text"], str):
            text_parts.append(d["text"])
    return "".join(text_parts)


def make_openai_sse(text, model, request_id):
    """Wrap plain text into OpenAI SSE chunks."""
    chunks = [
        {
            "id": request_id, "object": "chat.completion.chunk",
            "created": int(time.time()), "model": model,
            "choices": [{"index": 0, "delta": {"role": "assistant"}, "finish_reason": None}],
        },
        {
            "id": request_id, "object": "chat.completion.chunk",
            "created": int(time.time()), "model": model,
            "choices": [{"index": 0, "delta": {"content": text}, "finish_reason": None}],
        },
        {
            "id": request_id, "object": "chat.completion.chunk",
            "created": int(time.time()), "model": model,
            "choices": [{"index": 0, "delta": {}, "finish_reason": "stop"}],
        }
    ]
    return "".join(f"data: {json.dumps(c)}\n\n" for c in chunks) + "data: [DONE]\n\n"


class Handler(BaseHTTPRequestHandler):
    def log_message(self, *a):
        pass

    def _send(self, code, body, ctype="application/json"):
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Allow-Headers", "*")
        self.send_header("Access-Control-Allow-Methods", "GET, POST, OPTIONS")
        self.end_headers()
        self.wfile.write(body.encode() if isinstance(body, str) else body)

    def do_OPTIONS(self):
        self._send(200, "")

    def do_GET(self):
        if self.path in ("/v1/models", "/models"):
            models = []
            for m in MODEL_MAP:
                models.append({"id": m, "object": "model", "owned_by": "sparkian"})
            self._send(200, json.dumps({"object": "list", "data": models}))
        elif self.path == "/health":
            self._send(200, json.dumps({"status": "ok"}))
        else:
            self._send(404, json.dumps({"error": "not found"}))

    def do_POST(self):
        if self.path not in ("/v1/chat/completions", "/chat/completions"):
            self._send(404, json.dumps({"error": "not found"}))
            return

        length = int(self.headers.get("Content-Length", 0))
        raw = self.rfile.read(length) if length else b"{}"
        try:
            body = json.loads(raw)
        except Exception:
            self._send(400, json.dumps({"error": "invalid JSON"}))
            return

        # auth: apiKey from header or body
        auth = self.headers.get("Authorization", "")
        api_key = ""
        if auth.startswith("Bearer "):
            api_key = auth[7:].strip()
        if not api_key:
            api_key = body.get("apiKey", "")

        if ":" not in api_key:
            self._send(401, json.dumps({"error": "invalid apiKey (expected email:password)"}))
            return

        email, password = api_key.split(":", 1)
        model = body.get("model", "sparkian/claude-haiku-5.5")
        request_id = f"chatcmpl-{uuid.uuid4().hex[:24]}"
        stream = body.get("stream", False)

        try:
            tok, ws = firebase_signin(email.strip(), password.strip())
        except Exception as e:
            self._send(401, json.dumps({"error": f"firebase auth failed: {e}"}))
            return

        spark_body = openai_to_sparkian(body, tok, ws)
        data = json.dumps(spark_body).encode()
        req = urllib.request.Request(
            f"{API}/v1/chat", data=data, method="POST",
            headers={"Authorization": f"Bearer {tok}", "Content-Type": "application/json",
                     "User-Agent": UA, "Origin": "https://sparkian.com",
                     "Accept": "text/event-stream"})

        opener = get_opener()
        try:
            with opener.open(req, timeout=120) as r:
                raw_sse = r.read().decode("utf-8", "replace")
        except urllib.error.HTTPError as e:
            err = e.read().decode()[:300]
            self._send(e.code, json.dumps({"error": err}))
            return
        except Exception as e:
            self._send(502, json.dumps({"error": f"{type(e).__name__}: {e}"}))
            return

        text = parse_sparkian_sse(raw_sse)

        if stream:
            self._send(200, make_openai_sse(text, model, request_id), "text/event-stream")
        else:
            resp = {
                "id": request_id, "object": "chat.completion",
                "created": int(time.time()), "model": model,
                "choices": [{"index": 0, "message": {"role": "assistant", "content": text},
                             "finish_reason": "stop"}],
                "usage": {"prompt_tokens": 0, "completion_tokens": 0, "total_tokens": 0},
            }
            self._send(200, json.dumps(resp))


def main():
    port = 20128
    if "--port" in sys.argv:
        idx = sys.argv.index("--port")
        port = int(sys.argv[idx + 1])

    server = HTTPServer(("127.0.0.1", port), Handler)
    print(f"[sparkian-adapter] listening on http://127.0.0.1:{port}/v1", flush=True)
    server.serve_forever()


if __name__ == "__main__":
    main()
