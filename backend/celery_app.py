"""Celery app for background memory jobs: extraction (Celery's only job is
triggering `extraction_graph.invoke(...)`, the graph itself owns the logic)
and the daily maintenance reweight.

Run the worker from backend/ (with beat, since this is a single small
deployment — no separate beat process):
    celery -A celery_app worker -B --loglevel=info

Broker and result backend both point at Redis; see docker-compose.yml's
`redis` service.
"""

import os

from celery import Celery
from celery.schedules import crontab

REDIS_URL = os.environ.get("REDIS_URL", "redis://localhost:6379/0")

# Matches the daytime check interval; tasks.py skips the ticks that are too
# early for the current hour's pace.
PROACTIVE_TICK_SECONDS = 60 * int(os.environ.get("PROACTIVE_DAY_CHECK_MINUTES", "10") or 10)

celery_app = Celery("rbtl_memory", broker=REDIS_URL, backend=REDIS_URL, include=["tasks"])

celery_app.conf.update(
    task_serializer="json",
    result_serializer="json",
    accept_content=["json"],
    timezone="UTC",
    enable_utc=True,
    beat_schedule={
        "memory-reweight-daily": {
            "task": "memory.reweight",
            "schedule": crontab(hour=3, minute=0),  # low-traffic hour, UTC
        },
        # Fires at the daytime pace and lets the task itself hold back at night —
        # a beat entry's schedule is fixed at import, while the cadence this
        # wants depends on the local hour. The task is a no-op unless
        # PROACTIVE_ENABLED is set.
        "proactive-tick": {
            "task": "proactive.tick",
            "schedule": PROACTIVE_TICK_SECONDS,
        },
    },
)
