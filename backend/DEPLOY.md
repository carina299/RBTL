# Companion Relay · Backend Deployment Guide

A server-side component for a **private 1:1 chat channel**: it connects the “PWA on your phone” with the locally running AI companion on your computer (running as a **Claude Code *channel plugin***). Single-user, single-key, no account system, no third-party hosting—the messages only pass through **your own server**.

> This is a reusable version extracted from a private AI companion system and **thoroughly sanitized**. All names, keys, domains, and paths are parameterized through environment variables, and the code itself contains no private information. Treat it as your own foundation and feel free to modify it.

> **This file documents the bare systemd + venv path.** For the shorter, Docker-based
> route (recommended — also covers local dev and the Vercel/Render option), see
> [`../DEPLOY.md`](../DEPLOY.md). Either way you'll need Postgres — see §1.5 below.

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
* Core chat has very lightweight dependencies: FastAPI + uvicorn (+ optional pywebpush).
  Long-term memory and the album feature (both optional, off/local by default —
  see §5 and §6) add LangGraph, Celery/Redis, pgvector, and Pillow/minio on top.
* **Postgres** (see §1.5) — messages are no longer stored in SQLite; only
  `push_subscriptions` still is.

### 1.5 Database (Postgres)

Easiest: `docker compose up -d postgres` from the repo root (it's in
`../docker-compose.yml`) — gives you Postgres + pgvector without installing
anything system-wide. Then in `relay.env`:

```
RELAY_DATABASE_URL=postgresql+psycopg://rbtl:<password>@localhost:5432/rbtl
```

Apply the schema (from `backend/`, with the venv from §2.1 already set up):

```bash
./venv/bin/alembic upgrade head
```

Re-run this after every `git pull` that touches `backend/alembic/versions/`.

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
| `RELAY_DATABASE_URL`  | Postgres connection string — see §1.5                                                      |
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

## 5. Long-term memory

Optional, off by default (`MEMORY_EXTRACTION_ENABLED=false`). When on, a
Celery background job periodically distills recent dialogue into long-term
memories (episodic: dated events; semantic: stable facts), deduplicated
against what's already stored. The agent can also skip the wait and call
`memory_search` / `memory_remember` directly — see §5.4.

### 5.1 Background job (Celery + Redis)

Needs Redis reachable at `REDIS_URL` (docker-compose provides one — see
`../docker-compose.yml`'s `redis` service, which also wires `REDIS_URL` for
you). Outside docker-compose, run:

```bash
./venv/bin/celery -A celery_app worker -B --loglevel=info
```

`-B` runs Celery's own beat scheduler in the same process for the daily
maintenance reweight (`MemoryRepository.reweight` — a small importance boost
for memories accessed a lot, a small decay for ones nobody's touched in a
while) — fine for a single small deployment; split it into a separate
`celery beat` process only if you outgrow that.

| Variable | What it does |
| --- | --- |
| `MEMORY_EXTRACTION_ENABLED` | Master switch. Off by default so a fresh clone with no API keys still boots cleanly. |
| `MEMORY_EXTRACTION_EVERY_N` | Every N inbound human messages, the backend enqueues one extraction run. |
| `REDIS_URL` | Celery broker/result backend. |

### 5.2 LLM provider (turns dialogue into candidate memories)

`LLM_PROVIDER=anthropic|openai|gemini|openai_compatible` — defaults to
whichever of `ANTHROPIC_API_KEY`/`OPENAI_API_KEY`/`GEMINI_API_KEY` is set
first, else `openai_compatible` if `OPENAI_COMPATIBLE_API_BASE` is set.

| Provider | Keys | Model var |
| --- | --- | --- |
| `anthropic` (default) | `ANTHROPIC_API_KEY` | `MEMORY_EXTRACTION_MODEL` (default `claude-haiku-4-5-20251001`) |
| `openai` | `OPENAI_API_KEY` | `MEMORY_EXTRACTION_OPENAI_MODEL` (default `gpt-4o-mini`) |
| `gemini` | `GEMINI_API_KEY` | `MEMORY_EXTRACTION_GEMINI_MODEL` (default `gemini-2.5-flash`) |
| `openai_compatible` | `OPENAI_COMPATIBLE_API_KEY` (many self-hosted servers ignore this) + `OPENAI_COMPATIBLE_API_BASE` | `OPENAI_COMPATIBLE_MODEL` |

`openai_compatible` is for anything speaking OpenAI's chat-completions wire
format at a different base URL — Ollama, vLLM, OpenRouter, together.ai, etc.
— with its own credentials so it never collides with a real `OPENAI_API_KEY`
you might also have set for embeddings below.

### 5.3 Embedding provider (dedup + similarity search)

`EMBEDDING_PROVIDER=openai|gemini|local|local_hash` — defaults to whichever
of `OPENAI_API_KEY`/`GEMINI_API_KEY` is set, else `local_hash`.

| Provider | What it is | Keys / config |
| --- | --- | --- |
| `openai` (default if key set) | OpenAI's embeddings API | `OPENAI_API_KEY`, `OPENAI_EMBEDDING_MODEL` (default `text-embedding-3-small`) |
| `gemini` | Google's Gemini embeddings API | `GEMINI_API_KEY`, `GEMINI_EMBEDDING_MODEL` (default `gemini-embedding-001`) |
| `local` | A real local model (BGE/Qwen/MiniLM/...) via `sentence-transformers` — no network, no API key | `LOCAL_EMBEDDING_MODEL` (default `BAAI/bge-small-en-v1.5`); needs `pip install sentence-transformers` (not in `requirements.txt` — it pulls in torch) |
| `local_hash` (default if no key set) | Deterministic, dependency-free dev/test stand-in — vectors carry **no semantic meaning** | nothing to configure |

> **Dimension must match `EMBEDDING_DIM`** (`models.py`, default 1536 — the
> pgvector column is a fixed size). `text-embedding-3-small` and
> `gemini-embedding-001` (requested at 1536) both match the default out of
> the box; a local model's native dimension usually won't (e.g. the default
> `bge-small-en-v1.5` is 384-dim) — either pick a matching model/config or
> change `EMBEDDING_DIM` and run a new migration before switching.

### 5.4 Agent-callable memory tools (MCP server)

Besides the background job, the agent can search or write memory directly
via `mcp_server.py` — a separate stdio MCP server, independent of the
`companion` channel plugin in `channel/`, exposing:

- `memory_search(query, top_k=6)` — semantic search over the user's memories
- `memory_remember(content, importance=0.5, memory_type="semantic")` — save
  something right now instead of waiting for the next background pass

Register it in `.mcp.json` alongside `companion` — the repo root's
[`.mcp.json.example`](../.mcp.json.example) already has both entries, so
`cp .mcp.json.example .mcp.json` and just fill in the real paths/password:

```json
{
  "mcpServers": {
    "companion": { "...": "..." },
    "memory": {
      "command": "/absolute/path/to/backend/venv/bin/python3",
      "args": ["/absolute/path/to/backend/mcp_server.py"],
      "env": {
        "RELAY_DATABASE_URL": "postgresql+psycopg://rbtl:<password>@localhost:5432/rbtl",
        "EMBEDDING_PROVIDER": "local_hash"
      }
    }
  }
}
```

It talks straight to Postgres (not through the relay's HTTP API), so it needs
its own `RELAY_DATABASE_URL` and whichever LLM/embedding env vars from
§5.2/§5.3 you want it to use — these can differ from the backend's own env
since it's a separate process.

### 5.5 Debug/admin endpoints

`GET /memory/retrieve?query=&top_k=` and `GET /memory/list?memory_type=&limit=`
exist for manual testing and housekeeping — the real conversation path goes
through the MCP tools in §5.4, not these.

---

## 6. Album (object storage)

Optional, defaults to `STORAGE_BACKEND=local` (writes into
`RELAY_UPLOAD_DIR`, same as chat uploads). Set `STORAGE_BACKEND=minio` to use
MinIO or a real S3/R2 bucket instead — docker-compose provides a MinIO
container itself (see `../docker-compose.yml`'s `minio` service).

| Variable | What it does |
| --- | --- |
| `STORAGE_BACKEND` | `local` or `minio` |
| `MINIO_ENDPOINT` | Host:port MinIO/S3 is reachable at *from the backend* (docker-compose sets this to the internal `minio:9000`) |
| `MINIO_PUBLIC_ENDPOINT` | Host:port a **browser/phone** can reach for presigned image URLs — only set if it differs from `MINIO_ENDPOINT` (e.g. compose's internal `minio:9000` vs. `localhost:9000` from outside the Docker network) |
| `MINIO_ACCESS_KEY` / `MINIO_SECRET_KEY` | Credentials |
| `MINIO_BUCKET` | Default `rbtl-album` |
| `MINIO_SECURE` | `true` for HTTPS to the MinIO endpoint |
| `MINIO_REGION` | Default `us-east-1` |

Uploaded photos get a JPEG thumbnail generated on upload (`storage.py`'s
`make_thumbnail`) — the timeline view fetches thumbnails, not the original
multi-MB file. See §10 for the album endpoints (`/app/album/*`).

---

## 7. Connecting the Local AI Side (Brief Overview)

The default AI side is Claude Code running on your computer together with a **channel plugin**. It:

* Maintains a persistent connection to `GET /relay/channel/in?token=SECRET` (SSE), receiving messages you send and feeding them into Claude;
* When Claude wants to reply, the plugin calls `POST /relay/channel/out`:

  * Normal reply: `{"type":"reply","text":"..."}`
  * Poke: `{"type":"react","id":<target message id>,"emoji":"❤️"}` (empty emoji = retract this poke)

If you don't use Claude Code, skip `channel/` and directly run `examples/bridge_any_llm.py` to connect to any OpenAI-compatible API. If you want a persistent API-based agent running on the VPS, use `examples/api_loop.py` and switch to `loop` through `/app/brain`. See `AGENTS.md` in the repository root and `examples/README.md` for the complete decision tree.

---

## 8. Things Intentionally Removed from This Version

| Feature                                 | Why it was removed                                                | How to add it back                                     |
| --------------------------------------- | ----------------------------------------------------------------- | ------------------------------------------------------ |
| Private context switching control       | Depends on the original system's local daemon                     | It's a generic command queue; build your own as needed |
| Previous-day timeline summary injection | Depends on a private memory database + custom small-model routing | Connect your own LLM router                            |
| Hug event                               | Depends on ESP32 hardware                                         | Add another endpoint when you have the hardware        |
| Sensor / `sense` reporting              | Feeds into a private scheduling heartbeat                         | Same as above                                          |
| Memory editor cookie authentication     | Mounted on another independent backend                            | Usually unnecessary                                    |

These are all **additive features**. Removing them does not affect core chat. When needed, add them back following the endpoint style described in §7.

---

## 9. Security Notes (Must Read)

* **`RELAY_SECRET` is the only gate.** If it leaks, anyone can read your entire conversation history and impersonate either side. `chmod 600 relay.env`, don't commit it to git, and don't print it in externally visible logs.
* **Each person must use their own fresh secret/VAPID/MiniMax key.** Never reuse them between friends—reusing keys means they can access each other's channels.
* **HTTPS is not optional:** Service Workers and Web Push simply do not work over non-HTTPS connections.
* This is a **single-user** model: one key represents “you and your AI.” It does not support multi-tenancy and should not be exposed to untrusted people.
* `relay.db`, `uploads/`, `*.pem`, and `relay.env` all contain your private data/secrets—**be careful when backing them up, and always exclude them before open-sourcing or sharing the project**.

---

## 10. API Quick Reference

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
| GET    | `/memory/retrieve`                    | debug     | Manual memory search (§5.5) — the real path is the MCP tools, §5.4  |
| GET    | `/memory/list`                        | debug     | List stored memories (`?memory_type=&limit=`)                       |
| DELETE | `/memory/{memory_id}`                 | debug     | Soft-delete one memory                                              |
| POST   | `/app/album/upload`                   | PWA       | Upload a photo + caption/time/group (`?name=&caption=&time=&group=`)|
| GET    | `/app/album/list`                     | PWA       | Timeline page of photos (`?cursor=&limit=`)                         |
| GET    | `/app/album/photo/{entry_id}`         | PWA       | One photo's detail (bumps its view count)                           |
| GET    | `/app/album/random`                   | PWA       | One random photo                                                    |
| GET    | `/app/album/file/{key}`                | PWA       | Fetch a stored photo/thumbnail by storage key                       |

All endpoints (except `/healthz`) require `Authorization: Bearer <RELAY_SECRET>`; SSE endpoints can also use `?token=<RELAY_SECRET>`.
