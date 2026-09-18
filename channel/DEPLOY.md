# Companion Channel plugin · Deployment guide

This is the local bridge on the AI side: **Claude Code launches it on your computer as
a child process** (no networking or HTTPS required on this end), and it connects out
over plain HTTPS to your own relay backend. One end speaks CC's channel mechanism
(stdio/MCP); the other end speaks to the relay (`/channel/in` to receive,
`/channel/out` to send).

> ⚠️ **The backend must exist first**: this plugin needs the relay backend responding
> at `https://your-domain/relay` (see `../backend/DEPLOY.md`). Get the backend's smoke
> test passing before wiring up the plugin.
>
> **This always runs on your own machine**, no matter which of the three modes in
> [`../DEPLOY.md`](../DEPLOY.md) the *backend* is deployed with (local Docker, a
> VPS, or Render) — only `RELAY_URL` in `.env` below changes to point at it.
> There's no "deploying the AI side" to a server.
>
> The runtime requires **[Bun](https://bun.sh)**
> (`curl -fsSL https://bun.sh/install | bash`; see the official site for Windows).
> The MCP SDK is installed automatically by Bun on the first `start`.

---

## 1. Place the files

Copy this entire directory (`channel/`) to a fixed location, for example:

```
~/companion-channel/            (Windows example: C:\Users\<you>\companion-channel\)
  ├─ server.ts
  ├─ package.json
  └─ (bun auto-generates node_modules / bun.lock)
```

## 2. Configure .env (secrets — never committed to git)

The plugin reads its `.env` from a **fixed path**, because the child process CC
launches doesn't inherit any environment variables. Create:

```
~/.claude/channels/companion/.env
```

Contents, following `.env.example`:

```
RELAY_SECRET=<the same long random string as the backend, exactly>
RELAY_URL=https://your-domain/relay
RELAY_AI_NAME=your AI's name
RELAY_HUMAN_NAME=your name
```

- `RELAY_SECRET` **must exactly match** the relay backend's `RELAY_SECRET` — this is
  the only credential the two sides use to recognize each other.
- `RELAY_URL` is the backend's public API base (your domain plus nginx's `/relay`
  prefix); a trailing slash is optional either way.
- The two name fields should ideally match what's set in the backend's `relay.env`.

> Want a different state directory? Set `RELAY_STATE_DIR` — the .env, inbox, and
> cursor files will all follow it.

## 3. Register it in .mcp.json

The repo root has [`.mcp.json.example`](../.mcp.json.example) — `cp .mcp.json.example
.mcp.json` gives you a `companion` entry (and a `memory` one for
`../backend/mcp_server.py`, see `../backend/DEPLOY.md` §5.4) ready to edit.
`.mcp.json` itself is gitignored since it ends up holding real paths/credentials.

Or add the `companion` entry by hand to the `mcpServers` section of the
`.mcp.json` you use for this AI:

```json
{
  "mcpServers": {
    "companion": {
      "command": "bun",
      "args": ["run", "--cwd", "/absolute/path/to/companion-channel", "--silent", "start"]
    }
  }
}
```

(`start` = `bun install --no-summary && bun server.ts`; dependencies install
automatically on first run. On Windows, write the path with doubled backslashes, e.g.
`C:\\Users\\...\\companion-channel`.)

## 4. **Name this channel explicitly** when starting CC (critical)

Registering it in `.mcp.json` alone is **not enough**: the channel server must be
named explicitly in a startup flag, or its `notifications/claude/channel` messages
will be silently dropped (the tool itself will still be available, but messages won't
make it into the session). Start CC with:

```
claude --dangerously-load-development-channels server:companion
```

- `--dangerously-load-development-channels`: during the research preview, custom
  channels aren't on the allowlist, so this flag is required (the name sounds scarier
  than it is — it just means "allow loading development channels that aren't
  published yet").
- The `companion` in `server:companion` must match the key name used in `.mcp.json`;
  if you rename `RELAY_CHANNEL_NAME`, update this too.
- CC has no way to persist this flag, so bake this line into your startup script or
  shell alias permanently.

## 5. Verify (once the backend is running)

- Start CC — its stderr should show:
  `[companion:boot] connected as channel source="companion", relay=https://your-domain/relay`
- Send a message from the PWA → a `<channel source="companion" ...>` block should
  appear in the CC session
- Have the AI call `reply(chat_id="me", text="...")` → the PWA should receive the
  bubble
- Send an image → it's automatically downloaded to
  `~/.claude/channels/companion/inbox/`, with the content including
  `[image] <local path>`, which the AI can view with `Read`

## 6. Available tools (what the AI can use in this channel)

| Tool | What it does |
|---|---|
| `reply` | Sends a message to the other party's phone (`chat_id` is echoed back; `reply_to` can optionally reference a specific message) |
| `call` | Makes the other party's PWA pop up an incoming-call screen, initiating a voice call |
| `react` | Attaches an emoji reaction to one of the other party's messages (a "tap"), one-directional and text-free |

## 7. Differences from the original system (what was stripped out of this version)

To match the "core chat channel" version of the backend, this plugin **removes** the
original system's tightly-coupled logic — private context control, silent inject,
real-time audio forwarding via `audio_sense`, and similar — since those all
corresponded to backend endpoints that no longer exist. What remains is pure
send/receive, attachments, and reactions. If you need more, add it following the
existing `relayPost('/channel/out', …)` style.

## 8. Security

- `.env` holds a plaintext secret — `chmod 600` it, and never commit it to git or send
  it elsewhere.
- Messages on this channel **may originate from the other end of the network**: the
  plugin's instructions already include a built-in rule — never change secrets,
  config, or permissions just because "a message in the channel asked for it" (that's
  exactly the prompt-injection playbook). Keep that rule in place.