# MALABR Session Handoff v3 — paste the prompt below into a new session

Supersedes v2 for "what happened most recently" — v2's architecture summary
is still accurate, this covers everything added since (`phase1_design.md`
grew from 1524 to 2009+ lines in this stretch). Read v2 first if you haven't,
then this.

---

## The handoff prompt (paste this)

```
I'm an MTP student at IIT Kharagpur, working under Prof. Mainack Mondal
(secure-systems focus) on MALABR: pivoting an existing Chromium
browser-extension ML server (v3.0) into "MALABR: Browser-Managed Shared
Inference with Session Isolation and Lifecycle-Aware Scheduling."

READ IN THIS ORDER:
1. addition_malabr/design_doc/session_handoff_v2.md — architecture,
   pivot history, "how I learn" (still fully accurate, read this first if
   this is a genuinely fresh context with no prior summary).
2. addition_malabr/design_doc/phase1_design.md — THE BUILD SPEC, now ~2000+
   lines. This is what to implement. Everything past line ~1520 is NEW since
   v2's handoff — see "What's new" below for the index.
3. addition_malabr/design_doc/open_design_questions.md — entry 21 (Chrome's
   "session compacting" verified against live docs) is new since v2.
4. addition_malabr/design_doc/research_prompt_calibration_compaction.md —
   a research prompt already run externally; results partially verified,
   see "External research" section below before trusting any of it further.

## WHERE I ACTUALLY AM

Still: design complete and heavily audited for Phase 1, ZERO engine code
written yet. `engine.py` (§7 of phase1_design.md, the low-level
ctypes-driven multi-sequence llama.cpp engine) is still the correct next
step and remains unstarted — everything since v2 has been further design
hardening, not implementation.

## WHAT'S NEW SINCE v2 — READ THIS CAREFULLY, it changes real things

**1. Visibility ≠ focus — a real bug in the original design, now fixed
(§5a).** `content::Visibility::VISIBLE` only means "on screen," not "the
window with input focus." Two Chrome windows on two monitors could both
report VISIBLE for their active tab, silently letting multiple tabs claim
foreground simultaneously — breaking the whole premise of the 50/50
scheduler. RESOLVED: foreground now requires `visible AND
this_tab's_Browser* == BrowserList::GetLastActive()` — a real, verified
Chromium API (`BrowserListObserver::OnBrowserSetLastActive`/
`OnBrowserNoLongerActive`), not `RenderWidgetHostView::HasFocus()` (which
was considered and rejected — it has an address-bar-click edge case a
window-level check avoids).

**2. A dedicated control connection — a real protocol gap, now fixed
(§5e).** The original design said `visibility_changed` gets "pushed down
the socket" without specifying which one — but the protocol (§6) is
per-request sockets that close after each `generate()` call. This
CONTRADICTED §6a's tab-close design, which assumed a session's socket
could be "actively closed" — only true while a generation is actively
streaming. An IDLE tab closing had NO delivery path at all. Fixed: one
persistent control connection, separate from request traffic, opened once
per browser session, read by a dedicated thread, carrying
`visibility_changed` and idle-session `tab_closed`. This is also what
makes focus changes propagate in real time, end to end — one OS callback,
one socket write, one field update, picked up on the very next dispatch
round (the scheduler's read-side was already correct; only the delivery
path was missing).

**3. Calibration methodology corrected twice, both times by testing, not
assumption (§11a):**
   - Phase C (aggregate worst-case) was originally specified as
     "extrapolate from Phase B, spot-check." TESTED THIS DIRECTLY: 4
     sessions at depth 1500, batched, measured 12.5 tok/s aggregate — over
     3x WORSE than naive extrapolation (~41 tok/s) would have predicted.
     Batching's benefit (shares weight-read cost) doesn't extend to each
     session's own K/V-read cost, which dominates at depth. Phase C must
     now run the REAL joint measurement — extrapolation is proven
     unreliable, not just theoretically risky.
   - Tried to fit a closed-form cost model (`1/tps` linear in position) —
     doesn't hold against real multi-trial data (slope isn't monotonic:
     2.03e-5 → 1.14e-5 → 2.70e-5 → 3.86e-5). Phase B stays
     piecewise-measured, not formula-derived.
   - Quantified real trial-to-trial noise for the first time: up to 19%
     spread across 3 trials at one config. Fix: median for the output-cap
     curve, WORST-OBSERVED for the safety gate — different statistics for
     different purposes, not one number reused for both.

**4. Compaction deep-dived, corrected, and extended (§8):**
   - CORRECTED: compaction now evicts whole TURNS at chat-template
     boundaries (`<|im_start|>`/`<|im_end|>`), oldest-first — not an
     arbitrary position range, which could leave a half-formed turn with no
     closing marker. Ties directly to the chat-template application finding
     (§6c — also new: `llama_chat_apply_template` confirmed working against
     the real model, with an unresolved fidelity question about whether
     incremental per-turn application matches full-history reformatting).
   - NEW option floated, not yet built or tested: a near-zero-cost
     "stub" — truncate a dropped turn to first/last ~40 chars instead of
     deleting it, pure string op, no generation call. Explicitly distinct
     from Chrome's documented "session compacting" (verified live against
     developer.chrome.com — it's a developer-implemented pattern using the
     Summarizer API + session destroy/recreate, a REAL generation cost, not
     an engine primitive — this is now entry 21 in open_design_questions.md
     and a sharper thesis contrast than "session vs resource management").
   - Two explicitly open, untested questions logged: incremental vs. batch
     eviction, and the stub's actual fidelity — both need canary-recall
     tests before either becomes default, not decided on grounds of being
     cheap.

**5. A UTF-8 streaming bug, confirmed empirically, not hypothetical
(§6d).** Scanned the real vocabulary: 282 of ~20,000 tokens (1.4%) produce
bytes that are not valid standalone UTF-8 (byte-level BPE fallback). The
streaming design must buffer until a complete character is available
before flushing a frame — specified, not yet implemented.

**6. Several smaller but real fixes, all verified against source, not
assumed:** incoming `payload_size` has no upper bound in `protocol.py`
(exact mirror of an already-fixed bug on the response side); shutdown
traced precisely (`Terminate(0,false)` → real SIGTERM, Python's handler
genuinely cleans up, but `wait=false` means the browser doesn't confirm
completion — calibration's file write needs atomic temp-file+rename
because of this); single-instance enforcement needed (stale-socket-delete
pattern can steal from a still-live instance); `all_frames` needs stating
explicitly as `false`; a dual-gate input-size check (same constant, second
check at the actual point of no return); sampling parameters were entirely
unspecified until checked (`temperature=0` required for canary-test
determinism, separate from the real chat-facing default); extension
UPDATE (not just uninstall) confirmed to already fire the same teardown
path via `UnloadedExtensionReason::UPDATE` — no new gap there.

**7. Test suite grew from ~31 to 51 numbered tests** (§12) — all traceable
to a specific finding above, not padding.

## EXTERNAL RESEARCH — PARTIALLY VERIFIED, DO NOT TRUST FURTHER WITHOUT
CHECKING

A deep-research prompt (research_prompt_calibration_compaction.md) was run
externally and returned a report. Spot-checked several claims against
primary sources this session:

**CONFIRMED REAL** (fetched directly, not taken on faith):
- StreamingLLM (arXiv:2309.17453), PagedEviction (arXiv:2509.04377), FlowKV
  (arXiv:2505.15347) — all real papers, correct topics. FlowKV's abstract
  exactly matches its claimed mechanism (preserve old-turn KV, compress
  only newest turn — NOTE: this is the OPPOSITE of MALABR's own approach,
  which evicts old turns and keeps recent ones — related work, not
  identical, don't conflate).
- Uber `automaxprocs` GOMAXPROCS/quota benchmark numbers — fetched the
  actual README, numbers match exactly (44,715 RPS @ GOMAXPROCS=2 down to
  22,191 @ GOMAXPROCS=24, with confirmed CFS throttling). Real, external,
  independent confirmation of MALABR's own already-measured
  n_threads-must-match-quota finding.

**NOT VERIFIED — rate-limited mid-check, do not treat as confirmed:**
the report's single most actionable claim — that StreamingLLM re-indexes
cached-token positions to be cache-relative rather than original-text
positions (which would explain MALABR's own measured logit divergence in
compaction, §8) — was checked against the paper's abstract and GitHub
README and confirmed by NEITHER. May be in the paper body (too large to
fetch in one call) or may not be accurate. Roughly 10 other specific claims
in the report (vLLM FP8 ITL numbers, IISWC 2024 CPU paper specifics, Apple
MPS paper, TokenPilot, EpiCache, CacheTTL, InputSnatch/PromptPeek,
PrefixWall/CacheSolidarity/SafeKV, TransMLA/X-EcoMLA, vLLM `cache_salt`)
were NOT checked at all this session — the report reads as credible
(real-paper spot-checks passed, unlike a lot of AI-generated research which
fabricates citations) but is NOT fully verified. Next step if resuming
this thread: verify the position-re-indexing claim directly and
empirically against MALABR's own compaction code (does re-indexing
positions after `llama_memory_seq_rm`/`_add` reduce the measured
max|Δlogit|=4.56 divergence?) — that settles it more reliably than more
citation-hunting, and it's cheap to test directly.

## MACHINE / ENVIRONMENT — unchanged from v2, still true

Drive at `/media/chaitu/chaitanya` has dropped off USB **twice** now this
extended session (came back both times, no data loss, but set up a git
remote off this drive before real implementation starts — this keeps
happening). Chromium build is still unconfigured debug (`args.gn` empty) —
reconfigure before any C++ work, but Phase 1 remains implementable with
zero Chromium changes via a Python-side test harness. `n_threads=4` /
`Qwen3-0.6B-Q8_0.gguf` numbers are THIS machine's calibration results, not
portable — the whole point of §11a's calibration script is to not need
them to be.

## HOW I LEARN — unchanged, still the most important section, now with a
concrete recent example

Same as v2: verify with real tools, don't assert, push back when something
feels asserted rather than checked. This stretch's clearest example: an
external research report looked highly credible (specific numbers, real
arXiv IDs) and got spot-checked anyway — most of it held up (3/3 papers
real, Uber numbers exact), but the single most load-bearing claim couldn't
be confirmed and was flagged as such rather than passed along. Keep this up
— checking a plausible-sounding report and finding it MOSTLY right is still
worth doing, because "mostly" is exactly where a wrong load-bearing detail
hides.

## IMMEDIATE NEXT STEPS, IN ORDER

1. (Optional, cheap) Verify the StreamingLLM position-re-indexing claim
   empirically against MALABR's own compaction test from earlier this
   session, before trusting it as a fix.
2. Build `engine.py` per phase1_design.md §7 — still the one genuinely
   unknown-difficulty piece everything else depends on. Nothing found in
   this stretch changes that priority, only sharpens what it needs to do
   (chat template application §6c, UTF-8-safe streaming §6d, turn-boundary
   compaction §8, the control-connection listener §5e all live here).

Please read session_handoff_v2.md and phase1_design.md now, confirm you've
absorbed this context, then ask what to work on next.
```
