"""Run the D-prompt (DChunkAnalyzer) across multiple models on the same chunks."""
from __future__ import annotations
import json
import sys
import time
import traceback
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO_ROOT / "src"))

from agent_trace_signals.config import ModelConfig, PipelineConfig
from agent_trace_signals.models import Record, Session
from agent_trace_signals.providers.ollama import OllamaProvider
from agent_trace_signals.providers.base import ModelProvider

sys.path.insert(0, str(Path(__file__).resolve().parent))
from v2_chunk_analyzer import DChunkAnalyzerV2

SCRATCH = Path(__file__).resolve().parent

OLLAMA_MODELS = ["gemma3:12b", "gemma4:e4b", "gemma4:31b", "gemma4:12b", "olmo-3:7b-think"]

# Models that emit <think>...</think> chain-of-thought before their JSON answer.
# DChunkAnalyzer.analyze() hardcodes max_tokens=1024 for the D-prompt call, which
# is fine for non-thinking models but starves these — reasoning alone can consume
# the whole budget, leaving 0 tokens for the actual JSON (silent empty-string
# failure). Same issue config.py already documents for observer_llm/entity_verifier
# (bumped to 2048) but chunk_analyzer.py's D-prompt call was never updated.
THINKING_MODELS = {"gemma4:e4b", "olmo-3:7b-think", "gemini-2.5-flash", "gemma4:12b"}
THINKING_MODEL_MAX_TOKENS = 12288


class SeededOllamaProvider(ModelProvider):
    """OllamaProvider variant that pins a `seed` for reproducibility testing.

    Production OllamaProvider never sets seed (only temperature=0.0), so even
    "deterministic" greedy decoding can vary run-to-run due to GPU floating-point
    non-determinism in batched inference. This isolates that from genuine prompt-
    driven classification ambiguity: same chunk + same seed + identical output
    across repeats means any variance we've seen elsewhere is real ambiguity, not
    noise from the inference layer.
    """

    def __init__(self, config: ModelConfig, seed: int) -> None:
        self.config = config
        self.base_url = config.ollama_base_url
        self.seed = seed

    def complete(self, prompt, *, model=None, schema=None, max_tokens=1024, temperature=0.0):
        import httpx
        import json as json_
        import re
        model = model or self.config.chunk_summarizer
        if schema is not None:
            prompt = f"{prompt}\nRespond ONLY with valid JSON matching this schema: {json_.dumps(schema)}"
        with httpx.Client(timeout=httpx.Timeout(30.0, read=300.0)) as client:
            resp = client.post(f"{self.base_url}/api/generate", json={
                "model": model, "prompt": prompt, "stream": False,
                "options": {
                    "temperature": temperature, "num_predict": max_tokens,
                    "num_ctx": self.config.ollama_num_ctx, "seed": self.seed,
                },
            })
            resp.raise_for_status()
        data = resp.json()
        if schema is not None:
            text = data["response"].strip()
            text = re.sub(r"<think>.*?</think>", "", text, flags=re.DOTALL).strip()
            if text.startswith("```"):
                lines = text.splitlines()
                end = len(lines) - 1
                while end > 0 and not lines[end].strip().startswith("```"):
                    end -= 1
                text = "\n".join(lines[1:end]).strip()
            return json_.loads(text)
        return data["response"]


class MaxTokensBoostProvider(ModelProvider):
    """Wraps a provider, raising max_tokens for known thinking models."""

    def __init__(self, inner: ModelProvider, min_max_tokens: int) -> None:
        self.inner = inner
        self.min_max_tokens = min_max_tokens

    def complete(self, prompt, *, model=None, schema=None, max_tokens=1024, temperature=0.0):
        return self.inner.complete(
            prompt, model=model, schema=schema,
            max_tokens=max(max_tokens, self.min_max_tokens), temperature=temperature,
        )


class ThoroughnessProvider(ModelProvider):
    """Wraps a provider, appending an explicit volume instruction to the prompt.

    Tests the hypothesis that local models under-extract not because they lack the
    capability to notice entities/memories in a chunk, but because the prompt never
    explicitly tells them to be exhaustive — i.e. a volume gap closable by instruction
    rather than a genuine capability gap.
    """

    SUFFIX = (
        "\n\nBe thorough: list every entity and memory that meets the stated bar in "
        "each category, not just the single most prominent one — real chunks often "
        "contain multiple qualifying entities/memories. (Patterns still cap at 2 per "
        "the rule above.) Do not stop after the first qualifying item per field."
    )

    def __init__(self, inner: ModelProvider) -> None:
        self.inner = inner

    def complete(self, prompt, *, model=None, schema=None, max_tokens=1024, temperature=0.0):
        return self.inner.complete(
            prompt + self.SUFFIX, model=model, schema=schema,
            max_tokens=max_tokens, temperature=temperature,
        )


class GeminiProvider(ModelProvider):
    """Minimal REST provider for Gemini models (generateContent, JSON mode)."""

    def __init__(self, api_key: str) -> None:
        self.api_key = api_key

    def complete(self, prompt, *, model=None, schema=None, max_tokens=1024, temperature=0.0):
        import httpx
        model = model or "gemini-2.5-flash"
        url = f"https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent"
        body = {
            "contents": [{"parts": [{"text": prompt}]}],
            "generationConfig": {
                "temperature": temperature,
                "maxOutputTokens": max_tokens,
            },
        }
        if schema is not None:
            body["generationConfig"]["responseMimeType"] = "application/json"
        with httpx.Client(timeout=httpx.Timeout(30.0, read=120.0)) as client:
            resp = client.post(url, params={"key": self.api_key}, json=body)
            resp.raise_for_status()
        data = resp.json()
        try:
            text = data["candidates"][0]["content"]["parts"][0]["text"]
        except (KeyError, IndexError) as e:
            raise RuntimeError(f"Unexpected Gemini response shape: {json.dumps(data)[:500]}") from e
        if schema is not None:
            return json.loads(text)
        return text


def main():
    chunks = json.loads((SCRATCH / "chunks.json").read_text())

    pipeline_config = PipelineConfig()
    model_config = ModelConfig()

    out_name = "results.json"
    if "--out" in sys.argv:
        out_name = sys.argv[sys.argv.index("--out") + 1]
    results_path = SCRATCH / out_name
    results = json.loads(results_path.read_text()) if results_path.exists() and "--append" in sys.argv else []

    ollama_provider = OllamaProvider(model_config)
    gemini_provider = None
    if "--with-gemini" in sys.argv or "--gemini-only" in sys.argv:
        # No key is stored in this repo (deliberately, to avoid committing a secret).
        # Set GEMINI_API_KEY in the environment before running with a Gemini arm.
        import os
        key = os.environ.get("GEMINI_API_KEY")
        if not key:
            raise SystemExit("GEMINI_API_KEY env var not set — required for --with-gemini/--gemini-only")
        gemini_provider = GeminiProvider(key)

    skip_next = False
    requested = []
    for a in sys.argv[1:]:
        if skip_next:
            skip_next = False
            continue
        if a == "--out":
            skip_next = True
            continue
        if not a.startswith("--"):
            requested.append(a)
    if "--gemini-only" in sys.argv:
        ollama_models = []
    else:
        ollama_models = requested if requested else OLLAMA_MODELS
    models_to_run = [(m, ollama_provider) for m in ollama_models]
    if "--with-gemini" in sys.argv:
        models_to_run.append(("gemini-2.5-flash", gemini_provider))

    # Model-major order: run each model across all chunks before switching to the
    # next model. Ollama keeps one model resident in GPU memory at a time, so
    # chunk-major order (switching models every call) forces a reload almost every
    # single call — model-major keeps the same model loaded across its whole run.
    for model_name, provider in models_to_run:
        if "--thorough" in sys.argv:
            provider = ThoroughnessProvider(provider)
        if model_name in THINKING_MODELS:
            provider = MaxTokensBoostProvider(provider, THINKING_MODEL_MAX_TOKENS)
        # DChunkAnalyzer.analyze() hardcodes model=self.config.chunk_summarizer,
        # so each call needs its own config with chunk_summarizer set to the
        # model under test (otherwise every arm silently calls the same model).
        per_model_config = ModelConfig(chunk_summarizer=model_name)
        analyzer = DChunkAnalyzerV2(provider, per_model_config, pipeline_config)
        result_label = model_name + ("+thorough" if "--thorough" in sys.argv else "")

        for chunk in chunks:
            label = chunk["label"]
            idx = chunk["chunk_index"]
            record = Record(
                id=f"{label}-{idx}",
                session_id=chunk["session_id"],
                chunk_index=idx,
                chunk_text=chunk["chunk_text"],
            )
            session = Session(id=chunk["session_id"], source_type="agent_trace", source_plugin="unknown")

            t0 = time.time()
            try:
                r = analyzer.analyze(record, session)
                elapsed = time.time() - t0
                results.append({
                    "chunk_label": label,
                    "chunk_index": idx,
                    "model": result_label,
                    "elapsed_s": round(elapsed, 1),
                    "summary": r.summary,
                    "entities": [e.__dict__ for e in r.entities],
                    "memories": [m.__dict__ for m in r.memories],
                    "patterns": [p.__dict__ for p in r.patterns],
                    "preferences": [p.__dict__ for p in r.preferences],
                    "error": None,
                })
                print(f"OK   {label:20s} chunk={idx:3d} model={model_name:20s} {elapsed:5.1f}s  "
                      f"entities={len(r.entities)} memories={len(r.memories)} patterns={len(r.patterns)} prefs={len(r.preferences)}",
                      flush=True)
            except Exception as e:
                elapsed = time.time() - t0
                results.append({
                    "chunk_label": label,
                    "chunk_index": idx,
                    "model": result_label,
                    "elapsed_s": round(elapsed, 1),
                    "error": f"{type(e).__name__}: {e}",
                })
                print(f"FAIL {label:20s} chunk={idx:3d} model={model_name:20s} {elapsed:5.1f}s  {e}", flush=True)
                traceback.print_exc()

            # Flush progressively so partial results survive a crash/timeout.
            results_path.write_text(json.dumps(results, indent=2))

    print(f"\nDone. {len(results)} results written to {out_name}")


if __name__ == "__main__":
    main()
