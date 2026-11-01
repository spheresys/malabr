# STEP 7 — Thesis shape

**Recommended direction:** C1 as the spine, C2 as the distinctive chapter, C3
threaded through both as the evaluation. Rationale in `05_FEASIBILITY.md` §4.

Timeline is in **relative weeks** because no submission date was supplied (D8).

---

> **AMENDED 2026-10-04.** Decision 1 was answered **both**. The storyline in
> §1 and the 16-week plan in §5 are **superseded by
> `08_BOTH_DIRECTIONS.md`** (§5 there), which rebuilds Ch. 4-6 as a single arc
> around the claim that allocation and isolation are the same mechanism, and
> extends the plan to 17 weeks. The chapter list, contributions and figure table
> below remain current.

## 1. The storyline

One sentence: *sharing a local model across web origins already ships — but it
is not governed, and governing it requires a policy, a memory contract, and a
threat model that the serving literature does not supply because none of it runs
in a browser.*

The argument in five moves:

1. **The setting is genuinely new** — browser-scoped, multi-origin, CPU-only,
   interactive, with exactly one tab in front of a human. Chrome shipped it
   (Prompt API stable on the open web in Chrome 148 `[VERIFY]`), so this is
   description, not speculation.
2. **Sharing is solved; governance is not.** Measured on the shipped system:
   Chrome's context window is **9216 tokens, constant across 1-6 sessions** — a
   per-session cap that is never divided, so N sessions claim 9216N. And closing
   a tab reclaims **nothing** (survivors 1.04× vs 1.11× control, n=7 each).
3. **Governance needs three things**, and each is a chapter: a scheduling policy
   that can express attention without destroying fairness; a memory contract
   that says what is lost rather than who is refused; and a threat model for
   mutually distrusting tenants sharing one forward pass.
4. **Each is measured against real systems** — FIFO, equal-share
   (llama.cpp `--parallel` semantics), and Chrome's shipped Prompt API — not
   against a straw baseline.
5. **The honest boundary:** on foreground responsiveness MALABR and Chrome are
   **statistically indistinguishable** (relative score 0.092 ±0.009 vs 0.086
   ±0.012). The contribution is not "faster than Chrome"; it is *governed where
   Chrome is not*, and the thesis says so.

---

## 2. Chapter outline

**Ch. 1 — Introduction.** The browser as a multi-tenant inference host. The
three scarcities: compute, resident context, attention. Thesis statement and
contributions. ~8 pp.

**Ch. 2 — Background and related work.** Iteration-level scheduling (Orca),
paged KV (vLLM), chunked prefill (Sarathi-Serve); fairness (VTC, FairServe);
priority/SLO serving (ProServe, Kairos); KV eviction (StreamingLLM, H2O, SnapKV,
PyramidKV); in-browser inference (WebLLM) and Chrome's Prompt API; KV-cache
timing side channels (InputSnatch, PromptPeek, KVGov). Closes with the gap map
(G1-G8) and states plainly which mechanisms are *borrowed* rather than claimed.
~14 pp.

**Ch. 3 — MALABR: architecture.** The pipeline; one engine thread owning the
context and why (the `llama.h` thread-safety scope); session identity as
`(extension, tab, origin)` bound to an authenticated connection; wipe-on-acquire;
startup sizing; calibration. Positions the system as the *instrument* for the
later chapters. ~16 pp.

**Ch. 4 — Governing compute: attention-aware scheduling.** `[C1 — the spine]`
Latency-budget round composition; the problem with unconditional preference
(Jain 0.631 vs 0.997); policy/mechanism separation; borrowed virtual time
adapted from BVT; the fairness/responsiveness frontier. Answers C1-RQ1, RQ2,
RQ4. ~20 pp.

**Ch. 5 — Governing memory: a context contract.** Fixed pool, division, the
aggregate guard; anchor-preserving compaction; the mandatory/optional contract
from imprecise mixed-criticality; output fidelity under degradation; **what a
system chooses to lose** (Chrome loses the premise, MALABR loses the middle);
KV quantization as a measured, per-model, gated choice. Answers C1-RQ3 and
C3-RQ3. ~18 pp.

**Ch. 6 — Isolating tenants: from content to timing.** `[C2 — the distinctive
chapter]` Origin as a tenancy boundary; the canary method and its limits
(including that lossy KV produces recall false-negatives, so the method has a
validity precondition); the cross-origin timing channel, its capacity, its
dependence on policy, and whether partitioning narrows it; the same measurement
applied to Chrome. Answers C2-RQ1 to RQ4. ~20 pp.

**Ch. 7 — Evaluation.** `[C3 — threaded]` Workload model (homogeneous and
heterogeneous); scaling envelope and its honest limits; the whole-pipeline
metric set; lifecycle reclamation; threats to validity. ~16 pp.

**Ch. 8 — Conclusion and future work.** What transferred (BVT, IMC, DRF, CAT);
what did not and why; graded attention requiring browser changes; the GPU path;
perceptual grounding as the open question. ~6 pp.

---

## 3. Contributions list

Ordered by strength of evidence, which is deliberately not the order of novelty.

**Already measured — write these up first (week 1):**
1. **Degradation *content* as an evaluation axis.** Chrome evicts from the front
   and loses the premise (answers with filler: `'lazy'`, `'dog.'`); MALABR
   preserves the anchor and loses the middle, recalling the planted fact after
   dropping 58-64% of context at top-1 agreement 4/4. Reproducible, n=3
   byte-identical.
2. **Lifecycle reclamation of compute.** 453 → 0 wasted decode tokens;
   survivors finish 1.94× sooner. Chrome does **not** reclaim (1.04× vs 1.11×
   control, n=7 each).
3. **Pool division vs per-session capping.** Chrome's `contextWindow` is 9216
   tokens and constant for 1-6 sessions; MALABR divides a fixed pool with an
   aggregate guard that reclaims from the greediest.
4. **A methodological finding:** lossy KV (q8_0) produces canary **recall
   false-negatives** — a session failed to recall its own planted canary with
   KV provably intact — so canary-based isolation testing has a precondition
   that the literature does not state.

**To be produced:**
5. **Borrowed virtual time for inference scheduling** (C1): a repaid, tunable
   foreground preference replacing an unconditional one.
6. **A mandatory/optional context contract** (C1) adapted from imprecise
   mixed-criticality, with a guaranteed floor.
7. **Policy/mechanism separation** enabling foreground-first, fair-share,
   virtual-time and DRF policies to be compared on identical plumbing.
8. **The web origin as a tenancy boundary with a measurable timing channel**
   (C2), quantified against the classical 1 bps criterion and as a function of
   policy and partitioning.
9. **A heterogeneous browser-workload model** (C3), with evidence that
   homogeneous workloads misestimate governance effects.

---

## 4. Required experiments and figures

| # | figure / table | harness | status |
|---|---|---|---|
| F1 | pipeline metric table, 3 arms, 8 tabs | `tests/pipeline_bench.py` | **exists** |
| F2 | relative score distributions with spread | `tests/relative_repeat.py` | **exists** |
| F3 | fidelity: KL + top-1 vs % context dropped | `tests/fidelity_check.py` | **exists** |
| F4 | lifecycle reclamation, reclaim vs delivery-only | `tests/cancellation_bench.py` | **exists** |
| F5 | KV quantization cost per model | `tests/kv_quant_check.py` | **exists** |
| F6 | Chrome context window vs session count | `scripts/chrome_rq2_memory.py` | **exists** |
| F7 | Chrome overflow behaviour / anchor loss | `scripts/chrome_rq2_memory.py` | **exists** |
| F8 | Chrome cancellation non-reclamation | `scripts/chrome_rq4_cancel.py` | **exists** |
| F9 | cross-origin isolation, 3 cases + control | `scripts/chrome_rq1_isolation.py` | **exists** |
| F10 | **fairness/responsiveness frontier** (Jain vs fg TTFT, swept over warp) | needs **policy seam** + BVT policy | build |
| F11 | per-session service rates under each policy | extend `pipeline_bench.py` | small change |
| F12 | stalls before/after the mandatory floor | extend `pipeline_bench.py` | small change |
| F13 | ms-denominated vs token-denominated service at mixed depth | new, uses existing `CostCurve` | build |
| F14 | **channel capacity (bps) vs policy and KV mode** | new timing-channel harness | build |
| F15 | channel capacity in Chrome | new harness + existing `nano_cdp.py` | build |
| F16 | heterogeneous vs homogeneous workload comparison | new workload generator | build |
| F17 | scaling envelope: ratio vs tab count, both models | sweep `pipeline_bench.py` | sweep only |
| F18 | resident memory per session, Chrome | `scripts/chrome_rq2_rss.py` | **exists, never produced a result** |

Nine of eighteen figures come from harnesses that already run. Four require new
harnesses; two are sweeps; one (F18) exists but has never completed.

---

## 5. Week-by-week plan (16 weeks)

**Phase A — bank what exists (weeks 1-2)**
- W1: write Ch. 5's degradation-content section and Ch. 7's reclamation section
  from existing data (contributions 1-4). Re-run F1-F9 with **n ≥ 5 and
  warm-up discarded** so every number in the thesis meets its own standard.
  Complete F18 (the one missing measurement).
- W2: Ch. 3 (architecture) — it describes code that is already written and
  tested, so it is the cheapest substantial chapter.

**Phase B — the spine (weeks 3-7)**
- W3: **I8 policy seam** (1-2 days), then re-implement equal-share as a genuine
  policy rather than a flag flip. This repairs W9 and unblocks everything after.
- W4-5: BVT borrowed virtual time; sweep warp; produce F10, F11.
- W6: mandatory/optional floor; F12. Diagnose the background stalls either way.
- W7: cost-denominated service; F13. Write Ch. 4.

**Phase C — the distinctive chapter (weeks 8-11)**
- W8: timing-channel harness; establish the noise floor with a null sender
  (the control that makes the whole measurement credible).
- W9: capacity vs policy and vs KV mode; F14.
- W10: the same measurement on Chrome; F15.
- W11: write Ch. 6.

**Phase D — evaluation (weeks 12-14)**
- W12: heterogeneous workload generator; F16. Re-run the headline comparisons
  under it — expect the numbers to move, and report that they did.
- W13: scaling sweep; F17. State the envelope honestly.
- W14: write Ch. 7, including threats to validity.

**Phase E — assembly (weeks 15-16)**
- W15: Ch. 1, Ch. 2, Ch. 8. Scholar-verify every `[VERIFY]` citation.
- W16: full read-through, figure consistency, submission.

**Critical path:** W3 (the policy seam) gates Phase B and C2-RQ2. Do it first
and do not let it slip.

**Explicitly excluded, with reasons (D9, D10):**
- **I19 perceptual study** — ethics approval plus 20-30 days, and a negative
  result would undermine the premise. A2 is instead stated as a *scoped
  assumption* with external SLO numbers cited (Kairos: 8 s TTFT, 50 ms TPOT).
  Fallback if a guide insists: a small within-subjects preference test on
  *recorded traces*, needing no live system.
- **I6 graded attention** — needs C++ plus a Chromium rebuild (8-9 h), and the
  project's own notes record that freeing RAM for a build kills the working
  session. The server-side simulation (decay from time since the last
  `FOREGROUND` control message) delivers most of the research value with no
  rebuild; the C++ path stays in Ch. 8 as future work.
- **I4 DRF** — highest-effort scheduling idea (7-10 d) and may not change
  observable behaviour at 2 resources and 8 clients. Keep as the first
  extension if Phase B finishes early.
