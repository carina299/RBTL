# RBTLove (Work in Progress)

> A private 1:1 chat channel + long-term memory for an AI companion + an album/memories feature
> Derived from [Tidal_Echo](https://github.com/anhe2021212-spec/Tidal_Echo) (AGPLv3)

---

## What this is

A private 1:1 AI companion chat system: a phone PWA talks to an AI running locally
(Claude Code + a channel plugin). Building on top of the original project, we've
added:

1. **Long-term memory** — a LangGraph-based Agentic RAG pipeline: a Celery
   background job periodically distills recent dialogue into episodic/semantic
   memories, and the agent can also call `memory_search` / `memory_remember`
   directly (via `backend/mcp_server.py`, registered as its own MCP server) to
   look up or save something mid-conversation instead of waiting for the next
   batch. LLM extraction and embeddings are both pluggable — see
   [`backend/DEPLOY.md`](backend/DEPLOY.md#5-long-term-memory) for the provider
   options (Anthropic/OpenAI/Gemini/any OpenAI-compatible API for the LLM;
   OpenAI/Gemini/a local model/a deterministic dev stand-in for embeddings).
2. **Album, end to end** — photo upload with captions and thumbnails, timeline
   browsing, object storage on local disk or MinIO/S3 (the original project
   left this as an empty UI shell; see
   [`backend/DEPLOY.md`](backend/DEPLOY.md#6-album-object-storage)).

## Contributors

- [carina299] — backend
- [czhang11zhangyingmeng] — frontend

## Running it

> Don't feel like doing this by hand? Hand [`AGENTS.md`](AGENTS.md) to an AI
> coding assistant and ask it to deploy this for you — it's
> written as a step-by-step SOP for exactly that.

### 1. Backend + PWA

```bash
python3 -c "import secrets; print(secrets.token_urlsafe(32))" # generate RELAY_SECRET
cp .env.example .env   # fill in RELAY_SECRET and RELAY_HUMAN_NAME
docker compose up -d --build
```

Starts Postgres + Redis + MinIO + the backend (`:3011`) + the Celery worker +
the PWA (`:8080`) together. Once it's up, open **http://127.0.0.1:8080/** (or
`http://localhost:8080/`) in your browser — that's the chat UI — and paste
the `RELAY_SECRET` you generated above into the login box. On your phone,
open the same URL (swap `127.0.0.1` for your computer's LAN IP, or your
domain if you're on a VPS) and use the browser's "Add to Home Screen" so it
runs as a standalone app. See [`DEPLOY.md`](DEPLOY.md) for what each
container does, plus the VPS and Vercel/Render deployment modes.

### 2. AI side (channel plugin)

This always runs on **your own computer**, no matter where the backend from
step 1 is deployed — it's the local bridge between Claude Code and the relay:

```bash
# ~/.claude/channels/companion/.env
RELAY_SECRET=<the same secret as .env above>
RELAY_URL=http://127.0.0.1:3011   # or your deployed relay's URL
RELAY_AI_NAME=your AI's name
RELAY_HUMAN_NAME=your name
```

Register `channel/` as an MCP server in `.mcp.json`, then start Claude Code
with `claude --dangerously-load-development-channels server:companion`. Full
steps (including why that flag is needed and how to verify the connection)
in [`channel/DEPLOY.md`](channel/DEPLOY.md).

## License

This project is a derivative work of [Tidal_Echo](https://github.com/anhe2021212-spec/Tidal_Echo),
licensed under its current **GNU AGPLv3** license (see [`LICENSE`](LICENSE) and [`NOTICE`](NOTICE)).