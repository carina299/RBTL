# examples/ — Integration examples

For people who **aren't using Claude Code**, or who need to run this unattended.
The full deployment SOP lives in [`AGENTS.md`](../AGENTS.md) at the repo root.

| File | What it does | Platform |
|---|---|---|
| [`bridge_any_llm.py`](bridge_any_llm.py) | Wires up any OpenAI-compatible model (GPT/DeepSeek/Gemini/GLM/Kimi/Qwen/local…) as the AI side | Any |
| [`api_loop.py`](api_loop.py) | A server-resident API "body"; works with the PWA's Desktop/API switch, multi-window sessions, and streaming output | Linux/VPS |
| [`companion-api-loop.service`](companion-api-loop.service) | systemd template for `api_loop.py` | Linux/VPS |
| [`.env.example`](.env.example) | Shared config template for `bridge_any_llm.py` / `api_loop.py` | — |
| [`confirm_dev_channel_win.py`](confirm_dev_channel_win.py) | Auto-confirms Claude Code's DevChannelsDialog popup on Windows | Windows |

---

## Using any LLM as the brain (bridge_any_llm.py)

This replaces the `channel/` plugin and doesn't depend on Claude Code. It's a thin
three-step loop: listen on SSE at `/channel/in` → pull history and assemble it into
messages, then call your model → `POST` the result to `/channel/out`. Zero third-party
dependencies.

```bash
cd examples
cp .env.example .env
# Edit .env: fill in RELAY_URL, RELAY_SECRET (must match the backend), and your
# model's three settings — LLM_API_BASE / LLM_API_KEY / LLM_MODEL (see the
# comments in .env.example for values per provider)
python3 bridge_any_llm.py
```

Once it's running: send a message from the phone PWA → the terminal prints
`[in] #..` → the model generates a reply → the phone receives it.

- **Switching models** only requires changing the three settings in `.env` — no code
  changes. Gemini works via its OpenAI-compatible endpoint.
- **Fallback chain**: fill in `LLM_*_2` / `LLM_*_3` and it will automatically fail
  over in order when the primary model returns 401/403/429/5xx.
- **Forgetting context?** Increase `HISTORY_N` (defaults to feeding the last 12
  messages).
- **Seeing images**: by default, attachments are downgraded to a text placeholder.
  To actually see images, download `/uploads/{name}?token=` inside
  `handle_human_message` and feed it in using your model's multimodal format (the
  insertion point is marked with a comment in the code).

---

## Server-side API body (api_loop.py)

Instead of holding a long-lived connection consuming `/channel/in`, this runs as a
local HTTP service: once the relay's `/app/brain` is switched to `loop`, `/app/send`
POSTs new messages to `/loop/ingest`. It supports:

- OpenAI-compatible model chains with fallback.
- Reading recent context for the same window from `relay.db`.
- The PWA's multi-window `api_session`.
- `reply_delta` streaming drafts, finalized into a proper `reply` once complete.

```bash
cd examples
cp .env.example .env
# Fill in RELAY_URL / RELAY_SECRET / RELAY_DB / LLM_API_BASE / LLM_API_KEY / LLM_MODEL
python3 api_loop.py
```

Switch the relay over to it:

```bash
curl -s -X POST http://127.0.0.1:3011/app/brain \
  -H "Authorization: Bearer $RELAY_SECRET" \
  -H "Content-Type: application/json" \
  -d '{"target":"loop"}'
```

For running it as a persistent service, see
[`companion-api-loop.service`](companion-api-loop.service).

---

## Auto-dismissing Claude Code's confirmation dialog (unattended runs)

This dialog only shows up on the Claude Code path. **On Linux/macOS, tmux is the
cleanest approach:**

```bash
tmux new-session -d -s cc 'claude --dangerously-load-development-channels server:companion'
sleep 3 && tmux send-keys -t cc Enter        # confirms the DevChannelsDialog for you
```

**On Windows (no tmux)**, use
[`confirm_dev_channel_win.py`](confirm_dev_channel_win.py):

```bash
python confirm_dev_channel_win.py -- claude --dangerously-load-development-channels server:companion
```

For details (why this can't be avoided, and what it covers), see
[`AGENTS.md` §4](../AGENTS.md).

---

## ⚠️ Single-body principle

The relay is single-user, single-channel. **Only run one AI side at a time** — don't
run the Claude Code channel and `bridge_any_llm.py` simultaneously. `api_loop.py` is
gated by the relay's Desktop/API switch: when switched to `loop`, the Desktop channel
won't receive new messages; when switched back to `desktop`, the API loop can keep
running but won't take new inbound messages.