# Sparkian Automation Suite

Complete automation toolkit for **Sparkian AI** (`sparkian.com`):
- **Camoufox Turnstile Solver**: Bypasses Cloudflare Turnstile on signup via coordinate probing and anti-detect browser lifecycle.
- **Auto-Verification & Bootstrap**: Automated Tempik disposable inbox creation, email activation via Firebase Identity Toolkit, and default workspace bootstrap.
- **OpenAI-Compatible Adapter for 9router**: Serves Sparkian's native streaming chat API as standard OpenAI `/v1/chat/completions` (JSON & SSE streaming).
- **Auto-Pool Injection**: Automatically injects newly registered accounts into 9router SQLite connection database.

---

## Architecture Overview

```
                                      ┌──────────────────────┐
                                      │   Tempik Mail API    │
                                      │ (mail.xentranetwork) │
                                      └──────────┬───────────┘
                                                 │ 1. Create inbox
                                                 ▼
┌─────────────────────────┐          ┌──────────────────────┐
│  sparkian_camoufox_farm │ ────────►│  Camoufox Engine     │
│  (Batch Generator)      │          │  (Turnstile Solver)  │
└──────────┬──────────────┘          └──────────┬───────────┘
           │                                    │ 2. Bypass Turnstile
           │ 3. Fetch oobCode & verify          ▼
           ▼                         ┌──────────────────────┐
┌─────────────────────────┐          │  Firebase & Sparkian │
│ 9router Connection Pool │          │  Backend API         │
│ (data.sqlite)           │          └──────────────────────┘
└──────────▲──────────────┘
           │
           │ 4. Route requests
┌──────────┴──────────────┐
│   sparkian_adapter      │ ◄─────── 9router Client Requests
│   (:20128/v1)           │
└─────────────────────────┘
```

---

## Quick Start & Installation

Run the automated installer script:

```bash
chmod +x install.sh
./install.sh
```

Or install manually:

```bash
pip install -r requirements.txt
python3 -m camoufox fetch
```

---

## Usage

### 1. Generate Accounts

Generate accounts in batch with automatic Turnstile solving and 9router injection:

```bash
# Generate 1 account
python3 sparkian_camoufox_farm.py -n 1

# Generate 5 accounts in batch
python3 sparkian_camoufox_farm.py -n 5
```

Accounts are saved to `camoufox_accounts.jsonl` and instantly linked to 9router.

### 2. Run OpenAI-Compatible Adapter

Start the local adapter bridging 9router to Sparkian:

```bash
python3 sparkian_adapter.py --port 20128
```

Or run as a systemd service:

```bash
systemctl start sparkian-adapter
systemctl status sparkian-adapter
```

### 3. Test with 9router

Query model via 9router (`http://127.0.0.1:20127/v1/chat/completions`):

```bash
curl -X POST http://127.0.0.1:20127/v1/chat/completions \
  -H "Content-Type: application/json" \
  -H "Authorization: Bearer <YOUR_9ROUTER_KEY>" \
  -d '{
    "model": "sparkian/claude-haiku-5.5",
    "messages": [{"role": "user", "content": "Hello World!"}],
    "stream": false
  }'
```

Supported models:
- `sparkian/claude-haiku-5.5` *(Anthropic Claude Haiku 5.5)*
- `sparkian/gemini-3.8-flash`
- `sparkian/deepseek-v4-flash`
- `sparkian/qwen3.8-flash`
- `sparkian/gemini-3.1-flash-image`

---

## License

MIT License.
