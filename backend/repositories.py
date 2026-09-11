"""Data-access layer. Routes and the app.py storage helpers go through a
repository; this module is the only place that touches the ORM.
"""

from datetime import datetime, timezone

from sqlalchemy import select
from sqlalchemy.orm import Session

from models import Message

# Single-user project: every row carries this until real auth exists.
DEFAULT_USER_ID = "00000000-0000-0000-0000-000000000001"

_ROLE_TO_DIRECTION = {"human": "in", "ai": "out"}
_DIRECTION_TO_ROLE = {"in": "human", "out": "ai"}

# meta keys that get their own column — kept out of the meta JSONB blob
_PROMOTED_META_KEYS = {"attachments", "reactions", "api_session", "ts"}


def parse_ts(value) -> datetime | None:
    """Parse an ISO timestamp (accepts a trailing 'Z'); return None if unusable."""
    if not value:
        return None
    try:
        dt = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except ValueError:
        return None
    return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)


def message_to_dict(m: Message) -> dict:
    """Map an ORM row back to the shape app_payload()/plugin_payload() expect
    (id / ts / direction / kind / text / meta), so the SSE + PWA + channel
    contract is unchanged by the storage swap. Call this while the row is still
    attached to a live Session.
    """
    meta = dict(m.meta or {})
    meta["attachments"] = m.attachments or []
    if m.reactions:
        meta["reactions"] = m.reactions
    if m.api_session:
        meta["api_session"] = m.api_session
    return {
        "id": m.id,
        "ts": (m.created_at or datetime.now(timezone.utc)).isoformat(),
        "direction": _ROLE_TO_DIRECTION.get(m.from_role, "out"),
        "kind": m.kind,
        "text": m.text or "",
        "meta": meta,
    }


class MessageRepository:
    def __init__(self, session: Session):
        self.session = session

    # --- reads ----------------------------------------------------------
    def list_since(self, since_id: int, limit: int, session_id: str | None = None) -> list[Message]:
        """id > since_id, oldest first.
        session_id: None/"" = no filter · "__legacy__" = rows with no api_session ·
        anything else = that api_session only.
        """
        stmt = select(Message).where(Message.id > since_id)
        if session_id == "__legacy__":
            stmt = stmt.where(Message.api_session.is_(None))
        elif session_id:
            stmt = stmt.where(Message.api_session == session_id)
        stmt = stmt.order_by(Message.id.asc()).limit(limit)
        return list(self.session.scalars(stmt))

    def inbound_since(self, since_id: int, limit: int) -> list[Message]:
        stmt = (
            select(Message)
            .where(Message.id > since_id, Message.from_role == "human")
            .order_by(Message.id.asc())
            .limit(limit)
        )
        return list(self.session.scalars(stmt))

    def latest_non_thinking(self) -> Message | None:
        stmt = select(Message).where(Message.kind != "thinking").order_by(Message.id.desc()).limit(1)
        return self.session.scalars(stmt).first()

    def max_id(self) -> int:
        return self.session.scalar(select(Message.id).order_by(Message.id.desc()).limit(1)) or 0

    def get(self, message_id: int) -> Message | None:
        return self.session.get(Message, message_id)

    # --- writes -------------------------------------------------------
    def create(self, direction: str, kind: str, text: str | None, meta: dict) -> Message:
        """Insert one message. `direction` and `meta` are in app.py's vocabulary
        ('in'/'out', a flat meta dict); this splits meta into the promoted
        columns and stashes the rest in the meta blob.
        """
        meta = meta or {}
        attachments = meta.get("attachments")
        reactions = meta.get("reactions")
        m = Message(
            user_id=DEFAULT_USER_ID,
            from_role=_DIRECTION_TO_ROLE.get(direction, "ai"),
            kind=kind,
            text=text,
            api_session=(meta.get("api_session") or None),
            attachments=attachments if isinstance(attachments, list) else [],
            reactions=reactions if isinstance(reactions, dict) else {},
            meta={k: v for k, v in meta.items() if k not in _PROMOTED_META_KEYS},
        )
        created_at = parse_ts(meta.get("ts"))
        if created_at is not None:
            m.created_at = created_at
        self.session.add(m)
        self.session.flush()      # emit INSERT, populate m.id
        self.session.refresh(m)   # load server defaults (created_at) for the response
        return m

    def set_reaction(self, message_id: int, who: str, emoji: str) -> dict | None:
        """Set (or, with an empty emoji, clear) one party's reaction. Returns the
        message's reactions dict, or None if the message doesn't exist.
        """
        m = self.session.get(Message, message_id)
        if m is None:
            return None
        reactions = dict(m.reactions or {})
        if emoji:
            reactions[who] = emoji
        else:
            reactions.pop(who, None)
        m.reactions = reactions   # reassign so SQLAlchemy sees the change
        self.session.flush()
        return reactions
