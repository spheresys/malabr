# Decision 1 answered: BOTH — and the frame that makes it one thesis

Chaitanya chose both the systems and the security direction (2026-10-04,
post-completion amendment; see D11). This file records the unifying argument,
what changes in the plan, and the honest risks. It supersedes
`07_THESIS_OUTLINE.md` §1 (storyline) and adds one RQ to
`06_RESEARCH_QUESTIONS.md`.

---

## 1. The problem with "both" as stated

Taken naively, "both" means Chapters 4-5 are a scheduling paper and Chapter 6 is
a security paper, sharing a codebase and nothing else. Examiners notice. The
thesis would read as two M.Tech projects with one introduction, and the obvious
question — *why are these in the same document?* — would have no answer better
than "the same student wrote them".

So "both" needs a claim that **requires** both halves to be true.

## 2. The unifying claim

> **In a shared local model, the scheduling policy is simultaneously an
> allocation mechanism and an information channel. The same decision rule that
> determines who gets served determines what co-tenants can learn about each
> other. Therefore responsiveness, fairness and leakage cannot be chosen
> independently — they are one trade-off surface, and this thesis is the first
> to characterise it.**

Why this is a single claim rather than two:

- `build_batch` decides admission from **other sessions' state** — their context
  depths via `CostCurve`, aggregate pressure, who is foreground. That is exactly
  what makes it responsive, and exactly what makes it observable.
- A policy that ignored neighbours' state would leak nothing through admission
  — and would also be unable to prefer the visible tab. **The property that
  gives MALABR its headline result is the property that opens the channel.**
- So "make the foreground faster" and "make origins less observable to each
  other" are *the same dial turned in opposite directions*. That is a finding,
  not a framing device — and it is testable (C2-RQ2 already asks whether
  capacity depends on policy).

## 3. What this adds: one new research question

### JOINT-RQ — Is there a responsiveness / fairness / leakage trade-off surface, and what shape is it?

**Hypothesis.** Across the policy family implemented behind I8's seam
(foreground-first, BVT-warp at several `w`, equal-share, cost-denominated DRR),
channel capacity varies **monotonically with the strength of neighbour-dependent
preference**. Concretely: capacity is highest under unconditional
foreground-first, lowest under equal-share, and intermediate under BVT-warp —
so `w` tunes leakage as well as latency, and no setting optimises all three.

**Design.** One sweep, three measurements per point:
- *x-axis:* warp `w` (and the policy family at the extremes).
- *measure 1:* foreground TTFT (existing `pipeline_bench.py`).
- *measure 2:* Jain fairness over per-session tok/s (existing).
- *measure 3:* cross-origin channel capacity in bits/sec (new harness, C2).
- Repeat in **both** KV modes (partitioned, shared) — 2 × |policies| cells.
- n ≥ 5 per cell, warm-up discarded.

**Confirms** if capacity orders monotonically with preference strength, giving a
genuine three-dimensional surface with no dominating point.

**Refutes** in two distinct and separately useful ways:
- *Capacity is policy-invariant* → the channel lives in the **shared forward
  pass**, not the scheduler. Then the joint claim weakens, but a sharper one
  replaces it: *batching itself leaks, so every batched server including
  Chrome's inherits it regardless of policy.* That is arguably a better result.
- *Capacity is below 1 bps everywhere* → the joint claim becomes a **safety
  property of the design**: attention-aware scheduling can be had without
  measurable leakage. Still a joint statement about both halves.

**There is no outcome in which "both" collapses back to one direction.** That is
the main reason this frame is worth adopting.

## 4. Positioning against the literature — and the one paper to check first

Two communities that largely do not cite each other:

| | optimises | treats the scheduler as |
|---|---|---|
| serving / fairness (VTC, FairServe, ProServe, Kairos) | latency, fairness, SLO attainment | the object of design; leakage not considered |
| KV-cache security (InputSnatch, PromptPeek, KVGov) | leakage prevention | largely fixed; the *cache-sharing* mechanism is attacked |

The thesis sits in the join: it varies the **scheduler** and measures
**leakage**, which neither community does.

**CHECKED — see `09_VERIFICATION.md` V1.** KVGov (arXiv 2608.09225) **does**
link scheduling to leakage (its ORIGAMI audit scheduler) and **does** present a
leakage-vs-efficiency frontier, so it must be credited for the premise. But its
channel is the **prefix cache**, and MALABR has no cross-session prefix reuse —
verified in source: slots are wiped on acquire and the wipe is proved
(`seq_rm(-1,-1)` then `pos_max == -1` or `SlotWipeError`). All three of KVGov's
attack families are therefore **structurally inapplicable** here.

The claim narrows but survives, and the honest form is:

> KVGov establishes that scheduling affects leakage in cloud serving, for the
> prefix-cache channel. This thesis asks the same question for a channel that
> survives the removal of prefix sharing — **scheduler contention itself** — in
> a setting where tenants are **web origins** and the policy axis is **user
> attention** rather than adversary utility.

Drop "first to characterise the surface". Read KVGov in full before the
introduction, especially its llama.cpp/Metal replication section.

## 5. What changes in the plan

Less than you would expect, because `07_THESIS_OUTLINE.md` already allocated
chapters to both halves. The changes are structural, not additive:

**Thesis statement (replaces Ch. 1's framing).** From *"governing a shared local
model needs a policy, a memory contract and a threat model"* to *"in a shared
local model, allocation and isolation are the same mechanism, and this thesis
characterises the surface they jointly define."*

**Chapter arc (Ch. 4-6 become one movement, not two).**
- Ch. 4 — the policy and what it buys (responsiveness, fairness). *Ends by
  observing that the policy reads neighbours' state.*
- Ch. 5 — the memory contract (what is lost under pressure).
- Ch. 6 — **the same policy read as a channel**: what neighbours can learn
  because the policy reads their state. Opens by referring back to Ch. 4's
  closing observation.
- Ch. 7 — the joint surface (JOINT-RQ), then evaluation.

That one back-reference is what converts two halves into an argument.

**Timeline: +1 week, 16 → 17.** The joint sweep is one new week (W11.5, folded
into Phase C) because it *reuses* C1's policies and C2's harness rather than
building anything. Revised phases:
- A: weeks 1-2 — bank existing results (unchanged)
- B: weeks 3-7 — policy seam, BVT, floor, cost accounting (unchanged)
- C: weeks 8-12 — channel harness, capacity vs policy and KV mode, Chrome,
  **joint sweep**, write Ch. 6
- D: weeks 13-15 — heterogeneous workloads, scaling, write Ch. 7
- E: weeks 16-17 — Ch. 1/2/8, Scholar verification, assembly

**Critical path is unchanged and now matters more:** I8's policy seam (week 3)
gates Phase B, C2-RQ2 *and* JOINT-RQ. If it slips, the joint claim is the first
thing lost.

## 6. Honest risks of choosing both

1. **Scope.** 17 weeks assumes nothing goes wrong across two subfields. The
   mitigation is the dependency order: Ch. 4-5 (systems) stand alone if Ch. 6
   fails, and the already-measured contributions (degradation content, lifecycle
   reclamation, pool division) are banked in week 1 regardless.
2. **Two literatures to know properly.** You now need to be credible to both a
   serving reviewer and a security reviewer. The security half is the one you
   have not read yet — budget real reading time in week 8, not just
   implementation time.
3. **The channel may be unmeasurable** on CPU-only 0.6B. Covered: §3's second
   refutation path keeps the joint frame intact as a safety property.
4. **KVGov may have got there first** on the trade-off idea (§4). Check before
   committing the introduction.
5. **A2 is still assumed.** Choosing both does not help with
   "visibility ≈ attention"; it arguably raises the stakes, since the security
   half now also depends on attention-driven preference being the right design.

## 7. What to do differently starting now

1. **Read KVGov (arXiv 2608.09225) before anything else.** It determines how
   large the JOINT-RQ claim can be.
2. **Scholar-verify `"borrowed virtual time" + LLM serving`** — unchanged in
   priority; C1 is still the spine.
3. **Build the policy seam in week 3 and treat it as the project's keystone.**
   It is now load-bearing for both halves.
4. When building the channel harness (week 8), **instrument it to record the
   policy in force**, so JOINT-RQ's sweep is a configuration change rather than
   a re-measurement.
