# Continual Learning & Agent Self-Improvement — Paper Notes

> Working notes from June 2026 reading. Source: a continual-learning
> roundup that highlighted the "sleep" framing (offline consolidation as
> a controlled phase between experience and parameter change) plus three
> specific papers on agent reliability, harness design, and continual
> experience internalization.
>
> The thread argues that the live-update vs. static-retrieval dichotomy is
> too crude — agents need a third frame: **interact → collect experience
> → process it offline → decide what should persist, stay temporary, or
> be discarded.** Reads as direct external validation of what we're
> building.

---

## The "sleep" framing (background)

- **CMU/Maryland — "Do Language Models Need Sleep?" (May 25, 2026)**:
  inference-side argument. Long context is expensive; compression alone
  is insufficient when the model has to reason about information it can
  no longer attend to. Proposes an offline recurrent pass over recent
  context before clearing it. **Memory is not just storage — it is
  processing.**
- **Google-affiliated — "Language Models Need Sleep" (Jun 2, 2026)**:
  two-step **Sleep paradigm** — "Knowledge Seeding" consolidates
  short-term knowledge into stable parameters; "Dreaming" rehearses via
  model-generated synthetic data. Separates durable learning from live
  interaction.
- **Survey (2026)** — *Continual Learning in Large Language Models*
  divides the field into continual pre-training, continual fine-tuning,
  and continual alignment. Conclusion: methods work in limited settings;
  no smooth learning across tasks and time yet.
- **OpenAI ChatGPT "Dreaming" memory update (Jun 4, 2026)** —
  system-side, not parametric: synthesizes user memory in the background
  for freshness and continuity. Shows the same pressure appearing in
  production.

**Why this matters for agent_trace_signals:**
the architecture we're designing across `segments_and_calibration.md` and
`memory_evolution.md` IS this third frame. We:
- Observe traces (interaction)
- Extract per-segment / per-decision artifacts (collect experience)
- Run an offline analytics + evolution loop (process offline)
- Promote / patch / retire memories via the Bayesian posterior + rewrite
  policy (decide what persists)

The "sleep" framing is the missing vocabulary for our system-level pitch.

---

## Paper 1 — Span-Level Error Localization (TELBench / DRIFT)

> **Where Do Deep-Research Agents Go Wrong? Span-Level Error Localization
> in Agent Trajectories** — arXiv:2606.02060
> Wang, Feng, Wu, Li, Xie, Ren, Zhu, Han, Meng, Feng, Liu.

### What it does

Localizes errors at the **span level** inside long agent trajectories
(rather than scoring the trajectory as a whole). Two artifacts:

- **TELBench** — 1,000 instances of trajectories annotated with error
  spans. Drawn from 2,790 real trajectories across 2 agent frameworks,
  3 backbone models, 3 benchmarks. Spans annotated via "LLM-assisted
  expert review."
- **DRIFT** — claim-centric auditing framework: tracks the claims an
  agent makes, checks whether trajectory evidence supports them, flags
  spans where unsupported or conflicting claims affect the answer path.

### Span taxonomy

Four categories:
- `normal exploration`
- `failed searches`
- `tentative hypotheses`
- `harmless noise`

The third category — tentative hypotheses — is the subtle one. Not every
unsupported claim is a real error; the framework distinguishes hypothesis
formation from claim-as-fact assertion.

### Direct overlap with our specs

DRIFT is **the closest published analog** to our decision-point
calibration design. Both:
- Track agent claims/assertions inside long trajectories
- Verify against evidence available in the trace
- Flag spans (≈ our decisions) where evidence doesn't support the claim
- Operate offline / post-hoc on completed traces

Differences to note:
- DRIFT operates at the **span** level (variable-length text region).
  Our decision_points are anchored to a single exchange. Their span
  framing may be a finer-grained alternative we want to consider.
- They have a **labeled benchmark (TELBench, 1K instances)**. We have
  ~20 labeled inflections. The benchmark + their span taxonomy could
  serve as inspiration / external validation for our label-failures
  viewer.

### Borrowable ideas

- **`tentative_hypothesis` decision type** — separate from `root_cause`
  and `task_done`. Captures "I'll try X" or "this might be the bug"
  without the committing action. Useful for nuance in the
  agent_expressed_confidence scale.
- **Span granularity** — our decision_points anchor to one exchange; a
  span representation (start_exchange → end_exchange) might be needed
  for claims that develop over multiple turns. Schema could add
  `exchange_span_end` alongside `exchange_idx`.
- **Claim graph / contradiction detection** — DRIFT flags
  *self-contradicting* claims within a trajectory. Add as a third
  outcome type: `outcome = 'self_contradicted'` when a later decision in
  the same session contradicts an earlier supported one.

---

## Paper 2 — Harness-1 (state-externalizing harness)

> **Harness-1: Reinforcement Learning for Search Agents with
> State-Externalizing Harnesses** — arXiv:2606.02373
> Jiang, Shi, Hong, Xu, Sun, Sun, Bashir, Han.

### What it does

Moves the **agent's working memory out of the model and into structured
harness-side data**. The harness maintains:

- candidate pool
- importance-tagged curated set
- compact evidence links
- verification records
- compressed and deduplicated observations
- budget-aware context rendering

The neural policy is restricted to "semantic decisions: what to search,
which documents to keep or discard, what to verify, and when to stop."
State transitions and memory updates are done by the harness.

A 20B search subagent trained via RL inside this stateful harness
achieves 0.730 curated recall across 8 retrieval benchmarks (+11.4
over next-strongest open subagent).

### Why this is relevant to us — trace observability

Traditional traces hide state inside the model's context window — the
agent's "working memory" is just whatever's in the transcript. To
analyze, you have to infer state from sequence of messages.

Harness-1's externalized-state design makes traces **much more
analyzable**: each decision appears alongside explicit, structured
context (candidate pool snapshots, verification records, evidence
links). The "what did the agent know when it decided" question becomes
a database query, not an inference.

Our project's contribution is the reverse direction: we're trying to
**reconstruct externalized state from un-externalized traces** (CC,
Gemini CLI, Opencode all keep state in the transcript). The artifacts
we extract — `session_segments`, `decision_points`,
`memory_evidence`, `exchange_type` — are exactly the kind of
externalized state Harness-1 builds into the harness.

This is validation of the artifact taxonomy we're producing. It also
suggests a future direction: if any harness adopts Harness-1-style
state externalization, our ingestion pipeline becomes dramatically
simpler — read the harness's state log directly instead of inferring
from transcripts.

### Borrowable ideas

- **`candidate pool` analog for decisions** — at each decision point,
  the alternatives the agent considered but didn't commit to. Aligns
  with SWE-TRACE's "rejected alternatives" idea and our planned
  `rejected_alternatives` schema column. Externalizing this in the
  trace makes the calibration delta more interpretable.
- **`verification records` analog** — when a decision has been verified
  by a later observation (tests run, file inspected post-edit), the
  link between decision and verification observation is a first-class
  artifact. Our outcome detection currently computes this on-the-fly;
  storing it explicitly in a `decision_verifications` table would let
  the observer reference it directly.
- **Importance tagging** — Harness-1 tags items in the curated set with
  importance. Memories in our system could carry an "importance" field
  derived from posterior mean + recency + retrieval frequency.

---

## Paper 3 — Continual Experience Internalization & Capability Collapse

> **Rethinking Continual Experience Internalization for Self-Evolving
> LLM Agents** — arXiv:2606.04703
> Chen, Yang, Fan, Nie, Sun, Zheng, Hu, Pan, Zeng, Lin.

### What it does

Studies what happens when LLM agents iteratively learn from their own
past interactions. Diagnoses **progressive capability collapse rather
than compounding improvement** under multi-iteration experience
learning. Proposes a fix along three dimensions:

1. **Principle-level experience over instance-level** — abstract
   transferable rules rather than memorize specific cases.
2. **Step-wise injection aligned with intermediate decision states** —
   inject the relevant experience *at the decision moment*, not
   globally as context preamble.
3. **Off-policy context-distillation on high-quality teacher
   trajectories** — more stable than on-policy methods that
   recursively learn from the agent's own outputs.

### Why this is the most important paper for us

This is the **single most direct warning** about a failure mode the
memory_evolution spec could otherwise walk into:

> When an agent learns from its own past sessions, repeated cycles can
> degrade capability rather than improve it. The mechanism: instance-
> level details get entangled with transferable strategies; on-policy
> updates from noisy traces amplify their own errors.

In our terms: if Track 2 mints `failure_response` memories from
sessions that include the agent's own (degraded) decisions, then
retrieves those memories into future sessions, then mints new memories
based on traces of that degraded behavior — **capability collapse, not
compounding improvement.**

### Concrete implications for `memory_evolution.md`

These are not optional — they're failure-mode mitigations:

1. **Mint principle-level memories, not instance-level.**
   The Track 2 induction LLM prompt must abstract: not "in session XYZ
   the agent edited foo.py when it should have edited bar.py" but
   "when error_rate_bucket=>50% in fix_bug context, verify localization
   before editing." Our existing failure_response framing already leans
   this way; the paper makes it a hard requirement.

2. **Step-wise injection (retrieval at decision points).**
   Memories should surface *at the relevant decision moment*, not as
   context preamble. Implication for the retrieval log + MCP integration:
   log which memories were surfaced **for which segment / decision
   class**, not just per-session.

3. **Off-policy distillation > on-policy self-learning.**
   The strongest mitigation for capability collapse: weight memories
   minted from *high-quality teacher trajectories* (e.g., sessions with
   no refuted decisions, low calibration delta) more heavily in the
   prior. Memories minted from sessions that themselves had refuted
   decisions get **weaker priors** and stricter promotion gates.

4. **Hard isolation between "self-trained" and "teacher" memory pools.**
   Tag each memory with its provenance quality. Don't allow a low-
   quality session to update a high-quality memory's posterior except
   under stricter thresholds.

### Borrowable ideas (concrete)

- **`provenance_quality` on memories**: derived from the session-level
  calibration delta + outcome distribution of the source sessions.
  Memories with low provenance_quality get a weaker prior and may be
  excluded from feeding into future Track 2 inductions.
- **`source_session_quality_filter` on Track 2 induction**: only mint
  `failure_response` memories from sessions that ALSO had at least one
  `outcome=supported` decision with high observer_confidence.
  Otherwise we risk minting "lessons" from sessions where the agent was
  systematically confused — exactly the entanglement failure mode.
- **Off-policy bootstrap**: for the cold-start phase, prefer minting
  memories from sessions where the user explicitly confirmed success
  (a stronger external signal than our soft outcome detection). Use the
  label-failures viewer to gather these.
- **Decay on retrieval-conditioned counts**: if a memory's posterior is
  driven by retrievals from a degenerating self-trained loop, apply
  exponential decay on `memory_evidence` counts so old observations
  fade. Prevents lock-in.

---

## Cross-paper synthesis — what changes for our specs

### `segments_and_calibration.md`

- Add `tentative_hypothesis` as a v2 decision type (DRIFT taxonomy).
- Add `outcome = 'self_contradicted'` as a third outcome class (DRIFT
  contradiction detection).
- Consider span-level (multi-exchange) anchoring for some decision
  types; current single-exchange anchoring may miss multi-turn claim
  development.
- Treat the per-decision artifact we produce as the "externalized
  state" equivalent of what Harness-1 builds in. Cite the validation.

### `memory_evolution.md`

- **Add a "capability collapse mitigation" section.** Make it explicit
  that on-policy self-learning is a known failure mode and our
  mitigations (provenance_quality, principle-level abstraction in
  induction, off-policy bootstrap, decay) are deliberate guards.
- Add `provenance_quality: REAL` column on `memories`.
- Add `source_session_quality_filter` requirement to Track 2 induction.
- Note step-wise (per-decision) retrieval injection as the target
  integration pattern for the MCP server, not session-level preamble
  injection.
- Cite continual_learning_papers in the references section.

### `swe_trace_findings.md`

- No changes needed; complementary findings.

---

## References

- **Sleep papers**:
  - Do Language Models Need Sleep? (CMU/Maryland, May 25 2026)
  - Language Models Need Sleep (Google-affiliated, Jun 2 2026)
- **Survey**: Continual Learning in Large Language Models (2026)
- **OpenAI ChatGPT "Dreaming"** memory update (Jun 4 2026, blog post)
- **TELBench/DRIFT**: arXiv:2606.02060
- **Harness-1**: arXiv:2606.02373
- **Continual Experience Internalization / Capability Collapse**:
  arXiv:2606.04703
- Companions to look up next:
  - "Roadmap on lifelong learning for LLM agents" (mentioned in the
    thread; not yet retrieved)
