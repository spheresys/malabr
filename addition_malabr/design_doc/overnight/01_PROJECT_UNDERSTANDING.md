# STEP 1 — What this project is, and where it is weak

Read-only analysis of `addition_malabr/` at commit `645dc13`, branch
`chaitanya/v0`. Measurements are cited to the harness that produced them;
nothing here is estimated.

---

## 1. The problem

One local LLM, one machine, many web pages wanting it at once.

Chrome now ships an on-device model to web pages (`LanguageModel`, Gemini
Nano). That makes the setting real rather than hypothetical: a single model
process, shared by mutually distrusting web origins, on a user's own CPU, with
one tab in front of a human and the rest hidden.

The question the project takes up is **who gets what**, when several origins
contend for one resident model. Not "can it be shared" — Chrome already shares
it — but whether sharing is *governed*.

## 2. Motivation, stated precisely

Three things are scarce and only one of them is normally managed:

- **Compute** — one CPU, N sessions wanting tokens.
- **Resident context** — the KV cache is allocated once and cannot grow; every
  token a session keeps is a token another cannot have.
- **Attention** — exactly one tab has a human looking at it. This is the signal
  no serving system can see, because no serving system knows what a tab is.

An operating system can kill a process but cannot *shrink* one: it has no idea
what the memory means. A serving engine can rate-limit a client but cannot
prefer the visible one. The project sits in the gap that leaves.

## 3. The pipeline, end to end

```
 web page (content.js, 878 lines)
   | page-context extraction (Readability-style + relevance selection)
   | chrome.malabr.generate()
   v
 patched Chromium browser process (C++)
   | knows tab id, origin, visibility; binds identity to the connection
   | Unix domain socket, length-prefixed frames
   v
 Python inference server (mserver/, 3191 lines)
   protocol.py    wire format, envelope validation
   slots.py       SlotAllocator, wipe-on-ACQUIRE (isolation)
   session.py     Session / Turn / OutputCap / state machine
   formatter.py   ChatFormatter (model's own template), Utf8Streamer
   scheduler.py   CostCurve, build_batch (latency-budget composition)
   engine.py      the single engine thread: rounds, compaction, aggregate guard
   runtime.py     UDS listener, routing, control channel, PidLock
   config.py      startup sizing from detected hardware
   calibration.py phases A-G, measures the machine before serving
   v
 llama.cpp (CPU only) — one model, one context, n_seq_max sequences
```

**One engine thread owns the model context.** Connection threads never touch
it; they set flags. This is load-bearing: `llama.h` promises thread safety
exactly once, scoped to tokenization, and the memory API carries no such
guarantee.

## 4. The method, in detail

### 4.1 Scheduling — spend a latency budget, don't fill slots
`build_batch` composes each round by spending `ROUND_LATENCY_BUDGET_MS = 50`:
the foreground session is admitted **unconditionally and first**, then an aging
pass rescues anyone excluded `MAX_CONSECUTIVE_EXCLUSIONS = 5` times, then
cheapest-first fills the remainder, then prefill chunks consume whatever budget
is left. A `CostCurve` built from calibration predicts each session's per-token
cost from its context position, and a closed loop (`observe_round`) corrects
the prediction against reality so a wrong cost model degrades rather than lies.

`SOLO_ROUND_BUDGET_MS = 150` applies when exactly one session has work: the
fair budget protects nobody when there is nobody to protect, and chunking a
lone prefill is pure cost.

### 4.2 Memory governance — divide a fixed pool
`n_ctx` is computed once at startup from detected RAM
(`0.5 * (MemAvailable - 2 GB browser reserve - model bytes)`, clamped to
[4096, 16384]) and allocated once. Two modes:
- **partitioned**: `n_ctx / n_seq_max` hard per-sequence slices.
- **shared-KV**: one pool, a soft per-session cap of `n_ctx/2`, and an
  *aggregate guard* that compacts the **greediest** session when the sum of
  live positions approaches the pool.

Compaction (`COMPACT_TRIGGER = 0.95`, `TARGET_FREED_TOKENS = 512`) drops whole
oldest exchanges while **always keeping turn 0 (the anchor) and the most recent
turn**, shifting every absolute position and the formatter's message list in
lockstep.

### 4.3 Isolation — wipe on acquire, key on origin
The session key is `(extension_id, tab_id, origin)`, derived entirely
browser-side from an authenticated connection; a request cannot claim another
identity in its payload. Slots are wiped **when handed out**, not when
released, because a release can be skipped by a bug or a crash and an acquire
cannot.

### 4.4 Lifecycle — reclaim on tab close
A control connection pushes foreground changes, tab closes, `LIVE_TABS`
reconciliation and extension unloads. A close tears the session down within one
round rather than merely stopping delivery.

## 5. Workloads, metrics, and current results

There are no datasets. The "dataset" is a workload configuration: tab count,
arrival order (foreground first vs last), prompt family, output cap.

### 5.1 Harnesses in the repo
| harness | what it measures |
|---|---|
| `mserver/tests/test_phase1.py` (1756 ln) | §12 correctness suite, 68 tests |
| `mserver/tests/pipeline_bench.py` | 13 whole-pipeline metrics, 3 arms |
| `mserver/tests/relative_repeat.py` | MALABR relative score, repeated |
| `mserver/tests/fidelity_check.py` | §8 output fidelity under compaction |
| `mserver/tests/cancellation_bench.py` | RQ4 wasted-compute recovery |
| `mserver/tests/kv_quant_check.py` | KV quantization fidelity cost |
| `scripts/chrome_*` (5 files) | the same questions put to Chrome's Prompt API |

### 5.2 Correctness
**68 tests, 68 passing, 17 declared browser-only skips**, green in both
partitioned and `MALABR_TEST_SHARED_KV=1` modes.

### 5.3 Whole-pipeline, 8 tabs, foreground arrives last (medians of 3 runs)
| metric | FIFO | equal-share | MALABR |
|---|---|---|---|
| foreground TTFT (s) | 16.55 | 4.22 | **0.44** |
| TTFT p50 / p95 (s) | 16.48 / 16.55 | 3.27 / 4.22 | 3.09 / 4.08 |
| inter-token p50 (s) | **0.000** | 0.213 | 0.192 |
| foreground gap p95 (s) | **0.000** | 0.287 | **0.151** |
| stalls >1 s, fg / bg | 0 / **0** | 0 / **0** | 0 / **2** |
| makespan (s) | 16.55 | 16.15 | 15.83 |
| throughput (tok/s) | 31.4 | 32.2 | 32.8 |
| **Jain fairness** | **0.998** | **0.997** | **0.631** |

Per-session service rates showing what 0.631 is: foreground **14.9 tok/s**
against ~3.6 tok/s for each of seven background tabs.

### 5.4 Relative score (foreground TTFT / median TTFT), repeated, warm-up discarded
| system | n | median | min | max | stdev |
|---|---|---|---|---|---|
| MALABR | 5 | 0.092 | 0.084 | 0.106 | 0.009 |
| Chrome Prompt API | 4 | 0.086 | 0.074 | 0.101 | 0.012 |

**Statistically indistinguishable** — the gap is smaller than either stdev and
the ordering flips with the analysis choice.

### 5.5 The four RQs, measured against Chrome
| RQ | Chrome | MALABR | differentiator |
|---|---|---|---|
| 1 isolation | passes (cross-origin, cross-tab, post-destroy) | passes | **no** |
| 2 memory governance | per-session cap 9216 tok, **constant** for 1-6 sessions; pool never divided; evicts from the FRONT, earliest content lost | pool divided, aggregate guard, **anchor preserved** | **yes** |
| 3 scheduling | priority at **admission** (late foreground still ~1.8 s), then Jain 0.99 | priority **every round**, sustained, Jain 0.63 | **partly** |
| 4 cancellation | survivors speed up 1.04x vs control 1.11x (n=7 each) — **does not reclaim** | 453 -> 0 wasted tokens, survivors 1.94x sooner | **yes** |

### 5.6 Fidelity under degradation
Dropping 58-64% of context: top-1 agreement 4/4 probes, mean KL
0.0017-0.0032 nats against a measured noise floor of ~0.0002.
KV quantization: q8_0 costs KL 0.0040 (Qwen) but **0.0343 (gemma-3)** —
model-dependent by ~10x; q4_0 changes the chosen token on 25-50% of probes and
is rejected. q8_0 also produces canary **recall false-negatives**, which
attacks the §12 methodology itself, so f16 remains the default.

### 5.7 Incidental
Chrome with Nano resident: **4.2 GB in one process** (~5 GB tree).
MALABR's KV pool: ~1.1 GB, flat from 1 to 8 sessions.

---

## 6. Assumptions the design rests on

- **A1** Exactly one tab deserves priority at any instant, and the browser can
  name it. (`foreground_tab_id` is a single int.)
- **A2** Visibility is a good proxy for human attention.
- **A3** Token count is a good proxy for both memory and compute cost.
- **A4** Whole-exchange granularity is the right unit of eviction.
- **A5** The anchor turn (turn 0) is the most valuable thing to preserve.
- **A6** 50 ms is a round budget a human cannot perceive; 1 s is a stall.
- **A7** Aggregate throughput is fixed by the hardware, so scheduling only
  redistributes it. (Supported: throughput is 31-33 tok/s in all three arms.)

---

## 7. Weaknesses and limitations

Numbered for reference by later steps.

### Technical
- **W1 — Policy is hardcoded, not parameterised.** Foreground-first is
  unconditional. There is no knob expressing "the foreground should get 2x" vs
  "10x"; the system delivers 4.1x and Jain 0.631 with no way to choose a
  different point. The fairness/responsiveness exchange rate is *measured* but
  not *controllable*.
- **W2 — Attention is binary.** One `foreground_tab_id`. A tab viewed two
  seconds ago, one viewed an hour ago, and one never viewed are all
  "background". Browsers know far more (time since interaction, is-typing,
  scroll position, audible, pinned) and none of it is used.
- **W3 — Compute is never accounted for per turn.** A turn is capped by TOKENS
  PRODUCED (`ABSOLUTE_CEILING = 512`), not by engine time consumed. Two turns
  producing 512 tokens at positions 100 and 8000 cost very different amounts of
  machine. The project's own research notes flag per-turn compute time as the
  genuinely new accounting unit; it is not implemented.
- **W4 — Memory governance is reactive, not predictive.** Compaction fires at
  95% of budget. Nothing uses the *expected* growth of a session (a page
  summarisation will clearly need more than a one-line chat) to admit or size
  it up front.
- **W5 — Background stalls are unexplained.** The pipeline benchmark shows 2
  stalls >1 s in background sessions under MALABR and 0 under both baselines.
  The cause is not isolated and the aging bound
  (`MAX_CONSECUTIVE_EXCLUSIONS = 5`) carries the comment "no measured value
  yet".
- **W6 — Two constants are judgement calls.** `SOLO_ROUND_BUDGET_MS = 150` is
  documented in-code as "a judgement call on a continuous trade-off, not a
  measured optimum". `ROUND_LATENCY_BUDGET_MS = 50` has no perceptual study
  behind it either.

### Methodological
- **W7 — Workloads are homogeneous and synthetic.** Every session gets a
  same-family prompt and the same output cap, so every session is equally
  valuable and equally expensive. Real tab populations are heterogeneous — one
  long document summarisation beside five short chats — which is precisely the
  case where governance should matter most and which is never tested.
- **W8 — The Chrome comparison is uncontrolled.** Different model, size,
  quantization, runtime and harness. Only dimensionless ratios transfer, and on
  the headline ratio the two systems are indistinguishable (§5.4). Any claim of
  being "better than Chrome" on scheduling is unsupported.
- **W9 — The equal-share baseline is MALABR with a flag flipped.** It is not an
  independent implementation of VTC or of llama.cpp `--parallel`; it is the
  same engine with `foreground_tab_id = -1`. Good for isolating the visibility
  signal, weak as evidence against the literature's actual systems.
- **W10 — No perceptual grounding.** A1, A2 and A6 are assumed. Whether users
  prefer 0.44 s foreground with background stalls over 4.2 s everywhere has
  never been asked of a user.

### Evaluation
- **W11 — Fidelity is measured narrowly.** Next-token distributions at 4
  probes, one model, after one compaction. No end-to-end answer quality, no
  multi-compaction trajectory, no task-level evaluation.
- **W12 — Adversarial evaluation stops at the canary.** Isolation is tested;
  resource exhaustion, prompt injection through extracted page context, and a
  deliberately greedy origin are not. The greedy-neighbour experiment is
  incomplete (n=1; the tab's renderer died).
- **W13 — No scaling study.** One machine, CPU only, two small models
  (Qwen3-0.6B, gemma-3-1b), tab counts 4 and 8. Nothing about how any
  conclusion moves with model size, core count, or 30 tabs.
- **W14 — Page-context extraction is unevaluated.** Readability-style
  extraction plus term-overlap relevance selection is implemented and tested
  for *robustness* (15/15 pathological-DOM checks) but never for whether the
  selected context yields better answers.
- **W15 — The browser layer is under-tested relative to the engine.** 17 of 85
  §12 tests remain browser-only skips, and they cover exactly the C++/extension
  behaviour (focus semantics, control-channel reuse, navigation mid-generation).
- **W16 — One measurement is missing outright.** Chrome's resident-memory
  growth per session (`scripts/chrome_rq2_rss.py`) has never produced a result.
  This matters because it is the measurement that would quantify the cost of
  the undivided pool found in RQ2 (§5.5).
