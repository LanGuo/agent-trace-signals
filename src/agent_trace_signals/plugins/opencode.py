"""Opencode session parser — reads directly from the opencode SQLite database.

Virtual path format: ``{db_path}::{session_id}``
e.g. ``/home/user/.local/share/opencode/opencode.db::ses_1abc...``

The scanner generates these virtual paths so the ingestion pipeline can treat
each session as a first-class unit with its own content hash.
"""

from __future__ import annotations
import json
import logging
import re
import sqlite3
from pathlib import Path
from typing import Any

from agent_trace_signals.config import PipelineConfig
from agent_trace_signals.models import ParsedSession, RawChunk, RawEntityMention, SessionMetadata, TurnDescriptor
from agent_trace_signals.plugins.base import SourcePlugin

logger = logging.getLogger(__name__)

_OPENCODE_DB_NAME = "opencode.db"
_SEP = "::"

# Tool names that indicate file reads vs. writes
_READ_TOOLS = frozenset({"read", "list"})
_WRITE_TOOLS = frozenset({"write", "edit", "patch"})
_SHELL_TOOLS = frozenset({"bash", "shell", "run", "execute"})


def split_virtual_path(abs_path: str) -> tuple[str, str] | None:
    """Split ``db_path::session_id`` → (db_path, session_id). Returns None if not a virtual path."""
    if _SEP not in abs_path:
        return None
    idx = abs_path.rfind(_SEP)
    return abs_path[:idx], abs_path[idx + len(_SEP):]


class OpencodeSource(SourcePlugin):
    """Parse an opencode session from its SQLite database."""

    source_plugin = "opencode"
    source_type = "agent_trace"

    def __init__(self, config: PipelineConfig) -> None:
        self.config = config

    def can_handle(self, abs_path: str) -> bool:
        parts = split_virtual_path(abs_path)
        if parts is None:
            return False
        db_path, session_id = parts
        return Path(db_path).name == _OPENCODE_DB_NAME and session_id.startswith("ses_")

    def parse(self, abs_path: str) -> ParsedSession:
        parts = split_virtual_path(abs_path)
        _empty = ParsedSession(
            source_plugin=self.source_plugin,
            source_type=self.source_type,
            chunks=[],
            workspace_id="unknown",
            metadata={"raw_entity_mentions": [], "turn_descriptors": []},
        )

        if parts is None:
            logger.error(f"Invalid opencode virtual path: {abs_path}")
            return _empty

        db_path, session_id = parts

        try:
            conn = sqlite3.connect(db_path, timeout=10.0)
            conn.row_factory = sqlite3.Row
        except Exception as e:
            logger.error(f"Cannot open opencode DB {db_path}: {e}")
            return _empty

        try:
            return self._parse_session(conn, session_id, db_path)
        except Exception as e:
            raise RuntimeError(f"Error parsing opencode session {session_id}: {e}") from e
        finally:
            conn.close()

    # -------------------------------------------------------------------------
    # Private helpers
    # -------------------------------------------------------------------------

    def _parse_session(self, conn: sqlite3.Connection, session_id: str, db_path: str) -> ParsedSession:
        session_row = conn.execute(
            "SELECT * FROM session WHERE id = ?", (session_id,)
        ).fetchone()

        if session_row is None:
            logger.error(f"Session {session_id} not found in DB")
            return ParsedSession(
                source_plugin=self.source_plugin,
                source_type=self.source_type,
                chunks=[],
                workspace_id="unknown",
                metadata={"raw_entity_mentions": [], "turn_descriptors": []},
            )

        messages = conn.execute(
            "SELECT id, data FROM message WHERE session_id = ? ORDER BY time_created, id",
            (session_id,),
        ).fetchall()

        # Build map: message_id → list of part dicts
        parts_by_msg: dict[str, list[dict[str, Any]]] = {}
        for part_row in conn.execute(
            "SELECT message_id, data FROM part WHERE session_id = ? ORDER BY time_created, id",
            (session_id,),
        ).fetchall():
            mid = part_row["message_id"]
            try:
                part_data = json.loads(part_row["data"])
            except Exception:
                continue
            parts_by_msg.setdefault(mid, []).append(part_data)

        exchanges = self._build_exchanges(messages, parts_by_msg)
        chunks, entity_mentions = self._chunk_exchanges(exchanges)
        turn_descriptors = [
            self._extract_turn_descriptor(ex, idx).model_dump()
            for idx, ex in enumerate(exchanges)
        ]

        # Session timestamp: epoch ms → ISO8601
        time_created_ms = session_row["time_created"]
        session_timestamp: str | None = None
        if time_created_ms:
            from datetime import datetime, timezone
            session_timestamp = datetime.fromtimestamp(
                time_created_ms / 1000, tz=timezone.utc
            ).isoformat()

        model_raw = session_row["model"]
        model_id: str | None = None
        provider_id: str | None = None
        if model_raw:
            try:
                m = json.loads(model_raw)
                model_id = m.get("id") or m.get("modelID")
                provider_id = m.get("providerID")
            except Exception:
                model_id = str(model_raw)

        workspace_id = self._workspace_id(session_row["directory"] or "", db_path)

        metadata: dict[str, Any] = {
            "session_id": session_id,
            "raw_entity_mentions": [m.model_dump() for m in entity_mentions],
            "turn_descriptors": turn_descriptors,
        }

        try:
            session_meta = self._extract_session_metadata(session_row, messages, parts_by_msg, session_id)
            metadata["session_metadata"] = session_meta.model_dump()
        except Exception as e:
            logger.warning(f"Failed to extract opencode session metadata: {e}")

        return ParsedSession(
            source_plugin=self.source_plugin,
            source_type=self.source_type,
            session_timestamp=session_timestamp,
            workspace_id=workspace_id,
            raw_facets={
                "session_id": session_id,
                "title": session_row["title"],
                "directory": session_row["directory"],
                "model_id": model_id,
                "provider_id": provider_id,
            },
            chunks=chunks,
            metadata=metadata,
        )

    @staticmethod
    def _build_exchanges(
        messages: list[sqlite3.Row],
        parts_by_msg: dict[str, list[dict[str, Any]]],
    ) -> list[dict[str, Any]]:
        """Group messages into user→assistant exchange pairs."""
        exchanges: list[dict[str, Any]] = []
        current_user: dict[str, Any] | None = None
        current_assistant: list[dict[str, Any]] = []

        for msg_row in messages:
            try:
                msg_data = json.loads(msg_row["data"])
            except Exception:
                continue
            role = msg_data.get("role")
            msg_id = msg_row["id"]
            parts = parts_by_msg.get(msg_id, [])

            if role == "user":
                if current_user is not None:
                    exchanges.append({"user": current_user, "assistant_turns": current_assistant})
                current_user = {"data": msg_data, "parts": parts}
                current_assistant = []
            elif role == "assistant":
                if current_user is not None:
                    current_assistant.append({"data": msg_data, "parts": parts})

        if current_user is not None:
            exchanges.append({"user": current_user, "assistant_turns": current_assistant})

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
        acc_start: int | None = None  # Track exchange index of first exchange in accumulator

        def _flush(end_exchange_idx: int | None = None) -> None:
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
                acc_parts.clear()
                acc_evidence_parts.clear()
                acc_tokens = 0
                acc_start = None

        for exchange_idx, exchange in enumerate(exchanges):
            text = self._serialize_exchange(exchange)
            evidence_text = self._extract_evidence(exchange)
            entities = self._extract_entities(exchange, text)
            all_entities.extend(entities)

            token_count = max(1, int(len(text.split()) * 1.3))
            if token_count > self.config.max_chunk_tokens:
                _flush(exchange_idx - 1 if acc_start is not None else None)
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
                _flush(exchange_idx - 1)
                acc_parts.append(text)
                acc_evidence_parts.append(evidence_text)
                acc_tokens = token_count
                acc_start = exchange_idx
            else:
                if acc_start is None:
                    acc_start = exchange_idx
                acc_parts.append(text)
                acc_evidence_parts.append(evidence_text)
                acc_tokens += token_count

        _flush(len(exchanges) - 1)
        return chunks, all_entities

    @staticmethod
    def _extract_evidence(exchange: dict[str, Any]) -> str:
        """Extract verbatim tool result output from an opencode exchange.

        Walks every assistant tool part and pulls state.output (bash) or
        state.output / metadata diff (edit). Format per tool:
        '[tool: <name>(<args>)]\\n<output>\\n\\n'. Cap per tool at 4000 chars.
        """
        EVIDENCE_CAP = 4000
        parts: list[str] = []
        for turn in exchange.get("assistant_turns", []):
            for part in turn.get("parts", []):
                if not isinstance(part, dict) or part.get("type") != "tool":
                    continue
                tool_name = part.get("tool", "?")
                state = part.get("state", {}) or {}
                inp = state.get("input", {}) or {}
                arg_summary = OpencodeSource._summarize_tool_args(tool_name, inp)
                output = state.get("output", "")
                if isinstance(output, (dict, list)):
                    try:
                        output_str = json.dumps(output)
                    except Exception:
                        output_str = str(output)
                elif isinstance(output, str):
                    output_str = output
                else:
                    output_str = "" if output is None else str(output)
                result_text = output_str[:EVIDENCE_CAP]
                if result_text:
                    parts.append(f"[tool: {tool_name}({arg_summary})]\n{result_text}\n\n")
        return "".join(parts)

    @staticmethod
    def _serialize_exchange(exchange: dict[str, Any]) -> str:
        lines: list[str] = []

        user_parts = exchange["user"].get("parts", [])
        user_text = " ".join(
            p.get("text", "") for p in user_parts if isinstance(p, dict) and p.get("type") == "text"
        )[:500]
        lines.append(f"USER: {user_text}" if user_text else "USER: (no text)")

        for turn in exchange.get("assistant_turns", []):
            for part in turn.get("parts", []):
                if not isinstance(part, dict):
                    continue
                ptype = part.get("type")
                if ptype == "text":
                    text = part.get("text", "").strip()[:500]
                    if text:
                        lines.append(f"A: {text}")
                elif ptype == "reasoning":
                    reasoning = part.get("text", "").strip()[:150]
                    if reasoning:
                        lines.append(f"[reasoning]: {reasoning}")
                elif ptype == "tool":
                    tool_name = part.get("tool", "?")
                    state = part.get("state", {})
                    inp = state.get("input", {})
                    arg_summary = OpencodeSource._summarize_tool_args(tool_name, inp)
                    lines.append(f"[TOOL: {tool_name}({arg_summary})]")

        return "\n".join(lines)

    @staticmethod
    def _summarize_tool_args(tool_name: str, args: dict[str, Any]) -> str:
        lower = tool_name.lower()
        if lower in ("read", "write", "edit"):
            fp = args.get("filePath") or args.get("file_path") or args.get("path", "")
            return f"path={fp}"
        if lower in ("bash", "shell", "run", "execute"):
            cmd = args.get("command", args.get("cmd", ""))
            return f"command={str(cmd)[:80]}"
        if lower == "list":
            return f"path={args.get('path', '.')}"
        return json.dumps(args)[:60]

    def _extract_entities(
        self, exchange: dict[str, Any], exchange_text: str
    ) -> list[RawEntityMention]:
        entities: list[RawEntityMention] = []
        context = exchange_text[:300]

        for turn in exchange.get("assistant_turns", []):
            for part in turn.get("parts", []):
                if isinstance(part, dict) and part.get("type") == "tool":
                    entities.extend(self._extract_from_tool_part(part, context))

        return entities

    @staticmethod
    def _extract_from_tool_part(
        part: dict[str, Any], context_text: str
    ) -> list[RawEntityMention]:
        entities: list[RawEntityMention] = []
        tool_name = part.get("tool", "")
        state = part.get("state", {})
        args = state.get("input", {})
        lower = tool_name.lower()

        entities.append(RawEntityMention(
            canonical_name=tool_name,
            entity_type="tool",
            mention_text=tool_name,
            context_text=context_text,
            role="tool_used",
            source_layer="tool_call",
        ))

        if lower in ("read", "list"):
            fp = args.get("filePath") or args.get("file_path") or args.get("path", "")
            if fp and isinstance(fp, str):
                entities.append(RawEntityMention(
                    canonical_name=fp,
                    entity_type="file",
                    mention_text=fp,
                    context_text=context_text,
                    role="subject",
                    source_layer="tool_call",
                ))

        elif lower in ("write", "edit", "patch"):
            fp = args.get("filePath") or args.get("file_path") or args.get("path", "")
            if fp and isinstance(fp, str):
                entities.append(RawEntityMention(
                    canonical_name=fp,
                    entity_type="file",
                    mention_text=fp,
                    context_text=context_text,
                    role="file_modified",
                    source_layer="tool_call",
                ))

        elif lower in ("bash", "shell", "run", "execute"):
            command = args.get("command", args.get("cmd", ""))
            if isinstance(command, str):
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
            if end >= len(text):
                break
            start = end - overlap
        return chunks or [text]

    @staticmethod
    def _extract_turn_descriptor(exchange: dict[str, Any], exchange_idx: int) -> TurnDescriptor:
        user_parts = exchange["user"].get("parts", [])
        user_words = sum(
            len(p.get("text", "").split())
            for p in user_parts
            if isinstance(p, dict) and p.get("type") == "text"
        )

        tool_calls: list[dict] = []
        tool_errors: list[bool] = []
        model_turn_count = len(exchange.get("assistant_turns", []))

        for turn in exchange.get("assistant_turns", []):
            for part in turn.get("parts", []):
                if not isinstance(part, dict) or part.get("type") != "tool":
                    continue
                tool_name = part.get("tool", "")
                state = part.get("state", {})
                args = state.get("input", {})
                target = (
                    args.get("filePath")
                    or args.get("file_path")
                    or args.get("path")
                    or args.get("command", "")[:40]
                    or tool_name
                )
                tool_calls.append({"name": tool_name, "target": target})

                status = state.get("status", "")
                has_error = status == "error"
                if not has_error:
                    output = state.get("output", "")
                    if isinstance(output, str) and any(
                        kw in output.lower()
                        for kw in ("error", "exception", "failed", "fault", "traceback")
                    ):
                        has_error = True
                tool_errors.append(has_error)

        has_text_parts = any(
            isinstance(p, dict) and p.get("type") == "text" for p in user_parts
        )
        user_data = exchange["user"].get("data", {})
        if not isinstance(user_data, dict):
            user_data = {}
        summary = user_data.get("summary") if isinstance(user_data.get("summary"), dict) else {}
        has_summary_diff = bool(summary) and "diffs" in summary
        if has_summary_diff and not has_text_parts:
            exchange_type = "summary_diff"
        elif not tool_calls:
            exchange_type = "genuine_text"
        else:
            exchange_type = "genuine_human"

        user_text = " ".join(
            p.get("text", "") for p in user_parts
            if isinstance(p, dict) and p.get("type") == "text"
        )[:2000]

        return TurnDescriptor(
            exchange_idx=exchange_idx,
            user_word_count=user_words,
            tool_calls=tool_calls,
            tool_errors=tool_errors,
            model_turn_count=model_turn_count,
            exchange_type=exchange_type,
            user_text=user_text,
        )

    @classmethod
    def _extract_session_metadata(
        cls,
        session_row: sqlite3.Row,
        messages: list[sqlite3.Row],
        parts_by_msg: dict[str, list[dict[str, Any]]],
        session_id: str,
    ) -> SessionMetadata:
        from collections import Counter

        model_raw = session_row["model"]
        model_id: str | None = None
        if model_raw:
            try:
                m = json.loads(model_raw)
                model_id = m.get("id") or m.get("modelID")
            except Exception:
                model_id = str(model_raw)

        dominant_model = model_id
        models_used = [model_id] if model_id else []

        user_turn_count = 0
        assistant_turn_count = 0
        tool_call_count = 0
        models_seen: list[str] = []

        for msg_row in messages:
            try:
                msg_data = json.loads(msg_row["data"])
            except Exception:
                continue
            role = msg_data.get("role")
            if role == "user":
                user_turn_count += 1
            elif role == "assistant":
                assistant_turn_count += 1
                mid = msg_row["id"]
                for part in parts_by_msg.get(mid, []):
                    if isinstance(part, dict) and part.get("type") == "tool":
                        tool_call_count += 1
                # per-message model info if present
                msg_model = msg_data.get("modelID") or msg_data.get("model")
                if isinstance(msg_model, str):
                    models_seen.append(msg_model)
                elif isinstance(msg_model, dict):
                    mid_str = msg_model.get("id") or msg_model.get("modelID")
                    if mid_str:
                        models_seen.append(mid_str)

        if models_seen:
            dominant_model = Counter(models_seen).most_common(1)[0][0]
            models_used = list(dict.fromkeys(models_seen))

        time_created_ms = session_row["time_created"]
        time_updated_ms = session_row["time_updated"]
        start_time: str | None = None
        end_time: str | None = None
        duration_ms: int | None = None

        if time_created_ms and time_updated_ms:
            from datetime import datetime, timezone
            start_time = datetime.fromtimestamp(time_created_ms / 1000, tz=timezone.utc).isoformat()
            end_time = datetime.fromtimestamp(time_updated_ms / 1000, tz=timezone.utc).isoformat()
            duration_ms = time_updated_ms - time_created_ms

        return SessionMetadata(
            session_id=session_id,
            dominant_model=dominant_model,
            models_used=models_used,
            entrypoint=None,
            permission_mode=session_row["permission"],
            user_type=None,
            is_sidechain=session_row["parent_id"] is not None,
            cc_version=session_row["version"],
            user_turn_count=user_turn_count,
            assistant_turn_count=assistant_turn_count,
            tool_call_count=tool_call_count,
            start_time=start_time,
            end_time=end_time,
            duration_ms=duration_ms,
            total_input_tokens=session_row["tokens_input"] or 0,
            total_output_tokens=session_row["tokens_output"] or 0,
            total_cache_read_tokens=session_row["tokens_cache_read"] or 0,
            total_cache_creation_tokens=session_row["tokens_cache_write"] or 0,
            total_thought_tokens=session_row["tokens_reasoning"] or 0,
            cwd=session_row["directory"],
            git_branch=None,
        )

    @staticmethod
    def _workspace_id(directory: str, db_path: str) -> str:
        if directory:
            return Path(directory).name
        return Path(db_path).parent.name
