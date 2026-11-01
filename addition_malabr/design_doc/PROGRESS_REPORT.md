# MALABR -- progress report

Branch `chaitanya/v0`. Covers the work since the shared-KV memory model landed:
two full audits of the inference server and chat panel, the fixes that came out
of them, and the design analysis done alongside.

## What the system is

MALABR runs one local LLM (llama.cpp, CPU only) inside a patched Chromium and
shares it across browser tabs. Each tab gets an isolated session; the server
schedules them so the visible tab stays responsive, bounds each session's
memory, and keeps the browser itself usable. The chat panel is a content-script
extension talking to a Python server over a Unix socket.

## Audit 1 -- correctness (13 suspect areas, all resolved)

| Problem found | What was wrong | How it was fixed |
|---|---|---|
| Shared-KV pool could wedge permanently | A rollback snapshot from the previous turn was left pointing inside an exchange that the memory guard then dropped; the guard's own safety check fired every round and nothing recovered | Snapshot is pinned at the start of every turn and cleared when a turn ends |
| Same wedge from idle sessions | The guard also compacts idle sessions, which carry the same stale snapshot | Same pin applied on turn completion and rollback |
| A whitespace-only reply killed the session | Chat templates trim replies; the KV kept the whitespace, so the next turn failed its consistency check and tore the session down | Trim logic rewritten: handles all-whitespace replies, refuses to cut into real text, bounded to the reply |
| Model switch could leave a dead server | `os.execv` keeps the pid; if the old pidfile survived, the new process saw its own pid as a rival and refused to start | Lock treats its own pid as stale and reclaims it |
| "New chat" only cleared the display | The server session and its context lived on, so the next message continued the old conversation | Wired to a real server-side teardown over the existing control channel |
| CPU ceiling applied before the duplicate-instance check | A rejected second launch still created a systemd scope and could stall on it | Order swapped; ceiling now also verifies the quota actually took effect |
| Memory guard could give up and let the next decode fail | When compaction ran out of room it returned silently and the engine hit "no memory slot" | Guard now rejects the not-yet-started turn with a clear error instead |
| Chat panel could start two generations at once | The send lock timed out on slow first tokens | Timeout aligned with the existing round-trip limit; a timed-out send cleans up its UI |
| Test harness used a fixed per-session budget | Did not match the production formula, so the compaction trigger in tests was off | Harness mirrors production |

Four other suspect areas (BOS handling under rollback, template-error teardown,
an unlocked read in the guard, shared-KV parity) were verified correct and
locked with regression tests. The full suite now also runs a second time in
shared-KV mode and passes identically.

## Audit 2 -- line-by-line review (16 items, all resolved)

Real gaps: an unbounded read of the wire header length (same bug class already
fixed for the payload); the single-instance lock taken only after the
two-minute model load, so two launches could both pay for it; a foreground
session starved permanently when two extensions share a tab; the benchmark
command bypassing the send lock; a startup path that could crash instead of
degrading. The rest were duplicated constants, a stale module docstring,
scattered imports, a redundant model reload during calibration, and config
documentation that could be misread. Each is one commit.

## Operational

Diagnosed the "model name not showing" launch failure: the browser resolves the
server script relative to its working directory, so launching from any
directory other than the repo root silently starts nothing. Documented the
correct launch command.

## Design analysis

Positioned the per-session memory/CPU isolation against prior art (Orca, vLLM,
VTC, Sarathi): the mechanisms are known; the novelty is the setting (CPU-only,
interactive, in-browser, across web origins) and the isolation-verification
method. Assessed alternative control signals (per-session CPU time, PSI
pressure, browser attention signals) and the page-context truncation problem
(naive prefix cut; extraction and relevance selection are the fixes, encoding
tricks are not).

## Measured results (new)

Until now the project had mechanisms and tests but very little evidence. Two
measurements now exist, both reproducible from the repo.

### Scheduling -- three arms, same model and machine

- **fifo** -- a lock around one shared llama.cpp context.
- **parallel** -- continuous batching with equal shares and no visibility
  signal: llama.cpp `--parallel`, VTC-style fairness, and every serving engine.
  Run inside our own engine with the foreground disabled, so the only variable
  is the visibility signal itself.
- **malabr** -- the same engine with that signal on.

8 tabs, foreground arriving last (background tabs already busy when the user
turns to a tab and types):

| arm | foreground TTFT | median TTFT | p95 TTFT |
|---|---|---|---|
| fifo | 12.10 s | 7.01 s | 12.10 s |
| parallel | 4.19 s | 3.51 s | 4.19 s |
| **malabr** | **0.32 s** | **3.15 s** | **3.63 s** |

37.6x better than FIFO and **13.0x better than equal-share batching**. The
second figure is the meaningful one: equal-share batching already removes most
of FIFO's penalty, so the comparison that isolates this project's contribution
is the one against `--parallel`, with everything else held constant.

At 4 tabs the same experiment gives 18.4x over FIFO. The effect grows with
contention, which is the expected shape.

Control, foreground arriving FIRST: fifo 0.08 s, parallel 0.32 s, malabr 0.41 s
-- MALABR loses by the cost of its own machinery. Reported deliberately: a
first-arriving request is served immediately by FIFO for reasons that have
nothing to do with scheduling, and showing only the favourable ordering would
invite exactly one question.

### The relative comparison (model-independent)

Absolute seconds cannot be compared across systems that run different models.
What is comparable is each system measured against ITSELF:

    relative score = foreground TTFT / median TTFT across all tabs

Below 1.0 means the visible tab is prioritised. About 1.0 means the scheduler
cannot tell the tabs apart. The ratio is dimensionless, so it survives the
models differing -- which is what makes it the right way to sit MALABR beside
a shipped browser LLM.

8 tabs, foreground arriving last (background tabs already generating when the
user turns to a tab and types):

| system | foreground TTFT | median TTFT | **relative score** |
|---|---|---|---|
| FIFO (one shared context) | 14.26 s | 7.78 s | **1.83** |
| equal-share batching (llama.cpp `--parallel`, VTC) | 4.61 s | 3.83 s | **1.20** |
| **MALABR** | 0.31 s | 3.30 s | **0.10** |
| Chrome built-in Prompt API (Gemini Nano, CPU) | 0.50-0.58 s | 5.7-6.8 s | **0.086** |

**Repeated measurement, 8 tabs, foreground last, warm-up discarded:**

| system | n | median | min | max | stdev |
|---|---|---|---|---|---|
| MALABR | 5 | **0.092** | 0.084 | 0.106 | 0.009 |
| Chrome (comparable measure) | 4 | **0.086** | 0.074 | 0.101 | 0.012 |

The two harnesses did not originally measure the same interval: MALABR creates
its session BEFORE starting the clock, Chrome's `LanguageModel.create()` was
inside it. Chrome's figure above has `create()` subtracted; uncorrected it is
0.130. Warm-up matters on both sides and is discarded on both: MALABR's first
run gave 0.457 against 0.075-0.107 after, Chrome's first two gave 0.76 and 0.43
against 0.11-0.15 after.

**These two are statistically indistinguishable.** The ranges overlap almost
entirely, the gap (0.006) is smaller than either standard deviation, and the
ordering FLIPS with the analysis choice -- including all runs makes MALABR
lower by 0.003, warm-only makes Chrome lower by 0.007. No claim of being
better, or slightly better, is supportable.

MALABR's foreground is an order of magnitude faster than its own typical tab.
The two baselines score at or above 1.0: FIFO is actively *worse* for the
foreground (it is last in the queue), and equal-share batching is indifferent.

Control, foreground arriving FIRST: 0.02 / 0.06 / 0.07. All three look good
for a reason that has nothing to do with scheduling -- a first-arriving request
is served first by any policy -- which is exactly why the realistic ordering is
the one reported.

**What the Chrome row means, and a claim that has to be withdrawn.**

An earlier version of this report predicted, from source, that Chrome could not
score below 1.0 -- nothing in its model-execution path reads tab visibility,
and `OnDeviceModelAccessController` gates only on crashes, GPU blocking and
timeouts. **The measurement contradicts that prediction.** Chrome scores
0.12-0.26, comparable to MALABR's 0.10. The source reading was correct about
the model scheduler; the inference drawn from it about end-to-end behaviour was
not, and the prediction is withdrawn rather than defended.

So **"the shipped browser LLM does not prioritise the tab you are looking at"
is NOT supported by measurement, and must not be claimed.** Chrome prioritises
it as effectively as MALABR does, by a mechanism this experiment did not
isolate. An early single run suggested renderer throttling explained it
(0.12 -> 0.38 with throttling disabled); repeats gave 0.13 and 0.12, so that
was an outlier and the throttling hypothesis is NOT supported either.

What survives, and is still the stronger result, is the SAME-MODEL comparison:
against FIFO (1.83) and equal-share batching (1.20), with model, machine,
batching and budgets all held constant, MALABR's 0.10 isolates the visibility
signal as the cause. Chrome is a different model, a different runtime and an
unknown mechanism, so it is a useful sanity reference, not a controlled
comparison.

The honest differentiator against Chrome is therefore NOT foreground
prioritisation. It is the other three properties, which Chrome's own source
shows it does not have: no per-session memory budget (the output cap is
caller-supplied and context management is delegated to the page), no admission
control under contention, and no lifecycle reclamation of compute for closed
tabs.

### Whole-pipeline measurement (not just TTFT)

TTFT describes one instant. This records EVERY token's arrival time for every
session and derives what the user lives with afterwards. Same model, machine
and engine across all three arms; the scheduler is the only variable. 8 tabs,
foreground arrives last, medians of 3 runs after a warm-up
(`tests/pipeline_bench.py`):

| metric | FIFO | equal-share | **MALABR** | better |
|---|---|---|---|---|
| foreground TTFT (s) | 16.55 | 4.22 | **0.44** | lower |
| TTFT p50 (s) | 16.48 | 3.27 | **3.09** | lower |
| TTFT p95 (s) | 16.55 | 4.22 | **4.08** | lower |
| inter-token p50 (s) | **0.000** | 0.213 | 0.192 | lower |
| inter-token p95 (s) | **0.289** | 0.318 | 0.298 | lower |
| foreground gap p95 (s) | **0.000** | 0.287 | 0.151 | lower |
| background gap p95 (s) | **0.296** | 0.319 | 0.304 | lower |
| stalls >1s, foreground | 0 | 0 | 0 | lower |
| stalls >1s, background | **0** | **0** | 2 | lower |
| completion p50 (s) | 16.53 | 15.96 | **15.65** | lower |
| makespan (s) | 16.55 | 16.15 | **15.83** | lower |
| throughput (tok/s) | 31.4 | 32.2 | **32.8** | higher |
| Jain fairness (1 = equal) | **0.998** | **0.997** | 0.631 | higher |

**What MALABR buys:** foreground TTFT 9.7x better than equal-share batching and
38x better than FIFO, and a foreground stream about **1.9x smoother** once it
starts (gap p95 0.151 vs 0.287). Throughput, makespan and completion are within
noise of the other arms -- as expected, since batching the same model on the
same cores does not create throughput.

**What it costs, stated plainly:** fairness falls from ~0.998 to **0.631**, and
background sessions take 2 visible stalls (>1s) that neither other arm
produces. That is not a defect, it is the policy working: foreground-first
admission necessarily takes service away from background tabs. But it is a
trade, and the thesis should present it as one rather than claim a free win.

**FIFO's genuine advantage, worth not hiding:** its inter-token gap is *zero*.
Running one session at a time at full speed produces a perfectly smooth stream
-- it just makes you wait 16 s to see it. The trade the batched arms make is
smoothness-within-a-stream for concurrency across streams.

### Chrome on the same pipeline metrics

Measured with the same definitions, streams capped at 40 chunks. Two findings
are stable across runs and two are not:

| stable | Chrome | MALABR |
|---|---|---|
| Jain fairness | **0.987-0.994** | 0.631 |
| stalls >1s (foreground / background) | 0 / 1-9 | 0 / 2 |
| inter-chunk p50 (s) | 0.095-0.100 | 0.192 |

Chrome is markedly **fairer** than MALABR, and like MALABR it never stalls the
foreground -- only background tabs. Both systems therefore protect the visible
tab at the expense of hidden ones; Chrome simply takes less from them.

**NOT stable, and therefore not claimed:** Chrome's relative TTFT score under
sustained streaming ranged **0.02 - 0.78** across three runs, and one further
run produced no tokens on 7 of 8 tabs. That spread is far too wide to compare
against MALABR's 0.092 +/- 0.009. The earlier first-chunk-only measurement
(Chrome 0.086, MALABR 0.092, indistinguishable) remains the defensible
comparison; under sustained load on CPU-only Nano, Chrome's behaviour is simply
too variable on this hardware to characterise.

### The RQs tested against Chrome, architecture by architecture

Each RQ was put to Chrome's built-in Prompt API with the same method used on
MALABR, rather than argued from source.

**RQ1 (isolation) -- Chrome PASSES. Not a differentiator.**
`scripts/chrome_rq1_isolation.py`. Canary planted in a session on
`http://localhost:P`, then sought from `http://127.0.0.1:P` (a genuinely
different origin), from a second tab on the same origin, and from a fresh
session in the same page after `destroy()`. All three returned UNKNOWN, with a
POSITIVE CONTROL confirming the canary was recallable in its own session
("MAGENTA-7731") -- without that control the negatives would have been
worthless, and the first version of the test failed its own control for
exactly that reason. Chrome isolates correctly. MALABR's contribution here is
the verification METHOD and origin-keyed session identity, not a defect in
Chrome.

**RQ3 (scheduling) -- a real architectural difference: WHERE priority is
applied.**
Chrome prioritises at ADMISSION: a foreground request issued 4 s after three
background tabs still gets its first token in ~1.8 s, essentially unchanged
from issuing first (~1.5-2.4 s), so it jumps the queue. But in steady state it
serves near-equally (Jain 0.99). MALABR prioritises at EVERY DECODE ROUND:
foreground 14.9 tok/s against ~3.6 for every other tab, sustained, hence Jain
0.63. Same goal, different stage of the pipeline, different consequences.

**RQ4 (lifecycle reclamation) -- Chrome does NOT reclaim. Strong
differentiator.**
`scripts/chrome_rq4_cancel.py`. 4 tabs streaming; half closed at t=15 s;
surviving tabs' chunk rate after the close compared against before, with a
control arm that closes nothing.

| arm | survivor speed-up | n |
|---|---|---|
| control (close nothing) | 1.11x | 7 |
| close half the tabs | 1.04x | 7 |

If Chrome returned the closed tabs' compute, halving the session count should
roughly DOUBLE the survivors' rate. The observed change is 1.04x and the two
arms' distributions overlap entirely. The effect being looked for is large and
simply absent. MALABR on the same question: 453 -> 0 wasted decode tokens, and
the tabs still being watched finish 1.94x sooner.

**RQ2 (memory governance) -- Chrome caps per session but does NOT divide a
pool.**
`scripts/chrome_rq2_memory.py`. First, a correction: Chrome 153 DOES expose
per-session accounting -- `contextWindow`, `contextUsage`,
`measureContextUsage()` and an `oncontextoverflow` event -- so the earlier
claim here that Chrome has "no per-session memory budget", taken from the
Chromium 127 source in this checkout, is **wrong for 153 and is withdrawn**.

What the measurement shows is the distinction that actually matters. Holding
sessions open one at a time and reading `contextWindow` from each:

| sessions alive | 1 | 2 | 3 | 4 | 5 | 6 |
|---|---|---|---|---|---|---|
| contextWindow (tokens) | 9216 | 9216 | 9216 | 9216 | 9216 | 9216 |

**The window is constant.** Every session is handed a full 9216-token window
independently of how many already exist, so six sessions can claim 55,296
tokens between them and N sessions can claim 9216N. A per-session cap that is
never divided is a limit on one page's appetite, not governance of a shared
resource: nothing in it prevents the aggregate from growing without bound as
tabs are opened.

That is precisely the gap §8 is built around. MALABR allocates `n_ctx` ONCE at
startup and divides it -- weighted per-session budgets, an aggregate guard that
compacts the greediest session when the sum approaches the pool, and graceful
degradation rather than rejection. The two systems are answering different
questions: Chrome bounds a session, MALABR bounds the machine.

**And what happens AT the limit differs too.** Filling one session past its
window (reproduced across runs, identical numbers each time):

| | Chrome | MALABR |
|---|---|---|
| signal | `contextoverflow` event fires | compaction runs inside the round |
| usage after | 9216 -> 8208 tokens (content evicted) | freed to target, session continues |
| earliest content | **LOST** | **kept: the anchor turn is never dropped** |
| answer afterwards | degraded -- returned `'lazy'` / `'dog.'`, words from the filler, not the planted anchor | anchor recalled (§12 test 4); top-1 agreement 4/4 after dropping 58-64% (§8 fidelity check) |

Chrome evicts from the front -- a sliding window. The first thing the user said
is the first thing to go, which for a chat session is usually the instruction
or the topic. MALABR's compaction is anchor-preserving by construction
(`turn_boundaries[1:-1]` is the droppable range, so turn 0 and the most recent
turn always survive), which is why the canary planted in the opening turn is
still recallable after most of the conversation has been discarded.

This is the sharpest RQ2 result: both systems degrade rather than fail, but
they degrade in opposite directions -- Chrome keeps the recent and loses the
premise; MALABR keeps the premise and loses the middle.

**Inconclusive, and recorded as such: does a greedy session hurt its
neighbours?** One tab filling its context while two others ran normal prompts
gave control 3.32 s (n=6, tight: 3.26-3.57) against 4.05 s for the greedy arm
-- but the greedy arm has **n=1**, because that tab's DevTools connection died
on every subsequent run ("socket closed", then broken pipes), most likely its
renderer going down under repeated context filling. One sample against six is
not a comparison and 1.22x is not reported as a result.

Worth noting WHY a null result here would not have contradicted anything:
experiment A showed each session gets its own independent 9216-token window,
so a greedy neighbour does not take context away from anyone. The cost of an
undivided pool lands on RESIDENT MEMORY, not on a neighbour's latency at three
tabs -- which is what the RSS measurement below is for.

**Summary of where MALABR is and is not differentiated:**

| RQ | Chrome | MALABR | differentiator? |
|---|---|---|---|
| 1 isolation | passes | passes | **no** -- method, not outcome |
| 3 scheduling | priority at admission | priority every round | **partly** -- different design point |
| 4 cancellation | does not reclaim | reclaims fully | **yes** |
| 2 memory governance | per-session cap, pool NOT divided | pool divided + aggregate guard | **yes** |

### Cancellation -- what closing a tab recovers

8 tabs generating, 4 closed partway. Counted as tokens entering `llama_decode`
after the close, not frames delivered:

| arm | decode spent on closed tabs | watched tabs finish |
|---|---|---|
| delivery-only | 453 tokens | 22.19 s |
| reclaim (MALABR) | 0 tokens | 11.87 s |

All of the wasted compute recovered, and the tabs still being watched finish
1.87x sooner.

### Compaction fidelity -- what degrading gracefully actually costs

Two identical conversations; one compacted through the shipping code path; the
next-token distributions compared at four probes.

| | context dropped | top-1 agreement | mean KL |
|---|---|---|---|
| compacted | 58-64% | 4/4 probes | 0.0017-0.0032 nats |
| noise floor | -- | 4/4 probes | 0.0000-0.0002 nats |

Ranges are across runs rather than error bars: how far a conversation has grown
when compaction fires varies between runs. The conclusion did not.

The noise floor is measured between two *uncompacted* sessions, because batched
decode is not bit-exact across sequences; a nonzero divergence there is not a
cost of compaction. Dropping well over half the conversation cost about 8x that
floor and changed the model's chosen token on none of the probes.

## Chrome's built-in LLM as a baseline

Resolved what was previously recorded as "blocked by a performance-class gate".
That gate is not a blocker -- it is a field-trial parameter that accepts "*".
The actual blocker is that `build_with_internal_optimization_guide` defaults to
`is_chrome_branded`, so a public Chromium checkout compiles a stub that reports
"failed to load library" and implements inference as an echo of the prompt. No
flag changes that.

Real Google Chrome 142 on this machine does qualify -- performance class "high",
`LanguageModel` exposed, availability "downloadable". Scripts to drive the
download and the comparison are in `scripts/`. The weights themselves are still
arriving on Google's component-update schedule.

## Optimizations applied

- Idle engine loop: ~1000 wakeups/s -> blocks on an event. 400 idle rounds in
  0.4 s became 16, with no loss of responsiveness (1.1 ms from submit to round).
- A single session is no longer rationed against sessions that do not exist:
  5.3x larger prefill chunks when nothing else is competing.
- Chat panel: repaint was quadratic in reply length (full markdown re-parse and
  DOM rebuild per token); now coalesced to one frame.
- Page context: prefix truncation replaced by extraction plus relevance
  selection, verified in a real browser.

## Next

- Finish the Gemini Nano download and run the three-way scheduler comparison.
- Quantify cancellation: the saving from stopping work for closed tabs has been
  implemented and tested but never measured.
- KV-cache quantization, using the fidelity harness that now exists.
