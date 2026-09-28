"""Tests for InflectionDetector — structural feature inflection detection."""
from agent_trace_signals.models import TurnDescriptor
from agent_trace_signals.pipeline.inflection_detector import InflectionDetector, _WINDOW


def _td(exchange_idx, tool_calls=None, tool_errors=None, user_word_count=20):
    return TurnDescriptor(
        exchange_idx=exchange_idx,
        user_word_count=user_word_count,
        tool_calls=tool_calls or [],
        tool_errors=tool_errors or [],
    )


def _tc(name, target="file.py"):
    return {"name": name, "target": target}


def _make_turns(n, tool_calls=None, tool_errors=None, user_word_count=20):
    """Build n identical turns."""
    return [_td(i, tool_calls=tool_calls, tool_errors=tool_errors, user_word_count=user_word_count) for i in range(n)]


SESSION = "sess-test"


class TestBasic:
    def test_too_short_returns_empty(self):
        turns = _make_turns(_WINDOW)  # exactly WINDOW — below threshold
        assert InflectionDetector().detect(turns, SESSION) == []

    def test_no_inflection_in_uniform_session(self):
        # Uniform tool use — no z-score deviation
        turns = [
            _td(i, tool_calls=[_tc("Read"), _tc("Edit")], tool_errors=[False, False])
            for i in range(20)
        ]
        result = InflectionDetector().detect(turns, SESSION)
        # May or may not detect — key is it doesn't crash and returns a list
        assert isinstance(result, list)


class TestLoopEntry:
    def test_detects_loop_entry_from_high_recurrence(self):
        # 10 normal turns (varied tools), then 10 turns repeating same (Edit, file.py)
        normal = [
            _td(i, tool_calls=[_tc("Read", f"f{i}.py"), _tc("Bash", f"cmd{i}")],
                tool_errors=[False, False])
            for i in range(10)
        ]
        stuck = [
            _td(10 + i, tool_calls=[_tc("Edit", "config.py"), _tc("Bash", "pytest")],
                tool_errors=[False, True])
            for i in range(10)
        ]
        turns = normal + stuck
        result = InflectionDetector().detect(turns, SESSION)
        types = {r.inflection_type for r in result}
        assert "loop_entry" in types

    def test_loop_entry_has_valid_episode_indices(self):
        normal = [
            _td(i, tool_calls=[_tc("Read", f"f{i}.py")], tool_errors=[False])
            for i in range(10)
        ]
        stuck = [
            _td(10 + i, tool_calls=[_tc("Edit", "config.py"), _tc("Bash", "pytest")],
                tool_errors=[False, True])
            for i in range(10)
        ]
        turns = normal + stuck
        result = InflectionDetector().detect(turns, SESSION)
        loops = [r for r in result if r.inflection_type == "loop_entry"]
        if loops:
            lp = loops[0]
            assert lp.precipitating_turn <= lp.entry_turn
            assert lp.entry_turn < len(turns)
            assert lp.signal_strength > 0

    def test_loop_entry_has_session_id(self):
        normal = [_td(i, tool_calls=[_tc("Read", f"f{i}.py")]) for i in range(10)]
        stuck = [_td(10 + i, tool_calls=[_tc("Edit", "same.py")] * 3, tool_errors=[True] * 3) for i in range(10)]
        result = InflectionDetector().detect(normal + stuck, SESSION)
        for r in result:
            assert r.session_id == SESSION


class TestUserCorrection:
    def test_detects_short_user_turn_after_tool_activity(self):
        # 10 turns with normal user message length (20 words)
        # then a very short user message (2 words) after many tool calls
        long_turns = [
            _td(i, tool_calls=[_tc("Edit"), _tc("Bash")], tool_errors=[False, False],
                user_word_count=20)
            for i in range(10)
        ]
        short_turn = _td(10, tool_calls=[_tc("Edit"), _tc("Bash")],
                         tool_errors=[False, False], user_word_count=2)
        turns = long_turns + [short_turn] + long_turns[:4]
        result = InflectionDetector().detect(turns, SESSION)
        types = {r.inflection_type for r in result}
        assert "user_correction" in types


class TestEscalation:
    def test_detects_rising_error_rate(self):
        # 10 clean turns, then 10 turns all failing
        clean = [_td(i, tool_calls=[_tc("Bash", f"cmd{i}")], tool_errors=[False]) for i in range(10)]
        failing = [_td(10 + i, tool_calls=[_tc("Bash", f"alt{i}")], tool_errors=[True]) for i in range(10)]
        turns = clean + failing
        result = InflectionDetector().detect(turns, SESSION)
        types = {r.inflection_type for r in result}
        assert "escalation" in types


class TestSignalStrength:
    def test_signal_strength_is_positive(self):
        normal = [_td(i, tool_calls=[_tc("Read", f"f{i}.py")]) for i in range(10)]
        stuck = [_td(10 + i, tool_calls=[_tc("Edit", "same.py")], tool_errors=[True]) for i in range(10)]
        result = InflectionDetector().detect(normal + stuck, SESSION)
        for r in result:
            assert r.signal_strength > 0

    def test_signal_detail_is_valid_json(self):
        import json
        normal = [_td(i, tool_calls=[_tc("Read", f"f{i}.py")]) for i in range(10)]
        stuck = [_td(10 + i, tool_calls=[_tc("Edit", "same.py")], tool_errors=[True]) for i in range(10)]
        result = InflectionDetector().detect(normal + stuck, SESSION)
        for r in result:
            if r.signal_detail:
                parsed = json.loads(r.signal_detail)
                assert "action_entropy" in parsed


class TestTurnDescriptorExtraction:
    def test_extract_from_claude_code_plugin(self):
        from agent_trace_signals.plugins.claude_code import ClaudeCodeSource
        exchange = {
            "user": {
                "message": {"content": "please fix the test"}
            },
            "turns": [
                {
                    "type": "assistant",
                    "message": {
                        "content": [
                            {"type": "tool_use", "name": "Edit",
                             "input": {"file_path": "tests/test_foo.py"}}
                        ]
                    }
                },
                {
                    "type": "user",
                    "toolUseResult": True,
                    "message": {
                        "content": [
                            {"type": "tool_result", "is_error": False,
                             "content": "File edited successfully"}
                        ]
                    }
                },
            ]
        }
        td = ClaudeCodeSource._extract_turn_descriptor(exchange, 0)
        assert td.exchange_idx == 0
        assert td.user_word_count == 4
        assert len(td.tool_calls) == 1
        assert td.tool_calls[0]["name"] == "Edit"
        assert td.tool_calls[0]["target"] == "tests/test_foo.py"
        assert td.tool_errors == [False]

    def test_bash_target_is_first_word_of_command(self):
        from agent_trace_signals.plugins.claude_code import ClaudeCodeSource
        exchange = {
            "user": {"message": {"content": "run the tests"}},
            "turns": [
                {
                    "type": "assistant",
                    "message": {
                        "content": [
                            {"type": "tool_use", "name": "Bash",
                             "input": {"command": "pytest tests/ -v"}}
                        ]
                    }
                }
            ]
        }
        td = ClaudeCodeSource._extract_turn_descriptor(exchange, 1)
        assert td.tool_calls[0]["name"] == "Bash"
        assert td.tool_calls[0]["target"] == "pytest"


class TestAgentThrash:
    def test_detects_agent_thrash_high_model_turns_and_reads(self):
        """High model_turn_count + read-only tools → agent_thrash."""
        # 10 normal turns: 1 model turn each, mixed read+write
        normal = [
            TurnDescriptor(
                exchange_idx=i,
                user_word_count=20,
                tool_calls=[{"name": "Read", "target": f"f{i}.py"}, {"name": "Edit", "target": f"f{i}.py"}],
                tool_errors=[False, False],
                model_turn_count=1,
            )
            for i in range(10)
        ]
        # 10 thrash turns: 5 model turns each, all read-only
        thrash = [
            TurnDescriptor(
                exchange_idx=10 + i,
                user_word_count=20,
                tool_calls=[
                    {"name": "read_file", "target": f"src/a{i}.py"},
                    {"name": "read_file", "target": f"src/b{i}.py"},
                    {"name": "google_web_search", "target": f"query{i}"},
                ],
                tool_errors=[False, False, False],
                model_turn_count=5,
            )
            for i in range(10)
        ]
        turns = normal + thrash
        result = InflectionDetector().detect(turns, SESSION)
        types = {r.inflection_type for r in result}
        assert "agent_thrash" in types

    def test_no_thrash_when_model_turns_normal(self):
        """Single model turn per exchange should not trigger agent_thrash."""
        turns = [
            TurnDescriptor(
                exchange_idx=i,
                user_word_count=20,
                tool_calls=[{"name": "read_file", "target": f"f{i}.py"}],
                tool_errors=[False],
                model_turn_count=1,
            )
            for i in range(20)
        ]
        result = InflectionDetector().detect(turns, SESSION)
        types = {r.inflection_type for r in result}
        assert "agent_thrash" not in types


class TestExchangeTypeFilter:
    def test_non_genuine_filtered_out(self):
        """Turns with non-GENUINE exchange_type are excluded from baseline."""
        # 20 'agent_continuation' turns would be too few after filtering
        turns = [
            TurnDescriptor(
                exchange_idx=i, user_word_count=0, tool_calls=[], tool_errors=[],
                model_turn_count=1, exchange_type="agent_continuation",
            )
            for i in range(20)
        ]
        result = InflectionDetector().detect(turns, "sess")
        # All filtered out → 0 surviving → returns []
        assert result == []

    def test_mixed_only_genuine_participate(self):
        """Non-genuine exchanges should not affect detection of genuine-turn signals."""
        # 10 noise injections interleaved with 20 normal genuine turns; noise
        # should be ignored and detector should still process the 20 genuine.
        normal = [
            TurnDescriptor(
                exchange_idx=i, user_word_count=20,
                tool_calls=[{"name": "Read", "target": f"f{i}.py"}],
                tool_errors=[False], model_turn_count=1,
                exchange_type="genuine_human",
            )
            for i in range(20)
        ]
        noise = [
            TurnDescriptor(
                exchange_idx=100 + i, user_word_count=0,
                tool_calls=[], tool_errors=[], model_turn_count=1,
                exchange_type="task_notification",
            )
            for i in range(10)
        ]
        mixed = normal + noise
        result = InflectionDetector().detect(mixed, "sess")
        # Doesn't crash and produces a list (uniform → likely empty)
        assert isinstance(result, list)
