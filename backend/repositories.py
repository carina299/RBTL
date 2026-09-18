"""Data-access layer. Routes and the app.py storage helpers go through a
repository; this module is the only place that touches the ORM.
"""

from datetime import datetime, timedelta, timezone

from sqlalchemy import and_, func, or_, select, update
from sqlalchemy.orm import Session

from models import AlbumEntry, Attachment, Memory, Message

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

    def recent(self, limit: int) -> list[Message]:
        """Most recent `limit` messages, oldest first — the "last N turns" the
        memory extraction pipeline reads, as opposed to `list_since`'s
        "everything after a cursor".
        """
        stmt = select(Message).order_by(Message.id.desc()).limit(limit)
        return list(reversed(self.session.scalars(stmt).all()))

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


# Single fixed owner for this phase — one agent, no routing (for future implementation use).
DEFAULT_OWNER_AGENT_ID = "companion"


def memory_to_dict(m: Memory) -> dict:
    """Shape a `Memory` row for the `/memory/list` debug/admin endpoint."""
    return {
        "id": m.id,
        "memory_type": m.memory_type,
        "content": m.content,
        "importance": m.importance,
        "source": m.source,
        "status": m.status,
        "source_message_ids": m.source_message_ids or [],
        "superseded_by": m.superseded_by,
        "created_at": m.created_at.isoformat(),
        "last_accessed_at": m.last_accessed_at.isoformat(),
        "access_count": m.access_count,
    }


class MemoryRepository:
    """CRUD for the `memories` table. Working memory has no repository of its
    own — callers needing it just use `MessageRepository` against recent
    rows. This class only handles the two tiers that persist across
    sessions: episodic and semantic.

    Writing the embedding, the extraction pipeline, and the reranked
    `/memory/retrieve` search are later stages and live elsewhere once
    built; this repository only guarantees the storage primitives they'll
    need: create, fetch, list-active, the supersede chain, soft delete, and
    access-tracking.
    """

    def __init__(self, session: Session):
        self.session = session

    # --- reads ----------------------------------------------------------
    def get(self, memory_id: int) -> Memory | None:
        return self.session.get(Memory, memory_id)

    def list_active(
        self,
        user_id: str,
        memory_type: str | None = None,
        limit: int = 100,
    ) -> list[Memory]:
        """Active (non-superseded, non-deleted) memories, newest first.
        `memory_type`: None = both tiers · 'episodic' | 'semantic' = one tier only.
        """
        stmt = select(Memory).where(Memory.user_id == user_id, Memory.status == "active")
        if memory_type:
            stmt = stmt.where(Memory.memory_type == memory_type)
        stmt = stmt.order_by(Memory.created_at.desc()).limit(limit)
        return list(self.session.scalars(stmt))

    def find_most_similar(
        self,
        user_id: str,
        memory_type: str,
        embedding: list[float],
    ) -> tuple[Memory, float] | None:
        """Nearest active memory of the same tier by cosine similarity, for the
        extraction/`memory_remember` dedup decision. Returns
        (memory, similarity) — similarity = 1 - cosine_distance, in [-1, 1],
        practically [0, 1] for embeddings from the same model — or None if this
        user has no active memories of that type yet.
        """
        distance = Memory.embedding.cosine_distance(embedding)
        stmt = (
            select(Memory, distance)
            .where(
                Memory.user_id == user_id,
                Memory.memory_type == memory_type,
                Memory.status == "active",
            )
            .order_by(distance)
            .limit(1)
        )
        row = self.session.execute(stmt).first()
        if row is None:
            return None
        memory, dist = row
        return memory, 1.0 - dist

    def search_candidates(
        self,
        user_id: str,
        embedding: list[float],
        limit: int = 30,
        memory_type: str | None = None,
    ) -> list[tuple[Memory, float]]:
        """Top-`limit` active memories by cosine similarity — the coarse
        candidate pool `retrieval_graph`'s `vector_retrieve` node reranks from.
        Unlike `find_most_similar`, this returns many rows, not just the best
        one.
        """
        distance = Memory.embedding.cosine_distance(embedding)
        stmt = select(Memory, distance).where(Memory.user_id == user_id, Memory.status == "active")
        if memory_type:
            stmt = stmt.where(Memory.memory_type == memory_type)
        stmt = stmt.order_by(distance).limit(limit)
        return [(m, 1.0 - dist) for m, dist in self.session.execute(stmt).all()]

    # --- writes -------------------------------------------------------
    def create(
        self,
        user_id: str,
        memory_type: str,
        content: str,
        embedding: list[float] | None = None,
        importance: float = 0.5,
        source: str = "auto",
        source_message_ids: list[int] | None = None,
        owner_agent_id: str = DEFAULT_OWNER_AGENT_ID,
    ) -> Memory:
        """Insert one memory. `memory_type` must be 'episodic' or 'semantic'
        — episodic ones should carry `source_message_ids` so the memory can
        be traced back to the conversation it came from.
        `source`: 'auto' (extraction_graph) or 'explicit' (memory_remember).
        """
        m = Memory(
            user_id=user_id,
            owner_agent_id=owner_agent_id,
            memory_type=memory_type,
            content=content,
            embedding=embedding,
            importance=importance,
            source=source,
            source_message_ids=source_message_ids,
        )
        self.session.add(m)
        self.session.flush()
        self.session.refresh(m)
        return m

    def supersede(self, old_memory_id: int, new_memory: Memory) -> Memory | None:
        """Mark an existing memory 'superseded' by a newer one instead of
        deleting it, so retrieval can follow the chain to the current
        version rather than surfacing both and contradicting itself.
        Returns the old memory (now updated), or None if it doesn't exist.
        """
        old = self.session.get(Memory, old_memory_id)
        if old is None:
            return None
        old.status = "superseded"
        old.superseded_by = new_memory.id
        self.session.flush()
        return old

    def soft_delete(self, memory_id: int) -> bool:
        result = self.session.execute(
            update(Memory).where(Memory.id == memory_id).values(status="deleted")
        )
        return result.rowcount > 0

    def touch_access(self, memory_id: int) -> None:
        """Bump `access_count`/`last_accessed_at` on retrieval, so the
        maintenance job has real signal to re-weight importance.
        """
        self.session.execute(
            update(Memory)
            .where(Memory.id == memory_id)
            .values(
                access_count=Memory.access_count + 1,
                last_accessed_at=datetime.now(timezone.utc),
            )
        )

    def reweight(
        self,
        user_id: str,
        boost_access_count: int = 5,
        boost_amount: float = 0.05,
        decay_after_days: int = 30,
        decay_amount: float = 0.05,
    ) -> dict:
        """Daily maintenance: memories accessed a lot get a small
        importance boost (capped at 1.0); memories nobody has touched in a
        while get a small decay (floored at 0.0). Two independent bulk
        UPDATEs rather than a per-row Python loop — this runs over every
        active memory, so let Postgres do the arithmetic. A memory could in
        principle match both rules (accessed often, but not recently) and get
        both adjustments in the same run; that's fine, not mutually exclusive.
        """
        decay_cutoff = datetime.now(timezone.utc) - timedelta(days=decay_after_days)
        boosted = self.session.execute(
            update(Memory)
            .where(
                Memory.user_id == user_id,
                Memory.status == "active",
                Memory.access_count >= boost_access_count,
                Memory.importance < 1.0,
            )
            .values(importance=func.least(1.0, Memory.importance + boost_amount))
        ).rowcount
        decayed = self.session.execute(
            update(Memory)
            .where(
                Memory.user_id == user_id,
                Memory.status == "active",
                Memory.last_accessed_at < decay_cutoff,
                Memory.importance > 0.0,
            )
            .values(importance=func.greatest(0.0, Memory.importance - decay_amount))
        ).rowcount
        return {"boosted": boosted, "decayed": decayed}


# --- Album ------------------------------------------------------------------

def attachment_to_dict(a: Attachment) -> dict:
    """Raw fields only — no `url`/`thumbnail_url` here. Resolving a storage
    key to an actual URL needs the storage backend (local path vs. MinIO
    presigned link), which is an app.py/route concern, not this layer's.
    """
    return {
        "id": a.id,
        "storage_key": a.storage_key,
        "mime_type": a.mime_type,
        "size_bytes": a.size_bytes,
        "width": a.width,
        "height": a.height,
    }


class AttachmentRepository:
    def __init__(self, session: Session):
        self.session = session

    def create(
        self,
        user_id: str,
        storage_key: str,
        mime_type: str,
        size_bytes: int,
        width: int | None = None,
        height: int | None = None,
    ) -> Attachment:
        a = Attachment(
            user_id=user_id,
            storage_key=storage_key,
            mime_type=mime_type,
            size_bytes=size_bytes,
            width=width,
            height=height,
        )
        self.session.add(a)
        self.session.flush()
        self.session.refresh(a)
        return a

    def get_many(self, ids: list[int]) -> list[Attachment]:
        """Rows for `ids`, in the same order — an album entry's photos display
        in the order the caller (the entry's `attachment_ids`) specified.
        """
        if not ids:
            return []
        rows = {a.id: a for a in self.session.scalars(select(Attachment).where(Attachment.id.in_(ids)))}
        return [rows[i] for i in ids if i in rows]


def album_photo_dict(e: AlbumEntry, img_url: str | None) -> dict:
    """The shape `web/album.html`'s JS actually expects (its own
    self-documented contract, see the comment at the top of that file's
    <script> block) — flatter than the doc's schema: one photo per dict
    (`img`, the first attachment's resolved URL — this app only ever
    creates single-photo entries even though `attachment_ids` could hold
    more), `group`/`time` as free-text display fields, `views` a counter.
    `img_url` is the caller's job to resolve (needs the storage backend).
    """
    return {
        "id": e.id,
        "img": img_url,
        "caption": e.caption,
        "time": e.time_label,
        "group": e.group_name,
        "views": e.views,
    }


def _encode_album_cursor(sort_ts: datetime, entry_id: int) -> str:
    return f"{int(sort_ts.timestamp() * 1000)}:{entry_id}"


def _decode_album_cursor(cursor: str) -> tuple[datetime, int]:
    ts_ms, entry_id = cursor.split(":", 1)
    return datetime.fromtimestamp(int(ts_ms) / 1000, tz=timezone.utc), int(entry_id)


class AlbumRepository:
    """CRUD for `album_entries`. Timeline order is by when the moment
    happened (`taken_at`, falling back to `created_at` for photos with no
    claimed date) — not upload order — matching the index on
    `(user_id, taken_at)`.
    """

    def __init__(self, session: Session):
        self.session = session

    def create(
        self,
        user_id: str,
        attachment_ids: list[int],
        caption: str | None = None,
        location: str | None = None,
        taken_at: datetime | None = None,
        group_name: str | None = None,
        time_label: str | None = None,
    ) -> AlbumEntry:
        entry = AlbumEntry(
            user_id=user_id,
            caption=caption,
            attachment_ids=attachment_ids,
            location=location,
            taken_at=taken_at,
            group_name=group_name,
            time_label=time_label,
        )
        self.session.add(entry)
        self.session.flush()
        self.session.refresh(entry)
        return entry

    def get(self, entry_id: int) -> AlbumEntry | None:
        return self.session.get(AlbumEntry, entry_id)

    def random_entry(self, user_id: str) -> AlbumEntry | None:
        stmt = (
            select(AlbumEntry)
            .where(AlbumEntry.user_id == user_id)
            .order_by(func.random())
            .limit(1)
        )
        return self.session.scalars(stmt).first()

    def increment_views(self, entry_id: int) -> None:
        """Bumped on every `/photo/{id}` (and `/random`) hit — the frontend
        displays this back as "<peer> has viewed this N×".
        """
        self.session.execute(
            update(AlbumEntry).where(AlbumEntry.id == entry_id).values(views=AlbumEntry.views + 1)
        )

    def list_timeline(self, user_id: str, cursor: str | None, limit: int) -> list[AlbumEntry]:
        """Newest-moment-first page of `limit` entries. Pass a previous call's
        `next_cursor()` result to get the next page; omit for the first page.
        """
        sort_key = func.coalesce(AlbumEntry.taken_at, AlbumEntry.created_at)
        stmt = select(AlbumEntry).where(AlbumEntry.user_id == user_id)
        if cursor:
            cursor_ts, cursor_id = _decode_album_cursor(cursor)
            stmt = stmt.where(
                or_(sort_key < cursor_ts, and_(sort_key == cursor_ts, AlbumEntry.id < cursor_id))
            )
        stmt = stmt.order_by(sort_key.desc(), AlbumEntry.id.desc()).limit(limit)
        return list(self.session.scalars(stmt))

    def next_cursor(self, entries: list[AlbumEntry]) -> str | None:
        """Cursor for the page after `entries` (the last page `list_timeline`
        returned), or None once there's nothing further back in time.
        """
        if not entries:
            return None
        last = entries[-1]
        return _encode_album_cursor(last.taken_at or last.created_at, last.id)

