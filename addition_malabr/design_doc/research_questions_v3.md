# MALABR — research questions, v3 (2026-09-22)

Supersedes the RQ section of `research_problem_statement_v2.md`.

The reason for a v3 is not that the old questions were wrong. It is that the
thing they were speculating about now **ships**. Chrome has a built-in on-device
LLM exposed to web pages (`LanguageModel`, Gemini Nano). That changes what a
good research question looks like: "can one share a local model across tabs?"
is no longer a question, because Chrome already does it for hundreds of millions
of users. What Chrome does *not* do is where the work is.

Every claim about Chrome below is cited to source in this checkout
(`chrome/VERSION` = 127) or to a live measurement on Chrome 142 stable, and is
marked which.

---

## 1. What Chrome's built-in LLM actually does

Established by reading this tree, by querying Google's update server directly,
and by running the API — not from documentation.

> **Note on availability.** Chrome's Prompt API could not be executed on the
> development machine, and the reason is documented rather than assumed: the
> only local binary carrying `liboptimization_guide_internal.so` (Chrome 142)
> is refused the model component by Google's update server, which serves it to
> Chrome 128–136 only; and no Chrome-for-Testing build at any version ships
> that library. The weights were obtained and Chrome 142 loads them
> ("Foundational Model: Ready (Overridden)"), but the per-feature adaptation
> for PromptApi is absent from that 2024 package, so no session can be created.
> Every claim below is therefore from SOURCE, which is stronger evidence for
> design claims than a benchmark would have been anyway. See
> `session_handoff_v6.md` for the full trail.

### The single most important finding

`OnDeviceModelAccessController` — the class whose stated job is to "determine
when the model may be used" — gates on exactly three things: repeated
**crashes**, a blocked **GPU**, and session **timeouts**
(`on_device_model_access_controller.h`). A grep of
`services/on_device_model/` and
`components/optimization_guide/core/model_execution/` for `max_sessions`,
`session_limit` or `MaxConcurrent` returns nothing.

> Chrome decides **whether** the model may be used at all. It never decides
> **how much of it any one page gets.**

Everything else in the design is *eligibility*: performance class, free disk
space, whether the feature was used recently
(`on_device_model_component.cc::should_install`). Eligibility is a
property of the device. It is not resource management, and it does not become
resource management when several pages want the model at once.

That is the gap, stated in one sentence, from Chrome's own source.

---

## 2. The questions

### RQ1 — Is document-scoped session lifetime enough to isolate web origins?

Chrome scopes a model session to the **document**. That is a lifetime, not an
identity: it says when state is destroyed, not who may reach it. Two questions
follow that Chrome's design does not answer — what happens to residue when the
underlying slot is handed to a different origin, and whether an origin can
reach another's state while both are live.

*Why it is a real question:* we built it wrong first and demonstrated the leak.
It is not a formality.

*Method:* plant a canary in one session; try to reach it from another — while
both live, after one ends, after the slot is reused, and while both are fused
into the **same batched forward pass**.

*Status:* **answered.** §12 tests 1, 2, 3/3b/3c, 5, 28. Origin is part of the
session key, so the unit of isolation is the web origin, not the tab.

---

### RQ2 — When resident context runs out, who loses it, how much, and what does that cost in output quality?

This is the question Chrome structurally cannot ask, because in Chrome nobody
owns context: `max_tokens` is supplied by the caller, so the page that asks for
the most gets the most, and a page that omits the limit is unbounded.

Three sub-questions, and the third is the one that carries evidence:

1. **Who is shrunk?** Dynamic weighted per-session budgets with
   reclaim-from-the-greediest, rather than static equal slices
   (llama.cpp `--parallel`) or a global cap (serving engines).
2. **By how much?** Degrade gracefully under aggregate pressure instead of
   rejecting — the OS alternative is to kill a process, because an OS cannot
   compress one: it has no idea what the memory means.
3. **What does it cost?** Measured as distribution shift, not vibes.

*Method for (3):* drive two identical conversations, compact one through the
shipping `Engine.compact`, then compare the next-token distribution at the same
probe. Report top-1 agreement, top-k overlap, KL and JS — **against a noise
floor** measured between two *uncompacted* sessions, because batched decode is
not bit-exact across sequences and a nonzero divergence there is not a cost of
compaction.

*Status:* **mechanism answered, and now measured.** `tests/fidelity_check.py`.
First run, Qwen3-0.6B, 4096-token context:

| | dropped | top-1 agreement | mean KL |
|---|---|---|---|
| compacted | 58-64% of context | 4/4 probes | 0.0017-0.0032 nats |
| noise floor | — | 4/4 probes | 0.0000-0.0002 nats |

Ranges are across runs, not error bars — the point at which compaction fires
moves, so the fraction dropped moves with it. Top-1 agreement never broke.

Dropping well over half the conversation moved the next-token distribution by
roughly **8x the noise floor and 0.0017 nats in absolute terms**, with the
model's chosen token unchanged on every probe. That is the quantitative claim
RQ2 always needed and never had.

---

### RQ3 — Does deciding at token granularity, using signals only a browser has, make the visible tab faster?

Chrome's model path does not read tab visibility at all. Serving systems
(Orca, VTC, vLLM) cannot: they have no concept of a tab, a focus, or a user
looking at something. This is the one place where being *inside* the browser is
not an implementation detail but the source of the signal.

*Method:* three schedulers, same machine, same model, same workload — Chrome's
built-in model as shipped, a fair-share baseline, and ours — measuring
foreground time-to-first-token and tail latency under N concurrent tabs.


> **MEASURED UPDATE (2026-09-23), and a withdrawn prediction.** Chrome's
> built-in Prompt API was eventually obtained (Gemini Nano CPU variant) and
> measured on the same protocol: **relative score 0.12-0.26 over four runs at
> 8 tabs**, against MALABR's 0.10. An earlier prediction in §1 — that Chrome
> could not score below 1.0, because nothing in its model-execution path reads
> tab visibility — is **contradicted by measurement and is withdrawn**. The
> source reading was right about the model scheduler; the inference about
> end-to-end behaviour was wrong. A control with renderer background
> throttling disabled gave an overlapping range, so throttling does not
> explain it.
>
> Consequence for this RQ: **"the shipped browser LLM does not prioritise the
> visible tab" cannot be claimed.** What stands is the same-model comparison
> below, where model, machine, batching and budgets are held constant and the
> visibility signal is the only variable. Chrome remains a useful sanity
> reference, not a controlled arm.

*Status:* **answered against the same-model baselines.** `tests/baseline_bench.py`, three arms, same model,
machine, prompts and engine — the only thing that differs between the last two
is whether the scheduler may use the visibility signal.

8 tabs, foreground arriving last (background tabs already generating when the
user turns to a tab and types):

| arm | fg TTFT | median | p95 |
|---|---|---|---|
| FIFO (a lock around one context) | 12.10 s | 7.01 s | 12.10 s |
| equal-share batching (llama.cpp `--parallel`, VTC) | 4.19 s | 3.51 s | 4.19 s |
| **MALABR** | **0.32 s** | **3.15 s** | **3.63 s** |

**37.6x over FIFO; 13.0x over equal-share batching.** The second number is the
claim. Beating FIFO proves little — equal-share continuous batching already
removes most of FIFO's penalty. Beating `--parallel` by 13x with model,
machine, batching and budgets all held constant isolates the *visibility
signal* as the cause, which is exactly what RQ3 asks.

At 4 tabs the same experiment gives 18.4x over FIFO; the effect grows with
contention, because under a blind scheduler the foreground's wait scales with
the queue ahead of it and under MALABR it does not.

Control (foreground arrives FIRST): FIFO 0.08 s, equal-share 0.32 s,
MALABR 0.41 s. MALABR loses, by the cost of its own machinery. Reported on
purpose: a first-arriving request is served immediately by FIFO for reasons
unrelated to scheduling, and publishing only the favourable ordering would
invite exactly one question.

The policy itself is separately pinned by §12 tests 10, 52, 55/55b, 56, 58 and
62: foreground admitted every round, at most one foreground at any instant, a
bounded aging guarantee, graceful degradation when the control channel dies.

### RQ4 — Does lifecycle-driven reclamation actually recover compute, and how much?

Chrome destroys the session object when the document goes away
(`AITextSession::Destroy`), and tells any outstanding responder
`kErrorSessionDestroyed`. What it does **not** establish is whether the
generation already in flight inside the model service stops, or merely stops
being delivered. Those are the same thing to the user and completely different
things to the machine.

*Method:* start a long generation, close the tab, measure engine work performed
after the close — not frames delivered.

*Status:* **answered for MALABR; Chrome's own behaviour still unmeasured.**
`tests/cancellation_bench.py`. 8 tabs generating, 4 closed 1.5 s in, counting
tokens that entered `llama_decode` after the close:

| arm | decode spent on closed tabs | watched tabs finish |
|---|---|---|
| delivery-only (no lifecycle signal) | 453 tokens | 22.19 s |
| **reclaim (MALABR)** | **0 tokens** | **11.87 s** |

All of the wasted decode recovered, and the tabs the user is still looking at
finish **1.87x sooner** — the saving is not a private efficiency, it is
latency returned to whoever is still watching.

Correctness of the teardown itself is §12 tests 8, 19, 50 and 59 (slot freed
within one round).

### RQ5 — Does the governance cost more than it saves?

Kept explicitly, because every mechanism above is overhead if the answer is no.
Reported alongside every other result rather than studied alone.

*Status:* idle cost now materially lower — the engine no longer wakes ~1000x/s
when nothing is happening (§12 test 66), and a lone session is no longer
rationed against sessions that do not exist (test 67, 5.3x larger prefill
chunks).

---

## 3. What is deliberately NOT a research question

- **Whether sharing one model saves memory.** Chrome and Edge already ship it.
  It is the premise.
- **Whether an on-device browser LLM is useful.** Shipped; not ours to argue.
- **Continuous batching, paged KV, chunked prefill.** Prior art
  (Orca OSDI'22, vLLM SOSP'23, Sarathi-Serve). We use them; we do not claim them.

---

## 4. The honest novelty claim

Stated so it survives a hostile question.

The **mechanisms** are prior art, each from a different layer: iteration-level
scheduling (Orca), token-denominated per-client fairness (VTC), paged KV and
preemption (vLLM), chunked prefill (Sarathi-Serve), budgeted eviction and
attention sinks (H2O, StreamingLLM), process CPU caps (cgroups). No paper owns
the combination, and no library packages it.

What is new, ranked:

1. **The regime.** Shared single-model inference across mutually distrusting
   **web origins**, in-browser, CPU-only, with an interactive foreground.
2. **Origin isolation as a verified correctness property**, with the
   canary-against-a-deliberately-broken-implementation method — and the finding
   that Chrome's own answer here is a *lifetime*, not an identity.
3. **Rationing resident context rather than throughput**, degrading gracefully
   under aggregate pressure, with the fidelity cost now measured rather than
   asserted.
4. **Clean rollback enforced simultaneously with compaction** shifting every
   absolute position (§12 tests 53/53b, 54).

Strip the browser and the origin framing and little remains. The paper is
"safe interactive multi-origin local inference"; resource management is the
means, not the contribution.

### The question to expect, and the answer

> *"Chrome already shipped this. What is left?"*

Chrome shipped the **capability** and the **eligibility check**. It did not ship
resource **ownership**: no per-session budget, a caller-supplied output cap,
context management delegated to the page, no visibility signal in the scheduler,
and an access controller that reasons about crashes rather than contention. The
moment more than one origin wants the model at once, every one of those is a
policy vacuum. MALABR is an answer to what fills it.
