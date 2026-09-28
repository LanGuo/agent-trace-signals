"""Tests for GeminiSource plugin."""
import json
import pytest
from agent_trace_signals.config import PipelineConfig
from agent_trace_signals.plugins.gemini_cli import GeminiSource


@pytest.fixture
def plugin():
    return GeminiSource(PipelineConfig())


@pytest.fixture
def minimal_session(tmp_path):
    """Minimal valid Gemini session JSON."""
    data = {
        "sessionId": "sess-abc123",
        "projectHash": "proj-xyz",
        "startTime": "2026-03-03T02:28:00Z",
        "lastUpdated": "2026-03-03T03:00:00Z",
        "kind": "chat",
        "summary": "Hybrid search prototype review",
        "messages": [
            {
                "id": "msg-1",
                "timestamp": "2026-03-03T02:28:01Z",
                "type": "user",
                "content": [{"text": "please review this codebase"}],
                "toolCalls": [],
                "thoughts": [],
            },
            {
                "id": "msg-2",
                "timestamp": "2026-03-03T02:28:10Z",
                "type": "gemini",
                "content": "",
                "toolCalls": [
                    {
                        "id": "read_file_1_0",
                        "name": "read_file",
                        "args": {"file_path": "src/engine.py"},
                        "result": [{"functionResponse": {"id": "read_file_1_0", "name": "read_file", "response": {"output": "class HybridSearchEngine: pass"}}}],
                    }
                ],
                "thoughts": [{"subject": "Reading engine", "description": "I will read the engine file.", "timestamp": "2026-03-03T02:28:05Z"}],
                "model": "gemini-2.5-pro",
                "tokens": 100,
            },
            {
                "id": "msg-3",
                "timestamp": "2026-03-03T02:28:20Z",
                "type": "gemini",
                "content": "The engine implements SSHS with GLiNER2 and BGE-M3.",
                "toolCalls": [],
                "thoughts": [],
                "model": "gemini-2.5-pro",
                "tokens": 200,
            },
            {
                "id": "msg-4",
                "timestamp": "2026-03-03T02:29:00Z",
                "type": "user",
                "content": [{"text": "what are the performance issues?"}],
                "toolCalls": [],
                "thoughts": [],
            },
            {
                "id": "msg-5",
                "timestamp": "2026-03-03T02:29:10Z",
                "type": "gemini",
                "content": "",
                "toolCalls": [
                    {
                        "id": "run_shell_1_0",
                        "name": "run_shell_command",
                        "args": {"command": "python src/benchmark.py --top-k 5", "description": "Run benchmark"},
                        "result": [{"functionResponse": {"id": "run_shell_1_0", "name": "run_shell_command", "response": {"output": "Segmentation fault"}}}],
                    }
                ],
                "thoughts": [],
                "model": "gemini-2.5-pro",
                "tokens": 50,
            },
            {
                "id": "msg-6",
                "timestamp": "2026-03-03T02:29:30Z",
                "type": "gemini",
                "content": "There is a segmentation fault during GLiNER2 tagging.",
                "toolCalls": [],
                "thoughts": [],
                "model": "gemini-2.5-pro",
                "tokens": 150,
            },
        ],
    }
    p = tmp_path / "session-2026-03-03T02-28-abc123.json"
    p.write_text(json.dumps(data))
    return str(p)


class TestCanHandle:
    def test_accepts_json_file(self, plugin, tmp_path):
        p = tmp_path / "session-2026-03-03T02-28-abc123.json"
        p.write_text("{}")
        assert plugin.can_handle(str(p))

    def test_rejects_jsonl(self, plugin, tmp_path):
        p = tmp_path / "session.jsonl"
        p.write_text("{}")
        assert not plugin.can_handle(str(p))

    def test_rejects_non_json(self, plugin, tmp_path):
        p = tmp_path / "notes.txt"
        p.write_text("hello")
        assert not plugin.can_handle(str(p))


class TestParse:
    def test_returns_parsed_session(self, plugin, minimal_session):
        result = plugin.parse(minimal_session)
        assert result.source_plugin == "gemini_cli"
        assert result.source_type == "agent_trace"

    def test_session_timestamp_from_starttime(self, plugin, minimal_session):
        result = plugin.parse(minimal_session)
        assert result.session_timestamp == "2026-03-03T02:28:00Z"

    def test_chunks_one_per_user_exchange(self, plugin, minimal_session):
        result = plugin.parse(minimal_session)
        # 2 exchanges; small ones are packed into 1 chunk — verify both appear
        assert len(result.chunks) >= 1
        full_text = " ".join(c.chunk_text for c in result.chunks)
        assert "please review" in full_text
        assert "performance issues" in full_text

    def test_chunk_text_contains_user_and_gemini(self, plugin, minimal_session):
        result = plugin.parse(minimal_session)
        text = result.chunks[0].chunk_text
        assert "USER:" in text
        assert "please review" in text

    def test_chunk_text_contains_tool_call(self, plugin, minimal_session):
        result = plugin.parse(minimal_session)
        text = result.chunks[0].chunk_text
        assert "read_file" in text or "src/engine.py" in text

    def test_read_file_extracted_as_file_entity(self, plugin, minimal_session):
        result = plugin.parse(minimal_session)
        mentions = result.metadata.get("raw_entity_mentions", [])
        file_mentions = [m for m in mentions if m["entity_type"] == "file"]
        names = [m["canonical_name"] for m in file_mentions]
        assert "src/engine.py" in names

    def test_shell_command_extracts_file_path(self, plugin, minimal_session):
        result = plugin.parse(minimal_session)
        mentions = result.metadata.get("raw_entity_mentions", [])
        file_mentions = [m for m in mentions if m["entity_type"] == "file"]
        names = [m["canonical_name"] for m in file_mentions]
        assert any("benchmark" in n for n in names)

    def test_workspace_id_not_empty(self, plugin, minimal_session):
        result = plugin.parse(minimal_session)
        assert result.workspace_id != ""

    def test_turn_descriptors_populated(self, plugin, minimal_session):
        result = plugin.parse(minimal_session)
        tds = result.metadata.get("turn_descriptors", [])
        assert len(tds) >= 2

    def test_model_turn_count_reflects_gemini_turns(self, plugin, minimal_session):
        result = plugin.parse(minimal_session)
        tds = result.metadata.get("turn_descriptors", [])
        # First exchange has 2 gemini turns (msg-2 with toolCall + msg-3 with content)
        assert tds[0]["model_turn_count"] == 2

    def test_write_file_extracted_as_modified_entity(self, plugin, tmp_path):
        data = {
            "sessionId": "s1", "projectHash": "p1",
            "startTime": "2026-01-01T00:00:00Z", "lastUpdated": "2026-01-01T00:01:00Z",
            "kind": "chat", "summary": "", "messages": [
                {"id": "u1", "timestamp": "2026-01-01T00:00:01Z", "type": "user",
                 "content": [{"text": "write a file"}], "toolCalls": [], "thoughts": []},
                {"id": "g1", "timestamp": "2026-01-01T00:00:10Z", "type": "gemini",
                 "content": "", "toolCalls": [
                     {"id": "w1", "name": "write_file",
                      "args": {"file_path": "output/result.json", "content": "{}"},
                      "result": []}
                 ], "thoughts": [], "model": "gemini-2.5-pro", "tokens": 10},
            ]
        }
        p = tmp_path / "sess.json"
        p.write_text(json.dumps(data))
        result = plugin.parse(str(p))
        mentions = result.metadata.get("raw_entity_mentions", [])
        modified = [m for m in mentions if m["role"] == "file_modified"]
        assert any("output/result.json" in m["canonical_name"] for m in modified)

    def test_shell_command_error_detected(self, plugin, minimal_session):
        """run_shell_command with 'Segmentation fault' in output → tool_error=True."""
        result = plugin.parse(minimal_session)
        tds = result.metadata.get("turn_descriptors", [])
        # Second exchange has a shell command with segfault output
        second_td = tds[1]
        assert any(second_td["tool_errors"])


class TestJsonlFormat:
    """Tests for the newer streaming JSONL format."""

    @pytest.fixture
    def jsonl_session(self, tmp_path):
        lines = [
            # Header line
            json.dumps({"sessionId": "sess-jsonl-1", "projectHash": "proj-abc",
                        "startTime": "2026-05-30T06:00:00Z", "lastUpdated": "2026-05-30T06:30:00Z",
                        "kind": "main"}),
            # $set delta — should be ignored
            json.dumps({"$set": {"messages": [{"id": "u1", "type": "user", "content": []}],
                                 "lastUpdated": "2026-05-30T06:00:01Z"}}),
            # User message
            json.dumps({"id": "u1", "timestamp": "2026-05-30T06:00:01Z", "type": "user",
                        "content": [{"text": "fetch this URL and summarize"}]}),
            # Gemini turn with toolCalls (JSONL format: status field, prompt key)
            json.dumps({"id": "g1", "timestamp": "2026-05-30T06:00:05Z", "type": "gemini",
                        "content": "",
                        "thoughts": [{"subject": "Fetching", "description": "I will fetch the URL.",
                                      "timestamp": "2026-05-30T06:00:04Z"}],
                        "toolCalls": [{"id": "wf1", "name": "web_fetch",
                                       "args": {"prompt": "https://example.com summarize it"},
                                       "status": "success", "resultDisplay": "Summary done.",
                                       "result": [{"functionResponse": {"id": "wf1", "name": "web_fetch",
                                                   "response": {"output": "Example content"}}}]}],
                        "model": "gemini-2.5-pro", "tokens": 100}),
            # Gemini turn with error status
            json.dumps({"id": "g2", "timestamp": "2026-05-30T06:00:10Z", "type": "gemini",
                        "content": "",
                        "thoughts": [],
                        "toolCalls": [{"id": "wf2", "name": "web_fetch",
                                       "args": {"prompt": "https://bad.example.com"},
                                       "status": "error", "resultDisplay": "Error: connection refused",
                                       "result": []}],
                        "model": "gemini-2.5-pro", "tokens": 50}),
            # Gemini response
            json.dumps({"id": "g3", "timestamp": "2026-05-30T06:00:20Z", "type": "gemini",
                        "content": "Here is the summary of the content.",
                        "thoughts": [], "toolCalls": [],
                        "model": "gemini-2.5-pro", "tokens": 150}),
            # Second user turn
            json.dumps({"id": "u2", "timestamp": "2026-05-30T06:01:00Z", "type": "user",
                        "content": [{"text": "now write it to a file"}]}),
            # Gemini write
            json.dumps({"id": "g4", "timestamp": "2026-05-30T06:01:05Z", "type": "gemini",
                        "content": "",
                        "thoughts": [],
                        "toolCalls": [{"id": "wri1", "name": "write_file",
                                       "args": {"file_path": "output/summary.md", "content": "# Summary"},
                                       "status": "success", "resultDisplay": "File written.",
                                       "result": []}],
                        "model": "gemini-2.5-pro", "tokens": 80}),
        ]
        p = tmp_path / ".gemini" / "tmp" / "myproject" / "chats" / "session-2026-05-30T06-00-sess-jsonl-1.jsonl"
        p.parent.mkdir(parents=True)
        p.write_text("\n".join(lines))
        return str(p)

    def test_can_handle_jsonl_under_gemini(self, plugin, tmp_path):
        p = tmp_path / ".gemini" / "tmp" / "proj" / "chats" / "session-abc.jsonl"
        p.parent.mkdir(parents=True)
        p.write_text("{}")
        assert plugin.can_handle(str(p))

    def test_can_handle_rejects_non_gemini_jsonl(self, plugin, tmp_path):
        p = tmp_path / "projects" / "myproj" / "session.jsonl"
        p.parent.mkdir(parents=True)
        p.write_text("{}")
        assert not plugin.can_handle(str(p))

    def test_jsonl_parses_session_timestamp(self, plugin, jsonl_session):
        result = plugin.parse(jsonl_session)
        assert result.session_timestamp == "2026-05-30T06:00:00Z"

    def test_jsonl_set_lines_ignored(self, plugin, jsonl_session):
        result = plugin.parse(jsonl_session)
        # $set line should not produce a spurious extra exchange
        assert len(result.metadata.get("turn_descriptors", [])) == 2

    def test_jsonl_chunks_per_exchange(self, plugin, jsonl_session):
        result = plugin.parse(jsonl_session)
        # 2 exchanges; small ones are packed into 1 chunk — verify both appear
        assert len(result.chunks) >= 1
        full_text = " ".join(c.chunk_text for c in result.chunks)
        assert "fetch this URL" in full_text
        assert "write it to a file" in full_text

    def test_jsonl_status_error_detected(self, plugin, jsonl_session):
        result = plugin.parse(jsonl_session)
        tds = result.metadata.get("turn_descriptors", [])
        # First exchange has g2 with status=error
        assert any(tds[0]["tool_errors"])

    def test_jsonl_success_not_flagged_as_error(self, plugin, jsonl_session):
        result = plugin.parse(jsonl_session)
        tds = result.metadata.get("turn_descriptors", [])
        # Second exchange: write_file with status=success → no error
        assert not any(tds[1]["tool_errors"])

    def test_jsonl_write_file_extracted_as_modified(self, plugin, jsonl_session):
        result = plugin.parse(jsonl_session)
        mentions = result.metadata.get("raw_entity_mentions", [])
        modified = [m for m in mentions if m["role"] == "file_modified"]
        assert any("output/summary.md" in m["canonical_name"] for m in modified)

    def test_jsonl_model_turn_count(self, plugin, jsonl_session):
        result = plugin.parse(jsonl_session)
        tds = result.metadata.get("turn_descriptors", [])
        # First exchange: g1, g2, g3 = 3 gemini turns
        assert tds[0]["model_turn_count"] == 3

    def test_jsonl_workspace_id_from_gemini_path(self, plugin, jsonl_session):
        result = plugin.parse(jsonl_session)
        assert result.workspace_id == "myproject"


class TestExchangeClassifier:
    def test_agent_continuation_for_empty_user(self):
        ex = {"user": {"content": []}, "gemini_turns": [
            {"toolCalls": [], "tokens": {"thoughts": 0, "output": 10}}
        ]}
        from agent_trace_signals.plugins.gemini_cli import GeminiSource
        td = GeminiSource._extract_turn_descriptor(ex, 0)
        assert td.exchange_type == "agent_continuation"

    def test_genuine_text_when_no_tool_calls(self):
        ex = {"user": {"content": [{"text": "explain this please"}]}, "gemini_turns": [
            {"toolCalls": [], "tokens": {"thoughts": 0, "output": 5}}
        ]}
        from agent_trace_signals.plugins.gemini_cli import GeminiSource
        td = GeminiSource._extract_turn_descriptor(ex, 0)
        assert td.exchange_type == "genuine_text"

    def test_genuine_human_with_tool_calls(self):
        ex = {"user": {"content": [{"text": "read file"}]}, "gemini_turns": [
            {"toolCalls": [{"name": "read_file", "args": {"file_path": "/a.py"}}],
             "tokens": {"thoughts": 0, "output": 5}}
        ]}
        from agent_trace_signals.plugins.gemini_cli import GeminiSource
        td = GeminiSource._extract_turn_descriptor(ex, 0)
        assert td.exchange_type == "genuine_human"

    def test_avg_think_tok_ratio(self):
        ex = {"user": {"content": [{"text": "go"}]}, "gemini_turns": [
            {"toolCalls": [], "tokens": {"thoughts": 50, "output": 100}},
            {"toolCalls": [], "tokens": {"thoughts": 0, "output": 200}},
        ]}
        from agent_trace_signals.plugins.gemini_cli import GeminiSource
        td = GeminiSource._extract_turn_descriptor(ex, 0)
        # mean of (50/100, 0/200) = 0.25
        assert abs(td.avg_think_tok_ratio - 0.25) < 1e-9


# ---------------------------------------------------------------------------
# evidence_text extraction — REGRESSION: previously dropped tool result outputs
# ---------------------------------------------------------------------------

def test_evidence_text_captures_gemini_tool_result_output():
    """Gemini tool results used to be dropped entirely from chunk_text.
    evidence_text must now capture them verbatim."""
    from agent_trace_signals.plugins.gemini_cli import GeminiSource
    output_str = "Hello from shell\nLine 2 of output"
    exchange = {
        "user": {"content": [{"text": "run echo hello"}]},
        "gemini_turns": [{
            "toolCalls": [{
                "name": "run_shell_command",
                "args": {"command": "echo hello"},
                "result": [
                    {"functionResponse": {"response": {"output": output_str}}}
                ],
            }],
            "content": "I ran it.",
        }],
    }
    ev = GeminiSource._extract_evidence(exchange)
    assert "Hello from shell" in ev
    assert "Line 2 of output" in ev
    assert "[tool: run_shell_command(" in ev


def test_evidence_text_empty_when_no_tool_calls_gemini():
    from agent_trace_signals.plugins.gemini_cli import GeminiSource
    exchange = {
        "user": {"content": [{"text": "hi"}]},
        "gemini_turns": [{"toolCalls": [], "content": "hello"}],
    }
    assert GeminiSource._extract_evidence(exchange) == ""
