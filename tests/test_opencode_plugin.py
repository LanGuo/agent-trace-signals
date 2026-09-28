"""Tests for the opencode source plugin — parsing and chunking, no Ollama needed."""

from __future__ import annotations
import json
import sqlite3
from pathlib import Path

import pytest

from agent_trace_signals.config import PipelineConfig
from agent_trace_signals.plugins.opencode import OpencodeSource, split_virtual_path

_SEP = "::"


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_db(path: Path) -> sqlite3.Connection:
    """Create a minimal opencode.db with one session and a few messages."""
    conn = sqlite3.connect(str(path))
    conn.executescript("""
        CREATE TABLE session (
            id TEXT PRIMARY KEY,
            title TEXT,
            directory TEXT,
            model TEXT,
            permission TEXT,
            parent_id TEXT,
            version TEXT,
            time_created INTEGER,
            time_updated INTEGER,
            time_archived INTEGER,
            tokens_input INTEGER,
            tokens_output INTEGER,
            tokens_cache_read INTEGER,
            tokens_cache_write INTEGER,
            tokens_reasoning INTEGER
        );
        CREATE TABLE message (
            id TEXT PRIMARY KEY,
            session_id TEXT,
            data TEXT,
            time_created INTEGER
        );
        CREATE TABLE part (
            id TEXT PRIMARY KEY,
            session_id TEXT,
            message_id TEXT,
            data TEXT,
            time_created INTEGER
        );
    """)
    return conn


def _insert_session(conn, session_id="ses_abc123", directory="/home/user/myproject"):
    conn.execute(
        "INSERT INTO session VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
        (session_id, "Test session", directory,
         json.dumps({"id": "claude-3-sonnet", "providerID": "anthropic"}),
         "bypassPermissions", None, "1.0",
         1_700_000_000_000, 1_700_010_000_000, None,
         100, 200, 50, 30, 10),
    )
    conn.commit()


def _insert_exchange(conn, session_id, user_msg_id, asst_msg_id,
                      user_text="what is the bug?",
                      assistant_text="The bug is in foo.py line 42."):
    """Insert a user message + assistant message pair with parts."""
    conn.execute(
        "INSERT INTO message VALUES (?,?,?,?)",
        (user_msg_id, session_id,
         json.dumps({"role": "user", "content": user_text}), 1_700_000_001_000),
    )
    conn.execute(
        "INSERT INTO part VALUES (?,?,?,?,?)",
        (f"p_{user_msg_id}", session_id, user_msg_id,
         json.dumps({"type": "text", "text": user_text}), 1_700_000_001_000),
    )
    conn.execute(
        "INSERT INTO message VALUES (?,?,?,?)",
        (asst_msg_id, session_id,
         json.dumps({"role": "assistant"}), 1_700_000_002_000),
    )
    conn.execute(
        "INSERT INTO part VALUES (?,?,?,?,?)",
        (f"p_{asst_msg_id}", session_id, asst_msg_id,
         json.dumps({"type": "text", "text": assistant_text}), 1_700_000_002_000),
    )
    conn.commit()


def _insert_tool_exchange(conn, session_id, user_msg_id, asst_msg_id, tool_name="read",
                           file_path="/home/user/myproject/foo.py"):
    """Insert an exchange where the assistant uses a tool."""
    conn.execute(
        "INSERT INTO message VALUES (?,?,?,?)",
        (user_msg_id, session_id,
         json.dumps({"role": "user", "content": "show me foo.py"}), 1_700_000_003_000),
    )
    conn.execute(
        "INSERT INTO part VALUES (?,?,?,?,?)",
        (f"p_{user_msg_id}", session_id, user_msg_id,
         json.dumps({"type": "text", "text": "show me foo.py"}), 1_700_000_003_000),
    )
    conn.execute(
        "INSERT INTO message VALUES (?,?,?,?)",
        (asst_msg_id, session_id,
         json.dumps({"role": "assistant"}), 1_700_000_004_000),
    )
    conn.execute(
        "INSERT INTO part VALUES (?,?,?,?,?)",
        (f"p_{asst_msg_id}_tool", session_id, asst_msg_id,
         json.dumps({
             "type": "tool", "tool": tool_name,
             "state": {"input": {"filePath": file_path}, "status": "success", "output": "content"},
         }), 1_700_000_004_000),
    )
    conn.commit()


# ---------------------------------------------------------------------------
# split_virtual_path
# ---------------------------------------------------------------------------

def test_split_virtual_path_valid():
    db = "/home/user/.local/share/opencode/opencode.db"
    sid = "ses_1abc"
    result = split_virtual_path(f"{db}{_SEP}{sid}")
    assert result == (db, sid)


def test_split_virtual_path_invalid():
    assert split_virtual_path("/some/normal/path.json") is None


def test_split_virtual_path_no_double_sep_in_db_path():
    # DB paths should not contain "::" — single sep
    result = split_virtual_path("/home/opencode.db::ses_abc")
    assert result is not None
    assert result[1] == "ses_abc"


# ---------------------------------------------------------------------------
# can_handle
# ---------------------------------------------------------------------------

def test_can_handle_valid():
    cfg = PipelineConfig()
    plugin = OpencodeSource(cfg)
    assert plugin.can_handle("/home/user/.local/share/opencode/opencode.db::ses_1abc")


def test_can_handle_wrong_db_name():
    cfg = PipelineConfig()
    plugin = OpencodeSource(cfg)
    assert not plugin.can_handle("/home/user/.local/share/opencode/other.db::ses_1abc")


def test_can_handle_wrong_session_prefix():
    cfg = PipelineConfig()
    plugin = OpencodeSource(cfg)
    assert not plugin.can_handle("/home/user/opencode.db::msg_1abc")


# ---------------------------------------------------------------------------
# parse — full round-trip with real SQLite
# ---------------------------------------------------------------------------

@pytest.fixture
def opencode_db(tmp_path):
    db_path = tmp_path / "opencode.db"
    conn = _make_db(db_path)
    _insert_session(conn)
    _insert_exchange(conn, "ses_abc123", "msg_user1", "msg_asst1")
    _insert_tool_exchange(conn, "ses_abc123", "msg_user2", "msg_asst2")
    conn.close()
    return db_path


def test_parse_returns_chunks(opencode_db):
    cfg = PipelineConfig()
    plugin = OpencodeSource(cfg)
    virtual_path = f"{opencode_db}::ses_abc123"

    result = plugin.parse(virtual_path)

    assert result.source_plugin == "opencode"
    assert result.source_type == "agent_trace"
    assert len(result.chunks) >= 1
    assert result.workspace_id == "myproject"


def test_parse_extracts_file_entities(opencode_db):
    cfg = PipelineConfig()
    plugin = OpencodeSource(cfg)
    virtual_path = f"{opencode_db}::ses_abc123"

    result = plugin.parse(virtual_path)

    mentions = result.metadata.get("raw_entity_mentions", [])
    file_mentions = [m for m in mentions if m["entity_type"] == "file"]
    assert any("/home/user/myproject/foo.py" in m["canonical_name"] for m in file_mentions)


def test_parse_missing_session_returns_empty(opencode_db):
    cfg = PipelineConfig()
    plugin = OpencodeSource(cfg)
    virtual_path = f"{opencode_db}::ses_doesnotexist"

    result = plugin.parse(virtual_path)

    assert result.chunks == []


def test_parse_bad_db_path_returns_empty():
    cfg = PipelineConfig()
    plugin = OpencodeSource(cfg)
    virtual_path = "/nonexistent/opencode.db::ses_abc123"

    result = plugin.parse(virtual_path)

    assert result.chunks == []


def test_parse_session_metadata_present(opencode_db):
    cfg = PipelineConfig()
    plugin = OpencodeSource(cfg)
    virtual_path = f"{opencode_db}::ses_abc123"

    result = plugin.parse(virtual_path)

    meta = result.metadata.get("session_metadata", {})
    assert meta.get("session_id") is not None
    assert meta.get("user_turn_count", 0) >= 1


def test_parse_turn_descriptors(opencode_db):
    cfg = PipelineConfig()
    plugin = OpencodeSource(cfg)
    virtual_path = f"{opencode_db}::ses_abc123"

    result = plugin.parse(virtual_path)

    tds = result.metadata.get("turn_descriptors", [])
    assert len(tds) >= 1
    # Exchange with a read tool call should record it
    tool_td = next((td for td in tds if td.get("tool_calls")), None)
    assert tool_td is not None
    assert any(tc["name"] == "read" for tc in tool_td["tool_calls"])


def test_chunk_text_contains_user_message(opencode_db):
    cfg = PipelineConfig()
    plugin = OpencodeSource(cfg)
    virtual_path = f"{opencode_db}::ses_abc123"

    result = plugin.parse(virtual_path)

    all_text = "\n".join(c.chunk_text for c in result.chunks)
    assert "what is the bug?" in all_text


# ---------------------------------------------------------------------------
# DChunkAnalyzer.analyze_batch — mock provider, no Ollama
# ---------------------------------------------------------------------------

def test_analyze_batch_parallel_preserves_order():
    """analyze_batch must return results in the same order as input records."""
    from unittest.mock import MagicMock
    from agent_trace_signals.pipeline.chunk_analyzer import DChunkAnalyzer
    from agent_trace_signals.models import Record, Session

    call_order = []

    def fake_complete_json(prompt, schema, model, **kwargs):
        import re
        # Extract chunk index from the text
        m = re.search(r"chunk(\d+)", prompt)
        idx = int(m.group(1)) if m else 0
        call_order.append(idx)
        return {"summary": f"summary{idx}", "entities": [], "memories": []}

    provider = MagicMock()
    provider.complete_json.side_effect = fake_complete_json

    cfg_mock = MagicMock()
    cfg_mock.chunk_summarizer = "test-model"
    cfg_mock.ollama_num_ctx = 4096

    analyzer = DChunkAnalyzer(provider, cfg_mock)

    records = [
        Record(id=f"r{i}", session_id="s1", chunk_index=i,
               chunk_text=f"chunk{i} " * 50, token_count=100)
        for i in range(6)
    ]
    session = Session(id="s1", source_type="agent_trace", source_plugin="opencode",
                      workspace_id="test")

    results = analyzer.analyze_batch(records, session, max_workers=3)

    assert len(results) == 6
    for i, res in enumerate(results):
        assert res.summary == f"summary{i}", f"result {i} out of order"


class TestOpencodeExchangeClassifier:
    def test_summary_diff(self):
        from agent_trace_signals.plugins.opencode import OpencodeSource
        ex = {
            "user": {
                "data": {"summary": {"diffs": [{"file": "a.py"}]}},
                "parts": [],
            },
            "assistant_turns": [],
        }
        td = OpencodeSource._extract_turn_descriptor(ex, 0)
        assert td.exchange_type == "summary_diff"

    def test_genuine_text_no_tools(self):
        from agent_trace_signals.plugins.opencode import OpencodeSource
        ex = {
            "user": {"data": {}, "parts": [{"type": "text", "text": "hi"}]},
            "assistant_turns": [{"parts": [{"type": "text", "text": "hello"}]}],
        }
        td = OpencodeSource._extract_turn_descriptor(ex, 0)
        assert td.exchange_type == "genuine_text"

    def test_genuine_human_with_tools(self):
        from agent_trace_signals.plugins.opencode import OpencodeSource
        ex = {
            "user": {"data": {}, "parts": [{"type": "text", "text": "read"}]},
            "assistant_turns": [{"parts": [
                {"type": "tool", "tool": "read", "state": {"input": {"filePath": "/a"}}}
            ]}],
        }
        td = OpencodeSource._extract_turn_descriptor(ex, 0)
        assert td.exchange_type == "genuine_human"


# ---------------------------------------------------------------------------
# evidence_text extraction (Stage 1 of trajectory_signals)
# ---------------------------------------------------------------------------

def test_evidence_text_captures_bash_output_opencode():
    from agent_trace_signals.plugins.opencode import OpencodeSource
    exchange = {
        "user": {"parts": [{"type": "text", "text": "run ls"}]},
        "assistant_turns": [{
            "parts": [{
                "type": "tool",
                "tool": "bash",
                "state": {
                    "input": {"command": "ls /tmp"},
                    "output": "file1.txt\nfile2.txt\nfile3.txt",
                },
            }],
        }],
    }
    ev = OpencodeSource._extract_evidence(exchange)
    assert "file1.txt" in ev
    assert "file3.txt" in ev
    assert "[tool: bash(" in ev


def test_evidence_text_empty_for_textonly_opencode():
    from agent_trace_signals.plugins.opencode import OpencodeSource
    exchange = {
        "user": {"parts": [{"type": "text", "text": "hi"}]},
        "assistant_turns": [{"parts": [{"type": "text", "text": "hello"}]}],
    }
    assert OpencodeSource._extract_evidence(exchange) == ""
