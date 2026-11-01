# Track B — claims audit

Auditing my own output: can every number be traced, and is every statement no
stronger than its evidence?

---

## B1 — Number traceability

Checked 21 distinctive figures quoted across `overnight/` against the committed
record (`addition_malabr/design_doc/PROGRESS_REPORT.md`), committed code, and
the named harnesses.

**19 of 21 trace directly to the committed progress report.** Spot-checked:
9216 (×5 occurrences), 0.44 / 4.22 / 16.55, 0.631 / 0.997, 14.9, 0.0017 /
0.0032 / 0.0002, 453, 1.94, 1.04 / 1.11, 0.092 / 0.086, 8208, 58-64%, ~1.8 s.

**2 did not, and the finding is a hygiene problem rather than a factual one.**

| number | where it actually lives | status |
|---|---|---|
| q8_0 KL **0.0040** (Qwen) | `mserver/malabr_service/config.py:21`, committed and clean | traced |
| q8_0 KL **0.0343** (gemma-3) | same line | traced |

**Finding B1-a.** A research result — the model-dependence of KV quantization
fidelity, which is the stated reason q8_0 was *not* adopted — is recorded only
in a **source-code comment** and in `design_doc/implementation_notes.md`, which
is **untracked by design**. So on a fresh clone the only surviving record of it
is a comment inside `config.py`.

*Recommendation:* add the q8_0 per-model table to `PROGRESS_REPORT.md`. It is a
measured result that currently depends on a code comment surviving a future
refactor. **Not actioned** — `PROGRESS_REPORT.md` is outside `overnight/` and
the brief forbids touching it. Flagged for Chaitanya.

---

## B2 — Overclaim hunt

Searched every file for "first to", "first such", "nobody has", "no work",
"never been", "proves", "definitive", "guarantees that". Most hits were already
correctly hedged or were ordinary English ("first token", "cannot grow").

**Three genuine overclaims found and rewritten:**

| where | was | now |
|---|---|---|
| `04_IDEAS.md` I1 | "the **first such** measurement where the defender owns the scheduler" | states explicitly **NOT a first** — KVGov already ties scheduling to leakage; novelty relocated to the *channel* (scheduler contention, not prefix cache), the *tenancy unit* (web origin) and the *policy axis* (attention, not adversary utility) |
| `04_IDEAS.md` I16 | "the **first such numbers** for a browser shared model" | "as far as search establishes… Stated deliberately: an absence cannot be proved, and KVGov covers the adjacent prefix-cache channel" |
| `11_THE_PITCH.md` + `10_DEFENSE_STORY.md` | "**nobody has reported** that choice as a property before" | "I have not found that contrast reported as a system property elsewhere — though I say 'have not found', not 'does not exist'" |

That last one matters for the defence: the degradation-content contrast is a
genuinely good result, and claiming priority for it on the strength of a web
search is exactly the kind of thing a panel punctures. The hedged version is
still a strong claim and is defensible under questioning.

**Already-correct hedges left alone** (spot-checked, no change needed):
`02_RELATED_WORK.md`'s "no work that feeds a visibility signal into an LLM
scheduler" sits inside a paragraph headed *"Verified absence (the novelty
signal)"* that immediately says it needs manual Scholar verification;
`09_VERIFICATION.md` already **withdraws** "first to characterise the surface".

---

## B3 — Causal claims resting on one run

Audited for conclusions drawn from n=1, since this project has made that
mistake twice (recorded in `06_RESEARCH_QUESTIONS.md`).

**No surviving n=1 causal claims found in `overnight/`.** The two historical
errors are both documented *as errors* rather than repeated:
- the renderer-throttling explanation (0.12 → 0.38 on one run; 0.13 / 0.12 on
  repeat) is recorded as a withdrawn hypothesis;
- the greedy-neighbour result (1.22×) is recorded as **inconclusive, n=1**, with
  the cause named (the tab's renderer died).

**One item to keep honest:** the Chrome overflow result (front eviction, anchor
lost) is n=3 **byte-identical**, which is strong — but all three runs used the
same model, same filler text and same window. It is reproducible, not yet
*general*. `01_PROJECT_UNDERSTANDING.md` and the pitches describe it as
reproducible; none claims generality. No change needed.

---

## B4 — Cross-file consistency

Re-ran the ID check after this session's edits: **W1-16, G1-8, T1-18, I1-20 all
still defined contiguously, zero unresolved cross-references** (verified by
script, same as the STEP 8 pass).

**One inconsistency found and left deliberately:** `11_THE_PITCH.md` is
superseded by `12_THE_PITCH_WITH_PRIVACY.md`, and `07_THESIS_OUTLINE.md` §1/§5
are superseded by `08_BOTH_DIRECTIONS.md`. Both carry explicit supersession
notices rather than being deleted, so the reasoning trail survives. That is a
deliberate choice, not drift.
