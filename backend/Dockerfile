# Backend relay — same image for local (docker-compose), VPS (docker-compose +
# nginx in front), and Render (this Dockerfile deployed directly as a web service).
FROM python:3.12-slim

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY . .
RUN chmod +x entrypoint.sh

# All mutable runtime state (SQLite push_subscriptions DB, uploaded files, the
# brain-target flag) lives under /data, which every deployment mode mounts as a
# volume/disk — so it survives image rebuilds and redeploys. Still overridable via
# env (relay.env / docker-compose.yml / render.yaml) if you want it elsewhere.
ENV RELAY_PORT=3011 \
    RELAY_DB=/data/relay.db \
    RELAY_UPLOAD_DIR=/data/uploads \
    RELAY_BRAIN_FILE=/data/brain_target \
    PORT=3011

EXPOSE 3011

ENTRYPOINT ["./entrypoint.sh"]
