"""Entity resolution: canonicalize and deduplicate mentions into entities."""

from __future__ import annotations
import uuid
from agent_trace_signals.config import PipelineConfig
from agent_trace_signals.db.store import SQLiteStore
from agent_trace_signals.models import Entity, RawEntityMention
from agent_trace_signals.pipeline.chunk_analyzer import DEntity


class EntityResolver:
    """Resolve raw entity mentions to canonical Entity objects."""

    def __init__(self, store: SQLiteStore, config: PipelineConfig) -> None:
        """Initialize resolver.

        Args:
            store: SQLiteStore for entity lookups
            config: Pipeline configuration
        """
        self.store = store
        self.config = config

    def resolve(
        self,
        mentions: list[RawEntityMention],
        org_id: str,
        workspace_id: str,
        session_timestamp: str | None,
    ) -> list[Entity]:
        """Resolve mentions to canonical entities.

        Args:
            mentions: Raw entity mentions to resolve
            org_id: Organization ID
            workspace_id: Workspace ID
            session_timestamp: Session timestamp for first_seen/last_seen

        Returns:
            List of resolved Entity objects (may contain duplicates by id)
        """
        entities: list[Entity] = []
        seen_ids: set[str] = set()
        # Keyed by (entity_type, canonical_name_lower) — prevents within-session UUID explosion
        # for person/org entities that aren't in the DB yet when each chunk is processed.
        session_cache: dict[tuple[str, str], Entity] = {}
        # Keyed by canonical_name_lower only (no type) — prevents the same structured
        # entity from fragmenting into separate rows when the D-prompt assigns it a
        # different type across chunks within the same resolve() call (e.g. `.env` as
        # `file` in chunk 3, `technology` in chunk 7 of the same session).
        structured_name_cache: dict[str, Entity] = {}

        for mention in mentions:
            entity = self._resolve_mention(
                mention, org_id, workspace_id, session_timestamp, session_cache, structured_name_cache
            )
            if entity.id not in seen_ids:
                entities.append(entity)
                seen_ids.add(entity.id)

        return entities

    def resolve_d_entities(
        self,
        d_entities: list[DEntity],
        org_id: str,
        workspace_id: str,
        session_timestamp: str | None,
    ) -> list[Entity]:
        """Resolve D-extracted entities to canonical Entity objects.

        Args:
            d_entities: List of DEntity objects from chunk analyzer
            org_id: Organization ID
            workspace_id: Workspace ID
            session_timestamp: Session timestamp

        Returns:
            List of resolved Entity objects (may contain duplicates by id)
        """
        # Convert DEntity objects to RawEntityMention objects
        mentions = [
            RawEntityMention(
                canonical_name=d_entity.name.strip(),
                entity_type=d_entity.type.strip(),
                mention_text=d_entity.name.strip(),
                context_text=d_entity.role[:300],
                role="subject",
                source_layer="llm_combined",
            )
            for d_entity in d_entities
        ]

        # Use existing resolve() method
        return self.resolve(mentions, org_id, workspace_id, session_timestamp)

    def _resolve_mention(
        self,
        mention: RawEntityMention,
        org_id: str,
        workspace_id: str,
        session_timestamp: str | None,
        session_cache: dict[tuple[str, str], Entity],
        structured_name_cache: dict[str, Entity],
    ) -> Entity:
        """Resolve a single mention to an entity.

        Args:
            mention: Raw entity mention
            org_id: Organization ID
            workspace_id: Workspace ID
            session_timestamp: Session timestamp
            session_cache: In-memory cache for named entities created this session
            structured_name_cache: In-memory cache for structured entities created this session, keyed by name only

        Returns:
            Resolved Entity object
        """
        entity_type = mention.entity_type.lower()

        if entity_type in ("file", "commit", "pr", "concept"):
            # Structured entities use hash-based ID
            return self._resolve_structured(
                mention, entity_type, org_id, workspace_id, session_timestamp, structured_name_cache
            )
        elif entity_type in ("person", "org"):
            # Named entities use fuzzy matching + in-session cache
            return self._resolve_named(
                mention, entity_type, org_id, workspace_id, session_timestamp, session_cache
            )
        else:
            # Default to structured
            return self._resolve_structured(
                mention, entity_type, org_id, workspace_id, session_timestamp, structured_name_cache
            )

    def _resolve_structured(
        self,
        mention: RawEntityMention,
        entity_type: str,
        org_id: str,
        workspace_id: str,
        session_timestamp: str | None,
        structured_name_cache: dict[str, Entity],
    ) -> Entity:
        """Resolve structured entity (file, commit, pr, concept, technology, ...).

        Args:
            mention: Raw entity mention
            entity_type: Normalized entity type
            org_id: Organization ID
            workspace_id: Workspace ID
            session_timestamp: Session timestamp
            structured_name_cache: In-memory cache of structured entities created
                this resolve() call, keyed by name only (not type)

        Returns:
            Resolved Entity object
        """
        # Normalize canonical_name: strip whitespace
        canonical_name = mention.canonical_name.strip()
        name_key = canonical_name.lower()

        # Check this session's in-memory cache first (name only, ignoring type) —
        # catches type disagreements between two chunks in the same resolve() call,
        # before either has been written to the DB.
        cached = structured_name_cache.get(name_key)
        if cached:
            return Entity(
                id=cached.id,
                org_id=org_id,
                workspace_id=workspace_id,
                canonical_name=cached.canonical_name,
                entity_type=cached.entity_type,
                first_seen=cached.first_seen,
                last_seen=session_timestamp or cached.last_seen,
            )

        # Check the DB for this name under ANY structured type — catches type
        # disagreements across sessions. The first type ever assigned to a name wins;
        # later mentions with a different type reuse that entity rather than forking
        # a new hash-based ID for the new type.
        existing_any_type = self.store.get_entity_by_name(org_id, workspace_id, canonical_name)
        if existing_any_type:
            entity = Entity(
                id=existing_any_type["id"],
                org_id=org_id,
                workspace_id=workspace_id,
                canonical_name=existing_any_type["canonical_name"],
                entity_type=existing_any_type["entity_type"],
                first_seen=existing_any_type["first_seen"],
                last_seen=session_timestamp or existing_any_type["last_seen"],
            )
            structured_name_cache[name_key] = entity
            return entity

        # No match by name at all — fall back to the type-specific hash lookup
        # (covers the common case: same name, same type, already in the DB).
        entity_id = Entity.make_structured_id(org_id, entity_type, canonical_name)
        existing = self.store.get_entity_by_id(entity_id)
        if existing:
            entity = Entity(
                id=entity_id,
                org_id=org_id,
                workspace_id=workspace_id,
                canonical_name=canonical_name,
                entity_type=entity_type,
                first_seen=existing["first_seen"],
                last_seen=session_timestamp or existing["last_seen"],
            )
            structured_name_cache[name_key] = entity
            return entity

        # Genuinely new entity
        now = session_timestamp or _now()
        entity = Entity(
            id=entity_id,
            org_id=org_id,
            workspace_id=workspace_id,
            canonical_name=canonical_name,
            entity_type=entity_type,
            first_seen=now,
            last_seen=now,
        )
        structured_name_cache[name_key] = entity
        return entity

    def _resolve_named(
        self,
        mention: RawEntityMention,
        entity_type: str,
        org_id: str,
        workspace_id: str,
        session_timestamp: str | None,
        session_cache: dict[tuple[str, str], Entity],
    ) -> Entity:
        """Resolve named entity (person, org) with fuzzy matching.

        Checks session_cache first (entities created earlier in this same resolve() call),
        then falls back to the DB. New entities are added to session_cache so subsequent
        mentions within the same session reuse the same ID instead of creating duplicates.

        Args:
            mention: Raw entity mention
            entity_type: Normalized entity type
            org_id: Organization ID
            workspace_id: Workspace ID
            session_timestamp: Session timestamp
            session_cache: In-memory cache for entities created in this resolve() call

        Returns:
            Resolved Entity object
        """
        canonical = mention.canonical_name.lower().strip()

        # Check in-memory session cache first — avoids UUID explosion within a single session
        for (cached_type, cached_name), cached_entity in session_cache.items():
            if cached_type == entity_type and self._levenshtein(canonical, cached_name) <= 2:
                return cached_entity

        # Check DB for entities from previous sessions
        existing_entities = self.store.get_entities_for_name_resolution(
            org_id, workspace_id, entity_type
        )

        for existing in existing_entities:
            existing_name = existing["canonical_name"].lower().strip()
            if self._levenshtein(canonical, existing_name) <= 2:
                entity = Entity(
                    id=existing["id"],
                    org_id=org_id,
                    workspace_id=workspace_id,
                    canonical_name=existing["canonical_name"],
                    entity_type=entity_type,
                    first_seen=existing.get("first_seen") or (session_timestamp or _now()),
                    last_seen=session_timestamp or existing.get("last_seen") or _now(),
                )
                session_cache[(entity_type, existing_name)] = entity
                return entity

        # No match — create new entity and register in session cache
        now = session_timestamp or _now()
        entity = Entity(
            id=str(uuid.uuid4()),
            org_id=org_id,
            workspace_id=workspace_id,
            canonical_name=mention.canonical_name,
            entity_type=entity_type,
            first_seen=now,
            last_seen=now,
        )
        session_cache[(entity_type, canonical)] = entity
        return entity

    @staticmethod
    def _levenshtein(a: str, b: str) -> int:
        """Compute Levenshtein distance between two strings.

        Args:
            a: First string
            b: Second string

        Returns:
            Levenshtein distance
        """
        if len(a) < len(b):
            a, b = b, a

        if len(b) == 0:
            return len(a)

        # Single row DP
        previous_row = list(range(len(b) + 1))

        for i, ca in enumerate(a):
            current_row = [i + 1]
            for j, cb in enumerate(b):
                # j+1 instead of j since previous_row and current_row are one character longer
                insertions = previous_row[j + 1] + 1
                deletions = current_row[j] + 1
                substitutions = previous_row[j] + (ca != cb)
                current_row.append(min(insertions, deletions, substitutions))
            previous_row = current_row

        return previous_row[-1]


def _now() -> str:
    """Get current timestamp in ISO8601 format.

    Returns:
        ISO8601 timestamp string
    """
    from datetime import datetime, timezone
    return datetime.now(timezone.utc).isoformat()
