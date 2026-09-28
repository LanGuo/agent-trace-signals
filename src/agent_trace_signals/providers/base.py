"""ModelProvider interface — all LLM calls go through this."""

from __future__ import annotations
from abc import ABC, abstractmethod
from typing import Any, Iterator


class ModelProvider(ABC):
    """
    Abstract LLM provider. Implementations: OllamaProvider, AnthropicProvider.

    complete() returns:
    - str  when schema is None
    - dict when schema is provided (JSON-mode / structured output)
    """

    @abstractmethod
    def complete(
        self,
        prompt: str,
        *,
        model: str | None = None,
        schema: dict[str, Any] | None = None,
        max_tokens: int = 1024,
        temperature: float = 0.0,
    ) -> str | dict[str, Any]:
        """Send a prompt and return text or parsed JSON."""
        ...

    def complete_text(self, prompt: str, model: str | None = None, **kwargs) -> str:
        result = self.complete(prompt, model=model, schema=None, **kwargs)
        assert isinstance(result, str)
        return result

    def complete_json(
        self, prompt: str, schema: dict[str, Any], model: str | None = None, **kwargs
    ) -> dict[str, Any]:
        result = self.complete(prompt, model=model, schema=schema, **kwargs)
        assert isinstance(result, dict)
        return result

    def complete_text_stream(self, prompt: str, model: str | None = None, **kwargs) -> Iterator[str]:
        """Yield the answer incrementally. Default fallback: no native streaming,
        so just yield the full completion once (still works, just not incremental).
        Providers that support real token streaming (OllamaProvider) override this.
        """
        yield self.complete_text(prompt, model=model, **kwargs)
