"""Chunk and session summarization using LLM."""

from __future__ import annotations
from agent_trace_signals.config import ModelConfig
from agent_trace_signals.providers.base import ModelProvider


class Summarizer:
    """Summarize text chunks and sessions hierarchically."""

    def __init__(self, provider: ModelProvider, config: ModelConfig) -> None:
        """Initialize summarizer with provider and configuration.

        Args:
            provider: LLM provider for generating summaries
            config: Model configuration with model names
        """
        self.provider = provider
        self.config = config

    def summarize_session(self, chunk_summaries: list[str], source_type: str) -> str:
        """Generate a 4-6 sentence summary of a session from chunk summaries.

        Uses hierarchical summarization when there are more than 15 chunk summaries.

        Args:
            chunk_summaries: List of chunk summary strings
            source_type: Either 'agent session' or 'meeting'

        Returns:
            A 4-6 sentence session summary
        """
        # If there are few chunks, summarize directly
        if len(chunk_summaries) <= 15:
            joined_summaries = "\n".join(chunk_summaries)

            prompt = f"""Given these consecutive segment summaries from a {source_type}, write a 4-6 sentence summary covering: overall task or discussion, key decisions or outcomes, what went wrong (if anything), and the final state.

Segments:
{joined_summaries}"""

            summary = self.provider.complete_text(
                prompt,
                model=self.config.session_summarizer,
                max_tokens=512,
            )
            return summary.strip()

        # Otherwise use hierarchical reduction: group summaries first
        group_size = 10
        group_summaries = []
        for i in range(0, len(chunk_summaries), group_size):
            group = chunk_summaries[i : i + group_size]
            joined_group = "\n".join(group)
            group_prompt = f"""In 3-5 sentences, extract the durable knowledge from these consecutive segment summaries:
what problem was identified, what decision was made and why, what changed as a result.
Be specific about named systems and concepts.

Segments:
{joined_group}"""
            group_summary = self.provider.complete_text(
                group_prompt,
                model=self.config.session_summarizer,
                max_tokens=256,
            ).strip()
            group_summaries.append(group_summary)

        # Now summarize the group summaries
        joined_summaries = "\n".join(group_summaries)

        prompt = f"""Given these consecutive segment summaries from a {source_type}, write a 4-6 sentence summary covering: overall task or discussion, key decisions or outcomes, what went wrong (if anything), and the final state.

Segments:
{joined_summaries}"""

        summary = self.provider.complete_text(
            prompt,
            model=self.config.session_summarizer,
            max_tokens=512,
        )
        return summary.strip()
