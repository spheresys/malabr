# Control channel: what was measured where (2026-11-03)

## The defect

- The browser opens its control channel with a 5-field header,
  `ROUTE_MALABR_CONTROL,,-1,background,0` (`chrome/browser/malabr_manager.cc:96`,
  present in the built `out/Default/chrome`).
- The server's parser requires 6 fields, so it refused the header with
  `ProtocolError: expected 6 header fields, got 5` and closed the connection.
- Since the server never accepted the channel, no FOREGROUND, TAB_CLOSED,
  LIVE_TABS or EXT_UNLOADED message ever reached it from a real browser.

**Since when: always.** The control header arrived with the browser side in
`a66cd6b` (2026-08-23 01:57). The 6-field parser arrived 11 hours later in
`157d2a8` (2026-08-23 12:47). The file it replaced was FlatBuffers code that
could not even import. There was never a server that accepted this header.

**Why nothing noticed:**
- The §12 suite injects control messages on the server side (tests 10, 50,
  59), never through the browser.
- The browser-only cases that would have caught it (11, 14, 16, 17, 42, 49,
  51, 57) are declared skips.
- None of the browser scripts exercise the channel:
  - `test_panel_status.py` uses reload, Back, restart and crash;
  - `test_transcript_keys.py` runs with MalabrFeature off.

**What the real browser did instead:**
- **Foreground.** The engine starts with `control_connected = False`. Ten
  seconds after startup, `effective_foreground_tab_id()` returns −1
  (`engine.py:124-127`, `STALE_VISIBILITY_TIMEOUT`), so there is no
  foreground preference at all and every tab is served equally. The
  per-request visibility hint (`runtime.py:455-458`) sets
  `foreground_tab_id`, but the scheduler ignores it while the channel counts
  as lost.
- **Tab close.** No TAB_CLOSED or LIVE_TABS ever arrived, so the session and
  its KV slot stayed allocated until the server exited. The live run below
  shows the consequence: a third tab is refused for lack of a slot. That
  is still true after the fix, for a second reason (below).
- **Unaffected:** everything carried on the per-request connection. That
  covers rule 1 (cross-origin eviction), `new`, `status`, `switch`, stop,
  and the abandon-on-navigate socket drop.

## Live confirmation

`scripts/test_control_channel.py` (§12 test 80, by hand). It runs the
patched Chromium 127 build from `out/Default`, the real server with 2 slots,
and the real panel. Logs are in `design_doc/rq2_runs_2026-11-02/control_*.log`.

| Case | Before the fix (`HEAD`) | After the server-side fix |
|---|---|---|
| 0. control connection accepted | **FAIL**: `connection error: expected 6 header fields, got 5`, every 10 s | PASS |
| 1. activating tab A, then B, moves the server's foreground | **FAIL**: no FOREGROUND message ever | PASS: `FOREGROUND -> tab` A's id, then B's |
| 2a. with both slots held, a third tab is refused | PASS | PASS |
| 2b. after closing A, the third tab is served | **FAIL** | **FAIL**: second defect, below |

The browser's own log is misleading. It prints "MALABR: control connection
established, resynced 2 state message(s)" every time, because the socket
connected and the writes succeeded; the server closed the connection only
after reading the header.

**Second defect: TAB_CLOSED carries the wrong id.** `OnTabStripModelChanged`
prefers `removed.session_id`. For a user-closed tab, that is the id of the
"recently closed" history entry that `CreateHistoricalTab` makes
(`chrome/browser/ui/tabs/tab_strip_model.cc:456-458`), a fresh `SessionID`,
not the tab's. Measured: closing tab `1061986978` sent `TAB_CLOSED,1061986981`,
and the server cancelled 0 sessions. LIVE_TABS can't serve as a backstop,
because it is sent only when the channel (re)connects, and now it stays
connected. Fixing this needs the rebuild (rebuild item 9: use
`SessionTabHelper::IdForTab(removed.contents)`). Until then, a closed idle tab
keeps its session slot until the server exits.

## Every published scheduling and lifecycle result

"Harness" means `tests/*` driving the `Engine` in-process. Those tests set
`e.foreground_tab_id` or call `e.cancel()` directly, so they never touched the
control channel. "Browser" means a real Chromium build.

### Scheduling (RQ3)

| Result | Where published | Measured through | Still stands? |
|---|---|---|---|
| Three-arm TTFT, 8 tabs: foreground 0.32 s vs 4.19 s (equal share) vs 12.10 s (FIFO); 13.0×, 37.6×; 18.4× at 4 tabs | PROGRESS_REPORT §Scheduling | harness (`baseline_bench.py`, n_ctx 4096) | **As an engine result, yes.** Never shown in the browser; until now the browser applied no foreground preference. |
| Relative score MALABR 0.092 ± 0.009 (n = 5) | PROGRESS_REPORT, handoff v7, defence, pitches | harness (`relative_repeat.py`) | **As an engine result, yes.** The side-by-side with Chrome compares MALABR's *engine* with Chrome's *browser*. A browser-measured MALABR score would have been near equal share (~1) until this fix; not yet measured. |
| Relative score Chrome 0.086 ± 0.012 (n = 4) | same | browser (Chrome 153, `chrome_relative_bench.py`) | Yes (Chrome). |
| Whole pipeline: foreground TTFT 0.44 s, 1.9× smoother, Jain 0.631, 2 background stalls | PROGRESS_REPORT, handoff v7, defence | harness (`pipeline_bench.py`) | As an engine result, yes. |
| Foreground 14.9 tok/s vs ~3.6 per background tab | PROGRESS_REPORT, defence, pitches | harness (`pipeline_bench.py`, one run) | As an engine result, yes, but n = 1. |
| Chrome: Jain 0.987–0.994; priority at admission (foreground issued 4 s late still gets its first token in ~1.8 s) | PROGRESS_REPORT, defence, pitches | browser (Chrome) | Yes (Chrome). |
| "MALABR prioritises at every decode round" (vs Chrome at admission) | defence, pitches, handoff | harness for MALABR, browser for Chrome | **True of the engine, not yet of MALABR in a browser.** |
| §12 test 10: promotion within one round | RQ1_NOTES §3 | harness (passes the foreground tab id to `build_batch` directly) | Scheduler logic only. |

### Lifecycle (RQ4, RQ1_NOTES §3)

| Result | Where published | Measured through | Still stands? |
|---|---|---|---|
| Cancellation: 453 → 0 wasted decode tokens; watched tabs finish 1.87× / 1.94× sooner | PROGRESS_REPORT, handoff v7, defence, pitches | harness (`cancellation_bench.py`, `e.cancel`) | **As an engine result, yes.** In the browser, TAB_CLOSED never arrived. A reply in flight probably still stopped through the socket drop (abandon flag, `malabr_api.cc:174-177`), but the session and its slot were never freed. |
| Chrome does not reclaim (survivors 1.04× vs 1.11× control) | same | browser (Chrome, `chrome_rq4_cancel.py`) | Yes (Chrome). |
| Tab closed → model session deleted | RQ1_NOTES §3, handoff §2f | harness (tests 8, 50) | **Not in the browser, and still not after the server fix**: TAB_CLOSED carries the wrong id (rebuild item 9). The panel half (`bg.js`) was browser-tested and stands. |
| Tab closed while the channel was down → freed on reconnect (LIVE_TABS) | RQ1_NOTES §3 | harness (test 59) | **Not in the browser until this fix.** With the fix, LIVE_TABS is sent and reconciled on connect (seen live: `LIVE_TABS 1 live`); a close *during* a disconnect is untested. |
| Tab switched away → background priority | RQ1_NOTES §3 | harness (test 10) | **Not in the browser until this fix.** Now FOREGROUND arrives (test 80 case 1); a browser-measured priority effect is not yet measured. |
| Extension disabled → sessions deleted (EXT_UNLOADED) | RQ1_NOTES §3 | declared skip 16 | Never tested; never worked. |
| Chat on another origin in the same tab → old origin evicted (rule 1) | RQ1_NOTES §3 | harness (test 64, 73) and browser (test 72 case 2) | **Yes.** Per-request header, not the control channel. |
| Reload or same origin → kept | RQ1_NOTES §3 | browser (test 72 case 1) | Yes. |
| Back/forward cache, server restart, restart + reload | RQ1_NOTES §3 | browser (test 72 cases 2–4) | Yes. |
| "New chat" tears down the session | RQ1_NOTES §3 | harness (03, 03c); the panel path is the meta command | Yes (per-request connection). |
| Browser crash → server exits | RQ1_NOTES §3 | browser (test 72 case 5, 11/11) | Yes (`PR_SET_PDEATHSIG`, not the channel). |
| Non-browser programs refused | RQ1_NOTES §2c | browser (test 72), harness (test 75) | Yes. |
