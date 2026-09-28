"""CLI entry point — `ats` command."""

from __future__ import annotations
import json
import logging
from pathlib import Path

import click
from rich.console import Console
from rich.table import Table

logging.basicConfig(level=logging.WARNING, format="%(levelname)s %(name)s: %(message)s")

console = Console()


def _get_store(db_path: str):
    from agent_trace_signals.db.schema import open_db, create_all, migrate
    from agent_trace_signals.db.store import SQLiteStore
    conn = open_db(db_path)
    migrate(conn)
    create_all(conn)
    return SQLiteStore(conn)


def _build_pipeline(db_path: str, config=None):
    from agent_trace_signals.config import Config
    from agent_trace_signals.db.schema import open_db, create_all, migrate
    from agent_trace_signals.db.store import SQLiteStore
    from agent_trace_signals.providers.ollama import OllamaProvider
    from agent_trace_signals.embedder import Embedder
    from agent_trace_signals.pipeline.summarizer import Summarizer
    from agent_trace_signals.pipeline.entity_resolver import EntityResolver
    from agent_trace_signals.pipeline.occurrence_extractor import OccurrenceExtractor
    from agent_trace_signals.pipeline.ingestion import IngestionPipeline

    cfg = config or Config.default()
    if db_path != cfg.db_path:
        cfg.db_path = db_path

    conn = open_db(cfg.db_path)
    migrate(conn)
    create_all(conn)
    store = SQLiteStore(conn)
    provider = OllamaProvider(cfg.models)
    embedder = Embedder(cfg.models)

    return IngestionPipeline(
        store=store,
        summarizer=Summarizer(provider, cfg.models),
        embedder=embedder,
        entity_resolver=EntityResolver(store, cfg.pipeline),
        occurrence_extractor=OccurrenceExtractor(cfg.pipeline),
        config=cfg,
    ), store, cfg, provider


@click.group()
@click.option("--db", default="traces.db", show_default=True, help="SQLite DB path")
@click.pass_context
def cli(ctx, db: str):
    """Agent Trace Signals — memory layer for coding agent traces."""
    ctx.ensure_object(dict)
    ctx.obj["db"] = db


@cli.command()
@click.pass_context
def scan(ctx):
    """Scan configured paths and show pending files."""
    from agent_trace_signals.config import Config
    from agent_trace_signals.scanner.corpus_scanner import CorpusScanner

    store = _get_store(ctx.obj["db"])
    cfg = Config.default()
    scanner = CorpusScanner(store, cfg.scanner)
    store.conn.commit()

    pending = scanner.scan()
    store.conn.commit()

    summary = scanner.status_summary()
    console.print("\n[bold]Corpus scan complete.[/bold]")
    console.print(f"  Pending:  {summary.get('pending', 0)}")
    console.print(f"  Ingested: {summary.get('ingested', 0)}")
    console.print(f"  Failed:   {summary.get('failed', 0)}")
    console.print(f"\n[bold]{len(pending)} files queued for ingestion.[/bold]\n")

    if pending:
        t = Table("File", "Type", "Size")
        for s in pending[:20]:
            size = Path(s.abs_path).stat().st_size if Path(s.abs_path).exists() else 0
            t.add_row(Path(s.abs_path).name[:60], s.file_type, f"{size:,} B")
        console.print(t)
        if len(pending) > 20:
            console.print(f"  ... and {len(pending) - 20} more")


@cli.command()
@click.option("--limit", default=0, help="Max sessions to ingest (0 = all)")
@click.option("--prefix", default="", help="Only ingest files whose name starts with this prefix")
@click.option("--dry-run", is_flag=True, help="Scan only, don't ingest")
@click.option("--no-memories", is_flag=True, help="Skip memory extraction (faster)")
@click.option("--no-summaries", is_flag=True, help="Skip chunk/session summaries (fastest, for eval)")
@click.option("--workers", default=4, show_default=True, help="Parallel chunk threads per session (lower on memory-constrained machines)")
@click.option("-v", "--verbose", is_flag=True, default=False, help="Show per-chunk progress and pipeline INFO logs.")
@click.pass_context
def ingest(ctx, limit: int, prefix: str, dry_run: bool, no_memories: bool, no_summaries: bool, workers: int, verbose: bool):
    """Ingest pending session files."""
    if verbose:
        logging.getLogger("agent_trace_signals").setLevel(logging.INFO)
    from agent_trace_signals.scanner.corpus_scanner import CorpusScanner
    from agent_trace_signals.plugins.claude_code import ClaudeCodeSource
    from agent_trace_signals.plugins.gemini_cli import GeminiSource
    from agent_trace_signals.plugins.opencode import OpencodeSource

    pipeline, store, cfg, _ = _build_pipeline(ctx.obj["db"])
    pipeline.skip_memories = no_memories or no_summaries
    pipeline.skip_summaries = no_summaries
    pipeline.max_workers = workers
    scanner = CorpusScanner(store, cfg.scanner)
    pending = scanner.scan()
    store.conn.commit()

    if prefix:
        pending = [s for s in pending if Path(s.abs_path).name.startswith(prefix)]

    if dry_run:
        console.print(f"[bold]Dry run:[/bold] {len(pending)} files would be ingested.")
        return

    if limit > 0:
        pending = pending[:limit]

    plugins = [ClaudeCodeSource(cfg.pipeline), GeminiSource(cfg.pipeline), OpencodeSource(cfg.pipeline)]

    ingested = 0
    failed = 0
    for state in pending:
        plugin = next((p for p in plugins if p.can_handle(state.abs_path)), None)
        if plugin is None:
            console.print(f"[yellow]No plugin for {state.abs_path}[/yellow]")
            scanner.mark_failed(state.id, "no plugin")
            failed += 1
            continue

        try:
            console.print(f"Ingesting [cyan]{Path(state.abs_path).name}[/cyan]...")
            parsed = plugin.parse(state.abs_path)
            session = pipeline.run(parsed, state.abs_path, state.content_hash)
            scanner.mark_ingested(state.id, session.id)
            store.conn.commit()
            ingested += 1
        except Exception as e:
            console.print(f"[red]Failed: {e}[/red]")
            scanner.mark_failed(state.id, str(e))
            store.conn.commit()
            failed += 1

    stats = store.get_session_stats()
    console.print("\n[bold]Ingestion complete.[/bold]")
    console.print(f"  Ingested: {ingested}  Failed: {failed}")
    console.print(f"  DB totals: {stats}")


@cli.command("drop-session")
@click.argument("prefix")
@click.pass_context
def drop_session(ctx, prefix: str):
    """Drop a session and all its data so it can be re-ingested.

    PREFIX is the leading characters of the session ID (e.g. '215d9c1e').
    """
    store = _get_store(ctx.obj["db"])

    rows = store.conn.execute(
        "SELECT id FROM sessions WHERE id LIKE ?", (prefix + "%",)
    ).fetchall()

    if not rows:
        console.print(f"[red]No session found with prefix '{prefix}'[/red]")
        return

    for (session_id,) in rows:
        console.print(f"Dropping [cyan]{session_id[:16]}...[/cyan]")

        # FTS5: clean records_fts for records in this session (before deleting records)
        store.conn.execute(
            "DELETE FROM records_fts WHERE record_id IN "
            "(SELECT id FROM records WHERE session_id = ?)",
            (session_id,),
        )

        # FTS5: clean memories_fts for memories that will become orphaned after this
        # session's sources are removed (memories with no other session source).
        store.conn.execute(
            """DELETE FROM memories_fts WHERE memory_id IN (
                SELECT ms.memory_id FROM memory_sources ms
                WHERE ms.session_id = ?
                  AND ms.memory_id NOT IN (
                      SELECT memory_id FROM memory_sources WHERE session_id != ?
                  )
            )""",
            (session_id, session_id),
        )

        # Delete cascade (FK-ordered)
        store.conn.execute("DELETE FROM memory_sources WHERE session_id = ?", (session_id,))
        # Orphaned memories (no remaining sources) — clean referencing tables first
        store.conn.execute(
            "DELETE FROM memory_embeddings WHERE memory_id NOT IN "
            "(SELECT DISTINCT memory_id FROM memory_sources)"
        )
        store.conn.execute(
            "DELETE FROM memory_retrievals WHERE memory_id NOT IN "
            "(SELECT DISTINCT memory_id FROM memory_sources)"
        )
        store.conn.execute(
            "DELETE FROM memories WHERE id NOT IN (SELECT DISTINCT memory_id FROM memory_sources)"
        )
        store.conn.execute(
            "DELETE FROM session_graph_edges "
            "WHERE source_session_id = ? OR target_session_id = ?",
            (session_id, session_id),
        )
        store.conn.execute("DELETE FROM session_inflections WHERE session_id = ?", (session_id,))
        store.conn.execute("DELETE FROM occurrences WHERE session_id = ?", (session_id,))
        store.conn.execute(
            "DELETE FROM record_embeddings WHERE record_id IN "
            "(SELECT id FROM records WHERE session_id = ?)",
            (session_id,),
        )
        store.conn.execute("DELETE FROM records WHERE session_id = ?", (session_id,))
        store.conn.execute(
            "DELETE FROM session_structural_embeddings WHERE session_id = ?", (session_id,)
        )
        store.conn.execute(
            "DELETE FROM session_topic_embeddings WHERE session_id = ?", (session_id,)
        )
        store.conn.execute("DELETE FROM sessions WHERE id = ?", (session_id,))

        # Remove entities with no remaining occurrences
        store.conn.execute(
            "DELETE FROM entities WHERE id NOT IN (SELECT DISTINCT entity_id FROM occurrences)"
        )

        # Recalculate degree_count for all surviving entities
        store.conn.execute("""
            UPDATE entities SET
                degree_count = (
                    SELECT COUNT(DISTINCT o.session_id)
                    FROM occurrences o WHERE o.entity_id = entities.id
                ),
                confidence = 'raw'
        """)

        # Reset scanner state so the file is picked up again
        store.conn.execute(
            "UPDATE ingestion_state SET status = 'pending', session_id = NULL, "
            "last_ingested_at = NULL WHERE session_id = ?",
            (session_id,),
        )

        store.conn.commit()

        # vec0: clean all orphaned embedding rows (records, entities, occurrences,
        # memories, sessions) after the structured tables are in their final state.
        vec_counts = store.clean_orphaned_embeddings()
        removed = sum(vec_counts.values())
        if removed:
            console.print(f"  Cleaned {removed} orphaned vec0 rows")

        stats = store.get_session_stats()
        console.print(f"  Done. DB totals: {stats}")

    console.print("[green]Session dropped. Run 'ats ingest' to re-ingest.[/green]")


@cli.command("clean-orphans")
@click.pass_context
def clean_orphans(ctx):
    """Remove orphaned vec0 embedding rows left over from dropped or re-ingested sessions.

    Safe to run at any time. Removes vec0 rows whose source rows in records, entities,
    occurrences, memories, or sessions no longer exist.
    """
    store = _get_store(ctx.obj["db"])
    counts = store.clean_orphaned_embeddings()
    total = sum(counts.values())
    if total == 0:
        console.print("[green]No orphaned embeddings found.[/green]")
        return
    for table, n in counts.items():
        if n > 0:
            console.print(f"  [yellow]{table}[/yellow]: removed {n} orphaned rows")
    console.print(f"[green]Total cleaned: {total}[/green]")


@cli.command("recalculate-confidence")
@click.pass_context
def recalculate_confidence(ctx):
    """Recompute entity confidence from occurrence data already in the DB.

    For each entity, finds the maximum number of distinct chunks it appears in
    within any single session. Entities meeting session_promotion_min_chunks
    are promoted to 'session'. Confidence is upgrade-only (never downgrades).
    """
    from agent_trace_signals.config import Config

    store = _get_store(ctx.obj["db"])
    cfg = Config.default()
    min_chunks = cfg.pipeline.session_promotion_min_chunks

    rows = store.conn.execute("""
        SELECT cc.entity_id, MAX(cc.cnt) AS max_chunks
        FROM (
            SELECT o.entity_id, o.session_id, COUNT(DISTINCT r.chunk_index) AS cnt
            FROM occurrences o
            JOIN records r ON r.id = o.record_id
            GROUP BY o.entity_id, o.session_id
        ) cc
        GROUP BY cc.entity_id
    """).fetchall()

    promoted = 0
    for entity_id, max_chunks in rows:
        if max_chunks >= min_chunks:
            store.conn.execute(
                """UPDATE entities SET confidence = CASE
                       WHEN confidence IN ('corpus', 'session') THEN confidence
                       ELSE 'session'
                   END
                   WHERE id = ?""",
                (entity_id,),
            )
            promoted += 1

    store.conn.commit()
    total = len(rows)
    console.print("[bold]Confidence recalculation complete.[/bold]")
    console.print(f"  Checked: {total} entities")
    console.print(f"  Promoted to 'session': {promoted}")


@cli.command()
@click.option("--annotations", default="annotations", show_default=True,
              help="Directory containing annotation JSON files")
@click.option(
    "--min-confidence",
    default="raw",
    show_default=True,
    type=click.Choice(["raw", "llm_verified", "session", "corpus"]),
    help="Only count entities with at least this confidence level",
)
@click.pass_context
def eval0(ctx, annotations: str, min_confidence: str):
    """Run Level 0 evaluation against annotated sessions."""
    from agent_trace_signals.eval.level0 import Level0Evaluator

    store = _get_store(ctx.obj["db"])
    evaluator = Level0Evaluator(store)
    reports = evaluator.evaluate_corpus(annotations, min_confidence=min_confidence)

    if not reports:
        console.print(f"[yellow]No annotation files found in {annotations}/[/yellow]")
        console.print("Create annotation JSON files to enable Level 0 evaluation.")
        return

    if min_confidence != "raw":
        console.print(f"[dim]Filtering: entities with confidence >= '{min_confidence}'[/dim]")
    evaluator.print_summary(reports)


@cli.command("eval1a")
@click.option("--annotations", default="annotations", show_default=True,
              help="Directory containing annotation files (looks for level1a.json)")
@click.pass_context
def eval1a(ctx, annotations: str):
    """Run Level 1a evaluation — frequency memory quality."""
    from agent_trace_signals.eval.level1a import Level1aEvaluator

    store = _get_store(ctx.obj["db"])
    evaluator = Level1aEvaluator(store)
    report = evaluator.evaluate(annotations)
    evaluator.print_summary(report)


@cli.command()
@click.pass_context
def sessions(ctx):
    """List all ingested sessions with quality signals."""
    store = _get_store(ctx.obj["db"])
    rows = store.conn.execute(
        """SELECT id, source_plugin, workspace_id, session_timestamp, chunk_count,
                  LENGTH(session_summary) > 0 AS has_summary
           FROM sessions ORDER BY session_timestamp DESC"""
    ).fetchall()

    if not rows:
        console.print("[yellow]No sessions ingested yet.[/yellow]")
        return

    # Memory counts per session
    mem_rows = store.conn.execute(
        """SELECT ms.session_id, COUNT(DISTINCT m.id) AS cnt
           FROM memory_sources ms JOIN memories m ON m.id = ms.memory_id
           GROUP BY ms.session_id"""
    ).fetchall()
    mem_counts = {r[0]: r[1] for r in mem_rows}

    # Session metadata
    meta_rows = store.conn.execute(
        """SELECT session_id, dominant_model, permission_mode,
                  total_output_tokens, user_turn_count, tool_call_count
           FROM session_metadata"""
    ).fetchall()
    meta_map = {r[0]: r for r in meta_rows}

    # Recovery pattern counts per session
    rec_rows = store.conn.execute(
        """SELECT ms.session_id, COUNT(DISTINCT m.id)
           FROM memory_sources ms JOIN memories m ON m.id = ms.memory_id
           WHERE m.memory_type IN ('pattern_recovery','pattern_inefficiency')
           GROUP BY ms.session_id"""
    ).fetchall()
    rec_counts = {r[0]: r[1] for r in rec_rows}

    t = Table("ID", "Plugin", "Workspace", "Timestamp", "Chunks", "Mem",
              "Recovery", "Summary", "Model", "PermMode", "Turns", "ToolCalls", "OutTok")
    for r in rows:
        sid = r[0]
        sm = meta_map.get(sid)
        model_str = (sm[1] or "—")[:20] if sm else "—"
        perm_str = (sm[2] or "—")[:16] if sm else "—"
        out_tok = f"{sm[3]:,}" if (sm and sm[3]) else "—"
        turns = str(sm[4]) if (sm and sm[4] is not None) else "—"
        tool_calls = str(sm[5]) if (sm and sm[5] is not None) else "—"
        t.add_row(
            sid[:12],
            r[1] or "",
            (r[2] or "")[-30:],
            (r[3] or "")[:19],
            str(r[4] or 0),
            str(mem_counts.get(sid, 0)),
            str(rec_counts.get(sid, 0)),
            "✓" if r[5] else "—",
            model_str,
            perm_str,
            turns,
            tool_calls,
            out_tok,
        )
    console.print(t)


@cli.command()
@click.pass_context
def stats(ctx):
    """Show DB statistics."""
    store = _get_store(ctx.obj["db"])
    s = store.get_session_stats()
    t = Table("Table", "Rows")
    for table, count in s.items():
        t.add_row(table, f"{count:,}")
    console.print(t)


@cli.command("show-entities")
@click.argument("session_prefix")
@click.pass_context
def show_entities(ctx, session_prefix: str):
    """Show all entities extracted for a session (use first 6+ chars of session ID)."""
    store = _get_store(ctx.obj["db"])
    row = store.conn.execute(
        "SELECT id, workspace_id, chunk_count FROM sessions WHERE id LIKE ?",
        (session_prefix + "%",),
    ).fetchone()
    if not row:
        console.print(f"[red]No session found matching '{session_prefix}'[/red]")
        return

    sid, workspace_id, chunk_count = row[0], row[1], row[2]
    console.print(f"\n[bold]{sid[:16]}[/bold]  workspace={workspace_id}  chunks={chunk_count}\n")

    rows = store.conn.execute(
        """SELECT DISTINCT e.entity_type, e.canonical_name, e.confidence
           FROM entities e JOIN occurrences o ON o.entity_id=e.id
           WHERE o.session_id=?
           ORDER BY e.entity_type, e.canonical_name""",
        (sid,),
    ).fetchall()

    cur_type = None
    for r in rows:
        if r[0] != cur_type:
            cur_type = r[0]
            console.print(f"  [cyan]{cur_type}[/cyan]")
        conf_color = {"session": "green", "llm_verified": "yellow", "corpus": "bright_green"}.get(r[2], "dim")
        console.print(f"    [{conf_color}]{r[2]:<12}[/{conf_color}] {r[1]}")
    console.print(f"\n[bold]{len(rows)} entities total[/bold]\n")


@cli.command("annotate-init")
@click.argument("session_prefix")
@click.option("--annotations", default="annotations", show_default=True,
              help="Directory to write annotation file into")
@click.option("--source-file", default="", help="Source JSONL path (optional, for session resolution)")
@click.pass_context
def annotate_init(ctx, session_prefix: str, annotations: str, source_file: str):
    """Generate a pre-filled annotation JSON from extracted entities.

    Writes annotations/<session_id>.json with all extracted entities as
    expected_entities. Open the file and delete the false positives, then
    run eval0 to measure precision.
    """
    import json
    from pathlib import Path

    store = _get_store(ctx.obj["db"])
    row = store.conn.execute(
        "SELECT id, workspace_id, chunk_count FROM sessions WHERE id LIKE ?",
        (session_prefix + "%",),
    ).fetchone()
    if not row:
        console.print(f"[red]No session found matching '{session_prefix}'[/red]")
        return

    sid, workspace_id, chunk_count = row[0], row[1], row[2]

    # Fetch all extracted entities for this session
    entity_rows = store.conn.execute(
        """SELECT DISTINCT e.entity_type, e.canonical_name,
                  MAX(o.role) as role
           FROM entities e JOIN occurrences o ON o.entity_id=e.id
           WHERE o.session_id=?
           GROUP BY e.entity_type, e.canonical_name
           ORDER BY e.entity_type, e.canonical_name""",
        (sid,),
    ).fetchall()

    expected_entities = [
        {"canonical_name": r[1], "entity_type": r[0]}
        for r in entity_rows
    ]

    # One occurrence per entity (using the most common role)
    occ_rows = store.conn.execute(
        """SELECT e.canonical_name, e.entity_type, o.role
           FROM entities e JOIN occurrences o ON o.entity_id=e.id
           WHERE o.session_id=?
           GROUP BY e.entity_type, e.canonical_name
           ORDER BY e.entity_type, e.canonical_name""",
        (sid,),
    ).fetchall()

    expected_occurrences = [
        {"entity_canonical": r[0], "role": r[2]}
        for r in occ_rows
    ]

    annotation = {
        "session_id": sid,
        "source_file": source_file or f"~/.claude/projects/{workspace_id}/<session>.jsonl",
        "source_type": "agent_trace",
        "notes": f"Auto-generated from {len(expected_entities)} extracted entities. Delete false positives.",
        "expected_entities": expected_entities,
        "expected_occurrences": expected_occurrences,
        "expected_explicit_memories": [],
        "expected_structural_edges": [],
    }

    ann_dir = Path(annotations)
    ann_dir.mkdir(parents=True, exist_ok=True)
    out_path = ann_dir / f"{sid}.json"
    out_path.write_text(json.dumps(annotation, indent=2))

    console.print(f"\n[green]Written:[/green] {out_path}")
    console.print(f"  {len(expected_entities)} entities pre-filled ({chunk_count} chunks)")
    console.print("\n[bold]Next:[/bold] open the file, delete false positives, then run:")
    console.print(f"  ats --db {ctx.obj['db']} eval0 --annotations {annotations}/\n")

    # Print a summary grouped by type for quick review
    from collections import Counter
    type_counts = Counter(e["entity_type"] for e in expected_entities)
    console.print("  Entity types:")
    for et, cnt in sorted(type_counts.items()):
        console.print(f"    {et:<15} {cnt}")
    console.print()


@cli.command("annotate-add")
@click.argument("session_prefix")
@click.argument("entity_type", type=click.Choice(
    ["file", "tool", "technology", "commit", "pr", "person", "org", "concept"]
))
@click.argument("canonical_name")
@click.option("--annotations", default="annotations", show_default=True)
@click.option("--role", default="", help="Expected occurrence role (leave blank to skip occurrence)")
@click.pass_context
def annotate_add(ctx, session_prefix, entity_type, canonical_name, annotations, role):
    """Add a missed entity to an existing annotation file.

    Example (entity the pipeline missed):\n
        ats annotate-add ae3fc1 file /path/to/missed.py --role file_modified\n
        ats annotate-add ae3fc1 technology TileDB
    """
    import json
    from pathlib import Path

    store = _get_store(ctx.obj["db"])
    row = store.conn.execute(
        "SELECT id FROM sessions WHERE id LIKE ?", (session_prefix + "%",)
    ).fetchone()
    if not row:
        console.print(f"[red]No session found matching '{session_prefix}'[/red]")
        return
    sid = row[0]

    ann_dir = Path(annotations)
    out_path = ann_dir / f"{sid}.json"
    if not out_path.exists():
        console.print(f"[red]No annotation file at {out_path}[/red]")
        console.print(f"Run: ats --db {ctx.obj['db']} annotate-init {session_prefix}")
        return

    ann = json.loads(out_path.read_text())

    # Check for duplicate
    existing = {(e["entity_type"], e["canonical_name"]) for e in ann.get("expected_entities", [])}
    if (entity_type, canonical_name) in existing:
        console.print(f"[yellow]Already in annotation:[/yellow] {entity_type}:{canonical_name}")
        return

    ann.setdefault("expected_entities", []).append(
        {"canonical_name": canonical_name, "entity_type": entity_type}
    )
    if role:
        ann.setdefault("expected_occurrences", []).append(
            {"entity_canonical": canonical_name, "role": role}
        )

    out_path.write_text(json.dumps(ann, indent=2))
    console.print(f"[green]Added[/green] {entity_type}:{canonical_name}" +
                  (f" (occurrence role={role})" if role else ""))
    console.print(f"  Annotation now has {len(ann['expected_entities'])} entities")


@cli.command("show-chunks")
@click.argument("session_prefix")
@click.option("--start", default=0, help="First chunk index to show")
@click.option("--count", default=5, show_default=True, help="Number of chunks to show")
@click.pass_context
def show_chunks(ctx, session_prefix, start, count):
    """Show chunk text from a session to help identify missed entities."""
    store = _get_store(ctx.obj["db"])
    row = store.conn.execute(
        "SELECT id, chunk_count FROM sessions WHERE id LIKE ?", (session_prefix + "%",)
    ).fetchone()
    if not row:
        console.print(f"[red]No session found matching '{session_prefix}'[/red]")
        return
    sid, total = row[0], row[1]

    rows = store.conn.execute(
        "SELECT chunk_index, chunk_text FROM records WHERE session_id=? "
        "AND chunk_index >= ? ORDER BY chunk_index LIMIT ?",
        (sid, start, count),
    ).fetchall()

    console.print(f"\n[bold]{sid[:16]}[/bold]  chunks {start}–{start+count-1} of {total}\n")
    for r in rows:
        console.print(f"[cyan]── chunk {r[0]} ──[/cyan]")
        console.print(r[1][:800])
        console.print()


@cli.command("annotate-view")
@click.argument("session_prefix")
@click.option("--annotations", default="annotations", show_default=True,
              help="Directory containing annotation JSON files")
@click.option("--out", default="", help="Output HTML path (default: annotations/<session_id>.html)")
@click.pass_context
def annotate_view(ctx, session_prefix: str, annotations: str, out: str):
    """Generate a self-contained HTML annotation viewer for a session.

    Opens in any browser — shows chunk text with entity spans highlighted.
    Click FP to remove false positives; add missed entities (FNs) via the sidebar form.
    Save button downloads the updated annotation JSON.
    """
    import json as _json
    from pathlib import Path
    from agent_trace_signals.annotation.html_viewer import generate

    store = _get_store(ctx.obj["db"])
    row = store.conn.execute(
        "SELECT id, chunk_count FROM sessions WHERE id LIKE ?", (session_prefix + "%",)
    ).fetchone()
    if not row:
        console.print(f"[red]No session found matching '{session_prefix}'[/red]")
        return
    sid = row[0]

    chunks = [
        {"chunk_index": r[0], "chunk_text": r[1]}
        for r in store.conn.execute(
            "SELECT chunk_index, chunk_text FROM records WHERE session_id=? ORDER BY chunk_index",
            (sid,),
        ).fetchall()
    ]
    entities = [
        {"id": r[0], "canonical_name": r[1], "entity_type": r[2]}
        for r in store.conn.execute(
            """SELECT DISTINCT e.id, e.canonical_name, e.entity_type
               FROM entities e JOIN occurrences o ON o.entity_id=e.id
               WHERE o.session_id=?
               ORDER BY e.entity_type, e.canonical_name""",
            (sid,),
        ).fetchall()
    ]
    occurrences = [
        {"record_id": r[0], "entity_id": r[1], "role": r[2],
         "span_start": r[3], "span_end": r[4]}
        for r in store.conn.execute(
            "SELECT record_id, entity_id, role, span_start, span_end FROM occurrences WHERE session_id=?",
            (sid,),
        ).fetchall()
    ]

    ann_path = Path(annotations) / f"{sid}.json"
    annotation = _json.loads(ann_path.read_text()) if ann_path.exists() else {}

    out_path = Path(out) if out else Path(annotations) / f"{sid}.html"
    generate(sid, chunks, entities, occurrences, annotation, out_path)

    console.print(f"\n[green]Written:[/green] {out_path}")
    console.print(f"  {len(chunks)} chunks, {len(entities)} entities")
    console.print("\nOpen in browser:")
    console.print(f"  open {out_path}\n")


@cli.command("annotate-memories")
@click.option("--annotations", default="annotations", show_default=True,
              help="Annotations directory (reads existing level1a.json if present)")
@click.pass_context
def annotate_memories(ctx, annotations: str):
    """Generate HTML viewer for labeling frequency memories.

    Opens annotations/memories_<timestamp>.html with per-memory correct/incorrect/partial
    buttons. Save in browser downloads updated annotations/level1a.json.
    """
    import json as _json
    from datetime import datetime
    from pathlib import Path
    from agent_trace_signals.annotation.memory_viewer import generate

    store = _get_store(ctx.obj["db"])
    memories = store.get_frequency_memories_for_annotation()

    ann_dir = Path(annotations)
    ann_dir.mkdir(parents=True, exist_ok=True)

    existing_annotation: dict = {}
    ann_file = ann_dir / "level1a.json"
    if ann_file.exists():
        existing_annotation = _json.loads(ann_file.read_text())

    html = generate(memories, existing_annotation)

    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    out_path = ann_dir / f"memories_{ts}.html"
    out_path.write_text(html, encoding="utf-8")

    console.print(f"[green]Wrote[/green] {out_path}")
    console.print("Open in browser. Save button downloads updated level1a.json.")
    console.print(f"Replace [bold]{ann_file}[/bold] with the downloaded file, then run [bold]ats eval1a[/bold].")


@cli.command("label-failures")
@click.option("--limit", default=30, show_default=True, help="Max inflection candidates to include")
@click.option("--min-signal", default=2.0, show_default=True, help="Minimum signal_strength to include")
@click.option("--window-before", default=4, show_default=True, help="Exchanges before precipitating turn to include")
@click.option("--labels", default="labeled_failures.json", show_default=True, help="Labels file (read existing + write on save)")
@click.option("--out", default="", help="HTML output path (default: labels file stem + .html)")
@click.option("--no-open", is_flag=True, default=False, help="Don't open browser automatically")
@click.pass_context
def label_failures(ctx, limit, min_signal, window_before, labels, out, no_open):
    """Open an HTML labeling viewer for inflection window review.

    Generates (or refreshes) labeled_failures.json with inflection candidates,
    then opens a self-contained HTML viewer in the browser. Use the Save button
    in the viewer to download the updated labels file — replace labeled_failures.json
    with the downloaded file, then re-run to pick up labels on the next pass.

    Re-running preserves existing labels.
    """
    import webbrowser
    import os
    from pathlib import Path
    from agent_trace_signals.annotation.failure_labeler import generate_labels
    from agent_trace_signals.annotation.failure_viewer import generate

    generate_labels(
        db_path=ctx.obj["db"],
        output_path=labels,
        limit=limit,
        min_signal=min_signal,
        window_before=window_before,
    )

    records = json.loads(Path(labels).read_text())
    html = generate(records)

    html_path = out or str(Path(labels).with_suffix(".html"))
    Path(html_path).write_text(html)
    console.print(f"[green]Viewer:[/green] {html_path}")
    console.print(f"[dim]Save button downloads updated {labels} — replace the file then re-run.[/dim]")

    if not no_open:
        webbrowser.open(f"file://{os.path.abspath(html_path)}")


@cli.command("label-critical-steps")
@click.option("--limit", default=0, show_default=True, help="Max critical_steps to include (0 = unlimited)")
@click.option("--tag", default="all", show_default=True,
              type=click.Choice(["all", "failure_critical", "success_critical", "recovery_critical"]))
@click.option("--session-id", default=None, help="Restrict to one session")
@click.option("--labels", default="labeled_critical_steps.json", show_default=True,
              help="Labels JSON file (read existing + write on save)")
@click.option("--out", default="", help="HTML output path (default: labels stem + .html)")
@click.option("--no-open", is_flag=True, default=False, help="Don't open browser automatically")
@click.pass_context
def label_critical_steps(ctx, limit, tag, session_id, labels, out, no_open):
    """Open an HTML labeling viewer for critical_step review (Stage 4).

    Generates / refreshes labeled_critical_steps.json with candidate records,
    then opens a self-contained HTML viewer. Use the Download button in the
    viewer to save the updated labels file — replace labeled_critical_steps.json
    with the downloaded file, then re-run to preserve labels on the next pass.
    """
    import webbrowser
    import os
    from pathlib import Path
    from agent_trace_signals.annotation.critical_step_labeler import generate_labels
    from agent_trace_signals.annotation.critical_step_viewer import generate

    n = generate_labels(
        db_path=ctx.obj["db"],
        output_path=labels,
        limit=limit or None,
        tag=tag,
        session_id=session_id,
    )
    console.print(f"[green]Wrote[/green] {n} candidate records to {labels}")

    payload = json.loads(Path(labels).read_text())
    html = generate(payload)

    html_path = out or str(Path(labels).with_suffix(".html"))
    Path(html_path).write_text(html)
    console.print(f"[green]Viewer:[/green] {html_path}")
    console.print(f"[dim]Download button saves updated {labels} — replace the file then re-run.[/dim]")

    if not no_open:
        webbrowser.open(f"file://{os.path.abspath(html_path)}")


@cli.command("critical-steps-precision")
@click.option("--labels", default="labeled_critical_steps.json", show_default=True,
              help="Labels JSON file produced by label-critical-steps")
@click.pass_context
def critical_steps_precision(ctx, labels):
    """Print precision report for the labeled critical_steps JSON.

    Reports overall precision, precision by tag and failure_mode, and whether
    the ship gate (precision >= 0.8 AND labeled >= 20) is passed.
    """
    from agent_trace_signals.annotation.critical_step_labeler import compute_precision

    rep = compute_precision(labels)
    console.print(f"[bold]Critical-Step Precision Report[/bold] — {labels}")
    console.print(f"  total_candidates: {rep['total_candidates']}")
    console.print(f"  labeled:          {rep['labeled']}")
    po = rep["precision_overall"]
    console.print(f"  precision_overall: {('%.3f' % po) if po is not None else '—'}")
    if rep["precision_by_tag"]:
        console.print("  precision_by_tag:")
        for t, p in sorted(rep["precision_by_tag"].items()):
            console.print(f"    {t}: {('%.3f' % p) if p is not None else '—'}")
    if rep["precision_by_failure_mode"]:
        console.print("  precision_by_failure_mode:")
        for m, p in sorted(rep["precision_by_failure_mode"].items()):
            console.print(f"    {m}: {('%.3f' % p) if p is not None else '—'}")
    fn = rep.get("fn_flagged", 0)
    rl = rep.get("recall_lb")
    console.print(f"  fn_flagged:        {fn}  (⚑ missed events flagged in ±3-neighbor windows)")
    if rl is not None:
        console.print(f"  recall_lb:         {rl:.3f}  (TP / (TP + FN_flagged) — lower bound)")
    gate = rep["ship_gate_passed"]
    console.print(
        f"  ship_gate_passed: [{'green' if gate else 'red'}]{gate}[/{'green' if gate else 'red'}] "
        f"(requires precision >= 0.8 AND labeled >= 20)"
    )


@cli.command("recall")
@click.argument("query")
@click.option("--memory-type", "-t", default=None, help="Filter: episodic|procedural|preference")
@click.option("--top-k", "-k", default=10, show_default=True)
@click.option("--method", "-m", default="hybrid", show_default=True, help="hybrid|semantic|lexical")
@click.option("--workspace", "-w", default=None, help="Restrict to one project/workspace (substring match)")
@click.pass_context
def recall_cmd(ctx, query: str, memory_type: str | None, top_k: int, method: str, workspace: str | None):
    """Recall over memories. Method: hybrid (BM25+semantic+RRF), semantic, or lexical."""
    from agent_trace_signals.config import ModelConfig
    from agent_trace_signals.embedder import Embedder
    from agent_trace_signals.pipeline.retrieval import RetrievalEngine

    store = _get_store(ctx.obj["db"])
    engine = RetrievalEngine(store, Embedder(ModelConfig()))

    with console.status(f"Recalling: [cyan]{query}[/cyan]"):
        results = engine.recall(query, memory_type=memory_type, top_k=top_k, include_graph=False, method=method, workspace_id=workspace)

    if not results:
        console.print("[yellow]No results found.[/yellow]")
        return

    table = Table(show_header=True, header_style="bold")
    table.add_column("Score", width=7)
    table.add_column("Type", width=11)
    table.add_column("Method", width=10)
    table.add_column("Content")
    table.add_column("ID", width=10)

    for mem in results:
        content = mem["content"]
        preview = (content[:120] + "…") if len(content) > 120 else content
        table.add_row(
            f"{mem['rrf_score']:.4f}",
            mem.get("memory_type", ""),
            mem.get("extraction_method", ""),
            preview,
            mem["id"][:8],
        )

    console.print(table)
    console.print(f"[dim]{len(results)} result(s)[/dim]")


@cli.command("chunk-search")
@click.argument("query")
@click.option("--session", "-s", default=None, help="Session ID prefix to restrict to")
@click.option("--top-k", "-k", default=10, show_default=True)
@click.option("--method", "-m", default="hybrid", show_default=True, help="hybrid|semantic|lexical")
@click.option("--workspace", "-w", default=None, help="Restrict to one project/workspace (substring match)")
@click.pass_context
def chunk_search_cmd(ctx, query: str, session: str | None, top_k: int, method: str, workspace: str | None):
    """Search over raw conversation chunks. Method: hybrid (BM25+semantic+RRF), semantic, or lexical."""
    from agent_trace_signals.config import ModelConfig
    from agent_trace_signals.embedder import Embedder
    from agent_trace_signals.pipeline.retrieval import RetrievalEngine

    store = _get_store(ctx.obj["db"])

    session_id = None
    if session:
        row = store.conn.execute(
            "SELECT id FROM sessions WHERE id LIKE ?", (session + "%",)
        ).fetchone()
        if not row:
            console.print(f"[red]No session found with prefix '{session}'[/red]")
            return
        session_id = row[0]

    engine = RetrievalEngine(store, Embedder(ModelConfig()))

    with console.status(f"Searching chunks: [cyan]{query}[/cyan]"):
        results = engine.chunk_search(query, session_id=session_id, top_k=top_k, method=method, workspace_id=workspace)

    if not results:
        console.print("[yellow]No results found.[/yellow]")
        return

    table = Table(show_header=True, header_style="bold")
    table.add_column("Score", width=7)
    table.add_column("Session", width=12)
    table.add_column("Chunk#", width=6)
    table.add_column("Text")

    for chunk in results:
        text = chunk["chunk_text"]
        preview = (text[:150] + "…") if len(text) > 150 else text
        table.add_row(
            f"{chunk['rrf_score']:.4f}",
            chunk["session_id"][:10],
            str(chunk["chunk_index"]),
            preview,
        )

    console.print(table)
    console.print(f"[dim]{len(results)} result(s)[/dim]")


@cli.command("graph-walk")
@click.argument("session_prefix")
@click.pass_context
def graph_walk_cmd(ctx, session_prefix: str):
    """Show sessions connected to SESSION_PREFIX (workspace, structural entity, or shared memory pattern).

    Session-first entry point: find all sessions sharing a workspace (deterministic,
    no LLM/entity dependency), a structural entity (file/commit/pr), or a consolidated
    cluster memory (both sessions fed the same recurring-pattern summary) with the given session.
    """
    store = _get_store(ctx.obj["db"])

    row = store.conn.execute(
        "SELECT id, source_plugin, workspace_id, session_timestamp FROM sessions WHERE id LIKE ?",
        (session_prefix + "%",),
    ).fetchone()
    if not row:
        console.print(f"[red]No session found with prefix '{session_prefix}'[/red]")
        return

    sid, plugin, workspace, ts = row
    console.print(f"[bold]Session[/bold] {sid[:12]} | {plugin} | {workspace} | {ts[:10]}\n")

    # LEFT JOIN entities and memories: "workspace" edges have via_entity_id=''
    # (no matching entity row), "shared_memory" edges point at memories.id, not
    # entities.id — an INNER JOIN would silently drop rows for whichever type
    # doesn't match.
    edges = store.conn.execute(
        """SELECT sge.target_session_id, s.source_plugin, s.workspace_id, s.session_timestamp,
                  sge.edge_type, e.canonical_name, e.entity_type, sge.weight, m2.content
           FROM session_graph_edges sge
           JOIN sessions s ON s.id = sge.target_session_id
           LEFT JOIN entities e ON e.id = sge.via_entity_id
           LEFT JOIN memories m2 ON m2.id = sge.via_entity_id
           WHERE sge.source_session_id = ?
           ORDER BY sge.weight DESC""",
        (sid,),
    ).fetchall()

    if not edges:
        console.print("[yellow]No connections found. Sessions link via a shared workspace, a shared file/commit/PR entity, or a shared memory pattern.[/yellow]")
        return

    t = Table("Connected session", "Plugin", "Workspace", "Date", "Via")
    for e in edges:
        if e[4] == "workspace":
            via = "same project"
        elif e[4] == "shared_memory":
            content = e[8] or ""
            via = f"shared pattern: {content[:50]}{'…' if len(content) > 50 else ''}"
        else:
            via = f"{e[5]} ({e[6]})"
        t.add_row(e[0][:12], e[1] or "", (e[2] or "")[-25:], (e[3] or "")[:10], via)
    console.print(t)
    console.print(f"[dim]{len(edges)} connection(s)[/dim]")


@cli.command("session-search")
@click.argument("query")
@click.option("--top-k", "-k", default=5, show_default=True)
@click.option("--method", "-m", default="hybrid", show_default=True, help="hybrid|semantic|lexical")
@click.option("--include-graph", is_flag=True, default=False,
              help="Expand top hits with session_graph_edges neighbours (workspace/structural/shared_memory/structural_similarity)")
@click.option("--workspace", "-w", default=None, help="Restrict to one project/workspace (substring match)")
@click.pass_context
def session_search_cmd(ctx, query: str, top_k: int, method: str, include_graph: bool, workspace: str | None):
    """Rank sessions by relevance to a query using session-level embeddings.

    Semantic: cosine similarity vs avg chunk embeddings per session.
    Lexical: term match over session summaries.
    Hybrid: RRF fusion of both.
    """
    from agent_trace_signals.config import ModelConfig
    from agent_trace_signals.embedder import Embedder
    from agent_trace_signals.pipeline.retrieval import RetrievalEngine

    store = _get_store(ctx.obj["db"])
    engine = RetrievalEngine(store, Embedder(ModelConfig()))

    with console.status(f"Searching sessions: [cyan]{query}[/cyan]"):
        results = engine.session_search(query, top_k=top_k, method=method, include_graph=include_graph, workspace_id=workspace)

    if not results:
        console.print("[yellow]No results found.[/yellow]")
        return

    t = Table("Score", "ID", "Plugin", "Workspace", "Timestamp", "Chunks", "Summary preview")
    for r in results:
        summary = r.get("session_summary") or ""
        preview = (summary[:80] + "…") if len(summary) > 80 else summary
        t.add_row(
            f"{r['score']:.4f}",
            r["id"][:12],
            r.get("source_plugin") or "",
            (r.get("workspace_id") or "")[-25:],
            (r.get("session_timestamp") or "")[:19],
            str(r.get("chunk_count") or 0),
            preview,
        )
    console.print(t)
    console.print(f"[dim]{len(results)} result(s)[/dim]")


@cli.command("embed")
@click.option("--batch-size", default=64, show_default=True, help="Texts per embed batch")
@click.pass_context
def embed_cmd(ctx, batch_size: int):
    """Compute and store embeddings for all un-embedded items.

    Embeddings are deferred at ingest time to avoid evicting large LLMs from
    Ollama's GPU memory. Run this after ingestion is complete.
    """
    from agent_trace_signals.config import Config
    from agent_trace_signals.embedder import Embedder

    store = _get_store(ctx.obj["db"])
    cfg = Config.default()
    embedder = Embedder(cfg.models)

    records = store.get_unembedded_records()
    entities = store.get_unembedded_entities()
    occurrences = store.get_unembedded_occurrences()
    memories = store.get_unembedded_memories()
    sessions = store.get_unembedded_sessions()

    total = len(records) + len(entities) + len(occurrences) + len(memories) + len(sessions)
    console.print(
        f"[bold]Embedding {total} un-embedded items[/bold]  "
        f"(records={len(records)}, entities={len(entities)}, "
        f"occurrences={len(occurrences)}, memories={len(memories)}, sessions={len(sessions)})"
    )
    if total == 0:
        console.print("[dim]Nothing to embed.[/dim]")
        return

    def _batch_embed(texts: list[str]) -> list[list[float]]:
        result = []
        for i in range(0, len(texts), batch_size):
            result.extend(embedder.embed_batch(texts[i: i + batch_size]))
        return result

    record_embeddings: dict[str, list[float]] = {}
    if records:
        embs = _batch_embed([r["chunk_text"] for r in records])
        record_embeddings = {r["id"]: e for r, e in zip(records, embs)}

    entity_embeddings: dict[str, list[float]] = {}
    if entities:
        texts = [
            e["entity_profile"] if e["entity_profile"]
            else f"{e['entity_type']}: {e['canonical_name']}"
            for e in entities
        ]
        embs = _batch_embed(texts)
        entity_embeddings = {e["id"]: emb for e, emb in zip(entities, embs)}

    occurrence_embeddings: dict[str, list[float]] = {}
    if occurrences:
        embs = _batch_embed([o["context_text"] for o in occurrences])
        occurrence_embeddings = {o["id"]: e for o, e in zip(occurrences, embs)}

    memory_embeddings: dict[str, list[float]] = {}
    if memories:
        embs = _batch_embed([m["content"] for m in memories])
        memory_embeddings = {m["id"]: e for m, e in zip(memories, embs)}

    session_embeddings: dict[str, list[float]] = {}
    if sessions:
        embs = _batch_embed([s["embedding_text"] for s in sessions])
        session_embeddings = {s["id"]: e for s, e in zip(sessions, embs)}

    store.write_embeddings(
        record_embeddings=record_embeddings,
        entity_embeddings=entity_embeddings,
        occurrence_embeddings=occurrence_embeddings,
        memory_embeddings=memory_embeddings,
        session_embeddings=session_embeddings,
    )

    # Backfill structural embeddings for sessions that don't have one yet.
    # This covers sessions ingested before structural embeddings were introduced
    # or sessions where ingestion ran with --no-embed.
    from agent_trace_signals.pipeline.utils import build_structural_text
    sessions_without_struct = store.get_sessions_without_structural_embedding()
    struct_computed = 0
    with store.conn:
        for sess in sessions_without_struct:
            chunks = store.conn.execute(
                "SELECT chunk_text FROM records WHERE session_id=? ORDER BY chunk_index",
                (sess["id"],),
            ).fetchall()
            raw_texts = [row[0] for row in chunks if row[0]]
            if not raw_texts:
                continue
            structural_text = build_structural_text(raw_texts)
            if not structural_text:
                continue
            structural_emb = embedder.embed(structural_text)
            store.upsert_structural_embedding(sess["id"], structural_emb)
            struct_computed += 1
    if struct_computed:
        console.print(f"  structural embeddings: {struct_computed} computed")

    # Backfill topic embeddings for sessions that don't have one yet.
    # Topic embedding is the average of all record embeddings in the session.
    import numpy as np
    sessions_without_topic = store.get_sessions_without_topic_embedding()
    topic_computed = 0
    with store.conn:
        for sess in sessions_without_topic:
            vecs = store.get_record_embeddings_for_session(sess["id"])
            if not vecs:
                continue
            avg = np.mean(vecs, axis=0).tolist()
            store.upsert_topic_embedding(sess["id"], avg)
            topic_computed += 1
    if topic_computed:
        console.print(f"  topic embeddings: {topic_computed} computed")

    # Recompute structural_similarity session_graph_edges from the full set of
    # structural embeddings (not just newly-backfilled ones) — this is a full
    # replace, not an incremental add, so it stays correct as embeddings change
    # or new sessions join, with no versioning/staleness to manage.
    from agent_trace_signals.pipeline.utils import build_structural_similarity_edges
    session_vecs = store.get_all_structural_embeddings()
    if len(session_vecs) >= 2:
        edges = build_structural_similarity_edges(session_vecs)
        store.replace_session_graph_edges_of_type("structural_similarity", edges)
        console.print(f"  structural_similarity edges: {len(edges)} written ({len(session_vecs)} sessions)")

    console.print(f"[bold green]Done.[/bold green] Stored {total} embeddings.")


@cli.group()
def analytics():
    """Run analytics pipelines (2a light, 2b full)."""
    pass


@analytics.command("signals")
@click.option("--session", default=None, help="(ignored)")
@click.pass_context
def analytics_signals(ctx, session):
    """[DEPRECATED] Keyword density approach removed — use pattern_recovery/pattern_inefficiency memories instead.

    Failure signal is now extracted semantically by the D-prompt at ingest time and
    stored as pattern_recovery and pattern_inefficiency memory rows. Hard-coded keyword
    lists were abandoned for being brittle and incomplete.
    """
    console.print(
        "[yellow]ats analytics signals is deprecated.[/yellow]\n"
        "Failure signal is now extracted by the D-prompt at ingest time.\n"
        "Query pattern_recovery and pattern_inefficiency memories instead:\n"
        "  uv run ats recall 'recovery' --memory-type pattern_recovery"
    )


@analytics.command("step-scoring")
@click.pass_context
def analytics_step_scoring(ctx):
    """[DEPRECATED] Track 2 per-exchange observer scoring — abandoned at precision=0.33 (cohort 1).

    Exchange-level scoring was superseded by D-prompt behavioral pattern extraction.
    Patterns (strategy/decision/recovery/inefficiency) are extracted at ingest time.
    """
    console.print(
        "[yellow]ats analytics step-scoring is deprecated.[/yellow]\n"
        "Track 2 per-exchange observer scoring was abandoned (precision=0.33, cohort 1).\n"
        "Behavioral patterns are now extracted by the D-prompt at ingest time."
    )


@analytics.command("critical-steps")
@click.pass_context
def analytics_critical_steps(ctx):
    """[DEPRECATED] Track 2 critical-step detection — abandoned; no data in critical_steps table.

    Superseded by D-prompt pattern extraction (pattern_recovery, pattern_inefficiency).
    """
    console.print(
        "[yellow]ats analytics critical-steps is deprecated.[/yellow]\n"
        "Use pattern_recovery/pattern_inefficiency memories for failure signal instead."
    )


@analytics.command("light")
@click.pass_context
def analytics_light(ctx):
    """[DEPRECATED] Entity-frequency promotion pipeline — superseded by ClusteringAnalyticsPipeline.

    Memories are now extracted per-chunk by the D-prompt at ingest time (extraction_method=d_combined).
    Cross-session memory consolidation is done by `ats cluster-memories` (embedding-similarity clustering).
    """
    console.print(
        "[yellow]ats analytics light is deprecated.[/yellow]\n"
        "Entity-frequency promotion was superseded by D-prompt extraction + ClusteringAnalyticsPipeline.\n"
        "  - Per-chunk memories: extracted at ingest (ats ingest)\n"
        "  - Cross-session consolidation: ats cluster-memories\n"
        "  - Session type tagging: ats cluster-sessions"
    )


@analytics.command("reset-light")
@click.pass_context
def analytics_reset_light(ctx):
    """[DEPRECATED] No-op — analytics light is superseded."""
    console.print("[yellow]ats analytics reset-light is deprecated. analytics light no longer runs.[/yellow]")


@cli.command("backfill-metadata")
@click.pass_context
def backfill_metadata(ctx):
    """Backfill session_metadata for all ingested sessions by re-parsing source files.

    No LLM calls — only reads source files and writes metadata rows.
    Reports count of sessions backfilled and any skipped (file not found).
    """
    from agent_trace_signals.config import Config
    from agent_trace_signals.plugins.claude_code import ClaudeCodeSource
    from agent_trace_signals.plugins.gemini_cli import GeminiSource
    from agent_trace_signals.plugins.opencode import OpencodeSource
    from agent_trace_signals.models import SessionMetadata

    store = _get_store(ctx.obj["db"])
    cfg = Config.default()
    plugins = [ClaudeCodeSource(cfg.pipeline), GeminiSource(cfg.pipeline), OpencodeSource(cfg.pipeline)]

    # Fetch all ingested states with a known session_id
    rows = store.conn.execute(
        "SELECT abs_path, session_id, file_type FROM ingestion_state "
        "WHERE status='ingested' AND session_id IS NOT NULL"
    ).fetchall()

    if not rows:
        console.print("[yellow]No ingested sessions found.[/yellow]")
        return

    backfilled = 0
    skipped = 0
    errors = 0

    for abs_path, session_id, file_type in rows:
        # Virtual paths (e.g. opencode) contain "::" — check the DB file part only
        check_path = abs_path.split("::")[0] if "::" in abs_path else abs_path
        if not Path(check_path).exists():
            console.print(f"[yellow]File not found, skipping:[/yellow] {abs_path}")
            skipped += 1
            continue

        plugin = next((p for p in plugins if p.can_handle(abs_path)), None)
        if plugin is None:
            console.print(f"[yellow]No plugin for {abs_path}[/yellow]")
            skipped += 1
            continue

        try:
            parsed = plugin.parse(abs_path)
            raw_sm = parsed.metadata.get("session_metadata")
            if not raw_sm:
                console.print(f"[dim]No metadata extracted for {Path(abs_path).name}[/dim]")
                skipped += 1
                continue

            raw_sm_copy = dict(raw_sm)
            raw_sm_copy["session_id"] = session_id
            meta = SessionMetadata(**raw_sm_copy)
            store.upsert_session_metadata(meta)
            backfilled += 1
        except Exception as e:
            console.print(f"[red]Error processing {Path(abs_path).name}: {e}[/red]")
            errors += 1

    console.print("\n[bold]Backfill complete.[/bold]")
    console.print(f"  Backfilled: {backfilled}  Skipped: {skipped}  Errors: {errors}")


@cli.command("cluster-sessions")
@click.option("--min-cluster-size", default=3, show_default=True,
              help="HDBSCAN min_cluster_size (min sessions per cluster)")
@click.option("--no-label", is_flag=True, default=False,
              help="Skip the LLM labeling step — tag session_type only, leave clusters unlabeled")
@click.option("--label-model", default="gemma3:12b", show_default=True,
              help="Model for the session-type labeling LLM call")
@click.pass_context
def cluster_sessions_cmd(ctx, min_cluster_size: int, no_label: bool, label_model: str):
    """Cluster sessions by structural embedding, write session_type tags, and label each cluster.

    Requires structural embeddings to have been computed (run 'ats ingest' first,
    or 'ats embed' to backfill existing sessions). Labeling describes the shared
    INTERACTION SHAPE of each cluster (not topic — see label_session_types() docstring).
    """
    from agent_trace_signals.pipeline.clustering_analytics import ClusteringAnalyticsPipeline
    from agent_trace_signals.config import Config
    from agent_trace_signals.providers.ollama import OllamaProvider
    from rich.console import Console
    _console = Console()

    store = _get_store(ctx.obj["db"])
    pipeline = ClusteringAnalyticsPipeline(store=store)
    counts = pipeline.tag_session_types(min_cluster_size=min_cluster_size)

    if not counts:
        _console.print("[yellow]Not enough sessions with structural embeddings to cluster.[/yellow]")
        return

    _console.print("[green]Session type tagging complete:[/green]")
    for tag, count in sorted(counts.items()):
        _console.print(f"  {tag}: {count} sessions")

    if no_label:
        return

    cfg = Config.default()
    provider = OllamaProvider(cfg.models)
    _console.print(f"\nLabeling clusters (model={label_model})...")
    n_labeled = pipeline.label_session_types(provider=provider, model=label_model)
    labels = store.conn.execute("SELECT session_type, label, n_sessions FROM session_type_labels ORDER BY n_sessions DESC").fetchall()
    for stype, label, n in labels:
        _console.print(f"  {stype} ({n}): [cyan]{label}[/cyan]")
    _console.print(f"[green]Labeled {n_labeled} clusters.[/green]")


@cli.command("cluster-memories")
@click.option("--level", type=click.Choice(["within", "cross", "both"]), default="both", show_default=True,
              help="within = level-1 pass, consolidate recurring memories inside each session "
                   "independently (no cross-session corroboration claim). cross = level-2 pass, "
                   "cluster across sessions and require --min-recurrence distinct sessions before "
                   "minting. both = run within-session first, then cross-session.")
@click.option("--min-cluster-size", default=3, show_default=True,
              help="HDBSCAN density param for the cross-session pass: min memories (not sessions) "
                   "to form a cluster at all. The within-session pass uses a lower fixed default (2) "
                   "since a single session's own memory count is usually small.")
@click.option("--min-recurrence", default=2, show_default=True,
              help="Cross-session pass only: floor on distinct sessions before minting. Default 2 — "
                   "this pass exists to claim cross-session corroboration, so a cluster confined to "
                   "one session belongs to the within-session pass instead, not here.")
@click.option("--provider", "provider_name", default="ollama", show_default=True,
              help="LLM provider for consolidation calls (currently: ollama)")
@click.option("--model", default="gemma4:31b", show_default=True,
              help="Model for consolidation LLM calls — bigger/more capable than the gemma3:12b "
                   "used elsewhere in ingestion, since this text becomes the corpus's summary insights")
@click.option("--max-tokens", default=2048, show_default=True,
              help="num_predict budget for the consolidation call. gemma4:31b is a thinking model — "
                   "reasoning tokens count against this before any output text, so it needs much more "
                   "than a short-answer budget. Lower back to ~256 if using a non-thinking model.")
@click.option("--embed/--no-embed", default=True, show_default=True,
              help="Embed newly minted cluster memories immediately after consolidation, "
                   "so they're searchable without a separate 'ats embed' run.")
@click.pass_context
def cluster_memories_cmd(ctx, level: str, min_cluster_size: int, min_recurrence: int, provider_name: str, model: str, max_tokens: int, embed: bool):
    """Cluster memory+pattern rows by embedding similarity and consolidate recurring clusters.

    Run 'ats embed' first to ensure all *source* memories have embeddings (clustering
    reads member embeddings to find recurring groups). Newly minted cluster memories are
    embedded automatically afterward (via --embed/--no-embed) so they're searchable
    immediately — no need to re-run 'ats embed' by hand.
    """
    from agent_trace_signals.pipeline.clustering_analytics import ClusteringAnalyticsPipeline
    from agent_trace_signals.config import Config
    from agent_trace_signals.providers.ollama import OllamaProvider
    from rich.console import Console
    _console = Console()

    store = _get_store(ctx.obj["db"])
    cfg = Config.default()
    provider = OllamaProvider(cfg.models)

    pipeline = ClusteringAnalyticsPipeline(store=store)
    n = 0
    if level in ("within", "both"):
        _console.print(f"Consolidating within-session (model={model}, max_tokens={max_tokens})...")
        n_within = pipeline.consolidate_within_session(provider=provider, model=model, max_tokens=max_tokens)
        _console.print(f"[green]Minted {n_within} within-session consolidated memories.[/green]")
        n += n_within
    if level in ("cross", "both"):
        _console.print(
            f"Consolidating cross-session (min_cluster_size={min_cluster_size}, "
            f"min_recurrence={min_recurrence}, model={model}, max_tokens={max_tokens})..."
        )
        n_cross = pipeline.consolidate_memories(
            min_cluster_size=min_cluster_size, min_recurrence=min_recurrence,
            provider=provider, model=model, max_tokens=max_tokens,
        )
        _console.print(f"[green]Minted {n_cross} cross-session consolidated memories.[/green]")
        n += n_cross

    if embed and n:
        from agent_trace_signals.embedder import Embedder
        embedder = Embedder(cfg.models)
        unembedded = store.get_unembedded_memories()
        if unembedded:
            embs = embedder.embed_batch([m["content"] for m in unembedded])
            store.write_embeddings(
                record_embeddings={}, entity_embeddings={}, occurrence_embeddings={},
                memory_embeddings={m["id"]: e for m, e in zip(unembedded, embs)},
                session_embeddings={},
            )
            _console.print(f"[green]Embedded {len(unembedded)} newly minted memories.[/green]")


@cli.command("ui")
@click.option("--port", default=8501, show_default=True, help="Port to run the Streamlit UI on")
@click.option("--db", "db_override", default=None, help="Override DB path (defaults to --db flag value)")
@click.pass_context
def ui_cmd(ctx, port: int, db_override: str | None):
    """Launch the Streamlit web UI for browsing sessions, memories, and queries."""
    import subprocess
    import sys
    from pathlib import Path

    app_path = Path(__file__).parent / "ui" / "Home.py"
    db = db_override or ctx.obj["db"]

    cmd = [
        sys.executable, "-m", "streamlit", "run", str(app_path),
        "--server.port", str(port),
        "--server.headless", "false",
        "--",  # pass remaining as script args (not used, but good practice)
    ]
    env = {**__import__("os").environ, "ATS_DB": db}
    console.print(f"[green]Starting ATS UI at http://localhost:{port}[/green]")
    console.print(f"[dim]DB: {db}[/dim]")
    subprocess.run(cmd, env=env)


@cli.command("serve")
@click.pass_context
def serve(ctx):
    """Start the ATS MCP server (stdio transport for Claude Code).

    To connect from Claude Code, register it with the claude mcp CLI (not by
    hand-editing settings.json -- recent Claude Code versions don't read an
    mcpServers key there):

    \b
    claude mcp add ats-memory --scope user \\
      -e ATS_DB=/path/to/agent-trace-signals/traces.db \\
      -- uv run --directory /path/to/agent-trace-signals ats serve
    """
    import os
    os.environ.setdefault("ATS_DB", ctx.obj["db"])
    from agent_trace_signals.mcp.server import run
    run()


if __name__ == "__main__":
    cli()
