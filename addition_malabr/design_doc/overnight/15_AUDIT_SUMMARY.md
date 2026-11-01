# Audit summary

Two tracks, both exhausted. What survived, what was corrected, what is still
unverifiable.

---

## Track A — the novelty claims, after checking

| claim | verdict | what changed |
|---|---|---|
| **BVT borrowing/warp not yet applied to LLM scheduling** (C1's mechanism) | **CONFIRMED ABSENT** | Justitia was the live threat and is now ruled out in full text: one-way priority, **no repayment**, selection by predicted completion order, no BVT/warp/Duda/Cheriton, no UI signal. *Strengthens* C1 — virtual-time queuing is now clearly established (VTC, FairServe, Justitia), so borrowing reads as a natural extension. |
| **Tab visibility / attention as an LLM scheduling signal** | **CONFIRMED ABSENT** (search-limited) | Second independent check. Justitia's full text adds negative evidence. Still the strongest absence, still unprovable by search — flagged for manual Scholar verification. |
| **Scheduler-contention timing channel in a browser-shared model** (C2) | **CONFIRMED ABSENT for the channel; setting and tenant also distinct** | KVGov fully resolved: its llama.cpp replication is a **prefix-cache** channel (781-token prefix, 1861 ms cold vs 173 ms cached), it has **no** scheduling-contention analysis, **no** browser/origin setting, and its tenant is an *authenticated API principal with a billing account*. |
| "First to characterise the responsiveness/fairness/leakage surface" | **WITHDRAWN** | KVGov already ties scheduling to leakage and shows a leakage-vs-efficiency frontier. Claim narrowed to channel + setting + policy axis. |
| **DRF applied to LLM serving** | **CONFIRMED ABSENT** (direct application) | No DRF-for-LLM paper found; adjacent multi-resource work exists and is now cited. |

### The sharpest thing this audit produced

KVGov's adversary is *"a legitimate API user… holding billing accounts or API
credentials"*. A **web origin** is none of those: unregistered, trivially
numerous, enumerable only by what the user happens to visit, and not chosen
deliberately. **The browser's tenancy model is strictly harder than the cloud's,
and that is a property of the setting rather than of this implementation.** This
belongs in Ch. 1 — it is the cleanest available justification for why the
problem needed restating for the browser at all.

### Two papers found that were missed in STEP 2

Neither threatens the thesis; both must be cited.

- **FairBatching** (arXiv 2510.14392) — read in full. Fairness between
  **prefill and decode task types**, not between clients; internal signals
  only; does not favour any client; and **governs only compute and batch slots,
  not KV cache across clients**. That last point is *confirming evidence for
  RQ2's gap*: the nearest fairness-aware batch-formation work explicitly leaves
  inter-tenant memory to PagedAttention.
- **Mitigating KV Cache Competition to Enhance User Experience in LLM
  Inference** (arXiv 2503.13773) `[VERIFY]` — on title and framing, the closest
  thing found to RQ2. **Read before Chapter 5 is written.**

---

## Track B — auditing my own output

**Numbers: 21 traced, 0 fabricated.** 19 to the committed progress report, 2 to
committed code.

**Finding B1-a (needs your action).** The q8_0 model-dependence result — the
*stated reason* for rejecting a ~1.9× context win — exists only in a comment at
`config.py:21` and in `design_doc/implementation_notes.md`, which is **untracked
by design**. On a fresh clone, a code comment is the sole record of a measured
result. I could not fix it: `PROGRESS_REPORT.md` is outside `overnight/`.

**Three overclaims found and rewritten — all mine, all flattering the work:**
1. `04_IDEAS.md` I1: "the first such measurement…" → states explicitly **not** a
   first, with the novelty relocated to channel/tenant/policy axis.
2. `04_IDEAS.md` I16: "the first such numbers…" → "as far as search
   establishes", with the reason spelled out.
3. `11_THE_PITCH.md` and `10_DEFENSE_STORY.md`: "nobody has reported that choice
   as a property before" → "I have not found… though I say 'have not found',
   not 'does not exist'."

The third mattered most for the defence: the degradation-content contrast is a
genuinely good result, and a priority claim resting on a web search is exactly
what gets punctured in a viva. The hedged form survives questioning and loses
nothing.

**n=1 causal claims: none surviving.** The project's two historical errors (the
renderer-throttling explanation; the greedy-neighbour 1.22×) are recorded *as
errors*, not repeated.

**Consistency:** W1-16, G1-8, T1-18, I1-20 all contiguous, zero unresolved
cross-references, re-verified after this session's edits. Superseded files
(`11_THE_PITCH.md`, `07_THESIS_OUTLINE.md` §1/§5) carry explicit notices rather
than being deleted, so the reasoning trail survives.

---

## What remains genuinely unverifiable

Stated plainly, because the honest limit of this exercise matters:

1. **An absence cannot be proved by searching.** Three claims rest on "I
   searched and found nothing": attention-as-a-scheduling-signal, the
   scheduler-contention channel in a browser, and DRF-for-LLM. Each has been
   checked twice from different angles, and each is still a negative result
   about the literature rather than a positive one about the world. **Manual
   Scholar verification before submission is not optional.**
2. **I read abstracts and full HTML, not PDFs, and no code.** Where a paper's
   mechanism mattered (Justitia's pampering, KVGov's replication, FairBatching's
   fairness axis) I read the full text and quoted it. For the remaining
   `[VERIFY]` items I read search summaries only.
3. **arXiv IDs in the 2601-2609 range** are recent enough that some may be
   preprints without peer review. Worth checking venue status before citing as
   settled.

---

## Net effect on the thesis

**Nothing in the plan changed. Three claims got smaller and more defensible, one
got sharper, and two citations were added.**

The one genuinely new argument is the tenancy asymmetry: *cloud adversaries must
register; web origins need not.* That is the strongest justification yet for why
this problem needed restating for the browser, and it cost nothing to find
beyond reading KVGov's threat model properly.
