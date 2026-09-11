from datetime import datetime
from uuid import UUID

from sqlalchemy import BigInteger, Integer, Boolean, DateTime, String, Text, func, text as sql_text, Index
from sqlalchemy.dialects.postgresql import JSONB, UUID as PGUUID
from sqlalchemy.orm import Mapped, mapped_column

from database import Base


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