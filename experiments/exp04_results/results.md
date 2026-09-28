# Experiment 04: D per-chunk → E synthesis over D outputs

**Model:** `gemma3:12b`  
**Stage 1:** D combined prompt on raw chunks (from exp02)  
**Stage 2:** E synthesis over 5 D outputs — 1 LLM call  
**Total new calls:** 1

---

## Stage 1 — D outputs per chunk

### Chunk 25

**Summary:** The agent was attempting to run a systematic literature review (SLR) using the SLR_gemma4 system to investigate whether aspirin reduces cardiovascular events. The initial run failed due to an incorrect usage of `SqliteSaver.from_conn_string`, which returns a context manager instead of a saver instance directly. The agent corrected this by creating the connection directly and then committing the change to the `orchestrator.py` file, followed by running tests which passed with warnings.

**Entities:** `SLR_gemma4` (project), `SqliteSaver` (class), `orchestrator.py` (file)

**Memories:**
- **[episodic]** The `SqliteSaver.from_conn_string` method in SLR_gemma4 returns a context manager, requiring the use of a `with` statement or direct instantiation to properly create a saver instance.
- **[procedural]** To correctly instantiate a SqliteSaver in SLR_gemma4, either use a `with` statement around the call to `from_conn_string` or directly instantiate the class with the connection string: `SqliteSaver(conn)`.

### Chunk 26

**Summary:** The agent was working on the SLR (Systematic Literature Review) pipeline, specifically Stage 2: SEARCH, which retrieves and screens papers. A problem was identified: the pipeline was pausing for extended periods during the screening phase without providing any visible progress updates. To address this, a `log()` method was added to the `ProgressEmitter` and calls to this method were inserted at the beginning of slow nodes within the `orchestrator.py` file, providing more informative logging.

**Entities:** `SLR pipeline` (project), `Stage 2: SEARCH` (project), `ProgressEmitter` (technology), `orchestrator.py` (technology)

**Memories:**
- **[episodic]** During Stage 2: SEARCH of the SLR pipeline, the agent implemented a solution to address long, silent pauses by adding a `log()` method to `ProgressEmitter` and inserting calls to it within `orchestrator.py` to provide progress updates.

### Chunk 27

**Summary:** The agent identified gaps in the Stage 1 gate of the SLR agent system, specifically concerning the user's ability to modify search parameters. Users cannot edit the `search_sources` (currently hardcoded from configuration) or date range, and the date range is not implemented for PubMed.  A further gap was found in Stage 3, where the screening prompt lacks defined criteria, leading the LLM to invent its own. The agent is now implementing changes to address these issues, surfacing configuration values and adding date filtering capabilities.

**Entities:** `SLR agent` (project), `Stage 1 gate` (concept), `search_sources` (technology), `date range` (concept), `cfg` (technology), `Stage 3` (concept)

**Memories:**
- **[episodic]** In the SLR agent, the Stage 1 gate lacks user-editable search parameters like `search_sources` and date range, and Stage 3 screening prompts lack defined criteria, requiring the LLM to invent its own.
- **[procedural]** To improve the SLR agent's Stage 1 gate, implement surfacing of configuration values (currently in `cfg`) and add date filtering capabilities to allow users to modify search parameters.

### Chunk 28

**Summary:** The agent was working on the SLR_gemma4 project, specifically modifying core files and associated tests. The goal was to implement changes across five core files and their corresponding test updates. The agent read and edited files within the `slr_agent` directory, including `state.py`, `config.py`, and `orchestrator.py`, as well as unit and integration tests. The agent confirmed having all necessary information and proceeded with the implementation.

**Entities:** `SLR_gemma4` (project), `slr_agent` (project), `state.py` (file), `config.py` (file), `orchestrator.py` (file)

### Chunk 29

**Summary:** Work focused on the project, adding initial documentation. A problem was identified with the MockLLM registration keys, which were corrected to align with an updated prompt. This resulted in all 54 tests passing and the creation of a top-level `README.md` and a `docs/architecture.md` file, detailing the project's structure, functionality, and technical stack.

**Entities:** `MockLLM` (technology), `README.md` (project), `docs/architecture.md` (project)

**Memories:**
- **[episodic]** The MockLLM registration keys were corrected to match the updated prompt, resolving an issue and ensuring all 54 tests passed.
- **[semantic]** The project's README.md file details quick start instructions, CLI flags, pipeline stages, output locations, the tech stack, grounding/audit trail, and multi-language support.

---

## Stage 2 — E synthesis over D outputs

### Entities

- `SLR_gemma4` (project) — systematic literature review project and the project being modified
- `SLR pipeline` (project) — the overall system being developed
- `SLR agent` (project) — The project undergoing development and refinement.
- `SqliteSaver` (class) — class used for saving checkpoints in the SLR workflow
- `ProgressEmitter` (technology) — a component responsible for emitting progress updates
- `orchestrator.py` (file) — file containing the logic for the SLR workflow and core file being modified
- `search_sources` (technology) — A configuration parameter currently hardcoded and inaccessible to users.
- `date range` (concept) — A missing feature for filtering search results.
- `cfg` (technology) — Configuration file containing search parameters.
- `state.py` (file) — a core file being modified
- `config.py` (file) — a core file being modified
- `MockLLM` (technology) — a system requiring registration key adjustments
- `README.md` (project) — the newly created top-level documentation file
- `docs/architecture.md` (project) — the newly created architecture documentation file

### Memories

- **[episodic]** The `SqliteSaver.from_conn_string` method in SLR_gemma4 returns a context manager, requiring the use of a `with` statement or direct instantiation to properly create a saver instance. _(chunks [0])_
- **[procedural]** To correctly instantiate a SqliteSaver in SLR_gemma4, either use a `with` statement around the call to `from_conn_string` or directly instantiate the class with the connection string: `SqliteSaver(conn).` _(chunks [0])_
- **[episodic]** During Stage 2: SEARCH of the SLR pipeline, the agent implemented a solution to address long, silent pauses by adding a `log()` method to `ProgressEmitter` and inserting calls to it within `orchestrator.py` to provide progress updates. _(chunks [1])_
- **[episodic]** In the SLR agent, the Stage 1 gate lacks user-editable search parameters like `search_sources` and date range, and Stage 3 screening prompts lack defined criteria, requiring the LLM to invent its own. _(chunks [2])_
- **[procedural]** To improve the SLR agent's Stage 1 gate, implement surfacing of configuration values (currently in `cfg`) and add date filtering capabilities to allow users to modify search parameters. _(chunks [2])_
- **[episodic]** The MockLLM registration keys were corrected to match the updated prompt, resolving an issue and ensuring all 54 tests passed. _(chunks [4])_
- **[semantic]** The project's README.md file details quick start instructions, CLI flags, pipeline stages, output locations, the tech stack, grounding/audit trail, and multi-language support. _(chunks [4])_

---

## E-over-B (exp03, synthesis over B prose summaries) — for comparison

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

## Comparison

| | Entities | Memories | Input to E |
|---|---|---|---|
| D per-chunk (total) | 21 | 7 | raw chunk text |
| E-over-B | 11 | 5 | B prose summaries |
| E-over-D | 14 | 7 | D structured output |

---

## Key questions

- Does E-over-D produce better deduplicated entities than E-over-B?
  (E-over-D has richer input: typed entities + memories, not just prose)
- Does `spans_chunks` correctly identify multi-chunk memories?
- Are E-over-D memories higher quality than D per-chunk memories?
- Does E-over-D miss anything that D per-chunk caught?
- Is the D→E pipeline worth 6 calls (5 D + 1 E) vs E-over-B's 2 calls (5 B + 1 E)?
