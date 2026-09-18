"""Object storage abstraction: local filesystem or MinIO
(S3-compatible), switchable via STORAGE_BACKEND=local|minio so swapping to a
real S3/R2 bucket later is just an env change, not a code change.

Local mirrors the existing chat-upload behavior (files under RELAY_UPLOAD_DIR,
served through the app's own /uploads/{name} route). MinIO stores the bytes
in a bucket and hands back a presigned GET URL — the actual image bytes never
round-trip through the FastAPI process, which is the point of using object
storage at all.
"""

import io
import mimetypes
import os
import re
from datetime import timedelta
from pathlib import Path
from typing import Protocol

from PIL import Image

THUMBNAIL_MAX_SIZE = (400, 400)
MAX_UPLOAD_BYTES = int(os.environ.get("RELAY_MAX_UPLOAD_BYTES", str(10 * 1024 * 1024)))

_SAFE_NAME_RE = re.compile(r"[^A-Za-z0-9_.-]+")


def clean_filename(name: str) -> str:
    name = Path(name or "file").name
    name = _SAFE_NAME_RE.sub("_", name).strip("._") or "file"
    return name[:80]


def ext_for(name: str, mime: str) -> str:
    ext = Path(name).suffix.lower()
    if ext and re.fullmatch(r"\.[A-Za-z0-9]{1,8}", ext):
        return ext
    guessed = mimetypes.guess_extension((mime or "").split(";", 1)[0].strip())
    return guessed or ".bin"


class StorageBackend(Protocol):
    def save(self, key: str, data: bytes, content_type: str) -> None: ...
    def url_for(self, key: str) -> str: ...
    def read(self, key: str) -> bytes: ...


class LocalStorageBackend:
    def __init__(self, upload_dir: Path, public_prefix: str):
        self._dir = upload_dir
        self._dir.mkdir(parents=True, exist_ok=True)
        self._public_prefix = public_prefix

    def save(self, key: str, data: bytes, content_type: str) -> None:
        (self._dir / key).write_bytes(data)

    def url_for(self, key: str) -> str:
        return f"{self._public_prefix}/uploads/{key}" if self._public_prefix else f"/uploads/{key}"

    def read(self, key: str) -> bytes:
        return (self._dir / key).read_bytes()


class MinIOStorageBackend:
    def __init__(
        self,
        endpoint: str,
        access_key: str,
        secret_key: str,
        bucket: str,
        secure: bool = False,
        url_expiry_days: int = 7,
        public_endpoint: str | None = None,
        region: str = "us-east-1",
    ):
        from minio import Minio  # imported lazily — only needed when this backend is actually selected

        # `region` pinned explicitly: without it, the SDK's first request
        # (including presigning!) does its own network call to auto-detect
        # the bucket's region. That's fine for `self._client` (endpoint is
        # always reachable from here) but fatal for `self._url_client` below,
        # which is deliberately built against a host — e.g. "localhost:9000"
        # — that's only reachable from OUTSIDE this process (the browser),
        # not from it. MinIO's single-region deployments all accept this
        # default; override via `region` if yours doesn't.
        self._client = Minio(endpoint, access_key=access_key, secret_key=secret_key, secure=secure, region=region)
        # AWS SigV4 signs the Host header, so a presigned URL is only valid for
        # the host it was signed with — can't just string-replace it after the
        # fact. In docker-compose, `endpoint` is the internal service name
        # ("minio:9000", only reachable from other containers) but the
        # presigned URL has to be reachable from the user's browser/phone, so
        # when they differ we sign with a second client built for the public
        # endpoint instead (presigning is otherwise a local HMAC computation,
        # no network call, so a second client just for that is cheap).
        self._url_client = (
            self._client
            if not public_endpoint or public_endpoint == endpoint
            else Minio(public_endpoint, access_key=access_key, secret_key=secret_key, secure=secure, region=region)
        )
        self._bucket = bucket
        self._url_expiry = timedelta(days=url_expiry_days)
        if not self._client.bucket_exists(bucket):
            self._client.make_bucket(bucket)

    def save(self, key: str, data: bytes, content_type: str) -> None:
        self._client.put_object(self._bucket, key, io.BytesIO(data), length=len(data), content_type=content_type)

    def url_for(self, key: str) -> str:
        # Presigned, not a public-bucket policy — the bytes are only reachable
        # with a time-limited signed link, same trust model as everything else
        # behind this relay's Bearer auth, just handed off to MinIO directly.
        return self._url_client.presigned_get_object(self._bucket, key, expires=self._url_expiry)

    def read(self, key: str) -> bytes:
        resp = self._client.get_object(self._bucket, key)
        try:
            return resp.read()
        finally:
            resp.close()
            resp.release_conn()


_backend: StorageBackend | None = None


def get_storage_backend() -> StorageBackend:
    global _backend
    if _backend is None:
        choice = os.environ.get("STORAGE_BACKEND", "local")
        if choice == "minio":
            endpoint = os.environ.get("MINIO_ENDPOINT", "localhost:9000")
            _backend = MinIOStorageBackend(
                endpoint=endpoint,
                access_key=os.environ.get("MINIO_ACCESS_KEY", "rbtl_minio_admin"),
                secret_key=os.environ.get("MINIO_SECRET_KEY", "rbtl_minio_password"),
                bucket=os.environ.get("MINIO_BUCKET", "rbtl-album"),
                secure=os.environ.get("MINIO_SECURE", "false").lower() == "true",
                # Falls back to MINIO_ENDPOINT when unset — fine when the app
                # isn't running behind docker-compose's internal network.
                public_endpoint=os.environ.get("MINIO_PUBLIC_ENDPOINT", endpoint),
                region=os.environ.get("MINIO_REGION", "us-east-1"),
            )
        else:
            upload_dir = Path(os.environ.get("RELAY_UPLOAD_DIR", str(Path(__file__).parent / "uploads")))
            _backend = LocalStorageBackend(upload_dir, os.environ.get("RELAY_PUBLIC_PREFIX", ""))
    return _backend


def make_thumbnail(data: bytes, max_size: tuple[int, int] = THUMBNAIL_MAX_SIZE) -> bytes:
    """Downscale to fit within `max_size` (aspect ratio preserved) and
    re-encode as JPEG — the list/timeline view only ever needs a small
    preview, not the original multi-MB photo.
    """
    image = Image.open(io.BytesIO(data))
    if image.mode not in ("RGB", "L"):
        image = image.convert("RGB")
    image.thumbnail(max_size)
    out = io.BytesIO()
    image.save(out, format="JPEG", quality=82)
    return out.getvalue()


def thumbnail_key_for(key: str) -> str:
    """Deterministic name for a key's thumbnail — avoids a DB column just to
    remember it: `att-abc123.jpg` -> `att-abc123-thumb.jpg`.
    """
    path = Path(key)
    return f"{path.stem}-thumb.jpg"
