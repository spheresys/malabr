# STEP 3 — Cross-domain idea mining

**Verification status.** Sources below were found by web search this session;
I read abstracts and search summaries, not full texts. `[VERIFY]` marks items
to confirm on Scholar. **Prior-transfer checks are labelled by what I actually
searched** — "not checked" where I did not run the specific query, rather than
claiming an absence I did not test. That distinction matters: an unverified
"not found" is how a novelty claim dies in a viva.

---

## 1. Field-neutral formulations of this project's core

Five framings, each opening a different literature.

- **F1 — Proportional-share scheduling of a non-preemptible shared resource
  under a live, externally-supplied priority signal.** The resource is one
  model context; the signal is which tab a human is looking at; non-preemptible
  because sequences are fused into one batched kernel call and a round cannot
  be interrupted mid-flight.
- **F2 — Allocating a fixed, indivisible working-set budget among mutually
  distrusting tenants, with per-tenant replacement inside each share.** The KV
  pool is allocated once and cannot grow.
- **F3 — Graceful degradation under resource exhaustion: choosing *what to
  lose* rather than *whom to reject*.** Compaction instead of admission
  failure.
- **F4 — Interference and information leakage between co-tenants of a shared
  accelerator, observable through timing.** The scheduler's decisions are
  visible as latency to every origin holding a session.
- **F5 — Multi-resource fair allocation where tenants have heterogeneous
  demand across two coupled resources** (compute rate and resident context).

---

## 2. Transfer records

### T1 — Borrowed Virtual Time (BVT), the `warp` mechanism
- **Field:** OS / CPU scheduling. Duda & Cheriton, SOSP '99.
  [PDF](https://rcs.uwaterloo.ca/papers/bvt.pdf)
- **What it does there:** weighted proportional-share scheduling in virtual
  time; a latency-sensitive thread may **"warp back" in virtual time** to gain
  dispatch preference, and it *borrows that time from its own future
  allocation*, so long-run weighted sharing is preserved.
- **Analogy:** warp = the foreground tab. Virtual time = cumulative tokens
  served. A foreground session warps to the front of the round, then repays.
- **Adaptation needed:** MALABR's foreground preference is *unconditional and
  never repaid*, which is exactly why Jain collapses to 0.631 permanently.
  BVT's borrowing makes the advantage **transient**: fast first token, then
  convergence back toward fair share. Needs a virtual-time counter per session
  and a warp value; the non-preemptible round means warp is applied at
  admission, not mid-round.
- **Prior transfer:** **Partially.** Virtual time *has* reached LLM serving —
  VTC (arXiv [2401.00588](https://arxiv.org/pdf/2401.00588)) is literally a
  virtual token counter, and FairServe adds weights. **What I did not find is
  the warp/borrowing mechanism** applied to inference scheduling. Searched:
  virtual-time/fair-share LLM serving. **Not searched:** "borrowed virtual
  time" + LLM. **Check this one first.**
- **Addresses:** W1 (no knob), and the fairness collapse directly.

### T2 — Lottery and stride scheduling
- **Field:** OS. Waldspurger & Weihl, OSDI '94 / MIT TR. `[VERIFY]`
- **What it does there:** resource rights as **tickets**; a client's share is
  its ticket fraction. Tickets can be transferred between clients, and
  *currencies* let groups fund each other.
- **Analogy:** each origin gets tickets; the browser grants bonus tickets to
  whichever tab is visible and revokes them on blur. Ticket *transfer* is the
  interesting part — a page could cede its share to another it trusts.
- **Adaptation:** ticket inflation must be bounded or an origin games it; the
  browser, not the page, must be the only minter.
- **Prior transfer:** Not checked specifically.
- **Addresses:** W1, W2.

### T3 — Linux CFS `vruntime`
- **Field:** OS. Production scheduler, not a paper.
- **What it does there:** always run the task with least virtual runtime,
  scaled by weight (nice value). Self-correcting, O(log n), no explicit
  priorities.
- **Analogy:** replace `build_batch`'s hand-rolled aging pass
  (`MAX_CONSECUTIVE_EXCLUSIONS = 5`, commented "no measured value yet") with a
  vruntime ordering. Aging becomes a *consequence* of the policy rather than a
  patch on top of it.
- **Adaptation:** vruntime must be charged in *cost*, not token count, to
  respect that a token at position 8000 costs more than at 100 — which is
  exactly what `CostCurve` already predicts.
- **Prior transfer:** Partially — VTC is the same family.
- **Addresses:** W5 (unexplained background stalls), W1.

### T4 — Imprecise Mixed-Criticality (IMC): mandatory vs optional components
- **Field:** Real-time systems. See *EDF-VD with Degraded Quality Guarantees*,
  arXiv [1605.01302](https://arxiv.org/pdf/1605.01302); *Uniprocessor MC
  Scheduling with Graceful Degradation*, RTSS '18
  [PDF](https://userweb.cs.txstate.edu/~k_y47/webpage/pubs/rtss18a.pdf)
  `[VERIFY]`.
- **What it does there:** rather than discarding low-criticality tasks on a
  mode switch (classic Vestal), split each into a **mandatory** part that is
  always guaranteed and an **optional** part that runs only with slack. Gives
  *provable* degraded service instead of starvation.
- **Analogy:** a session's context has a mandatory part (anchor turn + most
  recent turn — enough to stay coherent) and an optional part (the middle).
  **MALABR already does this**, informally and without a name: compaction's
  droppable range is `turn_boundaries[1:-1]`.
- **Adaptation:** give it the formal structure and the guarantee. A background
  session under aggregate pressure keeps a *reserved minimum* budget rather
  than being compacted arbitrarily — which would also bound the 2 background
  stalls the pipeline benchmark currently shows unexplained.
- **Prior transfer:** Not checked specifically for IMC + LLM. The KV-eviction
  literature has the *mechanism* (StreamingLLM's sinks + recent window) but not
  the *schedulability framing*.
- **Addresses:** W5, W11, and gives §8 a theory.

### T5 — EDF-VD mode switch
- **Field:** Real-time. Same literature as T4.
- **What it does there:** a system-wide switch to a degraded mode when
  execution overruns pessimistic estimates.
- **Analogy:** MALABR's aggregate guard *is* a mode switch, triggered at 95% of
  pool. Currently it has no declared degraded-mode contract.
- **Adaptation:** state the contract: in degraded mode each session is
  guaranteed its mandatory share, optional context is reclaimed
  greediest-first.
- **Prior transfer:** Not checked.
- **Addresses:** W4 (reactive, not predictive).

### T6 — Intel CAT / Class-of-Service way-partitioning
- **Field:** Computer architecture / cloud QoS.
  [vCAT, RTAS '17](https://www.cis.upenn.edu/~linhphan/papers/rtas17-vcat.pdf);
  [Lynx CAT demo](https://www.lynx.com/embedded-systems-blog/multi-core-cache-allocation-technology-cat-demo)
- **What it does there:** partitions the last-level cache by *ways* into
  Classes of Service (CLOS) via capacity bitmasks, to stop the "noisy
  neighbour" problem. Partitions may be isolated or deliberately overlapped.
- **Analogy:** CLOS = session priority class; ways = KV pool blocks. MALABR's
  shared-KV mode is the *overlapping* configuration; partitioned mode is the
  *isolated* one. CAT's framing names what MALABR built.
- **Adaptation:** CAT is hardware with a fixed small number of partitions;
  MALABR's are software and can be arbitrary and dynamic — a genuine advantage
  to claim, since CAT's documented limitation is too few partitions.
- **Prior transfer:** Not checked for CAT-style CLOS + KV cache.
- **Addresses:** W1, and gives G3 a vocabulary.

### T7 — CATalyst: cache partitioning as a side-channel *defence*
- **Field:** Security + architecture. HPCA '16
  [PDF](http://class.ece.iastate.edu/tyagi/cpre581/papers/HPCA16Catalyst.pdf)
- **What it does there:** repurposes CAT partitioning to defeat last-level
  cache side-channel attacks in the cloud, by making a victim's lines
  un-evictable by the attacker.
- **Analogy:** MALABR's *partitioned* KV mode may already be a timing-channel
  defence that shared-KV mode is not. The project has both modes and a test
  suite that runs in both — so the experiment is nearly free.
- **Adaptation:** needs a channel to measure first (T11).
- **Prior transfer:** Not found for KV caches in a browser; note that CAT's
  own limitation (too few partitions) is reported to make it insufficient
  against cross-VM LLC attacks, so the analogous MALABR claim must be measured,
  not assumed.
- **Addresses:** W12, and links G3 (a strength) to G1 (the novelty).

### T8 — Dominant Resource Fairness (DRF)
- **Field:** Cloud scheduling. Ghodsi et al., NSDI '11
  [PDF](https://www.usenix.org/legacy/events/nsdi11/tech/full_papers/Ghodsi.pdf)
- **What it does there:** generalises max-min fairness to multiple resource
  types by equalising each user's **dominant share** (their largest fractional
  claim on any one resource). Strategy-proof and envy-free.
- **Analogy:** MALABR governs **two** coupled resources — token service rate
  (compute) and resident context (memory) — with **two independent mechanisms**
  (`build_batch` and the aggregate guard) that can disagree. A session can be
  compute-starved while memory-rich. DRF unifies them under one fairness
  notion.
- **Adaptation:** needs per-session demand vectors; both components are already
  measured.
- **Why it is especially apt here:** **strategy-proofness** is not a nicety
  when tenants are mutually distrusting web origins — it means an origin cannot
  gain by lying about its demand. No current MALABR mechanism has that
  property.
- **Prior transfer:** Not checked for DRF + LLM serving.
- **Addresses:** W1, W3.

### T9 — CoDel / active queue management
- **Field:** Networking. Nichols & Jacobson. `[VERIFY]`
- **What it does there:** controls *standing queue delay* rather than queue
  length, by tracking the minimum sojourn time over a window and dropping when
  delay persists above target. Famously parameter-light.
- **Analogy:** MALABR's `ROUND_LATENCY_BUDGET_MS = 50` is a fixed target with
  no feedback on *observed* per-session delay. CoDel's insight — control the
  delay you measure, not the occupancy you can see — maps onto admission.
- **Adaptation:** "drop" is not available (you cannot drop a user's question);
  the analogue is compact-or-defer.
- **Prior transfer:** Not checked.
- **Addresses:** W6 (unjustified constants), W5.

### T10 — Deficit Round Robin / WFQ
- **Field:** Networking packet scheduling. `[VERIFY]`
- **What it does there:** O(1) approximate fair queueing; each flow has a
  quantum and a deficit counter carried across rounds, so variable-size packets
  still get fair bandwidth.
- **Analogy:** variable-size packets = variable per-token *cost* by context
  depth. A deficit counter per session would make `build_batch` fair in cost
  terms rather than in token counts.
- **Prior transfer:** Partially — VTC's token accounting is the same spirit.
- **Addresses:** W3.

### T11 — Covert-channel capacity measurement
- **Field:** Security. *Whispers in the Hyper-space*, USENIX Sec '12
  [PDF](https://www.usenix.org/system/files/conference/usenixsecurity12/sec12-final97.pdf);
  [covert channel survey](https://www.cs.mcgill.ca/~myassa2/ReadingGroup/covert-channels.pdf)
- **What it does there:** quantifies a channel as a **bit rate** (bits/sec)
  under noise, per Shannon; models it as a binary asymmetric channel and
  reports measured capacity. Reference points from the literature: an L2-cache
  channel in Amazon EC2 measured ~**0.2 bps**, against the classical
  evaluation-criteria threshold of **1 bps**; cross-core/cross-VM hardware
  channels reach 7-200 kbit/s.
- **Analogy:** two web origins each hold a MALABR session. One modulates its
  context depth or request pattern; the other measures its own TTFT. The
  scheduler is the channel. Capacity in bps is the metric.
- **Adaptation:** MALABR's advantage as a research subject is that it is
  *instrumented* — `CostCurve`, round composition and admission are all
  observable from inside, so the channel can be characterised rather than only
  attacked. Chrome can be measured as a second subject with the existing CDP
  rig.
- **Prior transfer:** **Not found** for browser-origin pairs over a shared
  local model. Searched: cross-origin timing side channel + browser built-in AI
  / local LLM. Found only cloud multi-tenant (InputSnatch, PromptPeek) and
  local *hardware* cache channels (USENIX Sec '25). **This is the strongest
  absence I verified.**
- **Addresses:** W12, and is the whole of G1.

### T12 — ARC / LIRS recency-and-frequency cache replacement
- **Field:** Storage systems. `[VERIFY]`
- **What it does there:** adaptive replacement balancing recency against
  frequency, self-tuning without parameters.
- **Analogy:** MALABR evicts by **position** (oldest exchanges first, anchor
  exempt). It never considers whether an exchange was *referred back to*.
- **Adaptation:** needs a reference signal for conversation turns; attention
  scores would supply it, which is what H2O/SnapKV do at token level.
- **Prior transfer:** Partially — the KV-eviction field covers this at token
  granularity, not conversation granularity.
- **Addresses:** W11.

### T13 — Database buffer-pool partitioning with borrowing
- **Field:** Databases. `[VERIFY]`
- **What it does there:** per-tenant buffer pools with soft limits, allowing a
  tenant to borrow unused pages and be clawed back under pressure.
- **Analogy:** MALABR's shared-KV mode is exactly this (soft cap `n_ctx/2`,
  aggregate guard claws back from the greediest). The DB literature supplies
  the vocabulary and the clawback-fairness results.
- **Prior transfer:** Not checked.
- **Addresses:** validates G3 rather than extending it.

### T14 — Query admission control / cost-based governors
- **Field:** Databases. `[VERIFY]`
- **What it does there:** estimates a query's cost *before* admitting it and
  refuses or queues it based on predicted resource use.
- **Analogy:** MALABR compacts reactively at 95%. A page summarisation is
  visibly going to need more context than a one-line chat, and that is knowable
  at submit time from the prompt length.
- **Prior transfer:** Not checked for LLM context admission.
- **Addresses:** W4.

### T15 — Anytime algorithms / imprecise computation
- **Field:** AI and real-time. (Same family as T4.)
- **What it does there:** iterative computations that can be stopped early and
  still return a usable, degraded answer.
- **Analogy:** generation *is* anytime — a truncated reply is partially useful.
  MALABR's `OutputCap` already truncates, but by a fixed token count, not by a
  value-of-continuing judgement.
- **Prior transfer:** Not checked.
- **Addresses:** W3.

### T16 — Constant-time design from cryptography
- **Field:** Security / crypto engineering.
- **What it does there:** eliminates secret-dependent timing by making control
  flow and memory access independent of secrets.
- **Analogy:** the *defence* half of G1. A scheduler whose admission decisions
  are independent of other sessions' observable state leaks nothing through
  timing — but that is precisely what MALABR's cost-aware,
  contention-sensitive scheduling is not.
- **Adaptation:** full constant-time is impossible (sharing is the point); the
  realistic goal is a measured, bounded channel, which is why T11 must come
  first.
- **Prior transfer:** Not checked in this setting.
- **Addresses:** W12.

### T17 — Working-set theory (Denning)
- **Field:** OS / virtual memory. `[VERIFY]`
- **What it does there:** a process's working set is the pages referenced in a
  recent window; scheduling admits a process only if its working set fits,
  which prevents thrashing.
- **Analogy:** a session's "working set" is the context it actually attends to.
  Admitting more sessions than the pool can hold working sets for is the
  thrashing analogue — and MALABR's background stalls (W5) may be exactly that.
- **Prior transfer:** Not checked.
- **Addresses:** W4, W5.

### T18 — Hierarchical Token Bucket with borrowing
- **Field:** Networking (Linux `tc htb`).
- **What it does there:** class hierarchy where a child may borrow unused rate
  from its parent up to a ceiling.
- **Analogy:** origin -> tab -> session hierarchy, with a visible tab borrowing
  from the browser's spare capacity up to a ceiling. Gives a *structured* home
  for the one knob W1 wants.
- **Prior transfer:** Not checked.
- **Addresses:** W1, W2.

---

## 3. Shortlist: the five most promising transfers

Ranked by (novelty x fit), with the reason each survives scrutiny.

1. **T11 — covert-channel capacity measurement (security -> browser LLM).**
   The only absence I actually verified with a targeted search. Changes the
   project's threat model rather than its performance numbers, which is a
   stronger kind of contribution. Transfer distance is large and the
   methodology (bits/sec vs a 1 bps criterion) is mature and citable. Also the
   honest fix for an overstated current result (RQ1 "passes").
2. **T1 — BVT's warp (OS -> LLM scheduling).** Fixes the single biggest
   structural weakness (W1: unconditional, unrepaid foreground priority; Jain
   0.631 with no knob) with a mechanism that is 25 years mature and gives
   exactly the transient-advantage-with-preserved-long-run-fairness property
   the thesis wants. Must be distinguished carefully from VTC, which already
   brought *virtual time* across; the warp is the un-transferred part.
3. **T4 — IMC mandatory/optional (real-time -> context governance).** Gives
   §8's existing behaviour a formal name, a guarantee, and a schedulability
   argument, and would bound the background stalls currently unexplained. Low
   implementation cost because the mechanism already exists; the contribution
   is the framing plus the guarantee.
4. **T8 — DRF (cloud -> two coupled resources).** The only candidate that
   unifies MALABR's two independent governors, and the only one that brings
   **strategy-proofness** — a property that matters specifically because the
   tenants are mutually distrusting origins.
5. **T7 — CATalyst-style partitioning as defence (architecture/security).**
   Pairs with T11: MALABR already ships both partitioned and shared-KV modes
   and tests both, so "does partitioning close the channel?" is a nearly free
   experiment once the channel is measurable.

**Deliberately not shortlisted.** T12 (ARC/LIRS) and T15 (anytime) are good
ideas whose home field (KV eviction) is crowded and moving fast — MALABR would
be entering a race it is not equipped to win on a 0.6B CPU model. T13/T14
(databases) mostly *validate* existing design rather than extend it; they
belong in the related-work chapter, not the contribution.
