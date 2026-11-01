# STEP 2 — In-field literature and gap map

**Verification status, stated up front.** Everything below was found via web
search in this session. Titles, venues and URLs are as returned by search;
I read abstracts and search summaries, **not** full texts. Nothing is invented,
but several items need a manual check on Google Scholar before they go in a
thesis — those are flagged `[VERIFY]`. Where I could not establish something,
it says so rather than guessing.

---

## 1. Iteration-level scheduling and continuous batching

The mechanical foundation MALABR builds on. Prior art, not contested.

- **Orca** (OSDI '22) — iteration-level scheduling: re-decide the batch every
  iteration instead of per request. This is the mechanism MALABR's `build_batch`
  uses; MALABR's addition is the *signal* fed into it.
- **vLLM / PagedAttention** (SOSP '23) — paged KV cache and sequence
  preemption.
- **Sarathi-Serve** — chunked prefill, so a long prompt does not block decode.
  MALABR implements this (§9b) with a closed loop on the cost model.
- **Survey:** *Taming the Titans: A Survey of Efficient LLM Inference Serving*,
  arXiv [2504.19720](https://arxiv.org/pdf/2504.19720). Useful as the citation
  anchor for "these mechanisms are established".

**What they leave open for MALABR:** all of them schedule *anonymous* clients.
None has a concept of which client a human is currently looking at, because
none runs inside a browser.

## 2. Fairness in LLM serving

The closest literature to MALABR's scheduling claim, and the one that most
constrains it.

- **VTC — Fairness in Serving Large Language Models**, arXiv
  [2401.00588](https://arxiv.org/pdf/2401.00588) (OSDI '24). Adapts fair-share
  scheduling from networking/OS to LLM serving; tracks cumulative input+output
  tokens per client and serves the client with the lowest counter. Reported
  >2x reduction in "maximum service difference" vs FCFS.
- **FairServe / Weighted Service Counter** — *Ensuring Fair LLM Serving Amid
  Diverse Applications*, arXiv
  [2411.15997](https://arxiv.org/pdf/2411.15997). Adds
  application-characteristic-aware throttling and a **weighted** service
  counter. This is the nearest thing to "some clients matter more", but the
  weight comes from application class, not from user attention.
- **Equinox — Holistic Fair Scheduling in Serving LLMs**, arXiv
  [2508.16646](https://arxiv.org/pdf/2508.16646) `[VERIFY]`.
- **Cascade — SLO-aware latency budget for fair and high-goodput serving**,
  arXiv [2608.06557](https://arxiv.org/pdf/2608.06557) `[VERIFY]`.

**Direct relevance to MALABR, and it cuts both ways.** This literature treats a
high Jain fairness index as the *objective*. MALABR deliberately scores **0.631
against the baselines' 0.997** (STEP 1 §5.3) because its clients are not of
equal claim — one has a human watching. That is a different objective function,
not a worse result, and the thesis must say so explicitly or a reviewer from
this community will read it as a failure.

**What is open:** a *weighted* fairness formulation whose weights come from a
live attention signal, and a principled way to choose the weight. FairServe's
WSC is the obvious formal home for MALABR's policy, and MALABR currently has no
such formulation — see **W1**.

## 3. Priority- and SLO-aware serving

- **ProServe — Unified Multi-Priority Request Scheduling for LLM Serving**,
  arXiv [2512.12928](https://arxiv.org/pdf/2512.12928) `[VERIFY]`. Explicitly
  motivated by "the critical yet often overlooked dimension of inherent
  priority difference among clients"; reports up to 35% system-gain and 52%
  SLO-attainment improvements. **This is the closest competitor to MALABR's
  framing** and must be cited and distinguished: its priorities are declared
  per client, MALABR's are *observed* from the UI and change second to second.
- **Kairos — SLO-aware scheduling for disaggregated inference**, arXiv
  [2605.02329](https://arxiv.org/pdf/2605.02329) `[VERIFY]`. Notable for
  concrete interactive SLOs: **8 s TTFT, 50 ms TPOT (~20 tok/s)**. These are
  external numbers MALABR can cite instead of its own unjustified constants
  (**W6**) — MALABR's 50 ms round budget sits suspiciously close to Kairos's
  50 ms TPOT target, which is worth a sentence.
- **AdaServe** (EuroSys '26), CMU
  [PDF](https://www.cs.cmu.edu/~zhihaoj2/papers/AdaServe_EuroSys26.pdf) —
  multi-SLO serving via SLO-customised speculative decoding.
- **SlidingServe**, arXiv [2606.05933](https://arxiv.org/abs/2606.05933)
  `[VERIFY]`.

**What is open:** every one of these takes priority as an *input*. None derives
it from user state, and none handles priority that flips while a request is
mid-generation — which is exactly what a tab switch does.

## 4. KV-cache budgeting and eviction

MALABR's §8 compaction lives here, and this is where MALABR is weakest relative
to the field.

- **StreamingLLM** — attention sinks plus a recent window. MALABR's
  "keep turn 0 and the most recent turn" is structurally the same idea at
  conversation granularity rather than token granularity.
- **H2O** — cumulative-attention-score eviction.
- **SnapKV** — observation window to find important token clusters;
  [ResearchGate](https://www.researchgate.net/publication/397201904_SnapKV_LLM_Knows_What_You_are_Looking_for_Before_Generation).
- **PyramidKV / D2O / Ada-KV** — *non-uniform* budget allocation across layers
  and heads (pyramidal, attention-density-driven, per-head).
- Recent: *Beyond Token Eviction: Mixed-Dimension Budget Allocation*, arXiv
  [2603.20616](https://arxiv.org/pdf/2603.20616); *ZigZagKV*, arXiv
  [2412.09036](https://arxiv.org/pdf/2412.09036); *More Tokens, Lower
  Precision*, arXiv [2412.12706](https://arxiv.org/pdf/2412.12706) — the
  token-count vs precision trade-off, directly relevant to MALABR's own q8_0
  finding; *LKV: End-to-End Learning of Head-wise Budgets*, arXiv
  [2605.06676](https://arxiv.org/pdf/2605.06676) `[VERIFY]`; *Discovering KV
  Cache Eviction Policies via LLM-Guided Program Evolution*, arXiv
  [2608.14555](https://arxiv.org/pdf/2608.14555) `[VERIFY]`.

**The asymmetry to be honest about.** This field allocates budget *within one
model* (across layers, heads, tokens) to serve *one* sequence better. MALABR
allocates budget *across sessions* belonging to *different web origins*. They
are orthogonal axes and the thesis should say so plainly — MALABR's eviction
policy is crude by this field's standards (whole exchanges, no attention
scores), and that is defensible only because its contribution is the
inter-session division, not the intra-sequence selection.

**What is open:** nobody in this list divides a *fixed shared pool across
mutually distrusting tenants* and then evicts within each share. That
composition is MALABR's territory.

## 5. Browser and on-device inference

- **WebLLM: A High-Performance In-Browser LLM Inference Engine**, arXiv
  [2412.15803](https://arxiv.org/abs/2412.15803) — the key piece of prior art
  for the *setting*. WebGPU + WASM, OpenAI-shaped API, runs entirely in the
  browser. Architecturally it is **application-scoped**: a web app brings its
  own engine in a SharedWorker. MALABR is **browser-scoped**: one model shared
  across origins by the browser itself. (This distinction is already the
  project's framing and the literature supports it.)
- **Chrome built-in Prompt API** — per search results, the `LanguageModel`
  interface became **stable on the open web in Chrome 148 (5 May 2026)**, with
  stated requirements of ~22 GB free disk and >4 GB VRAM `[VERIFY — these
  figures came from a secondary source, not Google docs]`. Measured locally in
  the prior session: `contextWindow = 9216` tokens per session, constant across
  1-6 concurrent sessions.
- Practitioner sources independently identify the same pressure point MALABR
  addresses: running several in-browser LLM instances "can quickly exhaust
  browser memory limits", and "every token in the context occupies GPU memory
  on the user's device, competing with the model weights themselves"
  ([pinggy](https://pinggy.io/blog/run_llm_in_browser_webgpu/),
  [awflow](https://awflow.io/in-browser-llm-webllm-chrome-ai-api/)).

**What is open, and this is the headline gap:** I found **no paper** that
treats contention between browser origins for a shared local model as a
scheduling or governance problem. The practitioner literature names the problem
and stops. That is where MALABR sits.

## 6. Tab visibility / attention as a systems signal

- The **Page Visibility API** and `visibilitychange` are standard web platform
  features, universally used to pause timers, defer background work and save
  battery. Chrome itself throttles background renderers.
- **ITO: Real-time Browser Tab Orchestration Through Intent Detection**, arXiv
  [2608.09107](https://arxiv.org/html/2608.09107v1) `[VERIFY]` — uses on-device
  inference to organise tabs by intent. Note the direction: it uses a model to
  manage tabs. MALABR uses tab state to manage a model. Worth citing precisely
  because the inversion is clean.

**Verified absence (the novelty signal).** Searching for tab-visibility or
user-attention-aware *inference scheduling* returns the Page Visibility API,
generic background-task deferral, and ITO — but **no work that feeds a
visibility signal into an LLM scheduler**. The transfer appears not to have
been made. This is the single strongest novelty claim available, and it is also
the one most in need of manual verification (**flagged for Scholar**).

## 7. Multi-tenant isolation and KV-cache side channels

**The most important thing found this session, and it is not what the project
is currently testing.**

- **InputSnatch: Stealing Input in LLM Services via Timing Side-Channel
  Attacks**, arXiv [2411.18191](https://arxiv.org/pdf/2411.18191).
- **PromptPeek — I Know What You Asked: Prompt Leakage via KV-Cache Sharing in
  Multi-Tenant LLM Serving**, NDSS '25
  [PDF](https://www.ndss-symposium.org/wp-content/uploads/2025-1772-paper.pdf).
  Per search summary: fingerprints a victim's cached prompts by probing TTFT
  across a vocabulary, reported **99-100% attack success** against SGLang's
  radix-tree prefix cache.
- **Selective KV-Cache Sharing to Mitigate Timing Side-Channels**, arXiv
  [2508.08438](https://arxiv.org/pdf/2508.08438).
- **KVGov — Governing the KV Cache: Preventing Timing Side-Channel Leakage in
  Multi-Tenant LLM Inference**, arXiv
  [2608.09225](https://arxiv.org/pdf/2608.09225) `[VERIFY]` — claims to be the
  first to cover all three attack families under one mechanism.
- **Unveiling Hardware Cache Side-Channels in Local Large Language Model
  Inference**, USENIX Security '25
  [PDF](https://www.usenix.org/system/files/usenixsecurity25-gao-zibo.pdf) —
  token *value* leakage from embedding-lookup cache patterns and token
  *position* leakage from decode timing, in **local** inference.
- **Loophole: Timing Attacks on Shared Event Loops in Chrome**, arXiv
  [1702.06764](https://arxiv.org/pdf/1702.06764) — the classical
  browser-side-channel precedent.

**Why this matters to MALABR specifically.** MALABR's RQ1 tests
**content** leakage: plant a canary, try to read it from another origin. The
literature above shows the realistic attack on a shared model is **timing**,
not content — and MALABR's own architecture creates exactly the preconditions:
one shared context, sessions fused into the same batched forward pass, and a
scheduler whose admission decisions are *observable as latency* by any origin
holding a session. Chrome's Prompt API has the same exposure.

So the project's isolation result ("passes, same as Chrome") is **weaker than
it looks**: it is a pass on the easy channel. This is a gap and also an
opportunity — see the gap map.

---

## 8. Gap map

What is not yet done, keyed to STEP 1's weaknesses.

| # | Gap | Why MALABR is well placed | Weakness |
|---|---|---|---|
| **G1** | **Cross-origin timing side channels on a browser-shared local LLM.** Studied for cloud multi-tenant serving and for local hardware caches, never (found) for the browser's own shared model across web origins. | Already has origin-keyed sessions, the canary harness, a CDP measurement rig, and a measured scheduler whose decisions show up as latency. Chrome is available as a second subject. | W12, and it re-opens RQ1 |
| **G2** | **Attention-weighted fair scheduling with a formal weight.** The fairness literature maximises Jain; nobody parameterises the departure from it using a live UI signal. | Has the signal, the mechanism, and a measured 0.631-vs-0.997 exchange rate that currently has no knob. | W1, W2 |
| **G3** | **A fixed pool divided across mutually distrusting tenants, with per-share eviction.** KV-eviction work optimises within one sequence; serving work caps totals. Neither divides a pool across origins. | This is already implemented (shared-KV + aggregate guard + anchor-preserving compaction) and measured against Chrome, which does **not** divide (9216 constant). | — (strength to claim) |
| **G4** | **Per-turn compute accounting as the unit of governance.** All cited systems count tokens. Tokens are a poor proxy for compute at different context depths. | The project's own notes identify this; `CostCurve` already predicts per-position cost, so the plumbing exists. | W3 |
| **G5** | **Graceful degradation compared ACROSS systems.** The eviction literature compares policies within a model; nobody compares *what a system chooses to lose* under pressure. Prior session found Chrome evicts from the front (loses the premise) and MALABR preserves the anchor (loses the middle). | Both measurements already exist and are reproducible. | W11 |
| **G6** | **Lifecycle-driven compute reclamation.** No cited system reclaims work for a client that has gone away; Chrome measurably does not (1.04x vs 1.11x control). | Implemented and measured (453 -> 0 tokens). | — (strength to claim) |
| **G7** | **Heterogeneous browser workloads.** Every cited evaluation uses homogeneous request streams or public traces of anonymous clients. No benchmark represents "one long page summarisation beside five short chats, one of them visible". | Needs only a workload generator; the harnesses exist. | W7, W9 |
| **G8** | **Perceptual grounding for interactive local inference.** Kairos-style SLOs (8 s TTFT, 50 ms TPOT) are asserted by systems papers. Nobody has grounded them for the *browser* case where a user can see one tab and not others. | Could be done small-scale; would justify W6's constants. | W6, W10 |

**Honest note on G3 and G6.** These are already *done* in MALABR — they are
contributions to write up, not research to do. G1, G2 and G4 are the ones that
would add new work. G1 is the most distinctive and the most defensible; it is
also the only one that changes the project's threat model rather than its
performance story.
