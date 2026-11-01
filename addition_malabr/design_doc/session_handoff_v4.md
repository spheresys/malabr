# MALABR Session Handoff v4 — paste the prompt below into a new session

Supersedes v3 for "what happened most recently". v2's architecture summary and
v3's design-hardening record are both still accurate; this covers the stretch
where the **entire C++ side was written** (15 files, +1241/−524) and the design
doc grew from ~2000 to ~2950 lines.

Read v2 → v3 → this, or jump straight to `phase1_design.md` §10a if you only
need the Python contract.

---

## The handoff prompt (paste this)

```
I'm an MTP student at IIT Kharagpur, working under Prof. Mainack Mondal
(secure-systems focus) on MALABR: pivoting an existing Chromium
browser-extension ML server (v3.0) into "MALABR: Browser-Managed Shared
Inference with Session Isolation and Lifecycle-Aware Scheduling."

READ IN THIS ORDER:
1. addition_malabr/design_doc/session_handoff_v2.md — architecture and the
   pivot history. Still accurate.
2. addition_malabr/design_doc/phase1_design.md §10a — "The exact contract the
   Python side must implement." SELF-CONTAINED. If you read nothing else
   before writing Python, read this. It has both wire formats, all four
   control messages, and the three server-side rules.
3. addition_malabr/design_doc/phase1_design.md §10 — implementation status,
   and the six places the built C++ DIVERGED from what §10 originally said.
4. The rest of phase1_design.md as needed — it is ~2950 lines and is the
   build spec, not a document to read linearly.

## WHERE I ACTUALLY AM

**C++ side: WRITTEN, NEVER COMPILED.** All 15 files done. Every cross-file
reference grep-verified (signatures match, no dangling refs, every Chromium
API confirmed present in this checkout) — but grep is not a typechecker, so
the first build WILL surface include/signature errors. That is expected, not
a sign something is wrong.

**Python side: NOT STARTED.** Zero lines. This is the next work.

**The two sides do not talk yet, and I know exactly where:**
`protocol.py:143-144` splits the header into 3 fields and raises if it is not
exactly 3. The browser now sends 6. Every generate() and every control
connection is rejected today. That is the first thing to fix.

## THE PYTHON PLAN (already decided, don't re-litigate)

**Same folder — addition_malabr/mserver/malabr_service/.** Not a new one.
runtime.py and protocol.py are the direct counterparts of the C++ files we
extended rather than replaced; app.py's path is hardcoded in MalabrManager.

  runtime.py   (84)  EXTEND  — 6-field header, control route, reader thread
  protocol.py (157)  EXTEND  — the 3-field parser, frames, payload bound
  config.py    (39)  EXTEND  — n_ctx, n_seq_max, calibration path
  engine.py         NEW     — §7/§8/§9a/§9b
  calibration.py    NEW     — §11a phases A–G + new Phase B-prefill
  supervisor.py(418) GUT    — 90% is the dead sklearn fit/predict/score path,
                              whose IDL functions were already deleted
  training.py (161)  DELETE — sklearn/joblib
  storage.py  (177)  DELETE — SQLite model metadata, unused on this path

Two new files. Keep it that way — the whole point is that this stays readable.

**Build order inside engine.py** (smallest/most-safety-critical first, least
validated last):
  1. SlotAllocator + wipe-on-acquire   ← isolation; testable standalone
  2. chat template application (§6c)
  3. single-threaded engine loop, EOS / output cap
  4. compaction (§8) WITH the position-shift fix — every live absolute
     position shifts, not just s.pos
  5. §9a/§9b scheduler LAST — round-latency budget, aging, chunked prefill,
     closed-loop correction. This is the part with ZERO validation behind it.

Then protocol/runtime, then gut supervisor, then the §12 harness.

## WHAT'S NEW SINCE v3 — the C++ exists now

**Six deliberate divergences from §10's original plan**, each found by
implementing it (all documented in §10's table):
 - SafeRef<RenderProcessHost> DROPPED — SafeRef CHECK-fails when its target
   dies, turning a recoverable "destination went away" into a crash. The
   process is derived from the already-validated RenderFrameHost instead.
 - Tab close: two paths → ONE (TAB_CLOSED over the control connection covers
   streaming and idle both).
 - Per-session `visibility` → ONE `registry.foreground_tab_id`.
 - Session key gained `origin`: (extension_id, tab_id, origin).
 - Control channel: 2 message types → 4 (FOREGROUND, TAB_CLOSED, LIVE_TABS,
   EXT_UNLOADED).
 - SO_RCVTIMEO constant → base::FeatureParam (rebuilds cost hours).

**Five bugs came from ASKING ABOUT FAILURE PATHS, not from review passes** —
this is the pattern worth continuing:
 1. Five tabs each sending a request would ALL be marked foreground, because
    a per-session boolean can represent that state. One key makes it
    unrepresentable (§5f).
 2. A TAB_CLOSED lost while the control connection was down could never be
    replayed — the browser has no record the tab existed once it is gone. Hence
    LIVE_TABS reconciliation: push STATE, not events (§5f).
 3. Navigating mid-generation left the full response in the KV cache although
    the user never saw it — model context desynced from the display (§6b).
 4. **Cross-origin leak (I found this one):** chat on a bank site, navigate
    that same tab elsewhere, and the new site's content script inherited a
    session still holding the bank conversation. Session key now includes
    origin (§5g).
 5. `pagehide` as "tab-close cleanup" fires on EVERY navigation, so it would
    have wiped the chat history on every page change — destroying the exact
    feature it sat next to. Its test passed while the feature was broken;
    replaced with chrome.tabs.onRemoved (§2).

**13 further holes found by auditing the written C++ over 4 rounds**, all
fixed — including one I CAUSED in an earlier round (making the socket path
configurable turned a harmless strncpy into a silent sun_path truncation).
Full list in §10.

**Two design items were closed, one remains open by choice:**
 - Prefill blocking: FIXED by §9b's chunked prefill (Sarathi-Serve, OSDI
   2024, already in our literature survey). Prefill chunks now ride the same
   llama_batch as decode tokens, using the per-token `logits` flag — verified
   against the real llama_cpp API, not assumed.
 - §9a unmeasured: mitigated by §9b's closed loop — the engine times each
   real round and feeds a correction factor back, clamped so it can only
   become MORE conservative. A wrong cost model now shrinks batches instead
   of silently blowing the bound. Still needs tests 52/55/62/63.
 - Admission-time foreground starvation: STILL OPEN, deliberately. 8
   background tabs holding all slots reject a new foreground session.
   Hibernate-to-disk was considered and rejected for Phase 1 (reopens §4's
   privacy rule, ~214MB I/O for a depth-2000 session).

## BUILD MECHANICS — read before you build, this cost real time

**args.gn is already reconfigured** (is_debug=false, symbol_level=0,
is_component_build=true) and `gn gen out/Default` has run. The full rebuild
was started and deliberately STOPPED — the machine could not run it
alongside other work.

**Use siso directly. depot_tools' autoninja is incompatible with this
checkout.** third_party/siso is v0.2.2, older than the wrapper expects; it
injects --quiet/-local_jobs flags that version rejects, and dies instantly on
an unrecognised flag. Three attempts were lost to this. What works:

    ./third_party/siso/siso ninja -C out/Default -j 4 chrome

(Note `-j 4` with a SPACE — Go's flag parser rejects `-j4`. This siso also
warns that -j is unsupported and ignores it; harmless.)

**On incremental builds — accurate version:** after the first full build,
editing a .cc file rebuilds only that file plus a link, and
is_component_build=true keeps the link fast. BUT the first build after the
current change set will still be large, because we touched SHARED headers:
extension_function_histogram_value.h (§10's own note says it touches 41+
files transitively) and extensions/common/extension_features.h. So: the next
build is big; builds after that, touching only malabr files, are fast. Do not
expect the next one to be quick.

**out/Default/chrome exists but is from Aug 19** — it predates every change.
addition_malabr/scripts/run_malabr.sh REFUSES to launch a binary older than
the MALABR sources, deliberately, because running it silently tests old code.
ALLOW_STALE=1 overrides.

**run_malabr.sh** also assembles the FeatureParam command line, logs every
run with git SHA + exact feature string to run_history.log (§11a wants
calibration numbers traceable to their config), and uses a separate
/tmp/malabr-profile.

## HOW I WORK — unchanged, and it keeps paying off

Verify with real tools, don't assert. This stretch's proof: the SafeRef
removal, the llama_batch `logits` flag, the Chromium DELETED_ convention, the
WeakDocumentPtr invalidation rule, and the 71ns registry lookup were ALL
checked against real source or measured, and several contradicted what was
about to be written. The 71ns measurement in particular DISPROVED my own
efficiency argument for foreground_tab_id — the real justification is
structural correctness, and the design doc now says so explicitly.

I ask about failure paths, not happy paths. Five real bugs came from that.
Keep answering those questions concretely rather than reassuringly.

**Write the Python carefully.** It will be harder to debug than the C++:
a single-threaded engine loop with a control thread, ctypes into llama.cpp,
and failure modes that show up as subtly wrong output rather than crashes.
Prefer explicit over clever; comment WHY not what.

## IMMEDIATE NEXT STEPS

1. Read §10a. It is the contract; the C++ will emit exactly that.
2. Build engine.py in the order above — SlotAllocator first, scheduler last.
3. Do NOT start the full Chromium build casually; it is hours and the machine
   needs to be otherwise idle.

Please confirm you've absorbed this, then ask what to work on.
```
