"""LLM-based memory candidate extraction.

    ChatProvider
    ├── AnthropicChatProvider        → Anthropic Messages API
    ├── OpenAIChatProvider           → OpenAI's chat-completions API
    ├── GeminiChatProvider           → Google's Gemini generateContent API
    └── OpenAICompatibleChatProvider → any endpoint that speaks the same
                                        chat-completions wire format as
                                        OpenAI (Ollama, vLLM, OpenRouter,
                                        together.ai, ...) at a different
                                        base URL

Each calls its HTTP API directly (httpx) — same "plain HTTP, no vendor SDK"
pattern as embeddings.py and the existing MiniMax TTS call in app.py — so the
only new requirement per provider is an API key/base URL, not a client
library.

Select via LLM_PROVIDER=anthropic|openai|gemini|openai_compatible; defaults
to 'anthropic' if ANTHROPIC_API_KEY is set, else 'openai' if OPENAI_API_KEY
is set, else 'gemini' if GEMINI_API_KEY is set, else 'openai_compatible' if
OPENAI_COMPATIBLE_API_BASE is set, else 'anthropic' (so a fresh clone with no
keys still raises the same clear "not set" error it always did).
"""

import json
import os
import re
from typing import Protocol

import httpx

ANTHROPIC_API_KEY = os.environ.get("ANTHROPIC_API_KEY", "")
ANTHROPIC_API_BASE = os.environ.get("ANTHROPIC_API_BASE", "https://api.anthropic.com/v1")
ANTHROPIC_VERSION = "2023-06-01"
# Haiku: extraction is a cheap, high-volume background task, not the
# user-facing companion persona — no need for a bigger model here.
ANTHROPIC_MODEL = os.environ.get("MEMORY_EXTRACTION_MODEL", "claude-haiku-4-5-20251001")

OPENAI_API_KEY = os.environ.get("OPENAI_API_KEY", "")
OPENAI_API_BASE = os.environ.get("OPENAI_API_BASE", "https://api.openai.com/v1")
OPENAI_MODEL = os.environ.get("MEMORY_EXTRACTION_OPENAI_MODEL", "gpt-4o-mini")

GEMINI_API_KEY = os.environ.get("GEMINI_API_KEY", "")
GEMINI_API_BASE = os.environ.get("GEMINI_API_BASE", "https://generativelanguage.googleapis.com/v1beta")
GEMINI_MODEL = os.environ.get("MEMORY_EXTRACTION_GEMINI_MODEL", "gemini-2.5-flash")

# Any self-hosted or third-party endpoint that speaks OpenAI's chat-completions
# wire format — separate credentials from OPENAI_API_KEY/OPENAI_API_BASE above
# so the two can be configured independently (e.g. real OpenAI for one thing,
# a local Ollama for this one).
OPENAI_COMPATIBLE_API_KEY = os.environ.get("OPENAI_COMPATIBLE_API_KEY", "")
OPENAI_COMPATIBLE_API_BASE = os.environ.get("OPENAI_COMPATIBLE_API_BASE", "")
OPENAI_COMPATIBLE_MODEL = os.environ.get("OPENAI_COMPATIBLE_MODEL", "")

_SYSTEM_PROMPT = """\
You extract long-term memories from a conversation log. Carefully read the
dialogue below between a human and their AI companion, and pull out anything
worth remembering long-term, sorted into two kinds:

- episodic: a specific, dated/event-like fact, e.g. "the user mentioned a
  business trip to Tokyo next week"
- semantic: an abstracted, relatively stable fact, e.g. "the user has a cat
  named Mochi"

Ignore small talk, repeated confirmations, and pure emotional expression —
none of that is worth remembering long-term. If nothing in this dialogue is
worth keeping, return an empty array.

Output ONLY a JSON array, with no other text, explanation, or markdown code
fences. Each item looks like:

{"content": "<a third-person statement of the memory, no quote marks around the original wording>", "type": "episodic" | "semantic", "importance": <float 0.0-1.0, higher = more important>}
"""


class ChatProvider(Protocol):
    def complete(self, system: str, user: str, max_tokens: int, timeout: float) -> str: ...


class AnthropicChatProvider:
    def __init__(
        self,
        api_key: str = ANTHROPIC_API_KEY,
        model: str = ANTHROPIC_MODEL,
        base_url: str = ANTHROPIC_API_BASE,
    ):
        if not api_key:
            raise RuntimeError("ANTHROPIC_API_KEY not set")
        self._api_key = api_key
        self._model = model
        self._base_url = base_url.rstrip("/")

    def complete(self, system: str, user: str, max_tokens: int = 1024, timeout: float = 30.0) -> str:
        resp = httpx.post(
            f"{self._base_url}/messages",
            headers={
                "x-api-key": self._api_key,
                "anthropic-version": ANTHROPIC_VERSION,
                "Content-Type": "application/json",
            },
            json={
                "model": self._model,
                "max_tokens": max_tokens,
                "system": system,
                "messages": [{"role": "user", "content": user}],
            },
            timeout=timeout,
        )
        resp.raise_for_status()
        body = resp.json()
        return "".join(
            block.get("text", "") for block in body.get("content", []) if block.get("type") == "text"
        )


class OpenAIChatProvider:
    """Chat-completions call for OpenAI itself. `OpenAICompatibleChatProvider`
    below is the identical wire format pointed at a different base URL with
    its own env vars, not a separate implementation.
    """

    def __init__(
        self,
        api_key: str = OPENAI_API_KEY,
        model: str = OPENAI_MODEL,
        base_url: str = OPENAI_API_BASE,
    ):
        if not api_key:
            raise RuntimeError("OPENAI_API_KEY not set")
        if not base_url:
            raise RuntimeError("OpenAI-compatible base URL not set")
        self._api_key = api_key
        self._model = model
        self._base_url = base_url.rstrip("/")

    def complete(self, system: str, user: str, max_tokens: int = 1024, timeout: float = 30.0) -> str:
        resp = httpx.post(
            f"{self._base_url}/chat/completions",
            headers={
                "Authorization": f"Bearer {self._api_key}",
                "Content-Type": "application/json",
            },
            json={
                "model": self._model,
                "max_tokens": max_tokens,
                "messages": [
                    {"role": "system", "content": system},
                    {"role": "user", "content": user},
                ],
            },
            timeout=timeout,
        )
        resp.raise_for_status()
        return resp.json()["choices"][0]["message"]["content"] or ""


class OpenAICompatibleChatProvider(OpenAIChatProvider):
    """Any self-hosted or third-party endpoint that speaks the same
    chat-completions wire format as OpenAI (Ollama, vLLM, OpenRouter,
    together.ai, ...) — reuses `OpenAIChatProvider.complete` as-is, just
    constructed from its own env vars so it never collides with real
    OPENAI_API_KEY/OPENAI_API_BASE.
    """

    def __init__(
        self,
        api_key: str = OPENAI_COMPATIBLE_API_KEY,
        model: str = OPENAI_COMPATIBLE_MODEL,
        base_url: str = OPENAI_COMPATIBLE_API_BASE,
    ):
        if not base_url:
            raise RuntimeError("OPENAI_COMPATIBLE_API_BASE not set")
        self._api_key = api_key  # many self-hosted servers ignore this entirely
        self._model = model
        self._base_url = base_url.rstrip("/")


class GeminiChatProvider:
    def __init__(
        self,
        api_key: str = GEMINI_API_KEY,
        model: str = GEMINI_MODEL,
        base_url: str = GEMINI_API_BASE,
    ):
        if not api_key:
            raise RuntimeError("GEMINI_API_KEY not set")
        self._api_key = api_key
        self._model = model
        self._base_url = base_url.rstrip("/")

    def complete(self, system: str, user: str, max_tokens: int = 1024, timeout: float = 30.0) -> str:
        resp = httpx.post(
            f"{self._base_url}/models/{self._model}:generateContent",
            params={"key": self._api_key},
            headers={"Content-Type": "application/json"},
            json={
                "systemInstruction": {"parts": [{"text": system}]},
                "contents": [{"role": "user", "parts": [{"text": user}]}],
                "generationConfig": {"maxOutputTokens": max_tokens},
            },
            timeout=timeout,
        )
        resp.raise_for_status()
        candidates = resp.json().get("candidates") or []
        if not candidates:
            return ""
        parts = candidates[0].get("content", {}).get("parts", [])
        return "".join(part.get("text", "") for part in parts)


_PROVIDERS = {
    "anthropic": AnthropicChatProvider,
    "openai": OpenAIChatProvider,
    "gemini": GeminiChatProvider,
    "openai_compatible": OpenAICompatibleChatProvider,
}

_provider: ChatProvider | None = None


def get_chat_provider() -> ChatProvider:
    global _provider
    if _provider is None:
        choice = os.environ.get("LLM_PROVIDER") or _default_provider_choice()
        try:
            factory = _PROVIDERS[choice]
        except KeyError:
            raise RuntimeError(f"Unknown LLM_PROVIDER {choice!r} — choose one of {sorted(_PROVIDERS)}")
        _provider = factory()
    return _provider


def _default_provider_choice() -> str:
    if ANTHROPIC_API_KEY:
        return "anthropic"
    if OPENAI_API_KEY:
        return "openai"
    if GEMINI_API_KEY:
        return "gemini"
    if OPENAI_COMPATIBLE_API_BASE:
        return "openai_compatible"
    return "anthropic"


def extract_memory_candidates(dialogue_text: str, timeout: float = 30.0) -> list[dict]:
    """Ask the configured LLM to pull durable memory candidates out of a
    chunk of dialogue.

    Returns a list of {"content": str, "memory_type": "episodic"|"semantic",
    "importance": float}. Empty input, and an LLM that decides there's nothing
    worth keeping, both yield [].
    """
    dialogue_text = (dialogue_text or "").strip()
    if not dialogue_text:
        return []
    text_out = get_chat_provider().complete(_SYSTEM_PROMPT, dialogue_text, max_tokens=1024, timeout=timeout)
    return _parse_candidates(text_out)


def _parse_candidates(text_out: str) -> list[dict]:
    match = re.search(r"\[.*\]", text_out, re.DOTALL)
    if not match:
        return []
    try:
        raw = json.loads(match.group(0))
    except json.JSONDecodeError:
        return []
    if not isinstance(raw, list):
        return []

    candidates = []
    for item in raw:
        if not isinstance(item, dict):
            continue
        content = str(item.get("content") or "").strip()
        memory_type = item.get("type") or item.get("memory_type")
        if not content or memory_type not in ("episodic", "semantic"):
            continue
        try:
            importance = float(item.get("importance", 0.5))
        except (TypeError, ValueError):
            importance = 0.5
        importance = min(1.0, max(0.0, importance))
        candidates.append({"content": content, "memory_type": memory_type, "importance": importance})
    return candidates
