# STEP 5 — Feasibility

## Axis polarity (stated so the table is unambiguous)

| axis | 1 means | 5 means | better |
|---|---|---|---|
| **novelty** | well-trodden | genuinely new | higher |
| **impact** | marginal | changes the thesis story | higher |
| **effort** | a day or two | weeks of work | **lower** |
| **compute** | laptop, minutes | needs hardware this project lacks | **lower** |
| **risk** | near-certain to produce a result | may well produce nothing usable | **lower** |
| **fit** | fights the codebase | drops into existing structure | higher |
| **distance** | same field (LLM serving) | very distant field | informational |

**Composite = (novelty + impact + fit) − (effort + compute + risk).** A
heuristic, and a biased one: it rewards cheap high-fit work, which is correct
for choosing next week's task and *wrong* for choosing a thesis. The top-3
candidates below are therefore argued, not read off the table — see §4.

Two facts established by reading the source (D6) change several scores:
- `CostCurve` **already returns milliseconds** (`cost_ms_worst`, `prefill_ms`,
  `observe_round`), so cost-denominated ideas do not need a cost model built.
- `build_batch` is an almost-pure function with **one call site**
  (`engine.py:318`), so the policy seam is cheap.
- `Session` has **no cumulative service counter** — virtual-time and deficit
  ideas each need one new field plus a charge point.

---

## 1. Scores

| ID | idea | nov | imp | eff | cmp | risk | fit | dist | **comp** |
|---|---|---|---|---|---|---|---|---|---|
| I1 | cross-origin timing channel | 5 | 5 | 3 | 2 | 3 | 4 | **5** | 6 |
| I2 | borrowed foreground priority (BVT warp) | 4 | 5 | 2 | 2 | 2 | 5 | **4** | **8** |
| I3 | mandatory/optional context floor (IMC) | 3 | 4 | 2 | 1 | 2 | 5 | **4** | 7 |
| I4 | DRF over compute + context | 4 | 4 | 4 | 2 | 3 | 3 | 4 | 2 |
| I5 | per-turn compute accounting | 4 | 3 | 2 | 1 | 2 | 5 | 2 | 7 |
| I6 | graded attention signal | 4 | 3 | 4 | 1 | 3 | 2 | 3 | 1 |
| I7 | CLOS-style session classes | 3 | 3 | 3 | 1 | 2 | 4 | 4 | 4 |
| I8 | policy/mechanism seam | 1 | 5 | 1 | 1 | 1 | 5 | 2 | **8** |
| I9 | isolation posture as a dial | 3 | 4 | 2 | 2 | 3 | 5 | 4 | 5 |
| I10 | predictive admission from prompt size | 3 | 3 | 2 | 1 | 2 | 5 | 3 | 6 |
| I11 | cost-denominated deficit round robin | 3 | 3 | 2 | 1 | 2 | 5 | 3 | 6 |
| I12 | CoDel-style delay-targeted admission | 3 | 3 | 3 | 1 | 3 | 4 | 4 | 3 |
| I13 | per-model KV fidelity gating | 3 | 3 | 2 | 2 | 2 | 5 | 2 | 5 |
| I14 | working-set admission | 3 | 4 | 3 | 1 | 3 | 4 | 4 | 4 |
| I15 | heterogeneous workload benchmark | 2 | 5 | 2 | 3 | 1 | 5 | 1 | 6 |
| I16 | timing-channel harness + ablation | 4 | 5 | 3 | 3 | 2 | 4 | 4 | 5 |
| I17 | adversarial-origin suite | 3 | 4 | 3 | 3 | 2 | 4 | 2 | 3 |
| I18 | "what does it lose?" as a metric | 4 | 4 | 1 | 2 | 1 | 5 | 2 | **9** |
| I19 | perceptual study | 4 | 4 | 5 | 1 | 5 | 2 | 3 | −1 |
| I20 | scaling study | 1 | 4 | 3 | 5 | 2 | 5 | 1 | 0 |

---

## 2. Per-idea detail

Files named from read-only analysis of `addition_malabr/`.

| ID | files/functions that would change | days to first result | main risk | fallback |
|---|---|---|---|---|
| **I1** | none to measure; new `scripts/chrome_timing_channel.py` reusing `scripts/nano_cdp.py`. Read-only on `engine.py` round loop | 4–6 | channel too noisy on CPU to exceed 1 bps | a measured **upper bound** is itself a safety result, and still publishable |
| **I2** | `scheduler.py::build_batch`; new `Session.vtime` field (`session.py`); charge point in `engine.py::_sample_and_emit` | 3–5 | warp may not restore fairness without surrendering TTFT | report the **Pareto curve** (warp × fairness × TTFT) — useful even with no dominating point |
| **I3** | `engine.py::compact`, `engine.py::_relieve_aggregate_pressure` | 2–4 | the guarantee may be unprovable without bounding arrivals | drop to an **empirical** claim: stalls eliminated, no formal proof |
| **I4** | `scheduler.py` (new policy), `engine.py::_relieve_aggregate_pressure`; needs demand vectors | 7–10 | with 2 resources and 8 clients DRF may not change observable behaviour | narrow the claim: *demonstrate* the compute-starved/memory-rich state exists, then show DRF removes it |
| **I5** | `session.py::OutputCap`, cap check in `engine.py::_sample_and_emit` | 2–3 | on a 0.6B model cost may vary too little across depth to show an effect | derive the unfairness **analytically** from `CostCurve` without changing policy |
| **I6** | `chrome/browser/malabr_manager.cc`, `extensions/common/extension_features.cc`, `protocol.py`, `engine.py` — **needs a Chromium rebuild (8–9 h)** | 10–14 | rebuild cost dominates; C++ plumbing | **simulate server-side**: derive a decay from time since the last `FOREGROUND` control message — no rebuild, no C++ |
| **I7** | `scheduler.py`, `config.py` | 4–6 | may be refactoring with no measurable result | present as design contribution + ablation host |
| **I8** | `scheduler.py::build_batch` → strategy object; one call site `engine.py:318` | 1–2 | almost none | — |
| **I9** | none new; runs the §12 suite and I16 in both KV modes | 2–3 *after* I16 | partitioning may not narrow the channel (CAT's own documented weakness) | a null result still characterises the mode honestly |
| **I10** | `runtime.py::handle_generate`, `engine.py::_begin_turn` | 2–3 | prompt length is a weak predictor of total growth | predict from prompt length **plus** turn count; report prediction error |
| **I11** | `scheduler.py::build_batch`; new deficit field in `session.py` | 2–3 | may be indistinguishable from the current aging pass | report both; "no difference" is a finding about aging |
| **I12** | `scheduler.py::build_batch`, replaces two constants | 4–5 | AQM tuning is subtle; could regress TTFT | keep the fixed budget as a floor, apply CoDel only above it |
| **I13** | `config.py::KV_TYPE` gate, `tests/kv_quant_check.py` | 2–3 | threshold choice is arbitrary | publish the per-model table and let the operator choose |
| **I14** | `scheduler.py::build_batch`, `engine.py` admission | 4–6 | W5's stalls may not be thrashing at all | if not thrashing, the diagnosis itself resolves W5 |
| **I15** | new `tests/workload_gen.py`; extends `tests/pipeline_bench.py` | 2–3 | none substantive | — |
| **I16** | new `scripts/chrome_timing_channel.py` + MALABR-side twin | 3–5 | cross-process timing noise | reduce to a same-process measurement first to establish the ceiling |
| **I17** | new `scripts/adversarial_origin.py`; fixes the n=1 greedy test | 4–6 | renderer crashes under load (**already observed**) | drive the greedy side from Python over UDS rather than from a renderer |
| **I18** | none — **write-up of existing measurements** | 1–2 | none | — |
| **I19** | none (human study) | 20–30 **+ ethics approval** | a negative result undermines the thesis premise | run it **last**, after the systems claims stand on their own |
| **I20** | none; parameter sweep over `tests/pipeline_bench.py` | 5–7 | needs bigger models and more cores than this machine has (14 GB, 8 cores, CPU-only) | sweep what is reachable (tab count 1–16, both models) and state the envelope |

---

## 3. Distant transfers that still score well — the strongest novelty claims

The brief asks for these to be flagged explicitly.

| ID | distance | composite | why it survives |
|---|---|---|---|
| **I1** | **5** | 6 | Security → browser inference. The only verified absence in STEP 3. Needs no engine changes to *measure*, and a negative result is still a safety property. |
| **I2** | **4** | **8** | OS scheduling → LLM. Highest composite of any cross-domain idea. 25-year-mature mechanism, fits the existing seam, fixes the single biggest weakness. |
| **I3** | **4** | 7 | Real-time → context governance. Formalises behaviour that already exists, so effort is low and fit is perfect. |
| I9 | 4 | 5 | Architecture/security → KV modes. Nearly free given I16. |
| I16 | 4 | 5 | Operationalises I1. |

That I2 and I3 are both distant **and** cheap is unusual and is the most
exploitable fact in this analysis: they import mature theory into a field that
has not used it, at low implementation cost, on a codebase already shaped to
receive them.

---

## 4. Top 3 thesis candidates

Chosen by argument, not composite (D7). The composite's winner, **I18, is
deliberately not a candidate** — it is a result already measured that needs
writing up, so it should be banked in week 1 regardless of direction.

### Candidate 1 — I2 + I8 + I3: *governed* attention-aware scheduling
**The core contribution.** I8 (1–2 days) creates the policy seam; I2 puts
borrowed virtual time behind it; I3 guarantees a context floor so background
sessions degrade rather than starve.

*Why this is the thesis:* it fixes the weakness most likely to sink the viva.
Right now foreground-first is unconditional and unrepaid — Jain **0.631**
against baselines' **0.997**, with no knob. A VTC-literate examiner reads that
as a fairness failure. With borrowing, the claim becomes *"a transient,
repaid priority that preserves long-run fairness"*, which is a defensible
position rather than an awkward one. I8 additionally repairs **W9**: every
baseline (VTC, DRF, foreground-first) becomes a real implementation on
identical plumbing, so differences are attributable to policy.

*Total: ~8–12 days to first results. Risk: low. Fit: highest in the set.*

### Candidate 2 — I1 + I16 + I9: timing channels in browser-shared inference
**The distinctive contribution.** The only verified gap, and the only one that
changes the project's threat model rather than its performance numbers.

*Why it is strong:* it reframes RQ1 honestly. The current result — "isolation
passes, same as Chrome" — is a pass on the **content** channel while the
literature (InputSnatch, PromptPeek/NDSS '25) shows the realistic attack is
**timing**. MALABR is unusually well placed: origin-keyed sessions, two KV modes
to compare, an existing CDP rig, Chrome available as a second subject, and —
uniquely — the defender owns the scheduler and can instrument it.

*Risk is real and asymmetric:* the channel may be unmeasurable on a CPU-only
0.6B model. But a measured **upper bound** below the classical 1 bps criterion
is a publishable safety property, so the direction does not fail outright.

*Total: ~9–14 days. Risk: medium. Novelty: highest.*

### Candidate 3 — I15 + I20 + I18: make the existing claims defensible
**The insurance policy, and more necessary than it looks.** Heterogeneous
workloads (I15), a scaling envelope (I20), and degradation-content as a metric
(I18).

*Why it earns a slot:* every quantitative claim in the thesis currently rests
on homogeneous synthetic load at two tab counts on one machine with one model
family (**W7, W9, W13**). That is the easiest thing in the project to attack and
the cheapest to fix. I15 in particular is where governance *should* show its
largest effect — one long summarisation beside five short chats — and it has
never been run.

*Total: ~8–12 days. Risk: lowest. Novelty: lowest.*

### Recommendation
**Candidate 1 as the spine, Candidate 2 as the distinctive chapter, Candidate 3
threaded through both as the evaluation.** They compose rather than compete:
I8's seam is what lets Candidate 3 compare policies at all, and I15's workloads
are what make Candidate 2's contention measurements realistic. Sequencing is in
STEP 7.

**If only one can be done:** Candidate 1. It is the lowest-risk, best-fitting
work, and it repairs the objection most likely to be raised.
