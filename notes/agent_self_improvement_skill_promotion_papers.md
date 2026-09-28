# Agent Self-Improvement, Memory Processing, Skill Promotion — Paper Notes

> Working notes from a curated reading list (8 papers) on agent
> self-improvement and skill libraries. Spans 2023–2026, with the
> 2025-2026 papers being the most directly relevant to
> `design/memory_evolution.md`.
>
> Companion reading to
> [continual_learning_and_self_improvement_papers.md](continual_learning_and_self_improvement_papers.md)
> (which covers TELBench/DRIFT, Harness-1, and the capability-collapse
> paper) and [swe_trace_findings.md](swe_trace_findings.md).

---

## Quick map

| # | Paper | Year | arXiv | Relevance to us |
|---|---|---|---|---|
| 1 | Reflexion | 2023 | 2303.11366 | Foundational. Verbal RL via self-reflection on trajectories. |
| 2 | Voyager | 2023/24 | 2305.16291 | Foundational skill-library + curriculum. Lifelong learning prototype. |
| 3 | SkillWeaver | 2025 | 2504.07079 | Autonomous skill discovery → reusable APIs. Transfer to weaker agents. |
| 4 | Agent KB | 2025 | 2507.06229 | Cross-architecture shared memory system. Direct analog to our memory layer. |
| 5 | SEAgent | 2025 | 2508.04700 | Computer-use agent self-evolution. World State Model + Curriculum Generator. |
| 6 | MemoryAgentBench | 2025 | 2507.05257 | Benchmark; 4 memory competencies — evaluation framing. |
| 7 | When Continual Learning Moves to Memory | 2026 | 2604.27003 | **Directly relevant** — abstract procedural > detailed trajectory transfer. |
| 8 | SKILLFOUNDRY | 2026 | 2604.03964 | Skill library via knowledge-tree mining + operational contracts. |

---

## 1. Reflexion (2023) — arXiv:2303.11366

**Idea:** verbal reinforcement — after a failed trajectory, the agent
writes a natural-language reflection that gets prepended to the next
attempt. No weight updates; the "policy" lives in the reflection text.

**For us:** the reflection itself is an extracted artifact analogous to
our `failure_response`-induced procedural memory. Reflexion is essentially
the simplest possible version of what `memory_evolution.md` builds:
single-shot, in-session, no posterior, no cross-session aggregation.

**Borrow:** the **reflection-as-procedural-memory** mapping. When Track 2
mints a `failure_response` memory, the content should read like a
Reflexion reflection ("when X, do Y; last time I did Z which failed
because W") — actionable for the next time the same feature pattern
recurs.

---

## 2. Voyager (2023/24) — arXiv:2305.16291

**Idea:** open-ended Minecraft agent with three components:
- Automatic curriculum (proposing tasks of increasing difficulty)
- Iterative prompting + self-verification (rewriting buggy code)
- **Skill library** — successful programs added as named skills, indexed
  by embedding for retrieval

**For us:** Voyager's skill library is **the original procedural memory
library**. Key choices:
- Skills are **executable code**, not natural-language rules. Retrieval
  is by similarity to current task description.
- No posterior — just kept-or-not-kept after self-verification.
- Curriculum is driven by what the agent has already mastered, not by
  failure patterns.

**Borrow:** the indexing structure. Voyager's skill index is the prior
art for our `memories` + embedding retrieval. Our addition: feature-
conditioned posteriors on the index entries (Bayesian-Agent-style) plus
failure-driven induction (not just success-driven).

---

## 3. SkillWeaver (2025) — arXiv:2504.07079

> Zheng, Fatemi, Jin, Wang (Zora), Gandhi, Song, Gu, Srinivasa, Liu,
> Neubig, Su.

**Idea:** web agents that **autonomously discover skills, execute them
for practice, and distill practice experiences into robust APIs.**
Skills are reusable APIs — typed function signatures with documented
behavior. Iterative expansion of a skill library.

**Results:** 31.8% and 39.8% relative success rate gains on benchmark
and real websites. **APIs from stronger agents transfer to weaker agents
with up to 54.3% gain** — empirical evidence that skill libraries are a
genuine artifact, not just policy-internal hacks.

**For us:**
- **Practice loop** — SkillWeaver explicitly *practices* a skill by
  executing it on synthetic instances after induction. We don't have a
  practice mechanism; could be a memory-evolution v2: when a Track 2
  memory is minted, generate synthetic retrieval contexts and "rehearse"
  the memory by checking whether the observer would judge each rehearsed
  application as `supported`.
- **API-style skill structure** — typed inputs/outputs, documented
  preconditions. Maps to a richer `procedural memory` schema where
  content has structured fields: `when`, `inputs`, `action`, `outputs`,
  `preconditions`. Cleaner than free-form text.
- **Strong→weak transferability** as a benchmark for the memory library
  — if memories from high-quality sessions can be retrieved into a
  weaker agent's session and improve outcomes, the library has value.

---

## 4. Agent KB (2025) — arXiv:2507.06229

> Tang, Qin, Peng, Zhou, Shao, Du, Wei, Xia, Wu, Zhu, Zhang, Liu, Wang,
> Hong, Wu, Cheng, Wang, Zhou.

**Idea:** universal memory system that enables **knowledge sharing
across different agent architectures without retraining**. Hybrid
retrieval with planning + feedback stages. Transfers cross-domain
problem-solving strategies.

**Results:** smolagents gains up to **18.7 points** by tapping shared
memory infrastructure.

**For us:** this is the closest published analog to the
**cross-harness** dimension of our memory layer. Our project ingests
traces from CC, Gemini CLI, and Opencode into one schema. Agent KB is
the same idea but for *consumption* (the memory store is the back-end
for many agent frameworks).

**Borrow:**
- **Hybrid retrieval (planning + feedback stages).** A two-stage
  retrieval pipeline at MCP-server query time: stage 1 finds candidate
  memories by feature/embedding match; stage 2 plans which memories to
  surface given the current decision context. Our `decision_points` +
  `query_features` give us the inputs.
- **Architecture-agnostic memory schema** — Agent KB validates that the
  same memory store can serve heterogeneous agents. Our harness-agnostic
  `memories` table is on the same track; tagging memories with
  `applicable_harnesses` (if patterns differ) might be needed.

---

## 5. SEAgent (2025) — arXiv:2508.04700

> Sun, Liu, Zang, Cao, Dong, Wu, Lin, Wang.

**Idea:** computer-use agent that autonomously evolves through
interactions with unfamiliar software. Two key components:
- **World State Model** for trajectory assessment (judges what the
  agent's actions accomplished in the environment)
- **Curriculum Generator** for auto-generating progressively
  challenging tasks
- **Specialist-to-generalist** training: insights from individual
  specialized agents enhance overall performance

**Results:** +23.2 points on 5 new software environments vs. open-source
alternatives.

**For us:**
- **World State Model = our observer LLM** for `decision_points`. Both
  judge "what happened in the environment" from observations. SEAgent's
  framing gives us the right vocabulary.
- **Specialist-to-generalist promotion** maps to the rewrite-policy
  `compress` action: a memory minted from a single domain (one repo,
  one project) is a "specialist" memory; when posteriors converge
  across domains, compress into a "generalist" memory.

---

## 6. MemoryAgentBench (2025) — arXiv:2507.05257

> Hu, Wang, McAuley.

**Idea:** benchmark of LLM agent memory across **4 competencies**:
- accurate retrieval
- test-time learning
- long-range understanding
- **selective forgetting**

Finding: current memory architectures fail to handle all four.

**For us:**
- The **4-competency framework** is a ready-made evaluation rubric for
  the memory_evolution spec. Use it to structure the validation section:
  test our memory layer against each competency.
- **Selective forgetting** is the one most underserved by existing
  systems and most directly addressed by our `lifecycle_state =
  'retired'` action. Explicitly call out forgetting (not just
  acquisition) as a first-class operation.

---

## 7. When Continual Learning Moves to Memory (2026) — arXiv:2604.27003

> Hu, Long, Wang.

**This is the single most directly applicable paper in this list.**

**Core finding:** while external memory appears to bypass traditional
stability–plasticity challenges, it **relocates** the problem to the
memory retrieval level. Specifically:

- **Abstract procedural memories transfer better than detailed
  trajectories.** (Same conclusion as the capability-collapse paper.)
- **Finer-grained organization enables forward transfer but also
  causes forgetting.**

So: there's a granularity tradeoff, and abstraction beats verbatim
trajectory storage.

### Direct implications for `memory_evolution.md`

1. **Bias procedural memory induction toward abstraction.** The Track 2
   `failure_response` prompt should explicitly instruct: "extract the
   transferable principle, not the session-specific details." Already
   implied by our spec; this paper makes it a hard requirement.

2. **Granularity is a tunable dimension, not a fixed schema choice.**
   The same observation can be stored at multiple abstraction levels
   (specific incident → general principle → meta-strategy). Could add
   `abstraction_level` to memories so retrieval can prefer
   coarse-grained for transfer, fine-grained for local use.

3. **Forgetting at the retrieval level, not the storage level.**
   Don't physically delete memories during `retire` — instead, mark
   them retrieval-ineligible. This preserves audit trail and allows
   "un-retirement" if a previously-retired memory becomes relevant
   again under new evidence. Already our design intent; this paper
   strengthens the rationale.

4. **Tension with verbatim anchors (SWE-TRACE).** SWE-TRACE argued for
   verbatim observation storage (no rewriting). This paper argues for
   abstraction. The reconciliation: **verbatim for evidence (low-level
   tool outputs that ground a decision), abstracted for procedural
   memory (the lesson learned from many such groundings).** That's
   exactly the split we already have between `evidence_text` (verbatim)
   and `memories.content` (procedural, abstracted).

---

## 8. SKILLFOUNDRY (2026) — arXiv:2604.03964

> Shen, Cheng, Ma (Mingqian), Turcan, Zhang (Martin Jinye), Ma (Jian).

**Idea:** build a self-evolving agent skill library from
**heterogeneous scientific resources** (papers, code repos, protocols).
Pipeline:
- Organize domains as **knowledge trees**
- **Mine high-value resources**
- **Extract operational contracts**
- **Iteratively refine** the skill library through validation

**Results:** mined skills outperform existing libraries on benchmarks
and improve specialized genomics tasks.

**For us:**
- **Operational contracts** = the typed `(when, inputs, action,
  outputs, preconditions)` schema also suggested by SkillWeaver. Both
  papers converge on this. Strong signal that free-form text content for
  procedural memories is leaving value on the table.
- **Knowledge tree organization** is a more ambitious alternative to
  our flat `memories` table + procedural_associations. A tree gives
  hierarchical retrieval (search the parent node, then descend). Could
  be a v3 evolution: cluster memories into a hierarchy by content
  similarity + feature buckets.
- **Validation via execution** — SKILLFOUNDRY validates skills by
  running them. The agent_trace_signals analog is the
  retrieval-conditioned outcome signal already in our memory
  usefulness design.

---

## Cross-paper synthesis — convergent findings

**All five 2025-2026 papers converge on:**

1. **Abstracted/principle-level memory > instance-level trajectory
   memory.** Reinforces the capability-collapse mitigation already in
   `memory_evolution.md`.
2. **Structured skill schemas** with typed contracts (when/inputs/
   action/outputs/preconditions) outperform free-form content.
3. **Practice / validation loops** matter — minting a memory isn't
   enough; the memory needs evidence of usefulness before it's relied
   on. Our Bayesian posterior with retrieval-conditioned counts is the
   analog.
4. **Cross-context transfer is the gold standard** for evaluation.
   Memories that only work in their originating context aren't useful.
   Our cross-session feature buckets in `memory_evidence` are designed
   for this.
5. **Forgetting is first-class.** Not a side-effect; an explicit
   action. Our `lifecycle_state = 'retired'` directly maps.

---

## Aggregate borrowable changes for our specs

### `memory_evolution.md` — schema enrichment

Add structured fields to procedural memories (from SkillWeaver +
SKILLFOUNDRY):

```sql
ALTER TABLE memories ADD COLUMN skill_when TEXT;
                          -- the trigger condition (NL + feature pattern)
ALTER TABLE memories ADD COLUMN skill_inputs TEXT;
                          -- what's needed to apply (JSON list)
ALTER TABLE memories ADD COLUMN skill_action TEXT;
                          -- the actual procedure (could mirror content)
ALTER TABLE memories ADD COLUMN skill_outputs TEXT;
                          -- expected outcome / signal of success
ALTER TABLE memories ADD COLUMN skill_preconditions TEXT;
                          -- preconditions that must hold
ALTER TABLE memories ADD COLUMN abstraction_level TEXT;
                          -- 'incident' | 'principle' | 'meta_strategy'
```

These apply to procedural memories; nullable for episodic/semantic.

### `memory_evolution.md` — validation framework

Use MemoryAgentBench's 4 competencies as the validation scaffold:
- Accurate retrieval
- Test-time learning (does new evidence actually update beliefs?)
- Long-range understanding (cross-session pattern recognition)
- Selective forgetting (do retired memories stay retired appropriately?)

### `memory_evolution.md` — practice loop (v2)

Borrow SkillWeaver's practice mechanism: after a memory is minted,
generate K synthetic retrieval contexts matching its feature buckets,
present them to the observer, and check whether the observer judges the
memory's prescribed action as `evidence_supports = high`. Update the
posterior accordingly. This makes the cold-start prior less critical.

### `memory_evolution.md` — abstraction-tuned induction

Track 2 `failure_response` induction prompt explicitly produces memories
at multiple abstraction levels (paired):
- `incident` — what happened in this cluster
- `principle` — the transferable rule
- (optional) `meta_strategy` — when to apply the principle

Retrieval defaults to `principle`-level; `incident`-level surfaced only
when explicitly asked for case studies.

---

## Reading priority for follow-up

If we want to deep-read 2 of these, do **7 (When Continual Learning
Moves to Memory)** first — most directly addresses our risk surface —
then **4 (Agent KB)** for the cross-architecture memory framing.
SKILLFOUNDRY and SkillWeaver are interchangeable for the
structured-skill-schema lesson.

Reflexion and Voyager are foundational and well-known; abstract-level
familiarity is sufficient unless we run into specific implementation
questions.

---

## References

| Paper | arXiv |
|---|---|
| Reflexion | 2303.11366 |
| Voyager | 2305.16291 |
| SkillWeaver | 2504.07079 |
| Agent KB | 2507.06229 |
| SEAgent | 2508.04700 |
| MemoryAgentBench | 2507.05257 |
| When Continual Learning Moves to Memory | 2604.27003 |
| SKILLFOUNDRY | 2604.03964 |

Companion notes:
[continual_learning_and_self_improvement_papers.md](continual_learning_and_self_improvement_papers.md)
| [swe_trace_findings.md](swe_trace_findings.md)
