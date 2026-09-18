"""Tests for the storage abstraction. `LocalStorageBackend`
and `make_thumbnail`/`thumbnail_key_for` are pure/filesystem-only — no
network, no MinIO needed. `MinIOStorageBackend` isn't unit-tested here (it's
a thin wrapper over the `minio` SDK with no logic of its own beyond the
public/internal-endpoint split, which needs a running MinIO to exercise
meaningfully).
"""

import io

import pytest
from PIL import Image

from storage import LocalStorageBackend, make_thumbnail, thumbnail_key_for


def _make_test_image(size=(800, 600), mode="RGB", color=(200, 100, 50)) -> bytes:
    img = Image.new(mode, size, color)
    buf = io.BytesIO()
    img.save(buf, format="PNG")
    return buf.getvalue()


# --- thumbnail_key_for --------------------------------------------------

def test_thumbnail_key_for_appends_thumb_suffix():
    assert thumbnail_key_for("att-abc123.jpg") == "att-abc123-thumb.jpg"


def test_thumbnail_key_for_replaces_original_extension_with_jpg():
    assert thumbnail_key_for("att-abc123.png") == "att-abc123-thumb.jpg"


# --- make_thumbnail -------------------------------------------------------

def test_make_thumbnail_shrinks_to_fit_max_size():
    data = _make_test_image(size=(800, 600))
    thumb = make_thumbnail(data, max_size=(400, 400))
    img = Image.open(io.BytesIO(thumb))
    assert img.width <= 400 and img.height <= 400


def test_make_thumbnail_preserves_aspect_ratio():
    data = _make_test_image(size=(800, 400))  # 2:1
    thumb = make_thumbnail(data, max_size=(400, 400))
    img = Image.open(io.BytesIO(thumb))
    assert abs(img.width / img.height - 2.0) < 0.02


def test_make_thumbnail_never_upscales_a_smaller_image():
    data = _make_test_image(size=(100, 80))
    thumb = make_thumbnail(data, max_size=(400, 400))
    img = Image.open(io.BytesIO(thumb))
    assert img.width == 100 and img.height == 80


def test_make_thumbnail_outputs_jpeg():
    data = _make_test_image()
    thumb = make_thumbnail(data)
    assert Image.open(io.BytesIO(thumb)).format == "JPEG"


def test_make_thumbnail_converts_rgba_to_rgb_for_jpeg():
    data = _make_test_image(mode="RGBA", color=(200, 100, 50, 255))
    thumb = make_thumbnail(data)  # would raise if RGBA weren't converted before JPEG save
    assert Image.open(io.BytesIO(thumb)).mode == "RGB"


# --- LocalStorageBackend ---------------------------------------------------

def test_local_backend_save_and_read_round_trip(tmp_path):
    backend = LocalStorageBackend(tmp_path, public_prefix="")
    backend.save("att-1.jpg", b"hello world", "image/jpeg")
    assert backend.read("att-1.jpg") == b"hello world"


def test_local_backend_url_without_prefix(tmp_path):
    backend = LocalStorageBackend(tmp_path, public_prefix="")
    assert backend.url_for("att-1.jpg") == "/uploads/att-1.jpg"


def test_local_backend_url_with_prefix(tmp_path):
    backend = LocalStorageBackend(tmp_path, public_prefix="/relay")
    assert backend.url_for("att-1.jpg") == "/relay/uploads/att-1.jpg"


def test_local_backend_creates_upload_dir_if_missing(tmp_path):
    target = tmp_path / "not-yet-created"
    LocalStorageBackend(target, public_prefix="")
    assert target.is_dir()
