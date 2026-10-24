#!/usr/bin/env python3
"""
Sparkian account farm — pure HTTP, no browser, no captcha, no phone.

Flow (all verified against the live API):
  1. create a Tempik inbox
  2. POST api.sparkian.com/auth/register   {email, name, password} -> {"created":true}
     (Firebase client signUp is ADMIN_ONLY_OPERATION; the backend creates the
      user with the Admin SDK, so this endpoint is the only entry point)
  3. POST identitytoolkit accounts:signInWithPassword -> idToken
  4. POST accounts:sendOobCode {requestType: VERIFY_EMAIL}
  5. read the verification mail from Tempik, pull `oobCode` out of the body
  6. POST accounts:update {oobCode} -> emailVerified = true
  7. POST api.sparkian.com/auth/session -> bootstraps user + default workspace
  8. smoke-test POST api.sparkian.com/v1/chat (SSE) with a real model

Usage:
  python3 sparkian_farm.py -n 1
  python3 sparkian_farm.py -n 10
"""

import argparse
import json
import os
import random
import re
import string
import sys
import time
import urllib.error
import urllib.request
import uuid

sys.path.insert(0, "/root/qoder-suite")

FB_KEY = None
FB_KEY_URL = "https://sparkian.com/__/firebase/init.json"
API = "https://api.sparkian.com"
ITK = "https://identitytoolkit.googleapis.com/v1"
OUT = "/root/sparkian/accounts.jsonl"

DOMAINS = [
    "xentranetwork.me", "holysmp.me", "websurf.web.id", "fantech.tech",
    "mcgg.me", "arinata.my.id", "arinataskuy.my.id", "arinatatech.my.id",
    "arkheos.web.id", "baks.my.id", "mediterranean.biz.id", "ngurah.biz.id",
    "paragadisnet.my.id", "prabowolegowo.web.id", "terlaluai.my.id",
]

FIRST = ["Rian", "Bayu", "Dimas", "Adit", "Fajar", "Ilham", "Eko", "Putra", "Arif"]
LAST = ["Permana", "Santoso", "Nugroho", "Pratama", "Saputra", "Hidayat", "Wijaya"]

UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/133.0.0.0 Safari/537.36")

DEFAULT_MODEL = "anthropic/claude-haiku-5.5"


PROXY = None          # e.g. "http://127.0.0.1:10801"
BRIDGE = "http://127.0.0.1:10801"


def rotate_ip():
    """Ask the local bridge for a fresh residential sticky session."""
    try:
        op = urllib.request.build_opener(urllib.request.ProxyHandler({}))
        with op.open(BRIDGE + "/__new_session", timeout=10) as r:
            return r.read().decode().strip()
    except Exception as e:
        return f"ERR {e}"


def http(url, method="GET", body=None, token=None, timeout=40, stream=False):
    h = {"User-Agent": UA, "Accept": "text/event-stream" if stream else "application/json",
         "Origin": "https://sparkian.com", "Referer": "https://sparkian.com/"}
    if token:
        h["Authorization"] = f"Bearer {token}"
    data = None
    if body is not None:
        h["Content-Type"] = "application/json"
        data = json.dumps(body).encode()
    req = urllib.request.Request(url, data=data, headers=h, method=method)
    opener = (urllib.request.build_opener(
                  urllib.request.ProxyHandler({"http": PROXY, "https": PROXY}))
              if PROXY else urllib.request.build_opener())
    try:
        with opener.open(req, timeout=timeout) as r:
            return r.status, r.read().decode("utf-8", "replace")
    except urllib.error.HTTPError as e:
        return e.code, e.read().decode("utf-8", "replace")
    except Exception as e:
        return 0, f"{type(e).__name__}: {e}"


def firebase_key():
    global FB_KEY
    if FB_KEY:
        return FB_KEY
    st, body = http(FB_KEY_URL)
    if st == 200:
        FB_KEY = json.loads(body)["apiKey"]
    else:
        raise RuntimeError(f"cannot read firebase config ({st})")
    return FB_KEY


def gen_password(n=14):
    b = "".join(random.choices(string.ascii_lowercase, k=5))
    b += "".join(random.choices(string.ascii_uppercase, k=4))
    b += "".join(random.choices(string.digits, k=3))
    b += random.choice("!@#$%^&*")
    return "".join(random.sample(b, len(b)))


def read_oobcode(tm, email, tries=15, gap=5):
    """Poll the Tempik inbox and pull oobCode out of the verification mail."""
    for _ in range(tries):
        try:
            msgs = tm.get_messages(email) or []
        except Exception:
            msgs = []
        for m in msgs:
            mid = m.get("id")
            if not mid:
                continue
            detail = fetch_message_detail(tm, email, mid)
            body = (detail.get("body") or "") + " " + (detail.get("body_html") or "")
            mm = re.search(r"oobCode=([A-Za-z0-9_-]+)", body)
            if mm:
                return mm.group(1)
        time.sleep(gap)
    return None


def fetch_message_detail(tm, email, mid):
    from src.config import TEMPIK_BASE
    base = TEMPIK_BASE.rstrip("/")
    sid = tm.init_session()
    url = f"{base}/inboxes/{email}/messages/{mid}"
    req = urllib.request.Request(url, headers={
        "Accept": "application/json", "x-session-id": sid, "User-Agent": UA})
    try:
        with urllib.request.urlopen(req, timeout=25) as r:
            return json.loads(r.read())
    except Exception:
        return {}


def create_one(idx, tm, domain=None, model=DEFAULT_MODEL, test_chat=False):
    key = firebase_key()
    domain = domain or random.choice(DOMAINS)
    local = "sp" + "".join(random.choices(string.ascii_lowercase + string.digits, k=9))
    rec = {"idx": idx, "status": "pending", "domain": domain}

    try:
        email = tm.create_inbox(local_part=local, domain=domain)
    except Exception as e:
        rec["status"] = f"inbox_fail:{type(e).__name__}"
        return rec

    pwd = gen_password()
    name = f"{random.choice(FIRST)} {random.choice(LAST)}"
    rec.update(email=email, password=pwd, name=name)
    print(f"\n{'='*62}\n[{idx}] {email}", flush=True)

    # 1) register through the backend (Firebase client signup is admin-only)
    st, body = 0, ""
    for attempt in range(4):
        st, body = http(f"{API}/auth/register", "POST",
                        {"email": email, "name": name, "password": pwd})
        if st == 200:
            break
        if st == 429 and attempt < 3:
            wait = 20 * (attempt + 1)
            print(f"  [1] 429 rate limited — rotating IP, wait {wait}s", flush=True)
            rotate_ip()
            time.sleep(wait)
            continue
        break
    print(f"  [1] register {st} {body[:80]}", flush=True)
    if st != 200:
        rec.update(status="register_failed", detail=body[:150])
        return rec

    # 2) sign in to obtain an idToken
    st, body = http(f"{ITK}/accounts:signInWithPassword?key={key}", "POST",
                    {"returnSecureToken": True, "email": email,
                     "password": pwd, "clientType": "CLIENT_TYPE_WEB"})
    if st != 200:
        rec.update(status="signin_failed", detail=body[:150])
        return rec
    tok = json.loads(body)["idToken"]
    print(f"  [2] signin ok (token {len(tok)})", flush=True)

    # 3) ask Firebase to send the verification mail
    st, body = http(f"{ITK}/accounts:sendOobCode?key={key}", "POST",
                    {"requestType": "VERIFY_EMAIL", "idToken": tok})
    print(f"  [3] sendOobCode {st}", flush=True)

    # 4) read the oobCode out of the Tempik inbox
    oob = read_oobcode(tm, email)
    if not oob:
        rec.update(status="no_verification_mail")
        return rec
    print(f"  [4] oobCode {oob[:20]}...", flush=True)

    # 5) confirm the address
    st, body = http(f"{ITK}/accounts:update?key={key}", "POST", {"oobCode": oob})
    if st != 200:
        rec.update(status="verify_failed", detail=body[:150])
        return rec
    print("  [5] email verified", flush=True)

    # 6) refresh the token so it carries email_verified=true
    st, body = http(f"{ITK}/accounts:signInWithPassword?key={key}", "POST",
                    {"returnSecureToken": True, "email": email,
                     "password": pwd, "clientType": "CLIENT_TYPE_WEB"})
    if st != 200:
        rec.update(status="resignin_failed")
        return rec
    tok = json.loads(body)["idToken"]

    # 7) bootstrap user + default workspace
    st, body = http(f"{API}/auth/session", "POST", {}, token=tok)
    if st != 200:
        rec.update(status="session_failed", detail=body[:150])
        return rec
    ctx = json.loads(body)
    ws = ((ctx.get("context") or {}).get("workspace")
          or ctx.get("workspace") or {})
    rec.update(workspace_id=ws.get("id"), workspace_slug=ws.get("slug"),
               user_id=(ctx.get("user") or {}).get("id"))
    print(f"  [6] workspace {ws.get('slug')} ok", flush=True)

    if not test_chat:
        rec.update(status="active", id_token=tok, chat_ok=None, model=model)
        print("  [7] chat test skipped (--test-chat to enable)", flush=True)
        return rec

    # 8) smoke-test the chat endpoint
    chat_body = {
        "action": "send", "surface": "chat",
        "client_timezone": "America/Chicago",
        "conversation_id": None, "expected_leaf_message_id": None,
        "idempotency_key": str(uuid.uuid4()),
        "model_slug": model, "parent_message_id": None,
        "workspace_slug": ws.get("slug"),
        "parts": [{"type": "text", "text": "Reply with exactly: OK"}],
        "tools_mode": "auto",
    }
    st, body = http(f"{API}/v1/chat", "POST", chat_body, token=tok, stream=True, timeout=90)
    ok = st == 200 and '"type":"start"' in body
    print(f"  [7] chat {st} -> {'OK' if ok else body[:120]}", flush=True)

    rec.update(status="active" if ok else "created_no_chat", id_token=tok,
               chat_ok=ok, model=model)
    return rec


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("-n", "--count", type=int, default=1)
    ap.add_argument("--domain")
    ap.add_argument("--model", default=DEFAULT_MODEL)
    ap.add_argument("--proxy", action="store_true", help="route through the rotating bridge")
    ap.add_argument("--per-ip", type=int, default=5,
                    help="accounts per residential IP before rotating (measured budget: 5)")
    ap.add_argument("--gap", type=int, default=40,
                    help="seconds between accounts on the same IP (below ~30 triggers the burst limit)")
    ap.add_argument("--test-chat", action="store_true", help="run a real model call per account")
    a = ap.parse_args()

    global PROXY
    if a.proxy:
        PROXY = BRIDGE

    os.makedirs("/root/sparkian", exist_ok=True)
    from src.tempmail import TempikClient
    tm = TempikClient()

    results = []
    on_this_ip = 0
    for i in range(1, a.count + 1):
        if a.proxy and on_this_ip % a.per_ip == 0:
            print(f"\n[ip] rotating (every {a.per_ip} accounts)", flush=True)
            rotate_ip()
            time.sleep(1.5)
        elif i > 1:
            time.sleep(a.gap)
        on_this_ip += 1
        try:
            r = create_one(i, tm, a.domain, a.model, test_chat=a.test_chat)
            if r.get("status") == "register_failed" and "429" in str(r.get("detail", "")):
                print("[ip] rate limited — rotating and retrying", flush=True)
                rotate_ip()
                on_this_ip = 0
                time.sleep(5)
                r = create_one(i, tm, a.domain, a.model, test_chat=a.test_chat)
        except Exception as e:
            r = {"idx": i, "status": f"exc:{type(e).__name__}", "error": str(e)[:150]}
            print(f"  [✗] {r['error']}", flush=True)
        results.append(r)
        with open(OUT, "a") as f:
            f.write(json.dumps(r) + "\n")
        if i < a.count:
            time.sleep(random.uniform(2, 5))

    print("\n" + "=" * 62)
    ok = sum(1 for r in results if r["status"] == "active")
    print(f"  active {ok}/{len(results)}")
    for r in results:
        print(f"   [{r['idx']}] {r['status']:22} {r.get('email','')}")
    print("=" * 62)


if __name__ == "__main__":
    main()
