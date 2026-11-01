# STEP 6 — Research questions for the top 3 candidates

Every comparison point below is a **real measured number** from this project
(cited in `01_PROJECT_UNDERSTANDING.md` §5), not a target invented for the
proposal. Hypotheses are stated with a direction and a magnitude so they can be
refuted.

Standing baselines, measured, 8 tabs, foreground arrives last:

| | FIFO | equal-share | MALABR (current) | Chrome Prompt API |
|---|---|---|---|---|
| foreground TTFT | 16.55 s | 4.22 s | 0.44 s | ~0.5-0.6 s |
| Jain fairness | 0.998 | 0.997 | **0.631** | 0.987-0.994 |
| stalls >1 s (fg/bg) | 0/0 | 0/0 | 0/**2** | 0/1-9 |
| relative score (fg/median) | 1.83 | 1.20 | 0.092 ±0.009 | 0.086 ±0.012 |

---

# C1 — Governed attention-aware scheduling  (I8 + I2 + I3)

## C1-RQ1 — Can foreground priority be *repaid* rather than permanent?
**Hypothesis.** There exists a warp value `w` at which long-run fairness rises
from Jain **0.631 to ≥ 0.90** while foreground TTFT stays **below 0.9 s**
(≤ 2× the current 0.44 s), at 8 tabs with the foreground arriving last.

**Design.**
- *Prereq:* I8's policy seam, so all arms run on identical plumbing (this is
  what repairs **W9**).
- *Arms:* (a) current unconditional foreground-first; (b) BVT-warp at ~8 values
  of `w`; (c) equal-share; (d) FIFO. Chrome is **not** an arm here — different
  model, so only its *relative* score is comparable (**W8**).
- *Workloads:* homogeneous 8-tab (current, for continuity) **and**
  heterogeneous (I15) — one long page summarisation plus five short chats.
- *Metrics:* Jain over per-session tok/s; foreground TTFT; TTFT p50/p95;
  inter-token gap p95 split fg/bg; stalls > 1 s; makespan; aggregate throughput.
- *Ablation:* warp applied at admission only vs carried across rounds.
- **Confirms** if some `w` gives Jain ≥ 0.90 and fg TTFT ≤ 0.9 s.
- **Refutes** if the frontier is monotone with no such point — i.e. every unit
  of fairness costs proportional foreground latency.

## C1-RQ2 — Is the fairness/responsiveness trade-off convex, and where is the knee?
**Hypothesis.** The frontier is convex with an identifiable knee: most of the
fairness recovery costs little foreground latency, because the foreground needs
priority only for its **first few rounds** (to win TTFT), not for the whole
generation.

**Design.** Sweep `w`; plot Jain against fg TTFT. Separately decompose the
foreground's advantage into *time-to-first-token* and *sustained rate* (the
pipeline harness already reports both — current split: fg 14.9 tok/s vs 3.6
background).
- **Confirms** if the knee exists and if ≥70% of the fairness recovery is
  available for ≤30% of the TTFT budget.
- **Refutes** if linear or concave — which would mean sustained preference, not
  just a fast start, is doing the work, and would make MALABR's current policy
  the right one after all. *That refutation is itself a thesis result.*

## C1-RQ3 — Does a mandatory context floor remove background starvation?
**Hypothesis.** With an IMC-style floor (anchor turn + most recent turn
guaranteed un-reclaimable), background stalls > 1 s fall from **2 to 0** at 8
tabs, while foreground TTFT degrades by **< 10%** and output fidelity is
unchanged (top-1 agreement stays 4/4).

**Design.** Arms: current compaction vs floor-guaranteed compaction. Metrics:
stalls fg/bg; compactions per session; fg TTFT; KL divergence vs full-context
reference (existing `fidelity_check.py`, noise floor ~0.0002 nats).
- **Confirms** if stalls → 0 within the stated budgets.
- **Refutes** if stalls persist — which would mean they are *not* a reclamation
  artifact, and the diagnosis would redirect W5 (likely toward thrashing, I14).

## C1-RQ4 — Is token-denominated service unfair at depth?
**Hypothesis.** Under mixed context depths, charging service in **tokens**
rather than **milliseconds** over-serves deep sessions by **> 20%** of machine
time relative to a cost-denominated policy.

**Design.** `CostCurve` already returns ms (`cost_ms_worst(pos)`), so this needs
no new cost model. Construct sessions at positions ~100 and ~6000; run
token-deficit vs ms-deficit arms; measure ms actually consumed per session.
- **Confirms** at > 20% discrepancy. **Refutes** below 5% — which on a 0.6B
  model is plausible and would mean the accounting-unit argument needs a larger
  model to matter (**W13**).

## Contributions (C1)
> This thesis proposes **borrowed virtual time as a scheduling policy for
> browser-local LLM inference**, in which the visible tab warps forward in
> virtual time and repays the borrowed service, and shows that this recovers
> long-run fairness (Jain 0.631 → ≥0.90) at a bounded cost in foreground
> latency — converting an unconditional preference into a tunable, repaid one.
>
> It further proposes a **mandatory/optional context contract** adapted from
> imprecise mixed-criticality scheduling, and shows that guaranteeing a minimum
> resident context per session eliminates background starvation that
> unconstrained reclamation produces.
>
> It contributes a **policy/mechanism separation** for an inference engine that
> allows foreground-first, fair-share, virtual-time and dominant-resource
> policies to be compared on identical plumbing.

---

# C2 — Timing channels in browser-shared inference  (I1 + I16 + I9)

## C2-RQ1 — Does a cross-origin timing channel exist, and what is its capacity?
**Hypothesis.** Two mutually distrusting origins sharing one local model can
communicate through the scheduler alone at a capacity **above 1 bps** (the
classical covert-channel evaluation criterion) in shared-KV mode.

**Design.** Sender origin modulates an observable (its context depth, or
whether it has a request in flight); receiver origin times its own TTFT and
decodes. Model as a binary asymmetric channel; report capacity in bits/sec per
the standard methodology. Vary background load.
- *Reference points from the literature:* an L2-cache channel in Amazon EC2 was
  reported at ~0.2 bps; cross-core hardware channels reach 7-200 kbit/s.
- **Confirms** at a reliable > 1 bps.
- **Refutes** if a measured **upper bound** sits below 1 bps — and this is the
  key asymmetry: *refutation is a publishable safety property*, so the RQ
  cannot produce a worthless outcome.

## C2-RQ2 — Is channel capacity a function of the scheduling policy?
**Hypothesis.** Capacity is strictly higher under contention-sensitive policies
(current foreground-first, BVT) than under a policy whose admission decision is
independent of other sessions' observable state, because the latter removes the
modulation path.

**Design.** Reuse I8's seam to measure capacity under each policy from C1. This
is the coupling that makes C1 and C2 one thesis rather than two.
- **Confirms** if capacity orders by policy.
- **Refutes** if capacity is policy-invariant — which would locate the channel
  in the **shared forward pass** rather than the scheduler, a sharper and
  arguably more interesting finding, since it would apply to every batched
  server including Chrome's.

## C2-RQ3 — Does KV partitioning narrow the channel?
**Hypothesis.** Partitioned mode reduces capacity by **≥ 1 order of magnitude**
versus shared-KV mode.

**Design.** Same harness, both existing modes; the §12 suite already runs in
both, so no new mechanism is needed.
- **Confirms** at ≥ 10× reduction. **Refutes** if comparable — noting that
  Intel CAT's documented weakness (too few partitions) means an analogous
  negative result is entirely plausible and must not be assumed away.

## C2-RQ4 — Does Chrome's shipped Prompt API exhibit the same channel?
**Hypothesis.** Yes, at comparable or greater capacity, because Chrome does not
divide its pool — `contextWindow` is **9216 tokens, constant across 1-6
concurrent sessions** (measured).

**Design.** Existing CDP rig; two genuinely different origins
(`http://localhost:P` vs `http://127.0.0.1:P`, both secure contexts, already
used in the RQ1 isolation test); same sender/receiver protocol.
- **Confirms** if measurable in Chrome. **Refutes** if Chrome's renderer-level
  throttling or scheduling masks it.

## Contributions (C2)
> This thesis identifies the **web origin as a tenancy boundary for on-device
> model inference**, shows that a shared local model mediated by a browser
> constitutes a cross-origin timing channel, and quantifies its capacity as a
> function of scheduling policy and KV partitioning.
>
> It further shows that **content-based isolation testing is insufficient**:
> canary recall establishes only that context does not leak directly, while the
> realistic channel is timing — and demonstrates this on both MALABR and
> Chrome's shipped Prompt API.

---

# C3 — Making the existing claims defensible  (I15 + I20 + I18)

## C3-RQ1 — Do governance effects differ under heterogeneous load?
**Hypothesis.** Under heterogeneous load (one long page summarisation plus five
short chats) the foreground-TTFT gap between MALABR and equal-share **widens by
> 1.5×** relative to homogeneous load, because governance bites hardest when
sessions differ in cost.

**Design.** New workload generator: prompt sizes drawn to mimic a tab
population (one ~4000-token page context, several ~20-token chats), mixed
arrival times, mixed output caps. Same four arms. Report every pipeline metric.
- **Confirms** at > 1.5× widening. **Refutes** if the gap narrows — which would
  mean the current homogeneous evaluation *overstates* MALABR's benefit, and
  must be reported as such.

## C3-RQ2 — How do the conclusions scale?
**Hypothesis.** The foreground advantage grows **sublinearly** with tab count
and shrinks with per-token cost, so results at 8 tabs on a 0.6B CPU model do
not extrapolate without restatement.

**Design.** Sweep tab count 1, 2, 4, 8, 16 across both available models
(Qwen3-0.6B, gemma-3-1b). Existing hint: the FIFO relative-score ratio moved
18.4× → 31.9× going from 4 to 8 tabs.
- **Confirms** if monotone and sublinear. **Refutes** if flat (advantage is
  contention-independent) or superlinear.
- *Honest envelope:* this machine is 14 GB / 8 cores / CPU-only, so the study
  states its envelope rather than claiming generality (**W13**).

## C3-RQ3 — Is *what* a system loses under pressure a discriminating metric?
**Hypothesis.** Systems differ **categorically**, not just in degree: Chrome
evicts from the front and loses the premise, MALABR preserves the anchor and
loses the middle. Measurable as anchor-recall rate after forced overflow.

**Design.** Mostly already done and reproducible (n=3, byte-identical):
Chrome fires `contextoverflow`, drops 9216 → 8208 tokens, and answers with
filler (`'lazy'`, `'dog.'`) instead of the planted anchor; MALABR recalls the
anchor after dropping 58-64% of context with top-1 agreement 4/4. Extend across
models and to a third system if one is obtainable.
- **Confirms** if the categorical split holds across models.
- **Refutes** if MALABR also loses the anchor at higher pressure — a real
  possibility worth testing rather than assuming.

## Contributions (C3)
> This thesis contributes a **heterogeneous browser-workload model** for
> evaluating shared local inference, and shows that homogeneous workloads — the
> norm in serving evaluations — systematically misestimate the effect of
> governance.
>
> It proposes **degradation content** ("what is lost") as an evaluation axis
> alongside degradation magnitude, and shows that systems with similar
> aggregate fidelity make categorically different choices about which part of a
> conversation to discard.

---

# JOINT-RQ — added after Decision 1 was answered "both"

Chaitanya selected **both** the systems and the security direction. The RQ that
makes them one thesis rather than two is specified in
**`08_BOTH_DIRECTIONS.md` §3**: *is there a responsiveness / fairness / leakage
trade-off surface, and what shape is it?*

It sweeps warp `w` across the policy family behind I8's seam and measures
foreground TTFT, Jain fairness **and** channel capacity at each point, in both
KV modes. It is listed there rather than duplicated here because it depends on
C1 and C2 both existing.

# Threats to validity

Applies across all three candidates.

**Measurement hazards already encountered in this project — not hypothetical.**
- **n=1 conclusions have been wrong twice.** A single run suggested renderer
  throttling explained Chrome's foreground advantage (0.12 → 0.38); repeats gave
  0.13 and 0.12. A single run also put Chrome's relative score at 0.10 from only
  two reporting tabs. **Every number in this thesis needs n ≥ 5 with the spread
  reported.**
- **Warm-up dominates early runs.** MALABR's first run gave 0.457 against
  0.075-0.107 after; Chrome's first two gave 0.76 and 0.43 against 0.11-0.15
  after. Warm-up must be discarded explicitly and the discard justified.
- **Lossy KV invalidates canary testing.** Under q8_0 a session failed to
  recall **its own** planted canary while its KV positions were provably intact,
  so an isolation test can pass for the wrong reason. Any isolation evidence
  must be generated at f16.
- **Harness asymmetry is easy to introduce.** MALABR's TTFT originally excluded
  session creation while Chrome's included it — a comparison wrong in MALABR's
  favour until corrected. Intervals must be defined identically per system.

**Construct validity.**
- **A2** (visibility ≈ attention) is assumed, not shown (**W10**). C1's whole
  premise rests on it. I19 would test it but is high-risk; the honest
  alternative is to state A2 as a scoped assumption.
- Jain fairness over token rate treats all tokens as equally valuable. A
  background tab's tokens may genuinely be worth less — which is the thesis's
  claim — so using Jain as the fairness measure slightly *understates* MALABR.
  State this rather than exploit it.

**Internal validity.**
- Before I8, the equal-share arm is MALABR with a flag flipped, not an
  independent VTC implementation (**W9**). Until the seam exists, differences
  cannot be cleanly attributed to policy.
- Background stalls (W5) are currently unexplained. If they are a thrashing
  artifact, C1-RQ3's floor may fix a symptom rather than the cause.

**External validity.**
- One machine, CPU-only, two small models, tab counts ≤ 16 (**W13**). No GPU
  path, which is how Chrome actually runs Nano.
- The Chrome comparison is uncontrolled (**W8**): different model, runtime and
  harness. Only dimensionless ratios transfer, and on the headline ratio the two
  systems are **statistically indistinguishable** (0.092 ±0.009 vs 0.086
  ±0.012). No claim of beating Chrome on scheduling is supportable.
- Chrome 153's behaviour may change; the measurement should record the exact
  build (`153.0.8010.52`) and the model component version.

**Conclusion validity.**
- C2's channel measurement has a known confound: CPU contention from any source
  looks like signal. The control is a sender that modulates *nothing* while the
  receiver still measures.
- Composite scores in STEP 5 are heuristic (D7) and must not appear in the
  thesis as if they were measurements.
