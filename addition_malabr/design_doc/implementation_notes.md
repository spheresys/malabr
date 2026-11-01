    # MALABR Phase 1 — implementation notes

Working notes kept alongside `phase1_design.md` while the Python side was
built. Records (a) how far the build order has got, (b) facts established by
measurement that should not be re-asserted without re-measuring, and (c) points
where the design document turned out to be wrong or underspecified and an
implementation decision had to be taken.

## OVERNIGHT RUN — 2026-09-22 night (read this first on any wakeup)

Handed the machine for autonomous audit + fixes + testing. Standing rules in
force: commit locally EVERY cycle, **never push**, never start a Chromium
build (VS Code is an ancestor of the session; killing it kills the loop), and
update this section each cycle.

Baseline at start: HEAD `8ddf372`, working tree clean, §12 suite 62/62 in both
partitioned and shared-KV modes.

### Queue, in priority order

1. **AUDIT 3 — this session's own new code.** Everything written today has had
   far less scrutiny than the code audits 1 and 2 covered, and it touches the
   hot path. Targets, in order of risk:
   - `Engine.wake()` / `_run` idle block — the flag-then-wake ordering, the
     clear-after-wait, every producer actually wired, and whether any path can
     leave work queued with nobody woken.
   - `SOLO_ROUND_BUDGET_MS` — the `contenders <= 1` test uses `id(s)` on
     objects from two lists; confirm no aliasing or state-transition window
     makes a contended round look solo.
   - `content.js` rAF coalescing — a pending frame across teardown, "new chat",
     model switch or supersede; `flushPaint` on a session already removed.
   - `content.js` page extraction — `closest(DROP_SEL)` cost on large DOMs,
     pathological pages (no block elements, one huge `<p>`, deeply nested
     tables), and the `paragraphs()` single-newline fallback on minified text.
   - The four new measurement scripts: they monkey-patch `Engine._decode_batch`
     and `_run_round`; confirm they always restore, and that `Probe` really
     leaves no residue in the KV.
2. **Chrome Nano baseline.** Download stalled at 163 MB / "Foreground
   Installing 0%" for ~35 min under Chrome 153. Retry across browser restarts
   (component installs commonly unstick that way). If it lands, build the
   N-tab relative harness: foreground TTFT / median TTFT WITHIN each system,
   which is the comparison that survives the model differing.
3. **Wire q8_0 KV** in `config.py` + re-run calibration (bytes/token changes).
   Fidelity already measured: top-1 4/4, KL 0.0040 vs a 0.0002 noise floor.
   q4_0 is REJECTED (2/4, KL 0.63).
4. **Convert browser-only SKIPs** using `scripts/test_page_context.py` as the
   pattern (stdlib CDP + headless Chrome + fixture page).

### Cycle log

- (start) baseline recorded, queue set.
- **Cycle 1 — audit 3, finding 1: REAL BUG, introduced this session, fixed.**
  The idle-wake change conflated two states in `_run_round`'s `return False`:
  "nothing to schedule" and "something to schedule, deferred this round".
  `build_batch` deliberately defers a prefill that cannot afford a whole
  `PREFILL_CHUNK_MIN` chunk, for up to `MAX_PREFILL_STALLS` rounds, and
  NOTHING wakes the engine out of that — the only thing that clears it is
  running more rounds. So those rounds took the 25 ms idle backstop instead of
  the old 1 ms yield: **first prefill chunk went from 4 ms to 100 ms**, a 25x
  TTFT regression in precisely the contended two-tab case the project is about.
  Test 66 passed throughout because it only ever exercised a genuinely idle
  engine. Fix: `Engine._deferred`, set when a round schedules nothing but
  runnable sessions exist; `_run` keeps the 1 ms yield in that case and blocks
  only when truly idle. Regression test 68. Suite 63/63 both modes.
- **Cycle 1 — audit 3, finding 2: design trade, resolved but FLAG FOR REVIEW.**
  `SOLO_ROUND_BUDGET_MS` has a second effect I did not account for when I set
  it: a batched round cannot be preempted, so the solo budget is also the
  worst-case wait for a tab that ARRIVES mid-round. Measured: a solo round
  admits 69 prefill tokens and costs ~247 ms, so at 250 ms a second tab waited
  a quarter second against section 7's stated 50 ms bound. The penalty lands
  on the multi-tab arrival path -- the case the thesis is about -- while the
  benefit lands on the single-tab path, which it is not. Lowered to **150 ms**
  (still 3x the fair budget, ~3x fewer prefill chunks) and pinned by test 69.
  **This is a judgement call on a continuous trade-off, not a measured
  optimum** -- the settling experiment is end-to-end TTFT for a second tab
  arriving at a uniformly random point inside a solo round, swept over the
  constant. Chaitanya may want a different point; the reasoning is in the
  constant's comment. Suite 64/64 both modes.
- **Cycle 1 — audit 3, findings 3 and 4: fixed.**
  (3) `fidelity_check.Probe.next_token_dist` removed the probe's KV cells and
  restored `pos` only on the success path. A failed decode would have left the
  probe resident and every LATER probe in that run would have measured a
  context nobody intended -- as wrong numbers, not an error. Now in a
  `finally`. Re-ran §8 after the fix: 64% dropped, top-1 4/4, KL 0.0032 vs a
  ~0 noise floor, i.e. the reported conclusion is reproducible; only the
  fraction dropped moves run to run (compaction fires at different depths).
  Docs updated to quote RANGES (58-64%, 0.0017-0.0032) rather than one run.
  (4) `content.js`: "new chat" and model-switch emptied the log without
  cancelling pending animation frames or clearing `live`, so a late token
  could schedule a paint against a detached bubble and a late `onComplete`
  could append a note to a turn the user had dismissed. Pre-existing, but the
  frame coalescing made it worth closing: added `abandonLive()` on both paths.
  Also removed dead `counting_round` from `cancellation_bench.py` and asserted
  its slot->key attribution assumption instead of leaving it implicit.
  Cancellation re-run: 453 -> 0 wasted tokens, survivors 1.94x sooner.

- **Cycle 2 — Nano download: diagnosed as far as it can be from outside; PARKED.**
  Chrome 153 (branded, extracted to
  `/media/chaitu/chaitanya/chrome-stable-deb/`) gets *everything* right except
  the last step:
  - Manifest component `ceofadde...` v1.20260913.2 **installed**.
  - Manifest Criteria **all true**: enabled by feature / enterprise policy /
    user setting, VRAM 11444 MiB vs 3000 required, device capable, and disk
    **280219 MiB available vs 20480 required** (the "0 MiB" seen earlier was
    the Chrome 142 profile, not this one).
  - `optimizationguide-pa.googleapis.com/v1:GetModels` returns **HTTP 200**
    with 15,477 bytes of protobuf — so the model server answers.
  - And yet `nano_v3_gpu_component` sits at **"Foreground Installing", 0%**,
    `prompt_api` reports **"Pending Assets"**, and the netlog shows **no CDN
    fetch ever issued** (no edgedl/dl.google request for weights).
  A browser restart does not unstick it. Conclusion: Chrome has the metadata
  and has decided not to fetch the bytes yet — download scheduling internal to
  Chrome, not something reachable from flags, disk, or the update protocol.
  Left running so it can progress unattended. **If it is still 0% in the
  morning, treat the Chrome arm as unavailable and ship the two same-model
  baselines (FIFO and equal-share/`--parallel`), which are the stronger
  comparison anyway.**

- **Cycle 2 — queue item 3 (q8_0 KV): wired, but the default is DELIBERATELY
  still f16, and the reason is a methodological finding.**
  `config.KV_TYPE` + `MALABR_KV_TYPE` now select the KV element type, and
  `BYTES_PER_TOKEN` is derived from it (f16 107 KB/token, q8_0 56.8 KB, i.e.
  1.88x the resident context). `app.py` sets `cp.type_k/type_v`; the test
  Fixture mirrors it so the suite can run under either.

  I intended to adopt q8_0 as the default on the strength of the Qwen
  measurement (top-1 4/4, KL 0.0040). Two new results stopped that:

  1. **The cost is model-dependent by ~10x.** On gemma-3-1b (SWA) q8_0 costs
     **KL 0.0343**, against Qwen's 0.0040 — roughly ten times what §8
     compaction itself costs. Top-1 held on both, so nothing is broken, but a
     default that moves the output distribution by a model-dependent amount
     would silently move every number measured afterwards.
  2. **q8_0 produces canary RECALL FALSE-NEGATIVES, which attacks the §12
     methodology itself.** Running the suite under q8_0 fails test 28 — and
     the failure is NOT the isolation half (`b_after == b_pos-1` holds, so
     nothing leaked or corrupted). It is that session B could not reproduce
     **its own** planted canary, answering "UNKNOWN" with its KV provably
     intact. The whole suite proves isolation by checking a session does NOT
     recall another's canary; if a lossy cache degrades recall in general,
     a leak test can pass **for the wrong reason**. Test 4 still passed under
     q8_0, so the effect is probabilistic — which makes it worse, not better.

  Action: default stays f16; q8_0 is opt-in with the per-model numbers in the
  constant's comment; and `tests/test_phase1.py` now prints a loud banner when
  `MALABR_KV_TYPE != f16` saying the canary results are not isolation evidence
  in that mode. Suite 64/64 both modes at the f16 default.

  **For the morning:** adopting q8_0 is a real 1.88x context win and is a
  decision about research claims, not config — it needs a per-model
  `kv_quant_check.py` run against whatever model ships, and any isolation
  evidence must be regenerated at f16.

- **Cycle 3 — audit 3, findings 5 and 6: page extraction under pathological
  DOMs. Both fixed.** New fixtures + `scripts/test_page_context_patho.py`
  cover the pages extraction was NOT designed for: no block elements at all,
  one enormous paragraph, deeply nested tables, 1500 paragraphs behind 200 nav
  links, and an empty body. Every case now returns something sane, stays
  inside the budget, and finishes fast enough for the send path (it runs
  synchronously when you hit send).

  (5) **Forced layout.** `linkDensity()` read `innerText`, which is defined in
  terms of RENDERED text and therefore forces layout on every read. Scoring a
  large page calls it once per candidate — thousands of reflows. Switched to
  `textContent` (link density is a ratio; the difference washes out):
  **308 ms -> 133 ms** on the 1500-paragraph page. Threshold pinned at 250 ms.

  (6) **Budget thrown away.** `selectRelevant()` kept only paragraphs scoring
  above zero against the question. On a page of many near-identical
  paragraphs few score at all, so it returned **817 of 4000 characters** —
  handing the model a fifth of the context it could have had. Added a final
  document-order fill of whatever the relevance pass left unclaimed;
  relevance still decides what is kept first. Now fills the budget: 4000/4000.

  Suites: patho 15/15, original page-context 9/9.

- **Cycle 4 — Nano: CLOSED as unavailable. Stop spending time on it.**
  Final attempts: browser restart, switching the model-quality selector to
  "Fastest Inference", and the internals page's "Load Default". None moved it.
  After ~2.5 hours `nano_v3_gpu_component` is still "Foreground Installing,
  0%", the profile is still 157 MB, and `prompt_api` still reports "Pending
  Assets".

  Everything Chrome needs is verifiably in place -- manifest component
  installed, all Manifest Criteria true (280 GB free, VRAM 11444 vs 3000
  required, device capable), and `optimizationguide-pa.googleapis.com
  /v1:GetModels` answering HTTP 200 with 15 KB of protobuf. Chrome has the
  metadata and simply never issues the CDN fetch for the weights. That is
  download scheduling internal to Chrome; nothing reachable from flags, disk,
  the update protocol, or the internals page changes it.

  **Verdict: the Chrome Prompt API arm is not obtainable on this machine.**
  The monitor has been retired rather than re-armed. The two same-model
  baselines (FIFO and equal-share/`--parallel`) stand, and they isolate the
  scheduling variable better than a Nano comparison would have -- a different
  model, size and quantization would have confounded it. If a Chrome arm is
  ever wanted it should be a RELATIVE measurement inside Chrome (foreground
  TTFT / median TTFT as N tabs contend), which is comparable to MALABR's own
  ratio without either system's absolute speed entering.

- **Cycle 4 — queue item 4: two §12 SKIPs were mislabelled, now real tests.**
  Not every "needs a real browser" entry actually did. Converted:
  - **44, EOS assumption re-verified on a model swap.** Swapping models is a
    server-side operation and the assumption is about a *vocabulary*, so it
    needs no browser. This is design-doc correction #10 made executable:
    §5b described the stop check as `tok == llama_vocab_eos(vocab)`, and the
    test now shows why that is wrong for every model shipped here —
    Qwen3-0.6B flags **6** tokens as end-of-generation (eos=151645) and
    gemma-3-1b flags **3** (eos=1), so `== eos` would miss 5 and 2
    respectively and the turn would run to the output cap instead of
    stopping. Loads both models and asserts `is_stop_token` accepts every EOG
    token while `eos` alone demonstrably does not.
  - **48, compaction fidelity.** Was a "cheap stub" only because no real
    fidelity check existed; `tests/fidelity_check.py` now does. Converted to a
    fast in-suite version: two sessions driven identically, one compacted
    through the shipping `Engine.compact`, same probe decoded into both,
    argmax compared. Deliberately not a duplicate of test 4 — test 4 asks
    whether a fact can still be RECALLED (binary and generous), this asks
    whether the model still says the same next token, which is the claim §8
    actually makes. Result: 50% of context dropped, argmax identical.

  Suite now **66 executed, 66 passed, 18 skipped** (was 62/62/20), both modes.
  Runtime 80 s.

- **Cycle 5 — audit 3, finding 7: REAL CORRECTNESS BUG in compaction, present
  before tonight, fixed.** This is the most serious thing the audit found.

  `_finish_turn` recorded `Turn.msg_index` as `len(s.formatter._messages)`
  with the comment "index of the user msg". It was not. At that point
  `user_turn()` has already appended the user message and
  `assistant_generated()` has not yet appended the reply, so the user message
  is at `len()-1`. `msg_index` was therefore **user_index + 1** on every turn.

  `compact()` does `drop_messages(turn.msg_index, 2)`. With the offset it
  deleted **this turn's ASSISTANT reply plus the NEXT turn's USER question**,
  leaving this turn's question orphaned and the next turn's answer attached to
  a question that no longer existed. Demonstrated directly: a two-turn
  conversation `[user0, asst0, user1, asst1]` had `msg_index = [1, 3]`, so
  dropping turn 0 removed `asst0` and `user1`.

  Observable effect: after compacting a 6-turn conversation with
  `keep_recent=True`, the surviving user messages were the anchor and
  *"Explain what a hash table is"* — the SECOND turn — instead of the anchor
  and the most recent turn. The model was being shown a conversation that
  never happened.

  **Why two prior audits missed it:** every existing check was length-based.
  Two messages are removed either way, so token counts matched exactly, and
  test 40 ("formatter and KV agree") passed throughout. Nothing compared
  CONTENT.

  Fix: `len(s.formatter._messages) - 1`. After it, the survivors are the
  anchor and the newest turn, as `keep_recent` promises. New test **70**
  compares surviving message TEXT, not counts, so the same class of bug cannot
  return. §8 fidelity re-measured after the fix and is unchanged (64% dropped,
  top-1 4/4, KL 0.0032).

- **Cycle 5 — queue item 4: SKIP 47 converted, and it answers its own open
  question.** §12 asked for "comparison run, not assumed"; the default had
  simply been picked. Both strategies drop the same whole turns in the same
  order: batch reaches 510->222 / 382 freed / 4 turns in **1 call**,
  incremental in **5**. So they are outcome-equivalent and batch is the
  cheaper default — now measured. The assertion is the decision criterion
  (reclaims at least as much, in no more calls) rather than bitwise equality,
  because under shared KV the aggregate guard can make the two runs diverge.
  Canary recall is reported as informational, not gated, since that is a model
  property test 4 already covers properly.

- **Cycle 5 — audit 3: `kv_quant_check.py` lifecycle fixed.** It loaded the
  ~1 GB model once per KV type and freed neither model nor context if a decode
  raised — three leaked contexts on a 14 GB machine already at 10 GB. Now the
  model is loaded once by `main()` and the context is freed in a `finally`.
  Numbers unchanged (q8_0 KL 0.0040, q4_0 0.6278), confirming the restructure
  is behaviour-preserving.

  Suite now **68 executed, 68 passed, 17 skipped**, both modes.

- **Cycle 6 — relative baseline delivered; Chrome row remains empty.**
  Re-ran the three-arm benchmark after the compaction fix (numbers move
  run-to-run; conclusions do not). Relative score = foreground TTFT / median
  TTFT, 8 tabs, foreground last:

      fifo      1.83      (foreground is WORSE than the median tab)
      parallel  1.20      (indifferent -- cannot tell tabs apart)
      malabr    0.10      (foreground ~10x faster than the typical tab)

  Control (foreground first): 0.02 / 0.06 / 0.07 -- uninformative by design.

  `scripts/chrome_relative_bench.py` written and exercised: it opens N tabs,
  each with its own LanguageModel session, brings the last to the front, fires
  all prompts at once and reports the same ratio. Verified it exits cleanly
  with "not usable here (availability: 'downloading')" rather than fabricating
  numbers. Nano has now sat at 158 MB / 0% for ~13 hours; the Chrome arm is
  confirmed unavailable on this machine and the script is ready to run
  unchanged wherever the model does download.

- **Cycle 7 — NANO DOWNLOAD SOLVED: it was the GPU variant, not the API.**
  The Prompt API was never the problem and is fully usable in Chrome 153.
  `LanguageModel` is exposed, every criterion passes, `GetModels` returns 200.
  What stalled was WHICH model Chrome chose.

  `manifest.binarypb` inside the manifest component lists BOTH
  `nano_v3_gpu_component` (2025.8.8.1141) and `nano_v3_cpu_component`
  (2025.8.21.1028). Chrome detected 11444 MiB of "VRAM" on the Intel iGPU,
  decided the device was GPU-capable, and selected the GPU variant -- which
  then sat at "Foreground Installing, 0%" for 13 hours without ever issuing a
  CDN fetch.

  Launching with **`--disable-gpu --disable-software-rasterizer`** makes Chrome
  select `nano_v3_cpu_component`, and it downloads immediately:
  `downloadprogress` advanced 0.07 -> 0.51 within three minutes.

  Two useful notes: (a) the component ID is the SHA-256 of the public key,
  first 16 bytes, nibble-mapped 0-f -> a-p -- verified by deriving
  `fklghjjljmnfjoepjmlobpekiapffcja` from the manifest's hash
  `5ab6799b...`, which is why both variants share one ID and the Omaha `ap`
  parameter does not select between them (tested: every `ap` value returns
  noupdate for 153); (b) the CPU variant is also the RIGHT baseline here,
  since MALABR is CPU-only -- comparing against a GPU-accelerated Nano would
  have confounded the scheduler comparison with an accelerator difference.

  Launcher: `scripts/nano153.sh` plus the `--disable-gpu` flags; profile
  `/media/chaitu/chaitanya/nano-cpu-profile`.

### Next up (cycle 8)

Remaining browser-only SKIPs genuinely need the patched Chromium plus a running
server (chrome.malabr API), which is a heavier harness than tonight's headless
CDP pattern — candidates are 47 (incremental vs batch eviction canary) which
may also be convertible server-side, then 51/57 (control-connection behaviour)
which are not. Also still open: `kv_quant_check.py` lifecycle review.

Remaining: `kv_quant_check.py` context/model lifecycle review, then queue item
4 (convert browser-only §12 SKIPs via the CDP harness — `test_page_context.py`
and `test_page_context_patho.py` are the working pattern).

Remaining audit-3 targets not yet examined: `content.js` page extraction under
pathological DOMs (no block elements, one huge `<p>`, deep tables, minified
text), `closest(DROP_SEL)` cost on large pages, and `kv_quant_check.py`'s
context/model lifecycle. Then queue items 2-4.

## Environment

- Python 3.10 conda environment; `llama-cpp-python` 0.3.34.
- Test models: `addition_malabr/models/Qwen3-0.6B-Q8_0.gguf`,
  `gemma-3-1b-it-Q4_K_M.gguf`.
- `malabr_service/__init__.py` now imports nothing at package import time.
  It previously did `from .runtime import run_server`, dragging every importer
  through `ML.Request` -> `flatbuffers` (not installed), which made `engine.py`
  impossible to import standalone.
- Development machine is memory-constrained; use a small `n_ctx` in tests.

## Build order — status

1. [DONE] SlotAllocator + wipe-on-acquire        engine.py
2. [DONE] chat template application (§6c) — ChatFormatter, model-agnostic
3. [DONE] single-threaded engine loop, EOS / output cap (+ §6d streamer, §8 cap)
4. [DONE] compaction (§8) + position-shift fix + input-cap gate 2
5. [DONE] scheduler §9a/§9b (round budget, aging, chunked prefill, closed loop)
then:
6. [DONE] protocol.py — 6-field header, frames, payload bound, control msgs
7. [DONE] runtime.py — control route, reader thread, PID lock
8. [DONE] config.py — startup-computed n_ctx/n_threads, calibration path
9. [DONE] calibration.py — §11a phases A–G + Phase B-prefill
10.[DONE] deleted supervisor.py/training.py/storage.py; wrote real app.py
11.[DONE] §12 harness — 45 executed, 20 declared browser-only skips

## Verified facts from measurement

- KV leak is REAL: slot reused without wipe still reports pos_max=12 for a
  13-token conversation. Wipe-on-acquire clears to -1.
- `llama_memory_seq_rm(mem,slot,-1,-1)` returned True in every case tried
  (full, partial, empty slot) on Qwen3. Could NOT force a False. The return
  check in _wipe_and_verify is DEFENSIVE, not observed-necessary.
- Wiping one seq does not disturb another (slot 3 wiped, slot 4 kept pos_max=19).
- The SlotAllocator lock: an unlocked pop() does NOT hand out duplicate slots
  (list.pop() is atomic under the GIL — 0 duplicates measured). The real race is
  CHECK-THEN-ACT: 120 IndexErrors / 160 acquires with a 2ms widened window and no
  lock; 0 with the lock. Symptom is a crash on a request that should have been a
  clean "no capacity" reject.
- Concurrency and scheduler tests must be run against a deliberately broken
  implementation to confirm they can fail at all. A first attempt at the slot
  race test PASSED against a lock-free allocator, i.e. it had no detection
  power. This applies directly to §12 tests 52/55/62/63.

## Design-document corrections and open decisions

**STATUS: all 14 folded into `phase1_design.md` (2026-09-22).** Each affected
section (5b, 5f, 6b, 6c, 7, 8, 9a, 9b, 10, 10a, 11) now opens with a
`> **CORRECTION**` block citing the item number below. The list is kept here as
the reasoning and the evidence; the design doc carries the verdict, so a reader
of §6c can no longer act on the wrong fragment without seeing that it is wrong.


1. **§6c's incremental fragment is WRONG as written; replaced with a derived
   delta.** The doc says subsequent turns append a hardcoded
   `<|im_start|>{role}\n{content}<|im_end|>\n`. That is ChatML, i.e.
   Qwen-specific, and it is also wrong for the assistant turn (turn 1 with
   add_ass=True already leaves that block OPEN, so a fragment would duplicate
   the header). ChatFormatter instead renders the full conversation with the
   model's own template and takes the suffix past what is already in KV.
   Verified on gemma-3, whose delta begins `<end_of_turn>` — the hardcoded
   ChatML version would have silently malformed every turn after the first.
   **Recommend correcting §6c itself.**
2. **SlotAllocator must NOT touch the model from a connection thread — §7 is
   self-contradictory here.** §7's sketch has `acquire()` call
   `llama_memory_seq_rm`, while §7's own concurrency note says connection
   threads create sessions concurrently, and §7 separately names
   single-threadedness a load-bearing invariant. Those cannot all hold.
   `llama.h` states "The API is thread-safe" EXACTLY ONCE, scoped to the
   Tokenization section (line 1131); the Memory section (line 711) has no such
   guarantee. Wiping KV from a connection thread while the engine thread is
   inside `llama_decode()` is an undocumented data race whose symptom is
   corrupted KV — silently wrong output, not a crash.
   FIX APPLIED: acquire()/release() are now pure bookkeeping (any thread);
   `prepare(slot)` does the wipe and is ENGINE THREAD ONLY; `assert_ready(slot)`
   guards the decode path so a missed wipe is a loud error instead of a leak.
   The release-side "belt and braces" wipe was DROPPED — it would have to run on
   a connection thread, and wipe-on-acquire exists precisely because release
   cannot be trusted. **Recommend correcting §7.**
3. **§6b's rollback target for GENERATING is not usable; rolling the WHOLE
   turn back instead.** §6b says a GENERATING session rolls back to
   `pos_before_generation`, keeping the user message and the generation prompt
   in KV. That is not a valid boundary: the KV at that point ends inside an
   OPEN assistant block (the template's trailing generation prompt), so
   appending a new user turn after it produces malformed structure — caught by
   ChatFormatter's prefix check, not by reasoning. Rolling back to
   `pos_before_request` is also what §6b's OWN stated rule requires ("a turn
   only enters permanent context if it reaches EOS or the output cap"). A turn
   is the user message AND its response, so both go. `pos_before_generation` is
   still tracked — §8 compaction must shift it, and resume/regenerate would
   need it. **Recommend correcting §6b.**
4. **§6b and §6c were never reconciled: rollback must undo the FORMATTER too.**
   §6b specifies rollback purely as KV positions; §6c later added a second
   per-session state (the rendered conversation) that must roll back in
   lockstep. Rolling one without the other desyncs the formatter from the KV
   and the next turn is built on a conversation the model never saw. Added
   `ChatFormatter.checkpoint()/restore()`, captured in `_begin_turn` at exactly
   the same moment as `pos_before_request`. **Recommend §6b naming this.**
5. **§8 compaction must drop the FORMATTER's messages too — same gap as §6b.**
   §8 specifies compaction entirely as KV positions and never mentions the
   rendered conversation. Dropping KV without dropping the matching messages
   leaves `_rendered` describing content the model no longer has. Added
   `ChatFormatter.drop_messages()`, called from `compact()`, plus `msg_index`
   on each Turn (shifted like the positions). Verified: formatter render length
   == s.pos after compaction (330 == 330). **Recommend §8 name this.**
6. **Turn boundary = [user-delta start, response end), and that choice is
   load-bearing.** §8 says "aligned to chat-template boundaries" without saying
   which. This one works because every delta BEGINS with the terminator closing
   the preceding assistant block (a consequence of §6c deriving deltas from the
   template) — so dropping a whole exchange leaves the survivor's block closed
   by the next surviving delta, well-formed with no repair step.
7. **§9a's aging pass cannot rescue a session costing more than the whole
   budget — its prose and its code disagree.** §9a says a deep session "simply
   runs alone in its own round, spending the whole budget on itself, which
   harms nobody else". Its code gives that unconditional-first-pick escape ONLY
   to foreground. Measured: a background session at pos 6000 (100ms vs a 50ms
   round) was excluded 60/60 rounds with NO foreground present — permanent
   starvation, not the bounded limit §9a describes. FIX: aged background gets
   the same `or not picks` escape. Streak then bounded at exactly 5.
   **Recommend correcting §9a.**
8. **§9b's prefill chunk-minimum rule starves, taken literally.** §9b says
   "if n < PREFILL_CHUNK_MIN and n < remaining: continue  # wait for a rounder
   budget". If decode picks keep leaving less than a minimum chunk's budget,
   that holds EVERY round and the prompt is never prefilled. Measured: 200
   rounds, zero chunks. §9b bounds decode starvation with aging but leaves
   prefill unbounded. FIX: MAX_PREFILL_STALLS, then force one minimum chunk —
   overruns the bound by at most one chunk, which is finite, unlike never
   answering. **Recommend correcting §9b.**
9. **Where protocol.py actually broke is NOT what the handoff says.** The
   handoff and §10a state it "splits the header into 3 fields and raises
   otherwise". It used `split(",", 2)` — maxsplit=2 — so a 6-field header
   yields exactly THREE parts, the count check PASSES, and it dies later on
   `int()` of the merged remainder with "invalid payload size". Same outcome
   (everything rejected), different mechanism. Minor, but the handoff's
   description is wrong. Also: `mserver_uds.cc`'s comment claims origin may be
   the literal "null" when opaque; `malabr_api.cc:77` rejects opaque origins
   before that point, so the comment is stale.
10. **Stop condition uses `llama_vocab_is_eog`, not `== llama_vocab_eos`.**
   §5b describes the EOS check as verified-correct, but eos() returns ONE token
   while Qwen3 flags SIX as end-of-generation (gemma-3 flags 3). Comparing
   against eos alone would miss the rest and run to the output cap.
   **Recommend correcting §5b.**

### Further corrections found while building runtime.py

11. **The outbox must be per-REQUEST, not per-session.** §7 calls it "the
    session's outbox". Under §6b's single-flight replace TWO handlers are
    briefly alive: the superseded one waiting for its terminal frame and the
    new one waiting for tokens. One shared queue means whichever polls first
    steals the other's frames — the superseded client could receive the new
    response, or hang until its 60s read timeout. Each request now owns a
    queue; the engine writes to whichever is current, and the swap happens on
    the engine thread AFTER the superseded terminal frame is written to the old
    one.

12. **`LIVE_TABS` must exempt `tab_id == -1`.** -1 is the documented
    "not tab-scoped" value and can never appear in a list of live tabs, so a
    naive "reap anything not in the list" reaps those sessions instantly, on
    the very first reconciliation. Not mentioned in §5f.

13. **Single-instance enforcement must check a PID before touching the
    socket.** §10 already flags that the inherited delete-stale-socket pattern
    is dangerous; the ordering is the specific part that matters. The socket is
    only unlinked AFTER the pid lock confirms no live owner, otherwise a second
    process can delete a live instance's socket and steal the path.

14. **§11's memory formula and §7's stated `n_ctx` disagree by 4.6x, and the
    formula is the wrong one.** §11 gives
    `n_ctx = MemAvailable * 0.8 / BYTES_PER_TOKEN`; §7 states `n_ctx = 16384`
    outright. On the development machine the formula yields **75,340 tokens
    (7.7 GB of KV)** against §7's 16,384 (1.7 GB). The formula overshoots for
    three reasons: `MemAvailable` counts reclaimable page cache as free, which
    is true for transient allocations and false for a permanent one-shot
    reservation (here almost all of that 9 GB is cache, and swap is already
    full); it never subtracts the model weights, which are also resident; and
    it leaves nothing for the browser — whose protection is the entire point of
    the design. Corrected to `0.5 * (MemAvailable - browser_reserve -
    model_bytes)`, capped at §7's 16384. That lands on exactly 2048
    tokens/session on reference hardware and degrades sensibly on smaller
    machines (2 GB -> 4096 floor, 4 GB -> 6880). **Recommend correcting §11.**

## First real measurements of the assembled engine

Qwen3-0.6B-Q8_0, n_ctx 4096, n_seq_max 4, n_threads 3 (quota-matched), 4
physical cores. Full calibration takes ~43s; it is cached to disk afterwards.

    Phase A  threads   1 -> 18.3, 2 -> 33.2, 4 -> 46.8 tok/s
                       fastest is 4, quota caps at 3, so 3 is chosen
    Phase B  decode    pos   32 -> 46.1 tok/s (median), 45.1 (worst)
                       pos  256 -> 37.1 / 31.5   <- 15% spread
                       pos  960 -> 29.0 / 27.0
    Phase B-prefill    depth  32 -> 279 tok/s
                       depth 512 -> 195 tok/s
    Phase C  joint     4 sessions @ depth 512, batched:
                       64.4 tok/s aggregate, 16.1 each

Three things worth recording:

- **Prefill is ~6x decode, not the order of magnitude §9b assumed.** 279 vs
  46 tok/s at depth 32. §14 recorded prefill as entirely unmeasured; this is
  the first number. Same direction as predicted, smaller factor.
- **Phase C does not contradict the document's 12.5 tok/s figure, it
  complements it.** At depth 512 batching gives 64.4 aggregate against 31.6
  solo — a 2x benefit. The document's measurement at depth 1500 gave 12.5
  aggregate against 25.1 solo, i.e. batching *hurt*. Both fit "batching stops
  paying once sessions are deep"; this is the shallow end of that curve.
- **The measured trial spread (1–15%) justifies the median/worst split.** At
  pos 256 the spread was 15%, so a round admitted on the median estimate there
  really could overrun its budget.

### Corrections found by running the §12 suite

15. **§9b's forced prefill chunk broke the very bound it sits inside.** My
    anti-starvation fix (force `PREFILL_CHUNK_MIN` through after N stalls) was
    measured to overrun the round budget by **87 ms against 50 ms, in 20% of
    rounds**. That trades "prefill never runs" for "the ~50ms preemption bound
    breaks regularly", which is the wrong way round — the bound is the
    headline claim. Fixed to admit `max(1, affordable)` instead:
    `PREFILL_CHUNK_MIN` is an EFFICIENCY floor ("below this, per-call overhead
    dominates"), not a correctness one. Result: 0 over-budget rounds, prompt
    still progresses.

16. **The formatter rollback checkpoint goes stale across compaction —
    §8's own warning, applied to a reference §8 does not know exists.** §8
    lists the live absolute positions compaction must shift. The formatter
    checkpoint added for correction 4 is another one, and missing it made a
    replace-after-compaction restore to a message count that no longer
    existed. Caught by the formatter's own prefix check (§12 test 53), not by
    review. The checkpoint is now a message COUNT, shifted in `compact()` like
    any position, rather than a `(count, rendered_length)` pair.

17. **An empty conversation must render as the empty string.** The chat
    template emits a bare generation prompt for zero messages
    (`'<|im_start|>assistant\n'` on Qwen3). Returning that after rolling a
    session's FIRST turn back claims KV content the cache does not hold, and
    breaks the prefix check on the next turn.

### §12 coverage

42 tests execute here. 21 are declared SKIP because they need a real browser
(window focus, iframes, `chrome.storage.session`, content-script injection,
`cpu.max` quota timing) — listed explicitly in `tests/test_phase1.py` rather
than silently omitted, so the gap between "Phase 1 done" and "Phase 1 tested
here" stays visible.

### Architecture audit findings (whole-system pass, after all 11 items)

18. **Cross-origin eviction was spuriously rejected at full capacity — §5g's
    mechanism broke exactly under load.** Eviction is deliberately
    ASYNCHRONOUS (a connection thread must not touch the model, §7), but
    admission ran immediately on the connection thread. Measured with every
    slot occupied: the new-origin session was refused "no free session slots"
    while the slot it needed belonged to the session just condemned, released
    one round later. Self-inflicted, and worst in the case §5g exists for.
    Fixed with `wait_for_teardown()`, bounded and blocking only a client-pool
    thread, which §7 calls a waiting room rather than compute. Covered by new
    test 64.

19. **The engine thread had no crash protection.** An unhandled exception in
    a round killed it silently. The socket stays open and the server keeps
    ACCEPTING, so it looks healthy while serving nothing, and every session
    hangs until the browser's 60s read timeout. Now logged and survived, with
    a consecutive-failure cap so a systematic fault stops rather than spins.
    Covered by new test 65.

20. **`foreground_tab_id` had no staleness timeout (§5f, test 58).** If the
    control connection died, the last value was frozen forever — and a frozen
    key naming a now-hidden tab grants that tab §9a's UNCONDITIONAL admission
    every round, permanently. The engine now degrades to "no foreground" after
    `STALE_VISIBILITY_TIMEOUT`, which is the safe direction: it costs priority,
    it cannot break the latency bound. The raw key is never mutated, so a
    reconnect restores it immediately. Test 58 moves from browser-only to
    executed.

21. **One documented exception to "no model calls off the engine thread":**
    `ChatFormatter.__init__` calls `llama_model_chat_template` from a
    connection thread. Checked rather than assumed — it takes a `const
    llama_model *` and returns `const char *`, a pure read of immutable model
    metadata with no context involved, so concurrent reads are safe. Every
    other model call is on the engine thread.

## First compile of the browser-side C++

The C++ had never been through a compiler. The first full build stopped after
194 of 22,516 steps on a single failing target; two real defects came out of
it, both fixed and each verified by recompiling the affected object.

22. **`malabr_feature` in `chrome/browser/BUILD.gn` declared only `//base`.**
    `malabr_manager.cc` includes browser-UI, extensions, content, sessions and
    net headers, and the UI headers reach Skia transitively — so the target
    failed with `'include/core/SkAlphaType.h' file not found`, which looks like
    a Skia problem and is actually a missing dependency. Added `//chrome/common`,
    `//components/sessions`, `//content/public/browser`, `//extensions/browser`,
    `//extensions/common`, `//net` and `//skia`. `gn gen` confirms no circular
    dependency, and the object now compiles. Sibling targets in the same file
    (`display_file_feature`) already follow this pattern; this one had been left
    behind.

23. **§5a's visibility snippet does not compile on this Chromium revision.**
    §5a shows
    `rfh->GetVisibilityState() == content::PageVisibilityState::kVisible`.
    There is no `content::PageVisibilityState`:
    `RenderFrameHost::GetVisibilityState()` returns
    `blink::mojom::PageVisibilityState`, and `render_frame_host.h` includes only
    its `-forward.h`, so the type is incomplete at the use site. Using it would
    need a blink mojom dependency `extensions/browser` should not take on.
    Replaced with `web_contents->GetVisibility() == content::Visibility::VISIBLE`
    — which is what §5a's own RESOLVED rule names, needs no new dependency, and
    reuses the `WebContents` already fetched and null-checked a few lines above
    for the tab id. **Recommend correcting §5a's code snippet.**

All four malabr objects (`malabr_manager`, `malabr_api`, `mserver_uds`,
`msocket_uds`) now compile. The remaining ~22,300 steps of the full build are
untouched Chromium and have not been run to completion.

24. **Cold start is ~130s, not the ~13s §10 assumes, and nothing retried the
    connect.** Measured by starting `app.py` with the SHIPPED DEFAULTS
    (n_ctx=16384, n_seq_max=8, no cached calibration) rather than the reduced
    settings every earlier test used: the socket appears after **130 seconds**.
    §10 estimates "~13s (model load + calibration)" — a 10x underestimate, with
    calibration dominating. Once its result is cached on disk later starts are
    quick, but a fresh profile pays the full cost.

    Worse, §10 also requires "a short retry-with-backoff inside `Send()`'s
    `Connect()`" and that was **never implemented**: `MServerUDS::Send` called
    `Connect()` exactly once, so every `generate()` issued during those 130
    seconds failed outright. The jittered backoff that does exist covers only
    the control connection, not request sockets. Added a 180s jittered retry
    budget sized from the measurement rather than from the estimate.
    **Recommend correcting §10's cold-start figure.**

    Worth revisiting later: §11c's own audit says isolation and
    browser-protection do NOT depend on calibration, only conversation length
    and speed do. So the server could bind immediately with the conservative
    fallback curve and calibrate in the background, swapping the measured curve
    in when ready. Not done here because calibration and the engine would then
    share the model context concurrently, which is exactly the thread-safety
    problem correction 2 exists to prevent.

25. **The aggregate CPU ceiling is never applied — the easy half of the two-layer
    cap is the missing one.** cgroups are process-granular; they bound what the
    whole inference process may take and cannot see inside it, because a
    `seq_id` is not a process. That asymmetry is the project's premise: the OS
    supplies the outer ceiling, MALABR divides what sits inside it.

    §11 requires BOTH: "a real cgroup cpu.max quota on the process, AND set
    n_threads to match... setting one without the other is the nonlinear-loss
    regime measured above." Only `n_threads` is implemented. `config.py`
    computes `quota_pct`, calibration records it, comments throughout refer to
    "the cgroup limit" — but nothing calls `systemd-run --scope -p CPUQuota=`
    or writes `cpu.max`, and `run_malabr.sh` does not either. So CPU is bounded
    today only by `n_threads=3`, a soft limit: three threads can saturate three
    of four cores and the kernel will not intervene.

    The mechanism itself is verified working on this machine, no root needed:
    an unthrottled busy loop uses 99% CPU; the same loop under
    `systemd-run --user --scope -p CPUQuota=25%` uses 25%, with wall time
    unchanged. Note `cgroup.controllers` in our own scope lists only
    `memory pids`, so writing `cpu.max` directly is not possible — the
    `cpu` controller is not delegated. systemd-run is the working path.

    NOT implemented here because the fix touches process lifecycle:
    `MalabrManager` spawns `python3 app.py` and later calls `Terminate(pid)` on
    that handle, so re-execing under a transient scope changes the PID and the
    handle may no longer reach the real process. Needs a termination test
    alongside it. **Recommend §11 note that only half the cap is built.**

26. **Memory needs no cgroup, and that is a structural result rather than an
    omission.** §11 measured RSS flat (~1139MB) from 1 to 8 resident sessions:
    `n_ctx` is a single fixed allocation at startup with no runtime path to
    grow. A `memory.max` on top would be redundant, since the allocation cannot
    exceed itself. Memory is therefore capped exactly at BOTH granularities —
    aggregate by construction, per-session by the `n_ctx / n_seq_max` budget
    plus compaction at 95%. CPU is the asymmetric case: the per-session
    division exists, the aggregate ceiling does not.

27. **Resource monopolisation belongs in §11c's calibration-INDEPENDENT column.**
    §11c lists isolation and browser-protection as not depending on
    calibration, and conversation length and speed as depending on it.
    Monopolisation resistance belongs with the former, and this was measured
    rather than argued. One session at position 4000 against five shallow ones,
    300 rounds, cost model swept across a 1000x error range:

        accurate              deep session took  9.1% of slots, nobody starved
        10x too optimistic                      16.7%,           nobody starved
        100x too pessimistic                    16.9%,           nobody starved
        no calibration at all                   18.0%,           nobody starved

    Fair share is 16.7%. What the cost model's accuracy actually buys is
    proportional charging of expensive sessions (the accurate run gives the deep
    session LESS than fair share, correctly); as the model degrades the
    scheduler stops discriminating and converges to round-robin. It never
    permits monopolisation, because the anti-monopolisation property is
    structural: the loop yields after every single token, `ABSOLUTE_CEILING`
    bounds one response, and the aging pass counts rounds rather than
    milliseconds. None of those take a calibration input.

28. **There is no user-initiated stop, and the API cannot express one.**
    `malabr.idl` exposes exactly one function, `generate()`. A turn ends by
    reaching EOS, hitting the output cap, being superseded by the next prompt,
    or the tab closing/navigating. A user who simply wants to stop a running
    response — the Stop button every comparable chat UI has — has no way to say
    so except by sending another prompt, which replaces rather than cancels and
    puts a new turn into context.

    Noticed while using the panel, not by reading the spec. The server side is
    already capable: §6b's rule that "a turn only enters permanent context if it
    reaches EOS or the output cap" describes precisely what a stop must do, and
    `_roll_back_partial()` implements it — cancellation is reachable today only
    through tab-close and supersede. What is missing is a way to ask for it.

    Not a UI patch: it needs a new IDL function (`cancel(requestId)`), a
    corresponding ExtensionFunction class, a histogram enum value, and a route
    to carry it — either a fifth control message or a per-request signal.
    Worth deciding deliberately, since a Stop that leaves a half-finished
    assistant turn in the KV cache would reintroduce exactly the desync §6b
    exists to prevent.

29. **The panel must not disable input during generation — supersede is the
    designed behaviour.** An earlier version of the test extension greyed the
    Send button out while a response streamed, but left the Enter key wired to
    the same handler, so Enter did what the disabled button said was
    impossible. The second request then succeeded, because §6b's single-flight
    replace handled it correctly. The server was right and the UI was lying.
    The button now becomes "Replace" during generation, and a superseded turn
    is reported as an ordinary outcome rather than an error.

30. **`MalabrFeature` is disabled by default and the launcher never enabled it,
    so nothing ran at all.** `kMalabrFeature` is `FEATURE_DISABLED_BY_DEFAULT`
    (chrome/common/chrome_features.cc) and is the first thing
    `MalabrManager::StartMLServerIfEnabled()` checks. `run_malabr.sh` enabled
    only `MalabrTunables`, which is a DIFFERENT feature carrying the timeout
    parameters — so the manager returned immediately on every launch: no server
    spawned, no socket created, and every `generate()` failing with nothing in
    the log, because nothing was ever attempted.

    Worth noting how it presented. Two independent faults were stacked here:
    this one, and the interpreter (correction 31 below). Both produce the same
    visible symptom — an empty chat panel — and the second was invisible until
    the first was fixed. The absence of any log line was itself the clue: a
    failed spawn would have logged, a failed connect would have logged, and
    silence meant the guard.

31. **The spawned interpreter must be configurable; `python3` is the wrong
    one.** `MalabrManager` launched `base::FilePath("python3")`, which resolves
    through PATH. In a Chromium build shell that is the build environment's
    python, which has no `llama_cpp`, so `app.py` exited immediately with
    `ModuleNotFoundError`. The socket path was already an environment variable
    for exactly this class of reason; the interpreter now is too, via
    `MALABR_PYTHON`, with `run_malabr.sh` exporting the one that carries the
    runtime. **Recommend §10 name this alongside the socket path.**

32. **Reasoning tokens are suppressed at the PROMPT, not at the stream.**
    Qwen3 emits a visible chain of thought before its answer. Filtering that in
    the UI or the streaming layer would still generate every one of those
    tokens, so they would consume the per-request output cap, the session's
    context budget and wall-clock time, and then be thrown away. The template
    itself has the switch:

        {%- if enable_thinking is defined and enable_thinking is false %}
            {{- '<think>\n\n</think>\n\n' }}

    i.e. it pre-fills an already-closed think block. `llama_chat_apply_template`
    cannot pass template VARIABLES, only messages, so `ChatFormatter` READS
    that literal out of the template source rather than hardcoding it. A model
    whose template has no such branch gets "" and is unaffected — verified:
    Qwen3 yields `'<think>\n\n</think>\n\n'`, gemma-3 yields `''`.

    The marker is recorded as part of the ASSISTANT message, not appended only
    to `_rendered`. In KV terms that is what it is — those tokens sit inside the
    assistant block ahead of the generated text — and storing it there keeps
    `_apply()`'s output matching the cache on the next turn. A first attempt
    appended it to `_rendered` alone, so the re-render no longer contained it
    and the prefix check failed on turn two, breaking 11 of the §12 tests.

33. **A test that passed by accident: §12 test 19's precondition was implicit.**
    It installed its bounded outbox after exactly ONE engine step, assuming
    prefill had completed. That held only while the prompt fitted in a single
    round. Adding six tokens of no-think marker pushed prefill into a second
    round, so the session was still PENDING and the stalled-reader path was
    never exercised at all — the test failed for the right reason. It now drives
    until the session is demonstrably GENERATING and asserts that precondition
    before proceeding.

## First live browser session

The full path works end to end: content script -> browser process -> UDS ->
Python engine -> streamed tokens back into the page. Multi-turn context is
confirmed working live, which validates §6c's incremental template application
against a real conversation rather than a harness: told "my name is chaitanya"
and asked "what is my name" two turns later, the model answered correctly, so
the session's KV cache genuinely persisted across turns.

Three faults had to be cleared to get there, and two of them produced the SAME
symptom — an empty chat panel — which is why they had to be found in order:
`MalabrFeature` disabled by default (nothing was attempted at all), the spawned
interpreter lacking llama_cpp (server died instantly), and the test extension
still calling the removed sklearn API.


## Code rationale, by module

MOVED — this section is now `mserver/NOTES.md`, and it is tracked in git.

It was here while `design_doc/` was personal and untracked, which meant every
`[notes: ...]` pointer in the source resolved only on this machine. Anyone else
reading the code found a reference to a file that did not exist in their
checkout. The rest of this file (environment, build log, measurements, session
narrative) stays untracked, because it is genuinely a personal log.
