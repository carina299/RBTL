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
| `backend/` relay | User's VPS (needs an HTTPS domain) | Persists messages + fans out via SSE + auth | Almost never — just fill in env vars |
| `web/` PWA | Same VPS (static files) | The phone-side chat shell, added to the home screen | Edit 4 lines at the top of `CONFIG` |
| **AI side** | User's computer / server | The actual "AI brain" — receives messages → generates → replies | **Depends on which model the user uses, see §2** |

**One shared key**: `RELAY_SECRET` guards the backend, the frontend login, and the AI
side all at once. All three **must match exactly**.

**Key fact**: the frontend and backend **don't care at all who the AI is** — they only
know the relay's HTTP/SSE endpoints. So "switching to a different model" only means
swapping out the AI-side layer; the frontend and backend stay untouched.

---

## 1. Deployment order (must be sequential — verify each step before moving to the next)

### ① Backend relay — get this running first; everything else is its client
Follow [`backend/DEPLOY.md`](backend/DEPLOY.md). Key points:
- Requires a **Linux VPS + a domain already configured for HTTPS** (the PWA/service
  worker/push all require https — run `certbot` first if you don't have a cert).
- `cp .env.example relay.env`, generate a `RELAY_SECRET`, fill in
  `RELAY_ALLOW_ORIGINS` (the user's real https origin).
- Start `companion-relay` under systemd; nginx needs two locations (`/relay/` reverse
  proxy + `/chat/` static).
- **Verify**: `curl https://<domain>/relay/healthz` must return `{"ok":true,...}`.

### ② Frontend PWA
Follow [`web/DEPLOY.md`](web/DEPLOY.md). `rsync web/` to nginx's static directory,
edit the `CONFIG` block at the top of `index.html`
(`APP_NAME`/`AI_NAME`/`HUMAN_NAME`/`SINCE`).
- **Verify**: open `https://<domain>/chat/` on a phone, enter `RELAY_SECRET` in the
  login box, and you should reach the chat page.
- To preview the UI with zero backend: set `USE_MOCK=true` in `index.html` — it ships
  with a fake conversation (remember to set it back to `false` afterward).

### ③ AI side — see the decision tree below; this is where most people get stuck

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

---

## 6. Self-check before handing off to the user

- [ ] `curl https://<domain>/relay/healthz` → `{"ok":true}`
- [ ] On a phone, `https://<domain>/chat/` logs in and shows the chat page
- [ ] Sending a message from the PWA → the AI-side process receives it (check bridge /
      CC logs)
- [ ] The AI replies → a bubble appears on the phone within a few seconds
- [ ] Sending an image → the AI side can retrieve it (multimodal models can see it)
- [ ] Backgrounding the PWA on the phone → having the AI send another reply → a
      lock-screen push notification arrives (if VAPID is configured)
- [ ] **Security**: `relay.env`/`*.pem`/`relay.db`/any API keys are not committed to
      git; `RELAY_SECRET` is freshly generated and not reused from anywhere else

> Security baseline: if `RELAY_SECRET` leaks, anyone can read the entire
> conversation and impersonate either party. This is a single-user model — one key
> represents "just you and your AI." See the "Security" section in each `DEPLOY.md`
> for details.