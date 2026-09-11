#!/usr/bin/env python3
"""
bridge_any_llm.py — a bridge that connects "any LLM API" to the AI side of the
companion relay.

This is a generic replacement for the channel/ plugin (which is Claude Code
only): it doesn't depend on Claude Code, and uses any OpenAI-compatible model
(GPT / DeepSeek / Gemini / GLM / Kimi / Qwen / local vLLM …) as the "AI brain".
The frontend PWA and the relay backend are untouched.

It's a "chat with tools" loop, not an autonomous agent that runs off on its own —
it acts once, only when a human message arrives:

    (1) SSE long-poll  GET  {RELAY}/channel/in?since={cursor}   receive human messages (real time)
    (2) with the in-memory recent conversation + persona (system), call your model (OpenAI format)
    (3) POST           {RELAY}/channel/out  {"type":"reply","text":...}   reply back to the phone

On first start it pulls history once for "warm start" context and sets the cursor
to the latest message — so it **does not replay / re-answer your old messages**,
only new ones from after startup. On restart it continues from the last cursor,
answering anything missed while disconnected.

Zero third-party dependencies (Python standard library only, 3.7+). All config is
via environment variables, which can live in a .env next to this file (see
.env.example). To run:

    cp .env.example .env   &&   # fill in RELAY_URL / RELAY_SECRET / LLM_*
    python3 bridge_any_llm.py

WARNING — single-body rule: run only one AI side at a time. Don't run the Claude
   Code channel and this bridge together — both receive the same message and both
   reply, and the user sees a double response.
"""

from __future__ import annotations  # defer type-annotation evaluation, for Python 3.7+ compat

import collections
import json
import os
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path

# ---------------------------------------------------------------------------
# config (environment variables; also reads a .env next to this file)
# ---------------------------------------------------------------------------

def _load_dotenv(path: Path) -> None:
    """Minimal .env loader: KEY=VALUE line by line; real environment variables win."""
    try:
        for line in path.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            k, v = line.split("=", 1)
            os.environ.setdefault(k.strip(), v.strip())
    except FileNotFoundError:
        pass

_load_dotenv(Path(__file__).resolve().parent / ".env")

RELAY_URL = os.environ.get("RELAY_URL", "").rstrip("/")          # your domain + nginx /relay prefix
SECRET    = os.environ.get("RELAY_SECRET", "")                   # must match the backend's relay.env
CHAT_ID   = os.environ.get("RELAY_CHAT_ID", "me")               # single-user channel, always "me"
HISTORY_N = int(os.environ.get("HISTORY_N", "12"))             # recent conversation "turns" fed to the model
TEMPERATURE = float(os.environ.get("LLM_TEMPERATURE", "0.7"))
HTTP_TIMEOUT = int(os.environ.get("LLM_TIMEOUT", "120"))

# persona = the model's character (system prompt). Read from PERSONA text or a PERSONA_FILE.
PERSONA = os.environ.get("PERSONA", "").strip()
_persona_file = os.environ.get("PERSONA_FILE", "").strip()
if not PERSONA and _persona_file:
    try:
        PERSONA = Path(_persona_file).read_text(encoding="utf-8").strip()
    except OSError:
        pass
if not PERSONA:
    PERSONA = "You are the user's AI companion, in a private one-on-one chat. Speak naturally, briefly, and warmly, like texting on a phone — no long monologues."

# Model chain: primary model + optional fallbacks (LLM_*_2 / _3). Any FALLBACK_CODES response moves to the next.
def _model_routes() -> list:
    routes = []
    for suffix in ("", "_2", "_3"):
        base = os.environ.get(f"LLM_API_BASE{suffix}", "").rstrip("/")
        key  = os.environ.get(f"LLM_API_KEY{suffix}", "")
        model = os.environ.get(f"LLM_MODEL{suffix}", "")
        if base and model:
            routes.append({"base": base, "key": key, "model": model})
    return routes

MODEL_ROUTES = _model_routes()
FALLBACK_CODES = {401, 403, 404, 408, 409, 429, 500, 502, 503, 504}

# Reconnect cursor: only process messages with id > cursor; reconnect with
# ?since=cursor so the relay resends what was missed.
STATE_DIR = Path(os.environ.get("BRIDGE_STATE_DIR", Path.home() / ".companion-bridge"))
CURSOR_FILE = STATE_DIR / "last_in_id"

# In-memory rolling conversation context (avoids depending on the pagination
# semantics of the relay's history endpoint — it returns the *earliest* N, not the
# *latest*). Both received human messages and our own replies are appended; the
# tail is fed to the model.
convo: "collections.deque[dict]" = collections.deque(maxlen=max(HISTORY_N * 2, 8))


def log(tag: str, msg: str) -> None:
    print(f"[{time.strftime('%H:%M:%S')}] [{tag}] {msg}", file=sys.stderr, flush=True)


def _require_config() -> None:
    missing = []
    if not RELAY_URL: missing.append("RELAY_URL")
    if not SECRET:    missing.append("RELAY_SECRET")
    if not MODEL_ROUTES: missing.append("LLM_API_BASE + LLM_API_KEY + LLM_MODEL")
    if missing:
        log("fatal", "missing config: " + ", ".join(missing) + "  — fill in .env (see .env.example) and rerun")
        sys.exit(1)


# ---------------------------------------------------------------------------
# relay I/O
# ---------------------------------------------------------------------------

def _auth() -> dict:
    return {"Authorization": f"Bearer {SECRET}"}


def relay_get_json(path: str):
    req = urllib.request.Request(RELAY_URL + path, headers=_auth())
    with urllib.request.urlopen(req, timeout=30) as r:
        return json.loads(r.read().decode("utf-8"))


def relay_post_json(path: str, body: dict):
    data = json.dumps(body, ensure_ascii=False).encode("utf-8")
    req = urllib.request.Request(
        RELAY_URL + path, data=data, method="POST",
        headers={**_auth(), "Content-Type": "application/json"},
    )
    with urllib.request.urlopen(req, timeout=30) as r:
        txt = r.read().decode("utf-8")
        return json.loads(txt) if txt else {}


def send_reply(text: str) -> None:
    """The AI's reply -> persisted + fanned out to the PWA."""
    out = relay_post_json("/channel/out", {
        "type": "reply", "chat_id": CHAT_ID, "text": text,
        "ts": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
    })
    log("out", f"replied (id={out.get('id')})")


# ---------------------------------------------------------------------------
# history -> in-memory context
# ---------------------------------------------------------------------------

def _row_to_msg(m: dict):
    """Convert one relay history/message row to an OpenAI message; return None for
    anything that shouldn't enter the context."""
    text = (m.get("text") or "").strip()
    if not text or m.get("kind") == "call":         # skip system events like call start/end
        return None
    if m.get("from") == "human":
        return {"role": "user", "content": text}     # includes voice transcripts (🎤 …)
    if m.get("from") == "ai" and m.get("kind") == "reply":
        return {"role": "assistant", "content": text}  # skip intermediate states like thinking/act
    return None


def load_history() -> tuple:
    """Page through all history -> (recent conversation messages, id of the latest
    row). The relay's history is `id > since ASC LIMIT`, so page forward from 0
    until exhausted, then take the tail as context."""
    rows, since = [], 0
    while True:
        page = relay_get_json(f"/app/history?since={since}&limit=500").get("messages", [])
        if not page:
            break
        rows.extend(page)
        since = page[-1]["id"]
        if len(page) < 500:
            break
    max_id = rows[-1]["id"] if rows else 0
    msgs = [mm for m in rows if (mm := _row_to_msg(m))]
    return msgs[-convo.maxlen:], max_id


def build_messages() -> list:
    return [{"role": "system", "content": PERSONA}] + list(convo)


# ---------------------------------------------------------------------------
# call the model (OpenAI chat/completions; with a fallback chain)
# ---------------------------------------------------------------------------

def _one_call(route: dict, messages: list) -> str:
    body = json.dumps({
        "model": route["model"],
        "messages": messages,
        "temperature": TEMPERATURE,
        # To add function calling: put "tools": [...] here, handle tool_calls in the
        # response, and loop them back in (cap at ~8 steps).
    }, ensure_ascii=False).encode("utf-8")
    req = urllib.request.Request(
        route["base"] + "/chat/completions", data=body, method="POST",
        headers={"Authorization": f"Bearer {route['key']}", "Content-Type": "application/json"},
    )
    with urllib.request.urlopen(req, timeout=HTTP_TIMEOUT) as r:
        data = json.loads(r.read().decode("utf-8"))
    return (data["choices"][0]["message"]["content"] or "").strip()


def call_llm(messages: list) -> str:
    last_err = None
    for route in MODEL_ROUTES:
        try:
            return _one_call(route, messages)
        except urllib.error.HTTPError as e:
            last_err = e
            if e.code in FALLBACK_CODES:
                log("llm", f"{route['model']} HTTP {e.code} → next route")
                continue
            raise
        except (urllib.error.URLError, TimeoutError) as e:
            last_err = e
            log("llm", f"{route['model']} connection failed ({e}) → next route")
            continue
    raise RuntimeError(f"all models failed, last error: {last_err}")


# ---------------------------------------------------------------------------
# handling one message
# ---------------------------------------------------------------------------

def handle_human_message(msg: dict) -> None:
    content = (msg.get("content") or "").strip()
    atts = msg.get("attachments") or []
    if atts:
        # Images/attachments: to let a multimodal model see them, GET
        # {RELAY}/uploads/{name}?token={SECRET} here to download, then put them
        # into the last user message in your model's format (base64 / image_url).
        # This reference implementation degrades to a one-line text note to stay simple.
        names = ", ".join(a.get("name") or "file" for a in atts)
        content = (content + "\n" if content else "") + f"(the user sent {len(atts)} attachment(s): {names})"
    if not content:
        return
    log("in", f"#{msg.get('id')}: {content[:60]}")
    convo.append({"role": "user", "content": content})
    try:
        reply = call_llm(build_messages())
    except Exception as e:
        log("err", f"generation failed: {e}")
        return
    if reply:
        convo.append({"role": "assistant", "content": reply})
        send_reply(reply)


# ---------------------------------------------------------------------------
# SSE inbound stream: GET /channel/in (auto-reconnects on disconnect)
# ---------------------------------------------------------------------------

def read_cursor() -> int:
    try:
        return int(CURSOR_FILE.read_text().strip() or "0")
    except (OSError, ValueError):
        return 0


def write_cursor(i: int) -> None:
    try:
        STATE_DIR.mkdir(parents=True, exist_ok=True)
        CURSOR_FILE.write_text(str(i))
    except OSError:
        pass


def stream_inbound(cursor: int) -> None:
    backoff = 1
    while True:
        try:
            url = f"{RELAY_URL}/channel/in?since={cursor}"
            req = urllib.request.Request(url, headers={**_auth(), "Accept": "text/event-stream"})
            # timeout just needs to be longer than the relay's 15s heartbeat ping:
            # a timeout means it really disconnected — go reconnect.
            with urllib.request.urlopen(req, timeout=90) as resp:
                log("in", f"stream connected (since={cursor})")
                backoff = 1
                data_lines: list = []
                for raw in resp:
                    line = raw.decode("utf-8", "replace").rstrip("\r\n")
                    if line.startswith("data:"):
                        data_lines.append(line[5:].lstrip())
                    elif line == "":                      # blank line = end of a frame
                        if not data_lines:
                            continue
                        payload, data_lines = "\n".join(data_lines), []
                        try:
                            m = json.loads(payload)
                        except json.JSONDecodeError:
                            continue
                        if m.get("type") == "ping" or "id" not in m:
                            continue
                        mid = int(m.get("id") or 0)
                        if mid <= cursor:                 # already handled in the resend, skip
                            continue
                        handle_human_message(m)
                        cursor = mid
                        write_cursor(cursor)              # advance the cursor only after handling
            log("in", "stream ended → reconnect")
        except Exception as e:
            log("in", f"disconnected ({e}) → retry in {backoff}s")
            time.sleep(backoff)
            backoff = min(backoff * 2, 15)


def main() -> None:
    _require_config()
    log("boot", f"relay={RELAY_URL}  models={[r['model'] for r in MODEL_ROUTES]}  history={HISTORY_N}")
    cursor = read_cursor()
    # Warm start: pull history to fill context, and for a brand-new deployment set
    # the cursor to "current latest" — no replaying / re-answering old messages.
    try:
        ctx, max_id = load_history()
        convo.extend(ctx)
        if cursor == 0:
            cursor = max_id
            write_cursor(cursor)
        log("boot", f"warm-start: {len(convo)} msgs in context, cursor={cursor}")
    except Exception as e:
        log("boot", f"history warm-start skipped ({e})")
    stream_inbound(cursor)


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        pass
