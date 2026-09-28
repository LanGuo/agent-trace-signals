"""Level 0 evaluation — ingestion quality against a hand-labeled corpus.

Annotation files live in annotations/<session_id>.json with schema:
{
  "session_id": "...",
  "source_file": "...",          # abs_path to the source JSONL
  "source_type": "agent_trace|meeting_transcript",
  "expected_entities": [
    {"canonical_name": "PR #1234", "entity_type": "pr"}
  ],
  "expected_occurrences": [
    {"entity_canonical": "PR #1234", "role": "subject", "chunk_index": 2}
  ],
  "expected_explicit_memories": [
    {"memory_type": "episodic", "content_hint": "PR #1234 was merged"}
  ],
  "expected_structural_edges": [
    {"edge_type": "structural", "via_entity": "PR #1234"}
  ]
}
"""

from __future__ import annotations
import json
from dataclasses import dataclass, field
from pathlib import Path

from agent_trace_signals.db.store import SQLiteStore

_CONFIDENCE_RANK = {"raw": 0, "llm_verified": 1, "session": 2, "corpus": 3}


@dataclass
class EntityMetrics:
    true_positives: int = 0
    false_negatives: int = 0     # labeled but not extracted
    false_positives: int = 0     # extracted but not labeled

    @property
    def recall(self) -> float:
        denom = self.true_positives + self.false_negatives
        return self.true_positives / denom if denom > 0 else 0.0

    @property
    def precision(self) -> float:
        denom = self.true_positives + self.false_positives
        return self.true_positives / denom if denom > 0 else 0.0

    @property
    def f1(self) -> float:
        p, r = self.precision, self.recall
        return 2 * p * r / (p + r) if (p + r) > 0 else 0.0


@dataclass
class Level0Report:
    session_id: str
    source_file: str
    entity_metrics: dict[str, EntityMetrics] = field(default_factory=dict)  # by entity_type
    entity_overall: EntityMetrics = field(default_factory=EntityMetrics)
    occurrence_recall: float = 0.0       # fraction of labeled occurrences found
    structural_edge_precision: float = 0.0
    embedding_coverage: float = 0.0      # fraction of records with non-null embedding
    explicit_memory_count: int = 0
    notes: list[str] = field(default_factory=list)

    def summary_line(self) -> str:
        return (
            f"{self.session_id[:12]}  "
            f"entity_recall={self.entity_overall.recall:.2f}  "
            f"entity_prec={self.entity_overall.precision:.2f}  "
            f"occ_recall={self.occurrence_recall:.2f}  "
            f"embed_cov={self.embedding_coverage:.2f}  "
            f"memories={self.explicit_memory_count}"
        )


class Level0Evaluator:
    """
    Evaluate ingestion quality for a single annotated session.

    Usage:
        evaluator = Level0Evaluator(store)
        report = evaluator.evaluate(annotation_path)
    """

    def __init__(self, store: SQLiteStore) -> None:
        self.store = store

    def evaluate(self, annotation_path: str | Path, min_confidence: str = "raw") -> Level0Report:
        ann = json.loads(Path(annotation_path).read_text())
        session_id = ann["session_id"]
        source_file = ann.get("source_file", "")
        report = Level0Report(
            session_id=session_id,
            source_file=source_file,
        )

        # Check session exists — fall back to workspace match if id changed
        if not self.store.session_exists(session_id):
            resolved = self._resolve_session_by_path(source_file, session_id)
            if resolved:
                session_id = resolved
                report.session_id = session_id
                report.notes.append(f"Resolved session by source path → {session_id[:16]}")
            else:
                report.notes.append(f"Session {session_id[:16]} not found in DB — not yet ingested")
                return report

        # Entity metrics
        report.entity_metrics, report.entity_overall = self._eval_entities(
            session_id, ann.get("expected_entities", []), min_confidence=min_confidence
        )

        # Occurrence recall
        report.occurrence_recall = self._eval_occurrences(
            session_id, ann.get("expected_occurrences", [])
        )

        # Structural edge precision
        report.structural_edge_precision = self._eval_structural_edges(
            session_id, ann.get("expected_structural_edges", [])
        )

        # Embedding coverage
        report.embedding_coverage = self._eval_embedding_coverage(session_id)

        # Explicit memory count
        report.explicit_memory_count = self._count_explicit_memories(session_id)

        return report

    def evaluate_corpus(self, annotations_dir: str | Path, min_confidence: str = "raw") -> list[Level0Report]:
        """Evaluate all annotation files in a directory."""
        ann_dir = Path(annotations_dir)
        reports = []
        for ann_file in sorted(ann_dir.glob("*.json")):
            report = self.evaluate(ann_file, min_confidence=min_confidence)
            reports.append(report)
        return reports

    def print_summary(self, reports: list[Level0Report]) -> None:
        """Print a formatted summary of evaluation results."""
        if not reports:
            print("No reports to summarize.")
            return

        print(f"\n{'='*80}")
        print(f"Level 0 Evaluation — {len(reports)} sessions")
        print(f"{'='*80}")

        for r in reports:
            print(f"  {r.summary_line()}")
            for note in r.notes:
                print(f"    NOTE: {note}")

        # Aggregate across sessions
        ingested = [r for r in reports if r.entity_overall.true_positives + r.entity_overall.false_negatives > 0]
        if not ingested:
            print("\nNo ingested sessions to aggregate.")
            return

        avg_entity_recall = sum(r.entity_overall.recall for r in ingested) / len(ingested)
        avg_entity_prec = sum(r.entity_overall.precision for r in ingested) / len(ingested)
        avg_occ_recall = sum(r.occurrence_recall for r in ingested) / len(ingested)
        avg_embed_cov = sum(r.embedding_coverage for r in ingested) / len(ingested)

        print(f"\nAGGREGATE ({len(ingested)} ingested sessions):")
        print(f"  Entity recall:    {avg_entity_recall:.3f}  (target > 0.80)")
        print(f"  Entity precision: {avg_entity_prec:.3f}  (target > 0.90)")
        print(f"  Occ recall:       {avg_occ_recall:.3f}")
        print(f"  Embed coverage:   {avg_embed_cov:.3f}  (target = 1.00)")

        # Per-type breakdown
        all_types: set[str] = set()
        for r in ingested:
            all_types.update(r.entity_metrics.keys())
        if all_types:
            print("\n  Entity recall by type:")
            for et in sorted(all_types):
                type_reports = [r for r in ingested if et in r.entity_metrics]
                if type_reports:
                    avg = sum(r.entity_metrics[et].recall for r in type_reports) / len(type_reports)
                    print(f"    {et:<15} {avg:.3f}")

        print(f"{'='*80}\n")

    # ------------------------------------------------------------------
    # Internal evaluation helpers
    # ------------------------------------------------------------------

    def _eval_entities(
        self, session_id: str, expected: list[dict], min_confidence: str = "raw"
    ) -> tuple[dict[str, EntityMetrics], EntityMetrics]:
        """Compare extracted entities to ground truth."""
        # Fetch extracted entities for this session, filtering by confidence
        min_rank = _CONFIDENCE_RANK.get(min_confidence, 0)
        rows = self.store.conn.execute(
            """SELECT DISTINCT e.canonical_name, e.entity_type
               FROM entities e
               JOIN occurrences o ON o.entity_id = e.id
               WHERE o.session_id = ?
                 AND (? = 0 OR CASE e.confidence
                       WHEN 'raw'          THEN 0
                       WHEN 'llm_verified' THEN 1
                       WHEN 'session'      THEN 2
                       WHEN 'corpus'       THEN 3
                       ELSE 0 END >= ?)""",
            (session_id, min_rank, min_rank),
        ).fetchall()
        extracted: set[tuple[str, str]] = {
            (r[0].lower(), r[1].lower()) for r in rows
        }

        # Build expected set
        expected_set: set[tuple[str, str]] = {
            (e["canonical_name"].lower(), e["entity_type"].lower())
            for e in expected
        }

        # Per-type metrics
        all_types = {e[1] for e in expected_set} | {e[1] for e in extracted}
        type_metrics: dict[str, EntityMetrics] = {}
        for et in all_types:
            exp_t = {e for e in expected_set if e[1] == et}
            ext_t = {e for e in extracted if e[1] == et}
            m = EntityMetrics(
                true_positives=len(exp_t & ext_t),
                false_negatives=len(exp_t - ext_t),
                false_positives=len(ext_t - exp_t),
            )
            type_metrics[et] = m

        overall = EntityMetrics(
            true_positives=len(expected_set & extracted),
            false_negatives=len(expected_set - extracted),
            false_positives=len(extracted - expected_set),
        )
        return type_metrics, overall

    def _eval_occurrences(self, session_id: str, expected: list[dict]) -> float:
        """Fraction of labeled occurrences found (recall only — no ground-truth for FP)."""
        if not expected:
            return 1.0

        found = 0
        for exp_occ in expected:
            entity_name = exp_occ["entity_canonical"].lower()
            role = exp_occ.get("role", "").lower()
            # Check if there's an occurrence for this entity+role in this session
            row = self.store.conn.execute(
                """SELECT 1 FROM occurrences o
                   JOIN entities e ON e.id = o.entity_id
                   WHERE o.session_id = ?
                     AND LOWER(e.canonical_name) = ?
                     AND (? = '' OR LOWER(o.role) = ?)
                   LIMIT 1""",
                (session_id, entity_name, role, role),
            ).fetchone()
            if row:
                found += 1

        return found / len(expected)

    def _eval_structural_edges(self, session_id: str, expected: list[dict]) -> float:
        """Precision of structural edges (structural edges that exist divided by total extracted)."""
        total_edges = self.store.conn.execute(
            """SELECT COUNT(*) FROM session_graph_edges
               WHERE source_session_id = ? AND edge_type = 'structural'""",
            (session_id,),
        ).fetchone()[0]

        if total_edges == 0:
            return 1.0 if not expected else 0.0

        # For now: check how many expected edges are present
        correct = 0
        for exp_edge in expected:
            via = exp_edge.get("via_entity", "").lower()
            row = self.store.conn.execute(
                """SELECT 1 FROM session_graph_edges sge
                   JOIN entities e ON e.id = sge.via_entity_id
                   WHERE sge.source_session_id = ?
                     AND sge.edge_type = 'structural'
                     AND LOWER(e.canonical_name) = ?
                   LIMIT 1""",
                (session_id, via),
            ).fetchone()
            if row:
                correct += 1

        return correct / total_edges if total_edges > 0 else 0.0

    def _eval_embedding_coverage(self, session_id: str) -> float:
        """Fraction of records that have an embedding."""
        total = self.store.conn.execute(
            "SELECT COUNT(*) FROM records WHERE session_id = ?", (session_id,)
        ).fetchone()[0]
        if total == 0:
            return 1.0
        embedded = self.store.conn.execute(
            """SELECT COUNT(*) FROM record_embeddings re
               JOIN records r ON r.id = re.record_id
               WHERE r.session_id = ?""",
            (session_id,),
        ).fetchone()[0]
        return embedded / total

    def _count_explicit_memories(self, session_id: str) -> int:
        row = self.store.conn.execute(
            """SELECT COUNT(*) FROM memories m
               JOIN memory_sources ms ON ms.memory_id = m.id
               WHERE ms.session_id = ? AND m.extraction_method = 'explicit'""",
            (session_id,),
        ).fetchone()
        return row[0] if row else 0

    def _resolve_session_by_path(self, source_file: str, fallback_id: str) -> str | None:
        """Find the actual session_id given a source file path.

        Derives workspace_id from the path and matches against ingested sessions.
        Returns the most recent (highest chunk_count) matching session, or None.
        """
        if not source_file:
            return None
        from pathlib import Path as _Path
        p = _Path(source_file)
        # workspace_id is the project directory name under ~/.claude/projects/
        parts = p.parts
        try:
            idx = parts.index("projects")
            workspace_id = parts[idx + 1]
        except (ValueError, IndexError):
            return None

        row = self.store.conn.execute(
            """SELECT id FROM sessions
               WHERE workspace_id=? AND source_plugin='claude_code'
               ORDER BY chunk_count DESC LIMIT 1""",
            (workspace_id,),
        ).fetchone()
        return row[0] if row else None
