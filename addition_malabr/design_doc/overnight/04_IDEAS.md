# STEP 4 — Ideas

20 ideas. **10 are cross-domain**, drawn from the STEP 3 shortlist
(T11, T1, T4, T8, T7) and the wider transfer set. IDs are stable; STEP 5 and
STEP 6 reference them.

Format per idea: origin tag, hypothesis, intuition, weakness addressed, closest
prior work (from STEP 2), and what exactly would be new.

---

## A. Novelty — new policy, accounting unit, threat model, or formulation

### I1 — Cross-origin timing channel through a shared browser LLM
`[cross-domain: security / covert channels]` — from **T11**
- **Hypothesis:** two mutually distrusting web origins sharing one local model
  can establish a covert timing channel through the scheduler alone, with
  measurable capacity above the classical 1 bps criterion, and the capacity is
  a function of the scheduling policy.
- **Intuition:** MALABR's admission decisions depend on *other sessions'*
  state (cost curve, aggregate pressure, who is foreground). Anything whose
  latency depends on a neighbour's behaviour is a channel. The sender modulates
  its context depth or request rate; the receiver times its own TTFT.
- **Addresses:** W12, and re-opens RQ1 honestly.
- **Closest prior work:** InputSnatch (arXiv 2411.18191), PromptPeek (NDSS
  '25), KVGov (arXiv 2608.09225 `[VERIFY]`) — all **cloud** multi-tenant;
  USENIX Sec '25 hardware-cache channels in **local** inference; Loophole
  (arXiv 1702.06764) for browser event-loop timing.
- **What would be new:** the **browser-origin** threat model for a
  browser-provided shared model, and capacity reported for the
  **scheduler-contention** channel rather than the prefix-cache channel.
  **NOT a first:** KVGov (arXiv 2608.09225) already ties scheduling to leakage
  and presents a leakage-vs-efficiency frontier for cloud serving — but for the
  prefix cache, which MALABR structurally lacks (verified: no cross-session
  prefix reuse, wipe-on-acquire proved in `slots.py`). The novelty is the
  channel, the tenancy unit (web origin) and the policy axis (attention, not
  adversary utility).

### I2 — Borrowed foreground priority
`[cross-domain: OS scheduling]` — from **T1 (BVT warp)**
- **Hypothesis:** letting the foreground session *warp* in virtual time and
  repay the borrowed service restores long-run fairness (Jain -> ~0.9) while
  preserving most of the foreground TTFT advantage (<2x degradation from 0.44 s).
- **Intuition:** the foreground does not need to win *forever*; it needs to win
  *now*. The current policy gives it unconditional, unrepaid priority, which is
  why fairness collapses to 0.631 permanently.
- **Addresses:** W1 (no knob), W5 (background starvation), and the fairness
  objection a VTC-literate reviewer will raise.
- **Closest prior work:** BVT (SOSP '99) for the mechanism; VTC (arXiv
  2401.00588) already brought *virtual time* to LLM serving; FairServe (arXiv
  2411.15997) added static weights.
- **What would be new:** the **warp/borrowing** half, which VTC and FairServe
  do not have, driven by a **live UI signal** rather than a configured weight —
  and a tunable (warp value) where MALABR currently has none.

### I3 — Mandatory/optional context, with a guaranteed floor
`[cross-domain: real-time systems]` — from **T4 (imprecise mixed-criticality)**
- **Hypothesis:** declaring a per-session *mandatory* context (anchor + most
  recent turn) that the aggregate guard may never reclaim, with the middle as
  *optional*, eliminates the unexplained background stalls while costing
  <10% of the foreground's advantage.
- **Intuition:** MALABR already keeps `turn_boundaries[1:-1]` droppable and
  turn 0 exempt. That is IMC's mandatory/optional split without the name or the
  guarantee.
- **Addresses:** W5, W11; gives §8 a schedulability argument.
- **Closest prior work:** IMC / EDF-VD with degraded quality (arXiv 1605.01302),
  RTSS '18 graceful degradation `[VERIFY]`; StreamingLLM for the
  sink+recent-window mechanism at token level.
- **What would be new:** the *guarantee* and its proof obligation at
  conversation granularity across tenants, rather than a heuristic inside one
  sequence.

### I4 — Dominant-resource fairness over (token rate, resident context)
`[cross-domain: cloud scheduling]` — from **T8 (DRF)**
- **Hypothesis:** governing both resources under one dominant-share rule
  removes the pathological states the two independent governors can reach
  (compute-starved but memory-rich, and vice versa) and makes the allocation
  strategy-proof against an origin misreporting demand.
- **Intuition:** `build_batch` governs compute; the aggregate guard governs
  memory; they never consult each other.
- **Addresses:** W1, W3.
- **Closest prior work:** DRF (NSDI '11). I found no DRF-for-LLM-serving work,
  but **did not run that specific query** — must check.
- **What would be new:** a two-resource fairness formulation for LLM serving,
  and strategy-proofness as an explicit property in a *mutually distrusting
  origin* setting.

### I5 — Per-turn compute time as the unit of governance
`[in-field]`
- **Hypothesis:** capping a turn by *engine time consumed* rather than *tokens
  produced* equalises real machine cost across sessions at different context
  depths, reducing the variance in per-session service that token-counting
  hides.
- **Intuition:** `ABSOLUTE_CEILING = 512` tokens costs very different amounts
  at position 100 vs 8000. `CostCurve` already predicts that difference and the
  cap ignores it.
- **Addresses:** W3.
- **Closest prior work:** VTC counts tokens; DRR (T10) carries deficits in
  bytes. The project's own notes identify per-turn compute as the open
  accounting unit.
- **What would be new:** compute-time as a first-class quota in an LLM server,
  with the measurement to show token-counting is unfair at depth.

### I6 — Graded attention instead of a binary foreground
`[cross-domain: OS / UI systems]`
- **Hypothesis:** a decaying attention weight (time since last interaction,
  is-typing, audible, pinned) produces better *aggregate* perceived
  responsiveness than a single binary foreground flag, because the recently-left
  tab a user is about to return to is currently treated identically to one
  abandoned an hour ago.
- **Intuition:** the browser knows far more about attention than one tab id.
- **Addresses:** W2.
- **Closest prior work:** Page Visibility API (platform feature); ITO (arXiv
  2608.09107 `[VERIFY]`) uses a model to organise tabs — the inverse direction.
- **What would be new:** attention as a *continuous scheduling weight* derived
  from browser state, which is the signal no serving system can obtain.

---

## B. Design — architecture, modularity, generalisation, interpretability

### I7 — Session classes with a declarative budget policy
`[cross-domain: computer architecture]` — from **T6 (Intel CAT / CLOS)**
- **Hypothesis:** expressing governance as *classes of service* with declared
  shares makes the policy auditable and swappable, and reproduces today's
  behaviour as one configuration among many.
- **Intuition:** CAT's CLOS + capacity bitmasks is exactly the abstraction
  MALABR lacks: today the policy is spread across `build_batch`, the aggregate
  guard and two constants.
- **Addresses:** W1, W6.
- **Closest prior work:** Intel CAT / vCAT (RTAS '17).
- **What would be new:** a software CLOS for KV pools with *arbitrary and
  dynamic* partitions — CAT's documented weakness is too few hardware
  partitions.

### I8 — Separate policy from mechanism behind one admission interface
`[in-field]`
- **Hypothesis:** a single `Policy` interface (given session states, return
  admissions and budgets) lets BVT, DRF, VTC and today's foreground-first be
  compared *on the same engine*, which is the only way to attribute an effect
  to a policy rather than to an implementation.
- **Intuition:** STEP 1's W9 — the "equal-share" baseline is MALABR with a flag
  flipped, which is weak evidence against the literature's real systems. A
  policy interface turns that weakness into a strength: every baseline becomes
  a genuine implementation on identical plumbing.
- **Addresses:** W9, and de-risks I2/I4/I11/I12 by giving them somewhere to live.
- **Closest prior work:** standard OS scheduler-class design (Linux
  `sched_class`).
- **What would be new:** nothing conceptually — this is engineering that makes
  the *evaluation* credible. Listed because it is the highest-leverage
  refactor.

### I9 — Isolation posture as an exposed choice
`[cross-domain: security + architecture]` — from **T7 (CATalyst)**
- **Hypothesis:** MALABR's existing *partitioned* KV mode measurably narrows
  the I1 channel relative to shared-KV mode, making "isolated vs shared" a
  security/efficiency dial rather than an internal flag.
- **Intuition:** CATalyst repurposed cache partitioning as a side-channel
  defence. MALABR already ships both modes and the test suite runs in both.
- **Addresses:** W12.
- **Closest prior work:** CATalyst (HPCA '16).
- **What would be new:** the measurement, and a browser-facing security
  posture for on-device inference. Note CAT's own limitation (too few
  partitions) means the result must be measured, not assumed.

### I10 — Predictive admission from prompt size
`[cross-domain: databases]` — from **T14**
- **Hypothesis:** sizing a session at submit time from its prompt length and
  declared intent avoids most reactive compaction, reducing mid-generation
  compactions by >50%.
- **Intuition:** a page summarisation is visibly larger than a one-line chat
  *before* prefill. MALABR only reacts at 95%.
- **Addresses:** W4.
- **Closest prior work:** DB query governors / cost-based admission `[VERIFY]`.
- **What would be new:** context admission control for conversational sessions.

---

## C. Performance — latency, fairness, memory, robustness

### I11 — Deficit round robin in cost units
`[cross-domain: networking]` — from **T10**
- **Hypothesis:** carrying a per-session deficit in *predicted milliseconds*
  rather than tokens makes service fair in machine terms and removes the need
  for the ad-hoc aging pass.
- **Addresses:** W3, W5.
- **Closest prior work:** DRR/WFQ; VTC's token counters.
- **What would be new:** cost-denominated (not token-denominated) fair queueing
  for LLM decode.

### I12 — Delay-targeted admission instead of a fixed round budget
`[cross-domain: networking]` — from **T9 (CoDel)**
- **Hypothesis:** controlling *observed* per-session sojourn time against a
  target beats a fixed 50 ms round budget, and removes two unjustified
  constants.
- **Addresses:** W6, W5.
- **Closest prior work:** CoDel `[VERIFY]`; Kairos's 50 ms TPOT SLO gives an
  external target to aim at.
- **What would be new:** AQM-style parameter-light control applied to decode
  admission.

### I13 — Per-model fidelity gating for KV quantization
`[in-field]`
- **Hypothesis:** q8_0 can be adopted automatically where its measured
  divergence is below a threshold and refused where it is not, recovering ~1.9x
  resident context on models that tolerate it.
- **Intuition:** already measured: q8_0 costs KL 0.0040 on Qwen but 0.0343 on
  gemma-3 — a 10x model-dependent spread — and it produces canary recall
  false-negatives, which attacks the test methodology itself.
- **Addresses:** W13 partially; turns a known hazard into a safe mechanism.
- **Closest prior work:** *More Tokens, Lower Precision* (arXiv 2412.12706) on
  the token-count/precision trade-off.
- **What would be new:** an automatic, measurement-gated adoption rule, plus
  the finding that lossy KV **invalidates canary-based isolation testing** —
  which is a methodological contribution to anyone using canaries.

### I14 — Working-set admission to prevent context thrashing
`[cross-domain: OS virtual memory]` — from **T17**
- **Hypothesis:** the background stalls (W5) are a thrashing signature:
  admitting more sessions than the pool can hold working sets for causes
  repeated compact-refill cycles. Admission on working-set fit removes them.
- **Addresses:** W5, W4.
- **Closest prior work:** Denning's working-set model `[VERIFY]`.
- **What would be new:** working-set reasoning for conversational context.

---

## D. Evaluation — benchmarks, stress tests, ablations, failure analysis

### I15 — A heterogeneous browser-workload benchmark
`[in-field]`
- **Hypothesis:** governance effects are larger and qualitatively different
  under heterogeneous load (one long summarisation beside several short chats)
  than under the homogeneous load currently tested, where every session is
  equally valuable and equally expensive.
- **Addresses:** W7, W9.
- **Closest prior work:** serving papers use public traces of anonymous
  clients; none models a browser tab population.
- **What would be new:** the workload model itself — plausibly a reusable
  artifact beyond this thesis.

### I16 — Timing-channel harness with a partitioned-vs-shared ablation
`[in-field]` — operationalises I1 and I9
- **Hypothesis:** channel capacity is measurable, repeatable, and lower in
  partitioned mode.
- **Addresses:** W12.
- **What would be new:** the harness, and — as far as search establishes —
  the first capacity numbers for the scheduler-contention channel in a
  *browser-shared* model, with Chrome measurable as a second subject via the
  existing CDP rig. Stated as "as far as search establishes" deliberately: an
  absence cannot be proved, and KVGov covers the adjacent prefix-cache channel.

### I17 — Adversarial-origin suite
`[in-field]`
- **Hypothesis:** a deliberately hostile origin can degrade others'
  service (resource exhaustion), or influence another session's output via
  injected page context, in ways the current canary suite does not detect.
- **Addresses:** W12. Note the prior session's greedy-neighbour experiment was
  **inconclusive (n=1, the tab's renderer died)** — this idea includes fixing
  that measurement.
- **What would be new:** an adversarial evaluation for browser-local inference.

### I18 — "What does it lose?" as a cross-system metric
`[in-field]`
- **Hypothesis:** *what* a system discards under pressure is a comparable,
  policy-revealing metric, and systems differ sharply on it.
- **Intuition:** already measured — Chrome evicts from the front and loses the
  premise (answered `'lazy'`/`'dog.'` instead of the planted anchor); MALABR
  keeps the anchor and loses the middle.
- **Addresses:** W11; realises gap G5.
- **What would be new:** degradation *content* as an evaluation axis, where the
  literature reports only degradation *magnitude*.

### I19 — Perceptual grounding for the latency constants
`[in-field]`
- **Hypothesis:** users prefer MALABR's profile (0.44 s foreground, occasional
  background stalls) over equal-share (4.2 s everywhere) — and if they do not,
  the thesis's central premise needs restating.
- **Addresses:** W10, W6, assumptions A1/A2/A6.
- **Closest prior work:** Kairos's asserted 8 s TTFT / 50 ms TPOT SLOs; no
  perceptual study found for browser-local inference.
- **What would be new:** evidence for an assumption the whole field asserts.
  **Highest-risk idea here** — needs ethics approval and participants, and a
  negative result would undermine the thesis rather than enrich it.

### I20 — Scaling study
`[in-field]`
- **Hypothesis:** the foreground advantage grows with contention and shrinks
  with per-token cost, so conclusions drawn at 8 tabs on a 0.6B CPU model do
  not transfer unexamined.
- **Intuition:** already visible — the FIFO ratio moved 18.4x -> 31.9x going
  from 4 to 8 tabs.
- **Addresses:** W13.
- **What would be new:** the sensitivity analysis every reviewer will ask for.

---

## Coverage check

| bucket | ideas |
|---|---|
| Novelty | I1, I2, I3, I4, I5, I6 |
| Design | I7, I8, I9, I10 |
| Performance | I11, I12, I13, I14 |
| Evaluation | I15, I16, I17, I18, I19, I20 |

Cross-domain: I1, I2, I3, I4, I6, I7, I9, I10, I11, I12, I14 (11 of 20).
From the STEP 3 shortlist specifically: I1 (T11), I2 (T1), I3 (T4), I4 (T8),
I9 + I16 (T7).
