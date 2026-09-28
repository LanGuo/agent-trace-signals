"""Tests for EntityResolver — focuses on within-session deduplication bug fix."""

import pytest
from agent_trace_signals.config import PipelineConfig
from agent_trace_signals.models import RawEntityMention
from agent_trace_signals.pipeline.entity_resolver import EntityResolver


@pytest.fixture
def resolver(store):
    return EntityResolver(store, PipelineConfig())


def _mention(name, entity_type="person", source_layer="llm_verified"):
    return RawEntityMention(
        mention_text=name,
        canonical_name=name,
        entity_type=entity_type,
        context_text="some context",
        role="subject",
        source_layer=source_layer,
    )


class TestWithinSessionDedup:
    def test_same_person_mentioned_many_times_yields_one_entity(self, resolver):
        mentions = [_mention("languo") for _ in range(36)]
        entities = resolver.resolve(mentions, "org", "ws", None)
        assert len(entities) == 1

    def test_same_person_same_id_across_all_mentions(self, resolver):
        mentions = [_mention("languo") for _ in range(10)]
        entities = resolver.resolve(mentions, "org", "ws", None)
        assert len({e.id for e in entities}) == 1

    def test_two_distinct_persons_yield_two_entities(self, resolver):
        mentions = [_mention("languo"), _mention("alice"), _mention("languo")]
        entities = resolver.resolve(mentions, "org", "ws", None)
        assert len(entities) == 2
        names = {e.canonical_name for e in entities}
        assert names == {"languo", "alice"}

    def test_fuzzy_match_within_session(self, resolver):
        # "languo" and "Languo" should resolve to the same entity within a session
        mentions = [_mention("languo"), _mention("Languo")]
        entities = resolver.resolve(mentions, "org", "ws", None)
        assert len(entities) == 1

    def test_person_reuses_db_entity_across_sessions(self, resolver, store):
        # First session: create "languo"
        first_pass = resolver.resolve([_mention("languo")], "org", "ws", None)
        assert len(first_pass) == 1
        first_id = first_pass[0].id

        # Simulate writing to DB
        from agent_trace_signals.models import Session
        session = Session(id="sess1", source_type="agent_trace", source_plugin="claude_code", workspace_id="ws")
        store.write_session_batch(
            session=session,
            records=[],
            entities=first_pass,
            occurrences=[],
            memories=[],
            memory_sources=[],
            edges=[],
            record_embeddings={},
            session_embedding=None,
            entity_embeddings={},
            occurrence_embeddings={},
            memory_embeddings={},
        )

        # Second session: "languo" should resolve to same ID from DB
        second_pass = resolver.resolve([_mention("languo")], "org", "ws", None)
        assert len(second_pass) == 1
        assert second_pass[0].id == first_id


class TestStructuredEntitiesUnchanged:
    def test_structured_entities_still_use_hash_id(self, resolver):
        mentions = [_mention("src/foo.py", "file"), _mention("src/foo.py", "file")]
        entities = resolver.resolve(mentions, "org", "ws", None)
        assert len(entities) == 1

    def test_concept_entities_deduplicated_by_hash(self, resolver):
        mentions = [_mention("TDD", "concept")] * 5
        entities = resolver.resolve(mentions, "org", "ws", None)
        assert len(entities) == 1


class TestStructuredEntityTypeFragmentation:
    """Covers the fragmentation bug found in the exp06 extraction audit: the
    D-prompt classifies the same real-world entity inconsistently across chunks
    (e.g. `.env` as both `file` and `technology`), and structured entity IDs used
    to be hashed from (type, name) — so a type disagreement silently forked one
    entity into two DB rows, each with its own diluted degree_count.
    """

    def test_same_name_different_type_within_session_yields_one_entity(self, resolver):
        mentions = [_mention(".env", "file"), _mention(".env", "technology")]
        entities = resolver.resolve(mentions, "org", "ws", None)
        assert len(entities) == 1

    def test_first_type_wins_within_session(self, resolver):
        mentions = [_mention(".env", "file"), _mention(".env", "technology")]
        entities = resolver.resolve(mentions, "org", "ws", None)
        assert entities[0].entity_type == "file"

    def test_same_name_different_type_across_sessions_reuses_entity(self, resolver, store):
        from agent_trace_signals.models import Session

        first_pass = resolver.resolve([_mention(".env", "file")], "org", "ws", None)
        assert len(first_pass) == 1
        first_id = first_pass[0].id

        session = Session(id="sess1", source_type="agent_trace", source_plugin="claude_code", workspace_id="ws")
        store.write_session_batch(
            session=session, records=[], entities=first_pass, occurrences=[], memories=[],
            memory_sources=[], edges=[], record_embeddings={}, session_embedding=None,
            entity_embeddings={}, occurrence_embeddings={}, memory_embeddings={},
        )

        # A later session classifies the same name as `technology` instead of `file`.
        second_pass = resolver.resolve([_mention(".env", "technology")], "org", "ws", None)
        assert len(second_pass) == 1
        assert second_pass[0].id == first_id
        assert second_pass[0].entity_type == "file"  # first-assigned type still wins

    def test_distinct_names_still_yield_distinct_entities(self, resolver):
        mentions = [_mention(".env", "file"), _mention(".gitignore", "file")]
        entities = resolver.resolve(mentions, "org", "ws", None)
        assert len(entities) == 2
