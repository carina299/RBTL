#!/usr/bin/env python3
"""One-off: copy chat history from the legacy SQLite DB into Postgres.

    python scripts/migrate_sqlite_to_pg.py [--sqlite ./relay.db] [--dry-run] [--truncate]

What it does
------------
- reads every row from SQLite `messages`
- maps it to the new schema:
    direction 'in'/'out'      -> from_role 'human'/'ai'
    ts (TEXT)                 -> created_at (TIMESTAMPTZ)
    meta JSON                 -> split into attachments / reactions / api_session
                                columns; everything else stays in the meta JSONB
- writes with the ORM, preserving the original `id`
- re-syncs the `messages_id_seq` sequence so new inserts don't collide

Idempotent: uses Session.merge(), so re-running updates rather than duplicating.
`push_subscriptions` is intentionally left on SQLite and not touched.

Run `alembic upgrade head` first — this needs the `meta` and `api_session`
columns to exist.
"""

import argparse
import json
import os
import sqlite3
import sys
from pathlib import Path

# make "import database" / "import models" work when run from backend/
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from sqlalchemy import func, inspect, select, text  # noqa: E402

from database import SessionLocal, engine  # noqa: E402
from models import Message  # noqa: E402
from repositories import _PROMOTED_META_KEYS, parse_ts  # noqa: E402


def load_sqlite_rows(sqlite_path: Path) -> list[sqlite3.Row]:
    if not sqlite_path.exists():
        sys.exit(f"SQLite DB not found: {sqlite_path}")
    conn = sqlite3.connect(sqlite_path)
    conn.row_factory = sqlite3.Row
    try:
        names = {r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        if "messages" not in names:
            sys.exit(f"No `messages` table in {sqlite_path}")
        return conn.execute("SELECT * FROM messages ORDER BY id ASC").fetchall()
    finally:
        conn.close()


def row_to_message(row: sqlite3.Row) -> Message:
    try:
        meta = json.loads(row["meta"] or "{}")
    except (TypeError, ValueError):
        meta = {}
    if not isinstance(meta, dict):
        meta = {}

    attachments = meta.get("attachments")
    reactions = meta.get("reactions")
    return Message(
        id=row["id"],
        from_role="human" if row["direction"] == "in" else "ai",
        kind=row["kind"] or "user",
        text=row["text"],
        api_session=(meta.get("api_session") or None),
        attachments=attachments if isinstance(attachments, list) else [],
        reactions=reactions if isinstance(reactions, dict) else {},
        meta={k: v for k, v in meta.items() if k not in _PROMOTED_META_KEYS},
        created_at=parse_ts(row["ts"]),
    )


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--sqlite", default=os.environ.get("RELAY_DB", "./relay.db"),
                    help="path to the legacy SQLite DB (default: $RELAY_DB or ./relay.db)")
    ap.add_argument("--dry-run", action="store_true", help="report only, write nothing")
    ap.add_argument("--truncate", action="store_true",
                    help="DELETE all rows from Postgres `messages` before importing")
    args = ap.parse_args()

    # schema guard
    cols = {c["name"] for c in inspect(engine).get_columns("messages")}
    missing = {"meta", "api_session"} - cols
    if missing:
        sys.exit(f"Postgres `messages` is missing {missing}. Run `alembic upgrade head` first.")

    rows = load_sqlite_rows(Path(args.sqlite))
    print(f"SQLite: {len(rows)} message(s) in {args.sqlite}")

    session = SessionLocal()
    try:
        existing = session.scalar(select(func.count()).select_from(Message))
        print(f"Postgres: {existing} message(s) before import")

        if args.dry_run:
            for row in rows[:5]:
                m = row_to_message(row)
                print(f"  would import #{m.id}: {m.from_role}/{m.kind} "
                      f"{(m.text or '')[:48]!r} meta_keys={list(m.meta)}")
            if len(rows) > 5:
                print(f"  ... and {len(rows) - 5} more")
            print("dry run — nothing written")
            return

        if args.truncate:
            n = session.execute(text("DELETE FROM messages")).rowcount
            print(f"  truncated {n} existing row(s)")

        for row in rows:
            session.merge(row_to_message(row))

        # keep BIGSERIAL ahead of the imported ids
        session.execute(text(
            "SELECT setval(pg_get_serial_sequence('messages', 'id'), "
            "GREATEST((SELECT COALESCE(MAX(id), 1) FROM messages), 1))"
        ))
        session.commit()

        total = session.scalar(select(func.count()).select_from(Message))
        seq_at = session.execute(text("SELECT last_value FROM messages_id_seq")).scalar()
        print(f"Postgres: {total} message(s) after import · id sequence at {seq_at}")
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()


if __name__ == "__main__":
    main()
