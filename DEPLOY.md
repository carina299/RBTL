# Deployment — 3 modes

Same backend (`backend/`), same frontend (`web/`), same AI side (`channel/` or
`examples/`) in all three. What differs is only **where the backend + Postgres +
static frontend run**, and — for split-origin cloud — one line in the frontend
config so it knows where to find the backend.

| Mode | Backend + Postgres | Frontend | Good for |
|---|---|---|---|
| [**Local**](#1-local) | `docker compose`, your machine | `docker compose`, your machine | dev, testing |
| [**VPS**](#2-vps) | `docker compose` + nginx (TLS), your server | same nginx, static files | a real deployment you fully control |
| [**Website**](#3-website-vercelrender) | Render (web service + managed Postgres) | Vercel (static) | zero server ops, free tiers |

The **AI side never runs in any of these** — `channel/` (Claude Code plugin) or
`examples/bridge_any_llm.py` always run on your own computer, pointed at whichever
relay URL you deployed. See `channel/DEPLOY.md`.

---

## 1. Local

```bash
cp .env.example .env
# edit .env: at minimum set RELAY_SECRET (generate one with
#   python3 -c "import secrets; print(secrets.token_urlsafe(32))"
# )
docker compose up -d --build
```

This starts six containers:

- `postgres` — pgvector/pg16, migrations run automatically on backend startup
- `redis` — Celery broker for the long-term-memory background job (only does
  anything once `MEMORY_EXTRACTION_ENABLED=true`)
- `minio` — S3-compatible object storage for the album feature (only actually
  used when `STORAGE_BACKEND=minio`; `local` just writes into the backend's
  own `uploads/` dir and this container sits idle)
- `backend` — the relay, `http://localhost:3011`
- `celery_worker` — runs the memory-extraction job and its daily maintenance
  reweight; same image as `backend`, no uvicorn
- `web` — the PWA, `http://localhost:8080`

Open `http://localhost:8080`, paste `RELAY_SECRET` into the login box. `docker
compose logs -f backend` to watch it; `docker compose down` to stop (add `-v` to
also wipe the Postgres/Redis/MinIO volumes and uploaded files — normally you
don't want that).

Long-term memory and the album feature both have their own optional
configuration (LLM/embedding provider choice, MinIO vs. local storage) — see
[`backend/DEPLOY.md`](backend/DEPLOY.md#5-long-term-memory) for the full
picture; nothing here is required just to run the core chat.

To point the AI side at this: `channel/.env`'s `RELAY_URL=http://127.0.0.1:3011`
(see `channel/DEPLOY.md`).

---

## 2. VPS

Same `docker-compose.yml` as local, with nginx in front for TLS + your domain.

```bash
# on the VPS
git clone <this repo> && cd RBTL
cp .env.example .env
```

Edit `.env`:
- `RELAY_SECRET` — a fresh one, never reused
- `RELAY_PUBLIC_PREFIX=/relay` — nginx strips this prefix (unlike local, where the
  frontend talks directly to `:3011`)
- `RELAY_ALLOW_ORIGINS=https://your-domain.example`

```bash
docker compose up -d --build
```

Backend now listens on `127.0.0.1:3011`, frontend static files are served by the
`web` container on `127.0.0.1:8080` — both host-local only. Put nginx in front of
both: copy the two `location` blocks from
[`backend/nginx-companion.conf.example`](backend/nginx-companion.conf.example)
into your domain's `server { listen 443 ssl; ... }` block (`/relay/` →
`127.0.0.1:3011`, `/chat/` → `127.0.0.1:8080`), then `nginx -t && systemctl reload
nginx`. Full smoke-test checklist: [`backend/DEPLOY.md`](backend/DEPLOY.md) §2.5.

> Prefer running the backend directly with systemd + a venv instead of Docker?
> That path still works — see `backend/DEPLOY.md` §2.1–2.3 — you'll just need to
> also stand up Postgres yourself (`docker compose up -d postgres` from this repo
> works fine for just that piece) and set `RELAY_DATABASE_URL` /
> run `alembic upgrade head` before starting it.

---

## 3. Website (Vercel/Render)

Split origin: the backend needs a persistent process (SSE — a plain serverless
function can't hold those connections open), so it goes on **Render**; the static
frontend goes on **Vercel**.

### 3.1 Backend → Render

1. Push this repo to GitHub.
2. Render dashboard → **New → Blueprint** → pick the repo → it reads
   [`render.yaml`](render.yaml) → **Apply**. This creates:
   - a free Postgres database
   - a web service built from `backend/Dockerfile`, with `RELAY_SECRET`
     auto-generated and `DATABASE_URL` wired to the database
3. Once it's live, copy the service URL (`https://rbtl-backend-xxxx.onrender.com`).
4. Copy the auto-generated `RELAY_SECRET` from the service's Environment tab —
   you'll need it for the PWA login and `channel/.env`.

> **Uploads are ephemeral on Render's free plan** — images/voice attachments
> disappear on every redeploy or restart (no persistent disk on free tier). Fine
> for testing; for real use, upgrade to a plan with a disk and uncomment the
> `disk:` block in `render.yaml`, or point `RELAY_UPLOAD_DIR` at real object
> storage (out of scope here).

> **Long-term memory and MinIO-backed albums aren't provisioned by
> `render.yaml`** — it's just the web service + Postgres. `MEMORY_EXTRACTION_ENABLED`
> defaults to `false` and `STORAGE_BACKEND` defaults to `local`, so the backend
> boots and runs fine as-is; to actually enable memory extraction here you'd
> need to add your own Redis instance and a second worker service running
> `celery -A celery_app worker -B`, and for MinIO-backed albums a real
> S3/R2/MinIO bucket reachable from both Render and the browser. See
> [`backend/DEPLOY.md`](backend/DEPLOY.md#5-long-term-memory) for what each
> piece needs.

### 3.2 Frontend → Vercel

1. Vercel dashboard → **New Project** → import the repo → set **Root Directory**
   to `web`. No build command, no output directory — it's static.
2. Before deploying, edit [`web/index.html`](web/index.html) (and
   [`web/album.html`](web/album.html)) — set:
   ```js
   const RELAY_URL = "https://rbtl-backend-xxxx.onrender.com";  // your Render URL from 3.1
   ```
   (This is the one line that makes a split-origin deploy work with zero build
   step — see the comment above it in `index.html`.)
3. Deploy. Vercel gives you `https://your-project.vercel.app`.

### 3.3 Close the loop

Back in Render's dashboard, set two env vars on the backend to your Vercel URL and
redeploy:
- `RELAY_ALLOW_ORIGINS=https://your-project.vercel.app` — until this matches, the
  browser blocks every request with a CORS error.
- `RELAY_APP_PATH=https://your-project.vercel.app/` — where a push-notification
  tap should open (there's no same-origin PWA to default to here, unlike VPS).

Point the AI side at the Render URL (`channel/.env`'s `RELAY_URL=`), same as any
other mode.

---

## Frontend config reference

`web/index.html` / `web/album.html` resolve the backend address in this order:

1. `RELAY_URL` set → use it verbatim (**website mode**, split origins)
2. `RELAY_URL` empty + running on `localhost`/`127.0.0.1` → talk to `:3011`
   directly (**local mode**, no nginx in front)
3. `RELAY_URL` empty + anywhere else → same-origin relative `/relay`
   (**VPS mode**, nginx reverse-proxies it)

`RELAY_PUBLIC_PREFIX` on the backend mirrors this: `/relay` when nginx strips a
prefix (VPS), empty when the app is reachable at its own root (local docker,
Render).
