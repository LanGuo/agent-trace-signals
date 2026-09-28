"""Observer LLM comparison — EXP_OBSERVER_LLM_COMPARISON.md

Scores one session with multiple observer models in isolated in-memory DBs,
then compares score distributions and (if labels provided) precision/recall.

Usage:
    uv run experiments/run_observer_compare.py \
        --db /tmp/cohort2.db \
        --session fb3e418c2842fa182d630664f13716fefaabba8415395a62f9377f1d1d1fd630 \
        --labels ~/Downloads/labeled_critical_steps.json \
        --models gemma3:12b gemma4:e4b

Add --models qwen3:8b after pulling it with: ollama pull qwen3:8b
"""

from __future__ import annotations

import argparse
import json
import sqlite3
import statistics
import sys
import time
from pathlib import Path

# Allow running from repo root without install
sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from agent_trace_signals.config import CriticalStepConfig, ModelConfig
from agent_trace_signals.db.store import SQLiteStore
from agent_trace_signals.pipeline.critical_steps import CriticalStepDetectionPipeline
from agent_trace_signals.pipeline.step_scoring import StepScoringPipeline
from agent_trace_signals.providers.ollama import OllamaProvider


# ── helpers ────────────────────────────────────────────────────────────────


_NEEDED_TABLES = ("sessions", "records", "step_scores", "critical_steps")


def _copy_session_to_memory(src_db: str, session_id: str) -> sqlite3.Connection:
    """Copy one session into an in-memory SQLite DB with only the tables the pipeline needs."""
    src = sqlite3.connect(src_db)
    src.row_factory = sqlite3.Row

    mem = sqlite3.connect(":memory:")
    mem.row_factory = sqlite3.Row

    # Recreate only the tables the scoring + detection pipelines touch
    for table in _NEEDED_TABLES:
        sql = src.execute(
            "SELECT sql FROM sqlite_master WHERE type='table' AND name=?", (table,)
        ).fetchone()
        if sql:
            mem.execute(sql[0])

    # Copy session row
    row = src.execute("SELECT * FROM sessions WHERE id=?", (session_id,)).fetchone()
    if not row:
        raise ValueError(f"session {session_id[:12]}... not found in {src_db}")
    cols = list(row.keys())
    placeholders = ", ".join("?" * len(cols))
    mem.execute(
        f"INSERT INTO sessions ({', '.join(cols)}) VALUES ({placeholders})",
        list(row),
    )

    # Copy record rows (evidence_text used by step scorer)
    for r in src.execute("SELECT * FROM records WHERE session_id=?", (session_id,)).fetchall():
        cols = list(r.keys())
        placeholders = ", ".join("?" * len(cols))
        mem.execute(
            f"INSERT INTO records ({', '.join(cols)}) VALUES ({placeholders})",
            list(r),
        )

    mem.commit()
    src.close()
    return mem


def _load_labels(label_path: str, session_id: str) -> dict[int, bool]:
    """Return {exchange_idx: is_true_positive} from the human-label JSON."""
    data = json.loads(Path(label_path).read_text())
    out: dict[int, bool] = {}
    for rec in data.get("records", []):
        if rec["session_id"] != session_id:
            continue
        label = rec.get("label") or {}
        out[rec["exchange_idx"]] = bool(label.get("is_true_positive", False))
    return out


def _ascii_histogram(scores: list[float], n_buckets: int = 10, width: int = 40) -> str:
    if not scores:
        return "(no scores)"
    mn, mx = 0.0, 1.0
    bucket_size = (mx - mn) / n_buckets
    counts = [0] * n_buckets
    for s in scores:
        idx = min(int((s - mn) / bucket_size), n_buckets - 1)
        counts[idx] = counts[idx] + 1
    max_count = max(counts) or 1
    lines = []
    for i, c in enumerate(counts):
        lo = mn + i * bucket_size
        hi = lo + bucket_size
        bar = "█" * int(c / max_count * width)
        lines.append(f"  [{lo:.2f},{hi:.2f}) {bar} {c}")
    return "\n".join(lines)


# ── per-model run ──────────────────────────────────────────────────────────


def run_model(
    mem_conn: sqlite3.Connection,
    session_id: str,
    model_slug: str,
    detect_cfg: CriticalStepConfig,
    labels: dict[int, bool],
    limit: int = 0,
) -> dict:
    store = SQLiteStore(mem_conn)

    # Wipe any prior step_scores / critical_steps for a clean run
    mem_conn.execute("DELETE FROM step_scores")
    mem_conn.execute("DELETE FROM critical_steps")
    mem_conn.commit()

    # If --limit set, trim turn_descriptors to first N scorable exchanges
    if limit > 0:
        td_json = mem_conn.execute(
            "SELECT turn_descriptors FROM sessions WHERE id=?", (session_id,)
        ).fetchone()[0]
        if td_json:
            tds = json.loads(td_json)
            scorable_count = 0
            trimmed = []
            for td in tds:
                trimmed.append(td)
                if td.get("exchange_type") in ("genuine_human", "genuine_text", "agent_continuation", "summary_diff"):
                    scorable_count += 1
                    if scorable_count >= limit:
                        break
            mem_conn.execute(
                "UPDATE sessions SET turn_descriptors=? WHERE id=?",
                (json.dumps(trimmed), session_id),
            )
            mem_conn.commit()

    model_cfg = ModelConfig(observer_llm=model_slug)
    provider = OllamaProvider(model_cfg)

    pipeline = StepScoringPipeline(store=store, provider=provider, config=model_cfg)

    t0 = time.time()
    n_scored = pipeline.score_session(session_id)
    elapsed = time.time() - t0

    rows = mem_conn.execute(
        "SELECT exchange_idx, evidence_supports, progress_vector, cost_vector FROM step_scores "
        "WHERE session_id=? ORDER BY exchange_idx",
        (session_id,),
    ).fetchall()

    scores = [float(r[1]) for r in rows]
    n_zero = sum(1 for s in scores if s == 0.0)

    det = CriticalStepDetectionPipeline(store, detect_cfg)
    det.detect_session(session_id)
    critical = mem_conn.execute(
        "SELECT exchange_idx, tag FROM critical_steps WHERE session_id=?",
        (session_id,),
    ).fetchall()
    failure_idxs = {r[0] for r in critical if r[1] == "failure_critical"}

    # Precision / recall against labels
    tp = fp = fn_flagged = 0
    if labels:
        for idx, is_tp in labels.items():
            if idx in failure_idxs:
                if is_tp:
                    tp += 1
                else:
                    fp += 1
        precision = tp / (tp + fp) if (tp + fp) > 0 else None
        # FN: labeled TP not in failure_idxs
        fn_flagged = sum(1 for idx, is_tp in labels.items() if is_tp and idx not in failure_idxs)
        recall_lb = tp / (tp + fn_flagged) if (tp + fn_flagged) > 0 else None
    else:
        precision = recall_lb = None

    stddev = statistics.stdev(scores) if len(scores) > 1 else 0.0
    mean = statistics.mean(scores) if scores else 0.0
    sorted_scores = sorted(scores)
    p20 = sorted_scores[max(0, int(len(sorted_scores) * 0.20) - 1)] if sorted_scores else 0.0
    p80 = sorted_scores[min(len(sorted_scores) - 1, int(len(sorted_scores) * 0.80))] if sorted_scores else 0.0

    return {
        "model": model_slug,
        "n_scored": n_scored,
        "n_failure_critical": len(failure_idxs),
        "pct_zero": 100 * n_zero / len(scores) if scores else 0.0,
        "mean": mean,
        "stddev": stddev,
        "p20": p20,
        "p80": p80,
        "tp": tp,
        "fp": fp,
        "fn_missed": fn_flagged,
        "precision": precision,
        "recall_lb": recall_lb,
        "latency_per_exchange_s": elapsed / n_scored if n_scored else 0.0,
        "scores": scores,
    }


# ── main ───────────────────────────────────────────────────────────────────


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--db", default="/tmp/cohort2.db")
    parser.add_argument(
        "--session",
        default="fb3e418c2842fa182d630664f13716fefaabba8415395a62f9377f1d1d1fd630",
        help="session_id to score",
    )
    parser.add_argument(
        "--labels",
        default=str(Path.home() / "Downloads" / "labeled_critical_steps.json"),
        help="path to downloaded label JSON",
    )
    parser.add_argument(
        "--models",
        nargs="+",
        default=["gemma3:12b", "gemma4:e4b"],
        help="Ollama model slugs to compare",
    )
    parser.add_argument(
        "--limit",
        type=int,
        default=0,
        help="Score only first N scorable exchanges per model (0 = all). Useful for quick comparison.",
    )
    parser.add_argument(
        "--output",
        default=str(Path(__file__).parent / "exp_observer_compare_results.json"),
        help="where to write JSON results",
    )
    args = parser.parse_args()

    session_id = args.session
    label_path = Path(args.labels)
    labels = _load_labels(str(label_path), session_id) if label_path.exists() else {}
    if not labels:
        print(f"[warn] no labels found for session {session_id[:12]}... in {label_path}")

    detect_cfg = CriticalStepConfig()  # production defaults

    all_results = []

    for model_slug in args.models:
        print(f"\n{'='*60}")
        print(f"Model: {model_slug}")
        print(f"{'='*60}")

        # Fresh in-memory copy per model so runs are independent
        mem_conn = _copy_session_to_memory(args.db, session_id)

        try:
            result = run_model(mem_conn, session_id, model_slug, detect_cfg, labels, limit=args.limit)
        except Exception as e:
            print(f"  [ERROR] {e}")
            result = {"model": model_slug, "error": str(e)}
            all_results.append(result)
            continue
        finally:
            mem_conn.close()

        all_results.append({k: v for k, v in result.items() if k != "scores"})

        print(f"  Scored:            {result['n_scored']} exchanges")
        print(f"  Failure critical:  {result['n_failure_critical']}")
        print(f"  % zero scores:     {result['pct_zero']:.1f}%")
        print(f"  Mean / StdDev:     {result['mean']:.4f} / {result['stddev']:.4f}")
        print(f"  P20 / P80:         {result['p20']:.4f} / {result['p80']:.4f}")
        if labels:
            prec = f"{result['precision']:.3f}" if result['precision'] is not None else "n/a"
            rec  = f"{result['recall_lb']:.3f}" if result['recall_lb'] is not None else "n/a"
            print(f"  Precision:         {prec}  (TP={result['tp']} FP={result['fp']})")
            print(f"  Recall lb:         {rec}  (FN missed={result['fn_missed']})")
        print(f"  Latency/exchange:  {result['latency_per_exchange_s']:.2f}s")
        print("\n  Score distribution:")
        print(_ascii_histogram(result["scores"]))

    # Summary table
    print(f"\n\n{'='*60}")
    print("SUMMARY")
    print(f"{'='*60}")
    header = f"{'Model':<22} {'%0':>5} {'mean':>7} {'std':>7} {'p20':>6} {'p80':>6} {'prec':>7} {'rec':>7} {'lat/ex':>8}"
    print(header)
    print("-" * len(header))
    for r in all_results:
        if "error" in r:
            print(f"{r['model']:<22} ERROR: {r['error']}")
            continue
        prec = f"{r['precision']:.3f}" if r.get('precision') is not None else "  n/a"
        rec  = f"{r['recall_lb']:.3f}" if r.get('recall_lb') is not None else "  n/a"
        print(
            f"{r['model']:<22} {r['pct_zero']:>5.1f} {r['mean']:>7.4f} {r['stddev']:>7.4f}"
            f" {r['p20']:>6.4f} {r['p80']:>6.4f} {prec:>7} {rec:>7} {r['latency_per_exchange_s']:>7.2f}s"
        )

    out_path = Path(args.output)
    out_path.write_text(json.dumps({"session_id": session_id, "results": all_results}, indent=2))
    print(f"\nResults written to {out_path}")


if __name__ == "__main__":
    main()
