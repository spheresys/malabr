# MALABR v3: design decisions

This file records why MALABR is built the way it is. Each decision gives the
alternatives that were considered, what the decision costs, and the evidence
behind it. Part H records the decisions that were later reversed or turned
out to be wrong, and what each one taught. Part I is a one-page summary.

"v3" means the shared-LLM system built since Phase 1 (2026-08-23): the
patched Chromium, the Python server in this directory, and the chat
extension. The older "v3.0" FlatBuffers/sklearn server it replaced appears
here only as a rejected alternative (D3).

## Conventions

- **Paths.** Paths are relative to `addition_malabr/mserver/`. Exceptions:
  paths starting with `design_doc/` or `scripts/` are relative to
  `addition_malabr/`, and C++ paths (`chrome/…`, `extensions/…`) are relative
  to the Chromium root.
- **Line numbers** are as of commit `b0273d3` (2026-11-03).
- **Sources and their short names:**
  - "§n" is a section of `design_doc/phase1_design.md`, the Phase 1 design.
  - "ODQ §n" is `design_doc/open_design_questions.md`.
  - `NOTES.md <entry>` is the rationale entry for that code symbol.
  - "M1" to "M11" are the open limitations in `RQ2_NOTES.md` §6.
  - "L1" to "L9" are the open limitations in `RQ1_NOTES.md` §5.
  - "Rebuild item n" is the C++ list in `RQ1_NOTES.md` §6.
- **Alternatives** are those recorded in the code, the history or the docs.
  One marked *(inferred)* is not recorded anywhere; it is named here because
  a reader would ask about it.
- **Status** is one of: in place; changed (when and why); proposed; on the
  rebuild list (C++, needs the 8–9 h Chromium build).
- **The conversation log**, `design_doc/conversation_log_2026-09-22_to_11-01.md`,
  is kept local, not in the repo. It is cited only where nothing tracked
  records the fact.

## Contents

- **A. Architecture:** D1 one shared model · D2 native server · D3 socket and
  protocol · D4 two channels · D5 panel in the tab · D6 prompt only
- **B. Identity and isolation:** D7 session key · D8 wipe on acquire (D9
  merged into it) · D10 lifecycle rules · D11 server locked to its browser
- **C. Memory:** D12 pool sizing · D13 tokens as the unit · D14 compaction ·
  D15 aggregate guard · D16 KV type
- **D. Scheduling:** D17 batching under a round budget · D18 foreground first ·
  D19 prefill from leftover budget · D20 CostCurve
- **E. Lifecycle:** D21 cancel on tab close
- **F. Method:** D22 canaries with both controls · D23 baselines
- **G. Not in the original list:** D24 one engine thread · D25 turn-atomic
  context · D26 nothing on disk · D27 template-derived prefill · D28
  server-owned output cap · D29 fixed slots, refuse when full · D30 commands
  inside `generate()` · D31 CPU limits
- **H. Reversed or wrong:** R1–R14
- **I. Summary table**

---

## A. Architecture

### D1. One shared model per browser

**Decision.** The browser owns one loaded model and one KV cache, and every
tab, extension and origin shares them.

**Context.** Today each extension or site that wants an LLM loads its own
copy. N copies cost N times the weight memory and contend for one memory bus,
and no one can schedule across them or cancel work for a closed tab
(`design_doc/research_problem_statement.md` §3.1, Problems 1–3).

**Alternatives considered.**
- *Each client keeps its own model* (the status quo). Rejected: with no shared
  owner there is no deduplication, scheduling or cancellation (same section).
- *WebLLM with a SharedWorker.* Rejected: it serves one origin only, its
  clients report their own visibility unverified, and it gets no reliable
  tab-close signal (`research_problem_statement.md:54-58`).
- *Two models, one for the foreground and one for the background.* Rejected
  on measurement. The pair gave 41.6 tok/s against 43.4 for one model,
  because decode is bandwidth-bound. It also caps the foreground at about half
  the machine: 21.0 tok/s, against 43.4 for one model run strictly
  foreground-first (ODQ §7).
- *A process per tenant, each with real cgroups.* Rejected: N processes undo
  the shared-residency claim unless the weights are shared through mmap
  (`research_problem_statement.md` §6.8, fork 2).
- *Chrome's design: one context, wiped and replayed on every switch.*
  Rejected: work is scheduled per request, so a 128-token background job
  blocks a foreground request for 2.95 s. Per-token dispatch blocks it for
  0.023 s and costs 1.2% throughput (ODQ §9).

**Trade-off.**
- Isolation is enforced in software (D8), not by process boundaries
  (ODQ §10.1).
- All governance happens in userspace, because the kernel can't see a
  session (ODQ §14).
- The pool stays resident while idle: about 2.5 GB RSS at n_ctx 16,384.
  Chrome frees its model after 5 idle minutes (ODQ §10.3).

**Evidence.**
- ODQ §7: the pair-versus-single table.
- ODQ §9: RSS 1,139 → 1,143 MB from 1 to 8 sequences at n_ctx 4,096.
- `tests/rss_check.py`: 2,490.5 MB idle, 2,511.3 MB with 8 sessions each
  holding a page, in both memory modes (`RQ2_NOTES.md` §4f).

**Status.** In place. Idle teardown and hibernation (ODQ §10.3) were never
built.

**Code.** `app.py:88` `build()` creates one model and one context; `engine.py:32`
`Engine` owns them.

### D2. A separate native server, launched by the browser

**Decision.** Inference runs in a Python process that drives llama.cpp through
llama-cpp-python's low-level ctypes layer. `MalabrManager` launches it when
the browser starts, and it exits when the browser exits.

**Context.**
- The browser must control the model's lifetime.
- Multi-sequence batching, per-sequence KV removal and position shifts exist
  only in llama.cpp's low-level API (ODQ §9).

**Alternatives considered.**
- *The high-level `llama_cpp.Llama` class.* Rejected: it exposes no sequence
  id, and its state calls cover the whole context (ODQ §9, "Cost").
- *A container (podman, then Docker files).* Dropped: moved to a normal
  process in `aa71242` (2026-01-19), and the container files were removed in
  `af856d5`. The commit gives no reason.
- *In-page inference with WebGPU or WASM (WebLLM).* Out of scope
  (`research_problem_statement.md:946-947`) and rejected as the sharing
  mechanism (D1).
- *Wrapping the launch in `systemd-run --scope` for the CPU ceiling.*
  Rejected: `Terminate(pid)` on the wrapper doesn't reach the Python child
  (tested; `NOTES.md app.apply_cpu_ceiling`).
- *Inference inside the browser process or a Chromium utility process*
  *(inferred)*. Not recorded.

**Trade-off.**
- The Python server has no sandbox (L6).
- Any program running as this user can reach the socket (handled in D11).
- The server could outlive a crashed browser; it no longer does (D11).
- Cold start takes about 2 minutes, so the browser retries its connect for up
  to 180 s (`NOTES.md app.main`; `RQ1_NOTES.md` §2b).

**Evidence.** This is an architectural choice, not a measured one. The
lifetime coupling is tested in D11.

**Status.** In place.

**Code.**
- `chrome/browser/malabr_manager.cc:36`: the server path.
- `:236` `StartMLServerIfEnabled`, which calls `LaunchProcess` at `:275`.
- `app.py` `main()`.

### D3. A Unix socket, a text header and typed binary frames (FlatBuffers dropped)

**Decision.** The browser and the server talk over a Unix domain socket.
- Each connection opens with a length-prefixed ASCII header:
  `route,extension_id,tab_id,origin,visibility,size`.
- On a request connection, the prompt follows the header.
- Replies stream back as frames, `[1-byte type][4-byte length][payload]`,
  where the type is token, complete or error.

**Context.** The inherited v3.0 protocol didn't fit:
- it was one-shot: one request, then one whole response;
- its FlatBuffers schema carried x/y tensors for sklearn `fit`/`predict`;
- it identified the caller by extension only (ODQ §1, §3).

**Alternatives considered.**
- *Extend the FlatBuffers schema.* Rejected:
  - the change cascades through `ml.fbs`, the regenerated Python and JS
    bindings, the IDL and the C++ (ODQ §3);
  - the inherited Python path couldn't even import, because flatbuffers
    wasn't installed (`157d2a8` message);
  - so it was replaced rather than extended, and removed in `af856d5`.
- *Keep one-shot framing.* Rejected: time-to-first-token is the metric that
  shows scheduling at all, and streaming from Phase 1 meant Phase 2 changed
  one variable, not two (§6).
- *A zero-length terminator instead of a type byte.* Rejected: "finished" and
  "failed partway" must be distinguishable (§6).
- *A connection per conversation, or per token.* Rejected: a connection per
  call costs under a millisecond on a Unix socket. Per conversation adds
  handling for a dropped long-lived connection and saves nothing (ODQ §1).
- *Incrementing request ids.* Rejected for `base::UnguessableToken`, which
  can't collide across a restart (§6).

**Trade-off.**
- The header is hand-written on both sides with nothing to keep them in step.
  For the control channel they weren't (R7).
- Any same-user process can reach the socket, so the server validates every
  field and bounds every length before reading.

**Evidence.**
- Tests 34 (`tests/test_phase1.py:826`) and 34b (`:837`) bound the payload and
  the header.
- `157d2a8` checked the request header byte for byte against the built
  `MServerUDS::GetHeaderPayload`.
- Test 79 (`:2118`) covers the control header.

**Status.** In place.

**Code.**
- `malabr_service/protocol.py:8-31`: routes, frame types and bounds.
- `protocol.py:102-166`: `unpack_client_envelope`.
- `protocol.py:168`: `encode_frame`.
- `extensions/browser/api/malabr/mserver_uds.cc`: the browser side.

### D4. Two channels: a socket per request, and one control connection

**Decision.** Each `generate()` call gets its own socket. One long-lived
control connection from `MalabrManager` carries what no request carries:
- `FOREGROUND,<tab>`;
- `TAB_CLOSED,<tab>`;
- `LIVE_TABS,<ids…>`, sent on every (re)connect;
- `EXT_UNLOADED,<ext>`.

**Context.** An idle session has no open socket. So a tab closed while idle,
and every visibility change, had no channel to travel on (§5e).

**Alternatives considered.**
- *Per-request sockets only ("pushed down the socket").* Rejected: when a
  session is idle there is no socket to push down (§5e).
- *Replay `TAB_CLOSED` events after a reconnect.* Rejected: a close lost while
  disconnected can never be replayed, because the browser no longer knows the
  tab existed. The browser sends state (`LIVE_TABS`) instead, and the server
  reconciles against it (§5f; `NOTES.md runtime.ControlReader.dispatch`).
- *Per-session visibility booleans, or hide/show event pairs.* Rejected for
  one foreground key (§5f; D18).
- *Stop as a fifth control message.* Rejected: `extensions/browser` can't
  include `chrome/browser/malabr_manager.h` (Chromium layering), so stop has
  its own request route (`NOTES.md protocol.(module)`).

**Trade-off.**
- It is a second protocol path with its own failure modes.
- Nothing is acknowledged. The browser logged "control connection
  established" while the server was closing the channel after the header
  (`design_doc/control_channel_audit.md`).

**Evidence.**
- Tests 10, 50, 58 and 59 inject control messages on the server side.
- Test 79 checks that the browser's header parses.
- `scripts/test_control_channel.py` (test 80, run by hand, real browser):
  foreground arrives after the server fix; closing a tab still frees nothing.

**Status.** Changed. The channel never worked in a real browser until
`6dc6a0b` (2026-11-03); see R7. Rebuild items 8 and 9 finish the fix.

**Code.**
- `chrome/browser/malabr_manager.cc:96`: the header.
- `malabr_manager.cc:329-387`: connect and resync.
- `malabr_manager.cc:500-531`: tab removal.
- `runtime.py:168-270`: `ControlReader`.
- `protocol.py:183-264`: control message parsing.

### D5. The chat panel lives inside the tab

**Decision.** The UI is a content script in the page's top frame. It draws
into a closed shadow root.

**Context.** The browser must derive identity itself (D6). Only a caller that
sits in a tab has a real tab id and a meaningful visibility.

**Alternatives considered.**
- *A popup.* Rejected: it is destroyed when it loses focus, and it has no tab
  id; `IdForTab` returns `InvalidValue()` for it (`phase1_design.md:42-44`).
- *A side panel.* Rejected: the extension must claim which tab it serves, and
  it could lie to steal foreground priority (`phase1_design.md:46-52`).
- *`all_frames: true`.* Rejected: every ad iframe would open its own session
  (§2).

**Trade-off.**
- The host page can see keystrokes, because key events cross the shadow
  boundary (L2).
- Replies are delivered to the extension's content scripts in the calling
  renderer process. Same-site frames that share that process could receive
  them; unguessable request ids filter them out (L3).
- A proposed fix for L2: put the input in an extension-origin iframe, while
  the content script still calls `generate()`, so the key keeps the page's
  origin.

**Evidence.**
- `scripts/test_panel_status.py` (test 72) passed 8/8 on 2026-11-02
  (`RQ1_NOTES.md` §4d).
- Skip 36, "one session per page, not per iframe", is declared browser-only.

**Status.** In place. The L2 fix is proposed.

**Code.**
- `malabr_chat_extension/content.js:4`: top frame only.
- `content.js:14`: the closed shadow root.
- `malabr_chat_extension/manifest.json`: `"all_frames": false`.

### D6. The page sends only the prompt

**Decision.** `chrome.malabr.generate()` takes one field, `prompt`. The
browser works out everything else itself: the extension id, tab id, origin,
visibility and request id.

**Context.** Anything a page or an extension supplies can be forged. The
session key and the scheduling priority must not be forgeable.

**Alternatives considered.**
- *Caller-supplied tab id and visibility* (the side-panel pattern). Rejected
  (D5).
- *Route replies with the renderer-supplied filter dictionary of
  `EventListener`.* Rejected as an authorisation check, because it is stored
  verbatim and never validated (`event_router.cc:737-780`, §6). It is used
  only as a routing convenience.
- *Identity by extension only* (v3.0). Rejected (ODQ §3; D7).

**Trade-off.**
- Any extension that declares the `malabr` permission can call the API;
  there is no allow-list (`RQ1_NOTES.md` §2b).
- Adding a verb means changing the IDL and rebuilding, so server commands
  travel inside the prompt instead (D30).

**Evidence.**
- `RQ1_NOTES.md` §2b traces each derivation through the C++.
- The server validates the header again and refuses `"null"` origins as a
  second gate (`protocol.py:102-166`; test 34).

**Status.** In place.

**Code.**
- `extensions/common/api/malabr.idl:20-22`.
- `extensions/browser/api/malabr/malabr_api.cc`:
  - `:45` extension id;
  - `:59` tab id;
  - `:69` origin;
  - `:79-81` opaque origins refused;
  - `:92-103` visibility.

---

## B. Identity and isolation

### D7. Session key = (extension, tab, origin), one conversation per key

**Decision.**
- A conversation is identified by `(extension_id, tab_id, origin)`.
- Each key holds exactly one live conversation, in one KV slot.
- Opaque origins are refused.

**Context.** The key decides who can read which conversation.

**Alternatives considered.**
- *Extension only* (the v3.0 header). Rejected: two tabs of one extension
  collapse into one session (ODQ §3).
- *(extension, tab).* Rejected: a tab navigated from a bank to another site
  handed the new site a session still holding the bank conversation (§5g;
  `NOTES.md session.Session`).
- *One session per frame.* Rejected with `all_frames: false` (D5).
- *Add a profile id.* Not needed: tab ids come from one browser-wide
  `SessionIdGenerator`, verified in source (§5).
- *Allow opaque origins.* Rejected: every opaque origin serialises to
  `"null"`, so unrelated documents would share one key
  (`NOTES.md protocol.unpack_client_envelope`).

**Trade-off.**
- A tab holds one origin's conversation at a time (rule 1, D10).
- Two extensions on one tab are two sessions sharing one foreground tab. That
  once starved one of them; fixed in `33429f1`, test 55b.

**Evidence.**
- Test 01: two live sessions; the holder recalls the secret, the other
  doesn't.
- Test 13: 16 concurrent admissions; no slot granted twice.
- Test 64: eviction and admission at full capacity.
- Test 73: same tab, new origin; the secret is not repeated.

**Status.** In place.

**Code.**
- `protocol.py:81`: `session_key`.
- `engine.py:89-104`: `get_or_create`, one critical section.
- `session.py:93`: the conversation id.
- `malabr_api.cc:79-81`: opaque origins refused.

### D8. Wipe on acquire, verify the wipe, quarantine on failure (draft item 9 merged here)

**Decision.**
- A KV slot is cleared when it is handed to a new session, not when its old
  session releases it.
- The engine verifies the wipe: `llama_memory_seq_rm` must return true, and
  then `llama_memory_seq_pos_max` must return −1.
- A slot that fails is quarantined and never reused.
- No token is ever decoded into a slot that hasn't passed the wipe.

Because every slot starts empty, no prefix is ever shared between sessions.
That was never decided separately; it follows from this rule.

**Context.** llama.cpp clears nothing on its own. A reused sequence id still
holds the previous session's cells: measured `pos_max == 12` after a
13-token conversation (`NOTES.md slots.SlotAllocator`; ODQ §10.1).

**Alternatives considered.**
- *Wipe on release* (also on release, "belt and braces", in §7's sketch).
  Rejected for two reasons:
  - a release can be skipped by a bug or a crash; an acquire can't;
  - the wipe would run on a connection thread, which is a data race with
    `llama_decode` (`NOTES.md slots.SlotAllocator.release`; fixed in
    `5782cf8`).
- *Chrome's approach: wipe and replay the context on every switch.* Rejected:
  it buys isolation with seconds of recompute (ODQ §10.1).
- *Trust `seq_rm`'s return code alone.* Rejected: `pos_max` asks the cache
  itself whether anything is left (`NOTES.md slots.SlotAllocator._wipe_and_verify`).
- *A context-manager-only API and generation counters on slot ids.* Listed as
  fixes 3 and 5 in ODQ §10.1; not built. The single chokepoint (fix 2) and
  the runtime check (fix 4) were built.
- *Share common prefixes across sessions* (`llama_memory_seq_cp`)
  *(inferred)*. Never proposed. The related-work check found that KVGov's
  prefix-cache attacks can't apply here for this reason (conversation log,
  October).

**Trade-off.**
- A quarantined slot is lost capacity until the server restarts.
- The check reads llama.cpp's cell bookkeeping, which is what attention uses;
  it doesn't zero memory bytes (`RQ1_NOTES.md` §2d).
- Without shared prefixes, every session prefills its own copy of the system
  prompt and page text.

**Evidence.**
- Test 02: `pos_max` 82 before the wipe, −1 after; the slot is reused; the
  new session doesn't repeat the secret.
- Test 74 and `tests/negative_control.py`: engines broken on purpose (stale
  slot, shared sequence) were caught 40/40 by both detectors in each of four
  configurations, with 0/20 false alarms (`RQ1_NOTES.md` §4b).
- Test 28: a deliberately wrong `seq_rm` on one slot leaves another intact.

**Status.** In place.

**Code.**
- `slots.py:35-46`: `acquire` marks the slot pending wipe.
- `slots.py:65-84`: `prepare` does the wipe.
- `slots.py:86-97`: `assert_ready`.
- `slots.py:99-112`: `_wipe_and_verify`.
- `engine.py:335-342`: the checks before every batch.

### D10. Conversation lifecycle

**Decision.** Four rules:
- **(a) Origin owns a conversation.** A sibling origin never reads it.
- **(b) The site decides when the user has left.** Leaving for another site
  should end both copies of the conversation.
- **(c) Rule 1.** The first chat turn from a new origin in the same
  (extension, tab) evicts the old origin's session. `bg.js` deletes the
  panel's copy at the same moment.
- **(d) Conversation-id check.** The panel keeps its transcript only while the
  server's conversation id matches the saved one.

**Context.** Each conversation exists twice: the panel's transcript in
`chrome.storage.session`, and the model's KV session. They must end together.
The server can drop a session without the panel seeing it: by rule 1, through
`LIVE_TABS`, on a restart, or on a model switch.

**Alternatives considered.**
- *Delete on every cross-origin navigation.* Rejected: a same-site login
  bounce (`accounts.bank.com`) would wipe the chat. Rule 1 works lazily, with
  no navigation observer (`NOTES.md engine.Engine.evict_other_origins`;
  `NOTES.md bg.js`).
- *A grace period, or an allow-list of login providers, for logins on another
  site.* Rejected. The accepted cost: after the rebuild, a login hosted by
  Google, Microsoft or Okta counts as leaving and wipes the chat
  (`design_doc/session_handoff_v7.md:204-206`).
- *`status` answering yes/no rather than an id.* Rejected: the key outlives
  the conversation. After a restart, "is there a session?" says yes while
  every earlier turn is gone (`NOTES.md runtime.MalabrServer._handle_meta`).
- *Clean up the transcript on `pagehide`.* Rejected: it deleted the transcript
  on ordinary navigation and broke persistence across reloads (§12 test 39,
  `phase1_design.md:3005`).
- *Reconnect silently after a server crash.* Rejected: from outside, that
  looks the same as a cleared chat, which would make the leak tests
  unreliable (§4).

**Trade-off.**
- Until the rebuild, after the user leaves a site the old conversation stays
  in RAM until they next chat elsewhere.
- A discarded tab keeps its model memory (rebuild item 7).
- A page restored from the back/forward cache needs its own check, which
  stays even after the rebuild.

**Evidence.**
- Test 72 (`scripts/test_panel_status.py`): 8/8 on 2026-11-02.
- `scripts/test_transcript_keys.py`: 14/14.
- Test 03d: 7 cases.
- Tests 64 and 73.

**Status.**
- (a), (c) and (d): in place (`a7ad62e`, `c09ad8e`).
- (b): on the rebuild list (item 6, `TAB_NAVIGATED`). Today the deletion
  waits for a chat turn on the new origin.
- Tab discard: on the rebuild list (item 7).

**Code.**
- `runtime.py:444-449`: rule 1, then wait for the teardown.
- `engine.py:106-117`: `evict_other_origins`.
- `runtime.py:512`: `_handle_meta`, with `status` at `:522` and `new` at
  `:530`.
- `malabr_chat_extension/bg.js`:
  - `:12-16` builds the key from `sender`;
  - `:39-44` mirrors rule 1;
  - `:60` deletes on `tabs.onRemoved`.

### D11. The server talks only to the browser that launched it

**Decision.** Three layers:
1. **A private socket.** The socket lives in `$XDG_RUNTIME_DIR` (mode 0700)
   with mode 0600, and the server refuses a socket directory not owned by
   this user or writable by group or others.
2. **A peer check.** The server accepts a connection only if the kernel's
   record of the peer (`SO_PEERCRED`) names its own parent, the browser.
3. **Die with the parent.** The server arms `PR_SET_PDEATHSIG`, so it exits
   when the browser does.

**Context.** The server trusts the header, so any same-user program that
connected could:
- claim any tab and origin and read that conversation;
- take over the control channel;
- switch the model under every tab (`NOTES.md runtime.MalabrServer._peer_allowed`).

Two more gaps:
- With the `/tmp` default, another account could create the socket path
  first, or plant the pidfile (`NOTES.md runtime.check_socket_dir`).
- After a browser crash the server lived on with every conversation in RAM,
  and the next browser reused it (`NOTES.md runtime.die_with_parent`).

**Alternatives considered.**
- *Mode 0600 in `/tmp` only.* Rejected: another account can claim the path
  first.
- *Check the peer's uid.* Rejected: every program running as this user has
  the same uid (rebuild item 5).
- *Check the peer in the browser* (`SO_PEERCRED` in C++). This is the complete
  fix; it needs the rebuild (item 5).
- *`kill_on_parent_death` in the C++ launch.* Needs the rebuild. The
  server-side `prctl` gives the same result now (rebuild item 1, struck).

**Trade-off.**
- A server started by hand or by a test needs `MALABR_TRUST_ANY_PEER=1`,
  which turns the peer check off and prints a banner.
- The browser still trusts whatever listens at the path, so a program that
  kills the server and takes its place goes unnoticed (L4).
- Without the launcher, the browser defaults to `/tmp/malabr_v3.sck` (L5).

**Evidence.**
- Test 71: five unsafe setups refused (`/tmp`, a 1777 directory, a symlink,
  a missing directory, a planted pidfile); `main()` returns 4.
- Test 75: a non-parent is refused, the parent is served, and the opt-out is
  open and loud.
- Test 76: a child dies with its parent; a late start is refused.
- Test 77: repeated SIGTERM still exits. In real browser crashes the server
  hung 2 times in 7 before this fix, and 0 times in 11 after
  (`RQ1_NOTES.md` §3).
- Commits `a34926a` and `f44a4d5`.

**Status.** In place. The browser-side peer check is on the rebuild list.

**Code.**
- `runtime.py:93`: `check_socket_dir`.
- `runtime.py:304`: `chmod 0600`.
- `runtime.py:315`: records the expected peer.
- `runtime.py:355`: `_peer_allowed`.
- `runtime.py:75`: `die_with_parent`.
- `app.py:199-202`: arms the parent-death signal.
- `scripts/run_malabr.sh:83-95`: puts the socket in `$XDG_RUNTIME_DIR`.

---

## C. Memory

### D12. One KV pool sized at startup, in one of two modes

**Decision.** The pool size is computed once, at startup:

    n_ctx = 0.5 × (MemAvailable − 2 GiB − model size) / bytes per token

It is clamped to 4,096–16,384 and allocated once. There are two modes:
- **Partitioned** (the server's default): n_seq_max hard slices of
  n_ctx / n_seq_max each.
- **Shared KV** (what the launcher runs): one unified pool. Each session gets
  a soft budget of max(n_ctx / n_seq_max, n_ctx / 2), and the aggregate guard
  (D15) keeps the total within the pool.

**Context.** The inherited n_ctx of 4,096 with 8 slots left 512 tokens per
session, so every conversation was compacted constantly. That tests whether
compaction works at all, not whose context the policy chooses to shrink,
which is the research question (`NOTES.md config.(module)`).

**Alternatives considered.**
- *A hardcoded 4,096.* Rejected (above).
- *0.8 × MemAvailable* (§11). Rejected (`NOTES.md config.(module)`):
  - it gives 75,340 tokens, 7.7 GB of KV, on this machine;
  - it counts page cache as free;
  - it subtracts neither the model nor the browser.
- *A cgroup memory ceiling on top.* Not needed: the pool is one fixed
  allocation and can't grow (`NOTES.md config.(module)`).
- *A pool sized to demand* (destroy and recreate the context), *two-level
  idle teardown, or hibernation.* Recorded in ODQ §10.3; never built.
- *Uneven per-sequence slices in partitioned mode.* An open question in
  ODQ §21; answered instead by the shared mode (`kv_unified`, added in
  `c4fca68`).

**Trade-off.**
- The memory is held whether or not anyone uses it: about 2.5 GB RSS at
  n_ctx 16,384 (`RQ2_NOTES.md` §4f).
- **Partitioned:** a tab can never grow past 2,048 tokens, and one greedy tab
  wedged the engine (M1–M3).
- **Shared:** one tab may take half the pool, and its neighbours' turns got
  about 6× slower while it prefilled (`RQ2_NOTES.md` §4d).
- Two modes mean the suite must run twice, and for a long time the "shared"
  pass wasn't really shared (R9).

**Evidence.**
- `tests/rss_check.py`: +21 MB from 0 to 8 full sessions, flat in both modes.
- `tests/greedy_tab.py` at production size, one greedy tab: partitioned had
  156,019 failed rounds; shared had none (`RQ2_NOTES.md` §4d).

**Status.** In place. M1 (gate 2) and M3 (the failure stop) are open
limitations with no fix chosen.

**Code.**
- `config.py:30-47`: the constants.
- `config.py:94-104`: `compute_n_ctx`.
- `config.py:159-167`: `n_ctx_per_session`.
- `app.py:111`, `:124-126`: `n_seq_max` and `kv_unified`.
- `scripts/run_malabr.sh:62`: the launcher turns shared mode on.

### D13. Tokens (KV positions) are the unit of memory

**Decision.** Everything a conversation holds counts as KV positions against
one budget: the template, the system prompt, the user's text, attached page
text and the replies. Budgets, admission gates and compaction triggers are
all in tokens.

**Context.** The pool is a fixed number of cells. A cell costs space (107 KiB
per token at f16 for Qwen3-0.6B) and time, because every decode step reads
every earlier K/V vector. "One curve captures both memory and CPU"
(`NOTES.md calibration.(module)`).

**Alternatives considered.**
- *Bytes of RSS per tenant* (the cgroup `memory.max` analogue). Rejected: the
  kernel can't see a session; it is a sequence id inside one allocation
  (ODQ §14).
- *A cap on stored history per session* (`research_problem_statement.md`
  §6.8). Superseded once the KV cache became the store *(inferred)*.
- *Characters* *(inferred)*. Used only at the panel's edge, as a coarse
  pre-filter: `MAX_PAGE_CHARS = 4000`, which assumes about 3 characters per
  token.

**Trade-off.**
- The panel budgets page text in characters, but the server refuses in
  tokens. A token-dense page is refused whole at gate 2, not truncated
  (`NOTES.md content.js min`).
- Bytes per token depend on the model. `config.py` scales the Qwen figure
  only by KV element type.

**Evidence.**
- ODQ §6: a 439 MB context allocation at n_ctx 4,096 is about 107 KB per
  token.
- `RQ2_NOTES.md` §2c: `s.pos` counts everything in the cache.

**Status.** In place.

**Code.**
- `config.py:24-27`: `BYTES_PER_TOKEN`.
- `engine.py:632-637`: the compaction trigger.
- `malabr_chat_extension/content.js:10`: `MAX_PAGE_CHARS`.

### D14. Compaction drops whole middle exchanges, keeping the first and the latest

**Decision.**
- **When:** before each round, for any session at 95% of its budget.
- **What:** whole exchanges, oldest first, from `turn_boundaries[1:-1]`,
  until at least 512 tokens are freed.
- **How:** later positions shift down (K-shift), and the formatter drops the
  matching messages.
- **Kept:** the first exchange (the anchor) and the most recent one.

**Context.** With 8 slots, an ordinary conversation reaches its share, so
compaction is the normal path, not an edge case (§8).

**Alternatives considered.** The ladder in ODQ §15, and §8:
- *Summarise.* Rejected: it costs a full generation, and judging whether the
  summary is still good is a different research field (§8). This is what
  Chrome's documented "session compacting" does (ODQ §21).
- *Hibernate to disk.* Rejected (D26).
- *Evict the whole session.* That is teardown, not a response to memory
  pressure. Under pressure the guard refuses turns that haven't started
  instead (D15).
- *Keep tokens by attention score* (StreamingLLM, H2O, SnapKV). Out of scope
  (§8).
- *A "stub" keeping about 40 characters of each dropped turn.* Proposed in §8;
  never built.
- *Arbitrary position ranges* (the first prototype). Replaced by whole
  exchanges, `[user-delta start, response end)`, which leave the template
  well-formed with no repair (§8 corrections).
- *Incremental eviction instead of batch.* Left open in §8. Test 47 compares
  them: the same tokens freed, in 1 call against 5. Batch eviction was kept.
- *Check after the batch.* Rejected: one batched decode can push a session
  past its cap before anything re-checks (`NOTES.md engine.Engine.needs_compaction`).

**Trade-off.**
- The middle is gone, not summarised.
- **Cost on Qwen:** about 0.003–0.004 nats KL, and no probe's first token
  changes (`RQ2_NOTES.md` §4c).
- **Cost on gemma:** 8–20× more, and one open-ended probe changes its first
  token.
- Test 48 fails in partitioned mode on a fresh question, and is left failing
  on purpose (M11).
- A session holding only its anchor and one large turn can't be relieved
  (M2).
- The guard's last resort drops the latest turn as well (M6, D15).

**Evidence.**
- `tests/compaction_control.py`, 20 fresh secrets per variant, both modes:
  the correct compaction recalled 20/20, and loss was detected in 20/20 of the
  broken variant (`RQ2_NOTES.md` §4b). Test 78 runs one trial of each in the
  suite.
- `tests/fidelity_check.py`: the KL table (`RQ2_NOTES.md` §4c).
- Test 70 checks what survives by content, not length.
- Tests 04, 09, 53 and 54.

**Status.** In place. The `msg_index` bug was fixed (R3).

**Code.**
- `engine.py:629-690`.
- `session.py:65`: `Turn`.
- `formatter.py`: `ChatFormatter.drop_messages`.

### D15. The shared-pool guard compacts the greediest session first

**Decision.** In shared-KV mode, before each round, when the sum of all
sessions' positions reaches n_ctx − 64:
1. Compact the largest session above its fair share (n_ctx / n_seq_max). If
   none is above, compact the largest overall.
2. If that victim has nothing to drop while keeping its latest turn, drop the
   latest turn too.
3. If compaction is spent, roll back PENDING turns, largest first, each with
   "server is at capacity — try again".
4. Never touch a session that is generating.

**Context.** With one pool and a soft budget of n_ctx / 2 per session, the
total can exceed n_ctx. A decode that finds no free cell fails the whole
round (`NOTES.md engine.Engine._run_round`).

**Alternatives considered.**
- *No guard* (the negative control). Rejected on measurement. With a small
  pool there were 43,643 failed rounds, after which the engine stopped itself
  for every tab (`RQ2_NOTES.md` §4d).
- *At admission, reserve other sessions' current size.* Rejected: it starved
  a third tab once two others got deep. Their fair share is reserved instead
  (`NOTES.md engine.Engine._begin_turn`).
- *Kill a generating stream to make room.* Rejected: a worse trade than
  refusing a turn that has produced nothing (`engine.py:734-740`).
- *A fair-share floor:* never shrink a session at or near its fair share to
  feed a greedy one. Not adopted, and not recorded as a proposal anywhere.
  M5 records the gap without choosing a fix.

**Trade-off.**
- It rejects turns under pressure (M7).
- Its last resort drops the latest turn (M6).
- It can shrink a small tab to feed a greedy one: a tab at 517 tokens, 5 over
  its fair share of 512, was cut to 130 (M5, R8).

**Evidence.**
- Tests 40b–40e.
- `tests/greedy_tab.py`, small pool:
  - guard on: 24/24 small-tab turns completed, no failed rounds;
  - guard off: the engine stopped.
- At production size the guard was never exercised: two greedy tabs couldn't
  fill the pool in 600 s (M8).

**Status.** In place. The fairness gap (M5) is open; no floor is adopted.

**Code.**
- `engine.py:692-732`: `_relieve_aggregate_pressure`.
- `engine.py:734-750`: `_reject_pending_for_capacity`.
- `engine.py:459-468`: gate 2's room calculation in shared mode.

### D16. KV cache in f16 by default; q8_0 opt-in; q4_0 refused

**Decision.** The KV element type defaults to f16. `MALABR_KV_TYPE=q8_0` is
allowed; any other value stops the server at startup.

**Context.** q8_0 holds about 1.9× the context per byte: 56.8 KB per token,
against 107 KB at f16.

**Alternatives considered.**
- *q8_0 as the default.* It was first called "adoptable": on Qwen, KL 0.0040
  and top-1 4/4. That was reversed:
  - on gemma-3 the KL is 0.0343, about 10× more, so the cost depends on the
    model;
  - a default that moves the output by a model-dependent amount would
    silently move every fidelity and benchmark number measured after it
    (`NOTES.md config.KV_TYPE`, points 1–2).

  A second reason was given at the time, that q8_0 causes canary
  false-negatives. It was withdrawn on 2026-11-02 (R6).
- *Offer q4_0.* Rejected: it changes the top token on a quarter to a half of
  probes (KL 0.63 on Qwen, 0.14 on gemma).
- *A different precision per session.* Not possible: `type_k` and `type_v`
  apply to the whole context (ODQ §15).

**Trade-off.** Half the context per byte, compared with q8_0.

**Evidence.** The `tests/kv_quant_check.py` table in `NOTES.md config.KV_TYPE`;
commit `f22bf07`.

**Status.** In place. The comment at `config.py:10-12` still gives the
withdrawn reason ("causes canary recall false-negatives"); `NOTES.md` is
already corrected.

**Code.**
- `config.py:10-27`: the type, its check and the bytes per token.
- `app.py:115-120`: sets `type_k` and `type_v`.

---

## D. Scheduling

### D17. Continuous batching under a per-round latency budget

**Decision.** Each round is one `llama_decode` call. It carries one token for
each chosen decoding session, plus prefill chunks. The batch is composed by
spending a time budget, priced by the cost curve (D20):
- 50 ms when several sessions have work;
- 150 ms when only one does (R2).

Every session returns to the scheduler after every token.

**Context.**
- There is one compute thread.
- Decode is bandwidth-bound, so a batch reads the weights once for several
  tokens (ODQ §11).
- The budget bounds how long anyone waits for the next round.

**Alternatives considered.**
- *Schedule per request* (Chrome; Ollama is FIFO). Rejected: 2.95 s of
  blocking behind a 128-token job, against 0.023 s per token, for 1.2%
  dispatch overhead (ODQ §9).
- *Dispatch per token without batching.* This was the first recommendation:
  batching was deferred as "an optimisation, not the research question"
  (ODQ §11). It was adopted in `26f9479` (§9a/§9b), which supersedes that
  deferral (R10).
- *§7's slot-count split* (a batch size, with slots split 50/50 between
  foreground and background). Rejected (`NOTES.md scheduler.build_batch`):
  - it could never bind, because admission already caps sessions at
    n_seq_max;
  - the foreground's spare slots went to the background, the reverse of the
    intended priority.
- *Throttle the token rate to cap CPU.* Rejected; see D31.

**Trade-off.**
- A batched round can't be preempted, so a tab that arrives mid-round waits
  for the round to end (R2).
- A batch costs the sum of its members' KV reads, not the maximum. Measured:
  4 sessions at depth 1,500 gave 3.1 tok/s each, against 25.1 for one session
  alone. So a shallow foreground session pays for a deep neighbour.
- One failing decode fails every session in that batch (M4).

**Evidence.**
- Test 05: two sessions shared a `llama_decode` call in 16 of 21 rounds, and
  isolation held.
- Test 52: mixed depths stay within the budget.
- Test 62.

**Status.** In place.

**Code.**
- `scheduler.py:9-31`: the constants.
- `scheduler.py:128-209`: `build_batch`.
- `engine.py:310-385`: `_run_round`.
- `engine.py:542-566`: `_decode_batch`.

### D18. The visible tab goes first every round; aging bounds the rest

**Decision.** The foreground is one tab id, pushed by the browser: the active
tab of the active window, if it is visible. Each round:
1. Foreground sessions are admitted first, unconditionally.
2. Any session skipped 5 rounds in a row comes next, even over budget when it
   would run alone.
3. The cheapest sessions fill what is left.

If the control channel has been down for more than 10 s, there is no
foreground.

**Context.** The tab the user is looking at should answer first, without
starving the others.

**Alternatives considered.**
- *A 50/50 split between the foreground group and the background group*
  (§9's naive scheduler). Superseded by §9a before it was built.
- *Priority at admission only* (Chrome: take the first foreground task, then
  run it to completion). Measured as the contrast: Chrome's Jain fairness is
  0.987–0.994, while MALABR re-decides every round
  (`design_doc/PROGRESS_REPORT.md`).
- *A visibility boolean per session.* Rejected: one key can't express "two
  foreground tabs", and clearing it is one assignment (§5f).
- *Keep the last known foreground when the channel is lost.* Changed once
  §9a existed: a stale key would give a hidden tab unconditional admission
  every round, forever. The engine falls back to no foreground instead
  (§5f; `NOTES.md engine.Engine.__init__`; R11).
- *Fill in arrival order (FIFO).* Rejected: the first arrival could take the
  whole budget (`NOTES.md scheduler.build_batch`).

**Trade-off.**
- Fairness: Jain 0.631, against 0.997 for equal share.
- Two background stalls of more than 1 s.
- A foreground tab can still be refused a slot when every slot is held (D29).

**Evidence.**
- `tests/pipeline_bench.py`, 8 tabs, foreground arriving last: foreground
  TTFT 0.44 s, against 4.22 s for equal share and 16.55 s for FIFO, with
  makespan within noise (`design_doc/PROGRESS_REPORT.md:191-203`).
- Tests 10, 55, 55b, 56 and 58.

**Status.** In place in the engine. In a real browser the foreground never
reached the server until `6dc6a0b` (R7). So every published scheduling number
is an engine result from the test harness; no browser-measured number exists
yet (`design_doc/control_channel_audit.md`).

**Code.**
- `scheduler.py:140-182`.
- `engine.py:119-127`: `effective_foreground_tab_id`.
- `chrome/browser/malabr_manager.cc:394`: `UpdateForegroundTab`.

### D19. Prefill gets whatever budget decode leaves

**Decision.**
- Decode picks are admitted first.
- Waiting prompts then get prefill chunks sized to the leftover budget,
  foreground first.
- A prompt skipped 5 rounds for want of a minimum chunk (32 tokens) is given
  whatever the budget can afford.

**Context.** Prefilling a long prompt in one call freezes every other session
(§9b).

**Alternatives considered.**
- *Prefill in one blocking call.* Rejected (§9b, part 1).
- *§9b taken literally:* skip while the chunk would be smaller than
  `PREFILL_CHUNK_MIN`. Rejected: if decode keeps leaving less than that, the
  prompt is never prefilled (`NOTES.md scheduler.build_batch`).
- *Force a full minimum chunk after the stall bound.* Rejected on
  measurement: it overran a 50 ms budget by 87 ms in 20% of rounds (same
  entry).

**Trade-off.**
- Under contention, large pages prefill slowly: two greedy tabs managed about
  18 tok/s combined, against 147–208 tok/s calibrated (M8).
- A round that deferred a prefill must not sleep like an idle round (R1).

**Evidence.** Tests 62 and 68.

**Status.** In place.

**Code.**
- `scheduler.py:184-207`.
- `engine.py:275-281`.

### D20. CostCurve: measured costs per position, worst case for cutoffs, closed loop

**Decision.** At startup the server calibrates this machine, or loads
`calibration.json`. It measures:
- decode cost by depth;
- prefill cost by size and depth;
- the joint worst case with all slots full.

Two statistics are used:
- admission uses the worst observed trial;
- output caps use the median.

Each round's real time feeds a moving-average correction that can only make
estimates more conservative (floor 1.0, ceiling 8).

**Context.** Decode speed falls from 48 to 5 tok/s with depth. A time budget
is only as good as the prices it is spent at.

**Alternatives considered.**
- *A closed-form "fixed cost plus linear KV read" model.* Rejected: the slope
  of 1/tps isn't monotonic in position, so the table is interpolated piecewise
  (`NOTES.md calibration.phase_b_position_curve`).
- *Extrapolate the joint cost from per-session costs.* Rejected: it predicted
  about 41 tok/s; the measurement gave 12.5 (`NOTES.md calibration.phase_c_joint_worst_case`).
- *Use the median for admission.* Rejected: trials spread by up to 19%, so a
  round estimated at 45 ms can take 54 ms (`NOTES.md calibration.phase_b_position_curve`).
- *Reuse the decode curve for prefill.* Rejected: wrong by about an order of
  magnitude (`NOTES.md calibration.phase_b_prefill_curve`).
- *Open loop, or a correction that can also loosen.* Rejected: a lucky run of
  fast rounds would widen batches until the bound breaks
  (`NOTES.md scheduler.CostCurve.observe_round`).
- *Use the design document's numbers.* Rejected: they were measured on
  another machine (`NOTES.md calibration.(module)`).

**Trade-off.**
- Startup takes about 2 minutes.
- `calibration.json` is the one file written to disk; it holds timings only
  (D26).
- Within a run, estimates only ever get more cautious.

**Evidence.** Tests 22, 24, 26, 27, 31, 35, 41, 46 and 63.

**Status.** In place.

**Code.**
- `scheduler.py:34-125`.
- `calibration.py`, including `apply_to_engine`.

---

## E. Lifecycle

### D21. Closing a tab cancels its work and frees its slot

**Decision.** `TAB_CLOSED` cancels every session of that tab:
- within one round, because the flag is read before the batch is composed;
- with one terminal frame to any waiting request;
- with a rollback, and the slot freed.

`LIVE_TABS`, sent on every (re)connect, removes sessions whose tab no longer
exists.

**Context.** Chrome keeps working for closed tabs. Its `SessionDisconnected`
never touches the pending tasks, and a dead session counts as foreground and
jumps the queue (ODQ §5, point 2 and the note after the list).

**Alternatives considered.**
- *Stop delivering, keep decoding* ("delivery-only"). Measured as the
  baseline arm: 453 tokens decoded for tabs already closed
  (`design_doc/PROGRESS_REPORT.md:360-370`).
- *Rely on the request socket closing* (the abandon flag). Insufficient: an
  idle session has no socket (§5e), and the flag is raised only when a token
  arrives (rebuild item 2).
- *llama.cpp's `abort_callback`* to interrupt a running decode (listed in
  ODQ §9 and §14). Not used: cancellation is a flag read at the top of each
  round. Why `abort_callback` was passed over isn't recorded *(inferred)*.

**Trade-off.**
- Up to one round of extra work.
- It depends on the control channel and on the browser sending the right tab
  id; until now neither worked in the browser (see Status).

**Evidence.**
- `tests/cancellation_bench.py`: 453 → 0 tokens decoded for closed tabs, and
  the tabs still being watched finish 1.87× sooner, 22.19 s → 11.87 s
  (`PROGRESS_REPORT.md:360-370`). A repeat gave 1.94×
  (`PROGRESS_REPORT.md:285-286`).
- Chrome, same question: survivors 1.04× faster, against 1.11× in the
  control.
- Tests 08, 18, 50 and 59.

**Status.** In place in the server; broken end to end:
- the browser's message never reached the server until `6dc6a0b` (R7);
- it still frees nothing in a real browser, because `TAB_CLOSED` carries the
  id of the "recently closed" history entry, not the tab's (rebuild item 9).

The published numbers are engine results.

**Code.**
- `runtime.py:219-260`: `ControlReader.dispatch`.
- `engine.py:192-205`: `cancel`.
- `engine.py:409`: `_apply_control`.
- `chrome/browser/malabr_manager.cc:500-531`: sends `TAB_CLOSED`.

---

## F. Method

### D22. Isolation tests use canaries with a positive and a negative control

**Decision.**
- **The canary.** One session holds a fresh secret: a word and six random
  digits.
- **The question.** Every session is asked the same direct question: "Repeat
  back the vault passphrase I gave you, exactly."
- **Positive control.** A pass requires the holder to recall the secret.
- **Negative control.** The method must catch engines broken on purpose.
- **Two detectors.**
  - the secret's digits appearing in the reply;
  - the teacher-forced mean log-probability per digit, with the threshold
    (mean probability above ½) fixed before any run.

**Context.** A leak test that passes because the model never answers proves
nothing.

**Alternatives considered.**
- *The guarded question* ("…If you do not know, say UNKNOWN."). Rejected on
  2026-11-02: the holder itself recalled 1 of 20 fresh secrets at f16 (R5).
- *One fixed secret, `MAGENTA-7731`.* Still used by the older suite tests.
  Fresh secrets are used for the miss-rate runs, because the fixed one passed
  partly by luck (`RQ1_NOTES.md` §4a).
- *Detect the word.* Rejected: a model asked for a passphrase it never saw can
  make up a word, but not those six digits (`RQ1_NOTES.md` §4a).
- *Judge compaction quality with a scoring model.* Rejected in favour of
  canary recall (§8).

**Trade-off.**
- It tests content only. Timing side channels are untested (L1).
- One model (L9).

**Evidence.**
- `tests/negative_control.py`: 160/160 broken-engine leaks caught, 0/80 false
  alarms, at f16, q8_0, and f16 with shared KV on both a partitioned and a
  unified cache (`RQ1_NOTES.md` §4b).
- Test 74, and for compaction, test 78.
- Chrome, re-run with the direct question: 5/5.

**Status.** In place (`f44a4d5`, `b0273d3`).

**Code.**
- `tests/harness.py`: `fresh_secret`, `secret_logprob`, `leak_trial`.
- `tests/negative_control.py`.
- `tests/compaction_control.py`.

### D23. Baselines: the same engine with the policy switched off, and Chrome's Prompt API

**Decision.** Scheduling is compared in two ways:
- **Inside MALABR's own engine,** against two arms:
  - FIFO: a lock around one context;
  - equal share: continuous batching with the foreground signal off, roughly
    llama.cpp `--parallel`.
- **Against Chrome 153's Prompt API** (Gemini Nano on CPU), using a unitless
  relative score: foreground TTFT divided by median TTFT.

**Context.** Comparing raw numbers with Chrome would confound the model, the
engine and the hardware path (ODQ §19).

**Alternatives considered.**
- *Raw TTFT against Chrome.* Rejected: different models, so the relative
  score is used instead.
- *"MALABR-CHROME":* Chrome's algorithm re-implemented inside MALABR
  (ODQ §19). Proposed as the controlled baseline; not built. The equal-share
  arm covers part of what it would show.
- *Two model instances* (a process per tier). Measured once, in ODQ §7 (D1).
- *Reason about Chrome from the Chromium 127 source in this checkout.*
  Partly withdrawn once Chrome 153 was measured (R14).

**Trade-off.**
- The MALABR side is the engine driven in-process; the Chrome side is a real
  browser (`design_doc/control_channel_audit.md`).
- The samples are small: n = 5 runs for MALABR's relative score, 4 for
  Chrome's.

**Evidence.**
- Relative score: MALABR 0.092 ± 0.009, Chrome 0.086 ± 0.012
  (`PROGRESS_REPORT.md:125`).
- The three-arm table (`PROGRESS_REPORT.md:191-203`).
- The arms are described in the `tests/baseline_bench.py` docstring.

**Status.** In place. MALABR-CHROME is proposed. A MALABR score measured in
the browser has not been taken.

**Code.**
- `tests/baseline_bench.py`, `relative_repeat.py`, `pipeline_bench.py`,
  `cancellation_bench.py`.
- `scripts/chrome_*.py`.

---

## G. Decisions not in the original list

### D24. One engine thread owns the model; other threads only set flags

**Decision.**
- Every llama.cpp call that touches the cache runs on one engine thread:
  decode, wipe, compaction and rollback.
- Connection threads only update bookkeeping, under a lock.
- They set flags (cancel, stop, replace) that the engine reads at the top of
  each round.

**Context.** llama.h promises thread safety only for tokenisation. §7's
sketch had connection threads call `seq_rm` while the engine was inside
`llama_decode`, which is a data race (`NOTES.md slots.SlotAllocator`).

**Alternatives considered.**
- *Wipe inside `acquire()` on the connection thread* (§7). Rejected (above);
  fixed in `5782cf8`.
- *Timer threads for periodic work.* Rejected: all such work must run inside
  the loop (`NOTES.md engine.Engine`).
- *An allocator without a lock.* Measured: it doesn't hand out duplicate
  slots, but it does race between the check and the action. With the window
  widened, 120 of 160 acquires raised `IndexError`
  (`NOTES.md slots.SlotAllocator.__init__`).

**Trade-off.**
- Cancellation and eviction are asynchronous, so admission has to wait for a
  teardown to finish (`wait_for_teardown`).
- An exception in the engine used to kill its thread silently. It is now
  logged, and the engine carries on (test 65).

**Evidence.** Tests 13, 30b, 64 and 65; commit `5782cf8`.

**Status.** In place.

**Code.**
- `engine.py:172-205`.
- `engine.py:295-308`: `_step`, which applies the flags first.
- `slots.py:21-46`.

### D25. A turn enters the context only by finishing; one generation per session

**Decision.**
- A session has at most one generation in flight.
- A newer prompt on the same key supersedes the old one and rolls it back.
- A stopped, superseded or cancelled turn is removed entirely, user message
  and partial reply, back to `pos_before_request`.
- The formatter rolls back in step.

**Context.** Keeping half a turn would leave the model believing something
the panel doesn't show (§6b).

**Alternatives considered.**
- *Queue the second prompt.* Rejected: the first is superseded, not queued
  (§12 test 12).
- *§6b's rollback to `pos_before_generation`.* Rejected: it leaves an
  assistant block open, so the next user turn is malformed. The formatter's
  prefix check caught this (`NOTES.md engine.Engine._roll_back_partial`).
- *One outbox per session.* Rejected: the superseded handler and the new one
  steal each other's frames (`NOTES.md session.Session.__init__`).

**Trade-off.** A stopped answer is gone from the context. There is no resume
or regenerate.

**Evidence.** Tests 12, 18, 53 and 53b.

**Status.** In place.

**Code.**
- `engine.py:206-242`: `stop_generation` and `submit`.
- `engine.py:484-501`: `_roll_back_partial`.

### D26. Nothing is written to disk

**Decision.** Conversations live only in RAM: the KV cache on the server, and
`chrome.storage.session` in the extension. The one file written is
`calibration.json`, which holds timings, not text.

**Context.** A session's death should be its data's death; that is the
property RQ1 is about (§4).

**Alternatives considered.**
- *`chrome.storage.local`.* Rejected: it writes conversations to disk
  (`NOTES.md bg.js`).
- *The SQLite `storage.py` inherited from v3.0.* Not used (§4).
- *Hibernate sessions to disk* (`llama_state_seq_save_file`). ODQ §10.3 had
  called this "a contribution in its own right". Rejected for Phase 1 (§7):
  - it reopens retention and encryption-at-rest decisions;
  - a session at position 2,000 means about 214 MB of I/O;
  - tabs bouncing back and forth would thrash.

**Trade-off.**
- Memory can't be reclaimed without ending conversations.
- A restart loses every conversation.
- The extension's ~10 MB session-storage quota limits each saved transcript
  to its last 40 turns.

**Evidence.** A design rule rather than a measurement. Test 35 covers the
atomic write of `calibration.json`.

**Status.** In place.

**Code.**
- `malabr_chat_extension/bg.js`: uses `storage.session`.
- `calibration.py`: `save` and `load`.

### D27. Chat structure comes from the model's own template; only the new part is prefilled

**Decision.** Render the whole conversation with the model's own chat
template, prefill only the part past what the cache already holds, and verify
the prefix every turn.

**Context.**
- Re-prefilling the history each turn would defeat keeping the KV resident.
- Hardcoding Qwen's ChatML would break gemma-3 (`NOTES.md formatter.ChatFormatter`).

**Alternatives considered.**
- *Append a hardcoded ChatML fragment* (the §6c draft). Rejected (above).
- *Compare strings to check the prefix.* Rejected for comparing tokens,
  because BPE can merge across boundaries (`NOTES.md formatter.ChatFormatter.verify_against_full`).

**Trade-off.**
- A template that rewrites history breaks this; the formatter fails loudly
  rather than drift.
- BOS handling and whitespace trimming needed special cases (`c4fca68`,
  `dc1a3c6`).

**Evidence.** Tests 06b, 32, 44 and 53b.

**Status.** In place.

**Code.** `formatter.py`: `ChatFormatter.user_turn`, `checkpoint`,
`drop_messages`.

### D28. The server sets the output cap, by position

**Decision.** The server caps each reply from a calibrated table, so that one
reply takes at most about 15 s at its depth. The cap is clamped to 64–512
tokens, and the client can't raise it.

**Context.** Chrome's `max_output_tokens` is optional and set by the caller
(ODQ §16).

**Alternatives considered.**
- *A cap set by the caller* (Chrome). Rejected (ODQ §16).
- *A flat 512.* Rejected: about 11 s early in a conversation, about 98 s deep
  into one (`NOTES.md session.OutputCap`).
- *Rely on per-token requeueing alone.* Rejected: keep an absolute ceiling as
  a backstop (ODQ §16).
- *Caps that depend on visibility* (ODQ §16). Proposed; not built.

**Trade-off.** Deep conversations get shorter replies. The panel says when a
reply hit the limit.

**Evidence.** Tests 06, 20, 27 and 31.

**Status.** In place.

**Code.**
- `session.py:12-41`.
- `calibration.py`: `derive_output_cap_table`.
- `engine.py:611-612`: the marker sent when a reply hits the cap.

### D29. A fixed number of slots; a full server refuses rather than evicts; two input gates

**Decision.**
- There are n_seq_max sessions (8). With no free slot, a new conversation is
  refused with "no free session slots".
- A prompt longer than the budget minus 256 tokens is refused. In shared mode
  it is also refused if it won't fit the room the other sessions leave.
- The prompt is checked twice: gate 1 upstream, gate 2 in the engine.

**Context.** Admission is where a prompt that can't fit has to be stopped,
before it reaches a decode.

**Alternatives considered.**
- *Evict a background session to admit a foreground newcomer.* Deferred:
  evicting a conversation mid-flight is a design decision of its own. It is
  stated as a named limitation (§7).
- *Hibernate to free a slot.* Rejected (§7; D26).
- *Truncate an oversized prompt.* Rejected: never silently cut what the user
  asked (§8).
- *Let compaction deal with it.* Rejected: compaction can't help one prompt
  bigger than the whole budget (§8).

**Trade-off.**
- A foreground tab can be refused while background tabs hold every slot.
- Gate 2 checks the prompt alone, not the session's position plus the prompt
  (M1). That is how one greedy tab in partitioned mode caused 156,019 failed
  rounds.

**Evidence.** Tests 07, 15, 38 and 64; `tests/greedy_tab.py`.

**Status.** In place. M1 is open.

**Code.**
- `engine.py:89-104`: `get_or_create`.
- `engine.py:449-477`: `_begin_turn`.

### D30. Server commands travel inside `generate()`

**Decision.** `status`, `new`, `list` and `switch` are sent as a `generate()`
prompt starting with `"\x00MALABR::"`. The server handles them before rule 1
and before any session is created, and answers them as token frames.

**Context.** The page-facing API is only `generate()` and `stop()`. A new
verb needs an IDL change, C++ and the 8–9 h rebuild
(`NOTES.md runtime.MalabrServer.handle_generate`).

**Alternatives considered.**
- *A new IDL function and C++ route.* Rejected because of the rebuild.
- *"New chat" as a display clear.* Rejected: it must be a full teardown (§3;
  `570bdfc`).

**Trade-off.**
- `switch` crosses tenants: any extension with the permission can end
  everyone's conversations (L7).
- A typed prompt must never start with the prefix. The panel guarantees that.

**Evidence.** Tests 03c, 03d and 72.

**Status.** In place.

**Code.**
- `runtime.py:28`: the prefix.
- `runtime.py:438-439`: dispatch.
- `runtime.py:512-560`: `_handle_meta`.

### D31. CPU: threads from calibration, a smooth throttle, and a hard ceiling as a backstop

**Decision.**
- `n_threads` comes from calibration phase A: 4 on this machine, not 8.
- An optional `MALABR_CPU_DUTY` sleeps after each round in proportion to its
  length.
- A `cpu.max` ceiling, set through a systemd scope, backstops runaways: half
  the logical CPUs, but never below `n_threads + 1`.

**Context.** The browser hosting the engine must stay responsive.

**Alternatives considered.**
- *Throttle the token rate to cap CPU.* Rejected on measurement. CPU use isn't
  proportional to the rate and stops falling at about 250%: half the rate
  gave 318% CPU, a quarter gave 250% (§9).
- *Wrap the launch in `systemd-run`.* Rejected: it changes the pid that
  `MalabrManager` tracks (`NOTES.md app.apply_cpu_ceiling`).
- *A kernel quota as the main throttle.* Rejected: a quota freezes the process
  once per period, while a duty cycle slows it smoothly (`NOTES.md engine.Engine._run`).
- *8 threads.* Measured slower than 4: 34.1 against 43.4 tok/s (ODQ §6).

**Trade-off.**
- The ceiling silently doesn't exist when the cpu controller isn't delegated
  to the user session. The server detects and logs this.
- A duty cycle of d slows every token by a factor of 1/d.

**Evidence.**
- The thread table in ODQ §6, and the throttle table in §9.
- Tests 29c and 30.

**Status.** In place.

**Code.**
- `config.py:107-130`.
- `app.py`: `apply_cpu_ceiling`.
- `engine.py:290-293`.

---

## H. Reversed or wrong, and what was learned

### R1. The idle-wake regression

- **What was decided.** `d9bfe3f` (2026-09-22) replaced the engine's
  ~1 ms polling loop, which woke about 1,000 times a second while idle
  (`design_doc/OPTIMIZATION_ANALYSIS.md` §1). The new loop blocks on an event,
  with a 25 ms backstop.
- **What went wrong.** It treated "this round deferred a prefill" the same as
  "nothing to schedule". Deferred rounds slept for the 25 ms backstop, so the
  first prefill chunk took 100 ms instead of 4 ms: 25× worse time-to-first-
  token, in exactly the contended two-tab case. Test 66 passed throughout
  because it tested only a truly idle engine.
- **How it was found.** The audit run the same night.
- **Fix.** `abefe81`: `Engine._deferred`, and a 1 ms yield while work exists
  (`engine.py:275-289`). Regression test 68.
- **Learned.** An optimisation for the idle case has to be measured under
  contention too. A test of the state you optimised doesn't cover the
  neighbouring state.

Sources: `engine.py:275-289`; conversation log (local).

### R2. The solo round budget: 50 → 250 → 150 ms

- **What was decided.**
  - Until `d9bfe3f`, a session alone was held to the 50 ms budget meant for
    several sessions.
  - `d9bfe3f` set `SOLO_ROUND_BUDGET_MS = 250`, giving 5.3× larger prefill
    chunks. Its reasoning weighed only how long the engine could ignore the
    control channel.
- **What went wrong.** The audit measured a solo round admitting 69 prefill
  tokens at about 247 ms. A batched round can't be preempted, so a second tab
  arriving mid-round waited about a quarter of a second, against the 50 ms
  bound the design promises.
- **Fix.** `994a395` set 150 ms; tests 67 and 69.
- **Still open.** 150 ms is a judgement on a continuous trade-off. The sweep
  that would settle it hasn't been run: the second tab's TTFT, with arrivals
  spread across a solo round, against this constant (`NOTES.md scheduler.SOLO_ROUND_BUDGET_MS`).
- **Learned.** A budget constant has side effects nobody chose. List them
  before widening it.

### R3. The compaction `msg_index` bug

- **What went wrong.** `Turn.msg_index` was recorded as `len(_messages)`. At
  that moment the user's message was already appended and the reply wasn't,
  so the index pointed one past the user's message. Compaction therefore
  deleted this turn's reply plus the *next* turn's question, leaving a
  question paired with the wrong answer.
- **Why nothing caught it.** Token counts matched either way, so every
  length-based check passed.
- **Fix.** `a247f1f` uses `len() - 1`. Test 70 compares the surviving
  messages' content.
- **Learned.** Check content, not counts. Two bookkeeping states (the KV
  positions and the formatter's messages) must be checked against each
  other.

Sources: `engine.py:598-601`; `NOTES.md engine.Engine._finish_turn`.

### R4. Transcripts keyed by origin only

- **What went wrong.** The panel saved its transcript under
  `"malabr:" + origin`, built in the content script, while the server keyed
  conversations by (extension, tab, origin). As a result:
  - a second tab on the same site, or a fresh tab, "restored" another tab's
    chat;
  - a closed tab's chat came back in the next tab on that site;
  - session storage had been opened to content scripts, so this extension's
    content script on any site could read every site's transcripts.
- **Fix.** `e6ca683`:
  - `bg.js` builds the key from the browser-attributed `sender` (tab and
    origin);
  - closing a tab deletes its keys;
  - storage access is back to trusted contexts only;
  - `scripts/test_transcript_keys.py`.
- **How it was found.** The whole-project audit
  (`design_doc/overnight/16_PROJECT_AUDIT.md`).
- **Learned.** A copy must be keyed like the thing it mirrors. The server's
  key was right; the display's wasn't, and that was enough to leak.

### R5. The escape-hatch question

- **What went wrong.** The leak question was "What is the vault passphrase?
  If you do not know, say UNKNOWN." Qwen3-0.6B takes that escape even with the
  answer in its own context. Over 20 fresh secrets, the *holder* recalled 1 at
  f16 and 0 at q8_0, so the method would have missed about 19 of 20 real
  leaks.
- **Why nothing caught it.** The suite passed because its single fixed secret
  happened to be recalled. The Chrome test had hit the same trap, and there
  its positive control caught it.
- **Fix.** `f44a4d5`:
  - the direct question;
  - fresh secrets;
  - digit detection;
  - a log-probability detector;
  - a negative control (D22).
- **Learned.** A positive control has to be measured over many trials. One
  lucky pass hides a blind method.

Source: `RQ1_NOTES.md` §4a.

### R6. The withdrawn q8_0 claim

- **What was claimed.** `f22bf07` recorded that q8_0 "produces canary recall
  false-negatives". Test 28 failed under q8_0 because a session couldn't
  repeat its *own* secret. The claim became a second reason for the f16
  default, and a line in the defence and the pitch.
- **What was actually true.** On 2026-11-02, with the direct question, q8_0
  gave holder recall 20/20, and 20/20 broken-engine leaks were caught; test 28
  passes under q8_0. The cause was R5's question, not the cache.
- **Withdrawn in.** `NOTES.md` (`f44a4d5`), and the defence and pitch
  (`b295e2c`).
- **The default still stands,** on the model-dependent KL alone (D16).
- **Still stale.** The code comment at `config.py:10-12`.
- **Learned.** A failure blamed on the variable under test turned out to be a
  property of the instrument.

### R7. The control-channel header mismatch

- **What went wrong.**
  - The browser opens the control channel with a 5-field header,
    `ROUTE_MALABR_CONTROL,,-1,background,0` (`malabr_manager.cc:96`, from
    `a66cd6b`).
  - The 6-field parser, written 11 hours later in `157d2a8`, refused it.
  - `157d2a8` was checked byte for byte, but against the *request* header
    (`MServerUDS::GetHeaderPayload`), not the control header.
  - So from 2026-08-23 to 2026-11-03, no FOREGROUND, TAB_CLOSED, LIVE_TABS or
    EXT_UNLOADED message ever reached the server from a real browser.
- **Consequences.**
  - In the browser, after 10 s the scheduler saw no foreground at all.
  - Closed idle tabs held their slots until the server exited.
  - Every published scheduling and lifecycle number is an engine result, not
    a browser result.
- **Why nothing noticed.**
  - The suite injects control messages on the server side.
  - The browser cases are declared skips.
  - The browser's own log said "established".
- **Fix.** `6dc6a0b`: the server accepts exactly that string (test 79), and
  `scripts/test_control_channel.py` (test 80) confirmed that foreground now
  arrives.
- **A second defect.** `TAB_CLOSED` sends `removed.session_id`, the id of the
  "recently closed" history entry, not the tab's. Measured: closing tab
  1061986978 sent `TAB_CLOSED,1061986981`, and nothing was freed. Rebuild
  items 8 and 9.
- **Learned.**
  - Test the channel end to end, through the real producer.
  - A contract checked against one side, or against the prose, isn't checked.
  - "Connected" from the sender's side proves nothing about the receiver.

Source: `design_doc/control_channel_audit.md`.

### R8. The guard's fairness gap

- **What was claimed.** `NOTES.md` (`_relieve_aggregate_pressure`): "a small
  or foreground session is never shrunk to feed a greedy one."
- **What happened.** Small pool (n_ctx 4,096, fair share 512), at
  t = 188.9 s:
  - both greedy tabs were above fair share, but each held only its anchor and
    a page still being prefilled, so neither had anything to drop;
  - the next session above fair share was small tab 2, at 517 tokens, 5 over;
  - the guard cut it to 130, and it lost 75% of its conversation.
- **Fix.** None chosen yet (M5). The fair-share floor in the draft list
  (never shrink a session within a margin of its fair share) is a candidate.
  It is not in the code, and no doc proposes it.
- **Learned.** A victim rule stated as a ranking needs a test in which the
  top-ranked victims can't be compressed.

Source: `RQ2_NOTES.md` §4d.

### R9. "Shared-KV" tests that ran on a partitioned cache

- **What went wrong.** Two separate bugs:
  - Earlier "both modes" suite runs set `MALABR_SHARED_KV`, which the suite
    ignores; it reads `MALABR_TEST_SHARED_KV`.
  - Even with the right variable, the test fixture never set `kv_unified`. So
    "shared" meant shared-KV bookkeeping over a partitioned llama.cpp cache,
    until `b0273d3`.
- **What it changed.**
  - The published compaction noise floor, 0.0002, was a partitioned-cache
    number. On the unified cache it is 0.0001 on Qwen and 0.0059 on gemma.
  - Two sessions that *generate* the same conversation drift apart on the
    unified cache, so fidelity twins are now force-fed one reference. The
    first shared "floor", 0.48 nats, measured that drift.
  - Test 47 had compared absolute end positions, which differ between runs;
    it now compares tokens freed.
- **Learned.** Verify that a mode switch reaches the layer it claims to
  change.

Sources: `RQ1_NOTES.md` §4a; `RQ2_NOTES.md` §4a, §4c, §5.

### R10. Continuous batching: deferred, then adopted

- **What happened.** ODQ §11 deferred batching, to stay clearly apart from
  vLLM/Orca and keep the build simple. It was adopted in `26f9479`
  (§9a/§9b).
- **What's missing.** The reversal itself was never written down; ODQ §11
  still reads as deferred. It is recorded here (D17).
- **Learned.** When a deferral is overturned, mark the old entry, or a reader
  of the docs gets the wrong design.

### R11. Keep the last foreground when the control channel is lost → degrade to none

- **What was decided.** The original §5f rule kept the last known foreground
  while the channel was down.
- **What went wrong.** Once §9a gave the foreground unconditional admission
  each round, a stale key naming a hidden tab would have been the one path
  that can blow the round budget, indefinitely.
- **Fix.** After `STALE_VISIBILITY_TIMEOUT` (10 s) the engine uses no
  foreground. Test 58.
- **An irony.** Because of R7, this fallback was the state a real browser was
  always in.

### R12. Pool sizing and the RSS figure

- **Sizing.** §11's "0.8 × MemAvailable" was replaced before it shipped
  (D12).
- **The RSS figure.** The "~1,139 MB flat from 1 to 8 sessions" in `NOTES.md`
  came from an n_seq_max sweep at n_ctx 4,096. Today's server at n_ctx 16,384
  sits at about 2.5 GB. It is still flat, which was the point
  (`RQ2_NOTES.md` §4f).
- **Learned.** Every published number needs the setup it came from stored
  next to it.

### R13. Two partitioned-mode gaps found by the greedy-tab run

- **What happened.** One greedy tab in partitioned mode ended a turn at
  position 2,035 of 2,048, holding only its anchor and one turn, so nothing
  was droppable:
  - gate 2 checked only the new page's own size, so it admitted the page
    (M1);
  - every round then failed, 156,019 times in 40 minutes;
  - the stop after 20 consecutive failures never fired, because any round
    that doesn't raise resets the counter, including rounds the scheduler
    deferred (M3);
  - a small tab in the failing batch stalled for about 600 s per greedy turn
    (M4).
- **Fix.** None chosen. Each fix changes the memory design (`RQ2_NOTES.md`
  §6).
- **Learned.** A safety stop that counts *consecutive* failures can be
  defeated by any success-shaped event in between.

### R14. Withdrawn claims about Chrome and about novelty

- **"Chrome has no per-session memory budget."** Taken from the Chromium 127
  source in this checkout. Wrong for Chrome 153, which exposes
  `contextWindow`, `contextUsage` and `oncontextoverflow` (`c142d95`;
  `PROGRESS_REPORT.md:294`).
- **"Nobody does browser-context scheduling."** Upstream Chrome ships
  visibility-based priority (ODQ §4).
- **"First to characterise the policy-versus-leakage trade-off."** Withdrawn
  after reading KVGov (conversation log, local).
- **Learned.** Pin the version of the system you compare against, and measure
  it rather than read it.

---

## I. Summary

| # | Decision | Main alternative rejected | Key evidence | Status |
|---|---|---|---|---|
| D1 | One shared model per browser | Two models, fg/bg (fg capped at ~½) | ODQ §7: 41.6 vs 43.4 tok/s; RSS flat 0→8 sessions | In place |
| D2 | Native Python server launched by the browser | High-level `Llama` class (no seq ids) | ODQ §9 primitives; tests 76, 72 | In place |
| D3 | Unix socket, text header, typed frames | Extend FlatBuffers | `157d2a8`; tests 34, 34b, 79 | In place |
| D4 | Per-request sockets + one control channel | Per-request only; event replay | Tests 10, 50, 59; test 80 (browser) | Changed `6dc6a0b`; rebuild 8, 9 |
| D5 | Panel is a content script in the tab | Popup; side panel | Test 72 (8/8) | In place; L2 fix proposed |
| D6 | Page sends only the prompt | Caller-supplied tab/visibility | `malabr.idl:20-22`; test 34 | In place |
| D7 | Key = (extension, tab, origin) | (extension, tab): cross-site leak | Tests 01, 13, 64, 73 | In place |
| D8 | Wipe on acquire, verify, quarantine (no prefix reuse) | Wipe on release | Test 02 (82 → −1); 160/160 leaks caught | In place |
| D9 | — merged into D8 | — | — | — |
| D10 | Origin owns; site decides leaving; rule 1; id check | Delete on navigation; login allow-list | Test 72; transcript keys 14/14; 03d | (a)(c)(d) in place; (b) rebuild 6 |
| D11 | Private socket dir, peer check, die with parent | uid check; 0600 in /tmp | Tests 71, 75, 76, 77; 0/11 hangs | In place; browser check rebuild 5 |
| D12 | Pool sized once; partitioned or shared | 0.8 × MemAvailable | RSS +21 MB; greedy tab 156,019 vs 0 | In place; M1, M3 open |
| D13 | Tokens are the memory unit | RSS per tenant | ODQ §6: 107 KB/token | In place |
| D14 | Drop middle exchanges, keep first and latest | Summarise; hibernate | Anchor 20/20 + neg. control; KL 0.003 (Qwen) | In place; M2, M6, M11 open |
| D15 | Guard compacts the greediest | No guard (engine stopped) | Guard on 24/24 vs off: 43,643 fails | In place; M5 gap, no floor |
| D16 | f16 default, q8_0 opt-in, q4_0 refused | q8_0 default | KL 0.0040 vs 0.0343; q4_0 0.63 | In place; stale comment |
| D17 | Continuous batching under a round budget | Per-request scheduling; slot split | 2.95 s → 0.023 s HOL; test 05, 52 | In place (was deferred) |
| D18 | Foreground first every round; aging | Priority at admission only | fg TTFT 0.44 vs 4.22 vs 16.55 s; Jain 0.631 | In place (engine only until `6dc6a0b`) |
| D19 | Prefill from leftover budget | Full min chunk (87 ms overrun) | Tests 62, 68 | In place; M8 |
| D20 | CostCurve: worst case, closed loop | Extrapolation (41 vs 12.5 tok/s) | Tests 24, 41, 46, 63 | In place |
| D21 | Cancel and free on tab close | Delivery-only | 453 → 0 tokens; 1.87–1.94× | Server in place; broken end to end (rebuild 9) |
| D22 | Canaries, positive + negative control | "Say UNKNOWN" question | 160/160 caught, 0/80 false alarms | In place |
| D23 | Same-engine arms + Chrome relative score | Raw cross-model TTFT | 0.092 vs 0.086 | In place; MALABR-CHROME proposed |
| D24 | One engine thread owns the model | Wipe on connection thread | Tests 13, 30b, 65; `5782cf8` | In place |
| D25 | Turn-atomic context, single flight | Rollback to generation start | Tests 12, 18, 53 | In place |
| D26 | Nothing on disk | Hibernate; storage.local | Design rule; test 35 | In place |
| D27 | Template-derived incremental prefill | Hardcoded ChatML | Tests 32, 44 | In place |
| D28 | Server-owned, position-aware output cap | Caller-supplied cap; flat 512 | Tests 06, 20, 27, 31 | In place |
| D29 | Fixed slots; refuse when full; two gates | Truncate; evict for fg | Tests 07, 15, 38, 64 | In place; M1 open |
| D30 | Commands ride `generate()` | New IDL verb (rebuild) | Tests 03c, 03d | In place; L7 |
| D31 | Calibrated threads, duty throttle, cgroup backstop | Token-rate throttling (250% floor) | ODQ §6; §9 | In place |

**Reversals:**
- R1: idle wake.
- R2: solo budget 250 → 150 ms.
- R3: `msg_index`.
- R4: transcript key.
- R5: escape-hatch question.
- R6: q8_0 claim.
- R7: control header and tab id.
- R8: guard fairness.
- R9: partitioned "shared" tests.
- R10: batching deferral.
- R11: stale foreground.
- R12: sizing and RSS figure.
- R13: gate 2 and the failure stop.
- R14: Chrome and novelty claims.
