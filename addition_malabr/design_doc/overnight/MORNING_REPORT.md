# Morning report

Overnight research exploration, 2026-10-04. **No code was touched** — `git
status` clean apart from `overnight/`, HEAD still `645dc13`.

---

## Top 3 thesis directions

| | pitch | origin |
|---|---|---|
| **C1** | **Make foreground priority *repaid* instead of permanent.** Borrow service for the visible tab and pay it back, so fairness survives — replacing today's unconditional preference (Jain 0.631 vs baselines' 0.997, with no knob). | **cross-domain:** OS scheduling (BVT `warp`, SOSP '99) + real-time systems (imprecise mixed-criticality) |
| **C2** | **Treat the web origin as a tenancy boundary and measure the timing channel between origins sharing one local model** — in MALABR *and* in Chrome. | **cross-domain:** security / covert-channel capacity measurement |
| **C3** | **Make the existing claims defensible:** heterogeneous tab workloads, a scaling envelope, and "what a system loses" as a metric. | in-field |

## Recommended: C1 as the spine, C2 as the distinctive chapter, C3 as the evaluation

They compose rather than compete. C1's policy seam is what lets C3 compare
policies at all, and it is also what lets C2 ask *"does channel capacity depend
on the policy?"* — which is what makes C1 and C2 one thesis instead of two.

**If only one:** C1. Lowest risk, best fit, and it repairs the objection most
likely to be raised in the viva.

## Key research questions

- **C1-RQ1** Does a warp value exist where Jain rises **0.631 → ≥0.90** while
  foreground TTFT stays **under 0.9 s** (≤2× today's 0.44 s)?
- **C1-RQ2** Is the fairness/responsiveness frontier **convex with a knee** —
  i.e. does the foreground only need priority for its first few rounds?
- **C1-RQ3** Does a guaranteed mandatory context floor remove the **2 background
  stalls** that the baselines do not produce?
- **C2-RQ1** Is there a cross-origin timing channel, and is its capacity above
  the classical **1 bps** criterion?
- **C2-RQ2** Does capacity depend on the **scheduling policy**? (If not, the
  channel is in the shared forward pass — which would apply to every batched
  server, Chrome included.)
- **C3-RQ1** Do governance effects **widen by >1.5×** under heterogeneous load?

## Biggest risks

1. **A2 — "visibility ≈ attention" is assumed, never shown.** C1's entire
   premise rests on it, and testing it properly needs a user study that is
   excluded as too risky (a negative result would undermine the thesis). The
   plan states it as a scoped assumption instead. **This is the softest joint in
   the argument.**
2. **C2's channel may be unmeasurable** on a CPU-only 0.6B model. Mitigated: a
   measured upper bound below 1 bps is a publishable safety property, so the
   direction cannot fail outright — but it would stop being the *distinctive*
   chapter.
3. **The equal-share baseline is currently MALABR with a flag flipped**, not a
   real VTC implementation. Until the policy seam exists (week 3), no difference
   can be cleanly attributed to policy.
4. **Everything rests on one machine, CPU-only, two small models, ≤16 tabs.** No
   GPU path — which is how Chrome actually runs Nano.
5. **This project has twice drawn a wrong conclusion from n=1.** Every thesis
   number needs n≥5 with spread, and warm-up discarded (first runs differ by
   4-6× from settled ones).

## Novelty claims needing manual Google Scholar verification

These are the load-bearing absences. I searched, found nothing, and **could not
prove a negative** — check them yourself before committing:

1. **"borrowed virtual time" / BVT warp + LLM serving.** Searched again and
   found no direct combination (**V2**), but a new candidate surfaced:
   **Justitia**, reported to use virtual-time fair queuing with *provable
   long-term fairness guarantees* for LLM serving `[VERIFY]`. Long-term
   guarantees are exactly what borrowing provides — **check Justitia first**. If
   it borrows, C1's novelty reduces to the attention-driven warp value rather
   than the mechanism.
2. **Tab-visibility or user-attention as an input to an LLM scheduler.** I found
   the Page Visibility API and generic background-task deferral, but nothing
   feeding it into inference scheduling.
3. **Cross-origin timing side channel on a browser-provided shared local model.**
   Narrowed by **V1**: KVGov already links scheduling to leakage in cloud
   serving, but for the **prefix-cache** channel, which MALABR structurally does
   not have (no cross-session prefix reuse; wipe-on-acquire is verified in
   code). C2's novelty is now the **scheduler-contention** channel, the
   **web-origin** tenancy boundary, and **attention** as the policy axis — a
   smaller, accurate claim.
4. **DRF applied to LLM serving.** I did *not* run that specific query, so this
   is unchecked rather than absent.
5. Several 2025-26 arXiv entries are marked `[VERIFY]` in
   `02_RELATED_WORK.md` — I read search summaries, not full texts.

## Three decisions for you (and possibly your guide)

1. ~~**Is the thesis a systems contribution or a security contribution?**~~
   **ANSWERED: both** (2026-10-04). Not as two chapters sharing a codebase, but
   under one claim — *the scheduling policy is simultaneously an allocation
   mechanism and an information channel, because `build_batch` decides
   admission from other sessions' state*. That makes responsiveness, fairness
   and leakage one trade-off surface (new **JOINT-RQ**). Timeline 16 -> 17 weeks.
   See `08_BOTH_DIRECTIONS.md`. **New first action: read KVGov (arXiv
   2608.09225)** — it may already cover a policy-vs-leakage trade-off for cloud
   serving, which would narrow the claim to the browser-origin setting.
2. **How should fairness be positioned?** The serving literature treats a high
   Jain index as the goal; your 0.631 is deliberate. Either frame it as a
   different objective function and defend it, or adopt C1's borrowing so the
   number stops being embarrassing. **Do not leave it unexplained** — a
   VTC-literate examiner will read 0.631 as a failure.
3. **Does the guide require user evidence for A2?** If yes, the excluded
   perceptual study comes back and needs ethics approval starting in week 1, not
   week 12. The cheaper fallback is a within-subjects preference test on
   *recorded traces*.

## One thing to correct from the previous session's record

The claim that **"Chrome has no per-session memory budget"** was taken from the
Chromium 127 source in this checkout and is **wrong for Chrome 153**, which
exposes `contextWindow`, `contextUsage`, `measureContextUsage()` and an
`oncontextoverflow` event. The surviving — and sharper — finding is that the
window is **never divided**: 9216 tokens per session, constant from 1 to 6
sessions, so N sessions claim 9216N. Chrome bounds a session; MALABR bounds the
machine.
