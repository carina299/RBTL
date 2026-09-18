"""Shared fixtures. Tests that need Postgres use the `db_session` fixture,
which talks to whatever RELAY_DATABASE_URL already points at (see
database.py's default — matches docker-compose.yml's default credentials)
and skips (not fails) if it can't connect, so `pytest` still runs cleanly on
a machine with no Postgres up.
"""

import uuid

import pytest
from sqlalchemy import text
from sqlalchemy.exc import OperationalError

from database import SessionLocal


@pytest.fixture
def db_session():
    try:
        session = SessionLocal()
        session.execute(text("SELECT 1"))
    except OperationalError:
        pytest.skip("Postgres not reachable at RELAY_DATABASE_URL — skipping DB-backed test")
    try:
        yield session
        session.rollback()  # tests manage their own cleanup; never leave a stray commit
    finally:
        session.close()


@pytest.fixture
def test_user_id() -> str:
    """A throwaway user_id so memory rows from different test runs never collide."""
    return str(uuid.uuid4())
