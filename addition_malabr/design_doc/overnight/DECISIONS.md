# Decisions taken unattended

Judgement calls made without waking Chaitanya, per the brief's instruction to
decide and log rather than ask.

## D1 — `overnight/` placed at the repository root
The brief writes paths as `overnight/PROGRESS.md`, and `git status` is run from
the repository root, which here is a **Chromium checkout** (`.../malabr/src`)
with the thesis work living under `addition_malabr/`. Putting the folder at
`addition_malabr/overnight/` would be tidier, but a fresh session following the
brief literally would look for `overnight/` at the root, fail to find it, and
redo STEP 1. Resume safety wins. The folder is untracked, so nothing tracked in
the Chromium tree is touched.

## D2 — The brief assumes an ML-modelling thesis; this is a SYSTEMS thesis
The brief asks for datasets, metrics, accuracy, losses, representations and
training strategies. This project has none of those: it is browser-scoped
**resource governance** for shared local LLM inference (scheduling, memory
governance, isolation, lifecycle reclamation). Its "results" are latency,
throughput, fairness and fidelity measurements, and its "datasets" are workload
configurations.

I have therefore mapped the brief's categories onto their systems equivalents
rather than leaving them blank:
- "new loss / representation / training strategy" -> new policy, accounting
  unit, or admission mechanism
- "datasets" -> workloads (tab counts, arrival orders, prompt sizes)
- "accuracy" -> output fidelity under degradation (KL/top-1 vs a full-context
  reference), plus correctness properties (isolation, rollback)
- "benchmarks" -> harnesses, and the baselines are real systems (Chrome's
  built-in Prompt API, llama.cpp `--parallel`, FIFO)
Cross-domain mining (STEP 3) is unaffected and is arguably more valuable here,
since OS/DB/network scheduling literature transfers directly.

## D3 — Prior measured results are reused, not re-measured
A previous session on this machine built and ran benchmark harnesses against
both MALABR and Chrome's built-in Prompt API (Gemini Nano, CPU variant). Those
numbers are already committed in `addition_malabr/design_doc/PROGRESS_REPORT.md`
and the harnesses are in the repo. STEP 1 cites them rather than re-running,
because re-running costs hours and the brief caps read-only runs at ~10 minutes.
Every number carried over is labelled with the harness that produced it so it
can be re-derived.

## D4 — One measurement is known-missing and is NOT invented
A test of Chrome's resident-memory growth per session
(`addition_malabr/scripts/chrome_rq2_rss.py`) was running when the previous
session ended and produced no output. Its result is recorded as UNKNOWN
wherever relevant. The script exists and can be run; nothing is assumed about
what it would have shown.

## D5 — No code is run that writes outside `overnight/`
The §12 test suite and the benchmark harnesses write to the repo or to `/tmp`
and some take 15+ minutes, so they are NOT run. STEP 1's understanding comes
from reading the code and the committed measurement records. Any feasibility
probe goes in `overnight/scratch/` as a new file.

## D6 — Two feasibility questions settled by reading, so no scratch probe was needed
The brief allows probes in `overnight/scratch/`. Both open questions were
answerable from the source, which is cheaper and does not risk running anything:

1. **Does `CostCurve` already expose per-position cost in milliseconds?** YES.
   `scheduler.py:103 cost_ms_worst(pos)`, `:106 cost_ms_median(pos)`,
   `:114 prefill_ms(n, pos)`, `:130 prefill_tokens_affordable(budget_ms, pos)`,
   and `:143 observe_round(estimated_ms, observed_ms)` closes the loop against
   measured time. **Consequence:** I5, I11 and I12 already have their
   measurement primitive; their effort scores drop from "build a cost model" to
   "charge against an existing one".
2. **Is there a clean policy seam?** YES, cleaner than expected. `build_batch`
   is an almost-pure function
   `(sessions, cost_curve, foreground_tab_id, budget_ms, solo_budget_ms)
   -> (decode_picks, prefill_chunks, estimated_ms)` with **exactly one call
   site** (`engine.py:318`). **Consequence:** I8 is a 1-2 day refactor, not a
   redesign, which is why it is treated as the keystone enabler.
3. **Gap found while checking:** `Session` has `produced` (tokens this
   response) but **no cumulative lifetime service counter**. I2 (virtual time)
   and I11 (deficit) each need one new field plus a charge point. Small, but it
   is a real addition rather than bookkeeping that already exists.

## D7 — Composite ranking is a heuristic and is labelled as one
STEP 5's ranking uses `(novelty + impact + fit) - (effort + compute + risk)`.
This deliberately rewards cheap, high-fit work, which is right for picking what
to do next week and **wrong** for picking a thesis. So the top-3 thesis
candidates are chosen on argument, not on the composite, and the divergence is
stated in the file. In particular I18 tops the composite while explicitly NOT
being a thesis direction — it is a result already measured that needs writing up.

## D8 — Timeline expressed in relative weeks, not calendar dates
No submission deadline was given, and the brief forbids asking. Inventing one
would make the plan look authoritative while being wrong. STEP 7's timeline is
therefore **"week 1, week 2, ..." from whenever work starts**, with a stated
total (16 weeks) that can be compressed or stretched against a real date. The
critical-path ordering is the useful part and does not depend on the date.

## D9 — I19 (perceptual study) excluded from the plan, not merely deferred
It scored −1 on the composite, needs ethics approval and 20-30 days, and a
negative result would undermine the thesis premise rather than enrich it. The
plan instead states assumption **A2 (visibility ≈ attention) as a scoped
assumption** and cites external SLO numbers (Kairos: 8 s TTFT, 50 ms TPOT) in
place of a study. If a guide insists on user evidence, the fallback named in
STEP 7 is a small within-subjects preference test on recorded traces, which
needs no live system.

## D10 — I6 (graded attention) excluded from the critical path
It needs C++ changes plus a **Chromium rebuild (8-9 h)**, and the project's own
notes record that killing VS Code to free RAM for a build also kills the working
session. The server-side simulation fallback (derive a decay from time since the
last FOREGROUND control message) gives most of the research value with no
rebuild, so the plan uses that and keeps the C++ path as optional future work.

## D11 — Decision 1 answered "both"; a unifying claim was constructed rather than
## two directions merely co-scheduled
Chaitanya chose both the systems and the security direction. Taken naively that
produces two papers in one document, which invites the question "why are these
together?". So instead of accepting both and splitting the timeline, I
constructed the claim that *requires* both:

> the scheduling policy is simultaneously an allocation mechanism and an
> information channel, because `build_batch` decides admission **from other
> sessions' state** — which is what makes it responsive and what makes it
> observable.

This is a testable claim, not a framing device: C2-RQ2 already asks whether
capacity depends on policy, and the new JOINT-RQ sweeps responsiveness ×
fairness × leakage together. Crucially **no outcome collapses "both" back to
one direction** — if capacity is policy-invariant the finding becomes "batching
itself leaks, so every batched server inherits it"; if capacity is below 1 bps
everywhere it becomes "attention-aware scheduling can be had without measurable
leakage". Both are joint statements.

Recorded in `08_BOTH_DIRECTIONS.md`. Timeline 16 -> 17 weeks (the joint sweep
reuses C1's policies and C2's harness, so it costs one week, not a phase).

**Flagged honestly:** KVGov (arXiv 2608.09225) may already characterise a
policy-vs-leakage trade-off for cloud serving. If so the novelty narrows to the
browser-origin setting plus the attention signal — still defensible, smaller.
That paper must be read before the introduction is written.
