# MALABR — session handoff v7 (2026-11-01)

Supersedes v6 (22 Sep), which is out of date. For example, v6 says the Chrome
baseline could not be obtained; it was obtained in October.

**Companion file (local only, not in the repo):**
`conversation_log_2026-09-22_to_11-01.md`, the full working-session log.

**Reading order for a new session:**
1. This file.
2. `PROGRESS_REPORT.md`, which has all the measured numbers.
3. `overnight/16_PROJECT_AUDIT.md`, the latest audit.
4. `overnight/12_THE_PITCH_WITH_PRIVACY.md` and `overnight/10_DEFENSE_STORY.md`,
   which tell the story for presenting.

---

## 1. What MALABR is, in one paragraph

Today every website or extension that wants an LLM ships its own model, or each
tab runs its own copy. MALABR makes the **browser** own **one** local model and
share it across all tabs, extensions and origins, the way an OS shares a CPU
between processes. Sharing raises four problems, and MALABR answers each one:
- **Isolation:** one site must never see another's conversation.
- **Scheduling:** the tab you are looking at should answer first.
- **Memory:** many conversations must fit in one fixed KV cache.
- **Lifecycle:** closing a tab should free its compute and memory at once.

Everything runs locally on CPU, and nothing is written to disk.

---

## 2. The architecture, high level

```
 ┌──────────────────────────── PATCHED CHROMIUM (C++) ────────────────────────────┐
 │                                                                                 │
 │  Web page ──► content script (chat panel)        MalabrManager (browser-wide)   │
 │               malabr_chat_extension/content.js   chrome/browser/malabr_manager  │
 │                    │  chrome.malabr.generate({prompt})        │                 │
 │                    ▼                                          │ watches windows,│
 │  malabr_api.cc  ── derives IDENTITY itself:                   │ tabs, extensions│
 │                    extension id, tab id, origin, visible?     │                 │
 │                    (page supplies ONLY the prompt)            │                 │
 │                    │                                          │                 │
 │  mserver_uds.cc ── one socket per request                     │ one long-lived  │
 │                    │  streams tokens back                     │ CONTROL socket  │
 └────────────────────┼──────────────────────────────────────────┼─────────────────┘
                      │  Unix socket /tmp/malabr_v3.sck (0600)   │
                      ▼                                          ▼
 ┌──────────────────────────── PYTHON SERVER (addition_malabr/mserver) ────────────┐
 │  app.py           start-up: pid lock, load model, calibrate, serve              │
 │  runtime.py       accepts connections; request handlers + ControlReader         │
 │  engine.py        the loop: sessions, rounds, compaction, turn bookkeeping      │
 │  scheduler.py     WHO decodes this round (latency budget, foreground first)     │
 │  session.py       one conversation = key (extension, tab, origin)               │
 │  slots.py         KV-cache slots, wiped on acquire                              │
 │  formatter.py     chat templates, turn checkpoints                              │
 │  config.py        sizes n_ctx from RAM, threads/CPU caps, KV type               │
 │  protocol.py      wire framing, header, control messages                        │
 │                          │                                                      │
 │                          ▼                                                      │
 │            llama.cpp (via llama_cpp ctypes) — ONE model, ONE KV cache,          │
 │            batched decode across all active sessions                            │
 └─────────────────────────────────────────────────────────────────────────────────┘
```

There are three layers, and each owns one thing:

| Layer | Owns | Why it lives there |
|---|---|---|
| **Chromium (C++)** | *Who is asking*: identity and foreground | Only the browser knows the true tab, origin and visibility. A page can lie; the browser can't be lied to. |
| **Python server** | *Who gets served, and what is kept*: scheduling, memory, compaction | It owns the model and the KV cache, so it is the only place those decisions can be enforced. |
| **Extension (JS)** | *The user interface*: chat panel, page context | It is replaceable; any extension can call `chrome.malabr.generate`. |

### 2a. The two channels between browser and server

1. **Request channel:** one socket per `generate()` call.
   - The browser sends a six-field header (`route, extension_id, tab_id,
     origin, visibility, size`), then the prompt.
   - The server streams frames back: token, complete or error. The C++ side
     re-posts each frame to the UI thread as a `malabr.onToken` /
     `malabr.onComplete` event, sent only to the frame that asked.
   - Limits: 1 MiB per frame and per prompt, a 60 s read timeout, and connect
     retries for up to 180 s.
2. **Control channel:** one long-lived socket from `MalabrManager`. It carries
   facts that no request carries:
   - `FOREGROUND,<tab>` — the active tab of the active window, and only if it's
     visible. One tab id, so "two foreground tabs" can't be expressed.
   - `TAB_CLOSED,<tab>` — cancel and free that tab's sessions. This is the
     *only* way an **idle** session gets freed.
   - `LIVE_TABS,<ids…>` — sent on every (re)connect. The server frees any
     session whose tab isn't listed, which recovers closes lost while the
     channel was down.
   - `EXT_UNLOADED,<ext>` — the extension was disabled, updated or uninstalled.

### 2b. Life of one request

1. You type in the panel. `content.js` calls `chrome.malabr.generate({prompt})`.
2. `malabr_api.cc` works out the extension id, the **tab id**
   (`SessionTabHelper`) and the **origin** (`GetLastCommittedOrigin`), and
   **refuses opaque origins**. It returns a request id at once, then hands the
   work to a background thread.
3. `mserver_uds.cc` connects to the socket and sends the request.
4. `runtime.py` reads it and finds or creates the **session** for
   `(extension, tab, origin)`. A new session gets a **slot**, which is wiped
   before use.
5. **The engine loop**, every round:
   - `scheduler.build_batch` spends a **latency budget**: 50 ms when several
     sessions compete, 150 ms when only one has work.
   - **Foreground first**, unconditionally.
   - Then **aging**: anyone skipped 5 rounds in a row gets in.
   - Then the cheapest sessions, while budget remains.
   - Then whatever budget is left goes to **prefill** chunks for new prompts.
   - A `CostCurve` predicts what each token costs, and corrects itself from
     measured round times.
6. llama.cpp decodes the whole batch in one call. Each new token goes to its
   session's outbox, and that session's handler streams it to the browser.
7. If the conversation nears its memory budget, **compaction** drops the middle
   turns and keeps the first ("anchor") and the most recent ones.
8. Closing the tab sends `TAB_CLOSED`, which cancels generation mid-token and
   frees the slot.

### 2c. Memory model

- `n_ctx` (total KV cells) is computed from available RAM, after reserving 2 GB
  for the browser and subtracting the model. It is bounded to 4096–16384.
- There are 8 slots (`n_seq_max`) by default, one conversation each.
- **Shared-KV mode** (on in the launcher): one pool. A session may use up to
  n_ctx/2, and an aggregate guard compacts when the pool fills. So one or two
  tabs get a large context, and many tabs fall back to fair shares.
- **KV type:** f16 by default; q8_0 is opt-in. q4_0 is refused because it
  measurably damages output.

### 2d. Privacy boundaries: where each one is enforced

| Boundary | Enforced by |
|---|---|
| A page can't claim another tab, origin or foreground | `malabr_api.cc` derives all of them; the IDL accepts only `prompt` |
| Different sites never share a conversation | Origin is part of the session key; opaque origins are refused |
| A reused slot carries nothing over | Wipe-on-acquire (`seq_rm` then check `pos_max == -1`, else `SlotWipeError`) |
| A closed tab's conversation is gone | `TAB_CLOSED`, with `LIVE_TABS` as backstop; since `e6ca683` the panel's copy too (§4) |
| Nothing reaches disk | RAM only (KV cache, `chrome.storage.session`). The one exception is `calibration.json`, which holds timings, not text |
| Other users on the machine | Socket in `$XDG_RUNTIME_DIR` (0700), `chmod 0600`; the server refuses a directory other users can write. The browser doesn't yet check who is listening (`SO_PEERCRED`, rebuild). |

### 2e. Code map

- **C++:**
  - `extensions/browser/api/malabr/` — `malabr_api.cc`, `mserver_uds.cc`,
    `msocket_uds.cc`
  - `extensions/common/api/malabr.idl`
  - `chrome/browser/malabr_manager.cc`
  - The feature flags are `MalabrFeature` (starts the server) and
    `MalabrTunables`.
- **Python:**
  - `addition_malabr/mserver/app.py` and `malabr_service/*.py`
  - The *why* behind the code is in `mserver/NOTES.md`. The code points into it
    with `[notes: module.name]`.
- **Extension:** `addition_malabr/mserver/malabr_chat_extension/`
  (`content.js` is the panel; `bg.js` stores transcripts).
- **Tests:**
  - `mserver/tests/test_phase1.py` is the §12 suite, 70 tests. Run it in both
    modes with `/home/chaitu/Desktop/vscode/malabr/bin/python`.
  - Measurement scripts: `fidelity_check.py`, `kv_quant_check.py`,
    `baseline_bench.py`, `pipeline_bench.py`, with shared maths in `metrics.py`.
- **Scripts:**
  - `addition_malabr/scripts/run_malabr.sh` is the launcher.
  - `chrome_rq*.py` and `nano_*.py` form the Chrome Prompt API harness.
  - `test_page_context*.py` and `test_transcript_keys.py` are the browser tests.
- **Launch:** `EXTENSION_DIR=addition_malabr/mserver/malabr_chat_extension
  addition_malabr/scripts/run_malabr.sh`

### 2f. When a conversation ends

Each conversation exists twice: the panel's transcript (what the user sees, in
`chrome.storage.session`) and the model's memory (the KV session). They must
end together. When they disagree, the server wins: the panel asks it
(`status`) before redrawing a transcript.

| What happens | Result | Status |
|---|---|---|
| Tab closed | both deleted | done (`TAB_CLOSED`; `bg.js` on `tabs.onRemoved`) |
| Reload, or navigation within the same origin | both kept | done |
| Tab hidden or frozen | both kept; lower scheduling priority | done |
| Pass through another origin on the same site (a login at `accounts.bank.com`) | nothing deleted | done: deletion waits for a chat turn |
| Leave for another site | both deleted at the navigation | rebuild (`TAB_NAVIGATED`). Today: deleted when you chat on the new site |
| Chat on any other origin, same site or not | the tab's previous conversation is replaced | done (rule 1; `bg.js` mirrors it) |
| Back/forward cache return | panel re-asks the server and clears if it's gone | done |
| Server restart, crash or model switch | transcript dropped on the next load | done. Not noticed while the page stays open |
| Tab discarded by the browser | model memory dropped, rebuilt from the transcript on return | rebuild, plus a re-prefill feature |

- **Origin owns a conversation; site decides when you've left.** Origin is the
  web's own security boundary and what the C++ already derives, so a sibling
  origin never reads another's conversation. Site-level leaving avoids wiping
  the chat on same-site login bounces. Chromium already has the public suffix
  list (`net::SchemefulSite`), so the site check costs nothing in C++; the
  extension never needs its own copy, because the transcript follows the
  server through `status`.
- **Known cost:** a login hosted on another site (Google, Microsoft, Okta)
  counts as leaving and will wipe the conversation after the rebuild. Accepted
  rather than a grace period or an allow-list of login providers.
- **The back/forward-cache check stays after the rebuild.** `TAB_NAVIGATED`
  clears the server and storage, not a frozen page's memory, which comes back
  with the old chat still drawn.

---

## 3. What we can claim (the measured results)

Full tables and methods are in `PROGRESS_REPORT.md`.

**Scheduling.** Comparing three schedulers on MALABR's own engine, same model:
8 tabs, with the foreground request arriving last.

| | FIFO | equal-share | **MALABR** |
|---|---|---|---|
| foreground TTFT | 16.55 s | 4.22 s | **0.44 s** |
| Jain fairness | 0.998 | 0.997 | 0.631 |

Throughput and makespan are within noise across the three. The cost is
fairness, plus two background stalls of more than 1 s. Present it as a
trade-off.

**Against Chrome's built-in Prompt API** (Chrome 153, Gemini Nano, CPU):

- **Relative score.** Foreground TTFT divided by median TTFT; it has no units,
  so it is fair across different models. MALABR scores 0.092 ± 0.009 and
  Chrome 0.086 ± 0.012, which is **indistinguishable**. Chrome *does*
  prioritise the foreground.
- **Where priority is applied** (the defensible scheduling RQ). Chrome applies
  it *at admission*, then serves tabs nearly equally (Jain ≈ 0.99). MALABR
  applies it *at every decode round* (Jain 0.63).
- **Isolation.** Chrome passes too. MALABR's contribution here is the
  verification method, not a defect in Chrome.
- **Lifecycle reclamation** (strong differentiator). When tabs close, Chrome's
  survivors speed up 1.04×, against 1.11× in the control run. MALABR goes from
  453 wasted tokens to 0, and the survivors finish 1.94× sooner.

**Compaction fidelity.** 58–64% of the context is dropped. The KL divergence is
0.0017–0.0032 nats, against a noise floor of 0.0002.

**KV quantization:**
- q8_0: KL 0.0040 on Qwen and 0.0343 on gemma, so the cost varies about 10×
  between models.
- q4_0: KL 0.63 and 0.14, which rules it out.

**Lessons learned the hard way:**
- Conclusions from single runs were wrong twice; use n ≥ 3.
- Warm-up dominates first runs.
- A lossy KV cache gives false negatives on canary-recall tests.

---

## 4. What changed in this session (Sep 22 → Nov 1)

- **Measured:** the Chrome baseline, the relative score, the whole-pipeline
  metrics, each RQ re-tested against Chrome, compaction fidelity, and KV
  quantization.
- **Code fixes:**
  - The idle-wait regression.
  - The solo budget set to 150 ms.
  - The `msg_index` off-by-one in compaction.
- **Overnight research brief:** `overnight/01`–`15`, covering related work,
  ideas, RQs, the thesis outline, the defence story and the pitch.
- **Whole-project audit:** `overnight/16_PROJECT_AUDIT.md`. Its commits:
  - `41a06a3`: unused imports removed; shared `tests/metrics.py`.
  - `f22bf07`: q8_0 result written into `NOTES.md`; long inline comments moved
    to `NOTES.md`.
  - `1ae2a24`: the launcher logs `MALABR_*` settings per run, and its
    staleness guard covers all MALABR C++ files.
  - **`e6ca683`: chat-panel privacy bug fixed.** Transcripts were keyed by
    origin only, so a new tab, or the next tab after a close, showed another
    tab's chat, and every site could read every site's transcripts. They are
    now keyed by (tab, origin), built in `bg.js` from data the browser
    attributes to the message. A closed tab's transcript is deleted.
    `scripts/test_transcript_keys.py` passes 8/8.
- **C++ audited for the first time, read-only.** It is sound. Ranked fixes that
  each need the 8–9 h rebuild:
  1. Set `kill_on_parent_death`, so the server dies with a crashed browser.
  2. Raise the abandon flag earlier, during prefill.
  3. Recompute foreground when a window is hidden or occluded.
  4. Scope `EXT_UNLOADED` to its profile.
  5. Check the socket peer's uid (`SO_PEERCRED`) before sending. For now the
     launcher's private socket directory and the server's check cover it.
  6. `TAB_NAVIGATED`: delete both copies when a tab leaves for another site
     (§2f).
  7. Discarded tabs: drop the model memory, rebuild it from the transcript on
     return (§2f). Also needs a server re-prefill feature.
- **`a34926a`: socket moved to a private per-user directory** (finding 4 in
  the audit). The launcher uses `$XDG_RUNTIME_DIR`; the server refuses shared
  directories. Test 71.
- **`a7ad62e`: transcript and model memory kept in step (§2f).**
  - A `status` meta command: does the model still hold this tab's
    conversation? Read-only. Test 03d.
  - The panel redraws a saved transcript only on "yes", and asks again on a
    back/forward-cache return.
  - `bg.js` mirrors rule 1: a chat turn on a new origin deletes the tab's
    other-origin transcripts, the same moment the server evicts them.
    `test_transcript_keys.py` passes 13/13; the two new mirror checks fail
    against the old `bg.js`.

---

## 5. Open items

**Yours to do:**
- **Push.** Both remotes are at `c0b16d4`; the commits after it are local
  only.
- `design_doc/` is tracked (force-added; still in `.gitignore`) and pushed,
  except the conversation log, which stays local.
- Add `usb-storage.quirks=152d:0578:u` to the kernel command line.
- Run `fsck -f` on the SSD.

**Decisions pending:**
- Delete the dead legacy directories? These are `baseline/`, `benchmark_data/`,
  `plots/`, the two `read*server*extension` dirs, `uploads/`,
  `mserver/malabr_test_extension/`, and two stale learning docs.
- Do a rebuild for the C++ fixes above?

**Research directions** (`overnight/06_RESEARCH_QUESTIONS.md`): these are
choices, not tasks.
- **C1 — repayable foreground priority:** BVT-style warp, or a bound on
  background delay.
- **C2 — timing channels:** does cross-origin timing leak through the shared
  scheduler?
- **C3 — heterogeneous workloads and scaling.**
