# Verification log

Checking the load-bearing novelty claims rather than assuming them. Each entry
says what was checked, by what means, and what it does to the claim.

---

## V1 — KVGov: does it already own the policy-vs-leakage trade-off?

**Checked:** abstract of *Governing the KV Cache: Preventing Timing
Side-Channel Leakage in Multi-Tenant LLM Inference*, arXiv
[2608.09225](https://arxiv.org/abs/2608.09225), fetched 2026-10-04. Abstract
only — **full text not read**.

**What it does.** More than I expected, and it must be cited prominently:
- **Defends** (not attacks) against three attack families, under one mechanism.
- Uses **per-principal salts** to make cache keys cryptographically distinct
  across tenants.
- Includes **ORIGAMI**, described as a "Stackelberg water-filling audit
  scheduler", reported to reduce adversary expected utility by 12.6% — so it
  *does* treat scheduling as a leakage lever.
- **Characterises a trade-off**: injecting the salt at a chosen boundary
  "retains an estimated 93% of the prefix-cache benefit with no cross-principal
  signal", i.e. a leakage-vs-cache-efficiency Pareto frontier.
- Setting: cloud multi-tenant (Qwen2.5-7B, vLLM 0.26.0, A100), **plus an
  independent replication on llama.cpp / Apple Metal**.

**The decisive detail: the channel is the PREFIX CACHE, and MALABR has none.**

Verified in source this session: MALABR has **no cross-session prefix reuse, no
radix tree, and no prompt cache**. `grep` for prefix/radix/prompt_cache across
`malabr_service/` returns only unrelated hits (UTF-8 prefix handling, a meta
command string). Slots are wiped **on acquire** and the wipe is *proved*:
`slots.py` calls `llama_memory_seq_rm(mem, slot, -1, -1)` and then independently
confirms `llama_memory_seq_pos_max(slot) == -1`, raising `SlotWipeError`
otherwise.

So **all three of KVGov's attack families are structurally inapplicable to
MALABR**: there is no shared prefix to probe, and its salting defence solves a
problem this system does not have.

**Effect on the claim — narrowed, sharpened, and still real:**

| | KVGov | this thesis |
|---|---|---|
| channel | prefix-cache **hit** timing | scheduler **admission/contention** timing |
| applies to MALABR? | **no** — wipe-on-acquire, verified | yes, by hypothesis |
| performance axis traded | cache hit-rate (93% retained) | **foreground responsiveness + fairness** |
| what the policy optimises | adversary utility (Stackelberg audit) | user attention (visible tab) |
| tenancy unit | principal / tenant | **web origin** |
| setting | cloud serving (+ llama.cpp replication) | browser, mutually distrusting origins |

**Verdict.** JOINT-RQ survives. It is not the first paper to link scheduling to
leakage — KVGov is, and must be credited for that — but the channel, the
performance axes traded, the policy axis and the tenancy boundary are all
different. The honest framing becomes:

> KVGov establishes that scheduling affects leakage in cloud serving, for the
> prefix-cache channel. This thesis asks the same question for a channel that
> survives the removal of prefix sharing — scheduler contention itself — in a
> setting where the tenants are web origins and the policy axis is user
> attention rather than adversary utility.

That is a smaller claim than "first to characterise the surface", and it is the
one to write. **KVGov is now the single most important paper to read in full
before the introduction** — and it is partly *supporting* evidence, since it
establishes the premise that policy affects leakage.

**Residual risk:** their llama.cpp/Metal replication means they have touched
this stack. Read that section specifically to confirm it stays on the
prefix-cache path.

---

## V2 — Is BVT's `warp` genuinely un-transferred to LLM serving?

**Checked:** targeted search for `"borrowed virtual time" / "virtual time
borrowing" / warp` + LLM inference scheduling.

**Result:** no direct combination found. The search explicitly reported no
connection between virtual-time *borrowing* and LLM inference serving
schedulers, and surfaced only the OS-side origins (including two BVT patents,
US 7065762 and US 7921422).

**But one new candidate appeared and must be checked:** **Justitia** —
described as using virtual-time fair queuing for LLM inference serving with
"provable long-term fairness guarantees" `[VERIFY — name surfaced in a search
summary; I have not located the paper itself]`. *Long-term* fairness guarantees
are precisely what borrowing provides, so this is the nearest thing to the
claimed gap. If Justitia already borrows against future service, C1's novelty
reduces to the **attention-driven** warp value rather than the mechanism.

**Verdict.** The absence stands but is **not proven**, and the candidate list
has grown by one. Priority order for Scholar verification:
1. **Justitia** (closest — virtual time + long-term guarantees for LLM serving)
2. VTC / FairServe follow-ups 2025-26 that may have added borrowing
3. `"borrowed virtual time"` + inference, as an exact-phrase search

C1 remains the recommended spine either way: even if the mechanism is taken,
the *signal* driving the warp (live tab visibility) is not, and that was already
the independently-verified absence (STEP 2 §6).

---

## V3 — Still unverified, in priority order

1. **Tab visibility / user attention as an input to an LLM scheduler** — the
   strongest absence found in STEP 2 and still only search-verified.
2. **DRF applied to LLM serving** — never queried; unchecked rather than absent.
3. Every `[VERIFY]` in `02_RELATED_WORK.md` — read from search summaries, not
   full texts.
