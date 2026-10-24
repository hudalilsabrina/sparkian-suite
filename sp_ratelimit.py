#!/usr/bin/env python3
"""
Measure Sparkian's per-IP registration budget.

Answers: how many accounts can one residential IP create before
AUTH_RATE_LIMITED, and does a cool-down restore the budget?

Strategy under test (single pinned IP, no rotation):
  attempt 1..N with a fixed gap, then a long cool-down, then one more attempt.

Usage:
  python3 sp_ratelimit.py                 # 6 attempts, 45s gap, 180s cooldown
  python3 sp_ratelimit.py --gap 60 --n 6
"""

import argparse
import json
import random
import string
import sys
import time
import urllib.request

sys.path.insert(0, "/root/qoder-suite")

BRIDGE = "http://127.0.0.1:10801"
API = "https://api.sparkian.com"
DOMAINS = ["xentranetwork.me", "holysmp.me", "websurf.web.id", "fantech.tech",
           "mcgg.me", "arinata.my.id", "arinataskuy.my.id", "arinatatech.my.id",
           "arkheos.web.id", "baks.my.id", "mediterranean.biz.id", "ngurah.biz.id",
           "paragadisnet.my.id", "prabowolegowo.web.id", "terlaluai.my.id"]
UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/133.0.0.0 Safari/537.36")


def proxy_geo():
    op = urllib.request.build_opener(
        urllib.request.ProxyHandler({"http": BRIDGE, "https": BRIDGE}))
    try:
        with op.open("https://ipinfo.io/json", timeout=20) as r:
            d = json.loads(r.read())
            return d.get("ip"), d.get("city")
    except Exception as e:
        return None, str(e)[:40]


def pin_ip():
    """Keep the current SID (no rotation) so every attempt shares one IP."""
    ip, city = proxy_geo()
    return ip, city


def register(email, name, pwd):
    body = json.dumps({"email": email, "name": name, "password": pwd}).encode()
    req = urllib.request.Request(
        f"{API}/auth/register", data=body, method="POST",
        headers={"Content-Type": "application/json", "User-Agent": UA,
                 "Origin": "https://sparkian.com", "Referer": "https://sparkian.com/signup"})
    op = urllib.request.build_opener(
        urllib.request.ProxyHandler({"http": BRIDGE, "https": BRIDGE}))
    try:
        with op.open(req, timeout=40) as r:
            return r.status, r.read().decode()[:120]
    except urllib.error.HTTPError as e:
        return e.code, e.read().decode()[:120]
    except Exception as e:
        return 0, f"{type(e).__name__}: {e}"


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--n", type=int, default=6)
    ap.add_argument("--gap", type=int, default=45)
    ap.add_argument("--cooldown", type=int, default=180)
    a = ap.parse_args()

    from src.tempmail import TempikClient
    tm = TempikClient()

    ip, city = pin_ip()
    print(f"pinned IP: {ip} ({city})")
    print(f"plan: {a.n} attempts, {a.gap}s gap, then {a.cooldown}s cooldown + 1 retry\n")

    results = []
    for i in range(1, a.n + 1):
        domain = random.choice(DOMAINS)
        local = "rl" + "".join(random.choices(string.ascii_lowercase + string.digits, k=8))
        try:
            email = tm.create_inbox(local_part=local, domain=domain)
        except Exception as e:
            print(f"[{i}] inbox fail {type(e).__name__}")
            continue
        st, body = register(email, "Rate Probe", "Test!Pass12345")
        tag = "OK" if st == 200 else ("RATE" if st == 429 else str(st))
        print(f"[{i}] {tag:5} {email}", flush=True)
        results.append({"attempt": i, "status": st, "email": email})
        if i < a.n:
            time.sleep(a.gap)

    ok = sum(1 for r in results if r["status"] == 200)
    first429 = next((r["attempt"] for r in results if r["status"] == 429), None)
    print(f"\n--- {ok} success, first 429 at attempt {first429} ---")

    if first429:
        print(f"\ncooling down {a.cooldown}s, then one retry on the SAME IP...")
        time.sleep(a.cooldown)
        domain = random.choice(DOMAINS)
        local = "rc" + "".join(random.choices(string.ascii_lowercase + string.digits, k=8))
        email = tm.create_inbox(local_part=local, domain=domain)
        st, body = register(email, "Rate Probe", "Test!Pass12345")
        print(f"after cooldown: {st} {body[:80]}")
        print("=> cooldown RESTORES the budget" if st == 200 else "=> cooldown does NOT restore it")

    print("\nsummary:", json.dumps({"success": ok, "first_429": first429, "n": len(results)}))


if __name__ == "__main__":
    main()
