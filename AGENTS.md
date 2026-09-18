# AGENTS.md — For an AI assistant tasked with deploying this repo

> **This document is written for an AI.** Someone has handed you this repository and
> asked you to help them get this "private AI companion chat channel" running on their
> own server.
> This file is a **deployment SOP + decision tree + pitfall list**. Read it fully
> before doing anything. After reading it you should be able to complete the
> deployment independently, and diagnose issues yourself when you get stuck.
>
> Ready-to-copy code lives in [`examples/`](examples/): a bridge for any LLM, a
> Windows auto-confirm script, and env templates.

---

## 0. What you're installing, in 30 seconds

Three pieces, tied together by one shared key:

| Component | Runs where | Does what | Need to modify it? |
|---|---|---|---|
| `backend/` relay | **Pick one of 3 modes first — see §1①** (local Docker / VPS / Render) | Persists messages + fans out via SSE + auth; also long-term memory + album, both optional | Almost never — just fill in env vars |
| `web/` PWA | Same place as the backend (static files), or Vercel in the split-origin mode | The phone-side chat shell, added to the home screen | Edit 4 lines at the top of `CONFIG` |
| **AI side** | User's computer / server | The actual "AI brain" — receives messages → generates → replies | **Depends on which model the user uses, see §2** |

**One shared key**: `RELAY_SECRET` guards the backend, the frontend login, and the AI
side all at once. All three **must match exactly**.

**Key fact**: the frontend and backend **don't care at all who the AI is** — they only
know the relay's HTTP/SSE endpoints. So "switching to a different model" only means
swapping out the AI-side layer; the frontend and backend stay untouched.

**Optional subsystems, both off/local by default — don't set them up unless the
user actually asks for them**: long-term memory (a Celery background job +
pluggable LLM/embedding providers) and the album feature (photo storage,
local disk or MinIO/S3). See §1④.

---

## 1. Deployment order (must be sequential — verify each step before moving to the next)

### ① Backend relay — get this running first; everything else is its client

**First, confirm which of the 3 deployment modes the user wants** — see the
table at the top of [`DEPLOY.md`](DEPLOY.md) (the root one, not
`backend/DEPLOY.md`). Picking the wrong one wastes the whole setup, same as
§2's model decision:

| Mode | When to pick it |
|---|---|
| **Local** (`docker compose`, no domain) | Trying it out, or the user and their phone are on the same LAN. |
| **VPS** (`docker compose` + nginx/TLS, their own domain) | A real deployment they fully control — this is the classic "rent a VPS" path. |
| **Website** (Render backend + Vercel frontend) | They don't want to manage a server at all; free tiers exist for both. |

`docker compose up -d --build` (from the repo root, after `cp .env.example
.env` and filling in `RELAY_SECRET`) is the fastest path for **Local** and
**VPS** alike — it starts Postgres + Redis + MinIO + the backend + a Celery
worker + the PWA together; see root `DEPLOY.md` for the VPS-specific nginx
step and the Render/Vercel steps.

Only fall back to the bare systemd + venv path in `backend/DEPLOY.md` if the
user specifically doesn't want Docker (e.g. no Docker on their VPS, or a
policy against it) — that doc's §2 covers it, and you'll need to run Redis
yourself too if you also set up long-term memory (§1④).

Either way:
- **VPS/Docker mode needs a domain already configured for HTTPS** (the
  PWA/service worker/push all require https — run `certbot` first if the
  user doesn't have a cert yet). Local mode doesn't need this at all.
- Generate a fresh `RELAY_SECRET`, fill in `RELAY_ALLOW_ORIGINS` (the real
  https origin, or `http://localhost:8080` for Local mode).
- **Verify**: `curl <backend-url>/healthz` must return `{"ok":true,...}`
  (`<backend-url>` is `http://localhost:3011` for Local,
  `https://<domain>/relay` for VPS, or the Render URL for Website).

### ② Frontend PWA
Follow [`web/DEPLOY.md`](web/DEPLOY.md). Edit the `CONFIG` block at the top of
`index.html` (`APP_NAME`/`AI_NAME`/`HUMAN_NAME`/`SINCE`) regardless of mode;
the rest depends on which of §1①'s 3 modes you picked:
- **Local**: nothing to deploy — docker-compose's `web` container already
  serves `web/` at `:8080`.
- **VPS**: `rsync web/` to nginx's static directory (see root `DEPLOY.md` §2).
- **Website**: Vercel project with Root Directory = `web`, plus the one-line
  `RELAY_URL` edit for split-origin (see root `DEPLOY.md` §3.2).
- **Verify**: open the PWA URL on a phone (`http://<LAN-IP>:8080` for Local,
  `https://<domain>/chat/` for VPS, the Vercel URL for Website), enter
  `RELAY_SECRET` in the login box, and you should reach the chat page.
- To preview the UI with zero backend: set `USE_MOCK=true` in `index.html` — it ships
  with a fake conversation (remember to set it back to `false` afterward).

### ③ AI side — see the decision tree below; this is where most people get stuck

### ④ Optional: long-term memory + album

**Skip this entirely unless the user specifically asks for it** — both are
off/local by default and the core chat works fine without them.

- **Long-term memory**: `MEMORY_EXTRACTION_ENABLED=false` by default. Turning
  it on needs Redis (docker-compose already runs it) plus one working LLM
  provider (`LLM_PROVIDER=anthropic|openai|gemini|openai_compatible`) and one
  embedding provider (`EMBEDDING_PROVIDER=openai|gemini|local|local_hash`) —
  each needs its own API key/base URL. Full table of env vars in
  [`backend/DEPLOY.md`](backend/DEPLOY.md) §5. Two things that bite people:
  - **`EMBEDDING_PROVIDER=local` needs `pip install sentence-transformers`**
    (not installed by default — it pulls in torch) — if the user just wants
    something that works with zero extra installs/keys, use `local_hash`
    instead (deterministic, but not real semantic search).
  - **Whatever embedding provider you pick must output `EMBEDDING_DIM`
    (1536) vectors** — the Postgres column is a fixed size. `openai`'s
    default model and `gemini`'s (requested at 1536) both already match; a
    local model usually won't (e.g. `bge-small-en-v1.5` is 384-dim) and will
    fail on insert, not at startup, so test it with a real message before
    declaring victory.
  - Agent-callable memory tools (`memory_search`/`memory_remember`) are a
    **separate** MCP server (`backend/mcp_server.py`), not the same thing as
    the background job — see §5.4 there, and copy
    [`.mcp.json.example`](.mcp.json.example) to `.mcp.json` to register both
    it and the `companion` channel plugin in one step.
- **Album**: `STORAGE_BACKEND=local` by default (writes into the backend's
  own upload dir — nothing else to configure). `STORAGE_BACKEND=minio` needs
  a reachable MinIO/S3 bucket — docker-compose runs one itself, but on a VPS
  or split-origin deploy double-check `MINIO_PUBLIC_ENDPOINT` (what the
  **browser/phone** can reach, not just the backend container) or presigned
  image URLs will 404 for the user even though everything looks fine
  server-side. Details in `backend/DEPLOY.md` §6.
- **Website mode caveat**: `render.yaml` only provisions the web service +
  Postgres — no Redis, no Celery worker, no MinIO. If the user wants memory
  extraction or MinIO albums on Render, they need to add those services
  themselves; otherwise just leave `MEMORY_EXTRACTION_ENABLED=false` and
  `STORAGE_BACKEND=local` and move on.

---

## 2. Decision tree: what is the user's "AI brain"?

> ⚠️ **Confirm which model/tool the user is using first**, then pick a path. Picking
> the wrong one wastes the whole setup.

| User's brain | Which path | How |
|---|---|---|
| **Claude Code** (CC, a local agent) | Use the repo's built-in `channel/` plugin | Follow [`channel/DEPLOY.md`](channel/DEPLOY.md): place the files, write `~/.claude/channels/companion/.env`, register `.mcp.json`, and start with `--dangerously-load-development-channels server:companion`. **A confirmation dialog will pop up — see §4.** |
| **GPT / DeepSeek / Gemini / GLM / Kimi / Qwen / local vLLM / any OpenAI-compatible API** | **Don't touch `channel/`** — run a bridge instead | Use [`examples/bridge_any_llm.py`](examples/bridge_any_llm.py), see §3. |
| Some other custom agent / framework (LangChain, a homegrown loop…) | Write a thin layer yourself | Follow the protocol in §3 and swap in your own logic for "call the LLM". |

> **Why is the `channel/` plugin Claude-Code-only?** It relies on CC's **proprietary**
> channel mechanism (using `experimental:{'claude/channel':{}}` plus
> `--dangerously-load-development-channels` to actively push external messages into a
> session). GPT/Gemini/Codex etc. have no equivalent concept, so it can't be forced to
> work there. But they can all use the §3 bridge, because the relay's protocol is
> neutral HTTP/SSE.

---

## 3. Wiring up any LLM API (the most common need — covered in detail)

### 3.1 How it works: a thin, three-step loop

The AI side is essentially a **"chat loop with tools"** (not a freely-roaming
autonomous agent — it only acts once per incoming human message):

```
① Long-lived SSE  GET  {RELAY}/channel/in?since={cursor}   ← receive human messages (real-time)
② Assemble context + call your model (OpenAI chat/completions format)
③ POST             {RELAY}/channel/out  {"type":"reply","text":"..."}   → reply back to the phone
```

- **①** uses the **same SSE endpoint** the channel plugin uses — you're just feeding
  the messages to your own model instead of Claude Code.
- **② Context**: don't let the model forget everything. On every incoming message,
  `GET {RELAY}/app/history?limit=N` to fetch the most recent N messages, convert them
  into `messages` (human → `user`, ai → `assistant`), and prepend a `system` message
  (persona/character).
- **③** outbound requests need `Authorization: Bearer {RELAY_SECRET}`.

> Just use [`examples/bridge_any_llm.py`](examples/bridge_any_llm.py) — it implements
> all three steps (stdlib only, zero pip dependencies). Fill in the env vars and run
> `python bridge_any_llm.py`.

### 3.2 Provider settings (all use the OpenAI-compatible format)

`bridge_any_llm.py` only reads three values: `LLM_API_BASE` / `LLM_API_KEY` /
`LLM_MODEL`:

| Provider | `LLM_API_BASE` | Example `LLM_MODEL` |
|---|---|---|
| OpenAI | `https://api.openai.com/v1` | `gpt-4o` |
| DeepSeek | `https://api.deepseek.com/v1` | `deepseek-chat` |
| Qwen (DashScope) | `https://dashscope.aliyuncs.com/compatible-mode/v1` | `qwen-plus` |
| Zhipu GLM | `https://open.bigmodel.cn/api/paas/v4` | `glm-4.6` |
| Moonshot Kimi | `https://api.moonshot.cn/v1` | `kimi-k2` |
| **Gemini** | `https://generativelanguage.googleapis.com/v1beta/openai` | `gemini-2.5-pro` |
| Local vLLM / Ollama | `http://127.0.0.1:8000/v1` | your local model name |

> Gemini works directly via its **OpenAI-compatible endpoint** (above) with no code
> changes. The same applies to any other "OpenAI-compatible" proxy or self-hosted
> endpoint.

### 3.3 Advanced (optional — the bridge leaves extension points for these)

- **Multi-model fallback**: configure a chain of endpoints and fail over in order on
  error codes `{401,403,404,429,500,502,503,504}` — if one goes down, it automatically
  tries the next. (This comes from real-world experience: relay/proxy providers
  frequently have flaky single points of failure.)
- **Tool calling**: if the model supports function calling, feed it the MCP/tool
  `tools` definitions; when the model returns `tool_calls`, execute them and feed the
  results back, capping the loop at 8 steps to prevent infinite recursion.
- **Images/attachments**: images sent by the human are in `attachments[].url` — first
  `GET {RELAY}/uploads/{name}?token={SECRET}` to download it, then feed it in using
  your model's multimodal format (base64/URL); for non-multimodal models, fall back to
  a text placeholder.

### 3.4 ⚠️ Single-body principle (the easiest pitfall to overlook)

The relay is **single-user, single-channel**. **Only one AI side may be connected to**
`/channel/in` **at a time**. If both the Claude Code channel and the bridge are
running simultaneously, **both will receive the same message and both will reply →
the user sees duplicate replies**. When switching brains, stop the old process first,
then start the new one.

---

## 4. Deep dive: Claude Code's DevChannelsDialog confirmation popup (must-read for unattended setups)

> Only relevant if the user is on the "Claude Code + `channel/` plugin" path. The API
> bridge path **does not** have this problem.

**Symptom**: when CC starts with
`--dangerously-load-development-channels server:companion`, **an interactive dialog
appears every time**:
```
WARNING: Loading development channels
  1. I am using this for local development   ← highlighted by default, Enter confirms
  2. Exit
```
**Why it can't be avoided**: a self-hosted local `server:` channel can't get onto the
channel allowlist (that requires a Team/Enterprise plan), and this dialog **ignores**
`--dangerously-skip-permissions` — there's no env var or setting that silences it. In
unattended scenarios (boot-time autostart, auto-restart), it will **hang indefinitely
at this step**: the channel never connects and the frontend never receives messages.

**Fix: after starting CC, automatically press Enter for it.** Pick one based on your
deployment environment:

- **Linux / macOS + tmux (recommended, cleanest)**: run CC inside tmux, then send it a
  return keypress after it starts:
  ```bash
  tmux new-session -d -s cc 'claude --dangerously-load-development-channels server:companion'
  sleep 3
  tmux send-keys -t cc Enter        # confirms the DevChannelsDialog for you
  ```
  (For safety, you can send `Enter` a few more times every 2 seconds during the first
  20 seconds; extra blank Enters landing in the input box are harmless.)

- **Windows (no tmux)**: use `AttachConsole(pid) + WriteConsoleInput` to inject a
  return keypress into CC's child console. You can use
  [`examples/confirm_dev_channel_win.py`](examples/confirm_dev_channel_win.py)
  directly (a minimal standalone implementation based on a proven real-world
  approach: after starting CC, it spawns a background thread that sends a return
  keypress every 2 seconds for 2–20 seconds, wrapped in `try/except` so a failure
  doesn't affect CC; it doesn't cover every Windows/CC version combination — adjust
  the window/interval as needed).

- **General fallback**: wrap the startup command with `expect` / `pexpect`, and send
  `\r` when it matches `WARNING: Loading development channels`.

**Verification**: seeing `[companion:boot] connected ...` in CC's stderr confirms the
channel is connected.

---

## 5. Pitfall checklist (the most common things users get stuck on — go through these one by one when troubleshooting)

| # | Symptom | Real cause / fix |
|---|---|---|
| 1 | PWA won't install / service worker won't register / no push notifications | **Not using HTTPS.** PWA install, the service worker, and Web Push all require https — neither `http://` nor `file://` will work. |
| 2 | Every request returns 401 | `RELAY_SECRET` **doesn't match across all three places** (backend `relay.env` / frontend login box / AI side). It must be the exact same key everywhere. |
| 3 | Frontend "connects but never receives real-time messages" | nginx is buffering the SSE stream. The `/relay/` block must have `proxy_buffering off; proxy_read_timeout 3600s;` (already in the template — don't remove it). **The #1 hidden pitfall.** |
| 4 | Browser console shows CORS errors | `RELAY_ALLOW_ORIGINS` doesn't contain the user's real https origin. |
| 5 | Images send but fail to load (404) | `RELAY_PUBLIC_PREFIX` doesn't match nginx's location prefix (attachment URLs are built using this prefix). The two must be identical (both default to `/relay`). |
| 6 | Image upload returns 413 | nginx's `client_max_body_size` is smaller than `RELAY_MAX_UPLOAD_BYTES`. Increase the nginx setting. |
| 7 | Frontend changes "don't take effect" — existing users stuck on the old UI | After editing `web/`, the `CACHE` version at the top of `sw.js` wasn't bumped (`companion-v1` → `v2`). The PWA precached the old shell. |
| 8 | Writing your own AI side and SSE won't connect | Browsers' `EventSource` can't set custom headers, so the relay's SSE endpoints **also accept `?token=<SECRET>`** — but when writing an SSE client server-side (a bridge), you **should use the `Authorization: Bearer` header** instead. |
| 9 | The model "forgets everything" — every message feels like the first one | Context isn't being assembled. On every turn, `GET /app/history?limit=N` to pull history and build `messages` — see §3.1. |
| 10 | The bridge stops receiving messages after a disconnect | SSE connections do drop — wrap it in an outer reconnect loop, and pass `?since={highest processed id}` so the relay resends messages missed during the disconnect (don't re-pull from 0 — that causes duplicate replies). |
| 11 | The user receives duplicate replies | Violates the **single-body principle** (§3.4): both CC and the bridge are connected at once. Stop one of them. |
| 12 | Memory extraction silently does nothing | `MEMORY_EXTRACTION_ENABLED` is still `false` (the default), or the Celery worker isn't running — check `docker compose logs celery_worker` / `celery -A celery_app worker -B` output for errors. |
| 13 | `memory_search`/`memory_remember` errors with "not set" or "needs sentence-transformers" | The relevant `LLM_PROVIDER`/`EMBEDDING_PROVIDER` key/package isn't configured for whichever process is calling it — remember `.mcp.json`'s `memory` server has its **own** env block, separate from the backend's `.env`. |
| 14 | Album photos upload fine but the timeline shows broken images | `MINIO_PUBLIC_ENDPOINT` doesn't match what the browser/phone can actually reach (presigned URLs use this, not `MINIO_ENDPOINT`) — see §1④. |

---

## 6. Self-check before handing off to the user

- [ ] `curl <backend-url>/healthz` → `{"ok":true}` (`<backend-url>`: see §1①
      for what this is per mode)
- [ ] On a phone, the PWA URL (§1②) logs in and shows the chat page
- [ ] Sending a message from the PWA → the AI-side process receives it (check bridge /
      CC logs)
- [ ] The AI replies → a bubble appears on the phone within a few seconds
- [ ] Sending an image → the AI side can retrieve it (multimodal models can see it)
- [ ] Backgrounding the PWA on the phone → having the AI send another reply → a
      lock-screen push notification arrives (if VAPID is configured)
- [ ] **Security**: `relay.env`/`.env`/`*.pem`/`relay.db`/`.mcp.json`/any API
      keys are not committed to git; `RELAY_SECRET` is freshly generated and
      not reused from anywhere else
- [ ] **If long-term memory was enabled** (§1④): send a few messages, wait for
      `MEMORY_EXTRACTION_EVERY_N` of them, and confirm `GET /memory/list`
      shows something — or call `memory_remember` directly and check it shows
      up there immediately
- [ ] **If album was set up** (§1④): upload a photo through the PWA and
      confirm it appears in the timeline with a working thumbnail (not a
      broken image — that's pitfall #14)

> Security baseline: if `RELAY_SECRET` leaks, anyone can read the entire
> conversation and impersonate either party. This is a single-user model — one key
> represents "just you and your AI." See the "Security" section in each `DEPLOY.md`
> for details.