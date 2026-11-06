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
| What the panel shows | `content.js` and `bg.js`, checked against the server | §3, `content.js:69-131` |

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
3. `ask()` (`content.js:668`) builds the prompt. On the first turn it
   includes the page text: the typed question, plus the page's text if
   "include page text" is on. A prompt that starts with the meta prefix
   `\x00MALABR::` is a server command (2c, step 5). The panel only sends one
   from `meta()` (`content.js:420`); a typed question or attached page text
   never starts with that prefix.
4. **The transcript is saved under a key the browser attributes.**
   `save(true)` (`content.js:719`) messages `bg.js`. `bg.js` builds the key
   from `sender.tab.id` and `sender.origin` (`bg.js:12-17`), never from the
   message, and refuses opaque origins (`bg.js:15`). Content scripts can't
   touch `chrome.storage.session` themselves. The default access level,
   trusted contexts only, is kept.
5. **Rule 1, mirrored.** Because this save is a chat turn
   (`evictOtherOrigins`), `bg.js` deletes the tab's transcripts for other
   origins (`bg.js:37-44`). That is the same moment the server evicts their
   sessions (2c, step 6).
6. `chrome.malabr.generate({prompt})` (`content.js:723`).

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
   group- or world-writable (`malabr_service/runtime.py:93-109`, called at
   `:290` before the pidfile is read). The socket is created with mode 0600
   (`:299`). `run_malabr.sh:83-95` puts it in `$XDG_RUNTIME_DIR` and stops
   rather than fall back to `/tmp`.
2. **Only the browser may connect.** Each accepted connection's peer, as the
   kernel recorded it (`SO_PEERCRED`), must be the process that started the
   server: `os.getppid()` at start, i.e. the browser
   (`runtime.py:350-367`, checked in `serve_forever` at `:334`; test 75).
   Anything else is closed unanswered and logged. A model switch keeps this
   valid, because `os.execv` (`:573`) keeps the pid and the parent. The
   opt-out `MALABR_TRUST_ANY_PEER=1` is for servers started by hand or by
   tests, and prints a banner (`:300-307`).
3. **The header is bounded before it is read.** The header length is checked
   against `MAX_HEADER_LEN = 4096` before `recv_full` (`runtime.py:381-394`;
   test 34b).
4. **The header is validated** (`protocol.py:97-156`):
   - the route must be one of three;
   - the extension id must be 32 characters in `a-p`;
   - `tab_id >= -1`;
   - the origin must be non-empty and **not `"null"`** (`:133-136`). This is
     a second line of defence behind `malabr_api.cc:79`;
   - visibility must be `foreground` or `background`;
   - the payload must be at most 1 MiB, and control or stop requests must
     declare 0 (test 34).
5. **Stop is keyed like generate.** The stop route acts only on
   `env.session_key` (`runtime.py:397-406`), so a page can't stop another
   tab's reply.
6. **Meta commands are handled before rule 1** (`runtime.py:433-435`,
   `_handle_meta` at `:507`):
   - `status` returns the caller's own conversation id or none. It is
     read-only (`:517-523`; `engine.conversation_id`, `engine.py:146`; test
     03d).
   - `new` tears down the caller's own session (`:525`; tests 03, 03c).
   - `switch` re-execs the server and **ends every tenant's conversation**
     (§5, L7).
7. **Rule 1: cross-origin eviction.** Before creating or reusing a session,
   the server cancels every session with the same extension and tab but a
   different origin (`runtime.py:439`, `engine.evict_other_origins` at
   `engine.py:106-117`). It then waits for that teardown to finish
   (`runtime.py:444`), so the new origin can't be handed the old origin's
   slot mid-teardown (test 64 covers this at full capacity).
8. **Find or create, atomically.** `get_or_create` (`engine.py:89-104`) runs
   under `_reg_lock`: one session per key (test 13 covers 16 concurrent
   admissions). A new session acquires a free slot, which is marked
   *pending wipe* (`slots.py:35-46`). With no free slot, the request fails
   ("no free session slots").
9. **A new session gets a random conversation id**
   (`session.py:93`, `secrets.token_hex(8)`).
10. **The visibility seed is used once**, for a new session and only when no
   foreground is known yet (`runtime.py:455-458`).
11. `submit` (`engine.py:221`) gives this request its own outbox. A newer
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
   (`app.py:124-126`): every sequence draws cells from one pool, and that
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
   (`runtime.py:466-485`, `protocol.py:19`). The browser bounds each frame
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
   issue (`content.js:537-541`), and ids are unguessable. The filter is this
   extension's own code, not a browser guarantee (§5, L3).
4. Exactly one terminal frame ends the request (`OnComplete`,
   `malabr_api.cc:205-228`). The panel stores the reply, saves, and then asks
   `status` for the conversation id (`content.js:579`, `syncConversation` at
   `:84-103`). If the id changed, the model no longer has the earlier turns.
   The panel keeps only the latest exchange and says so (`:97`).

---

## 3. Lifecycle events

"Transcript" is the panel's copy in `chrome.storage.session`; "model" is the
KV session.

| Event | Transcript | Model | Enforced by | Evidence |
|---|---|---|---|---|
| Reload; another page on the same origin | kept | kept | key unchanged; `content.js:107-131` redraws on a matching id (or, marked unconfirmed, when the server doesn't answer or no id was saved) | test 72 case 1 |
| Tab switched away or minimised | kept | kept, background priority | `malabr_manager.cc:394-428` (active tab of active window, and visible) | test 10 (promotion); rebuild fix 3 for occlusion |
| Tab closed | deleted | deleted | browser: `malabr_manager.cc:500-531` (only `kDeleted`, so drags survive) → `runtime.py:238-241`; panel: `bg.js:60-64` | tests 8, 50; `test_transcript_keys.py` |
| Tab closed while the control channel was down | deleted on reconnect | deleted on reconnect | resync on every connect: `FOREGROUND` + `LIVE_TABS` (`malabr_manager.cc:312`, `:323`, `:353-387`) → `runtime.py:243-250` | test 59 |
| Pass through another origin without chatting | kept | kept | deletion waits for a chat turn on both sides | `test_transcript_keys.py` |
| Chat on another origin in the same tab | old origin's deleted | old origin's deleted (rule 1) | `runtime.py:439-444`, `engine.py:106-117`; `bg.js:37-44` | test 64 (admission); `test_transcript_keys.py`; test 72 case 2 |
| Back to a page served from the back/forward cache | cleared if the model's id differs or is gone | — | `pageshow` (`content.js:766-773`) | test 72 case 2 |
| "New chat" | cleared | deleted | `content.js` clear → `new` meta command → `runtime.py:525-531` | tests 03, 03c |
| Server restart or crash, page open | trimmed to the latest exchange after the next reply | new conversation, new id | `content.js:84-105` | test 72 case 3 |
| Server restart, then reload | dropped | — | `content.js:107-131` | test 72 case 4 |
| Model switch | cleared in the switching tab; other tabs as for a restart | all conversations end | `runtime.py:533-549` (re-exec) | not separately tested |
| Extension disabled, updated or uninstalled | (extension's storage goes with it) | deleted | `malabr_manager.cc:472-488` → `runtime.py:252-255` | skip 16 (browser-only); no unit test dispatches `EXT_UNLOADED` |
| Browser crashes | gone (session storage) | deleted: the server exits with the browser | `PR_SET_PDEATHSIG` + startup check (`runtime.die_with_parent`, armed in `app.py:200-206`); one-shot SIGTERM handler (`runtime.run_server`) | tests 76, 77; real browser crashes: the server exited 11/11 after the fix (2 hangs in 7 before it) |
| Leave for another site | today: as "pass through" | today: kept until a chat elsewhere | after rebuild: `TAB_NAVIGATED` | rebuild fix 6 |
| Tab discarded by Chrome | kept | today: **kept** (a discard is a `kReplaced` change, `chrome/browser/ui/tabs/tab_strip_model.cc:351-379`, not `kDeleted`) | after rebuild: freed, rebuilt from the transcript | rebuild fix 7 |

Policy in one line: **origin owns a conversation; site decides when the user
has left** (the second half arrives with the rebuild).

**Caveat found 2026-11-03: the control channel never worked in a real
browser.** The browser opens it with a 5-field header that the server refused
(fixed server-side, test 79; rebuild item 8). So until then, the rows that
depend on a browser control event were true of the server only, never end to
end. Those rows are: tab closed, tab closed while the channel was down, tab
switched away, and extension unloaded. With the fix, foreground and LIVE_TABS
reach the server (test 80). **Tab close still doesn't free the model's session
in a real browser**, because TAB_CLOSED carries the wrong tab id (rebuild item
9). Rows driven by the per-request connection were never affected: rule 1,
reload, back/forward cache, "New chat", restart, and crash. Full list:
`design_doc/control_channel_audit.md`.

---

## 4. The isolation tests

### 4a. Method

- **The secret.** The suite plants `"The vault passphrase is MAGENTA-7731."`
  (`tests/harness.py:34`). The miss-rate runs generate a fresh one per trial:
  a word and six random digits (`harness.py:158`, `fresh_secret`).
- **One question, asked of everyone:** "Repeat back the vault passphrase I
  gave you, exactly." (`harness.py:149`). The holder is asked it as the
  positive control; every other session is asked it as the leak check.
- **Why not the old question.** Until 2026-11-02 the leak question was
  guarded: "What is the vault passphrase? If you do not know, say UNKNOWN."
  Qwen3-0.6B takes that escape hatch even with the answer in its own
  context. Over 20 fresh secrets the **holder** recalled 1 at f16 and 0 at
  q8_0, so the method would have missed about 19 of 20 real leaks. The suite
  passed only because its one secret, `MAGENTA-7731`, happened to be
  recalled. The Chrome test had hit the same trap in its control.
- **Two detectors.**
  - **Reply:** the secret's digits appear in the answer, allowing separators
    such as "77 31" (`harness.py:135`). Digits, not the word: a model asked
    for a passphrase it never saw may make up a word, not these digits.
  - **Score:** the model's mean log-probability per digit, teacher-forced
    after "The vault passphrase is WORD-" (`harness.py:173`,
    `secret_logprob`). The threshold, fixed before any run, is a mean
    probability per digit above 1/2 (`harness.py:170`). It reads the model's
    belief directly, so it doesn't depend on what the model chooses to say.
    The miss-rate runs and test 74 use it; the other suite tests use the
    reply detector.
- **Positive control in every leak test.** Tests 1, 2, 3, 5 and 73 ask the
  holder first, and a pass requires it to recall. Tests 12 and 28 are
  recall checks themselves.
- **Negative control: a deliberately broken engine** (`harness.py:208`,
  `leak_trial`). The method must report these leaks:
  - *stale slot*: the wipe is skipped and the new session continues at the
    slot's next position;
  - *shared sequence*: a live session's sequence is handed to a second
    session.

  Skipping the wipe while starting the new session at position 0 is **not**
  a leak in this llama.cpp: `llama_decode` refuses it (`rc=-1`), because
  positions in a sequence must be consecutive. That is a second guard,
  independent of the wipe.
- **The model** is Qwen3-0.6B-Q8_0 (`harness.py:23-26`).
- **Both memory modes.** The suite runs partitioned and with shared KV; the
  harness reads `MALABR_TEST_SHARED_KV=1`. Earlier "both modes" runs set
  only `MALABR_SHARED_KV`, which the suite ignores, so before 2026-11-02 the
  shared-KV pass was really a second partitioned run.
- **The cache must match the mode, too.** Until later on 2026-11-02 the test
  fixture never set `kv_unified`, so even with the right variable "shared
  KV" meant shared-KV *bookkeeping* over a *partitioned* llama.cpp cache:
  sessions never shared one pool of cells. The fixture now builds a unified
  cache whenever `MALABR_TEST_SHARED_KV=1` (`harness.py`, `Fixture`). Every
  shared-KV result below says which cache it ran on.

### 4b. Is the method trustworthy? (`tests/negative_control.py`)

20 fresh secrets per variant. Trusted only if the holder recalls at least
19 of 20 in every variant; the script refuses (exit 2) otherwise.

| Run | Seed | Holder recall (each variant) | Broken engine caught (reply / score) | Correct engine false alarms (reply / score) | Correct-engine score range | Broken-engine score |
|---|---|---|---|---|---|---|
| f16, partitioned | 1539520127 | 20/20 | 40/40 / 40/40 | 0/20 / 0/20 | −3.61 to −2.67 | 0.00 |
| q8_0, partitioned | 2978590849 | 20/20 | 40/40 / 40/40 | 0/20 / 0/20 | −3.84 to −2.72 | 0.00 |
| f16, shared-KV bookkeeping, **partitioned cache** | 3366340333 | 20/20 | 40/40 / 40/40 | 0/20 / 0/20 | −3.36 to −2.37 | 0.00 |
| f16, shared KV, **unified cache** (fixture fixed) | 3540045651 | 20/20 | 40/40 / 40/40 | 0/20 / 0/20 | −3.29 to −2.84 | 0.00 |

- With the direct question, no real leak was missed in 160 broken-engine
  trials, by either detector, and there were no false alarms in 80
  correct-engine trials. The row that matters most for the shared design is
  the unified-cache one: there, sessions draw cells from one pool, and only
  llama.cpp's per-sequence mask keeps them apart.
- **Does a neighbour's content reach a session's output on the unified
  cache?** Not measurably. With identical, force-fed conversations, a
  neighbour full of very different content (French, with its own secret)
  changed the session's next-token distributions by exactly 0.0000, and the
  neighbour's secret stayed at chance. Cell ORDER alone does move the output
  slightly: identical tokens whose cells land out of order (reused holes)
  differ by up to 0.0007 nats, the same whatever content once filled those
  holes (`RQ2_NOTES.md`, noise floor). Future work, as a hypothesis only:
  since placement depends on other tabs' activity, a tab's output may depend
  very slightly on its neighbours' activity, though not their content.
- The score separates completely. A session that never saw the secret puts
  about 1/10 to 1/40 on each digit (chance for a random digit), while a
  leaking one is certain.
- **q8_0 did not hurt recall.** The earlier "q8_0 makes isolation tests pass
  for the wrong reason" came from test 28 failing under q8_0 with the old
  question; with the direct question, test 28 and every isolation test pass
  under q8_0. This is one model; run `negative_control.py` before trusting
  another model or KV type.

### 4c. §12 suite (`tests/test_phase1.py`)

Last run, 2026-11-02, 18 declared browser-only skips in each mode:
partitioned 75/75; shared KV 74/75. The one failure was test 47, an RQ2
compaction test: its pass condition compared absolute end positions, and its
two runs can start at different positions (510 vs 496; seen in both modes).
Both freed 382 tokens; batch did it in 1 call against 5. Fixed the same day
to compare tokens freed; it then passed once partitioned and twice under
shared KV.

| # | Plants | Asks | Pass | Last detail |
|---|---|---|---|---|
| 1 (`:51`) | secret in A = (tab 1, `a.test`) | A (control), then B = (tab 2, `b.test`), both live | A repeats it, B doesn't | Tab and origin both differ; test 73 varies origin alone |
| 2 (`:63`) | secret in A; A torn down | C = (tab 3, `c.test`), which gets A's slot | A repeats it before teardown; the slot is reused; `pos_max` ≥ 0 before the wipe and −1 after; C doesn't repeat it | `pos_max 82 → -1` |
| 3 (`:89`) | secret in A | A, then the same key after cancel + teardown | repeats it before, not after | "New chat" is a teardown, not a display clear |
| 03c (`:142`) | secret in A | same key after the `new` meta command | reply `ok`/`ended`, one terminal frame, session gone, secret not repeated | the panel's real path |
| 03d (`:177`) | — | `status` for its own key, another origin in the same tab, after teardown, after re-create | correct id or none; stable; new id on re-create; nothing created or evicted | 7 cases |
| 5 (`:299`) | secret in A | A and B asked **at the same time** | A repeats it, B doesn't, **and** at least one `llama_decode` call held both sequences | `shared_rounds=16/21`. The round budget is lifted for this test only (scheduling, not the forward pass) |
| 8 (`:423`) | long generation | cancel ("tab closed") | dead, slot freed, terminal frame, within one round | lifecycle |
| 12 (`:480`) | secret in A | supersede a long reply on A, then ask A | superseded, rolled back, A repeats it | recall check |
| 13 (`:502`) | — | 16 threads admit 16 keys | no slot granted twice | admission race |
| 28 (`:632`) | secret in B | wrong `seq_rm(A, 0, 100000)`, then ask B | B's `pos_max` unchanged and B repeats it | passes under q8_0 too |
| 34, 34b (`:828`) | — | oversized payload or header | rejected before the read | bounds |
| 50 (`:1109`), 59 (`:1278`) | — | `TAB_CLOSED,1`; `LIVE_TABS,1,3` | exactly the right session cancelled | fake engine |
| 64 (`:1343`) | all slots full | rule-1 eviction then admission | evicted, admitted, slot pending wipe | admission |
| 71 (`:1779`) | — | socket in `/tmp`, 1777 directory, symlink, missing directory, planted pidfile | all refused; `main()` returns 4 | socket |
| 73 (`:1833`) | secret in (ext, tab 1, A) via `handle_generate` | A (control), then (ext, tab 1, B) | A repeats it, A's session evicted, B doesn't repeat it | server half of skip 61 |
| 74 (`:1861`) | secret in A | correct, stale-slot and shared-sequence engines | holder recalls in all; the correct engine flagged by neither detector; both broken engines flagged by both | scores −2.97 / 0.00 / 0.00 |
| 75 (`:1877`) | — | status over a real socket: as a non-parent, as the expected parent, with the opt-out | refused; served; served, with the banner | 5 cases |
| 76 (`:1951`) | — | a child arms the parent-death signal, then its parent exits; a late start | the child dies; the late start is refused | |
| 77 (`:2006`) | — | SIGTERM twice while the server's thread holds the pool lock | the server exits and removes its socket | fails (hangs) with the old handler |

Declared browser-only skips that matter for RQ1: 16, 36, 39, 60, and 61
(its server half is now test 73).

### 4d. Browser tests (`scripts/`)

| Script | Setup | Checks | Last result |
|---|---|---|---|
| `test_transcript_keys.py` | patched Chromium, MalabrFeature **off**; drives the real `bg.js` over CDP | content script can't read storage; other tab or origin gets nothing; reload restores; conversation id round-trips; pass-through deletes nothing; a non-chat save evicts nothing; a chat turn elsewhere deletes only this tab's old copy; tab close deletes only that tab's keys | 14/14 in 7 consecutive runs, after 1 failure in the first run of the day (the failing check wasn't captured; not reproduced) |
| `test_panel_status.py` | full stack via `run_malabr.sh`; types into the closed shadow DOM over CDP; restarts the server by hand | a non-browser program is refused; reload redraws; Back from the back/forward cache clears a stale panel; restart mid-page keeps only the new exchange, and reload redraws exactly that; restart then reload redraws nothing; the hand-restarted server warns about the opt-out; killing the browser takes its server down | 8/8 on 2026-11-02, after the shutdown fix; case 2 came from the back/forward cache |
| `test_control_channel.py` | full stack via `run_malabr.sh`, 2 session slots | the server accepts the control channel; activating a tab moves the server's foreground; with both slots held a third tab is refused, and closing a tab frees its slot | 2026-11-03, before the server fix: 1/4 (header refused). After it: 3/4. Closing a tab still frees nothing, because TAB_CLOSED carries the history entry's id (rebuild item 9) |
| `chrome_rq1_isolation.py` | Chrome 153, Gemini Nano on CPU; fresh six-digit secret, the direct question for control and leaks | control; cross-origin; second same-origin tab; fresh session after `destroy()` | 5/5 on 2026-11-02 (secret `MAGENTA-477146`). The first attempt that day failed before planting: "unable to create a session" although `availability()` said available |

---

## 5. Open limitations

**Closed on 2026-11-02**
- Batching is now tested (test 5 requires shared forward passes).
- Test 2 checks the wipe itself, before and after.
- The method has a negative control and a measured miss rate (§4b).
- Same tab, new origin has a canary test at server level (73).
- Other local programs can no longer pose as the browser (L4, partly).
- The server no longer outlives a crashed browser (old L9, crash half).
- The panel's note no longer names a cause it can't know (old L10).

**Still open**

- **L1. Timing.** Sessions share the scheduler and forward passes; latency
  side channels are untested.
- **L2. Keystrokes.** Key events cross the panel's closed shadow root to the
  host page. Fix without a rebuild: an extension-origin iframe for the
  input, with the content script still calling `generate()` so the key
  keeps the page's origin.
- **L3. Event fan-out.** Replies go to every content-script context of the
  extension in the calling renderer process (§2e): same-site by Site
  Isolation, filtered by unguessable request ids in `content.js`.
- **L4. Same-user software, what's left.** Other programs can no longer
  connect as the browser (§2c step 2). They can still kill the server and
  listen in its place, because the browser doesn't check who it's talking
  to (rebuild fix 5). And the opt-out `MALABR_TRUST_ANY_PEER=1` reopens
  everything; it prints a banner.
- **L5. The socket is private only via the launcher.** Without
  `run_malabr.sh` the browser uses `/tmp/malabr_v3.sck`
  (`msocket_uds.cc:24`).
- **L6. No sandbox** for the Python server.
- **L7. `switch` is cross-tenant.** Any extension with the `malabr`
  permission can end every conversation by switching the model
  (`runtime.py:533-549`). Other programs no longer can (L4).
- **L8. Tab discard** keeps the model's memory (rebuild fix 7).
- **L9. One model.** Every measurement uses Qwen3-0.6B. The question trap
  shows how much a small model's habits shape a canary test; run
  `negative_control.py` again for any other model.
- ~~**L10. Other tests with the old question.**~~ Closed 2026-11-02: the
  compaction tests (04, 09, 47, 53) now use the direct question with fresh
  secrets, and test 78 is their negative control (`RQ2_NOTES.md` §4b).

---

## 6. Rebuild list (C++, needs the 8–9 h build)

The same list is in `design_doc/session_handoff_v7.md` §4 and the audit,
`design_doc/overnight/16_PROJECT_AUDIT.md` §2b (numbered differently there).

1. ~~`kill_on_parent_death`~~ **Done without a rebuild** (2026-11-02): the
   server arms `PR_SET_PDEATHSIG` itself (§3, browser crash). Setting
   `kill_on_parent_death` in `malabr_manager.cc:242` would still be a cheap
   second layer.
2. Raise the abandon flag during prefill (today only in `OnToken`,
   `malabr_api.cc:174-177`). `TAB_CLOSED` already covers this in practice;
   this closes a race between the two paths.
3. Recompute foreground on visibility changes (occluded or minimised without
   an activation change).
4. Scope `EXT_UNLOADED` to its profile (`malabr_manager.cc:472-488`).
5. Check the server before sending: compare the socket peer's **pid** with
   the server process the browser launched (`SO_PEERCRED`), not its uid. Any
   program running as this user has the same uid (L4).
6. `TAB_NAVIGATED`: leaving for another site deletes both copies at once.
7. Report tab discards: free the model's memory, keep the transcript, rebuild
   on return with the same conversation id.
8. Send a 6-field control header. Today's browser opens the control channel
   with `ROUTE_MALABR_CONTROL,,-1,background,0` (`malabr_manager.cc:96`).
   Until 2026-11-03 the server refused it (6 fields required), so no
   FOREGROUND, TAB_CLOSED, LIVE_TABS or EXT_UNLOADED ever reached the server
   from a real browser. The server now accepts exactly that string
   (`protocol.LEGACY_CONTROL_HEADER`, test 79); drop the special case once
   the browser sends 6 fields.
9. Send the closed tab's own id in TAB_CLOSED. `OnTabStripModelChanged`
   (`malabr_manager.cc`, the `kRemoved` branch) prefers
   `removed.session_id`, which is the id of the "recently closed" history
   entry `CreateHistoricalTab` makes (`tab_strip_model.cc:456-458`), not the
   tab's. Measured 2026-11-03: closing tab 1061986978 sent `TAB_CLOSED,1061986981`,
   and nothing was freed. Use `SessionTabHelper::IdForTab(removed.contents)`
   always. Until then a closed idle tab keeps its slot until the server exits
   (test 80, case 2b).
   **Source changed 2026-11-07, not built yet:** it passes a syntax check with the
   build's flags, but the running browser is the 2026-08-28 build, so test 80's
   case 2b still fails until a rebuild.
