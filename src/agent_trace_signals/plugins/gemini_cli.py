"""Gemini CLI session parser — handles both JSON (legacy) and JSONL (streaming) formats."""

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

_READ_ONLY_TOOLS = frozenset({
    "read_file", "list_directory", "google_web_search", "web_fetch", "grep_search",
})
_WRITE_TOOLS = frozenset({"write_file", "replace"})


class GeminiSource(SourcePlugin):
    """Parse Gemini CLI session JSON files into exchanges with entity extraction."""

    source_plugin = "gemini_cli"
    source_type = "agent_trace"

    def __init__(self, config: PipelineConfig) -> None:
        self.config = config

    def can_handle(self, abs_path: str) -> bool:
        path = Path(abs_path)
        if path.suffix == ".json":
            return True
        # JSONL is the newer streaming format; guard against CC .jsonl files
        if path.suffix == ".jsonl" and ".gemini" in path.parts:
            return True
        return False

    def parse(self, abs_path: str) -> ParsedSession:
        path = Path(abs_path)
        _empty = ParsedSession(
            source_plugin=self.source_plugin,
            source_type=self.source_type,
            chunks=[],
            workspace_id=self._workspace_id(abs_path),
            metadata={"raw_entity_mentions": [], "turn_descriptors": []},
        )

        try:
            if path.suffix == ".jsonl":
                header, messages = self._load_jsonl(path)
            else:
                with open(path, "r", encoding="utf-8") as f:
                    data = json.load(f)
                if not isinstance(data, dict):
                    logger.error(f"Expected dict root in {abs_path}, got {type(data).__name__}")
                    return _empty
                header = data
                messages = data.get("messages", [])
        except Exception as e:
            logger.error(f"Error reading Gemini session {abs_path}: {e}")
            return _empty

        session_timestamp = header.get("startTime")
        project_hash = header.get("projectHash", "")
        session_id_raw = header.get("sessionId", path.stem)

        exchanges = self._build_exchanges(messages)
        chunks, entity_mentions = self._chunk_exchanges(exchanges)
        turn_descriptors = [
            self._extract_turn_descriptor(ex, idx).model_dump()
            for idx, ex in enumerate(exchanges)
        ]

        metadata: dict[str, Any] = {
            "session_id": session_id_raw,
            "project_hash": project_hash,
            "session_summary": header.get("summary", ""),
            "raw_entity_mentions": [m.model_dump() for m in entity_mentions],
            "turn_descriptors": turn_descriptors,
        }

        # Extract session-level metadata — no LLM needed
        try:
            session_meta = self._extract_session_metadata(header, messages, session_id_raw)
            metadata["session_metadata"] = session_meta.model_dump()
        except Exception as e:
            logger.warning(f"Failed to extract Gemini session metadata: {e}")

        return ParsedSession(
            source_plugin=self.source_plugin,
            source_type=self.source_type,
            session_timestamp=session_timestamp,
            workspace_id=self._workspace_id(abs_path),
            raw_facets={
                "session_id": session_id_raw,
                "project_hash": project_hash,
                "gemini_summary": header.get("summary", ""),
            },
            chunks=chunks,
            metadata=metadata,
        )

    # -------------------------------------------------------------------------
    # Private helpers
    # -------------------------------------------------------------------------

    @staticmethod
    def _load_jsonl(path: Path) -> tuple[dict[str, Any], list[dict[str, Any]]]:
        """Parse streaming JSONL format: first-line header + per-message records.

        $set delta lines are skipped — standalone message records carry full data.
        """
        header: dict[str, Any] = {}
        messages: list[dict[str, Any]] = []
        with open(path, "r", encoding="utf-8") as f:
            for i, line in enumerate(f):
                line = line.strip()
                if not line:
                    continue
                try:
                    obj = json.loads(line)
                except json.JSONDecodeError:
                    continue
                if "$set" in obj:
                    continue  # incremental delta — full records appear as standalone lines
                if i == 0 and "sessionId" in obj:
                    header = obj
                elif obj.get("type") in ("user", "gemini"):
                    messages.append(obj)
        return header, messages

    @staticmethod
    def _build_exchanges(messages: list[dict[str, Any]]) -> list[dict[str, Any]]:
        exchanges: list[dict[str, Any]] = []
        current_user: dict[str, Any] | None = None
        current_gemini: list[dict[str, Any]] = []

        for msg in messages:
            mtype = msg.get("type")
            if mtype == "user":
                if current_user is not None:
                    exchanges.append({"user": current_user, "gemini_turns": current_gemini})
                current_user = msg
                current_gemini = []
            elif mtype == "gemini":
                if current_user is not None:
                    current_gemini.append(msg)

        if current_user is not None:
            exchanges.append({"user": current_user, "gemini_turns": current_gemini})

        return exchanges

    def _chunk_exchanges(
        self, exchanges: list[dict[str, Any]]
    ) -> tuple[list[RawChunk], list[RawEntityMention]]:
        chunks: list[RawChunk] = []
        all_entities: list[RawEntityMention] = []
        chunk_index = 0
        acc_parts: list[str] = []
        acc_evidence_parts: list[str] = []
        acc_tokens = 0
        acc_start: int | None = None  # exchange index of first exchange in accumulator

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
            text = self._serialize_exchange(exchange)
            evidence_text = self._extract_evidence(exchange)
            entities = self._extract_entities(exchange, text)
            all_entities.extend(entities)

            token_count = max(1, int(len(text.split()) * 1.3))
            if token_count > self.config.max_chunk_tokens:
                # Single oversized exchange — flush accumulator then split it
                _flush_acc(exchange_idx - 1 if acc_start is not None else None)
                subs = self._split_text(text)
                for i, sub in enumerate(subs):
                    tc = max(1, int(len(sub.split()) * 1.3))
                    chunks.append(RawChunk(
                        chunk_index=chunk_index,
                        chunk_text=sub,
                        evidence_text=evidence_text if i == 0 else "",
                        token_count=tc,
                        span_start=str(exchange_idx),
                        span_end=str(exchange_idx),
                    ))
                    chunk_index += 1
            elif acc_tokens + token_count > self.config.max_chunk_tokens:
                # Adding this exchange would overflow — flush first
                _flush_acc(exchange_idx - 1)
                acc_parts.append(text)
                acc_evidence_parts.append(evidence_text)
                acc_tokens = token_count
                acc_start = exchange_idx
            else:
                acc_parts.append(text)
                acc_evidence_parts.append(evidence_text)
                acc_tokens += token_count
                if acc_start is None:
                    acc_start = exchange_idx

        _flush_acc(len(exchanges) - 1 if exchanges else None)
        return chunks, all_entities

    @staticmethod
    def _extract_evidence(exchange: dict[str, Any]) -> str:
        """Extract verbatim tool result outputs from a Gemini exchange.

        Walks each gemini turn's toolCalls and pulls the functionResponse output
        verbatim. Format: '[tool: <name>(<args>)]\\n<output>\\n\\n'.
        Cap per tool result at 4000 chars; no exchange-level cap.
        """
        EVIDENCE_CAP = 4000
        parts: list[str] = []
        for turn in exchange.get("gemini_turns", []):
            for tc in turn.get("toolCalls", []):
                name = tc.get("name", "?")
                args = tc.get("args", {}) or {}
                arg_summary = GeminiSource._summarize_tool_args(name, args)
                output_pieces: list[str] = []
                for r in tc.get("result", []) or []:
                    if not isinstance(r, dict):
                        continue
                    resp = r.get("functionResponse", {}).get("response", {})
                    output = resp.get("output", "")
                    if isinstance(output, str):
                        output_pieces.append(output)
                    elif isinstance(output, list):
                        for item in output:
                            if isinstance(item, dict):
                                t = item.get("text", "")
                                if isinstance(t, str):
                                    output_pieces.append(t)
                            elif isinstance(item, str):
                                output_pieces.append(item)
                    elif isinstance(output, dict):
                        try:
                            output_pieces.append(json.dumps(output))
                        except Exception:
                            pass
                result_text = "".join(output_pieces)[:EVIDENCE_CAP]
                if result_text:
                    parts.append(f"[tool: {name}({arg_summary})]\n{result_text}\n\n")
        return "".join(parts)

    @staticmethod
    def _serialize_exchange(exchange: dict[str, Any]) -> str:
        lines: list[str] = []

        user_content = exchange["user"].get("content", [])
        user_text = " ".join(
            item.get("text", "") for item in user_content if isinstance(item, dict)
        )[:500]
        lines.append(f"USER: {user_text}" if user_text else "USER: (no text)")

        # Cap turns to avoid unbounded serialization from long agentic chains.
        # Entity extraction (_extract_entities) still runs on the full exchange.
        _HEAD = 5
        _TAIL = 3
        turns = exchange.get("gemini_turns", [])
        if len(turns) > _HEAD + _TAIL:
            visible_turns = turns[:_HEAD] + turns[-_TAIL:]
            omitted = len(turns) - _HEAD - _TAIL
        else:
            visible_turns = turns
            omitted = 0

        for turn in visible_turns:
            for thought in turn.get("thoughts", []):
                desc = thought.get("description", "")[:150]
                if desc:
                    lines.append(f"[thought]: {desc}")

            for tc in turn.get("toolCalls", []):
                name = tc.get("name", "?")
                args = tc.get("args", {})
                arg_summary = GeminiSource._summarize_tool_args(name, args)
                lines.append(f"[TOOL: {name}({arg_summary})]")

            content = turn.get("content", "")
            if isinstance(content, str) and content.strip():
                lines.append(f"A: {content[:500]}")

            if omitted and turn is visible_turns[_HEAD - 1]:
                lines.append(f"[... {omitted} intermediate turns omitted ...]")

        return "\n".join(lines)

    @staticmethod
    def _summarize_tool_args(name: str, args: dict[str, Any]) -> str:
        if name in ("read_file", "write_file", "replace"):
            return f"file_path={args.get('file_path', '')}"
        if name == "list_directory":
            return f"path={args.get('path', '.')}"
        if name == "run_shell_command":
            return f"command={args.get('command', '')[:80]}"
        if name in ("google_web_search", "web_fetch"):
            q = args.get("query") or args.get("url") or args.get("prompt", "")
            return f"query={str(q)[:60]}"
        return json.dumps(args)[:60]

    def _extract_entities(
        self, exchange: dict[str, Any], exchange_text: str
    ) -> list[RawEntityMention]:
        entities: list[RawEntityMention] = []
        context = exchange_text[:300]

        for turn in exchange.get("gemini_turns", []):
            for tc in turn.get("toolCalls", []):
                entities.extend(self._extract_from_tool_call(tc, context))

        return entities

    @staticmethod
    def _extract_from_tool_call(
        tc: dict[str, Any], context_text: str
    ) -> list[RawEntityMention]:
        entities: list[RawEntityMention] = []
        name = tc.get("name", "")
        args = tc.get("args", {})

        entities.append(RawEntityMention(
            canonical_name=name,
            entity_type="tool",
            mention_text=name,
            context_text=context_text,
            role="tool_used",
            source_layer="tool_call",
        ))

        if name in ("read_file", "list_directory"):
            fp = args.get("file_path") or args.get("path", "")
            if fp and isinstance(fp, str):
                entities.append(RawEntityMention(
                    canonical_name=fp,
                    entity_type="file",
                    mention_text=fp,
                    context_text=context_text,
                    role="subject",
                    source_layer="tool_call",
                ))

        elif name in ("write_file", "replace"):
            fp = args.get("file_path", "")
            if fp and isinstance(fp, str):
                entities.append(RawEntityMention(
                    canonical_name=fp,
                    entity_type="file",
                    mention_text=fp,
                    context_text=context_text,
                    role="file_modified",
                    source_layer="tool_call",
                ))

        elif name == "run_shell_command":
            command = args.get("command", "")
            file_pattern = r"/[^\s\"']+\.[a-zA-Z0-9]+"
            for match in re.finditer(file_pattern, command):
                entities.append(RawEntityMention(
                    canonical_name=match.group(0),
                    entity_type="file",
                    mention_text=match.group(0),
                    context_text=context_text,
                    role="subject",
                    source_layer="tool_call",
                ))

        return entities

    def _split_text(self, text: str) -> list[str]:
        max_chars = int(self.config.max_chunk_tokens / 1.3) * 10
        overlap = int(max_chars * self.config.chunk_overlap_pct)
        chunks = []
        start = 0
        while start < len(text):
            end = min(start + max_chars, len(text))
            chunks.append(text[start:end])
            if end == len(text):
                break
            start = end - overlap
        return chunks or [text]

    @staticmethod
    def _extract_turn_descriptor(exchange: dict[str, Any], exchange_idx: int) -> TurnDescriptor:
        user_content = exchange["user"].get("content", [])
        user_words = sum(
            len(item.get("text", "").split())
            for item in user_content
            if isinstance(item, dict)
        )

        tool_calls: list[dict] = []
        tool_errors: list[bool] = []
        model_turn_count = len(exchange.get("gemini_turns", []))

        for turn in exchange.get("gemini_turns", []):
            for tc in turn.get("toolCalls", []):
                name = tc.get("name", "")
                args = tc.get("args", {})
                target = (
                    args.get("file_path")
                    or args.get("path")
                    or args.get("query")
                    or args.get("command", "")[:40]
                    or name
                )
                tool_calls.append({"name": name, "target": target})

                # JSONL format: explicit status field; JSON format: keyword scan output
                if tc.get("status") == "error":
                    has_error = True
                else:
                    has_error = False
                    for r in tc.get("result", []):
                        resp = r.get("functionResponse", {}).get("response", {})
                        output = resp.get("output", "")
                        if isinstance(output, str) and any(
                            kw in output.lower()
                            for kw in ("error", "exception", "failed", "fault", "traceback")
                        ):
                            has_error = True
                tool_errors.append(has_error)

        # avg_think_tok_ratio: mean of (thoughts / max(output, 1)) across gemini turns
        ratios: list[float] = []
        for turn in exchange.get("gemini_turns", []):
            tokens = turn.get("tokens", {})
            if isinstance(tokens, dict):
                thoughts = tokens.get("thoughts", 0) or 0
                output = tokens.get("output", 0) or 0
                ratios.append(thoughts / max(output, 1))
        avg_ratio = (sum(ratios) / len(ratios)) if ratios else 0.0

        user_text_raw = " ".join(
            item.get("text", "")
            for item in user_content
            if isinstance(item, dict)
        )
        if not user_text_raw.strip():
            exchange_type = "agent_continuation"
        elif not tool_calls:
            exchange_type = "genuine_text"
        else:
            exchange_type = "genuine_human"

        return TurnDescriptor(
            exchange_idx=exchange_idx,
            user_word_count=user_words,
            tool_calls=tool_calls,
            tool_errors=tool_errors,
            model_turn_count=model_turn_count,
            exchange_type=exchange_type,
            avg_think_tok_ratio=avg_ratio,
            user_text=user_text_raw[:2000],
        )

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
        cls,
        header: dict[str, Any],
        messages: list[dict[str, Any]],
        session_id: str,
    ) -> SessionMetadata:
        """Extract aggregate session metadata from Gemini header and messages."""
        from collections import Counter

        start_time = header.get("startTime")
        end_time = header.get("lastUpdated")
        duration_ms: int | None = None
        if start_time and end_time:
            s = cls._parse_iso8601(start_time)
            e = cls._parse_iso8601(end_time)
            if s is not None and e is not None:
                duration_ms = int((e - s) * 1000)

        models: list[str] = []
        user_turn_count = 0
        assistant_turn_count = 0
        tool_call_count = 0
        total_input = 0
        total_output = 0
        total_cache_read = 0
        total_thought = 0

        for msg in messages:
            mtype = msg.get("type")
            if mtype == "user":
                user_turn_count += 1
            elif mtype == "gemini":
                assistant_turn_count += 1
                model = msg.get("model")
                if model:
                    models.append(model)
                tokens = msg.get("tokens", {})
                if isinstance(tokens, dict):
                    total_input += tokens.get("input", 0) or 0
                    total_output += tokens.get("output", 0) or 0
                    total_cache_read += tokens.get("cached", 0) or 0
                    total_thought += tokens.get("thoughts", 0) or 0
                elif isinstance(tokens, (int, float)):
                    total_output += int(tokens)
                for tc in msg.get("toolCalls", []):
                    tool_call_count += 1

        dominant_model: str | None = Counter(models).most_common(1)[0][0] if models else None

        return SessionMetadata(
            session_id=session_id,
            dominant_model=dominant_model,
            models_used=list(dict.fromkeys(models)),
            entrypoint=None,
            permission_mode=None,
            user_type=None,
            is_sidechain=False,
            cc_version=None,
            user_turn_count=user_turn_count,
            assistant_turn_count=assistant_turn_count,
            tool_call_count=tool_call_count,
            start_time=start_time,
            end_time=end_time,
            duration_ms=duration_ms,
            total_input_tokens=total_input,
            total_output_tokens=total_output,
            total_cache_read_tokens=total_cache_read,
            total_cache_creation_tokens=0,
            total_thought_tokens=total_thought,
            cwd=None,
            git_branch=None,
        )

    @staticmethod
    def _workspace_id(abs_path: str) -> str:
        parts = Path(abs_path).parts
        for i, part in enumerate(parts):
            if part == ".gemini" and i + 2 < len(parts) and parts[i + 1] == "tmp":
                return parts[i + 2]
        # Fallback: for archive paths (data/archive/gemini_cli/<project_dir>/<file>),
        # parent.name is the project_dir. Original paths under .gemini/tmp are handled
        # above; everything else uses the immediate parent directory.
        return Path(abs_path).parent.name
