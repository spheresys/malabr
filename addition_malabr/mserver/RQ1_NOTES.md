# RQ1 notes: isolation, end to end

RQ1: can many sites share one local model without seeing each other's
conversations? This file traces how the code answers it, and how far the
tests back each part. Every claim cites the code that enforces it, so a
reader can check it. Line numbers are as of commit `4cd7463` (2026-11-02).
The C++ paths are relative to the Chromium root; everything else is relative
to `addition_malabr/`.

Scope: conversation **content** only. Timing, the host page watching the
panel, and other software under the same user account are limitations (§5),
not results.

---

## 1. Who decides what

| Decision | Made by | Why there |
|---|---|---|
| Who is asking: extension, tab, origin, visibility | browser process (`malabr_api.cc`) | Only the browser holds these as its own records; a page can only claim them. |
| Which conversation that is | server, by the key `(extension_id, tab_id, origin)` | `protocol.py:76-81` |
| What memory a conversation may attend to | llama.cpp, by sequence id = the session's slot | `engine.py:556` |
| When a conversation ends | browser events (control channel) plus server rules | §3 |
| What the panel shows | `content.js` and `bg.js`, checked against the server | §3, `content.js:69-129` |

---

## 2. One request, end to end

What happens when a user types into the panel on `https://bank.example` in tab
7, and every check the request passes on the way.

### 2a. In the page (extension content script)

1. **Only the top frame gets a panel.** `content.js:4` returns in any frame
   that isn't `window.top`, and the manifest sets `"all_frames": false`
   (`malabr_chat_extension/manifest.json`). One page is one session, not one
   per iframe. (§12 test 36 is a declared browser-only skip.)
2. **The panel lives in the content script's isolated world** and draws into
   a closed shadow root (`content.js:12-14`). Page scripts can't read its
   variables or its DOM through `shadowRoot`. They **can** see keystrokes:
   key events cross the shadow boundary (§5, L2).
3. `ask()` (`content.js:666`) builds the prompt. On the first turn it
   includes the page text: the typed question, plus the page's text if
   "include page text" is on. A prompt that starts with the meta prefix
   `\x00MALABR::` is a server command (2c, step 5). The panel only sends one
   from `meta()` (`content.js:418`); a typed question or attached page text
   never starts with that prefix.
4. **The transcript is saved under a key the browser attributes.**
   `save(true)` (`content.js:717`) messages `bg.js`. `bg.js` builds the key
   from `sender.tab.id` and `sender.origin` (`bg.js:12-17`), never from the
   message, and refuses opaque origins (`bg.js:15`). Content scripts can't
   touch `chrome.storage.session` themselves. The default access level,
   trusted contexts only, is kept.
5. **Rule 1, mirrored.** Because this save is a chat turn
   (`evictOtherOrigins`), `bg.js` deletes the tab's transcripts for other
   origins (`bg.js:37-44`). That is the same moment the server evicts their
   sessions (2c, step 6).
6. `chrome.malabr.generate({prompt})` (`content.js:721`).

### 2b. In the browser process

1. **Who may call it.** The `malabr` API is exposed to privileged extension
   contexts and content scripts of extensions that declare the `malabr`
   permission (`extensions/common/api/_api_features.json:520-524`,
   `_permission_features.json:550-553`). There is no allow-list: any
   extension that declares the permission can call it. Web pages can't.
2. **The API accepts only a prompt.** `GenerateRequest` has one field
   (`extensions/common/api/malabr.idl:20-22`). `stop()` takes no arguments
   (`malabr.idl:45`).
3. **Identity is derived, not read** (`extensions/browser/api/malabr/malabr_api.cc`):
   - extension id: `extension_id()` (`:45`);
   - tab id: `SessionTabHelper::IdForTab(web_contents)` (`:59`);
   - origin: `render_frame_host()->GetLastCommittedOrigin()` (`:69`);
   - **opaque origins are refused** (`:79-81`), because every opaque origin
     serialises to `"null"` and would collide on one key;
   - visibility: `GetVisibility() == VISIBLE` (`:102-103`). It is a seed for
     a new session only; the authoritative foreground comes from the
     manager (§3).
4. The request gets an unguessable id (`base::UnguessableToken`, `:108`). The
   reply is bound to the calling document through a `WeakDocumentPtr`
   (`:106`).
5. **The socket client** (`mserver_uds.cc`):
   - It refuses prompts over 1 MiB (`:63`, `:83-87`).
   - It connects to `GetMalabrSocketPath()`, which is `$MALABR_SOCKET_PATH`
     or else `/tmp/malabr_v3.sck` (`msocket_uds.cc:18-25`). The browser does
     **not** check who is listening (rebuild fix 5).
   - It retries the connection for up to 180 s (`:38`, `:105-124`).
   - It bounds every read by a timeout, 60 s by default
     (`extension_features.cc:182-185`, applied at `:128-129`).
   - It writes the header
     `route,extension_id,tab_id,origin,visibility,size` (`:286-308`), then
     the prompt.

### 2c. In the server, before the model

1. **The socket is private.** At start, `check_socket_dir` refuses a socket
   directory that isn't a real directory, isn't owned by this user, or is
   group- or world-writable (`malabr_service/runtime.py:44-60`, called at
   `:236` before the pidfile is read). The socket is created with mode 0600
   (`:245`). `run_malabr.sh:83-95` puts it in `$XDG_RUNTIME_DIR` and stops
   rather than fall back to `/tmp`.
2. **The header is bounded before it is read.** The header length is checked
   against `MAX_HEADER_LEN = 4096` before `recv_full` (`runtime.py:288-301`;
   test 34b).
3. **The header is validated** (`protocol.py:97-156`):
   - the route must be one of three;
   - the extension id must be 32 characters in `a-p`;
   - `tab_id >= -1`;
   - the origin must be non-empty and **not `"null"`** (`:133-136`). This is
     a second line of defence behind `malabr_api.cc:79`;
   - visibility must be `foreground` or `background`;
   - the payload must be at most 1 MiB, and control or stop requests must
     declare 0 (test 34).
4. **Stop is keyed like generate.** The stop route acts only on
   `env.session_key` (`runtime.py:304-313`), so a page can't stop another
   tab's reply.
5. **Meta commands are handled before rule 1** (`runtime.py:340-342`,
   `_handle_meta` at `:414`):
   - `status` returns the caller's own conversation id or none. It is
     read-only (`:424-431`; `engine.conversation_id`, `engine.py:146`; test
     03d).
   - `new` tears down the caller's own session (`:432`; tests 03, 03c).
   - `switch` re-execs the server and **ends every tenant's conversation**
     (§5, L7).
6. **Rule 1: cross-origin eviction.** Before creating or reusing a session,
   the server cancels every session with the same extension and tab but a
   different origin (`runtime.py:346`, `engine.evict_other_origins` at
   `engine.py:106-117`). It then waits for that teardown to finish
   (`runtime.py:351`), so the new origin can't be handed the old origin's
   slot mid-teardown (test 64 covers this at full capacity).
7. **Find or create, atomically.** `get_or_create` (`engine.py:89-104`) runs
   under `_reg_lock`: one session per key (test 13 covers 16 concurrent
   admissions). A new session acquires a free slot, which is marked
   *pending wipe* (`slots.py:35-46`). With no free slot, the request fails
   ("no free session slots").
8. **A new session gets a random conversation id**
   (`session.py:93`, `secrets.token_hex(8)`).
9. **The visibility seed is used once**, for a new session and only when no
   foreground is known yet (`runtime.py:362-365`).
10. `submit` (`engine.py:221`) gives this request its own outbox. A newer
    prompt on the **same key** supersedes and rolls back an unfinished one
    (test 12). Other keys are untouched.

### 2d. In the engine and llama.cpp

1. **Wipe before the first token, and check it.** On the engine thread,
   before a session's first prefill chunk, `prepare()` runs `_wipe_and_verify`
   (`engine.py:339-342`, `slots.py:65-84`):
   - `llama_memory_seq_rm(mem, slot, -1, -1)` must return true
     (`slots.py:102-103`);
   - then `llama_memory_seq_pos_max(mem, slot)` must be `-1`
     (`slots.py:106-109`);
   - otherwise the slot is **quarantined**, never returned to the pool, and
     `SlotWipeError` is raised.

   This checks llama.cpp's bookkeeping of which cells belong to the sequence,
   which is what attention uses. It does not zero memory bytes.
   `assert_ready` (`slots.py:86-97`, `engine.py:338`, `:342`) refuses any
   decode into a slot still pending a wipe.
2. **The scheduler** (`scheduler.py:128-209`) picks who runs this round. It
   decides order and timing, not which memory is visible.
3. **One sequence id per token.** `_decode_batch` gives every token exactly
   one sequence id, the session's slot (`engine.py:542-566`, `:555-556`).
   Separation inside a forward pass is then llama.cpp's per-sequence attention
   mask. That code is in the compiled library the venv loads (llama-cpp-python
   0.3.34), not in this repo.

   In shared-KV mode the context sets `kv_unified = true`
   (`app.py:120-122`): every sequence draws cells from one pool, and that
   mask is the only thing separating them. In partitioned mode each sequence
   also has its own region.
4. **One sampler per session**, never shared (`engine.py:518-540`). Logits are
   read at this session's batch index (`engine.py:392`).
5. **Rollback stays within the session.** A superseded, stopped or cancelled
   turn is removed with `seq_rm` on the session's own slot, from its own
   snapshot position (`engine.py:484-501`). The formatter rolls back with it.
   Test 28 shows that even a deliberately wrong `seq_rm` range on one slot
   leaves another slot intact.

### 2e. Back to the page

1. Tokens go into this request's outbox and out as frames of at most 1 MiB
   (`runtime.py:373-392`, `protocol.py:19`). The browser bounds each frame
   before allocating (`mserver_uds.cc:186-214`).
2. `OnToken` (`malabr_api.cc:163-203`) drops the token, and raises the
   abandon flag, if the calling document is gone (`:174-177`). It then calls
   `DispatchEventToSender` with **the calling frame's renderer process**
   (`:196-202`).
3. **Delivery is to the process, not the frame.** Chromium hands the event to
   every script context of this extension in that renderer process
   (`extensions/renderer/dispatcher.cc:768-773`, `ForEach(host_id, nullptr,
   …)`). With Site Isolation, processes are per site, so the possible
   recipients are this extension's content scripts in other same-site frames
   that share the process. `content.js` ignores any request id it did not
   issue (`content.js:535-539`), and ids are unguessable. The filter is this
   extension's own code, not a browser guarantee (§5, L3).
4. Exactly one terminal frame ends the request (`OnComplete`,
   `malabr_api.cc:205-228`). The panel stores the reply, saves, and then asks
   `status` for the conversation id (`content.js:577`, `syncConversation` at
   `:84-103`). If the id changed, the model no longer has the earlier turns.
   The panel keeps only the latest exchange and says so (`:95`).

---

## 3. Lifecycle events

"Transcript" is the panel's copy in `chrome.storage.session`; "model" is the
KV session.

| Event | Transcript | Model | Enforced by | Evidence |
|---|---|---|---|---|
| Reload; another page on the same origin | kept | kept | key unchanged; `content.js:105-129` redraws on a matching id (or, marked unconfirmed, when the server doesn't answer or no id was saved) | test 72 case 1 |
| Tab switched away or minimised | kept | kept, background priority | `malabr_manager.cc:394-428` (active tab of active window, and visible) | test 10 (promotion); rebuild fix 3 for occlusion |
| Tab closed | deleted | deleted | browser: `malabr_manager.cc:500-531` (only `kDeleted`, so drags survive) → `runtime.py:189-192`; panel: `bg.js:60-64` | tests 8, 50; `test_transcript_keys.py` |
| Tab closed while the control channel was down | deleted on reconnect | deleted on reconnect | resync on every connect: `FOREGROUND` + `LIVE_TABS` (`malabr_manager.cc:312`, `:323`, `:353-387`) → `runtime.py:194-201` | test 59 |
| Pass through another origin without chatting | kept | kept | deletion waits for a chat turn on both sides | `test_transcript_keys.py` |
| Chat on another origin in the same tab | old origin's deleted | old origin's deleted (rule 1) | `runtime.py:346-351`, `engine.py:106-117`; `bg.js:37-44` | test 64 (admission); `test_transcript_keys.py`; test 72 case 2 |
| Back to a page served from the back/forward cache | cleared if the model's id differs or is gone | — | `pageshow` (`content.js:764-771`) | test 72 case 2 |
| "New chat" | cleared | deleted | `content.js` clear → `new` meta command → `runtime.py:432-438` | tests 03, 03c |
| Server restart or crash, page open | trimmed to the latest exchange after the next reply | new conversation, new id | `content.js:84-103` | test 72 case 3 |
| Server restart, then reload | dropped | — | `content.js:105-129` | test 72 case 4 |
| Model switch | cleared in the switching tab; other tabs as for a restart | all conversations end | `runtime.py:440-456` (re-exec) | not separately tested |
| Extension disabled, updated or uninstalled | (extension's storage goes with it) | deleted | `malabr_manager.cc:472-488` → `runtime.py:203-206` | skip 16 (browser-only); no unit test dispatches `EXT_UNLOADED` |
| Browser crashes | gone (session storage) | **kept** until the server is killed | no `kill_on_parent_death` (`malabr_manager.cc:242`) | rebuild fix 1 |
| Leave for another site | today: as "pass through" | today: kept until a chat elsewhere | after rebuild: `TAB_NAVIGATED` | rebuild fix 6 |
| Tab discarded by Chrome | kept | today: **kept** (a discard is a `kReplaced` change, `chrome/browser/ui/tabs/tab_strip_model.cc:351-379`, not `kDeleted`) | after rebuild: freed, rebuilt from the transcript | rebuild fix 7 |

Policy in one line: **origin owns a conversation; site decides when the user
has left** (the second half arrives with the rebuild).

---

## 4. The isolation tests

### 4a. Method

- **The canary** is `"The vault passphrase is MAGENTA-7731."`. Planting it
  means asking the session to remember it (`tests/harness.py:32`).
- **A leak** is the reply containing `magenta`, case-insensitive
  (`harness.py:130-132`): yes or no, never a score.
- **The leak question is guarded:** "What is the vault passphrase? If you do
  not know, say UNKNOWN."
- **The model** is Qwen3-0.6B-Q8_0 (`harness.py:21-24`), with an f16 KV
  cache. The suite prints a warning if `MALABR_KV_TYPE` is not f16
  (`test_phase1.py`, `main`).
- **The suite runs in both memory modes**: partitioned, and shared KV
  (`MALABR_TEST_SHARED_KV=1`). The last full run was 70/70 executed and
  passed in both modes, with 18 declared browser-only skips (2026-11-02).
- **Positive controls.** A "no" only counts if the model can recall a canary
  it holds. Tests 12 and 28 provide that control: the same model, canary and
  guarded question, asked of the session that holds the canary, and both
  must recall it. Tests 1, 2, 3 and 5 have **no control of their own**.
- **No broken-version run.** No run has applied the canary tests to a
  deliberately broken engine (for example, wipe disabled) to show they catch
  a real leak. The nearest evidence is a direct measurement: without the
  wipe, a reused slot still reported `pos_max = 12` for a 13-token
  conversation (`design_doc/implementation_notes.md:371`).

### 4b. §12 suite (`tests/test_phase1.py`)

| # | Plants | Asks | Pass | Notes |
|---|---|---|---|---|
| 1 (`:49`) | canary in A = (tab 1, `a.test`) | B = (tab 2, `b.test`), both live | B's reply lacks the canary | Tab **and** origin both differ; no test varies origin alone. |
| 2 (`:60`) | canary in A; A torn down | C = (tab 3, `c.test`), which gets A's slot | C's reply lacks the canary | The `pos_max` part of the pass condition is dead code (precedence: `not r and x or not r` is `not r`), and it is read before the wipe runs. The last run printed `pos_max_at_reuse=42`. Only the canary check decides. |
| 3 (`:78`) | canary in A | same key after cancel + teardown | lacks the canary | "New chat" is a teardown, not a display clear. |
| 03c (`:119`) | canary in A | same key after the `new` meta command | reply `ok` and `ended`, one terminal frame, session gone, no canary | the panel's real path |
| 03d (`:154`) | — | `status` for own key, another origin in the same tab, after teardown, after re-create | correct id, or none; stable id; new id on re-create; nothing created or evicted | 7 cases |
| 5 (`:276`) | canary in A | both A and B submit, then B is asked | lacks the canary | **Does not currently test a shared forward pass**: the last run printed `shared_rounds=0` in both modes. See §5, T1. |
| 8 (`:379`) | long generation | cancel ("tab closed") | session dead, slot freed, terminal frame, within one round | lifecycle, not content |
| 12 (`:436`) | canary in A | supersede a long reply on A, then ask A | superseded, rolled back, **A recalls the canary** | positive control |
| 13 (`:458`) | — | 16 threads admit 16 keys | no slot granted twice | admission race |
| 28 (`:588`) | canary in B | wrong `seq_rm(A, 0, 100000)` on A, then ask B | B's `pos_max` unchanged **and B recalls the canary** | positive control; failed under q8_0 because B could not recall (`NOTES.md`, `config.KV_TYPE`) |
| 34, 34b (`:784`) | — | oversized payload or header | rejected before the read | bounds |
| 50 (`:1065`) | — | `TAB_CLOSED,1` | exactly tab 1's session cancelled | control dispatch, fake engine |
| 59 (`:1234`) | — | `LIVE_TABS,1,3` | exactly tab 2's session cancelled | fake engine |
| 64 (`:1299`) | all slots full | rule-1 eviction then admission | evicted, admitted, new slot pending wipe | admission, not a canary test |
| 71 (`:1735`) | — | socket in `/tmp`, a 1777 directory, a symlink, a missing directory, a planted pidfile | all refused; `main()` returns 4 | socket |

Declared browser-only skips that matter for RQ1: 16 (extension-wide
teardown), 36 (main frame only), 39 (tab-close clears storage), 60
(navigation mid-generation), **61 (cross-origin navigation does not leak)**.
No canary test covers the same tab on a new origin; rule 1 makes it a
different key by construction, and only that is tested.

### 4c. Browser tests (`scripts/`)

| Script | Setup | Checks | Last result |
|---|---|---|---|
| `test_transcript_keys.py` | patched Chromium, MalabrFeature **off** (no model); drives the real `bg.js` from content-script worlds over CDP | content script can't read storage; same-origin second tab and other origin get nothing; reload restores; conversation id round-trips; pass-through deletes nothing; a non-chat save evicts nothing; a chat turn on B deletes the tab's A copy only; tab close deletes only that tab's keys | 14/14. The two mirror checks fail against the pre-`a7ad62e` `bg.js`; the storage-access check fails on `f22bf07` (audit §2) |
| `test_panel_status.py` | full stack via `run_malabr.sh`, scratch profile, private socket; types into the closed shadow DOM over CDP; restarts the server by hand | (1) reload redraws; (2) after a chat on B, Back to A from the back/forward cache clears the stale panel; (3) restart mid-page keeps only the post-restart exchange, and a reload redraws exactly that; (4) restart then reload redraws nothing | 6/6 (5 cases + "server restarted"), run by hand once, 2026-11-02; case 2 really came from the cache |
| `chrome_rq1_isolation.py` | Chrome 153, Gemini Nano on CPU | positive control with a **direct** question; then **guarded** questions from another origin, a second same-origin tab, and a fresh session after `destroy()` | control passed; all three negative (`design_doc/PROGRESS_REPORT.md:250-260`) |

---

## 5. Open limitations

**Content-isolation gaps in the evidence**

- **T1. Batched isolation is untested.** Test 5 never shares a round. Its
  cost table puts one decode at 37–46 ms (`harness.py:28`), and the
  two-contender budget is 50 ms (`scheduler.py:9`), so a second session
  never fits. The claim "no leak within one forward pass" rests on llama.cpp's
  sequence mask, not on a test.
- **T2.** Test 2's residue check is dead code, as noted in §4b.
- **T3.** There is no negative control (a deliberately broken engine that
  must be caught), so the canary method's miss rate is unknown.
- **T4.** Same tab, new origin, has no canary test (skip 61).

**Boundaries left open by design**

- **L1. Timing.** Sessions share the scheduler and the forward pass; latency
  side channels are untested.
- **L2. Keystrokes.** The panel's closed shadow root still lets key events
  reach the host page. Fix without a rebuild: an extension-origin iframe for
  the input, with the content script still calling `generate()` so the key
  keeps the page's origin.
- **L3. Event fan-out.** Replies go to every content-script context of the
  extension in the calling renderer process (§2e). That is same-site by
  Site Isolation, and filtered by unguessable request ids in `content.js`.
  An extension that does not filter would show another same-site tab's reply.
- **L4. Same-user software is trusted, completely.** The server
  authenticates no client: any process that can connect can claim any
  `(extension, tab, origin)` and read that conversation back. It can also
  take over the control channel (`ControlReader.serve` replaces the previous
  connection, `runtime.py:130-144`) and send `TAB_CLOSED` or `LIVE_TABS`.
- **L5. The socket is private only via the launcher.** A browser started
  without `run_malabr.sh` uses `/tmp/malabr_v3.sck` (`msocket_uds.cc:24`).
  The real server refuses that directory, but another account could still
  listen there.
- **L6. No sandbox.** Prompts and page text are attacker-controlled input to
  a Python process with the user's permissions.
- **L7. `switch` is a cross-tenant action.** Any caller (any extension with
  the permission) can end every tenant's conversation by switching the model
  (`runtime.py:440-456`). The panel asks the user first; the server does not.
- **L8. Lossy KV.** Isolation evidence requires an f16 KV cache (test 28
  under q8_0).
- **L9. Model kept after a crash or discard.** It persists after a browser
  crash (fix 1) and after a tab discard (fix 7).
- **L10. The restart note can be wrong.** The panel's "the server restarted"
  note (`content.js:95`) shows for **any** change of conversation id, which
  can also come from a server-side teardown, such as a stalled reader or a
  formatter desync, followed by a new turn.

---

## 6. Rebuild list (C++, needs the 8–9 h build)

The same list appears in `design_doc/session_handoff_v7.md` §4 and the audit,
`design_doc/overnight/16_PROJECT_AUDIT.md` §2b. The audit numbers them
differently.

1. `kill_on_parent_death` when launching the server
   (`malabr_manager.cc:242`), so the model's memory dies with a crashed
   browser.
2. Raise the abandon flag during prefill (today it is raised only in
   `OnToken`, `malabr_api.cc:174-177`). `TAB_CLOSED` already covers this in
   practice; this closes a race between the two paths.
3. Recompute foreground on visibility changes (occluded or minimised without
   an activation change).
4. Scope `EXT_UNLOADED` to its profile (`malabr_manager.cc:472-488` sends the
   bare id).
5. Check the socket peer's uid (`SO_PEERCRED`) before sending. That is the
   browser checking the server. L4, the server checking its clients, is not
   on the list.
6. `TAB_NAVIGATED`: leaving for another site deletes both copies at once.
7. Report tab discards: free the model's memory, keep the transcript, rebuild
   on return with the same conversation id.
