"""SQLAlchemy connection layer.

Four things live here, in the order FastAPI touches them:

  engine        one connection pool, created once at import, shared process-wide
  SessionLocal  a factory that hands out Session objects bound to that engine
  Base          declarative base every model in models.py inherits from
  get_db        the per-request dependency: `db: Session = Depends(get_db)`

The DB URL comes from RELAY_DATABASE_URL (see relay.env); the default matches
docker-compose.yml so local dev works with no config.
"""

import os
from contextlib import contextmanager

from sqlalchemy import create_engine
from sqlalchemy.orm import DeclarativeBase, Session, sessionmaker

DATABASE_URL = os.environ.get(
    "RELAY_DATABASE_URL",
    "postgresql+psycopg://tidal_echo:tidal_echo_password@localhost:5432/tidal_echo",
)

engine = create_engine(
    DATABASE_URL,
    pool_pre_ping=True,   # drop connections the server/proxy killed instead of erroring
    future=True,
)

SessionLocal = sessionmaker(bind=engine, autoflush=False, autocommit=False, future=True)


class Base(DeclarativeBase):
    pass


def get_db() -> Session:
    """Yield one Session per request; roll back on error, always close.

    FastAPI runs this sync generator in a threadpool, so it never blocks the
    event loop. Writes commit explicitly (in the repository); this dependency
    only guarantees cleanup.

    A route using this dependency should be a plain `def` (FastAPI threadpools
    it too). If it must stay `async def` — e.g. it also awaits an SSE broadcast —
    wrap the DB calls in `fastapi.concurrency.run_in_threadpool`, or the sync
    query will stall every other connection.
    """
    db = SessionLocal()
    try:
        yield db
    except Exception:
        db.rollback()
        raise
    finally:
        db.close()


@contextmanager
def session_scope() -> Session:
    """Transactional scope for code outside a FastAPI request (the sync storage
    helpers in app.py, scripts): commit on success, roll back on error, close.

    Callers awaiting this from async code must run it in a thread
    (`await asyncio.to_thread(...)`), since the session is synchronous.
    """
    db = SessionLocal()
    try:
        yield db
        db.commit()
    except Exception:
        db.rollback()
        raise
    finally:
        db.close()
