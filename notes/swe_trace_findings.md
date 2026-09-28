# SWE-TRACE — Notes and Borrowable Ideas

> Paper: **SWE-TRACE: Optimizing Long-Horizon SWE Agents Through Rubric
> Process Reward Models and Heuristic Test-Time Scaling**
> Hao Han, Jin Xie, Xuehao Ma, Weiquan Zhu, Ziyao Zhang, ZhiLiang Long,
> Hongkai Chen, Qingwen Ye (vivo, ShenZhen). arXiv:2604.14820, Apr 2026.
>
> These are working notes summarizing what's in the paper and the concrete
> ideas worth borrowing for `design/segments_and_calibration.md` and
> `design/memory_evolution.md`. Read in conjunction with those two specs.

---

## TL;DR

SWE-TRACE optimizes the full lifecycle (data curation, RL, inference) for
long-horizon SWE agents. The pieces most relevant to agent_trace_signals
are the **data curation pipeline**, the **rubric structure**, the
**verbatim-anchor memory architecture**, and the **analysis of why
execution-only rewards fail** in long-horizon settings.

---

## What the paper actually does

### 1. Data curation pipeline (140K → 60K)

Starts with >1000 GitHub repos. Screens to **77 Dockerizable + testable
repos**. For each repo:

- Build a **test → function relevance graph** by executing the test suite.
- Constrain bug synthesis scope to functions covered by tests
  (`F_rel = ⋃_t Γ(t)`).
- **Test-aware bug synthesis** — generator sees both the target function
  and the relevant tests; injects perturbations that the tests can verify.

Effect of test-grounded scope: bug synthesis success rate **35% → 50.7%**.

Filtering protocol (4 stages):

1. **Environment validity** — Docker reliably builds and runs
2. **Test validity** — synthesized bug induces reproducible fail-to-pass
3. **Issue consistency** — issue description aligns with failure, no leak
4. **Difficulty / stability** — drop trivial and non-deterministic samples

Yields ~60K high-quality SFT instances.

### 2. LLM Multi-Task Cascading — the oracle scorer

Token-efficient trajectory optimization. At each step, generate K
candidates under **five task modes**:

```
M = {localize, inspect, edit, validate, summarize}
```

Each candidate scored by an oracle:

```
S(h, a) = λ₁·Δ_test + λ₂·Δ_scope + λ₃·Δ_patch + λ₄·Δ_info
          − λ₅·C_tok − λ₆·C_red
```

Where:
- `Δ_test` — does the action move test status forward?
- `Δ_scope` — does it move into the test-relevant function region?
- `Δ_patch` — is the proposed edit aligned with the hidden repair?
- `Δ_info` — does it reveal useful debugging information?
- `C_tok` — penalizes token-heavy actions
- `C_red` — penalizes redundancy (repeated reads, navigation loops)

Selected action `a*_t = argmax S(h_t, a)`. **Rejected candidates retained
as hard negatives** for downstream PRM training.

### 3. Memory architecture — verbatim anchors

When interaction history `h_t` exceeds context budget `L_max`, build a
memory buffer `M_t` of "critical steps":

```
m_j = (j, a_j, o_j)   -- step index, action text, observation
```

**Explicitly not abstractive summaries.** Their reasoning:

> "Long-horizon coding trajectories are highly sensitive to precise
> details such as filenames, stack traces, command outputs, and patch
> content; rewriting them can introduce hallucinated or omitted facts."

Critical-step detection uses **PRM score gradient**:

```
K_t = { j ≤ t : s_j^prm ≥ δ_abs   OR   |s_j^prm − s_{j-1}^prm| ≥ δ_chg }
```

- `δ_abs` selects intrinsically important steps
- `δ_chg` selects decision points that **significantly alter progress**

### 4. Rubric-Based PRM

`Rubric-Agent` generates an issue-specific rubric `R_x = {c_1, …, c_K}`.
Each criterion is a triple:

```
c_k = (u_k, z_k, w_k)
       ^   ^   ^
       │   │   weight in the trajectory score
       │   structured target descriptor (files / fns / patterns)
       natural-language rule
```

Four criterion categories:

1. **target localization** — which files/classes/functions are relevant
2. **edit constraints** — what modification is expected (specific
   function, interface change, invariant preservation)
3. **trajectory discipline** — avoid repeated ineffective validation,
   follow localize → edit → verify pattern
4. **budget awareness** — stay within reasonable step budget

Trajectory-level score:

```
s_prm(τ, R_x) = Σ_k w_k · q_k(τ, c_k) ∈ [0,1]
```

(They explicitly choose trajectory-level over per-step scoring; per-step
labels are too noisy for free-form supervision.)

### 5. PRM training — two stages

- **Stage 1**: rubric generator `g_ψ(I, C, U) → R_x` via SFT on
  rubric labels produced by MiniMax 2.5.
- **Stage 2**: pairwise PRM ranking objective on two kinds of pairs:
  - **Execution preference pairs** — one passes tests, one fails
  - **Rubric preference pairs** — both plausible, one ranked higher by a
    frozen rubric-conditioned judge

```
L_PRM = − Σ log σ( s_prm(τ⁺, R_x) − s_prm(τ⁻, R_x) )
```

Frozen judge is critical — judge ≠ trained model, judge stays fixed.

### 6. Margin-separated GRPO reward

```
            ⎧ (1 − γ)·s_prm(τ, R_x)        if r_exec = 0
R(τ) =      ⎨
            ⎩ γ + (1 − γ)·s_prm(τ, R_x)    if r_exec = 1
```

With `γ ∈ (½, 1)`. Guarantees passing > failing by margin
`Δ_min = 2γ − 1`, while PRM ranks within each execution class.

### 7. Why execution-only rewards fail in long-horizon (motivating analysis)

Three pathologies:

- **Reward indifference among successful trajectories** — efficient and
  bloated rollouts both get `r_exec = 1`.
- **Trajectory inflation** — policy reinforced for redundant work as long
  as the outcome happens to succeed.
- **Reward noise under imperfect tests** — `r̂_exec(τ) = r_true(τ) +
  ε_test(τ)`. Flaky / incomplete tests bias updates.

These motivate dense, process-aware reward.

### 8. Heuristic-Guided TTS

PRM reused at inference as a step-level guide. Evaluates a *provisional
extended prefix* `Π(h̄_t, a)` for each candidate action `a`; prunes weak
branches before they incur further environment cost.

---

## What we can borrow — concrete additions to our two specs

Numbered to match the discussion that produced this doc.

### For `design/segments_and_calibration.md`

1. **Decompose `evidence_supports_decision` into a progress/cost vector.**
   Currently the observer returns one scalar in `[0,1]`. Borrowing
   SWE-TRACE's oracle, return:
   - **Progress dimensions** (positive): `test_status_change`,
     `scope_alignment`, `target_specificity`, `info_gained`
   - **Cost dimensions** (negative): `token_inflation`, `redundancy`

   Dimensions specialize per decision type (`task_done` → test_status +
   scope; `root_cause` → target_specificity + info_gained). Lets us
   detect **trajectory inflation** as its own failure mode.

2. **Hard negatives — capture rejected alternatives at decision points.**
   When the observer scores a decision, also enumerate 1–2 plausible
   alternative actions and assess them. Schema addition: add
   `rejected_alternatives: TEXT` (JSON) to `decision_points`. Cheap if
   the observer is already reading the evidence; produces hard negatives
   for any future model training.

3. **Add `trajectory_inflation` to the failure-mode taxonomy.**
   A session can have `outcome = supported` while still being
   token-bloated / redundant. Detect via `length_bucket` +
   `redundancy_bucket` on segments. Currently flagged as `supported`;
   should be a soft-failure variant.

4. **Verbatim anchors on `evidence_text` — direct validation.**
   Their memory architecture stores verbatim observations rather than
   summaries, for exactly the reason we added `evidence_text` on
   `RawChunk`. External validation of that design decision; note it in
   the spec.

### For `design/memory_evolution.md`

5. **Critical-step detection via score gradient — sharper induction
   trigger.** Their `|s_j − s_{j-1}| ≥ δ_chg` selects decisions that
   significantly alter progress. Analog for us: high *calibration delta
   gradient between adjacent decisions in a segment* is a sharper
   induction trigger than "second occurrence of a failure_mode" — catches
   within-session inflections, not just cross-session recurrence.

6. **Rubric structure for procedural memories.** Their `(u_k, z_k, w_k)`
   triple is more structured than our free-form content strings:
   - `u_k` = the rule in natural language
   - `z_k` = structured target descriptor (overlaps with our feature
     buckets in `memory_evidence`)
   - `w_k` = weight in the posterior
   Refining procedural memories to this form unifies content with
   feature-bucket conditions.

7. **Frozen-judge pattern — strengthens our observer separation.**
   They explicitly use a frozen LLM judge for preference labels. The
   principle: judge and trained model must be different and stable. Our
   "separate observer LLM" already follows this; cite the rationale.

8. **Rubric generator as a separately-trainable module.** Their `g_ψ`
   is SFT'd from a teacher. Analog: once we have ground-truth labeled
   decision points (via label-failures viewer), the decision-point
   extractor could become a trainable module, not a forever zero-shot
   LLM call.

### Validation strategy (both specs)

9. **Pairwise ranking objective for labeling.** Pairwise labels are
   easier to produce than absolute scores and give a stronger gradient if
   we ever train an observer. Extend the label-failures viewer with a
   pairwise comparison mode for decisions and for memories.

10. **Margin separation between supported and refuted.** Their `γ`
    margin guarantees passing > failing while preserving rubric ranking
    within each class. Analog: when computing memory posteriors, treat
    `outcome=supported > outcome=unknown > outcome=refuted` as strict
    classes; within-class noise should not confuse across-class signal.

---

## What doesn't port

- **Oracle test-pass/fail** — they have ground truth from the test suite;
  we don't. Our soft outcome detection is the substitute.
- **RL training loop** — they're optimizing a policy. We're analyzing
  traces. PRM is a reward model for GRPO; ours would be an analytic
  signal for memory evolution.
- **Synthetic data scale (60K)** — they have a generator with a verifier.
  Until we have labeling tooling, we operate at much smaller scale.

---

## Companion papers attempted

Tried abstracts/fetches for SWE-Gym (2412.21139), Agent-FLAN (2403.12881),
and the paper at 2310.10134. Returned mostly abstract-level content
without enough technical detail to extract concrete borrows.

If we want more sources for trace curation methodology, candidates worth
chasing:

- SWE-Gym (Pan et al. 2025) — first open SWE training environment
- SWE-smith (Yang et al. 2025) — 50K synthetic instances from 128 repos
- R2E-Gym (Jain et al. 2025) — 8K executable tasks with verifier-guided
  TTS
- SWE-Master (Song et al. 2026) — long-horizon SWE via post-training
- SWE-RM (Shum et al. 2025) — argues verifier quality, discrimination,
  and calibration matter for stable RL signal

Citations from SWE-TRACE's related work.
