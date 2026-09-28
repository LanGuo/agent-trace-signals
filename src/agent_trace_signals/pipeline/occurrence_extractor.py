"""Extract occurrences: (entity, context) pairs from mentions."""

from __future__ import annotations
import re
from agent_trace_signals.config import PipelineConfig
from agent_trace_signals.models import Entity, Occurrence, Record, RawEntityMention
from agent_trace_signals.pipeline.chunk_analyzer import DEntity


class OccurrenceExtractor:
    """Extract occurrence records for entity mentions in context."""

    def __init__(self, config: PipelineConfig) -> None:
        """Initialize occurrence extractor.

        Args:
            config: Pipeline configuration
        """
        self.config = config

    def extract(
        self,
        record: Record,
        entities: list[Entity],
        mentions: list[RawEntityMention],
    ) -> list[Occurrence]:
        """Extract occurrences for mentions paired with entities.

        Args:
            record: Record containing the chunk text
            entities: Resolved Entity objects
            mentions: Raw entity mentions

        Returns:
            List of Occurrence objects
        """
        occurrences: list[Occurrence] = []

        for mention in mentions:
            # Find matching entity
            matching_entity = self._find_matching_entity(mention, entities)
            if matching_entity is None:
                continue

            # Extract context
            context_text = self._extract_context(record.chunk_text, mention.mention_text)

            # Create occurrence
            span_start = self._find_span_start(record.chunk_text, mention.mention_text)

            occurrence = Occurrence(
                id=Occurrence.make_id(record.id, matching_entity.id, span_start or "0"),
                record_id=record.id,
                session_id=record.session_id,
                entity_id=matching_entity.id,
                role=mention.role,
                mention_text=mention.mention_text,
                context_text=context_text,
                span_start=span_start,
            )
            occurrences.append(occurrence)

        return occurrences

    def extract_from_d(
        self,
        record: Record,
        entities: list[Entity],
        d_entities: list[DEntity],
    ) -> list[Occurrence]:
        """Extract occurrences from D-extracted entities.

        Args:
            record: Record containing the chunk text
            entities: Resolved Entity objects
            d_entities: D-extracted entity objects

        Returns:
            List of Occurrence objects
        """
        occurrences: list[Occurrence] = []

        for d_entity in d_entities:
            # Find matching entity by name (case-insensitive)
            matching_entity = None
            entity_name_lower = d_entity.name.lower()
            for entity in entities:
                if entity.canonical_name.lower() == entity_name_lower:
                    matching_entity = entity
                    break

            if matching_entity is None:
                continue

            # Set context_text from d_entity.role, or fall back to chunk prefix
            context_text = d_entity.role[:300] if d_entity.role else record.chunk_text[:300]

            # No span offsets for D entities
            span_start = "0"

            occurrence = Occurrence(
                id=Occurrence.make_id(record.id, matching_entity.id, span_start),
                record_id=record.id,
                session_id=record.session_id,
                entity_id=matching_entity.id,
                role="subject",
                mention_text=matching_entity.canonical_name,
                context_text=context_text,
                span_start=None,
                span_end=None,
            )
            occurrences.append(occurrence)

        return occurrences

    def _find_matching_entity(
        self, mention: RawEntityMention, entities: list[Entity]
    ) -> Entity | None:
        """Find entity matching a mention.

        Args:
            mention: Raw entity mention
            entities: List of resolved entities

        Returns:
            Matching Entity or None
        """
        mention_type = mention.entity_type.lower()
        mention_name = mention.canonical_name.lower()

        for entity in entities:
            if (
                entity.entity_type.lower() == mention_type
                and entity.canonical_name.lower() == mention_name
            ):
                return entity

        return None

    def _extract_context(self, chunk_text: str, mention_text: str) -> str:
        """Extract context sentences around mention.

        Args:
            chunk_text: Full chunk text
            mention_text: Text of the mention

        Returns:
            Context text (max 500 chars)
        """
        # Split by sentence boundaries
        sentences = self._split_sentences(chunk_text)

        # Find sentence containing mention
        mention_sentence_idx = None
        for i, sentence in enumerate(sentences):
            if mention_text in sentence:
                mention_sentence_idx = i
                break

        if mention_sentence_idx is None:
            # Mention not found in sentences, return chunk prefix
            return chunk_text[:500]

        # Collect N sentences before and after
        n = self.config.occurrence_context_sentences
        start_idx = max(0, mention_sentence_idx - n)
        end_idx = min(len(sentences), mention_sentence_idx + n + 1)

        context_sentences = sentences[start_idx:end_idx]
        context_text = " ".join(context_sentences)

        # Truncate to 500 chars
        return context_text[:500]

    def _split_sentences(self, text: str) -> list[str]:
        """Split text into sentences.

        Args:
            text: Text to split

        Returns:
            List of sentences
        """
        # Split on sentence boundaries: . ! ? or newline
        sentences = re.split(r'[.!?\n]+', text)
        # Strip whitespace and filter empty sentences
        sentences = [s.strip() for s in sentences if s.strip()]
        return sentences

    def _find_span_start(self, text: str, mention_text: str) -> str:
        """Find string index of mention in text.

        Args:
            text: Full text
            mention_text: Mention text to find

        Returns:
            String representation of index, or "0" if not found
        """
        idx = text.find(mention_text)
        return str(idx) if idx >= 0 else "0"
