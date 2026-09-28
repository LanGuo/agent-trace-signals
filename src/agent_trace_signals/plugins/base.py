"""SourcePlugin interface — one implementation per source type."""

from __future__ import annotations
from abc import ABC, abstractmethod
from agent_trace_signals.models import ParsedSession


class SourcePlugin(ABC):
    """
    Parse a source file into a ParsedSession.

    Each plugin is responsible for:
    - Splitting the file into raw chunks (exchanges for agent traces,
      topic segments for transcripts)
    - Extracting session-level metadata (timestamp, workspace_id, raw_facets)
    - Performing source-type-specific entity extraction (e.g. tool-call
      parsing for claude_code) by populating chunk_text with serialized
      exchange text that downstream extractors can process

    The plugin does NOT embed, summarize, resolve entities, or write to DB.
    """

    source_plugin: str   # e.g. "claude_code", "zoom", "otter"
    source_type: str     # "agent_trace" | "meeting_transcript"

    @abstractmethod
    def can_handle(self, abs_path: str) -> bool:
        """Return True if this plugin can parse the given file."""
        ...

    @abstractmethod
    def parse(self, abs_path: str) -> ParsedSession:
        """
        Parse the file at abs_path.

        Returns a ParsedSession with:
        - chunks: list of RawChunk (text + optional span markers)
        - session_timestamp: ISO8601 of earliest event, or None
        - workspace_id: project directory identifier
        - raw_facets: source-specific dict (cwd, git_branch, etc.)
        - metadata: additional structured metadata
        """
        ...
