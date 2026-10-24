#!/usr/bin/env python3
"""
Sparkian Batch Generator via Camoufox Turnstile Solver.

Bypasses Cloudflare Turnstile on Sparkian signup form using Camoufox:
1. Generates Tempik inbox
2. Loads https://sparkian.com/signup via Camoufox
3. Unhides Turnstile container and injects form fields
4. Solves Turnstile via coordinate probe click
5. Awaits redirect to /verify-email
6. Reads inbox via Tempik REST API, extracts oobCode
7. Verifies email via Firebase Identity Toolkit
8. Bootstraps default workspace via /auth/session
9. Stores account in camoufox_accounts.jsonl & adds to 9router connection pool

Usage:
  python3 sparkian_camoufox_farm.py -n 1
  python3 sparkian_camoufox_farm.py -n 5
"""

import argparse
import asyncio
import json
import os
import random
import re
import sqlite3
import string
import sys
import time
import urllib.error
import urllib.request
import uuid

# Use local tempmail client first
try:
    from tempmail import TempikClient, TEMPIK_BASE
except ImportError:
    sys.path.insert(0, "/root/qoder-suite")
    from src.tempmail import TempikClient
    from src.config import TEMPIK_BASE

from camoufox.async_api import AsyncCamoufox

DOMAINS = [
    "holysmp.me", "xentranetwork.me", "websurf.web.id", "fantech.tech",
    "mcgg.me", "arinata.my.id", "arinataskuy.my.id", "arinatatech.my.id",
    "arkheos.web.id", "baks.my.id", "mediterranean.biz.id", "ngurah.biz.id",
    "paragadisnet.my.id", "prabowolegowo.web.id", "terlaluai.my.id"
]

FIRST = ["Rian", "Bayu", "Dimas", "Adit", "Fajar", "Ilham", "Eko", "Putra", "Arif", "Budi", "Surya"]
LAST = ["Permana", "Santoso", "Nugroho", "Pratama", "Saputra", "Hidayat", "Wijaya", "Kusuma"]

PROXY = "http://127.0.0.1:10801"
FB_KEY_PATH = "/tmp/fbkey.txt"
OUT_FILE = "/root/sparkian/camoufox_accounts.jsonl"
DB_PATH = "/var/lib/9router/db/data.sqlite"


def get_fb_key():
    if os.path.exists(FB_KEY_PATH):
        with open(FB_KEY_PATH) as f:
            k = f.read().strip()
            if k:
                return k
    req = urllib.request.Request(
        "https://sparkian.com/__/firebase/init.json",
        headers={"User-Agent": "Mozilla/5.0"}
    )
    with urllib.request.urlopen(req, timeout=15) as r:
        k = json.loads(r.read())["apiKey"]
        with open(FB_KEY_PATH, "w") as f:
            f.write(k)
        return k


def gen_password():
    chars = string.ascii_letters + string.digits + "!@#$%^&*"
    return "Spk#" + "".join(random.choices(chars, k=10)) + "1"


def get_opener():
    return urllib.request.build_opener(
        urllib.request.ProxyHandler({"http": PROXY, "https": PROXY})
    )


def extract_oob_code(tm, email, tries=15, gap=3):
    base = TEMPIK_BASE.rstrip("/")
    for _ in range(tries):
        try:
            msgs = tm.get_messages(email) or []
            for m in msgs:
                mid = m.get("id")
                if not mid:
                    continue
                sid = tm.init_session()
                url = f"{base}/inboxes/{email}/messages/{mid}"
                req = urllib.request.Request(
                    url,
                    headers={
                        "Accept": "application/json",
                        "x-session-id": sid,
                        "User-Agent": "Mozilla/5.0"
                    }
                )
                with urllib.request.urlopen(req, timeout=15) as r:
                    d = json.loads(r.read())
                    body = (d.get("body") or "") + " " + (d.get("body_html") or "")
                    mm = re.search(r"oobCode=([A-Za-z0-9_-]+)", body)
                    if mm:
                        return mm.group(1)
        except Exception:
            pass
        time.sleep(gap)
    return None


def add_to_9router(email, password, workspace_slug):
    try:
        conn = sqlite3.connect(DB_PATH)
        cur = conn.cursor()
        cur.execute("SELECT id FROM providerNodes WHERE name LIKE '%Sparkian%' LIMIT 1;")
        row = cur.fetchone()
        if not row:
            conn.close()
            return False
        node_id = row[0]
        now_iso = time.strftime("%Y-%m-%dT%H:%M:%S.000Z", time.gmtime())
        conn_id = str(uuid.uuid4())
        conn_data = {
            "apiKey": f"{email}:{password}",
            "defaultModel": "sparkian/claude-haiku-5.5",
            "testStatus": "active",
            "errorCode": None,
            "backoffLevel": 0,
            "providerSpecificData": {
                "prefix": "sparkian",
                "apiType": "chat",
                "baseUrl": "http://127.0.0.1:20128/v1",
                "nodeName": "Sparkian",
                "connectionProxyEnabled": False,
                "connectionProxyUrl": "",
                "connectionNoProxy": ""
            }
        }
        cur.execute(
            """INSERT INTO providerConnections
               (id, provider, authType, name, email, priority, isActive, data, createdAt, updatedAt)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?);""",
            (conn_id, node_id, "apikey", workspace_slug or email.split("@")[0], email, 1, 1,
             json.dumps(conn_data), now_iso, now_iso)
        )
        conn.commit()
        conn.close()
        return True
    except Exception as e:
        print(f"Failed to add to 9router: {e}")
        return False


async def register_account(idx, tm):
    print(f"\n{'='*60}\n[{idx}] Memulai registrasi akun Sparkian via Camoufox...")
    domain = random.choice(DOMAINS)
    local = "sp" + "".join(random.choices(string.ascii_lowercase + string.digits, k=9))
    email = tm.create_inbox(local_part=local, domain=domain)
    password = gen_password()
    name = f"{random.choice(FIRST)} {random.choice(LAST)}"
    print(f"  Email    : {email}")
    print(f"  Name     : {name}")

    async with AsyncCamoufox(headless=True) as browser:
        page = await browser.new_page()
        try:
            await page.goto("https://sparkian.com/signup", timeout=40000)
            await asyncio.sleep(2)

            # Fill inputs
            await page.fill('#signup-name', name)
            await page.fill('#signup-email', email)
            await page.fill('#signup-password', password)
            await page.fill('#signup-confirm-password', password)

            # Submit form to trigger Turnstile mounting
            await page.click('button[type="submit"]')
            await asyncio.sleep(1.5)

            # Unhide Turnstile widget container in DOM (poll for mount)
            rect = None
            for _ in range(12):
                rect = await page.evaluate("""() => {
                    const input = document.querySelector('input[name="cf-turnstile-response"]');
                    if (!input) return null;
                    let cur = input;
                    while (cur && cur.parentElement) {
                        if (cur.parentElement.hasAttribute('hidden')) {
                            cur.parentElement.removeAttribute('hidden');
                            cur.parentElement.style.display = 'block';
                            cur.parentElement.style.minHeight = '75px';
                        }
                        cur = cur.parentElement;
                    }
                    return input.parentElement ? input.parentElement.getBoundingClientRect() : null;
                }""")
                if rect and rect.get("height", 0) >= 30:
                    break
                await asyncio.sleep(0.5)

            if rect and rect.get("height", 0) >= 30:
                cx = rect["x"] + 35
                cy = rect["y"] + 35
                print(f"  Solving Turnstile via coordinate click at ({cx:.1f}, {cy:.1f})...")
                await page.mouse.move(cx, cy)
                await asyncio.sleep(0.2)
                await page.mouse.click(cx, cy)
            else:
                print("  [!] Turnstile input element not found in DOM")

            # Wait for redirect to /verify-email
            redirected = False
            for _ in range(12):
                await asyncio.sleep(1)
                if "verify-email" in page.url or "verify" in page.url:
                    redirected = True
                    break

            if not redirected:
                print(f"  [!] Gagal redirect, URL saat ini: {page.url}")
                return None

            print("  [✓] Sukses redirect ke /verify-email")

        except Exception as e:
            print(f"  [!] Error pada browser automation: {e}")
            return None

    # Step 2: Fetch verification email
    print("  Menunggu email verifikasi masuk...")
    oob = extract_oob_code(tm, email)
    key = get_fb_key()
    opener = get_opener()

    if not oob:
        print("  Email belum muncul, meminta pengiriman ulang via Firebase...")
        try:
            b = json.dumps({"returnSecureToken": True, "email": email, "password": password, "clientType": "CLIENT_TYPE_WEB"}).encode()
            r = urllib.request.Request(f"https://identitytoolkit.googleapis.com/v1/accounts:signInWithPassword?key={key}", data=b, headers={"Content-Type": "application/json", "Origin": "https://sparkian.com"})
            with opener.open(r, timeout=15) as resp:
                tok = json.loads(resp.read().decode())["idToken"]
            r2 = urllib.request.Request(f"https://identitytoolkit.googleapis.com/v1/accounts:sendOobCode?key={key}", data=json.dumps({"requestType": "VERIFY_EMAIL", "idToken": tok}).encode(), headers={"Content-Type": "application/json", "Origin": "https://sparkian.com"})
            opener.open(r2, timeout=15)
            time.sleep(4)
            oob = extract_oob_code(tm, email)
        except Exception as e:
            print(f"  [!] Error saat resend email: {e}")

    if not oob:
        print("  [!] oobCode tidak ditemukan dari inbox.")
        return None

    print(f"  [✓] oobCode ditemukan: {oob[:20]}...")

    # Step 3: Confirm email via oobCode
    try:
        v_req = urllib.request.Request(
            f"https://identitytoolkit.googleapis.com/v1/accounts:update?key={key}",
            data=json.dumps({"oobCode": oob}).encode(),
            headers={"Content-Type": "application/json", "Origin": "https://sparkian.com"}
        )
        with opener.open(v_req, timeout=15) as v_resp:
            pass
        print("  [✓] Email terkonfirmasi")

        # Step 4: Re-signin to get fresh idToken
        s_req = urllib.request.Request(
            f"https://identitytoolkit.googleapis.com/v1/accounts:signInWithPassword?key={key}",
            data=json.dumps({"returnSecureToken": True, "email": email, "password": password, "clientType": "CLIENT_TYPE_WEB"}).encode(),
            headers={"Content-Type": "application/json", "Origin": "https://sparkian.com"}
        )
        with opener.open(s_req, timeout=15) as s_resp:
            final_tok = json.loads(s_resp.read().decode())["idToken"]

        # Step 5: Bootstrap workspace
        b_req = urllib.request.Request(
            "https://api.sparkian.com/auth/session",
            data=b"{}",
            headers={
                "Authorization": f"Bearer {final_tok}",
                "Content-Type": "application/json",
                "Origin": "https://sparkian.com",
                "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"
            },
            method="POST"
        )
        ws_slug = email.split("@")[0]
        with opener.open(b_req, timeout=15) as b_resp:
            ctx = json.loads(b_resp.read().decode())
            ws_slug = ((ctx.get("context") or {}).get("workspace") or {}).get("slug", ws_slug)
        print(f"  [✓] Workspace aktif: {ws_slug}")

        rec = {
            "idx": idx,
            "status": "active",
            "email": email,
            "password": password,
            "name": name,
            "workspace_slug": ws_slug,
            "created_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
        }

        with open(OUT_FILE, "a") as f:
            f.write(json.dumps(rec) + "\n")

        # Auto-integrate into 9router
        added = add_to_9router(email, password, ws_slug)
        if added:
            print(f"  [✓] Terhubung langsung ke 9router connection pool!")

        return rec

    except Exception as e:
        print(f"  [!] Gagal menyelesaikan aktivasi: {e}")
        return None


async def main():
    parser = argparse.ArgumentParser(description="Sparkian Camoufox Turnstile Farm")
    parser.add_argument("-n", "--count", type=int, default=1, help="Jumlah akun yang akan dibuat")
    args = parser.parse_args()

    tm = TempikClient()
    success = 0
    for i in range(1, args.count + 1):
        res = await register_account(i, tm)
        if res:
            success += 1
        if i < args.count:
            await asyncio.sleep(3)

    print(f"\n{'='*60}")
    print(f"FARM SELESAI: {success}/{args.count} akun berhasil dibuat dan terhubung ke 9router.")
    print(f"Daftar akun tersimpan di: {OUT_FILE}")
    print(f"{'='*60}")


if __name__ == "__main__":
    asyncio.run(main())
