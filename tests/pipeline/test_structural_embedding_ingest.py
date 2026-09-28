"""Test that a structural embedding is computed and stored during ingestion."""
from unittest.mock import MagicMock, patch


from agent_trace_signals.db.schema import create_all, open_db
from agent_trace_signals.db.store import SQLiteStore
from agent_trace_signals.models import RawChunk


def _make_store(tmp_path, name="test.db"):
    db_path = tmp_path / name
    con = open_db(str(db_path))
    create_all(con)
    return SQLiteStore(con)


def _make_parsed_session(chunks):
    parsed_session = MagicMock()
    parsed_session.source_plugin = "test_plugin"
    parsed_session.source_type = "agent_trace"
    parsed_session.workspace_id = "ws1"
    parsed_session.session_timestamp = None
    parsed_session.metadata = {}
    parsed_session.raw_facets = {}
    parsed_session.chunks = chunks
    return parsed_session


def _make_pipeline(store, embedder):
    from agent_trace_signals.config import Config
    from agent_trace_signals.pipeline.ingestion import IngestionPipeline
    from agent_trace_signals.pipeline.chunk_analyzer import DChunkResult

    summarizer = MagicMock()
    summarizer.summarize_session.return_value = "summary"

    entity_resolver = MagicMock()
    entity_resolver.resolve_d_entities.return_value = []

    occurrence_extractor = MagicMock()
    occurrence_extractor.extract_from_d.return_value = []

    config = Config()

    with patch("agent_trace_signals.providers.ollama.OllamaProvider"), \
         patch("agent_trace_signals.pipeline.ingestion.DChunkAnalyzer") as MockAnalyzer:
        d_result = DChunkResult(summary="s", entities=[], memories=[], patterns=[])
        mock_d = MagicMock()
        mock_d.analyze_batch.side_effect = lambda records, session, **kw: [d_result] * len(records)
        MockAnalyzer.return_value = mock_d

        pipeline = IngestionPipeline(
            store=store,
            summarizer=summarizer,
            embedder=embedder,
            entity_resolver=entity_resolver,
            occurrence_extractor=occurrence_extractor,
            config=config,
        )
    return pipeline


def test_structural_embedding_stored_after_ingest(tmp_path):
    """After ingesting a session, a structural embedding row exists."""
    store = _make_store(tmp_path)
    embedder = MagicMock()
    embedder.embed.return_value = [0.5] * 768
    embedder.embed_batch.return_value = [[0.5] * 768]

    pipeline = _make_pipeline(store, embedder)

    chunks = [
        RawChunk(chunk_index=0, chunk_text="A" * 5000),
        RawChunk(chunk_index=1, chunk_text="B" * 3000),
    ]
    parsed_session = _make_parsed_session(chunks)
    session = pipeline.run(parsed_session, abs_path="/fake/path.json", content_hash="abc123")

    row = store.conn.execute(
        "SELECT session_id FROM session_structural_embeddings WHERE session_id = ?",
        (session.id,),
    ).fetchone()
    assert row is not None, "Expected a structural embedding row to be stored"
    assert row[0] == session.id
    assert embedder.embed.called


def test_structural_embedding_uses_sampled_chunks(tmp_path):
    """Structural embedding text includes first and last chunk (sampled-chunk strategy)."""
    store = _make_store(tmp_path, "test2.db")

    captured_texts = []

    embedder = MagicMock()
    def capture_embed(text):
        captured_texts.append(text)
        return [0.1] * 768
    embedder.embed.side_effect = capture_embed
    embedder.embed_batch.return_value = [[0.1] * 768]

    pipeline = _make_pipeline(store, embedder)

    first_chunk = "FIRST_CHUNK"
    last_chunk = "LAST_CHUNK"

    chunks = [
        RawChunk(chunk_index=0, chunk_text=first_chunk),
        RawChunk(chunk_index=1, chunk_text=last_chunk),
    ]
    parsed_session = _make_parsed_session(chunks)
    pipeline.run(parsed_session, abs_path="/fake/path2.json", content_hash="def456")

    assert len(captured_texts) >= 1
    structural_text = captured_texts[0]
    assert "FIRST_CHUNK" in structural_text, "First chunk must appear in structural text"
    assert "LAST_CHUNK" in structural_text, "Last chunk must appear in structural text"


