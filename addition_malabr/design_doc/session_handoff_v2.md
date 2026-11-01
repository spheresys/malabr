# MALABR Session Handoff v2 — paste the prompt below into a new session

This supersedes `session_handoff.md` (v1, 2026-08-20 morning). Everything in
v1 about the C++ codebase understanding is still true and still holds — this
document covers what happened in the (much longer) session AFTER v1 was
written: the pivot from "browser-aware scheduling" to "session isolation +
resource governance," the Chrome upstream investigation, extensive hardware
benchmarking, and a fully detailed Phase 1 implementation design.

---

## The handoff prompt (paste this)

```
I'm an MTP student at IIT Kharagpur, working under Prof. Mainack Mondal
(secure-systems focus) on MALABR: pivoting an existing Chromium
browser-extension ML server (v3.0) into "MALABR: Browser-Managed Shared
Inference with Session Isolation and Lifecycle-Aware Scheduling."

READ THESE FILES FIRST, IN THIS ORDER:
1. addition_malabr/design_doc/research_problem_statement_v2.md — the CURRENT
   research plan. Supersedes research_problem_statement.md (v1) on the
   research-question and related-work sections; v1's background/threat-model
   (§6.6-6.7) and reference list are still good and unchanged. v2 explains in
   plain language what changed and why (Chrome shipped foreground-first
   scheduling while v1 was being written; the real gap turned out to be
   memory/isolation governance, which nobody — including Chrome — does).
2. addition_malabr/design_doc/phase1_design.md — THE BUILD SPEC. 1524 lines,
   extremely detailed, extensively verified/tested (not speculative). This is
   what to implement. Read start to end before writing code — it has been
   audited across ~30 rounds of "what's the hole here" and most obvious gaps
   are already closed and recorded, including several found by testing real
   code, not just reasoning.
3. addition_malabr/design_doc/open_design_questions.md — historical record of
   HOW the design got to where it is: every idea explored, why adopted or
   rejected, with measurements. Entries 1-13 are from an earlier session;
   entries 14+ (added 2026-08-20) cover the Chrome upstream findings, the
   two-model-vs-one-model decision (rejected, measured), multi-sequence
   residency (adopted), and the VTC/Sarathi-Serve/StreamingLLM literature
   survey. Read if phase1_design.md references something confusingly, or if
   revisiting a decision — the reasoning and the rejected alternatives are
   here.
4. addition_malabr/design_doc/high_level_overview.md and
   code_structure_map.md — the ORIGINAL v3.0 codebase understanding (C++
   layer fully mapped, still accurate, unchanged by this session). Needed for
   context on what exists today vs. what phase1_design.md proposes changing.

## THE PIVOT, IN ONE PARAGRAPH

v1's headline question was "should the browser prioritize the visible tab?"
Mid-session, live-fetching current Chromium upstream (this local checkout is
pinned at 127.0.6519.0, from mid-2026) showed Chrome had ALREADY shipped
exactly that — verified in source, quoted directly (ai_manager.cc's
GetPriorityFromVisibility, on_device_model_mojom_impl.cc's RunTaskIfPossible).
That collapsed the original RQ2. But reading Chrome's code also showed what
NOBODY does: session-granular memory governance. No session cap, no memory
ceiling, no per-session budget, unbounded queue growth, dead sessions actively
PROMOTED (not just uncancelled) in the dispatch loop. That became the real
thesis: browsers now put many AI sessions in one process for shared model
residency, which is correct for memory but puts sessions below the granularity
where the OS can enforce anything (cgroups attach to processes, not to
`seq_id`s inside one process). MALABR builds the missing governance layer.

## WHERE I ACTUALLY AM

**Design is done and heavily verified for Phase 1** (isolation + memory
governance + naive scheduling with real batching). NOT YET STARTED: writing
any of the actual engine code. The next concrete step is building
`engine.py` per phase1_design.md §7 — the low-level llama.cpp driving code
(the high-level `Llama` class does NOT support multi-sequence; this must be
done through the ctypes layer, `llama_cpp.llama_cpp`, confirmed present with
everything needed: `n_seq_max`, per-token `seq_id` tagging,
`llama_memory_seq_rm/add/cp`, `llama_state_seq_save/load_file`,
`abort_callback`, `llama_set_n_threads` — all individually tested this
session, not just read from headers).

**Machine state:** Chromium build at `out/Default` is DEBUG, empty `args.gn`
(6 bytes) — this is why the original build took 8.5h. Should be reconfigured
(`is_debug=false`, `symbol_level=0`, `is_component_build=true`) BEFORE any C++
work in Phase 1's small required change set (§10 of phase1_design.md) — do
this reconfigure overnight since Phase 1 is otherwise pure Python and doesn't
block on it. `llama-cpp-python 0.3.34` is installed in a venv at
`/home/chaitu/Desktop/vscode/malabr/` (internal disk, survived a drive
failure this session — see below). Test models present:
`Qwen3-0.6B-Q8_0.gguf` (610MB, used for all benchmarking this session) and
`gemma-3-1b-it-Q4_K_M.gguf` (769MB, used once for a compaction-drift test —
result was model-specific, needs repeating on Qwen3 per open item).

**A hardware note that matters for reproducing numbers:** this machine is
8 logical / 4 physical cores, 14GB RAM. Every throughput number in
phase1_design.md is EITHER measured on this exact machine (stated as such) OR
explicitly flagged as needing startup calibration on whatever machine actually
runs it (§11/§11a — a full calibration script is specified, not yet built).
Do not assume these numbers transfer to a different machine without
recalibrating — the design explicitly does not hardcode them for this reason.

## THE BIGGEST FINDING THIS SESSION (verify before trusting it elsewhere)

Decode speed collapses hard as conversation context grows — NOT a small
effect: 48.3 tok/s at position 64, down to 5.2 tok/s at position 6000 (9x
slower). This RETROACTIVELY SCOPES every other throughput number in this
document to "measured at short context" — see phase1_design.md §11's
"CRITICAL CORRECTION" block. Two concrete design consequences already fixed
in the doc: (1) max_output_per_request must be position-aware, not a flat
constant — a fixed cap costs wildly different wall-clock time depending on
where in the conversation it fires; (2) the naive 50/50 scheduler split must
be TIME-weighted, not token-COUNT-weighted, or a deep-context background
session can dominate real CPU time while looking "fair" on paper. Both fixes
are specified in the doc; neither is built yet.

## THE OTHER LOAD-BEARING FINDING: cpu.max mechanics

Real, kernel-enforced `cpu.max` quotas (tested via `systemd-run --user
--scope -p CPUQuota=`, no root needed) genuinely work as a CPU ceiling — this
was NOT true of a `psutil`-based reactive thread-throttling approach, which
was tried, measured, and explicitly REJECTED (made throughput worse under
contention due to per-core-percentage readings being unreliable on a
hyperthreaded machine — full writeup in phase1_design.md §11). But hitting the
quota has a real latency cost: per-token p99/max roughly DOUBLES when
throttled vs. unthrottled (measured, not assumed) — this corrected an
earlier, too-optimistic "~23ms preemption bound" claim to the honest "~50ms
under real quota" everywhere it's stated in the doc.

## ARCHITECTURE, ONE SCREEN

```
content script (injected into pages, NOT popup/side panel — both lack real
  tab_id/visibility; content script gets both browser-derived, unspoofable)
    │ generate(text)                    ▲ onToken(requestId, text) — streamed
    ▼                                   │ DispatchEventToSender(proc) — process-
BROWSER PROCESS (malabr_api.cc)         │ exact, never trusts renderer-supplied
  identity ALL browser-derived:         │ listener filters (verified those are
  ext_id, tab_id (SessionTabHelper,     │ unvalidated renderer input — NOT a
  verified globally unique across       │ security boundary)
  profiles/incognito), visibility,      │
  RenderFrameHost→WeakDocumentPtr,      │
  RenderProcessHost→SafeRef             │
    │ streamed UDS frames                │
    ▼                                   │
PYTHON SERVER (one process)             │
  SessionRegistry{(ext,tab)→Session}    │
  SlotAllocator: WIPES ON ACQUIRE       │
    (not just release — the isolation   │
    fix for the demonstrated leak: a    │
    reused seq_id inherits old KV data  │
    unless explicitly cleared)          │
    │                                   │
    ▼                                   │
ENGINE THREAD (exactly one — single-    │
  threadedness is a NAMED INVARIANT,    │
  not incidental — it's what makes      │
  mid-batch cancellation impossible by  │
  construction, and any new periodic    │
  mechanism, e.g. throttle-checking,    │
  MUST run inside this loop, never on   │
  a separate timer thread)              │
    real batching (multiple seq_ids per │
    llama_decode call, measured 1.65x   │
    at 4 sessions) ─────────────────────┘
```

## WHAT'S FULLY SPECIFIED IN phase1_design.md (read it for the "why", this
is just the index)

- §1-4: UI (content script + shadow-DOM chat panel), "new chat" = real
  teardown+reacquire (not cosmetic clear), storage = nowhere durable except
  calibration data (explicit stated exception)
- §5-6: identity derivation, protocol (streamed frames, req_id vs seq_id
  distinction), the two-visibility-channel ordering rule
- §6a-6b: tab-close cancellation (WeakDocumentPtr/SafeRef pattern, matches
  Chrome's own AIManager), single-flight-per-session with KV rollback on
  replace (two snapshots: pos_before_request AND pos_before_generation)
- §7: engine loop, SlotAllocator, real batching, the single-threaded
  invariant, outbox backpressure (unbounded queue was a real gap, fixed)
- §8: memory governance, compaction (drop-middle-and-shift, tested
  mechanically correct but NOT numerically identical to fresh prefill —
  logit divergence at rank 4, likely gemma-3's sliding-window attention,
  UNVERIFIED whether this is model-specific — repeat on Qwen3), input-size
  cap, absolute output ceiling independent of calibration
- §9: naive scheduler (50/50 fg/bg, work-conserving, MUST be time-weighted
  not count-weighted per the context-cost finding)
- §10: the small C++ change set (NOT the full 8.5h rebuild — reconfigure
  args.gn first), single-instance enforcement gap (stale-socket-delete
  pattern is dangerous, needs a PID lockfile)
- §11/§11a/§11b/§11c: sizing, the full calibration script spec (5 phases +
  2 defense-in-depth phases for clamping calibration's own output), what
  happens when cpu.max is hit (detection via /proc/self/cgroup's cpu.stat,
  confirmed readable; compact the largest-position runnable session, not
  attribution-based), and an explicit audit of what safety properties do
  and don't depend on calibration/compaction (isolation and browser-
  protection do NOT; conversation length and speed do)
- §12: 31 numbered tests — this is what "Phase 1 done" means operationally

## HOW I LEARN (unchanged from v1, still the most important section)

Explain in pieces, not a whole file at once, unless asked for a full pass.
When uncertain, VERIFY WITH REAL TOOLS — this entire session was built on
"don't assume, test it," including catching and fixing several of MY OWN
mistakes in real time (a switch-cost test that gave an impossible negative
number from an unchecked decode() error; a CPU-percentage heuristic that
looked reasonable and was measured to make things WORSE; a calibration script
draft that produced a physically impossible 41323 tok/s from the same class
of unchecked-return-code bug). I will push back with "did you actually check
that" and have been right to, repeatedly, this session — keep verifying
rather than asserting, including when self-correcting.

I like concrete grounding: real numbers, real measured tradeoffs, real
rejected alternatives with reasons, not "trust me." When I ask "why does X
exist" or propose an idea, either verify it works (test it) or find precisely
where it breaks (also test it) — several ideas this session (two-model
architecture, psutil-based CPU adaptation, token-rate-as-CPU-cap) were
initially plausible and were REJECTED only after real measurement, not
argued away. That pattern — propose, test, keep or reject on evidence — is
exactly how I want this to keep working.

## MACHINE / ENVIRONMENT NOTES

- External drive (`/media/chaitu/chaitanya`) dropped off USB mid-session once
  already this session — came back fine, no data loss, but it's a known risk
  on this machine. Set up a git remote off this drive before Phase 1
  implementation starts in earnest.
- `n_threads=4` is optimal on THIS machine specifically (8 more threads is
  SLOWER — hyperthread contention, not real parallelism). This is exactly
  the kind of number the calibration script (§11a) exists to re-derive per
  machine, not assume.

## IMMEDIATE NEXT STEP

Build `engine.py` per phase1_design.md §7 — the low-level ctypes-driven
multi-sequence llama.cpp engine. This is the one genuinely unknown-difficulty
piece everything else depends on; start there, not with the C++ side (which
is small and can wait — Phase 1 needs zero Chromium changes to test with a
harness pretending to be several extensions).

Please read the four docs listed above now, confirm you've absorbed this
context, then ask what to work on next.
```
