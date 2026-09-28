"""Shared helpers for the Streamlit UI."""

from __future__ import annotations
import os

# Must run before numba is imported anywhere in this process (numba reads its
# threading config lazily, on first parallel-dispatch use — not at import time —
# but setting this as early as possible is the safest guarantee). umap-learn and
# hdbscan both JIT-compile via numba; numba's default "workqueue" threading layer
# is documented as NOT safe to invoke concurrently from multiple OS threads. This
# app is single-process but multi-threaded (Streamlit runs each connected browser
# session in its own thread), so two tabs/sessions both loading a UMAP chart
# around the same time crash with "workqueue... accessed concurrently by multiple
# threads". Forcing numba to 1 thread removes its internal parallelism entirely,
# so there's nothing for concurrent Streamlit sessions to race on — UMAP/HDBSCAN
# calls just run single-core instead of using all cores. These are cached
# (st.cache_data) background chart computations, not a latency-critical path, so
# the perf tradeoff is worth the stability. Respects an explicit env var if the
# user has already set one (e.g. to "tbb" after installing the tbb package).
os.environ.setdefault("NUMBA_NUM_THREADS", "1")

import streamlit as st
from agent_trace_signals.pipeline.utils import format_workspace  # noqa: F401 — re-exported


def db_path() -> str:
    return os.environ.get("ATS_DB", "traces.db")


@st.cache_resource
def get_store():
    from agent_trace_signals.db.schema import open_db, create_all, migrate
    from agent_trace_signals.db.store import SQLiteStore
    conn = open_db(db_path())
    migrate(conn)
    create_all(conn)
    return SQLiteStore(conn)


@st.cache_resource
def get_engine():
    from agent_trace_signals.config import ModelConfig
    from agent_trace_signals.embedder import Embedder
    from agent_trace_signals.pipeline.retrieval import RetrievalEngine
    store = get_store()
    return RetrievalEngine(store, Embedder(ModelConfig()))


def session_label(row: dict) -> str:
    """Short human-readable label for a session."""
    ts = (row.get("session_timestamp") or "")[:10]
    plugin = (row.get("source_plugin") or "")
    ws_short = format_workspace(row.get("workspace_id") or "")[-25:]
    sid = (row.get("id") or "")[:8]
    return f"{ts} | {plugin} | {ws_short} | {sid}"


PAGE_CONFIG = dict(
    page_title="ATS — Agent Trace Signals",
    page_icon="🧠",
    layout="wide",
    initial_sidebar_state="expanded",
)
