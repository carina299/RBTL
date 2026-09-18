"""Unit tests for the dev/test embedding provider — no network, no API key.
`OpenAIEmbeddingProvider` isn't tested here since it's a thin HTTP call with
no logic of its own to verify without hitting the real API.
"""

import math

from memory.embeddings import EMBEDDING_DIM, LocalHashEmbeddingProvider


def test_same_text_yields_same_vector():
    provider = LocalHashEmbeddingProvider()
    a = provider.embed(["hello world"])[0]
    b = provider.embed(["hello world"])[0]
    assert a == b


def test_different_text_yields_different_vector():
    provider = LocalHashEmbeddingProvider()
    a = provider.embed(["hello world"])[0]
    b = provider.embed(["goodbye world"])[0]
    assert a != b


def test_vector_has_configured_dimension():
    provider = LocalHashEmbeddingProvider()
    vec = provider.embed(["x"])[0]
    assert len(vec) == EMBEDDING_DIM


def test_vector_is_unit_normalized():
    provider = LocalHashEmbeddingProvider()
    vec = provider.embed(["some text"])[0]
    norm = math.sqrt(sum(v * v for v in vec))
    assert math.isclose(norm, 1.0, abs_tol=1e-6)


def test_embed_empty_list_returns_empty_list():
    provider = LocalHashEmbeddingProvider()
    assert provider.embed([]) == []


def test_embed_batches_multiple_texts_independently():
    provider = LocalHashEmbeddingProvider()
    vecs = provider.embed(["a", "b", "c"])
    assert len(vecs) == 3
    assert vecs[0] != vecs[1] != vecs[2]
