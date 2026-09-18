"""Celery tasks: the extraction_graph trigger and the daily memory
maintenance reweight. Both are background jobs; nothing in this
file makes the "search vs. write" decision itself, that's the memory package.
"""

from celery_app import celery_app
from database import session_scope
from memory.extraction_graph import DEFAULT_DIALOGUE_LIMIT, run_extraction
from repositories import DEFAULT_USER_ID, MemoryRepository


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
