"""Failure signal analytics — keyword density and Gemini thought signals.

Independent of analytics light and embed. Reads records.chunk_text (set at ingest)
and writes failure_keyword_density back to records. No LLM calls.

Per-harness keyword sets extend a universal base set:
- Universal: user frustration + agent self-correction phrases visible in chunk_text
- CC-specific: harness error tokens that appear in serialized tool results
- Gemini-specific: thought subject keywords (requires source re-parse; separate method)
- Opencode-specific: Opencode tool error formats
"""

from __future__ import annotations
import re
import sqlite3


# ── Keyword sets ──────────────────────────────────────────────────────────────

# User-signal: frustration, repetition, failure reports
_USER_KEYWORDS = [
    "not working", "still not", "still broken", "still cannot", "still failing",
    "broken", "cannot", "can't", "doesn't work", "not loading", "not moving",
    "not responding", "not updating", "not found", "again", "that's wrong",
    "that's not", "you're not", "keeps happening", "same problem", "same issue",
]

# Agent-signal: self-correction, confusion, backtracking visible in chunk text
_AGENT_KEYWORDS = [
    "i apologize", "i was wrong", "i made an error", "let me try", "try again",
    "try a different", "different approach", "let me reconsider", "unexpected",
    "i'm not sure", "i notice i've been", "i notice the", "this is strange",
    "this isn't working", "seems incorrect", "i misunderstood",
    "let me re-read", "my previous", "i see the issue", "the issue is",
]

# Shared failure nouns that appear in both user and agent text
_FAILURE_NOUNS = [
    "bug", "error", "exception", "traceback", "failed", "failure",
    "incorrect", "wrong", "issue", "problem", "crash", "fix",
]

# CC-specific: harness error tokens that appear in serialized tool output
_CC_KEYWORDS = [
    "exit code", "inputvalidationerror", "tool_use_error", "permission denied",
    "no such file", "command not found", "syntax error",
]

# Gemini-specific additions visible in chunk text (tool result outputs)
_GEMINI_KEYWORDS = [
    "compilation error", "type error", "cannot find module",
    "build failed", "npm error",
]

# Opencode-specific: error formats from bash/edit tool results
_OPENCODE_KEYWORDS = [
    "status: error", "exit_code", "stderr:", "command failed",
]

# Thought subject keywords for Gemini (used separately on thought text)
_GEMINI_THOUGHT_FAILURE_SUBJECTS = [
    "misstep", "incorrect", "reconsider", "reconsidering", "backtrack",
    "error", "failed", "problem", "issue", "I was wrong", "redo",
    "retry", "re-approach", "revisit",
]


def _build_pattern(keywords: list[str]) -> re.Pattern:
    escaped = [re.escape(kw) for kw in keywords]
    return re.compile("|".join(escaped), re.IGNORECASE)


_UNIVERSAL_PAT = _build_pattern(_USER_KEYWORDS + _AGENT_KEYWORDS + _FAILURE_NOUNS)
_CC_PAT = _build_pattern(_CC_KEYWORDS)
_GEMINI_PAT = _build_pattern(_GEMINI_KEYWORDS)
_OPENCODE_PAT = _build_pattern(_OPENCODE_KEYWORDS)
_GEMINI_THOUGHT_PAT = _build_pattern(_GEMINI_THOUGHT_FAILURE_SUBJECTS)


def _count_matches(text: str, *patterns: re.Pattern) -> int:
    return sum(len(p.findall(text)) for p in patterns)


# ── Per-harness density functions ─────────────────────────────────────────────

def keyword_density_cc(chunk_text: str) -> int:
    """Failure keyword density for Claude Code chunks."""
    return _count_matches(chunk_text, _UNIVERSAL_PAT, _CC_PAT)


def keyword_density_gemini(chunk_text: str) -> int:
    """Failure keyword density for Gemini CLI chunks."""
    return _count_matches(chunk_text, _UNIVERSAL_PAT, _GEMINI_PAT)


def keyword_density_opencode(chunk_text: str) -> int:
    """Failure keyword density for Opencode chunks."""
    return _count_matches(chunk_text, _UNIVERSAL_PAT, _OPENCODE_PAT)


def keyword_density_universal(chunk_text: str) -> int:
    """Fallback for unknown source plugins."""
    return _count_matches(chunk_text, _UNIVERSAL_PAT)


_HARNESS_DENSITY_FN = {
    "claude_code": keyword_density_cc,
    "gemini_cli":  keyword_density_gemini,
    "opencode":    keyword_density_opencode,
}


# ── Gemini thought signal (requires source re-parse) ──────────────────────────

def thought_failure_density_gemini(exchange: dict) -> int:
    """Count failure-signal thought subjects in a Gemini exchange.

    Operates on a parsed exchange dict (from GeminiSource._build_exchanges).
    Thought blocks are in turn['thoughts'] — either a string (JSONL format)
    or a list of {subject, description} dicts (JSON format).
    """
    count = 0
    for turn in exchange.get("gemini_turns", []):
        thoughts = turn.get("thoughts", "")
        if isinstance(thoughts, str):
            count += len(_GEMINI_THOUGHT_PAT.findall(thoughts))
        elif isinstance(thoughts, list):
            for block in thoughts:
                if isinstance(block, dict):
                    subject = block.get("subject", "")
                    desc = block.get("description", "")
                    count += len(_GEMINI_THOUGHT_PAT.findall(subject))
                    count += len(_GEMINI_THOUGHT_PAT.findall(desc))
    return count


# ── Main analytics entry point ────────────────────────────────────────────────

def compute_failure_keyword_density(db_path: str, session_id: str | None = None) -> int:
    """Compute and store failure_keyword_density for all un-scored chunks.

    Reads source_plugin from sessions to choose the per-harness keyword set.
    Writes raw keyword counts to records.failure_keyword_density.
    Idempotent: skips chunks that already have a non-NULL score unless
    session_id is provided (re-scores that session).

    Returns count of chunks updated.
    """
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row

    _ensure_column(conn)

    if session_id:
        rows = conn.execute(
            """SELECT r.id, r.chunk_text, s.source_plugin
               FROM records r JOIN sessions s ON s.id = r.session_id
               WHERE r.session_id = ?""",
            (session_id,),
        ).fetchall()
    else:
        rows = conn.execute(
            """SELECT r.id, r.chunk_text, s.source_plugin
               FROM records r JOIN sessions s ON s.id = r.session_id
               WHERE r.failure_keyword_density IS NULL""",
        ).fetchall()

    if not rows:
        return 0

    updates = []
    for row in rows:
        fn = _HARNESS_DENSITY_FN.get(row["source_plugin"], keyword_density_universal)
        density = fn(row["chunk_text"] or "")
        updates.append((density, row["id"]))

    conn.executemany(
        "UPDATE records SET failure_keyword_density = ? WHERE id = ?", updates
    )
    conn.commit()
    conn.close()
    return len(updates)


def _ensure_column(conn: sqlite3.Connection) -> None:
    """Add failure_keyword_density column to records if it doesn't exist."""
    cols = {row[1] for row in conn.execute("PRAGMA table_info(records)").fetchall()}
    if "failure_keyword_density" not in cols:
        conn.execute(
            "ALTER TABLE records ADD COLUMN failure_keyword_density INTEGER"
        )
        conn.commit()


def session_density_profile(db_path: str, session_id: str) -> list[dict]:
    """Return per-chunk density profile for a session, with z-scores.

    Useful for inspection and for Track 2 window selection.
    Requires compute_failure_keyword_density to have run first.
    """
    import math
    conn = sqlite3.connect(db_path)
    rows = conn.execute(
        """SELECT chunk_index, failure_keyword_density
           FROM records WHERE session_id = ?
           ORDER BY chunk_index""",
        (session_id,),
    ).fetchall()
    conn.close()

    if not rows:
        return []

    densities = [r[1] or 0 for r in rows]
    n = len(densities)
    mean = sum(densities) / n
    variance = sum((d - mean) ** 2 for d in densities) / n if n > 1 else 0
    std = math.sqrt(variance) if variance > 0 else 1.0

    return [
        {
            "chunk_index": rows[i][0],
            "density": densities[i],
            "zscore": round((densities[i] - mean) / std, 2),
        }
        for i in range(n)
    ]
