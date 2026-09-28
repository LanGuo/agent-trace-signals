"""Embedding provider for text vectorization."""

from __future__ import annotations
import hashlib

import httpx

from agent_trace_signals.config import ModelConfig


class Embedder:
    """Ollama-based text embedding with caching."""

    def __init__(self, config: ModelConfig) -> None:
        self.config = config
        self.base_url = config.ollama_base_url
        self._cache: dict[str, list[float]] = {}

    # nomic-embed-text has an 8192-token context window. Char-to-token ratio varies:
    # CJK text can be ~1 char/token. 2000 chars is safe across all content types.
    # _BATCH_SIZE limits texts per /api/embed call to avoid combined-context overflow.
    _MAX_CHARS = 2000
    _BATCH_SIZE = 4

    def embed(self, text: str) -> list[float]:
        """Embed a single text string."""
        text = text[:self._MAX_CHARS]
        cache_key = self._make_cache_key(text)

        if cache_key in self._cache:
            return self._cache[cache_key]

        try:
            with httpx.Client(timeout=30.0) as client:
                response = client.post(
                    f"{self.base_url}/api/embed",
                    json={"model": self.config.embedding_model, "input": [text]},
                )
                response.raise_for_status()
        except (httpx.RequestError, httpx.HTTPStatusError) as e:
            raise RuntimeError(
                f"Ollama server not reachable at {self.base_url}. "
                f"Make sure Ollama is running: {e}"
            ) from e

        data = response.json()
        embedding = data["embeddings"][0]
        self._cache[cache_key] = embedding
        return embedding

    def embed_batch(self, texts: list[str]) -> list[list[float]]:
        """Embed multiple texts using the batch /api/embed endpoint.

        Sends all texts in one request rather than one request per text, preventing
        nomic-embed-text from being evicted by gemma3:12b between calls.
        Falls back to sequential embed() on API error.
        """
        if not texts:
            return []

        # Check cache first — only request uncached texts
        cache_keys = [self._make_cache_key(t) for t in texts]
        uncached_indices = [i for i, k in enumerate(cache_keys) if k not in self._cache]

        if not uncached_indices:
            return [self._cache[k] for k in cache_keys]

        uncached_texts = [texts[i][:self._MAX_CHARS] for i in uncached_indices]
        try:
            embeddings: list[list[float]] = []
            with httpx.Client(timeout=120.0) as client:
                for batch_start in range(0, len(uncached_texts), self._BATCH_SIZE):
                    batch = uncached_texts[batch_start: batch_start + self._BATCH_SIZE]
                    response = client.post(
                        f"{self.base_url}/api/embed",
                        json={"model": self.config.embedding_model, "input": batch},
                    )
                    response.raise_for_status()
                    embeddings.extend(response.json()["embeddings"])
            for i, emb in zip(uncached_indices, embeddings):
                self._cache[cache_keys[i]] = emb
        except Exception:
            # Fall back to sequential single-embed on any error
            for i in uncached_indices:
                self._cache[cache_keys[i]] = self.embed(texts[i])

        return [self._cache[k] for k in cache_keys]

    def _make_cache_key(self, text: str) -> str:
        """Generate cache key from model and text."""
        content = f"{self.config.embedding_model}:{text}"
        return hashlib.sha256(content.encode()).hexdigest()
