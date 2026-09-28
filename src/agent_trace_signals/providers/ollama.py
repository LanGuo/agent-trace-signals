"""Ollama and Anthropic LLM provider implementations."""

from __future__ import annotations
import json
import os
from typing import Any, Iterator

import httpx
from anthropic import Anthropic

from agent_trace_signals.config import ModelConfig
from agent_trace_signals.providers.base import ModelProvider


def _parse_json_response(text: str) -> dict[str, Any]:
    """Parse JSON from LLM response, stripping thinking tokens and markdown fences."""
    import re
    text = text.strip()
    # Strip <think>...</think> blocks emitted by thinking models (qwen3, gemma4:e4b)
    text = re.sub(r"<think>.*?</think>", "", text, flags=re.DOTALL).strip()
    # Strip ```json ... ``` or ``` ... ``` fences
    if text.startswith("```"):
        lines = text.splitlines()
        end = len(lines) - 1
        while end > 0 and not lines[end].strip().startswith("```"):
            end -= 1
        text = "\n".join(lines[1:end]).strip()
    return json.loads(text)


class OllamaProvider(ModelProvider):
    """LLM provider for local Ollama server."""

    def __init__(self, config: ModelConfig) -> None:
        self.config = config
        self.base_url = config.ollama_base_url

    def complete(
        self,
        prompt: str,
        *,
        model: str | None = None,
        schema: dict[str, Any] | None = None,
        max_tokens: int = 1024,
        temperature: float = 0.0,
        keep_alive: str | None = None,
    ) -> str | dict[str, Any]:
        model = model or self.config.chunk_summarizer

        if schema is not None:
            schema_str = json.dumps(schema)
            prompt = f"{prompt}\nRespond ONLY with valid JSON matching this schema: {schema_str}"

        request_json = {
            "model": model,
            "prompt": prompt,
            "stream": False,
            "options": {
                "temperature": temperature,
                "num_predict": max_tokens,
                "num_ctx": self.config.ollama_num_ctx,
            },
        }
        if keep_alive is not None:
            request_json["keep_alive"] = keep_alive

        try:
            # Timeouts bumped from the original 30s connect / (default, ~5s) read.
            # gemma4:31b (21GB, used for ats cluster-memories' consolidation calls)
            # is genuinely slow on this hardware, not just slow-to-connect: a direct
            # measurement (ClusteringAnalyticsPipeline.consolidate_memories() on a
            # real cluster, not a toy prompt) took 310s total before the READ
            # timeout fired — confirmed by reproducing at exactly the old 300s read
            # ceiling. An intermediate theory (connect-phase timeout from cold model
            # reload after Ollama's ~4min idle-unload) was tested and ruled out:
            # bumping connect alone from 30s->60s->180s made no difference, because
            # the connection itself was never the bottleneck — generation time was.
            # 180s connect is still reasonable headroom for a cold reload; 900s read
            # covers real generation time for a complex consolidation prompt (many
            # cluster members + conflict-detection) on a large, slow model.
            with httpx.Client(timeout=httpx.Timeout(180.0, read=900.0)) as client:
                response = client.post(f"{self.base_url}/api/generate", json=request_json)
                response.raise_for_status()
        except (httpx.RequestError, httpx.HTTPStatusError) as e:
            raise RuntimeError(
                f"Ollama server not reachable at {self.base_url}. "
                f"Make sure Ollama is running: {e}"
            ) from e

        data = response.json()

        if schema is not None:
            result_text = data["response"]
            try:
                return _parse_json_response(result_text)
            except json.JSONDecodeError:
                # Retry with clearer instruction
                retry_prompt = (
                    f"{prompt}\n\nPlease respond ONLY with valid JSON, "
                    "no additional text or markdown formatting."
                )
                retry_json = {**request_json, "prompt": retry_prompt}
                try:
                    with httpx.Client(timeout=httpx.Timeout(180.0, read=900.0)) as client:
                        response = client.post(f"{self.base_url}/api/generate", json=retry_json)
                        response.raise_for_status()
                except (httpx.RequestError, httpx.HTTPStatusError) as e:
                    raise RuntimeError(
                        f"Ollama server not reachable at {self.base_url}. "
                        f"Make sure Ollama is running: {e}"
                    ) from e

                retry_data = response.json()
                return _parse_json_response(retry_data["response"])
        else:
            return data["response"]

    def complete_text_stream(
        self,
        prompt: str,
        model: str | None = None,
        *,
        max_tokens: int = 1024,
        temperature: float = 0.0,
        keep_alive: str | None = None,
    ) -> Iterator[str]:
        """Yield response text incrementally via Ollama's NDJSON streaming mode.

        Plain-text only (no schema/JSON mode — a partial JSON chunk isn't parseable
        anyway, so streaming only makes sense for the plain-text case).
        """
        model = model or self.config.chunk_summarizer
        request_json = {
            "model": model,
            "prompt": prompt,
            "stream": True,
            "options": {
                "temperature": temperature,
                "num_predict": max_tokens,
                "num_ctx": self.config.ollama_num_ctx,
            },
        }
        if keep_alive is not None:
            request_json["keep_alive"] = keep_alive

        try:
            with httpx.Client(timeout=httpx.Timeout(180.0, read=900.0)) as client:
                with client.stream("POST", f"{self.base_url}/api/generate", json=request_json) as response:
                    response.raise_for_status()
                    for line in response.iter_lines():
                        if not line:
                            continue
                        chunk = json.loads(line)
                        if chunk.get("response"):
                            yield chunk["response"]
                        if chunk.get("done"):
                            break
        except (httpx.RequestError, httpx.HTTPStatusError) as e:
            raise RuntimeError(
                f"Ollama server not reachable at {self.base_url}. "
                f"Make sure Ollama is running: {e}"
            ) from e


class AnthropicProvider(ModelProvider):
    """LLM provider for Anthropic's Claude API."""

    def __init__(self, config: ModelConfig) -> None:
        self.config = config
        api_key = config.anthropic_api_key or os.environ.get("ANTHROPIC_API_KEY")
        self.client = Anthropic(api_key=api_key)

    def complete(
        self,
        prompt: str,
        *,
        model: str | None = None,
        schema: dict[str, Any] | None = None,
        max_tokens: int = 1024,
        temperature: float = 0.0,
    ) -> str | dict[str, Any]:
        model = model or self.config.chunk_summarizer

        if schema is not None:
            schema_str = json.dumps(schema)
            prompt = f"{prompt}\nRespond ONLY with valid JSON matching this schema: {schema_str}"

        response = self.client.messages.create(
            model=model,
            max_tokens=max_tokens,
            temperature=temperature,
            messages=[{"role": "user", "content": prompt}],
        )

        result_text = response.content[0].text

        if schema is not None:
            try:
                return json.loads(result_text)
            except json.JSONDecodeError:
                # Retry with clearer instruction
                retry_prompt = (
                    f"{prompt}\n\nPlease respond ONLY with valid JSON, "
                    "no additional text or markdown formatting."
                )
                retry_response = self.client.messages.create(
                    model=model,
                    max_tokens=max_tokens,
                    temperature=temperature,
                    messages=[{"role": "user", "content": retry_prompt}],
                )
                return json.loads(retry_response.content[0].text)
        else:
            return result_text
