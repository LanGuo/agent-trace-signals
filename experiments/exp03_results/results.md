# Experiment 03: NER + Memory on B Summaries vs D (combined prompt)

**Input for NER/memory:** B summary text (3-5 sentences)  
**Comparison:** D entities + memories from the same exp02 run  
**NER model:** GLiNER `urchade/gliner_multi-v2.1` + verifier `gemma4:e4b`  
**Memory model:** `gemma3:12b`  
**LLM calls:** B+NER+memory=2, D=5 (per-chunk), E=1 (cross-chunk unified)

---

## Chunk 25

### B summary (input to NER + memory)

> The SLR system, designed to perform Systematic Literature Reviews using the Langgraph framework, encountered a problem when attempting to use the `SqliteSaver` class for checkpointing. Specifically, the `SqliteSaver.from_conn_string` method was incorrectly assumed to return a `SqliteSaver` instance directly, when it actually returns a context manager. To resolve this, the code was modified in `slr_agent/orchestrator.py` to directly instantiate the `SqliteSaver` class with the connection string. This change allows the SLR system to properly checkpoint its state during the literature review process, as evidenced by the successful completion of subsequent tests.

### Entities: NER on B summary

**Pre-LLM (regex + GLiNER):**
- `Systematic Literature Reviews` (concept) [ner]
- `Langgraph framework` (technology) [ner]

**After LLM verifier:**
- `Systematic Literature Reviews` (concept) [ner]
- `Langgraph framework` (technology) [ner]

**D entities (combined prompt on raw chunk):**
- `SLR_gemma4` (project) — systematic literature review project
- `SqliteSaver` (class) — class used for saving checkpoints in the SLR workflow
- `orchestrator.py` (file) — file containing the logic for the SLR workflow

### Memories: extracted from B summary

- **[episodic]** The SLR system's checkpointing process failed because `SqliteSaver.from_conn_string` was incorrectly assumed to return a `SqliteSaver` instance instead of a context manager.
- **[episodic]** The code in `slr_agent/orchestrator.py` was modified to directly instantiate the `SqliteSaver` class with the connection string to fix the checkpointing issue.
- **[semantic]** The SLR system uses the Langgraph framework for performing Systematic Literature Reviews.

### Memories: from D (combined prompt on raw chunk)

- **[episodic]** The `SqliteSaver.from_conn_string` method in SLR_gemma4 returns a context manager, requiring the use of a `with` statement or direct instantiation to properly create a saver instance.
- **[procedural]** To correctly instantiate a SqliteSaver in SLR_gemma4, either use a `with` statement around the call to `from_conn_string` or directly instantiate the class with the connection string: `SqliteSaver(conn)`.

---

## Chunk 26

### B summary (input to NER + memory)

> The system's `SLR_gemma4` pipeline was experiencing a problem: the user was unaware of progress during the "SEARCH" stage (Stage 2), which retrieves and prepares papers for LLM screening, leading to the perception of a hang. To address this, a decision was made to add a `log()` method to the `ProgressEmitter` class and insert calls to it at the beginning of each "slow subgraph" within the `orchestrator.py` file. This change will provide a "starting" log message for each slow stage, giving the user visual feedback that the pipeline is actively processing, even when there's a delay.

### Entities: NER on B summary

**Pre-LLM (regex + GLiNER):**
- `SLR_gemma4` (technology) [ner]

**After LLM verifier:**
- `SLR_gemma4` (technology) [ner]

**D entities (combined prompt on raw chunk):**
- `SLR pipeline` (project) — the overall system being developed
- `Stage 2: SEARCH` (project) — a specific stage within the SLR pipeline
- `ProgressEmitter` (technology) — a component responsible for emitting progress updates
- `orchestrator.py` (technology) — a file containing the logic for managing the pipeline's workflow

### Memories: extracted from B summary

- **[episodic]** The `SLR_gemma4` pipeline's "SEARCH" stage (Stage 2) lacked progress feedback, causing users to perceive a hang.
- **[episodic]** A `log()` method was added to the `ProgressEmitter` class to provide visual feedback during slow stages of the `SLR_gemma4` pipeline.
- **[procedural]** To provide progress feedback, calls to the `log()` method were inserted at the beginning of each "slow subgraph" within the `orchestrator.py` file.

### Memories: from D (combined prompt on raw chunk)

- **[episodic]** During Stage 2: SEARCH of the SLR pipeline, the agent implemented a solution to address long, silent pauses by adding a `log()` method to `ProgressEmitter` and inserting calls to it within `orchestrator.py` to provide progress updates.

---

## Chunk 27

### B summary (input to NER + memory)

> The identified problem was a lack of user control and flexibility within the Stage 1 gate of the SLR agent's search process. Specifically, users couldn't edit crucial search parameters like `search_sources` (PubMed, bioRxiv), date ranges, or `max_results`, which were hardcoded or pulled from the command-line configuration (`cfg`). The decision was made to implement these editable parameters within the gate, allowing users to modify search criteria beyond the initial PICO query strings. As a result, users will now have the ability to adjust search sources and date ranges, improving the precision and adaptability of the search process.





### Entities: NER on B summary

**Pre-LLM (regex + GLiNER):**
_(none)_

**After LLM verifier:**
_(none)_

**D entities (combined prompt on raw chunk):**
- `SLR agent` (project) — The project undergoing development and refinement.
- `Stage 1 gate` (concept) — A component of the SLR agent with identified usability gaps.
- `search_sources` (technology) — A configuration parameter currently hardcoded and inaccessible to users.
- `date range` (concept) — A missing feature for filtering search results.
- `cfg` (technology) — Configuration file containing search parameters.
- `Stage 3` (concept) — A stage in the SLR agent workflow with a lack of defined screening criteria.

### Memories: extracted from B summary

- **[episodic]** The SLR agent's Stage 1 gate initially hardcoded search parameters like `search_sources` (PubMed, bioRxiv) and `max_results`, which were pulled from the command-line configuration (`cfg`).
- **[episodic]** A decision was made to implement editable parameters within the SLR agent's Stage 1 gate, allowing users to modify search criteria beyond the initial PICO query strings.
- **[semantic]** The SLR agent's Stage 1 gate controls the search process.
- **[procedural]** Users can now adjust search sources and date ranges within the SLR agent's Stage 1 gate to improve search precision and adaptability.

### Memories: from D (combined prompt on raw chunk)

- **[episodic]** In the SLR agent, the Stage 1 gate lacks user-editable search parameters like `search_sources` and date range, and Stage 3 screening prompts lack defined criteria, requiring the LLM to invent its own.
- **[procedural]** To improve the SLR agent's Stage 1 gate, implement surfacing of configuration values (currently in `cfg`) and add date filtering capabilities to allow users to modify search parameters.

---

## Chunk 28

### B summary (input to NER + memory)

> The agent identified a need to ensure each run within the SLR agent has a unique identifier (`run_id`). Initially, the `orchestrator.py` file was generating a `run_id` but not persisting it to a database (likely `db`). To address this, the agent modified `orchestrator.py` to call `db.ensure_run(run_id)` after generating a new `run_id` using `uuid.uuid4()` if one wasn't already present in the state. This change guarantees that each run has a persistent, unique identifier, improving the system's ability to track and manage individual SLR processes.

### Entities: NER on B summary

**Pre-LLM (regex + GLiNER):**
- `agent` (technology) [ner]
- `database` (technology) [ner]

**After LLM verifier:**
- `agent` (technology) [ner]
- `database` (technology) [ner]

**D entities (combined prompt on raw chunk):**
- `SLR_gemma4` (project) — the project being modified
- `slr_agent` (project) — the directory containing the core files
- `state.py` (file) — a core file being modified
- `config.py` (file) — a core file being modified
- `orchestrator.py` (file) — a core file being modified

### Memories: extracted from B summary

- **[episodic]** The agent modified `orchestrator.py` to call `db.ensure_run(run_id)` after generating a new `run_id` using `uuid.uuid4()`.
- **[semantic]** The SLR agent requires each run to have a unique identifier (`run_id`).
- **[semantic]** `orchestrator.py` is a file involved in generating `run_id` values within the SLR agent.

### Memories: from D (combined prompt on raw chunk)

_(none)_

---

## Chunk 29

### B summary (input to NER + memory)

> The problem identified was that the MockLLM registration keys were misaligned with the updated prompt, causing test failures. To resolve this, the prompt used for the MockLLM was corrected from `"screen the following abstracts"` to `"You are screening abstracts for a systematic review"`. This change ensured all 54 tests passed and enabled the addition of comprehensive documentation, including a `README.md` detailing the project's quick start, pipeline stages, tech stack (grounding/audit trail, multi-language support), and a `docs/architecture.md` outlining the system's layer diagram and components.

### Entities: NER on B summary

**Pre-LLM (regex + GLiNER):**
- `MockLLM` (technology) [ner]

**After LLM verifier:**
- `MockLLM` (technology) [ner]

**D entities (combined prompt on raw chunk):**
- `MockLLM` (technology) — a system requiring registration key adjustments
- `README.md` (project) — the newly created top-level documentation file
- `docs/architecture.md` (project) — the newly created architecture documentation file

### Memories: extracted from B summary

- **[episodic]** The MockLLM registration keys were misaligned with the updated prompt, causing test failures.
- **[episodic]** The prompt used for the MockLLM was corrected from "screen the following abstracts" to "You are screening abstracts for a systematic review".
- **[episodic]** Correcting the MockLLM prompt ensured all 54 tests passed.
- **[episodic]** A `README.md` file was added to detail the project's quick start, pipeline stages, tech stack, and other information.
- **[semantic]** The project's tech stack includes grounding and audit trail capabilities.
- **[episodic]** A `docs/architecture.md` file was created to outline the system's layer diagram and components.

### Memories: from D (combined prompt on raw chunk)

- **[episodic]** The MockLLM registration keys were corrected to match the updated prompt, resolving an issue and ensuring all 54 tests passed.
- **[semantic]** The project's README.md file details quick start instructions, CLI flags, pipeline stages, output locations, the tech stack, grounding/audit trail, and multi-language support.

---

## E — Cross-chunk unified (1 call over all 5 B summaries)

### Entities

- `SLR system` (project) — primary project for systematic literature reviews
- `Langgraph` (framework) — framework used for orchestrating the SLR system
- `SqliteSaver` (class) — checkpointing mechanism within the SLR system
- `SLR_gemma4` (project) — specific pipeline within the SLR system
- `ProgressEmitter` (class) — class responsible for providing user feedback during pipeline execution
- `orchestrator.py` (file) — core file for managing the SLR agent's workflow
- `db` (dataset) — database used for persisting run identifiers
- `uuid.uuid4()` (algorithm) — algorithm used to generate unique run identifiers
- `MockLLM` (model) — mock model used for testing the SLR system
- `README.md` (file) — documentation file providing project overview and quick start guide
- `docs/architecture.md` (file) — documentation file outlining the system's architecture

### Memories

- **[episodic]** The SLR system's checkpointing process using `SqliteSaver` was failing due to a misunderstanding of the `from_conn_string` method's return value; the code was corrected to directly instantiate the `SqliteSaver` class, resolving the issue and enabling successful tests.
- **[procedural]** To improve user feedback during the 'SEARCH' stage of the `SLR_gemma4` pipeline, add a `log()` method to the `ProgressEmitter` class and insert calls to it at the beginning of each 'slow subgraph' in `orchestrator.py`.
- **[procedural]** To allow users to customize search parameters in the SLR agent's Stage 1 gate, implement editable fields for `search_sources`, date ranges, and `max_results` within the gate itself.
- **[procedural]** To ensure each SLR run has a persistent identifier, generate a `run_id` using `uuid.uuid4()` in `orchestrator.py` and then call `db.ensure_run(run_id)` to persist it to the database.
- **[semantic]** The MockLLM registration keys were misaligned with the prompt, causing test failures; the prompt was corrected to 'You are screening abstracts for a systematic review' to resolve the issue and enable comprehensive documentation.

---

## Signal density comparison

| Chunk | B pre-LLM | B verified | B memories | D entities | D memories |
|-------|-----------|------------|------------|------------|------------|
| 25 | 2 | 2 | 3 | 3 | 2 |
| 26 | 1 | 1 | 3 | 4 | 1 |
| 27 | 0 | 0 | 4 | 6 | 2 |
| 28 | 2 | 2 | 3 | 5 | 0 |
| 29 | 1 | 1 | 6 | 3 | 2 |
| **E (window)** | — | — | — | 11 | 5 |

---

## Key questions to answer

- Does NER on B summaries reduce noise vs NER on raw chunks (exp01)?
- Are B+batched memories better/worse/different from D per-chunk memories?
- Does E (cross-chunk) deduplicate effectively — are its memories higher-level syntheses that span multiple chunks?
- Does E miss anything important that per-chunk B or D caught?
- Which approach gives the best recall signal for 'what did I work on in SLR-gemma4?'
