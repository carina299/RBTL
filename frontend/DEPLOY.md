# RBTL · Frontend Deployment Guide

The client side of the relay: a set of static pages, no build step. Drop the
folder into nginx and it works.

> Sanitized and reusable — no domains, keys, or private names are hardcoded.
> The relay address and connection key are entered in the app's own settings
> and stored per-device in `localStorage`.

---

## 0. Prerequisites

Get the **backend** running first (see [`../backend/DEPLOY.md`](../backend/DEPLOY.md));
the frontend is only its client. `curl <backend-url>/healthz` should return
`{"ok":true,...}` before you continue.

---

## 1. The pages

| File | What it is | Talks to the relay? |
|---|---|---|
| `index.html` | Intro — tap the door to come home | no |
| `desktop.html` | The hub: clock, partner card, app icons | no |
| `chat.html` | The conversation with your AI companion | **yes** |
| `memories.html` | The photo album | **yes** |
| `css/`, `js/` | Styles + scripts for `index`/`desktop` (`chat` and `memories` are self-contained) | — |

Flow: `index.html` → `desktop.html` → `chat.html` / `memories.html`, and each
of those has a back button to the desktop.

---

## 2. Deploy the files

**Local (docker compose)** — nothing to do. The `web` container already serves
this folder:

```yaml
  web:
    image: nginx:alpine
    volumes:
      - ./frontend:/usr/share/nginx/html:ro
    ports:
      - "8080:80"
```

Open `http://localhost:8080`. Static files are bind-mounted, so **edits take
effect on reload without restarting the container** (but read §7 first — the
browser may still hand you a cached copy).

**VPS** — rsync the folder to nginx's static directory:

```bash
rsync -av frontend/  root@your-VPS:/var/www/companion-web/
```

nginx mounts it at `/chat/` (see `location /chat/` in
[`../backend/nginx-companion.conf.example`](../backend/nginx-companion.conf.example)).

**Vercel** — Root Directory = `frontend`, no build command, no output
directory. See [`../DEPLOY.md`](../DEPLOY.md) §3 for the split-origin setup.

---

## 3. Connecting to the relay

Open **chat settings** (the `···` button in `chat.html`) → *Backend connection*:

- **Relay URL** — leave blank to auto-detect:
  - `localhost` / `127.0.0.1` → the relay on `:3011` (local docker)
  - anywhere else → same-origin `/relay` (nginx reverse proxy)

  Set it explicitly only for a **split-origin** deploy (this frontend on
  Vercel, the backend on Render), e.g. `https://your-backend.onrender.com`.

- **Connection key** — the backend's `RELAY_SECRET`, verbatim.

Both are shared with `memories.html` (same `localStorage` keys
`rbtl-relay-url-v1` / `rbtl-relay-secret-v1`), so you only enter them once per
device. `memories.html` has no settings panel of its own — if no key is set it
tells you to go to chat settings.

The status line above the fields reports what's actually happening: `live`,
`connecting`, `connection key rejected`, or `offline`.

> **CORS**: the relay's `RELAY_ALLOW_ORIGINS` must list the exact origin the
> page is served from (it defaults to `http://localhost:8080`). Opening the
> file over `file://` will always fail.

---

## 4. Contract with the backend

What the frontend actually calls today:

| Direction / Event | Convention |
|---|---|
| Auth | `Authorization: Bearer <RELAY_SECRET>` on every request; SSE and `<img>` use `?token=` instead (neither can set headers) |
| Message direction | `from: "human" \| "ai"` |
| History | `GET /app/history?since=&limit=` → `{messages:[{id, ts, from, kind, text, meta}]}` |
| Send | `POST /app/send {text}` → `{id}` |
| Realtime | `SSE /app/stream?token=` — AI replies, plus `{type:"typing", active}` |
| Presence | `POST /app/ping` every 60s while the page is in the foreground |
| Album list / detail | `GET /app/album/list` · `GET /app/album/photo/{id}` |
| Album upload | `POST /app/album/upload?name=&caption=&group=` (raw bytes body) |
| Album images | `GET /app/album/file/{key}?token=` |

Two things worth knowing if you modify `chat.html`:

1. **The relay is asynchronous.** `POST /app/send` returns only `{id}`; the
   AI's reply arrives later over the stream. Never treat a response body as
   the reply.
2. **Your own message is echoed back over the stream** (so other tabs/devices
   stay in sync), and that echo is broadcast *before* `/app/send` returns.
   Either path can be first, so both claim the message id through `claimId()`
   before rendering — otherwise your message appears twice.

`kind` is `user` / `reply` / `voice` / `call`, plus `thinking` and `act` for
the AI's internal chatter. `chat.html` filters the last two out; the relay
still streams them for other clients.

---

## 5. Not wired up yet

These have backend endpoints but no working frontend:

| Feature | Endpoint | Frontend state |
|---|---|---|
| Send image / file | `/app/upload` | `camera` + `gallery` buttons show a "coming soon" toast |
| Voice message | `/app/voice` | `mic` button, same |
| Voice call + TTS | `/app/call`, `/app/tts` | no UI at all |
| Lock-screen push | `/app/vapid_public`, `/app/subscribe` | see §6 — the *Message notifications* toggle only animates the contact strip, it is **not** push |
| Presence display | `/app/status` | the contact strip's mood line is static text |
| Brain / session switching | `/app/brain`, `/app/sessions` | no UI |

Also incomplete in what *is* wired: attachments on incoming messages
(`meta.attachments`) aren't rendered, AI reactions (`{type:"reaction"}`) are
ignored, and replies appear all at once rather than streaming in token by
token (`reply_delta`).

---

## 6. This is currently **not** a PWA

A PWA needs a web app manifest and a registered service worker. This frontend
has **neither**:

- there is no `manifest.webmanifest` and no icon files
- no page calls `navigator.serviceWorker.register()`
- `sw.js` is still here but **orphaned** — nothing loads it, and its `PRECACHE`
  list points at wallpaper/avatar files that no longer exist, so installing it
  as-is would fail on `addAll()`

Consequences: **no Add to Home Screen, no offline, and no lock-screen
notifications** (on iOS, push only works inside an installed standalone PWA).

To restore it: add `manifest.webmanifest` + icons, register `sw.js` from the
pages, fix its `PRECACHE` list, bump its `CACHE` name, and wire the
notifications toggle to `/app/subscribe` / `/app/unsubscribe`.

---

## 7. "I changed the frontend and nothing happened"

The single most common source of confusion here.

- The old PWA registered a service worker at this origin that does
  **cache-first** for same-origin GETs. **A registered service worker survives
  the code that registered it being deleted** — if you ever opened the old app
  on this origin, it may still be intercepting requests and serving stale
  files. Unregister it in DevTools → Application → Service Workers, or:

  ```javascript
  (async () => {
    for (const r of await navigator.serviceWorker.getRegistrations()) await r.unregister();
    for (const k of await caches.keys()) await caches.delete(k);
    location.reload();
  })()
  ```

- Neither the docker nginx nor the VPS nginx config sets any
  `Cache-Control` header for static files, so the browser applies its own
  heuristic caching. A hard reload (Cmd/Ctrl+Shift+R) bypasses it.

- Phones are the worst case: an installed old PWA keeps its service worker and
  there is no easy hard-reload. Unregister it from the mobile browser first.

---

## 8. Themes

`chat.html` ships three, selectable in chat settings and stored in
`localStorage.rbtl-chat-theme-v1`: **doodle**, **sticker**, **dm**. To add one,
copy a `:root[data-theme="..."]` block in its `<style>` and add a matching
button to the theme grid.

---

## 9. Security

- The connection key **is** the backend's `RELAY_SECRET`. Whoever has it can
  read the entire conversation and impersonate either side. This is a
  single-user model: one key represents you and your AI.
- It is stored in the browser's `localStorage` on each device you enter it on.
- Don't commit a populated key, your domain, or real photos/avatars to a
  public repository.
