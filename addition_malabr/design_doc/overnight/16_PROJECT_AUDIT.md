# Whole-project audit — 2026-10-28

Code changes are committed locally (`41a06a3`, `f22bf07`), not pushed, and
verified: §12 suite 68/68 in both KV modes, page-context 9/9 and 15/15, and
`kv_quant_check.py` reproduces its earlier numbers exactly (0.0040 / 0.6278).

---

## 1. Most urgent: the project's results exist on one flaky disk

The external SSD (JMicron JMS578 bridge, `uas` driver) dropped mid-run at
12:50:55. The kernel logged `JBD2: I/O error when updating journal superblock`,
the device re-enumerated as `sdb`, and the running test suite got `error -5`
reading source files, which produced 7 spurious failures. After remounting, the
journal replayed cleanly, `git fsck` passed, and every file checked intact. This
time nothing was lost.

But the audit found that **almost everything that matters lives only on that
disk:**

| what | where | off this disk? |
|---|---|---|
| all measured results (`PROGRESS_REPORT.md`), RQs, handoffs, this research | `design_doc/` — **gitignored** (`addition_malabr/.gitignore:10`) | **no** |
| the Sept "legacy backup" | same drive | **no** |
| ~28 commits since `06f636f` (all Chrome-comparison harnesses, all fixes since) | local branch, **unpushed** | **no** |

**Correction to an earlier recommendation.** The previous audit (B1-a) said to
move the q8_0 result into `PROGRESS_REPORT.md`. That file is untracked too, so
the move would have made nothing more durable. It now lives in the tracked
`mserver/NOTES.md` under `config.KV_TYPE`.

**Recommended, in order (all yours to do):**
1. **Push `chaitanya/v0`.** One command removes ~28 commits of single-copy risk.
2. **Copy `design_doc/` off the drive** (internal disk, cloud, or a private
   repo). It is 3.3 MB.
3. **Add `usb-storage.quirks=152d:0578:u` to the kernel boot parameters.** That
   forces this bridge onto plain usb-storage instead of UAS: slightly slower,
   but the standard fix for this dropout. It's a boot-config change, so I
   haven't applied it.
4. At some point run a full `sudo fsck -f /dev/sdb1` with the drive unmounted.
   The journal replay made the filesystem consistent, but it isn't a full check.

---

## 2. Code changes (committed)

**`41a06a3` — cleanup**
- Unused imports removed from three production modules (`config.field`,
  `runtime.ROUTE_GENERATE`, `session.FRAME_*`), checked not to be re-exported,
  and from five scripts and tests.
- The duplicated measurement helpers (`softmax`, `kl`, `js`, `topk`, `pct`),
  copy-pasted across four scripts, now live in one `tests/metrics.py`. Before,
  a fix to one copy would have left the others measuring the old way.
- A committed turn now clears its formatter checkpoint. It was never read again,
  but compaction kept shifting a dead value.

**`f22bf07` — convention**
- The q8_0 result was written up properly in tracked `NOTES.md`, including the
  canary false-negative finding the code comment had omitted.
- Five long inline comments I wrote last month broke your "the why goes in
  NOTES.md" convention. They were moved verbatim, and the code now has one-line
  hints.
- Every `[notes: …]` pointer in the codebase resolves (checked by script).

**Sweep result: no new off-by-one.** Last month's compaction bug hid behind
checks that only compared lengths. I swept the remaining index bookkeeping
(`pos_before_request`, `pos_before_generation`, `formatter_cp_before_request`,
and the boundary shifts) for the same class of bug. All correct: the checkpoint
is a message *count* taken at a pair boundary, and the positions are guarded by
an explicit "lies inside dropped range" check.

**`e6ca683` — chat-panel transcript bug (the one real bug this audit found)**

The panel's reload copy of the conversation was keyed by **origin only**, but
the server keys the conversation by (extension, tab, origin). I confirmed three
consequences in a real browser against the old code:

| Old behaviour (HEAD `f22bf07`) | Why it matters |
|---|---|
| A new tab on the same site showed another tab's transcript | The display/model desync that section 2 exists to prevent: the panel shows turns this tab's model never saw. |
| After a tab was **closed**, the next tab on that site still showed its conversation | Contradicts the privacy story's "closing a tab wipes it". The server did wipe it; the display copy did not. |
| This extension's content script on *any* site could read *every* site's transcripts | `bg.js` had opened `storage.session` to content scripts, so a compromised renderer for one site could read all of them. |

The fix: content scripts no longer touch storage. They message `bg.js`, which
builds the key from `sender.tab.id` and `sender.origin`, both attributed by the
browser. That is the same rule `malabr_api.cc` applies to the KV key. `bg.js`
also deletes a tab's keys on `tabs.onRemoved`, and `storage.session` goes back
to Chrome's default (trusted contexts only).

New `scripts/test_transcript_keys.py` covers it. It loads the real extension into
the patched Chromium with MalabrFeature off, so there is no model server and the
run is cheap, and drives the real `bg.js` over CDP. Results: **8/8** on the new
code; on the old code the first check fails with the full store readable.

> Thesis note: if your privacy story says "closing a tab erases the
> conversation", that was true of the model but not of the panel until this
> commit. It is now true of both.

**`1ae2a24` — launcher (`run_malabr.sh`)**
- The run log claimed to "record exactly which config produced the run" but saved
  only `--enable-features`. Since the scheduler moved into Python, most of the
  config is `MALABR_*` env, which is now logged too.
- The staleness guard watched 5 paths, but MALABR also touches
  `chrome_browser_main.cc`, `chrome_features.cc/.h`, `extension_features.h` and
  the API JSON files. Those are now guarded. None is newer than the current
  binary, so this blocks nothing today (checked).
- Removed the commented "future FeatureParams" (round budget etc.). Those are
  Python constants now, and one disagreed with the code (2000 ms vs
  `STALE_VISIBILITY_TIMEOUT = 10.0` s).
- Tested with a fake browser binary: the guard fires, the log line is correct,
  and the `exec` is reached.

---

## 2b. Browser side (C++): first audit, no edits

The earlier audits covered only the server and the panel. I read all of
`malabr_api.cc`, `malabr_manager.cc`, `mserver_uds.cc`, `msocket_uds.cc` and
`malabr.idl`. **I made no C++ edits:** any change trips the staleness guard and
needs an 8–9 h rebuild before it can be verified.

**Sound (checked, not assumed):**
- **Identity:** tab, origin and visibility all come from the browser; the IDL
  takes only `prompt`. Opaque origins are refused in both `generate` and `stop`.
- **Wire safety:**
  - The 1 MiB frame bound is checked before allocating.
  - Reads and writes loop until complete.
  - `SO_RCVTIMEO` is set.
  - SIGPIPE is ignored process-wide (`content_main.cc:90`), so a dying server
    gives EPIPE rather than killing the browser.
  - `Connect()` closes its fd on every failure, so the 180 s retry loop leaks
    nothing.
- **Lifetime:**
  - `AddRef`/`Release` are balanced, with one terminal frame per request.
  - Weak document pointers protect against a tab dying mid-stream.
  - Profile shutdown is checked before dispatching events.
- **Control channel:**
  - Resync sends state, not replayed events (`FOREGROUND` and `LIVE_TABS`).
  - The duplicate-suppression after a handshake can't drop a real change,
    because messages are sequenced.
  - Moving a tab to another window is not treated as a close.
- **Socket:** `chmod 0600`, and bound under a pid lock.

**Findings, ranked (all one-liners, but all need a rebuild):**
1. **The server outlives a crashed browser.**
   - Cause: `LaunchOptions.kill_on_parent_death` is not set.
     `StopMLServer()` runs only on clean shutdown, and the launcher never kills
     a stale server. The next server's pid lock makes it exit (code 3) while
     the orphan keeps serving.
   - Mitigated: Chromium pads tab ids after an unclean exit
     (`session_id_generator.cc`), so the next browser's `LIVE_TABS` frees every
     orphan session (test t59).
   - Still exposed in two ways:
     - If the browser isn't restarted, the conversation stays in the orphan's
       RAM indefinitely.
     - The orphan runs whatever code it started with.
   - Fix: `options.kill_on_parent_death = true;` in `StartMLServerIfEnabled`.
   - A Python-only alternative, `prctl(PR_SET_PDEATHSIG)` in `app.py` when the
     parent is chrome, would avoid the rebuild. But it changes standalone/test
     launches, so it's your call.
2. **A tab closed during a long prefill keeps the server busy.** The abandon
   flag is raised only when the next token arrives, so a closed tab's prompt
   still finishes prefill (up to seconds). `TAB_CLOSED` on the control channel
   covers it in practice. The race is only between the two paths.
3. **Foreground isn't recomputed on visibility alone.** The manager reacts to
   window activation and tab switches, but not to a window being occluded or
   minimized without an activation change. The foreground tab can then stay
   "foreground" while hidden. Fix: a `WebContentsObserver::OnVisibilityChanged`
   on the active tab.
4. **`EXT_UNLOADED` isn't profile-scoped.** Disabling the extension in one
   profile also ends its sessions in another profile. Harmless, but imprecise.
5. **Cosmetic:**
   - `mserver_uds.cc:292-294` still says opaque origins are sent as "null"
     (they are refused: design-doc correction #9).
   - The socket default is still named `malabr_v3.sck`.
   - `malabr.idl` carries a "2024 The Chromium Authors" header.

**Legacy C++ is still compiled in but inert.** `ml_server_manager.cc`,
`ml_server_uds_manager.cc` and the `read_server_uds` extension API are built
and started from `chrome_browser_main.cc`. Their features are
`DISABLED_BY_DEFAULT`, so nothing runs. Removing them is a rebuild-sized change
with no runtime effect, so it's worth doing only alongside fix 1.

---

## 3. Deliberately not done

**Legacy directories, pending your decision.** These were verified dead: they
call the old train/predict/flatbuffers API, and nothing references them.
- `baseline/`
- `benchmark_data/`
- `plots/`
- `reading server extension/` and `read_server_uds_extension/` (near-duplicates
  of each other)
- `uploads/` (11 MB)
- `mserver/malabr_test_extension/`
- Two stale learning docs: `learning_and_setup/extension_flow_step1.md` and
  `extension_testing_guide.md`. Your own PDF and notes in that folder are
  untouched.

Not deleted, because it's a shared repo and `baseline/` may be cited.

**The Chrome-harness helpers stay duplicated.** `launch`, `serve` and
`kill_stale` are copied across 4–5 scripts in `scripts/`, which is real
duplication. I left it because those scripts produced the committed Chrome
results. Refactoring them means the recorded numbers no longer come from the
exact code in the repo. They also can't be re-run cheaply here: Chrome with Nano
resident takes 4.2 GB and crashed VS Code earlier. If you refactor them, re-run
F6–F9 afterwards.

---

## 4. Novelty refresh (October 2026)

Nothing new threatens a claim. Four findings, two of them helpful:

| source | what it does | effect |
|---|---|---|
| Chrome's own [session-management guide](https://developer.chrome.com/docs/ai/session-management) | tells **page authors** to destroy unused sessions to "raise resource availability" | **supports** RQ2/RQ4: Chrome hands governance to each page's good behaviour, which is direct evidence for "governance is absent" |
| Edge [Prompt API docs](https://learn.microsoft.com/en-us/microsoft-edge/web-platform/prompt-api) | the same API, with the model "shared across all websites" | **supports generality**: the setting isn't Chrome-only |
| *Orchestrating On-Device LLM Inference for End-User Multi-Agent Systems*, arXiv [2610.03394](https://arxiv.org/abs/2610.03394) (abstract) | on-device; several agents share one model | the nearest *setting* yet, but its consumers **cooperate** inside one user's workflow: no trust boundary, no per-consumer KV budget, internal signals only, no browser. On-device multi-tenancy is now active research; yours is the **distrusting** case |
| *Token Latency Fairness / FairInference*, arXiv [2609.18112](https://arxiv.org/abs/2609.18112) (abstract) | guarantees each well-behaved client `d → d + δ` slowdown via per-token deadlines; cloud GPU | the **opposite** design choice (you deliberately slow background tabs), and a bounded δ caps a timing channel, so it's a citable **defence** for C2 |

The search summary claimed FairInference "protects each client's KV cache
state". That phrase isn't in the abstract. I'm noting it because it's exactly
how a wrong citation gets into a thesis.

---

## 5. Design improvements, in order

These are the recommendations from the research files, re-ranked after today.
None is implemented; they're research decisions.

1. **Policy seam (I8, 1–2 days).** Turn `build_batch` into a strategy object; it
   has one call site, `engine.py:318`. Everything below depends on it, and it
   fixes the weakest part of the evaluation: "equal-share" is currently MALABR
   with a flag flipped.
2. **Borrowed foreground priority (I2, BVT warp).** Fixes the fairness number
   (Jain 0.63). Still the confirmed-absent mechanism.
3. **A δ-style bound for background tabs.** New from FairInference: guarantee
   hidden tabs a bounded slowdown instead of an unbounded one. It attacks the
   fairness objection *and* caps the timing channel at the same time, which
   makes it a natural bridge between C1 and C2.
4. **Mandatory context floor (I3).** Explains or removes the 2 background
   stalls.
5. **Heterogeneous workload benchmark (I15).** The cheapest fix for the most
   attackable part of the evaluation.
6. **A tracked results file.** Not research, but it belongs in the design: the
   measurements behind the thesis should live in git next to the harnesses
   that produce them, not in an ignored folder.
