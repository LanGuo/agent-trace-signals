"""Inflection point detector — structural feature analysis over turn sequences.

[SOFT-DEPRECATED as of 2026-07-01] Track 2 (per-exchange observer scoring, critical step detection)
is no longer invested in. The patterns[] field in D-prompt covers the same semantic territory at lower cost.
"""

from __future__ import annotations
import json
import math
import uuid
from collections import Counter, deque
from datetime import datetime, timezone
from typing import TYPE_CHECKING

from agent_trace_signals.models import GENUINE_TYPES, SessionInflection, TurnDescriptor

if TYPE_CHECKING:
    pass

_WINDOW = 5          # sliding window size for all features
_Z_THRESHOLD = 2.0   # σ deviation to flag a turn as anomalous
_LOOKBACK = 5        # max turns to search back for precipitating_turn

_READ_ONLY_TOOLS = frozenset({
    "Read", "Glob",                        # Claude Code read-only
    "read_file", "list_directory",          # Gemini read-only
    "google_web_search", "web_fetch",       # Gemini web read-only
})


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


class _RunningStats:
    """Welford online mean and std — avoids storing full history."""

    def __init__(self) -> None:
        self.n = 0
        self.mean = 0.0
        self._m2 = 0.0

    def update(self, x: float) -> None:
        self.n += 1
        delta = x - self.mean
        self.mean += delta / self.n
        self._m2 += delta * (x - self.mean)

    @property
    def std(self) -> float:
        if self.n < 2:
            return 1.0  # avoid division by zero; z-score is 0 when std unknown
        return math.sqrt(self._m2 / (self.n - 1))

    def zscore(self, x: float) -> float:
        s = self.std
        return (x - self.mean) / s if s > 0 else 0.0


def _tool_target(tc: dict) -> str:
    return f"{tc.get('name', '?')}::{tc.get('target', '')}"


def _action_entropy(tool_names: list[str]) -> float:
    if not tool_names:
        return 0.0
    counts = Counter(tool_names)
    total = len(tool_names)
    return -sum((c / total) * math.log2(c / total) for c in counts.values())


class InflectionDetector:
    """Detect inflection episodes from per-exchange turn descriptors.

    All features are derived from structured JSONL data — no text parsing.
    Z-scores are relative to the session's own running statistics.
    """

    def detect(
        self, turn_descriptors: list[TurnDescriptor], session_id: str
    ) -> list[SessionInflection]:
        # Filter out harness-injected / agent-continuation exchanges so the
        # sliding-window z-score baseline isn't contaminated. See
        # design/exchange_classifier.md and
        # notes/inflection_detector_evaluation.md (95% FP rate motivation).
        turns = [
            td for td in turn_descriptors
            if getattr(td, "exchange_type", "genuine_human") in GENUINE_TYPES
        ]
        if len(turns) < _WINDOW + 1:
            return []

        n = len(turns)

        # Per-session running stats for z-score normalisation
        stats: dict[str, _RunningStats] = {
            "action_entropy": _RunningStats(),
            "action_target_recurrence": _RunningStats(),
            "error_rate": _RunningStats(),
            "user_turn_length_ratio": _RunningStats(),
            "read_only_ratio": _RunningStats(),
            "model_turn_count": _RunningStats(),
        }

        # Track session-wide history for recurrence computation
        seen_action_targets: set[str] = set()
        user_word_counts: list[int] = []

        # Sliding window buffers
        tool_name_window: deque[list[str]] = deque(maxlen=_WINDOW)
        error_window: deque[list[bool]] = deque(maxlen=_WINDOW)
        action_target_window: deque[list[str]] = deque(maxlen=_WINDOW)
        model_turn_window: deque[int] = deque(maxlen=_WINDOW)

        # Per-turn computed features (for episode detection pass)
        features: list[dict[str, float]] = []
        zscores: list[dict[str, float]] = []

        for t in turns:
            tool_names = [tc["name"] for tc in t.tool_calls]
            targets = [_tool_target(tc) for tc in t.tool_calls]
            errors = t.tool_errors

            tool_name_window.append(tool_names)
            action_target_window.append(targets)
            error_window.append(errors)
            user_word_counts.append(t.user_word_count)

            flat_names = [n for w in tool_name_window for n in w]
            flat_errors = [e for w in error_window for e in w]

            ae = _action_entropy(flat_names)
            er = sum(flat_errors) / len(flat_errors) if flat_errors else 0.0

            # Recurrence: fraction of this turn's action-targets already seen in session
            repeat_count = sum(1 for tgt in targets if tgt in seen_action_targets)
            atr = repeat_count / len(targets) if targets else 0.0

            median_wc = sorted(user_word_counts)[len(user_word_counts) // 2]
            utl = t.user_word_count / median_wc if median_wc > 0 else 1.0

            # read_only_ratio: fraction of this window's tool calls that are read-only
            read_only_count = sum(
                1 for w in tool_name_window for name in w if name in _READ_ONLY_TOOLS
            )
            total_calls = len(flat_names)
            ror = read_only_count / total_calls if total_calls > 0 else 0.0

            # model_turn_count: average model turns per exchange in window
            model_turn_window.append(t.model_turn_count)
            avg_mtc = sum(model_turn_window) / len(model_turn_window)

            f = {
                "action_entropy": ae,
                "action_target_recurrence": atr,
                "error_rate": er,
                "user_turn_length_ratio": utl,
                "read_only_ratio": ror,
                "model_turn_count": avg_mtc,
            }
            features.append(f)

            # Update running stats with this turn's values
            for name, val in f.items():
                stats[name].update(val)

            # Compute z-scores using stats updated through this turn
            zs = {name: stats[name].zscore(val) for name, val in f.items()}
            zscores.append(zs)

            # Update seen targets after computing recurrence
            seen_action_targets.update(targets)

        # -----------------------------------------------------------------------
        # Episode detection pass
        # -----------------------------------------------------------------------
        inflections: list[SessionInflection] = []
        in_loop = False

        i = _WINDOW  # skip the warm-up window where stats are unreliable
        while i < n:
            zs = zscores[i]
            f = features[i]

            # Dominant feature: highest absolute z-score
            dominant = max(zs, key=lambda k: abs(zs[k]))
            strength = abs(zs[dominant])

            if strength < _Z_THRESHOLD:
                i += 1
                continue

            inflection_type = _classify(dominant, zs[dominant], f, in_loop)
            if inflection_type is None:
                i += 1
                continue

            # Precipitating turn: look back for root cause
            precipitating = _find_precipitating(i, features, turns)

            if inflection_type == "loop_entry":
                in_loop = True
                exit_turn = None
            elif inflection_type == "loop_exit":
                in_loop = False
                exit_turn = i
                # Link exit to the most recent loop_entry episode
                if inflections and inflections[-1].inflection_type == "loop_entry":
                    inflections[-1] = inflections[-1].model_copy(update={"exit_turn": i})
                i += 1
                continue  # loop_exit updates the loop_entry record, no new row
            else:
                exit_turn = i  # for corrections/escalations, entry IS the exit

            inflections.append(SessionInflection(
                id=uuid.uuid4().hex,
                session_id=session_id,
                inflection_type=inflection_type,
                precipitating_turn=precipitating,
                entry_turn=i,
                exit_turn=exit_turn if inflection_type != "loop_entry" else None,
                signal_strength=round(strength, 3),
                dominant_feature=dominant,
                signal_detail=json.dumps({k: round(v, 3) for k, v in f.items()}),
                detected_at=_now(),
            ))

            i += 1

        return inflections


def _classify(
    dominant: str, z: float, f: dict[str, float], in_loop: bool
) -> str | None:
    """Map dominant feature + direction + context to an inflection type."""
    # Agent thrash: many model turns per exchange AND mostly reading — no synthesis.
    # Removed the absolute model_turn_count > 2.0 threshold (meaningless across
    # harnesses — Gemini SDE sessions can have very different baselines); z-score
    # carries the signal. See design/exchange_classifier.md "Inflection detector".
    if dominant == "model_turn_count" and z > 0 and f.get("read_only_ratio", 0) > 0.6:
        return "agent_thrash"
    if dominant == "read_only_ratio" and z > 0:
        return "agent_thrash"
    # High recurrence = agent repeating same action-target pairs = stuck
    if dominant == "action_target_recurrence" and z > 0:
        return "loop_entry"
    if dominant == "action_entropy":
        # Entropy drop = action space narrowing = entering a loop
        if z < 0:
            return "loop_entry"
        # Entropy spike after being stuck = loop breaking
        if z > 0 and in_loop:
            return "loop_exit"
        # Entropy spike without prior loop = deliberate strategy change
        if z > 0 and not in_loop:
            return "strategy_pivot"
    if dominant == "error_rate" and z > 0 and f["error_rate"] > 0.5:
        return "escalation"
    if dominant == "user_turn_length_ratio" and z < 0 and f["user_turn_length_ratio"] < 0.5:
        return "user_correction"
    return None


def _find_precipitating(entry: int, features: list[dict], turns: list[TurnDescriptor]) -> int:
    """Scan backwards from entry (max _LOOKBACK turns) for estimated root cause.

    Root cause heuristic: the last turn before entry where error_rate first
    became non-zero, or the last turn that introduced new action targets.
    """
    earliest = max(0, entry - _LOOKBACK)
    for j in range(entry - 1, earliest - 1, -1):
        f = features[j]
        # First non-zero error rate before the loop = likely the precipitating failure
        if f["error_rate"] > 0 and (j == 0 or features[j - 1]["error_rate"] == 0):
            return j
        # Turn that introduced new targets (recurrence was 0 before it rose)
        if f["action_target_recurrence"] == 0 and features[j + 1 if j + 1 < entry else j]["action_target_recurrence"] > 0:
            return j
    return max(0, entry - 1)
