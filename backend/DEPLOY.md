# Companion Relay · Backend Deployment Guide

A server-side component for a **private 1:1 chat channel**: it connects the “PWA on your phone” with the locally running AI companion on your computer (running as a **Claude Code *channel plugin***). Single-user, single-key, no account system, no third-party hosting—the messages only pass through **your own server**.

> This is a reusable version extracted from a private AI companion system and **thoroughly sanitized**. All names, keys, domains, and paths are parameterized through environment variables, and the code itself contains no private information. Treat it as your own foundation and feel free to modify it.

---

## 0. Architecture at a Glance

```
   Your phone                                            Your computer (local)
  ┌─────────┐                                       ┌──────────────────────┐
  │  PWA    │                                       │  Claude Code          │
  │ (web    │                                       │  + channel plugin     │
  │  app    │                                       │    = AI side          │
  │  added  │                                       └─────────┬────────────┘
  │  to     │                                                 │  persistent
  │  home)  │                                                 │  connection
  └────┬────┘                                                 │  GET  /relay/channel/in   (SSE, receive your messages)
       │ HTTPS                                                │  POST /relay/channel/out  (replies / poke)
       ▼                                                      │
  ┌────────────────────────  Your VPS (nginx, 443/TLS) ───────┼───────────────┐
  │   /chat/   → static files (PWA)                            │               │
  │   /relay/  → reverse proxy ─────────────►  127.0.0.1:3011 │               │
  │                                            (backend app.py) ◄──────────────┘
  │                                            │  SQLite persistence + SSE fan-out
  └────────────────────────────────────────────────────────────────────────┘

Data flow:
  You type in the PWA → POST /relay/app/send → persist → SSE to plugin → your AI reads it
  AI replies       → POST /relay/channel/out → persist → SSE to PWA (display directly
                                                       when foreground;
                                                       send a lock-screen push when background)
```

**Two sides, one key**: every endpoint is protected by the same Bearer key (`RELAY_SECRET`). Since the browser's native `EventSource` cannot set custom headers, SSE endpoints also accept `?token=` as a query parameter.

---

## 1. Prerequisites

* A Linux VPS (Ubuntu 22.04+, with root access)
* **A domain name pointing to the VPS, with nginx already configured for HTTPS**
  → PWA installation, Service Worker, and **Web Push** all require HTTPS. A PWA cannot be installed over `http://`.
  → If you don't have a certificate yet, use certbot first: `apt install certbot python3-certbot-nginx && certbot --nginx -d your-domain.example`
* Python 3.10+
* This backend has very lightweight dependencies: FastAPI + uvicorn (+ optional pywebpush)

---

## 2. Deployment Steps

### 2.1 Copy Files + Create a Virtual Environment

```bash
mkdir -p /root/companion-relay
cd /root/companion-relay
# Copy app.py / requirements.txt from this directory

python3 -m venv venv
./venv/bin/pip install -U pip
./venv/bin/pip install -r requirements.txt
```

### 2.2 Generate a Secret and Create relay.env

```bash
cp .env.example relay.env
chmod 600 relay.env          # readable only by root, critical

# Generate a brand-new random secret (NEVER reuse someone else's):
./venv/bin/python -c "import secrets; print(secrets.token_urlsafe(32))"
```

Put the generated secret into `RELAY_SECRET=` in `relay.env`, and fill in these fields:

| Variable              | What to enter                                                                              |
| --------------------- | ------------------------------------------------------------------------------------------ |
| `RELAY_SECRET`        | The random string generated above (**the same one must also be entered in the phone PWA**) |
| `RELAY_AI_NAME`       | The name of your AI companion (used in push notification titles and voice narration)       |
| `RELAY_HUMAN_NAME`    | Your name (the name used when the AI receives “×× started a voice call”)                   |
| `RELAY_PUBLIC_PREFIX` | The API mount prefix on nginx, default `/relay`; **if changed, it must match nginx**       |
| `RELAY_APP_PATH`      | The path used to open the PWA when a push notification is tapped, default `/chat/`         |
| `RELAY_ALLOW_ORIGINS` | Your `https://your-domain.example` (CORS allowlist)                                        |

The MiniMax / VAPID fields **can be left empty for now**. The backend will automatically degrade gracefully (no voice if TTS isn't configured, no lock-screen pushes if push isn't configured), while core chat continues to work normally. Enable them later once the core system is working (see §3 and §4).

### 2.3 Run Under systemd

```bash
cp companion-relay.service /etc/systemd/system/
systemctl daemon-reload
systemctl enable --now companion-relay
systemctl status companion-relay        # should be active (running)
journalctl -u companion-relay -n 50     # view logs
```

> After modifying `app.py`, restart with: `systemctl restart companion-relay`
> After modifying the env file, you also need to restart for the changes to take effect.

### 2.4 Connect nginx

Open `nginx-companion.conf.example`, copy the two `location` blocks into the `server { listen 443 ssl; ... }` block for your domain, then:

```bash
nginx -t && systemctl reload nginx
```

Key points (already included in the template, but worth emphasizing):

* `/relay/` is reverse-proxied to `127.0.0.1:3011`, **with a trailing slash** (strips the `/relay` prefix)
* SSE requires: `proxy_buffering off; proxy_read_timeout 3600s;`—otherwise the stream may be buffered or terminated
* `client_max_body_size 10m;`—must be ≥ `RELAY_MAX_UPLOAD_BYTES`, otherwise image uploads will return 413

### 2.5 Smoke Tests (Required)

```bash
S=your_RELAY_SECRET

# 1) Health check (no secret required)
curl -s https://your-domain.example/relay/healthz
#   expected: {"ok":true,"plugin_subs":0,"app_subs":0}

# 2) Send a message (simulate PWA → persistence)
curl -s -X POST https://your-domain.example/relay/app/send \
  -H "Authorization: Bearer $S" -H "Content-Type: application/json" \
  -d '{"text":"hello from curl"}'
#   expected: {"id":1}

# 3) Fetch history
curl -s "https://your-domain.example/relay/app/history" -H "Authorization: Bearer $S"
#   expected: {"messages":[{...,"from":"human","text":"hello from curl"...}]}

# 4) Real-time stream (keep one terminal running; send another message with send from another terminal, and this one should receive it immediately)
curl -N "https://your-domain.example/relay/app/stream?token=$S"
```

All four steps working = the backend is ready. Next, connect the frontend PWA and the local AI-side plugin.

---

## 3. MiniMax TTS (Optional — Read AI Replies Aloud)

1. Register in the MiniMax console, obtain an **API Key** and **Group ID**, and create/select a **voice_id**.
2. Add them to `relay.env`: `MINIMAX_API_KEY` / `MINIMAX_GROUP_ID` / `MINIMAX_VOICE_ZH` (voice ID).
3. `systemctl restart companion-relay`.
4. The frontend calls `POST /relay/app/tts {"text":"..."}` and receives an mp3. If TTS is not configured or fails, the frontend should gracefully degrade (no audio).

> Don't want to use MiniMax? This is an independent small function (`minimax_tts_mp3`). You can replace it with any TTS service that takes text in and outputs mp3—only one place needs to be changed.

---

## 4. Web Push / VAPID (Optional — Push AI Replies to the Phone's Lock Screen)

Unread push logic: **only when the PWA is not in the foreground** (no active SSE connection) will an AI `reply` trigger a lock-screen notification. If the PWA is open in the foreground, it won't bother you.

### 4.1 Generate Your Own VAPID Key Pair

```bash
cd /root/companion-relay
./venv/bin/vapid --gen                 # generate private_key.pem and public_key.pem
./venv/bin/vapid --applicationServerKey
#   prints one line: Application Server Key = BJ... (a long base64url string)
```

Add these to `relay.env`:

* `VAPID_PUBLIC_KEY=` ← the base64url string printed above (**this is the public key; the frontend also needs it for subscription**, and it can be public)
* `VAPID_PRIVATE_PEM=/root/companion-relay/private_key.pem` (private key—**NEVER expose it**)
* `VAPID_SUBJECT=mailto:you@your-domain.example`

`chmod 600 private_key.pem`, then `systemctl restart companion-relay`.

### 4.2 Self-Test

After allowing notifications in the PWA and completing the subscription:

```bash
curl -s -X POST https://your-domain.example/relay/app/push_test \
  -H "Authorization: Bearer $S" -H "Content-Type: application/json" -d '{}'
#   expected: {"ok":true,"sent":1,"dead":0}
```

A test notification should appear on the phone's lock screen. `sent:0` usually means the PWA subscription has not been completed yet.

---

## 5. Connecting the Local AI Side (Brief Overview)

The default AI side is Claude Code running on your computer together with a **channel plugin**. It:

* Maintains a persistent connection to `GET /relay/channel/in?token=SECRET` (SSE), receiving messages you send and feeding them into Claude;
* When Claude wants to reply, the plugin calls `POST /relay/channel/out`:

  * Normal reply: `{"type":"reply","text":"..."}`
  * Poke: `{"type":"react","id":<target message id>,"emoji":"❤️"}` (empty emoji = retract this poke)

If you don't use Claude Code, skip `channel/` and directly run `examples/bridge_any_llm.py` to connect to any OpenAI-compatible API. If you want a persistent API-based agent running on the VPS, use `examples/api_loop.py` and switch to `loop` through `/app/brain`. See `AGENTS.md` in the repository root and `examples/README.md` for the complete decision tree.

---

## 6. Things Intentionally Removed from This Version

| Feature                                 | Why it was removed                                                | How to add it back                                     |
| --------------------------------------- | ----------------------------------------------------------------- | ------------------------------------------------------ |
| Private context switching control       | Depends on the original system's local daemon                     | It's a generic command queue; build your own as needed |
| Previous-day timeline summary injection | Depends on a private memory database + custom small-model routing | Connect your own LLM router                            |
| Hug event                               | Depends on ESP32 hardware                                         | Add another endpoint when you have the hardware        |
| Sensor / `sense` reporting              | Feeds into a private scheduling heartbeat                         | Same as above                                          |
| Memory editor cookie authentication     | Mounted on another independent backend                            | Usually unnecessary                                    |

These are all **additive features**. Removing them does not affect core chat. When needed, add them back following the endpoint style described in §5.

---

## 7. Security Notes (Must Read)

* **`RELAY_SECRET` is the only gate.** If it leaks, anyone can read your entire conversation history and impersonate either side. `chmod 600 relay.env`, don't commit it to git, and don't print it in externally visible logs.
* **Each person must use their own fresh secret/VAPID/MiniMax key.** Never reuse them between friends—reusing keys means they can access each other's channels.
* **HTTPS is not optional:** Service Workers and Web Push simply do not work over non-HTTPS connections.
* This is a **single-user** model: one key represents “you and your AI.” It does not support multi-tenancy and should not be exposed to untrusted people.
* `relay.db`, `uploads/`, `*.pem`, and `relay.env` all contain your private data/secrets—**be careful when backing them up, and always exclude them before open-sourcing or sharing the project**.

---

## 8. API Quick Reference

| Method | Path                                  | Used by   | Purpose                                                             |
| ------ | ------------------------------------- | --------- | ------------------------------------------------------------------- |
| GET    | `/healthz`                            | —         | Health check (no authentication required)                           |
| GET    | `/channel/in`                         | AI side   | SSE: receive messages sent by the human                             |
| POST   | `/channel/out`                        | AI side   | Send replies / poke                                                 |
| POST   | `/app/send`                           | PWA       | Human sends a message (including image attachment IDs)              |
| GET    | `/app/stream`                         | PWA       | SSE: receive AI messages                                            |
| GET    | `/app/history`                        | PWA       | Fetch history (`?since=&limit=`)                                    |
| POST   | `/app/upload`                         | PWA       | Upload images/files and return attachment objects with signed paths |
| GET    | `/uploads/{name}`                     | PWA       | Retrieve an attachment (authentication required)                    |
| POST   | `/app/voice`                          | PWA       | Voice input (browser transcription or audio upload)                 |
| POST   | `/app/call`                           | PWA       | Call start/end events                                               |
| POST   | `/app/tts`                            | PWA       | Text-to-speech (MiniMax, optional)                                  |
| POST   | `/app/ping`                           | PWA       | Foreground heartbeat (online status)                                |
| GET    | `/app/status`                         | Scheduler | Online status + recent message metadata (excluding message bodies)  |
| GET    | `/app/vapid_public`                   | PWA       | Retrieve the VAPID public key for subscription                      |
| POST   | `/app/subscribe` · `/app/unsubscribe` | PWA       | Enable/disable lock-screen push subscription                        |
| POST   | `/app/push_test`                      | PWA       | Send a test notification                                            |

All endpoints (except `/healthz`) require `Authorization: Bearer <RELAY_SECRET>`; SSE endpoints can also use `?token=<RELAY_SECRET>`.
