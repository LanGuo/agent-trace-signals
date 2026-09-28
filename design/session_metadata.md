# Session Metadata — Design

*Written 2026-06-03. Implemented 2026-06-03.*

## Motivation

The current sessions table captures what happened (entities, memories, inflections) but not how the session was run. Metadata like model used, permission mode, token cost, and duration are first-class signals for:

- **Quality analysis**: did high-error sessions use a different model or permission mode?
- **Cost tracking**: which sessions consumed the most tokens / cache hits?
- **Filtering in recall**: "show me memories from bypassPermissions sessions only"
- **UI display**: session cards with richer context at a glance

---

## Available fields by source

### Claude Code (`.jsonl`)

Fields found on raw JSONL messages — not currently extracted:

| Field | Location | Type | Example |
|---|---|---|---|
| `model` | `assistant.message.model` | string | `"claude-sonnet-4-6"` |
| `input_tokens` | `assistant.message.usage.input_tokens` | int | `3` |
| `output_tokens` | `assistant.message.usage.output_tokens` | int | `983` |
| `cache_creation_input_tokens` | `assistant.message.usage.cache_creation_input_tokens` | int | `9222` |
| `cache_read_input_tokens` | `assistant.message.usage.cache_read_input_tokens` | int | `11960` |
| `service_tier` | `assistant.message.usage.service_tier` | string | `"standard"` |
| `permissionMode` | `user.permissionMode` | string | `"bypassPermissions"`, `"default"`, `"plan"` |
| `entrypoint` | `user.entrypoint` | string | `"sdk-cli"`, `"ide"`, `"cli"` |
| `userType` | `user.userType` | string | `"external"`, `"internal"` |
| `isSidechain` | `user/assistant.isSidechain` | bool | `false` |
| `timestamp` | per message | ISO string | `"2026-05-30T06:05:40.834Z"` |
| `stop_reason` | `assistant.message.stop_reason` | string | `"tool_use"`, `"end_turn"` |
| `cwd` | `user.cwd` | string | `/Users/you/src/myproject` |
| `gitBranch` | `user.gitBranch` | string | `"main"` |
| `version` | `user.version` | string | `"1.2.3"` (CC version) |

**Derived session-level aggregates:**

| Derived field | How | Notes |
|---|---|---|
| `duration_ms` | `last_message.timestamp - first_message.timestamp` | Wall clock span. Not a proxy for effort — sessions routinely span days or weeks as users continue across restarts. Use `user_turn_count` or `total_output_tokens` as work-volume proxies instead. |
| `user_turn_count` | count of `type=user` messages | |
| `assistant_turn_count` | count of `type=assistant` messages | |
| `total_input_tokens` | sum of `message.usage.input_tokens` | |
| `total_output_tokens` | sum of `message.usage.output_tokens` | |
| `total_cache_read_tokens` | sum of `message.usage.cache_read_input_tokens` | |
| `total_cache_creation_tokens` | sum of `message.usage.cache_creation_input_tokens` | |
| `models_used` | set of distinct `message.model` values | may differ across turns if switched |
| `dominant_model` | mode of `message.model` | the most-used model in the session |
| `permission_mode` | mode/first of `user.permissionMode` | usually constant per session |
| `entrypoint` | first `user.entrypoint` | `sdk-cli`, `ide`, `cli` |
| `is_sidechain` | any `isSidechain=true` | marks subagent sessions |

### Gemini CLI (`.jsonl` / `.json`)

| Field | Location | Type | Example |
|---|---|---|---|
| `model` | `gemini.model` | string | `"gemini-3-flash-preview"` |
| `input_tokens` | `gemini.tokens.input` | int | `8550` |
| `output_tokens` | `gemini.tokens.output` | int | `105` |
| `cached_tokens` | `gemini.tokens.cached` | int | `0` |
| `thought_tokens` | `gemini.tokens.thoughts` | int | `680` |
| `total_tokens` | `gemini.tokens.total` | int | `9335` |
| `timestamp` | per message | ISO string | `"2026-05-30T22:38:10.831Z"` |
| `startTime` | header | ISO string | session start |
| `lastUpdated` | header | ISO string | session end (approximate) |
| `kind` | header | string | `"main"` |

**Derived:**

| Derived field | How |
|---|---|
| `duration_ms` | `lastUpdated - startTime` (header) or last - first message timestamp |
| `user_turn_count` | count of `type=user` messages |
| `assistant_turn_count` | count of `type=gemini` messages |
| `dominant_model` | mode of `gemini.model` across turns |
| `total_input_tokens` | sum of `tokens.input` |
| `total_output_tokens` | sum of `tokens.output` |

---

## Schema changes required

### Option A: New `session_metadata` table (recommended)

Keeps the sessions table stable. Metadata is queryable via JOIN and extensible without migrations on the hot path.

```sql
CREATE TABLE session_metadata (
    session_id   TEXT PRIMARY KEY REFERENCES sessions(id) ON DELETE CASCADE,
    -- Identity
    dominant_model        TEXT,      -- e.g. "claude-sonnet-4-6"
    models_used           TEXT,      -- JSON array of distinct models
    entrypoint            TEXT,      -- "sdk-cli" | "ide" | "cli" | null
    permission_mode       TEXT,      -- "bypassPermissions" | "default" | "plan" | null
    user_type             TEXT,      -- "external" | "internal" | null
    is_sidechain          INTEGER,   -- 0/1
    cc_version            TEXT,      -- Claude Code version string
    -- Turns
    user_turn_count       INTEGER,
    assistant_turn_count  INTEGER,
    tool_call_count       INTEGER,
    -- Duration
    start_time            TEXT,      -- ISO8601
    end_time              TEXT,      -- ISO8601
    duration_ms           INTEGER,
    -- Token usage
    total_input_tokens    INTEGER,
    total_output_tokens   INTEGER,
    total_cache_read_tokens      INTEGER,
    total_cache_creation_tokens  INTEGER,
    total_thought_tokens  INTEGER,   -- Gemini thinking tokens
    -- Git context
    cwd                   TEXT,
    git_branch            TEXT
);
```

### Option B: Extend `sessions.raw_facets` JSON

No migration needed — store as JSON in the existing column. Currently `raw_facets = '{}'` for all sessions. Downside: not directly SQL-filterable without `json_extract()` everywhere.

**Decision: Option A** — separate table is cleaner for querying and the JOIN cost is negligible. The sessions table already has many columns; metadata shouldn't bloat it further.

---

## Implementation plan

### Phase 1: Schema + extraction at ingest time

1. Add `session_metadata` table to `schema.py` DDL and `migrate()`.
2. Extend `ClaudeCodeSource.parse()` to populate a `SessionMetadata` model in `ParsedSession.metadata`.
3. Extend `GeminiSource.parse()` similarly.
4. Write `store.upsert_session_metadata()`.
5. Call it from `IngestionPipeline.run()` after session write.

### Phase 2: Backfill existing sessions

Existing sessions have `raw_facets = '{}'` and no metadata. Options:

- **Re-ingest** (cleanest): `ats drop-session` + `ats ingest`. Rebuilds everything with new metadata.
- **Backfill script** `ats backfill-metadata`: re-parse the source files, extract metadata only, upsert into `session_metadata` without touching sessions/records/memories. Fast — no LLM calls.

Backfill script is the right approach for already-ingested sessions since re-ingesting a 33 MB file from scratch is expensive.

### Phase 3: Surface in UI and CLI

- `ats sessions` table: add model, permission_mode, duration, token totals columns.
- UI Home page: session cards show model badge, permission mode, duration, cost estimate.
- UI Compare page: add token usage comparison chart.
- `ats recall` / `ats session-search`: support `--model` and `--permission-mode` filters.

---

## Fields to expose in UI session cards

Priority order:

| Field | Why useful |
|---|---|
| `dominant_model` | Identify which model ran the session |
| `permission_mode` | Bypass vs default is a strong quality signal |
| `total_output_tokens` | Better work-volume proxy than duration — wall-clock duration is meaningless for sessions spanning days/weeks |
| `user_turn_count` | Conversation depth; combined with output_tokens gives effort density |
| `total_output_tokens` | Proxy for session complexity/verbosity |
| `total_cache_read_tokens` | High cache read = efficient; low = cold start |
| `user_turn_count` | Conversation depth |
| `entrypoint` | sdk-cli vs ide vs cli distinguishes automation from interactive |
| `is_sidechain` | Flag subagent sessions for separate analysis |
| `git_branch` | Context for what was being worked on |

---

## Existing sessions — manual fix path

Sessions already in DB need backfill. After Phase 1 is implemented:

```bash
# Option 1: full re-ingest (cleanest, most expensive)
rm traces.db
uv run ats ingest --workers 8

# Option 2: metadata-only backfill (fast, no LLM)
uv run ats backfill-metadata   # (to be implemented in Phase 2)
```

The `ats backfill-metadata` command should:
1. Scan `ingestion_state` for `status='ingested'` rows with known `abs_path`
2. Re-parse each file (parse() only, no entity extraction)
3. Upsert into `session_metadata`
4. No DB writes to sessions/records/memories — idempotent and safe

---

## Open questions

1. **Cost estimation**: CC doesn't log `costUSD` in the JSONL (it's computed client-side from token counts × model pricing). Should we compute it at ingest time using a static pricing table, or leave it as raw token counts?
2. **Multi-model sessions**: some sessions switch models mid-session (e.g. haiku for tool calls, sonnet for synthesis). `dominant_model` captures the majority, but `models_used` array captures the full picture. Which should drive filtering?
3. **`isSidechain` sessions**: currently `include_subagents=False` in ScannerConfig excludes them. Should they be ingested separately and linked to parent sessions via `parentUuid`?
