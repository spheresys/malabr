# Track A — ideas check

Resolving every claimed absence and `[VERIFY]` marker. Verdicts:
**CONFIRMED ABSENT** / **PARTIALLY TAKEN** / **ALREADY DONE**.

---

## A1 — BVT's borrowing/warp applied to LLM scheduling (C1's mechanism novelty)
**Verdict: CONFIRMED ABSENT** — and the nearest paper turns out to help rather
than hurt.

The threat was **Justitia: Fair and Efficient Scheduling of Task-parallel LLM
Agents with Selective Pampering**, arXiv
[2510.17015](https://arxiv.org/abs/2510.17015) (full text read via
[HTML v2](https://arxiv.org/html/2510.17015v2)). It uses virtual-time fair
queuing adapted from network packet scheduling, with a bounded worst-case delay
(`f_j − F̄_j ≤ 2c_max + C_max/M`), and its "Selective Pampering" sounded like
warp. Checked directly:

| question | finding |
|---|---|
| Is the boost repaid / charged back? | **No.** One-way priority, no charge-back. The paper's own framing: serve agents *"sequentially in the fair completion order with all the resources… with no agent actually delayed."* |
| Does it cite BVT / warp / Duda / Cheriton? | **No occurrences** of any of those terms. |
| What selects the favoured client? | A **deterministic predicted completion order**, computed at arrival — not a runtime signal. |
| Any UI / visibility / foreground signal? | **None anywhere.** |
| Setting / tenancy unit | Shared **GPU servers**; **task-parallel LLM agents** (one agent spawning parallel tasks), not mutually distrusting tenants. |

**What this does to the claim.** It *strengthens* the position:
1. Virtual-time queuing is now clearly established in LLM serving (VTC,
   FairServe, Justitia) — so the borrowing extension is a **legible, natural**
   contribution rather than an exotic import.
2. The specific mechanism (borrow now, repay from your own future share) remains
   untaken.
3. Justitia's core insight is almost the **opposite** of MALABR's: it argues for
   serving agents *sequentially* in fair order rather than concurrently.
   MALABR batches concurrently and prioritises *within* each round. Worth a
   sentence in related work — it is a genuine design-space contrast, not a
   competitor.

**Residual risk:** low. Still worth one exact-phrase Scholar search for
`"borrowed virtual time"` + inference before the introduction is written.

---

## A2 — KVGov and the policy-vs-leakage trade-off
**Verdict: PARTIALLY TAKEN** (recorded in `09_VERIFICATION.md` V1, unchanged).

KVGov (arXiv [2608.09225](https://arxiv.org/abs/2608.09225)) does link
scheduling to leakage and does present a leakage-vs-efficiency frontier, so
"first to characterise the surface" is **withdrawn**. But its channel is the
**prefix cache**, which MALABR structurally does not have (no cross-session
prefix reuse; wipe-on-acquire is verified in code). Its three attack families
are inapplicable here.

**RESOLVED — the replication stays on the prefix-cache path, and the paper
explicitly does not cover the contention channel.** Read §5.8 and the threat
model:

- The llama.cpp/Metal replication measures a **prefix-cache** channel: *"using
  the same streaming-TTFT method and a 781-token shared prefix, cold TTFT is
  1861 ms against 173 ms cached, a ratio of 0.093."* It shows the channel comes
  from prefix-caching mechanics rather than vLLM specifics.
- **No scheduling-contention analysis anywhere.** There is no treatment of
  interference between concurrent sequences sharing a batch or a scheduler
  queue as a *separate* side channel. Every measurement isolates whether cached
  KV was reused.
- **No browser, web origins, or consumer on-device multi-tenancy.**
- Its tenant is *"a legitimate API user… authenticated principals holding
  billing accounts or API credentials"*.

**Effect: C2's novelty confirmed on three axes, not one.**

| axis | KVGov | this thesis |
|---|---|---|
| channel | prefix-cache hit timing | **scheduler contention** (explicitly not covered) |
| setting | server-side serving (vLLM, SGLang, llama.cpp-as-server) | **browser** |
| tenant | authenticated API principal with a billing account | **any web origin the user happens to visit** |

That third row is sharper than I first framed it. KVGov's adversary must
*register* — hold credentials, pay a bill, be enumerable. A web origin needs
none of that: it is unregistered, trivially numerous, and the user did not
choose it deliberately. **That is a strictly harder threat model, and it is
specific to the browser setting.**

*Useful citable number:* their llama.cpp TTFT ratio of 0.093 (1861 ms cold vs
173 ms cached) is a reference point on the same inference stack MALABR uses.

---

## A3 — Tab visibility / user attention as an LLM scheduling signal
**Verdict: CONFIRMED ABSENT** (as far as search can establish), now with a
second independent check.

Searched in STEP 2 and again here. Results return the Page Visibility API,
generic background-task deferral, Chrome's renderer throttling, and ITO (which
uses a *model to organise tabs* — the inverse direction). Justitia's full text
contains **no** UI/visibility/foreground signal (A1). VTC, FairServe and
ProServe take priority as a declared input.

**This remains the project's strongest absence.** It is also the one that cannot
be proven by search, so it stays flagged for manual Scholar verification.

---

## A4 — DRF applied to LLM serving
**Verdict: CONFIRMED ABSENT for the direct application**, with adjacent work
that must be cited.

Searched `"dominant resource fairness" + LLM inference serving + multi-resource
+ KV cache`. No paper found applying DRF's dominant-share rule to LLM serving.
But the multi-resource framing exists in pieces, and the search surfaced **two
papers closer to MALABR than anything found in STEP 2** — these are genuine
additions to related work, not threats:

- **FairBatching: Fairness-Aware Batch Formation for LLM Inference**, arXiv
  [2510.14392](https://arxiv.org/html/2510.14392v1). Read in full. Adjacent but
  **orthogonal**: its fairness is between **prefill and decode task types**,
  not between clients; it uses **only internal signals** (SLO slack, arrival
  time, context length) with no external/attention signal; it does **not
  deliberately favour** any client, aiming instead to prevent starvation; and
  critically it **governs only compute and batch slots, not KV cache across
  clients** (that is left to vLLM's PagedAttention). Cloud GPU.
  **This is confirming evidence for RQ2's gap:** the nearest fairness-aware
  batch-formation work explicitly does not divide memory between tenants.
- **Mitigating KV Cache Competition to Enhance User Experience in LLM
  Inference**, arXiv [2503.13773](https://arxiv.org/pdf/2503.13773)
  `[VERIFY — title and framing only]`. On name alone this is the closest thing
  found to RQ2 and **must be read before Ch. 5 is written**.

Also noted for related work, unread: *Locality-aware Fair Scheduling in LLM
Serving* (2501.14312), *Robust KV Cache Management under Output Token Length
Uncertainty* (2607.16892) — the latter relevant to predictive admission (I10).

---

## A5 — remaining `[VERIFY]` markers in `02_RELATED_WORK.md`
Open. Lower priority: these are supporting citations, not load-bearing novelty
claims. Items: Equinox (2508.16646), Cascade (2608.06557), ProServe
(2512.12928), Kairos (2605.02329), SlidingServe (2606.05933), LKV (2605.06676),
KV-eviction-via-program-evolution (2608.14555), ITO (2608.09107), and the
textbook-level entries (lottery/stride scheduling, CoDel, DRR, ARC/LIRS,
Denning's working set, DB buffer-pool partitioning and query governors) which
are standard and need citation hygiene rather than verification.

---

## A6 — "Mitigating KV Cache Competition" (arXiv 2503.13773), the closest work to RQ2
**Verdict: PARTIALLY TAKEN — per-request budgets in a shared pool exist; what
MALABR does with them does not.** Abstract read (system name: **CacheOPT**).

| question | CacheOPT | MALABR |
|---|---|---|
| who competes | concurrent **requests** | mutually distrusting **web origins** (sessions) |
| pool division | per-request **estimated demand** allocated from a shared pool, with reuse of others' allocations | fixed pool divided per session, aggregate guard reclaims from the **greediest** |
| priority signal | internal only: TBT SLO, remaining time, preemption cost | **external**: which tab is visible |
| at exhaustion | **preempts a whole request** (swap or recompute, whichever is faster) | **compacts within a conversation**: keeps anchor + latest turn, drops the middle |
| setting | cloud GPU | browser, CPU |

**Effect on the claim.** "Dividing a KV pool between concurrent users" on its own
is no longer a novelty claim — CacheOPT does per-request budgeting. What remains
distinct, and should be stated precisely in Ch. 5:
1. **What is lost, not who is evicted.** CacheOPT's unit of degradation is the
   whole request; MALABR's is a span *inside* a conversation, chosen to keep the
   premise. That is exactly the degradation-content contrast already measured
   against Chrome, and CacheOPT does not make it.
2. **Tenant = origin, priority = attention** — same two axes that distinguish
   MALABR from every serving paper checked so far.

Cite CacheOPT as the nearest prior art for per-client KV budgeting and position
MALABR against it on those two points. Remaining check: read the full text to
confirm it never does partial-context retention.
