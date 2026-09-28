"""Level 1a evaluation — frequency memory quality from Pipeline 2a.

Annotation files live in annotations/level1a.json with schema:
{
  "annotation_type": "level1a",
  "notes": "optional description",
  "expected_memories": [
    {
      "entity_name": "pytest",
      "entity_type": "technology",
      "memory_type": "procedural",
      "content_hint": "Uses TDD approach"
    }
  ]
}
"""

from __future__ import annotations
import json
from dataclasses import dataclass, field
from pathlib import Path

from agent_trace_signals.db.store import SQLiteStore


@dataclass
class Level1aReport:
    total_frequency_memories: int
    memories_by_type: dict[str, int] = field(default_factory=dict)
    entity_coverage: float = 0.0
    cross_session_memory_count: int = 0
    trivial_memory_count: int = 0
    annotation_recall: float | None = None
    annotation_fpr: float | None = None
    notes: list[str] = field(default_factory=list)

    def passes_targets(self) -> bool | None:
        """Returns None if can't evaluate (no annotations), True/False otherwise."""
        if self.annotation_recall is None:
            return None
        return self.annotation_recall > 0.60 and self.annotation_fpr < 0.25


class Level1aEvaluator:
    """
    Evaluate frequency memory quality from Pipeline 2a (Light Analytics).

    Usage:
        evaluator = Level1aEvaluator(store)
        report = evaluator.evaluate("annotations")
        evaluator.print_summary(report)
    """

    def __init__(self, store: SQLiteStore) -> None:
        self.store = store

    def evaluate(self, annotations_dir: str | Path) -> Level1aReport:
        """Evaluate frequency memory quality.

        Mode A: Always runs — structural quality metrics.
        Mode B: If annotations/level1a.json exists — recall and FP metrics.
        """
        # Mode A: Structural quality
        report = self._eval_structural_quality()

        # Mode B: Recall and FP (if annotation file exists)
        ann_path = Path(annotations_dir) / "level1a.json"
        if ann_path.exists():
            recall, fpr = self._eval_recall_and_fpr(ann_path)
            report.annotation_recall = recall
            report.annotation_fpr = fpr

        return report

    def print_summary(self, report: Level1aReport) -> None:
        """Print a formatted summary of evaluation results."""
        print(f"\n{'='*80}")
        print("Level 1a Evaluation — Frequency Memory Quality")
        print(f"{'='*80}\n")

        print("Structural Metrics (no annotations required):")
        print(f"  Total frequency memories:     {report.total_frequency_memories}")
        print("  Type distribution:")
        for memory_type in ["procedural", "preference", "episodic"]:
            count = report.memories_by_type.get(memory_type, 0)
            print(f"    {memory_type:<15} {count}")
        print(f"  Entity coverage (with >=1 mem): {report.entity_coverage:.1%}")
        print(f"  Cross-session memories (ev>=2): {report.cross_session_memory_count}")
        print(f"  Trivial memories (content<10w): {report.trivial_memory_count}")

        if report.annotation_recall is not None:
            print("\nAnnotation-based Metrics (with level1a.json):")
            print(f"  Recall:                       {report.annotation_recall:.3f}  (target > 0.60)")
            print(f"  False positive rate:          {report.annotation_fpr:.3f}  (target < 0.25)")
            passes = report.passes_targets()
            status = "[PASS]" if passes else "[FAIL]"
            print(f"  {status} Overall quality targets")
        else:
            print("\nAnnotation-based Metrics:")
            print("  [No annotations/level1a.json found]")
            print("  To enable recall/FP evaluation, create annotations/level1a.json:")
            print("    {\n"
                  '      "annotation_type": "level1a",\n'
                  '      "expected_memories": [\n'
                  '        {\n'
                  '          "entity_name": "pytest",\n'
                  '          "entity_type": "technology",\n'
                  '          "memory_type": "procedural",\n'
                  '          "content_hint": "test-driven development"\n'
                  '        }\n'
                  '      ]\n'
                  "    }")

        if report.notes:
            print("\nNotes:")
            for note in report.notes:
                print(f"  - {note}")

        print(f"{'='*80}\n")

    # ------------------------------------------------------------------
    # Internal evaluation helpers
    # ------------------------------------------------------------------

    def _eval_structural_quality(self) -> Level1aReport:
        """Compute structural metrics: counts, types, coverage, evidence, triviality."""
        # Total frequency memories
        total_row = self.store.conn.execute(
            "SELECT COUNT(*) FROM memories WHERE extraction_method = 'frequency'"
        ).fetchone()
        total_memories = total_row[0] if total_row else 0

        report = Level1aReport(total_frequency_memories=total_memories)

        if total_memories == 0:
            return report

        # Type distribution
        type_rows = self.store.conn.execute(
            """SELECT memory_type, COUNT(*) FROM memories
               WHERE extraction_method = 'frequency'
               GROUP BY memory_type"""
        ).fetchall()
        report.memories_by_type = {row[0]: row[1] for row in type_rows}

        # Cross-session memory count (evidence_count >= 2)
        cross_session_row = self.store.conn.execute(
            """SELECT COUNT(*) FROM memories
               WHERE extraction_method = 'frequency' AND evidence_count >= 2"""
        ).fetchone()
        report.cross_session_memory_count = cross_session_row[0] if cross_session_row else 0

        # Trivial memory count (content < 10 words)
        trivial_row = self.store.conn.execute(
            """SELECT COUNT(*) FROM memories
               WHERE extraction_method = 'frequency'
                 AND (
                   LENGTH(content) - LENGTH(REPLACE(content, ' ', '')) + 1
                 ) < 10"""
        ).fetchone()
        report.trivial_memory_count = trivial_row[0] if trivial_row else 0

        # Entity coverage: promoted entities with at least 1 frequency memory
        total_promoted_row = self.store.conn.execute(
            "SELECT COUNT(*) FROM entities WHERE memory_promoted IS NOT NULL"
        ).fetchone()
        total_promoted = total_promoted_row[0] if total_promoted_row else 0

        if total_promoted > 0:
            entities_with_mem_row = self.store.conn.execute(
                """SELECT COUNT(DISTINCT e.id)
                   FROM entities e
                   JOIN occurrences o ON o.entity_id = e.id
                   JOIN memory_sources ms ON ms.session_id = o.session_id
                   JOIN memories m ON m.id = ms.memory_id
                   WHERE m.extraction_method = 'frequency'
                     AND e.memory_promoted IS NOT NULL"""
            ).fetchone()
            entities_with_mem = entities_with_mem_row[0] if entities_with_mem_row else 0
            report.entity_coverage = entities_with_mem / total_promoted
        else:
            report.entity_coverage = 0.0

        return report

    def _eval_recall_and_fpr(self, ann_path: Path) -> tuple[float, float]:
        """Evaluate recall and false positive rate against annotation file.

        FPR computation:
        - If labeled_memories present: FPR = incorrect / (correct + incorrect)
          (true precision-based measure — requires human labels per memory)
        - Otherwise: FPR = (total_frequency - matched) / total_frequency
          (annotation-coverage proxy — misleading when annotations are sparse)
        """
        ann = json.loads(ann_path.read_text())
        expected_memories = ann.get("expected_memories", [])
        labeled_memories = ann.get("labeled_memories", [])

        matched_memory_ids: set[str] = set()

        # --- Recall (uses expected_memories) ---
        if not expected_memories:
            recall = 0.0
        else:
            for expected in expected_memories:
                entity_name = expected.get("entity_name", "").lower()
                memory_type = expected.get("memory_type", "").lower()
                content_hint = expected.get("content_hint", "").lower()
                if not entity_name or not content_hint:
                    continue
                hint_words = set(w.lower() for w in content_hint.split() if w and len(w) > 1)
                memory_rows = self.store.conn.execute(
                    """SELECT DISTINCT m.id, m.content, m.memory_type
                       FROM memories m
                       JOIN memory_sources ms ON ms.memory_id = m.id
                       JOIN occurrences o ON o.session_id = ms.session_id
                       JOIN entities e ON e.id = o.entity_id
                       WHERE m.extraction_method = 'frequency'
                         AND LOWER(e.canonical_name) = ?""",
                    (entity_name,),
                ).fetchall()
                for memory_id, content, mem_type in memory_rows:
                    content_lower = content.lower()
                    if all(word in content_lower for word in hint_words):
                        if memory_type and mem_type.lower() != memory_type:
                            continue
                        matched_memory_ids.add(memory_id)
                        break
            recall = len(matched_memory_ids) / len(expected_memories)

        # --- FPR ---
        if labeled_memories:
            correct = sum(1 for lm in labeled_memories if lm.get("label") == "correct")
            incorrect = sum(1 for lm in labeled_memories if lm.get("label") == "incorrect")
            total_labeled = correct + incorrect  # exclude "partial" from denominator
            fpr = incorrect / total_labeled if total_labeled > 0 else 0.0
        else:
            total_row = self.store.conn.execute(
                "SELECT COUNT(*) FROM memories WHERE extraction_method = 'frequency'"
            ).fetchone()
            total_frequency = total_row[0] if total_row else 0
            if not expected_memories or total_frequency == 0:
                fpr = 1.0
            else:
                unmatched = total_frequency - len(matched_memory_ids)
                fpr = unmatched / total_frequency

        return recall, fpr
