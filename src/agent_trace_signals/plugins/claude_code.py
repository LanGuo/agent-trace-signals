"""Claude Code JSONL session parser — extracts exchanges and tool-based entities."""

from __future__ import annotations
import json
import logging
import re
from pathlib import Path
from typing import Any

from agent_trace_signals.config import PipelineConfig
from agent_trace_signals.models import ParsedSession, RawChunk, RawEntityMention, SessionMetadata, TurnDescriptor
from agent_trace_signals.plugins.base import SourcePlugin


logger = logging.getLogger(__name__)


class ClaudeCodeSource(SourcePlugin):
    """Parse Claude Code session JSONL files into exchanges with entity extraction."""

    source_plugin = "claude_code"
    source_type = "agent_trace"

    def __init__(self, config: PipelineConfig) -> None:
        """Initialize with pipeline configuration.

        Args:
            config: PipelineConfig with max_chunk_tokens and chunk_overlap_pct
        """
        self.config = config

    def can_handle(self, abs_path: str) -> bool:
        """Return True if file ends with .jsonl and not under subagents/ directory.

        Args:
            abs_path: Absolute file path

        Returns:
            True if this plugin can parse the file
        """
        path = Path(abs_path)
        # Must end with .jsonl
        if not path.suffix == ".jsonl":
            return False
        # Exclude subagents directories
        if "subagents" in path.parts:
            return False
        # Exclude Gemini CLI sessions (handled by GeminiSource)
        if ".gemini" in path.parts:
            return False
        return True

    def parse(self, abs_path: str) -> ParsedSession:
        """Parse Claude Code JSONL file into exchanges with entity extraction.

        Args:
            abs_path: Absolute path to JSONL file

        Returns:
            ParsedSession with chunks and metadata
        """
        path = Path(abs_path)

        # Initialize session metadata
        session_id: str | None = None
        cwd: str | None = None
        git_branch: str | None = None
        cc_version: str | None = None
        session_timestamp: str | None = None
        user_turns = 0
        tool_calls = 0

        # Read and parse JSONL file
        messages: list[dict[str, Any]] = []
        try:
            with open(path, "r", encoding="utf-8") as f:
                for line_num, line in enumerate(f, 1):
                    line = line.strip()
                    if not line:
                        continue
                    try:
                        obj = json.loads(line)
                        messages.append(obj)

                        # Extract session-level metadata from any message
                        if session_id is None and "sessionId" in obj:
                            session_id = obj["sessionId"]
                        if cwd is None and "cwd" in obj:
                            cwd = obj["cwd"]
                        if git_branch is None and "gitBranch" in obj:
                            git_branch = obj["gitBranch"]
                        if cc_version is None and "version" in obj:
                            cc_version = obj["version"]
                        # Track earliest timestamp
                        if "timestamp" in obj:
                            ts = obj["timestamp"]
                            if session_timestamp is None or ts < session_timestamp:
                                session_timestamp = ts

                    except json.JSONDecodeError as e:
                        logger.warning(f"Skipping malformed JSONL line {line_num}: {e}")
                        continue
        except Exception as e:
            logger.error(f"Error reading JSONL file {abs_path}: {e}")
            return ParsedSession(
                source_plugin=self.source_plugin,
                source_type=self.source_type,
                chunks=[],
                workspace_id=self._compute_workspace_id(abs_path),
            )

        # Extract away_summaries — strip the "(disable recaps in /config)" trailer
        away_summaries = []
        for m in messages:
            if m.get("type") == "system" and m.get("subtype") == "away_summary":
                content = m.get("content", "")
                content = content.split(" (disable recaps")[0].strip()
                if content:
                    away_summaries.append({
                        "timestamp": m.get("timestamp", ""),
                        "content": content,
                    })

        # Filter messages and build exchanges
        filtered_messages = [m for m in messages if self._should_keep_message(m)]

        # Build exchanges (user message + following assistant messages)
        exchanges = self._build_exchanges(filtered_messages)

        # Chunk exchanges
        chunks, entity_mentions = self._chunk_exchanges(
            exchanges, abs_path
        )

        # Compute workspace_id from file path
        workspace_id = self._compute_workspace_id(abs_path)

        # Count turns from exchanges
        user_turns = len(exchanges)
        tool_calls = sum(
            sum(
                1 for block in (turn.get("message", {}).get("content") or [])
                if isinstance(block, dict) and block.get("type") == "tool_use"
            )
            for exchange in exchanges
            for turn in exchange.get("turns", [])
            if turn.get("type") == "assistant"
        )

        # Build raw_facets
        raw_facets: dict[str, Any] = {
            "session_id": session_id,
            "cwd": cwd,
            "git_branch": git_branch,
            "cc_version": cc_version,
            "total_lines": len(messages),
            "away_summaries": away_summaries,
        }

        # Build per-exchange turn descriptors for inflection detection
        turn_descriptors = [
            self._extract_turn_descriptor(ex, idx).model_dump()
            for idx, ex in enumerate(exchanges)
        ]

        # Build metadata with entity mentions
        metadata: dict[str, Any] = {
            "session_id": session_id,
            "cwd": cwd,
            "git_branch": git_branch,
            "cc_version": cc_version,
            "session_end": session_timestamp,
            "user_turns": user_turns,
            "tool_calls": tool_calls,
            "raw_entity_mentions": [m.model_dump() for m in entity_mentions],
            "turn_descriptors": turn_descriptors,
            # Latest away_summary used as free session summary when skip_summaries=True
            "away_summary_latest": away_summaries[-1]["content"] if away_summaries else None,
        }

        # Extract session-level metadata (token counts, model, etc.) — no LLM needed
        try:
            session_meta = self._extract_session_metadata(messages, session_id or "")
            metadata["session_metadata"] = session_meta.model_dump()
        except Exception as e:
            logger.warning(f"Failed to extract session metadata: {e}")

        return ParsedSession(
            source_plugin=self.source_plugin,
            source_type=self.source_type,
            session_timestamp=session_timestamp,
            raw_facets=raw_facets,
            chunks=chunks,
            workspace_id=workspace_id,
            metadata=metadata,
        )

    # -----------------------------------------------------------------------
    # Private helpers
    # -----------------------------------------------------------------------

    @staticmethod
    def _should_keep_message(msg: dict[str, Any]) -> bool:
        """Return True if message should be kept for exchange building.

        Args:
            msg: Parsed JSON object from JSONL

        Returns:
            True if message type should be kept
        """
        msg_type = msg.get("type")
        # Filter out non-useful types
        if msg_type in (
            "file-history-snapshot",
            "permission-mode",
            "attachment",
            "queue-operation",
            "last-prompt",
            "system",
        ):
            return False
        return True

    @staticmethod
    def _build_exchanges(messages: list[dict[str, Any]]) -> list[dict[str, Any]]:
        """Build exchanges: one real human turn + all following assistant/tool turns.

        Tool-result user records (toolUseResult=True) are part of the same agentic
        turn as the preceding assistant — they are NOT new exchanges.

        Args:
            messages: Filtered list of JSONL message objects

        Returns:
            List of exchange dicts with 'user' and 'turns' keys
        """
        exchanges: list[dict[str, Any]] = []
        current_user: dict[str, Any] | None = None
        current_turns: list[dict[str, Any]] = []  # assistant + tool-result records in order

        for msg in messages:
            msg_type = msg.get("type")
            is_tool_result = bool(msg.get("toolUseResult"))

            if msg_type == "user" and not is_tool_result:
                # Real human turn — close previous exchange, start new one
                if current_user is not None:
                    exchanges.append({"user": current_user, "turns": current_turns})
                current_user = msg
                current_turns = []

            elif msg_type == "assistant" or (msg_type == "user" and is_tool_result):
                # Continuation of the current agentic turn
                if current_user is not None:
                    current_turns.append(msg)

        if current_user is not None:
            exchanges.append({"user": current_user, "turns": current_turns})

        return exchanges

    def _chunk_exchanges(
        self, exchanges: list[dict[str, Any]], abs_path: str
    ) -> tuple[list[RawChunk], list[RawEntityMention]]:
        """Convert exchanges to chunks and extract entities.

        Args:
            exchanges: List of exchange dicts
            abs_path: Absolute path for context

        Returns:
            Tuple of (chunks, entity_mentions)
        """
        chunks: list[RawChunk] = []
        all_entities: list[RawEntityMention] = []

        chunk_index = 0
        acc_parts: list[str] = []
        acc_evidence_parts: list[str] = []
        acc_tokens = 0
        acc_start: int | None = None  # Track exchange index of first exchange in accumulator

        def _flush_acc(end_exchange_idx: int | None = None) -> None:
            nonlocal chunk_index, acc_parts, acc_evidence_parts, acc_tokens, acc_start
            if acc_parts:
                text = "\n\n".join(acc_parts)
                evidence = "".join(acc_evidence_parts)
                chunks.append(RawChunk(
                    chunk_index=chunk_index,
                    chunk_text=text,
                    evidence_text=evidence,
                    token_count=acc_tokens,
                    span_start=str(acc_start) if acc_start is not None else None,
                    span_end=str(end_exchange_idx) if end_exchange_idx is not None else None,
                ))
                chunk_index += 1
                acc_parts = []
                acc_evidence_parts = []
                acc_tokens = 0
                acc_start = None

        for exchange_idx, exchange in enumerate(exchanges):
            exchange_text = self._serialize_exchange(exchange)
            evidence_text = self._extract_evidence(exchange)
            entities = self._extract_entities(exchange, exchange_text)
            all_entities.extend(entities)
            token_count = self._estimate_tokens(exchange_text)

            if token_count > self.config.max_chunk_tokens:
                # Single oversized exchange — flush accumulator then split it
                _flush_acc(exchange_idx - 1 if acc_start is not None else None)
                sub_chunks = self._split_long_exchange(exchange_text)
                for i, sub_text in enumerate(sub_chunks):
                    chunks.append(RawChunk(
                        chunk_index=chunk_index,
                        chunk_text=sub_text,
                        evidence_text=evidence_text if i == 0 else "",
                        token_count=self._estimate_tokens(sub_text),
                        span_start=str(exchange_idx),
                        span_end=str(exchange_idx),
                    ))
                    chunk_index += 1
            elif acc_tokens + token_count > self.config.max_chunk_tokens:
                # Adding this exchange would overflow — flush first
                _flush_acc(exchange_idx - 1)
                acc_parts.append(exchange_text)
                acc_evidence_parts.append(evidence_text)
                acc_tokens = token_count
                acc_start = exchange_idx
            else:
                if acc_start is None:
                    acc_start = exchange_idx
                acc_parts.append(exchange_text)
                acc_evidence_parts.append(evidence_text)
                acc_tokens += token_count

        _flush_acc(len(exchanges) - 1)
        return chunks, all_entities

    @staticmethod
    def _extract_evidence(exchange: dict[str, Any]) -> str:
        """Extract verbatim tool_result content from an exchange.

        Format per tool_result: '[tool: <name>(<args>)]\\n<result>\\n\\n'.
        Cap per individual tool_result at 4000 chars. No exchange-level cap.
        """
        EVIDENCE_CAP = 4000
        # Build id → (name, args) map by walking assistant tool_use blocks first.
        tool_use_by_id: dict[str, tuple[str, str]] = {}
        ordered_uses: list[tuple[str, str]] = []
        for turn in exchange.get("turns", []):
            if turn.get("type") != "assistant":
                continue
            content = turn.get("message", {}).get("content", [])
            if not isinstance(content, list):
                continue
            for block in content:
                if isinstance(block, dict) and block.get("type") == "tool_use":
                    name = block.get("name", "?")
                    summary = ClaudeCodeSource._summarize_tool_input(block)
                    tu_id = block.get("id", "")
                    if tu_id:
                        tool_use_by_id[tu_id] = (name, summary)
                    ordered_uses.append((name, summary))

        parts: list[str] = []
        fallback_idx = 0
        for turn in exchange.get("turns", []):
            if turn.get("type") != "user":
                continue
            content = turn.get("message", {}).get("content", [])
            if not isinstance(content, list):
                continue
            for block in content:
                if not (isinstance(block, dict) and block.get("type") == "tool_result"):
                    continue
                tu_id = block.get("tool_use_id", "")
                name, args = ("tool", "")
                if tu_id and tu_id in tool_use_by_id:
                    name, args = tool_use_by_id[tu_id]
                elif fallback_idx < len(ordered_uses):
                    name, args = ordered_uses[fallback_idx]
                fallback_idx += 1

                inner = block.get("content", "")
                result_text_parts: list[str] = []
                if isinstance(inner, str):
                    result_text_parts.append(inner)
                elif isinstance(inner, list):
                    for item in inner:
                        if isinstance(item, dict) and item.get("type") == "text":
                            result_text_parts.append(item.get("text", ""))
                result_text = "".join(result_text_parts)[:EVIDENCE_CAP]
                if result_text:
                    parts.append(f"[tool: {name}({args})]\n{result_text}\n\n")
        return "".join(parts)

    @staticmethod
    def _serialize_exchange(exchange: dict[str, Any]) -> str:
        """Serialize exchange into readable text format.

        Args:
            exchange: Exchange dict with 'user' and 'turns' keys

        Returns:
            Serialized exchange text
        """
        lines: list[str] = []

        # Real human turn
        user_msg = exchange.get("user", {})
        user_content = user_msg.get("message", {}).get("content")
        if isinstance(user_content, str):
            user_text = user_content[:500]
        elif isinstance(user_content, list):
            parts: list[str] = []
            for block in user_content:
                if isinstance(block, dict) and block.get("type") == "text":
                    parts.append(block.get("text", "")[:200])
            user_text = " ".join(parts)[:500]
        else:
            user_text = ""
        lines.append(f"USER: {user_text}" if user_text else "USER: (no text)")

        # Cap turns to bound serialized size for long agentic chains.
        # Entity extraction still runs on the full exchange.
        _HEAD, _TAIL = 5, 3
        all_turns = exchange.get("turns", [])
        if len(all_turns) > _HEAD + _TAIL:
            visible_turns = all_turns[:_HEAD] + all_turns[-_TAIL:]
            omitted = len(all_turns) - _HEAD - _TAIL
        else:
            visible_turns = all_turns
            omitted = 0

        for turn_idx, turn in enumerate(visible_turns):
            if omitted and turn_idx == _HEAD:
                lines.append(f"[... {omitted} intermediate turns omitted ...]")
            turn_type = turn.get("type")

            if turn_type == "assistant":
                content = turn.get("message", {}).get("content", [])
                if isinstance(content, list):
                    for block in content:
                        if not isinstance(block, dict):
                            continue
                        if block.get("type") == "thinking":
                            thought = block.get("thinking", "")[:300]
                            if thought:
                                lines.append(f"[thinking]: {thought}")
                        elif block.get("type") == "text":
                            text = block.get("text", "")[:500]
                            if text:
                                lines.append(f"A: {text}")
                        elif block.get("type") == "tool_use":
                            summary = ClaudeCodeSource._summarize_tool_input(block)
                            lines.append(f"[TOOL: {block.get('name','?')}({summary})]")

            elif turn_type == "user":
                # Tool result — emit abbreviated output so context is preserved
                content = turn.get("message", {}).get("content", [])
                result_parts: list[str] = []
                if isinstance(content, list):
                    for block in content:
                        if isinstance(block, dict) and block.get("type") == "tool_result":
                            inner = block.get("content", "")
                            if isinstance(inner, str):
                                result_parts.append(inner[:200])
                            elif isinstance(inner, list):
                                for item in inner:
                                    if isinstance(item, dict) and item.get("type") == "text":
                                        result_parts.append(item.get("text", "")[:200])
                if result_parts:
                    lines.append(f'[{"result":>6}]: {" ".join(result_parts)[:300]}')

        return "\n".join(lines)

    @staticmethod
    def _summarize_tool_input(tool_use_block: dict[str, Any]) -> str:
        """Summarize tool input for display.

        Args:
            tool_use_block: Tool use block from assistant message

        Returns:
            Short summary of tool input
        """
        tool_name = tool_use_block.get("name", "")
        tool_input = tool_use_block.get("input", {})

        if tool_name in ("Read", "Edit", "Write", "Glob"):
            file_path = tool_input.get("file_path", "")
            return f"file_path={file_path}"
        elif tool_name == "Bash":
            command = tool_input.get("command", "")[:80]
            return f"command={command}"
        elif tool_name == "Agent":
            subagent_type = tool_input.get("subagent_type", "")
            description = tool_input.get("description", "")[:60]
            return f"{subagent_type}: {description}"
        else:
            # Generic: first 60 chars of JSON
            summary = json.dumps(tool_input)[:60]
            return summary

    @staticmethod
    def _estimate_tokens(text: str) -> int:
        """Estimate token count using simple word-based model.

        Args:
            text: Text to count

        Returns:
            Estimated token count
        """
        word_count = len(text.split())
        return max(1, int(word_count * 1.3))

    def _split_long_exchange(self, exchange_text: str) -> list[str]:
        """Split long exchange into sub-chunks with overlap.

        Args:
            exchange_text: Text to split

        Returns:
            List of sub-chunk texts
        """
        # Try to split at tool_use boundaries first
        tool_pattern = r"\[TOOL:[^\]]+\]"
        tool_positions = [m.end() for m in re.finditer(tool_pattern, exchange_text)]

        if tool_positions:
            # Split at tool boundaries
            chunks: list[str] = []
            start = 0

            for pos in tool_positions:
                chunk_text = exchange_text[start:pos]
                if self._estimate_tokens(chunk_text) <= self.config.max_chunk_tokens:
                    continue

                # This chunk is too long, save previous part and start new
                if start > 0:
                    chunks.append(exchange_text[start:pos])
                start = pos

            if start < len(exchange_text):
                chunks.append(exchange_text[start:])

            if chunks:
                return chunks

        # Fall back to character-based splitting with overlap
        max_chars = int(self.config.max_chunk_tokens / 1.3) * 10  # Rough estimate
        overlap_chars = int(max_chars * self.config.chunk_overlap_pct)

        chunks = []
        start = 0

        while start < len(exchange_text):
            end = min(start + max_chars, len(exchange_text))
            chunks.append(exchange_text[start:end])
            if end == len(exchange_text):
                break
            start = end - overlap_chars

        return chunks if chunks else [exchange_text]

    def _extract_entities(
        self, exchange: dict[str, Any], exchange_text: str
    ) -> list[RawEntityMention]:
        """Extract entities from tool calls and context.

        Args:
            exchange: Exchange dict
            exchange_text: Serialized exchange text for context

        Returns:
            List of RawEntityMention objects
        """
        entities: list[RawEntityMention] = []
        context_text = exchange_text[:300]  # First 300 chars as context

        # Extract from tool_use blocks in assistant turns
        for asst_msg in (t for t in exchange.get("turns", []) if t.get("type") == "assistant"):
            asst_content = asst_msg.get("message", {}).get("content", [])
            if isinstance(asst_content, list):
                for block in asst_content:
                    if isinstance(block, dict) and block.get("type") == "tool_use":
                        tool_entities = self._extract_from_tool_use(
                            block, context_text
                        )
                        entities.extend(tool_entities)

        return entities

    @staticmethod
    def _extract_from_tool_use(
        tool_use_block: dict[str, Any], context_text: str
    ) -> list[RawEntityMention]:
        """Extract entities from a single tool_use block.

        Args:
            tool_use_block: Tool use block
            context_text: Context for mentions

        Returns:
            List of RawEntityMention objects
        """
        entities: list[RawEntityMention] = []
        tool_name = tool_use_block.get("name", "")
        tool_input = tool_use_block.get("input", {})

        # All tool uses get recorded as "tool" entity
        entities.append(
            RawEntityMention(
                canonical_name=tool_name,
                entity_type="tool",
                mention_text=tool_name,
                context_text=context_text,
                role="tool_used",
                source_layer="tool_call",
            )
        )

        # File-based tools: extract file_path
        if tool_name in ("Read", "Edit", "Write", "Glob"):
            file_path = tool_input.get("file_path", "")
            if file_path:
                role = "file_modified" if tool_name in ("Edit", "Write") else "subject"
                entities.append(
                    RawEntityMention(
                        canonical_name=file_path,
                        entity_type="file",
                        mention_text=file_path,
                        context_text=context_text,
                        role=role,
                        source_layer="tool_call",
                    )
                )

        # Bash: extract file paths and commit hashes
        elif tool_name == "Bash":
            command = tool_input.get("command", "")

            # File paths
            file_pattern = r"/[^\s\"']+\.[a-zA-Z0-9]+"
            for match in re.finditer(file_pattern, command):
                file_path = match.group(0)
                entities.append(
                    RawEntityMention(
                        canonical_name=file_path,
                        entity_type="file",
                        mention_text=file_path,
                        context_text=context_text,
                        role="subject",
                        source_layer="tool_call",
                    )
                )

            # Commit hashes — require at least one letter to avoid decimal false positives
            commit_pattern = r"\b[0-9a-f]{7,40}\b"
            for match in re.finditer(commit_pattern, command):
                commit_hash = match.group(0)
                if not any(c in commit_hash for c in "abcdef"):
                    continue
                entities.append(
                    RawEntityMention(
                        canonical_name=commit_hash,
                        entity_type="commit",
                        mention_text=commit_hash,
                        context_text=context_text,
                        role="commit_ref",
                        source_layer="tool_call",
                    )
                )

            # PR refs
            pr_pattern = r"PR\s*#(\d+)"
            for match in re.finditer(pr_pattern, command):
                pr_ref = f"PR#{match.group(1)}"
                entities.append(
                    RawEntityMention(
                        canonical_name=pr_ref,
                        entity_type="pr",
                        mention_text=pr_ref,
                        context_text=context_text,
                        role="subject",
                        source_layer="tool_call",
                    )
                )

        return entities

    @staticmethod
    def _compute_workspace_id(abs_path: str) -> str:
        """Compute workspace_id from file path.

        For ~/projects/my-project/path/to/file.jsonl, extract 'my-project'
        and encode it as '-Users-username-src-my-project'

        Args:
            abs_path: Absolute file path

        Returns:
            Encoded workspace identifier
        """
        path = Path(abs_path)
        parts = path.parts

        # Find the project directory (first component under ~/.claude/projects or similar)
        # Fallback: use hash of full path if we can't determine project dir
        if len(parts) > 1:
            # Try to extract meaningful project identifier
            for i, part in enumerate(parts):
                if part == "projects" and i + 1 < len(parts):
                    # Return the project directory name as-is (already encoded with -)
                    return parts[i + 1]

        # Fallback: parent directory name
        return path.parent.name

    @staticmethod
    def _parse_iso8601(ts: str) -> float | None:
        """Parse ISO8601 timestamp to epoch seconds. Returns None on failure."""
        from datetime import datetime
        if not ts:
            return None
        try:
            ts = ts.replace("Z", "+00:00")
            return datetime.fromisoformat(ts).timestamp()
        except Exception:
            return None

    @classmethod
    def _extract_session_metadata(
        cls, messages: list[dict[str, Any]], session_id: str
    ) -> SessionMetadata:
        """Extract aggregate session metadata from all raw messages."""
        from collections import Counter

        permission_mode: str | None = None
        entrypoint: str | None = None
        user_type: str | None = None
        is_sidechain: bool = False
        cwd: str | None = None
        git_branch: str | None = None
        cc_version: str | None = None

        user_turn_count = 0
        assistant_turn_count = 0
        tool_call_count = 0

        models: list[str] = []
        timestamps: list[float] = []

        total_input = 0
        total_output = 0
        total_cache_read = 0
        total_cache_creation = 0

        for msg in messages:
            mtype = msg.get("type")
            ts_str = msg.get("timestamp", "")
            if ts_str:
                epoch = cls._parse_iso8601(ts_str)
                if epoch is not None:
                    timestamps.append(epoch)

            if mtype == "user" and not msg.get("toolUseResult"):
                user_turn_count += 1
                if permission_mode is None:
                    permission_mode = msg.get("permissionMode")
                if entrypoint is None:
                    entrypoint = msg.get("entrypoint")
                if user_type is None:
                    user_type = msg.get("userType")
                if not is_sidechain and msg.get("isSidechain"):
                    is_sidechain = True
                if cwd is None:
                    cwd = msg.get("cwd")
                if git_branch is None:
                    git_branch = msg.get("gitBranch")
                if cc_version is None:
                    cc_version = msg.get("version")

            elif mtype == "assistant":
                assistant_turn_count += 1
                inner_msg = msg.get("message", {})
                model = inner_msg.get("model")
                if model:
                    models.append(model)
                usage = inner_msg.get("usage", {})
                total_input += usage.get("input_tokens", 0) or 0
                total_output += usage.get("output_tokens", 0) or 0
                total_cache_read += usage.get("cache_read_input_tokens", 0) or 0
                total_cache_creation += usage.get("cache_creation_input_tokens", 0) or 0
                content = inner_msg.get("content", [])
                if isinstance(content, list):
                    for block in content:
                        if isinstance(block, dict) and block.get("type") == "tool_use":
                            tool_call_count += 1

        dominant_model: str | None = None
        if models:
            dominant_model = Counter(models).most_common(1)[0][0]

        start_time: str | None = None
        end_time: str | None = None
        duration_ms: int | None = None
        if timestamps:
            start_time = messages[0].get("timestamp") if messages else None
            # Find earliest/latest timestamps
            min_epoch = min(timestamps)
            max_epoch = max(timestamps)
            # Map back to ISO strings from messages
            for msg in messages:
                ts_str = msg.get("timestamp", "")
                if ts_str:
                    ep = cls._parse_iso8601(ts_str)
                    if ep == min_epoch:
                        start_time = ts_str
                    if ep == max_epoch:
                        end_time = ts_str
            duration_ms = int((max_epoch - min_epoch) * 1000)

        return SessionMetadata(
            session_id=session_id,
            dominant_model=dominant_model,
            models_used=list(dict.fromkeys(models)),  # unique, order-preserved
            entrypoint=entrypoint,
            permission_mode=permission_mode,
            user_type=user_type,
            is_sidechain=is_sidechain,
            cc_version=cc_version,
            user_turn_count=user_turn_count,
            assistant_turn_count=assistant_turn_count,
            tool_call_count=tool_call_count,
            start_time=start_time,
            end_time=end_time,
            duration_ms=duration_ms,
            total_input_tokens=total_input,
            total_output_tokens=total_output,
            total_cache_read_tokens=total_cache_read,
            total_cache_creation_tokens=total_cache_creation,
            total_thought_tokens=0,
            cwd=cwd,
            git_branch=git_branch,
        )

    @staticmethod
    def _extract_turn_descriptor(exchange: dict[str, Any], exchange_idx: int) -> TurnDescriptor:
        """Extract structural features from one exchange for inflection detection."""
        user_msg = exchange.get("user", {})
        user_content = user_msg.get("message", {}).get("content", "")
        if isinstance(user_content, str):
            user_words = len(user_content.split())
            user_text = user_content
        elif isinstance(user_content, list):
            user_words = sum(
                len(b.get("text", "").split())
                for b in user_content
                if isinstance(b, dict) and b.get("type") == "text"
            )
            user_text = " ".join(
                b.get("text", "")
                for b in user_content
                if isinstance(b, dict) and b.get("type") == "text"
            )
        else:
            user_words = 0
            user_text = ""
        # Cap at 2000 chars so observer prompt stays bounded
        user_text = user_text[:2000]

        tool_calls: list[dict] = []
        tool_errors: list[bool] = []
        model_turn_count = 0  # count assistant turns in this exchange

        for turn in exchange.get("turns", []):
            turn_type = turn.get("type")
            content = turn.get("message", {}).get("content", [])
            if not isinstance(content, list):
                continue

            if turn_type == "assistant":
                model_turn_count += 1  # each assistant message = one model turn
                for block in content:
                    if not isinstance(block, dict) or block.get("type") != "tool_use":
                        continue
                    name = block.get("name", "")
                    inp = block.get("input", {})
                    # Extract target: file_path for file tools, command for Bash
                    if name in ("Read", "Edit", "Write", "Glob", "NotebookEdit"):
                        target = inp.get("file_path", inp.get("notebook_path", ""))
                    elif name == "Bash":
                        # Use first word of command as the target (the binary/script)
                        cmd = inp.get("command", "")
                        target = cmd.split()[0] if cmd.split() else ""
                    else:
                        target = name
                    tool_calls.append({"name": name, "target": target})

            elif turn_type == "user" and turn.get("toolUseResult"):
                for block in content:
                    if not isinstance(block, dict) or block.get("type") != "tool_result":
                        continue
                    tool_errors.append(bool(block.get("is_error", False)))

        exchange_type = ClaudeCodeSource._classify_exchange(exchange, tool_calls)

        return TurnDescriptor(
            exchange_idx=exchange_idx,
            user_word_count=user_words,
            tool_calls=tool_calls,
            tool_errors=tool_errors,
            model_turn_count=model_turn_count,
            exchange_type=exchange_type,
            user_text=user_text,
        )

    @staticmethod
    def _user_text(exchange: dict[str, Any]) -> str:
        """Extract concatenated plain user text from an exchange."""
        user_msg = exchange.get("user", {})
        content = user_msg.get("message", {}).get("content", "")
        if isinstance(content, str):
            return content
        if isinstance(content, list):
            return " ".join(
                b.get("text", "")
                for b in content
                if isinstance(b, dict) and b.get("type") == "text"
            )
        return ""

    @staticmethod
    def _classify_exchange(exchange: dict[str, Any], tool_calls: list[dict]) -> str:
        """Classify a CC exchange. See design/exchange_classifier.md."""
        ut = ClaudeCodeSource._user_text(exchange)
        if ut.startswith("Base directory for this skill:"):
            return "skill_invocation"
        if "<task-notification" in ut:
            return "task_notification"
        if ut.startswith("This session is being continued"):
            return "context_continuation"
        # ScheduleWakeup tool call OR scheduledFor in any tool result
        if any(tc.get("name") == "ScheduleWakeup" for tc in tool_calls):
            return "wakeup_injection"
        for turn in exchange.get("turns", []):
            tur = turn.get("toolUseResult")
            if isinstance(tur, dict) and "scheduledFor" in tur:
                return "wakeup_injection"
        if not tool_calls:
            return "genuine_text"
        return "genuine_human"
