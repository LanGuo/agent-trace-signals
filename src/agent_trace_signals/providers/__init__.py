from agent_trace_signals.providers.base import ModelProvider
from agent_trace_signals.providers.ollama import OllamaProvider, AnthropicProvider


def provider_for_model(model_name: str, config) -> ModelProvider:
    """Pick a provider by model-name prefix.

    Claude / Anthropic model names ("claude-*") route to AnthropicProvider;
    everything else falls back to OllamaProvider.
    """
    if model_name.startswith("claude-") or model_name.startswith("anthropic"):
        return AnthropicProvider(config)
    return OllamaProvider(config)


__all__ = ["ModelProvider", "OllamaProvider", "AnthropicProvider", "provider_for_model"]
