"""Tests for `album.create_album_photo` — the write path shared by the HTTP
upload route (app.py) and the `album_upload_photo` MCP tool (mcp_server.py).
Uses a fake in-memory `StorageBackend` so these don't need MinIO or a real
filesystem; still needs Postgres for the `AttachmentRepository`/
`AlbumRepository` writes, like the rest of `test_album_repository.py`.
"""

import io

import pytest
from PIL import Image

from album import create_album_photo


class _FakeBackend:
    def __init__(self):
        self.saved: dict[str, tuple[bytes, str]] = {}

    def save(self, key: str, data: bytes, content_type: str) -> None:
        self.saved[key] = (data, content_type)

    def url_for(self, key: str) -> str:
        return f"/fake/{key}"

    def read(self, key: str) -> bytes:
        return self.saved[key][0]


def _make_test_image() -> bytes:
    img = Image.new("RGB", (800, 600), (200, 100, 50))
    buf = io.BytesIO()
    img.save(buf, format="PNG")
    return buf.getvalue()


def test_create_album_photo_saves_bytes_and_creates_entry(db_session, test_user_id):
    backend = _FakeBackend()
    data = _make_test_image()
    entry = create_album_photo(
        db_session, test_user_id, data, "photo.png", "image/png",
        caption="a day out", time_label="2026-06-25", group_name="Trips",
        backend=backend,
    )
    assert entry.caption == "a day out"
    assert entry.time_label == "2026-06-25"
    assert entry.group_name == "Trips"
    assert entry.taken_at is not None  # parsed from time_label
    assert len(backend.saved) == 2  # original + thumbnail
    saved_data, saved_mime = next(iter(backend.saved.values()))
    assert saved_mime == "image/png"


def test_create_album_photo_rejects_oversized_file(db_session, test_user_id, monkeypatch):
    monkeypatch.setattr("album.MAX_UPLOAD_BYTES", 10)
    with pytest.raises(ValueError):
        create_album_photo(
            db_session, test_user_id, b"x" * 100, "photo.png", "image/png",
            backend=_FakeBackend(),
        )


def test_create_album_photo_thumbnail_failure_does_not_block_upload(db_session, test_user_id):
    backend = _FakeBackend()
    entry = create_album_photo(
        db_session, test_user_id, b"not a real image", "photo.jpg", "image/jpeg",
        backend=backend,
    )
    assert entry.id is not None
    assert len(backend.saved) == 1  # only the original — thumbnailing failed silently


def test_create_album_photo_strips_blank_optional_fields_to_none(db_session, test_user_id):
    entry = create_album_photo(
        db_session, test_user_id, b"data", "photo.bin", "application/octet-stream",
        caption="  ", time_label="", group_name=None,
        backend=_FakeBackend(),
    )
    assert entry.caption is None
    assert entry.time_label is None
    assert entry.group_name is None
    assert entry.taken_at is None
