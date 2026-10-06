"""Celery tasks: the extraction_graph trigger, the daily memory maintenance
reweight, and the proactive-outreach tick. All background jobs; nothing in this
file makes the "search vs. write" decision itself, that's the memory package,
and nothing here decides what the AI says.
"""

import json
import os
import urllib.error
import urllib.request
from datetime import datetime, timedelta, timezone

import proactive
from celery_app import REDIS_URL, celery_app
from database import session_scope
from memory.extraction_graph import DEFAULT_DIALOGUE_LIMIT, run_extraction
from repositories import (
    DEFAULT_USER_ID, MemoryRepository, MessageRepository, ProactiveSendRepository,
)

# The worker reaches the web process over the compose network; both read the
# same RELAY_SECRET out of the environment.
RELAY_INTERNAL_URL = os.environ.get("RELAY_INTERNAL_URL", "http://backend:3011").rstrip("/")
RELAY_SECRET = os.environ.get("RELAY_SECRET", "")

_TICK_KEY = "proactive:last_tick"


def _tick_is_due(now: datetime) -> bool:
    """Whether enough time has passed to reconsider, per the day/night pace.

    Beat fires on a fixed short interval because its schedule is static, so the
    slower night cadence is enforced here instead. The marker lives in Redis —
    already present as the broker — because the worker is not guaranteed to be
    the same process from one tick to the next.
    """
    interval = timedelta(minutes=proactive.check_interval_minutes(now))
    try:
        import redis

        client = redis.Redis.from_url(REDIS_URL)
        raw = client.get(_TICK_KEY)
        if raw:
            last = datetime.fromisoformat(raw.decode())
            if now - last < interval:
                return False
        client.set(_TICK_KEY, now.isoformat())
        return True
    except Exception as exc:
        # Losing the marker only costs a slightly eager cadence; the gate in
        # proactive.evaluate is what actually protects the human.
        print(f"[proactive] tick marker unavailable ({type(exc).__name__}) — checking anyway")
        return True


def _local_day_start(now: datetime) -> datetime:
    """Midnight in her timezone, so the daily cap resets when her day does."""
    local = now.astimezone(proactive.local_timezone())
    return local.replace(hour=0, minute=0, second=0, microsecond=0).astimezone(timezone.utc)


def _nudge_text(last_human_at: datetime | None, now: datetime) -> str:
    template = os.environ.get("PROACTIVE_NUDGE_TEMPLATE", "").strip()
    local = now.astimezone(proactive.local_timezone())
    quiet_for = int(proactive.silence_minutes(last_human_at, now)) if last_human_at else 0
    if template:
        return template.format(local_time=local.strftime("%H:%M"), quiet_minutes=quiet_for)
    # Saying outright that silence is a valid answer matters: without it the
    # model treats being woken as an instruction to produce something, and
    # every tick becomes a message whether or not one was warranted.
    return (
        f"(Background wake-up — she has not sent anything. It is {local.strftime('%H:%M')} "
        f"where she is and her last message was about {quiet_for} waking minutes ago. "
        f"Say something only if you actually want to; ignoring this is a normal outcome.)"
    )


def _post_nudge(content: str) -> bool:
    """Returns whether an AI side was actually listening."""
    payload = json.dumps({"content": content}).encode()
    request = urllib.request.Request(
        f"{RELAY_INTERNAL_URL}/internal/nudge",
        data=payload,
        headers={"Content-Type": "application/json", "Authorization": f"Bearer {RELAY_SECRET}"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(request, timeout=10) as response:
            return bool(json.loads(response.read()).get("delivered"))
    except Exception as exc:
        print(f"[proactive] nudge failed: {type(exc).__name__}: {exc}")
        return False


@celery_app.task(name="memory.extract", bind=True, max_retries=2, default_retry_delay=30)
def extract_memories_task(self, user_id: str, limit: int = DEFAULT_DIALOGUE_LIMIT):
    try:
        with session_scope() as session:
            results = run_extraction(session, user_id, limit=limit)
        print(f"[memory] extraction for {user_id}: {len(results)} outcome(s)")
        return results
    except Exception as exc:
        print(f"[memory] extraction failed for {user_id}: {type(exc).__name__}: {exc}")
        raise self.retry(exc=exc)


@celery_app.task(name="memory.reweight", bind=True, max_retries=2, default_retry_delay=60)
def reweight_memories_task(self, user_id: str = DEFAULT_USER_ID):
    """Daily maintenance: small importance boost for often-accessed
    memories, small decay for long-unaccessed ones. Scheduled via Celery beat
    (see celery_app.py's beat_schedule); safe to also run by hand.
    """
    try:
        with session_scope() as session:
            result = MemoryRepository(session).reweight(user_id)
        print(f"[memory] reweight for {user_id}: {result}")
        return result
    except Exception as exc:
        print(f"[memory] reweight failed for {user_id}: {type(exc).__name__}: {exc}")
        raise self.retry(exc=exc)


@celery_app.task(name="proactive.tick", bind=True, max_retries=0)
def proactive_tick_task(self, user_id: str = DEFAULT_USER_ID):
    """Consider reaching out, and do it if the moment allows.

    Split of responsibility: this decides *whether* to interrupt, using facts
    it can see (how long she has been quiet, when outreach last landed, how
    much of today's allowance is left). It never decides what to say — it wakes
    the AI and hands it the situation, and the AI may well choose silence.

    The nudge has to go over HTTP even though both processes share a database:
    the set of connected AI sides lives in the web process's memory, so a
    worker cannot broadcast into it directly.

    An attempt is recorded either way, but only a delivered one counts against
    the budget — see ProactiveSendRepository.
    """
    if not proactive.ENABLED:
        return {"skipped": "disabled"}

    now = datetime.now(timezone.utc)
    if not _tick_is_due(now):
        return {"skipped": "not due yet"}

    source = proactive.ProactiveSource.silence_checkin
    with session_scope() as session:
        last_human = MessageRepository(session).latest_human_at()
        sends = ProactiveSendRepository(session)
        decision = proactive.evaluate(
            source,
            last_human_message_at=last_human,
            last_delivered_at=sends.last_delivered_at(user_id),
            delivered_today=sends.delivered_since(user_id, _local_day_start(now)),
            now=now,
        )
        if not decision.allowed:
            print(f"[proactive] holding off — {decision.reason}")
            return {"sent": False, "reason": decision.reason}

        delivered = _post_nudge(_nudge_text(last_human, now))
        sends.record(user_id, source.value, delivered)

    print(f"[proactive] {decision.reason} — delivered={delivered}")
    return {"sent": delivered, "reason": decision.reason}
