"""Embedding provider abstraction — a switchable-provider embedding service wrapper.

    EmbeddingProvider
    ├── OpenAIEmbeddingProvider       → OpenAI's embeddings API
    ├── GeminiEmbeddingProvider       → Google's Gemini embeddings API
    ├── LocalEmbeddingProvider        → a local model (BGE / Qwen / MiniLM /
    │                                   ...) via sentence-transformers, no
    │                                   network call and no API key
    └── LocalHashEmbeddingProvider    → deterministic, dependency-free
                                        stand-in with no semantic meaning —
                                        dev/test only

`OpenAIEmbeddingProvider` and `GeminiEmbeddingProvider` call their HTTP APIs
directly (httpx), the same "plain HTTP client, no vendor SDK" pattern app.py
already uses for MiniMax TTS. `LocalEmbeddingProvider` is the one exception —
running a model locally needs the actual model-serving library, not just an
HTTP client — so it lazy-imports `sentence-transformers` and only requires it
installed if you actually select this provider.

Whichever provider you pick must produce vectors of `models.EMBEDDING_DIM`
(the pgvector column is a fixed size): pick a model/config that matches, or
change `EMBEDDING_DIM` and run a migration if you switch to one that doesn't.

Select via EMBEDDING_PROVIDER=openai|gemini|local|local_hash; defaults to
'openai' if OPENAI_API_KEY is set, else 'gemini' if GEMINI_API_KEY is set,
else 'local_hash' so a fresh clone with no keys and no extra deps installed
still boots cleanly.
"""

import hashlib
import os
from typing import Protocol

import httpx

from models import EMBEDDING_DIM

OPENAI_API_KEY = os.environ.get("OPENAI_API_KEY", "")
OPENAI_API_BASE = os.environ.get("OPENAI_API_BASE", "https://api.openai.com/v1")
OPENAI_EMBEDDING_MODEL = os.environ.get("OPENAI_EMBEDDING_MODEL", "text-embedding-3-small")

GEMINI_API_KEY = os.environ.get("GEMINI_API_KEY", "")
GEMINI_API_BASE = os.environ.get("GEMINI_API_BASE", "https://generativelanguage.googleapis.com/v1beta")
GEMINI_EMBEDDING_MODEL = os.environ.get("GEMINI_EMBEDDING_MODEL", "gemini-embedding-001")

# BGE-small's native dim is 384, not EMBEDDING_DIM's default 1536 — this default
# is a reasonable starting model, not a dimension match; swap it (and/or
# EMBEDDING_DIM) for a model whose output dimension actually matches.
LOCAL_EMBEDDING_MODEL = os.environ.get("LOCAL_EMBEDDING_MODEL", "BAAI/bge-small-en-v1.5")


class EmbeddingProvider(Protocol):
    def embed(self, texts: list[str]) -> list[list[float]]: ...


class OpenAIEmbeddingProvider:
    def __init__(
        self,
        api_key: str = OPENAI_API_KEY,
        model: str = OPENAI_EMBEDDING_MODEL,
        base_url: str = OPENAI_API_BASE,
        timeout: float = 30.0,
    ):
        if not api_key:
            raise RuntimeError("OPENAI_API_KEY not set")
        self._api_key = api_key
        self._model = model
        self._base_url = base_url.rstrip("/")
        self._timeout = timeout

    def embed(self, texts: list[str]) -> list[list[float]]:
        if not texts:
            return []
        resp = httpx.post(
            f"{self._base_url}/embeddings",
            headers={
                "Authorization": f"Bearer {self._api_key}",
                "Content-Type": "application/json",
            },
            json={"model": self._model, "input": texts},
            timeout=self._timeout,
        )
        resp.raise_for_status()
        rows = resp.json()["data"]
        rows.sort(key=lambda row: row["index"])  # API preserves order; sort defensively
        return [row["embedding"] for row in rows]


class GeminiEmbeddingProvider:
    def __init__(
        self,
        api_key: str = GEMINI_API_KEY,
        model: str = GEMINI_EMBEDDING_MODEL,
        base_url: str = GEMINI_API_BASE,
        dim: int = EMBEDDING_DIM,
        timeout: float = 30.0,
    ):
        if not api_key:
            raise RuntimeError("GEMINI_API_KEY not set")
        self._api_key = api_key
        self._model = model
        self._base_url = base_url.rstrip("/")
        self._dim = dim
        self._timeout = timeout

    def embed(self, texts: list[str]) -> list[list[float]]:
        if not texts:
            return []
        resp = httpx.post(
            f"{self._base_url}/models/{self._model}:batchEmbedContents",
            params={"key": self._api_key},
            headers={"Content-Type": "application/json"},
            json={
                "requests": [
                    {
                        "model": f"models/{self._model}",
                        "content": {"parts": [{"text": t}]},
                        "outputDimensionality": self._dim,
                    }
                    for t in texts
                ]
            },
            timeout=self._timeout,
        )
        resp.raise_for_status()
        return [item["values"] for item in resp.json()["embeddings"]]


class LocalEmbeddingProvider:
    """Runs a local sentence-embedding model (BGE / Qwen / MiniLM / ...) via
    `sentence-transformers` — real semantic embeddings with no network call
    and no API key, at the cost of a heavier dependency (torch) and a slower
    first call while the model downloads and loads into memory.
    """

    def __init__(self, model_name: str = LOCAL_EMBEDDING_MODEL):
        try:
            from sentence_transformers import SentenceTransformer
        except ImportError as exc:
            raise RuntimeError(
                "LocalEmbeddingProvider needs the 'sentence-transformers' package — "
                "pip install sentence-transformers (see requirements-dev.txt)."
            ) from exc
        self._model = SentenceTransformer(model_name)

    def embed(self, texts: list[str]) -> list[list[float]]:
        if not texts:
            return []
        vectors = self._model.encode(texts, normalize_embeddings=True)
        return [vector.tolist() for vector in vectors]


class LocalHashEmbeddingProvider:
    """Deterministic pseudo-embedding for dev/test. Never use in production —
    vectors carry no semantic meaning, so similarity search over them is
    meaningless. It only exists so the dedup/extraction pipeline is exercisable
    (including its cosine-similarity logic) with zero external dependencies.
    """

    def __init__(self, dim: int = EMBEDDING_DIM):
        self._dim = dim

    def embed(self, texts: list[str]) -> list[list[float]]:
        return [self._embed_one(t) for t in texts]

    def _embed_one(self, text: str) -> list[float]:
        digest = hashlib.sha256(text.encode("utf-8")).digest()
        vec = [(digest[i % len(digest)] - 128) / 128.0 for i in range(self._dim)]
        norm = sum(v * v for v in vec) ** 0.5 or 1.0
        return [v / norm for v in vec]


_PROVIDERS = {
    "openai": OpenAIEmbeddingProvider,
    "gemini": GeminiEmbeddingProvider,
    "local": LocalEmbeddingProvider,
    "local_hash": LocalHashEmbeddingProvider,
}

_provider: EmbeddingProvider | None = None


def get_embedding_provider() -> EmbeddingProvider:
    global _provider
    if _provider is None:
        choice = os.environ.get("EMBEDDING_PROVIDER") or _default_provider_choice()
        try:
            factory = _PROVIDERS[choice]
        except KeyError:
            raise RuntimeError(f"Unknown EMBEDDING_PROVIDER {choice!r} — choose one of {sorted(_PROVIDERS)}")
        _provider = factory()
    return _provider


def _default_provider_choice() -> str:
    if OPENAI_API_KEY:
        return "openai"
    if GEMINI_API_KEY:
        return "gemini"
    return "local_hash"


def embed_texts(texts: list[str]) -> list[list[float]]:
    return get_embedding_provider().embed(texts)
