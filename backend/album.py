"""Album write path: turns raw photo bytes into a stored attachment + album
entry (thumbnailing + two DB writes). Reused by both the HTTP upload route
(app.py's `/app/album/upload`) and the agent-callable `album_upload_photo`
MCP tool (mcp_server.py), so the two entry points can't drift out of sync.
"""

import secrets

from sqlalchemy.orm import Session

from repositories import AlbumRepository, AttachmentRepository, parse_ts
from storage import (
    MAX_UPLOAD_BYTES,
    StorageBackend,
    clean_filename,
    ext_for,
    get_storage_backend,
    make_thumbnail,
    thumbnail_key_for,
)


def create_album_photo(
    session: Session,
    user_id: str,
    data: bytes,
    filename: str,
    mime: str,
    caption: str | None = None,
    time_label: str | None = None,
    group_name: str | None = None,
    backend: StorageBackend | None = None,
):
    """Save one photo to object storage (+ a best-effort thumbnail) and
    record it as a new `album_entries` row. Raises `ValueError` if `data`
    exceeds `MAX_UPLOAD_BYTES`.
    """
    if len(data) > MAX_UPLOAD_BYTES:
        raise ValueError("file too large")
    backend = backend or get_storage_backend()
    safe = clean_filename(filename)
    ext = ext_for(safe, mime)
    key = f"album-{secrets.token_urlsafe(10)}{ext}"
    mime = mime or "application/octet-stream"

    backend.save(key, data, mime)
    if mime.startswith("image/"):
        try:
            backend.save(thumbnail_key_for(key), make_thumbnail(data), "image/jpeg")
        except Exception:
            pass  # thumbnail is a nice-to-have; the full image still uploaded fine

    caption = (caption or "").strip() or None
    time_label = (time_label or "").strip() or None
    group_name = (group_name or "").strip() or None
    # Best-effort: "2026-06-25" parses fine (ISO date), free-form text won't —
    # either way `time_label` still gets displayed back to the user verbatim,
    # this only affects timeline sort order.
    taken_at = parse_ts(time_label) if time_label else None

    att = AttachmentRepository(session).create(user_id, key, mime, len(data))
    return AlbumRepository(session).create(
        user_id,
        [att.id],
        caption=caption,
        taken_at=taken_at,
        group_name=group_name,
        time_label=time_label,
    )
