"""Tests for ClaudeCodeSource._classify_exchange."""
from __future__ import annotations

from agent_trace_signals.plugins.claude_code import ClaudeCodeSource


def _ex(user_text: str = "", *, turns=None):
    """Build a minimal CC exchange dict."""
    return {
        "user": {
            "message": {"content": user_text} if isinstance(user_text, str) else {"content": user_text},
        },
        "turns": turns or [],
    }


def _td(exchange):
    return ClaudeCodeSource._extract_turn_descriptor(exchange, 0)


def test_skill_invocation():
    ex = _ex("Base directory for this skill: /tmp/foo\n\nMore text.")
    assert _td(ex).exchange_type == "skill_invocation"


def test_task_notification():
    ex = _ex("<task-notification>do thing</task-notification>")
    assert _td(ex).exchange_type == "task_notification"


def test_context_continuation():
    ex = _ex("This session is being continued from a previous conversation...")
    assert _td(ex).exchange_type == "context_continuation"


def test_wakeup_injection_tool_call():
    turns = [{
        "type": "assistant",
        "message": {"content": [
            {"type": "tool_use", "name": "ScheduleWakeup", "input": {"delay": 10}},
        ]},
    }]
    ex = _ex("wake up later", turns=turns)
    assert _td(ex).exchange_type == "wakeup_injection"


def test_wakeup_injection_scheduled_for():
    turns = [{
        "type": "user",
        "toolUseResult": {"scheduledFor": "2026-01-01T00:00:00Z"},
        "message": {"content": []},
    }]
    ex = _ex("hi", turns=turns)
    assert _td(ex).exchange_type == "wakeup_injection"


def test_genuine_text_no_tool_calls():
    turns = [{
        "type": "assistant",
        "message": {"content": [{"type": "text", "text": "hello"}]},
    }]
    ex = _ex("hi please explain", turns=turns)
    assert _td(ex).exchange_type == "genuine_text"


def test_genuine_human_with_tool_calls():
    turns = [{
        "type": "assistant",
        "message": {"content": [
            {"type": "tool_use", "name": "Read", "input": {"file_path": "/x.py"}},
        ]},
    }]
    ex = _ex("read this file", turns=turns)
    assert _td(ex).exchange_type == "genuine_human"


# ---------------------------------------------------------------------------
# evidence_text extraction (Stage 1 of trajectory_signals)
# ---------------------------------------------------------------------------

def test_evidence_text_captures_verbatim_tool_result():
    """evidence_text must contain verbatim tool_result content, not truncated."""
    long_output = "ERROR at line 42: NullPointerException\n" + ("x" * 1000)
    turns = [
        {"type": "assistant", "message": {"content": [
            {"type": "tool_use", "id": "t1", "name": "Bash",
             "input": {"command": "pytest"}},
        ]}},
        {"type": "user", "message": {"content": [
            {"type": "tool_result", "tool_use_id": "t1", "content": long_output},
        ]}},
    ]
    ex = _ex("run tests", turns=turns)
    ev = ClaudeCodeSource._extract_evidence(ex)
    # Verbatim output beyond the 200-char chunk_text cap
    assert "ERROR at line 42: NullPointerException" in ev
    assert "[tool: Bash(" in ev
    # 1000 x's preserved (vs 200-char cap on chunk_text)
    assert ev.count("x") >= 900


def test_evidence_text_empty_for_text_only_exchange():
    ex = _ex("just a question", turns=[
        {"type": "assistant", "message": {"content": [
            {"type": "text", "text": "Sure, here is the answer."}
        ]}}
    ])
    assert ClaudeCodeSource._extract_evidence(ex) == ""


def test_evidence_text_caps_per_tool_result_at_4000_chars():
    huge = "Z" * 10000
    turns = [
        {"type": "assistant", "message": {"content": [
            {"type": "tool_use", "id": "t1", "name": "Read",
             "input": {"file_path": "/big.txt"}}
        ]}},
        {"type": "user", "message": {"content": [
            {"type": "tool_result", "tool_use_id": "t1", "content": huge}
        ]}},
    ]
    ex = _ex("read file", turns=turns)
    ev = ClaudeCodeSource._extract_evidence(ex)
    # Header + capped body + trailing \n\n
    assert ev.count("Z") == 4000
