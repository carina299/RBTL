from datetime import datetime
from uuid import UUID

from pgvector.sqlalchemy import Vector
from sqlalchemy import (
    ARRAY, BigInteger, Boolean, DateTime, Float, ForeignKey, Index, Integer,
    String, Text, func, text as sql_text,
)
from sqlalchemy.dialects.postgresql import JSONB, UUID as PGUUID
from sqlalchemy.orm import Mapped, mapped_column

from database import Base

# Embedding dimension for the memory pipeline. Fixed at the model
# layer because the pgvector column and its ivfflat index are both sized to
# it; changing providers to a different dimension needs a migration either way.
EMBEDDING_DIM = 1536


class Message(Base):
    __tablename__ = "messages"

    __table_args__ = (
    Index("idx_messages_created_at", "created_at"),
    Index("idx_messages_user_id", "user_id"),
    Index("idx_messages_api_session", "api_session"),
)

    id: Mapped[int] = mapped_column(
        BigInteger,
        primary_key=True
    )

    user_id: Mapped[UUID] = mapped_column(
        PGUUID(as_uuid=True),
        nullable=False,
        server_default=sql_text("'00000000-0000-0000-0000-000000000001'::uuid")
    )

    from_role: Mapped[str] = mapped_column(
        String(8),
        nullable=False
    )

    kind: Mapped[str] = mapped_column(
        String(16),
        nullable=False
    )

    # which server-side API-loop session this message belongs to; NULL == the
    # "legacy" desktop Claude Code session. The PWA filters history by this.
    api_session: Mapped[str | None] = mapped_column(
        String(64),
        nullable=True
    )

    text: Mapped[str | None] = mapped_column(
        Text,
        nullable=True
    )

    attachments: Mapped[list] = mapped_column(
        JSONB,
        server_default=sql_text("'[]'::jsonb")
    )

    reactions: Mapped[dict] = mapped_column(
        JSONB,
        server_default=sql_text("'{}'::jsonb")
    )

    # Catch-all for the long tail of message metadata that isn't worth its own
    # column: glyph/steps/act (AI render hints), call_id, source, voice,
    # transcribed, chat_id, reply_to, stream_id, user, ... The /channel/out
    # endpoint accepts arbitrary keys, so this stays open-ended.
    meta: Mapped[dict] = mapped_column(
        JSONB,
        nullable=False,
        server_default=sql_text("'{}'::jsonb")
    )

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now()
    )


class Attachment(Base):
    __tablename__ = "attachments"

    id: Mapped[int] = mapped_column(
        BigInteger,
        primary_key=True
    )

    user_id: Mapped[UUID] = mapped_column(
        PGUUID(as_uuid=True),
        nullable=False
    )

    storage_key: Mapped[str] = mapped_column(
        Text,
        nullable=False
    )

    mime_type: Mapped[str | None] = mapped_column(
        String(64),
        nullable=True
    )

    size_bytes: Mapped[int | None] = mapped_column(
        BigInteger,
        nullable=True
    )

    width: Mapped[int | None] = mapped_column(
        Integer,
        nullable=True
    )

    height: Mapped[int | None] = mapped_column(
        Integer,
        nullable=True
    )

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now()
    )


class Session(Base):
    __tablename__ = "sessions"

    id: Mapped[UUID] = mapped_column(
        PGUUID(as_uuid=True),
        primary_key=True,
        server_default=sql_text("gen_random_uuid()")
    )

    user_id: Mapped[UUID] = mapped_column(
        PGUUID(as_uuid=True),
        nullable=False
    )

    last_ping_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True),
        nullable=True
    )

    is_online: Mapped[bool] = mapped_column(
        Boolean,
        nullable=False,
        server_default=sql_text("false")
    )


class Memory(Base):
    """Long-term memory for the companion agent.

    Working memory isn't a table — it's just the most recent `messages` rows,
    queried directly. This table holds the two tiers that need to survive and
    be searched across sessions:
      - episodic (`memory_type="episodic"`): a specific dated event, traceable
        back to the messages that produced it via `source_message_ids`.
      - semantic (`memory_type="semantic"`): a distilled standing fact about
        the user. When a new semantic memory contradicts an old one, the old
        row is marked `status="superseded"` (not deleted) and `superseded_by`
        points at its replacement, so retrieval can follow the chain instead
        of returning both and confusing the agent.
    `owner_agent_id` is reserved for a future multi-agent setup; this phase
    only ever writes 'companion'.
    """

    __tablename__ = "memories"

    __table_args__ = (
        Index("idx_memories_user_status", "user_id", "status"),
        Index(
            "idx_memories_embedding",
            "embedding",
            postgresql_using="ivfflat",
            postgresql_with={"lists": 100},
            postgresql_ops={"embedding": "vector_cosine_ops"},
        ),
    )

    id: Mapped[int] = mapped_column(
        BigInteger,
        primary_key=True
    )

    user_id: Mapped[UUID] = mapped_column(
        PGUUID(as_uuid=True),
        nullable=False
    )

    owner_agent_id: Mapped[str] = mapped_column(
        String(64),
        nullable=False,
        server_default=sql_text("'companion'")
    )

    memory_type: Mapped[str] = mapped_column(
        String(16),
        nullable=False
    )

    content: Mapped[str] = mapped_column(
        Text,
        nullable=False
    )

    embedding: Mapped[list[float] | None] = mapped_column(
        Vector(EMBEDDING_DIM),
        nullable=True
    )

    importance: Mapped[float] = mapped_column(
        Float,
        nullable=False,
        server_default=sql_text("0.5")
    )

    # 'auto' = background extraction_graph · 'explicit' = Agent called
    # memory_remember mid-conversation. Kept separate from source_message_ids
    # so effectiveness of the two write paths can be compared later.
    source: Mapped[str] = mapped_column(
        String(16),
        nullable=False,
        server_default=sql_text("'auto'")
    )

    source_message_ids: Mapped[list[int] | None] = mapped_column(
        ARRAY(BigInteger),
        nullable=True
    )

    status: Mapped[str] = mapped_column(
        String(16),
        nullable=False,
        server_default=sql_text("'active'")
    )

    superseded_by: Mapped[int | None] = mapped_column(
        BigInteger,
        ForeignKey("memories.id"),
        nullable=True
    )

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now()
    )

    last_accessed_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now()
    )

    access_count: Mapped[int] = mapped_column(
        Integer,
        nullable=False,
        server_default=sql_text("0")
    )


class AlbumEntry(Base):
    """One "memory" post in the album — a caption plus one or more photos,
    timestamped by when the moment actually happened (`taken_at`), not when
    it was uploaded (`created_at`).

    `group_name`, `time_label`, `views`, `notes` aren't in the original schema —
    they exist because `web/album.html` (the actual frontend shell already
    built, not just a wireframe) has its own self-documented API contract
    that's flatter than the doc's and includes features the doc never
    mentioned: a free-text "group" tag for loose albums, a `views` counter,
    and `notes_list` (annotations the AI companion can leave on a photo).
    `time_label` holds the user's raw typed date string ("2026-06-25") for
    verbatim display — `taken_at` (parsed best-effort from it) is what
    actually drives sort order.
    """

    __tablename__ = "album_entries"

    __table_args__ = (
        Index("idx_album_user_taken_at", "user_id", "taken_at"),
    )

    id: Mapped[int] = mapped_column(
        BigInteger,
        primary_key=True
    )

    user_id: Mapped[UUID] = mapped_column(
        PGUUID(as_uuid=True),
        nullable=False
    )

    caption: Mapped[str | None] = mapped_column(
        Text,
        nullable=True
    )

    attachment_ids: Mapped[list[int]] = mapped_column(
        ARRAY(BigInteger),
        nullable=False
    )

    location: Mapped[str | None] = mapped_column(
        String(128),
        nullable=True
    )

    taken_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True),
        nullable=True
    )

    group_name: Mapped[str | None] = mapped_column(
        String(128),
        nullable=True
    )

    time_label: Mapped[str | None] = mapped_column(
        String(64),
        nullable=True
    )

    views: Mapped[int] = mapped_column(
        Integer,
        nullable=False,
        server_default=sql_text("0")
    )

    notes: Mapped[list] = mapped_column(
        JSONB,
        nullable=False,
        server_default=sql_text("'[]'::jsonb")
    )

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now()
    )