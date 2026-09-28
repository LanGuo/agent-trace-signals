"""Evaluation — retrieval head-to-head: raw session grep vs. design log grep vs. ats (MCP).

Static results from experiments/exp10_retrieval_h2h/ (run_h2h.py / FINDINGS.md). Not
recomputed live — this is a point-in-time study on 14 hand-verified facts, rerun after the
2026-07-27/28 RRF and workspace-filtering fixes, and again after the 2026-07-30
graph-expansion fix.
"""

from __future__ import annotations
import json
from pathlib import Path

import pandas as pd
import streamlit as st

from agent_trace_signals.ui.common import PAGE_CONFIG

st.set_page_config(**PAGE_CONFIG)
st.title("📊 Evaluation")

RESULTS_PATH = Path(__file__).resolve().parents[4] / "experiments/exp10_retrieval_h2h/h2h_results.json"

st.markdown(
    """
### Retrieval head-to-head: raw session grep vs. design log grep vs. ats (MCP)

Can an agent find a specific fact faster/cheaper via ats than by grepping raw session transcripts
or the design log directly? Tested on 14 facts from `design/design_decisions.md`, each asked a
precise (jargon-matching) and a fuzzy (paraphrased, no shared vocabulary) way — 28 cases. **ats**
means the full MCP surface an agent actually has — both `recall` and `chunk_search`,
workspace-filtered to this project — not one tool in isolation.
"""
)

if not RESULTS_PATH.exists():
    st.warning(f"Results file not found at `{RESULTS_PATH}`. Run `experiments/exp10_retrieval_h2h/run_h2h.py` first.")
    st.stop()

rows = json.loads(RESULTS_PATH.read_text())
n = len(rows)

raw_hit = sum(1 for r in rows if r["raw_grep_hit"])
design_hit = sum(1 for r in rows if r["design_log_hit"])
ats_hit = sum(1 for r in rows if r["ats_hit"])
raw_tok = sum(r["raw_grep_tokens"] for r in rows) / n
design_tok = sum(r["design_log_tokens"] for r in rows) / n
ats_tok = sum(r["ats_tokens_total"] for r in rows) / n

st.subheader("Summary")
c1, c2, c3 = st.columns(3)
with c1:
    st.markdown("#### Raw session grep")
    st.metric("Raw session grep", f"{raw_hit}/{n} hit", f"{raw_tok:,.0f} tokens/query avg (max {max(r['raw_grep_tokens'] for r in rows):,})", delta_color="off", label_visibility="collapsed")
    st.warning("Hit ≈ meaningless here — the keyword exists *somewhere* in 118MB of transcript, not that it found the right answer.")
with c2:
    st.markdown("#### Design log grep")
    st.metric("Design log grep", f"{design_hit}/{n} hit", f"{design_tok:,.0f} tokens/query avg", delta_color="off", label_visibility="collapsed")
    st.warning("Cheap and fast, but scope-limited (only what got manually written up) and brittle to exact phrasing.")
with c3:
    st.markdown("#### ats (recall + chunk_search, MCP)")
    st.metric("ats (recall + chunk_search, MCP)", f"{ats_hit}/{n} hit", f"{ats_tok:,.0f} tokens/query avg", delta_color="off", label_visibility="collapsed")
    st.warning("Matches grep's practical hit rate at ~1/30th the token cost and latency.")

st.subheader("Precise vs. fuzzy, per method")


def _hit_rate(method_key: str, variant: str) -> str:
    sub = [r for r in rows if r["variant"] == variant]
    hits = sum(1 for r in sub if r[method_key])
    return f"{hits}/{len(sub)}"


category_df = pd.DataFrame([
    {"Method": "Raw session grep", "Precise": _hit_rate("raw_grep_hit", "precise"), "Fuzzy": _hit_rate("raw_grep_hit", "fuzzy")},
    {"Method": "Design log grep", "Precise": _hit_rate("design_log_hit", "precise"), "Fuzzy": _hit_rate("design_log_hit", "fuzzy")},
    {"Method": "ats (recall + chunk_search)", "Precise": _hit_rate("ats_hit", "precise"), "Fuzzy": _hit_rate("ats_hit", "fuzzy")},
])
st.dataframe(category_df, use_container_width=True, hide_index=True)
st.caption(
    "Design log grep: all 4 remaining precise misses are a plausible paraphrase vs. the doc's "
    "exact wording (e.g. \"page\" vs. \"tab\", missing a hyphen) — even searching the document a "
    "fact came from, literal grep still loses to imperfect phrasing. ats: fuzzy stays hard "
    "regardless of the RRF fix, which corrects leg fusion, not either leg's paraphrase tolerance."
)

both = sum(1 for r in rows if r["ats_recall_rank"] and r["ats_chunk_rank"])
chunk_only = sum(1 for r in rows if r["ats_chunk_rank"] and not r["ats_recall_rank"])
recall_only = sum(1 for r in rows if r["ats_recall_rank"] and not r["ats_chunk_rank"])

st.markdown(
    f"""
- **`recall` and `chunk_search` are complementary:** {both}/{ats_hit} ats hits found by both tools,
  {chunk_only} by `chunk_search` only (fact never became a memory, but the raw chunk is still
  searchable), {recall_only} by `recall` only.
- **`include_graph` was measured separately** (not in the numbers above), before and after two
  fixes. `recall`'s inflation dropped from 627× to **9.2×** (625 → 5,744 tokens, 2026-07-30 fix)
  once cross-session clustering switched `eom`→`leaf` and `graph_walk` capped its BFS.
  `chunk_search` expands via a different mechanism (`_chunk_graph_expand`, shared entity
  mentions) that fix didn't touch — its inflation dropped separately from 23× to **5.2×**
  (2,496 → 12,971 tokens, down from a 58,460-token/414-extra-chunk worst case, 2026-08-04 fix)
  once matched entities were weighted by inverse specificity (the project's own name, LLM model
  names, and README.md/traces.db were dominating fan-out simply from being mentioned everywhere,
  not because they were topically relevant) plus per-entity/per-call caps. `recall` still
  rescued 0/28 misses; `chunk_search` rescued 1/28 after its fix. Built for exploratory
  cross-session context, not fact lookup, in both cases.
"""
)

with_graph_hit = sum(1 for r in rows if r["ats_hit"] or r["graph_rescued"])
graph_tok_avg = sum(r["graph_recall_tokens"] + r["graph_chunk_tokens"] for r in rows) / n
st.info(
    f"**Should `include_graph` be on by default, now that it's fixed?** No — recomputing hit rate "
    f"with graph expansion as the primary mode (not just a rescue check): {with_graph_hit}/{n} hits, "
    f"identical to the {ats_hit}/{n} base rate, at {graph_tok_avg:,.0f} tokens/query avg "
    f"({graph_tok_avg / ats_tok:.1f}×). The one `graph_rescued` case found a fact via graph "
    f"expansion that plain `recall`/`chunk_search` also already found through the other tool, so "
    f"it never moves the aggregate — ~6× the cost for zero measured accuracy gain in this sample."
)

st.subheader("Full results (28 cases)")
df = pd.DataFrame(rows)[[
    "fact", "variant", "query",
    "raw_grep_hit", "raw_grep_tokens",
    "design_log_hit", "design_log_tokens",
    "ats_recall_rank", "ats_chunk_rank", "ats_hit", "ats_tokens_total",
]].rename(columns={
    "raw_grep_hit": "grep hit", "raw_grep_tokens": "grep tokens",
    "design_log_hit": "log hit", "design_log_tokens": "log tokens",
    "ats_recall_rank": "recall rank", "ats_chunk_rank": "chunk rank",
    "ats_hit": "ats hit", "ats_tokens_total": "ats tokens",
})
st.dataframe(df, use_container_width=True, hide_index=True)
