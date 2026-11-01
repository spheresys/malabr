# MALABR overnight audit plan

Standing task for a fresh session: audit for holes, fix iteratively, commit each
fix. Suite must stay 46/46 (`python tests/test_phase1.py` from `mserver/`). No
co-author trailers in commits. Server: hand-run from
`addition_malabr/mserver` with `MALABR_SHARED_KV=1`.

## STATUS: complete (all 13 items, one commit each, f4dfc70..6e99f04)

8 real fixes (items 1, 2, 4, 6, 8, 9, 10, 12), 5 unfounded-but-confirmed
(3, 5, 7, 11, 13). Suite 46 -> 57 (11 regression tests added), green in BOTH
partitioned and `MALABR_TEST_SHARED_KV=1` modes. Per-item findings inline below.
Test venv: `/home/chaitu/Desktop/vscode/malabr/bin/python`.

## Method per item

1. Read the code against its design section (`phase1_design.md`,
   `implementation_notes.md`).
2. Write a §12-style test that would FAIL if the bug existed (against a
   deliberately broken copy first, to confirm it has detection power -- this is
   `implementation_notes` correction "concurrency tests must be run against a
   broken impl").
3. Run it. If it passes, the concern is unfounded -- record that and move on.
   If it fails, fix, re-run, commit.
4. One concern per commit.

## Suspect areas, roughly in priority order

1. **Shared-KV x §6b rollback.** `_relieve_aggregate_pressure` can `compact()` a
   PENDING/GENERATING session mid-round, including `compact(keep_recent=False)`
   which drops the most-recent COMPLETED turn. Verify `pos_before_request` /
   `pos_before_generation` / `formatter_cp_before_request` all shift correctly
   and the compaction asserts never trip. Drive: build a session to several
   turns, start a new turn (PENDING), force aggregate pressure, then supersede
   or roll back -- does it land on the right position?
   -- FIXED. `_begin_turn` left `pos_before_generation` holding the PREVIOUS
   turn's value through the PENDING window. When the aggregate guard's
   `compact(keep_recent=False)` last resort dropped that finished exchange while
   the next turn was still PENDING (exactly 2 turn_boundaries), the stale
   position sat inside the dropped range and tripped compact()'s corruption
   assert -- the RuntimeError abandoned the round and it retried forever,
   wedging the pool. Fix: pin `pos_before_generation = pos_before_request` at
   turn start; prefill completion still overwrites it with the real value.
   Regression: test 40c. `pos_before_request` and `formatter_cp_before_request`
   were already correct.

2. **Shared-KV x §8 compaction snapshot staleness.** §8 lists the absolute
   positions compaction must shift. The aggregate guard compacts a DIFFERENT
   session than the one whose turn is running. Confirm cross-session: guard
   compacts session A while session B is mid-prefill -- B's positions are
   untouched (seq_rm/seq_add are seq-scoped), but re-check.
   -- CROSS-SESSION CONCERN UNFOUNDED. compact() is fully seq/session-scoped:
   seq_rm/seq_add pass s.slot, and every position/boundary/formatter mutation
   is on the victim's own Session. Compacting A while B is mid-prefill leaves
   B's pos / prefill_offset / turn_boundaries / inbox_tokens / formatter byte
   -identical, and B's canary survives. Verified by driving two shared-KV
   sessions.
   -- BUT a DISTINCT bug surfaced: _relieve_aggregate_pressure compacts IDLE
   sessions too, and a normally-finished IDLE session has pos_before_generation
   pointing INSIDE its last exchange (set at that turn's prefill completion).
   compact(keep_recent=False) drops exactly that exchange -> corruption assert
   -> round abandoned and retried forever -> pool wedges. Same class as item 1,
   different session state. Fix: pin pos_before_generation = pos_before_request
   in _finish_turn and _roll_back_partial, so once no turn is in flight the
   marker never lingers inside a compactable exchange. Regression: test 40d.

3. **BOS x rollback-to-empty x compaction.** First turn tokenises with
   `add_special=True` (adds `<bos>` for gemma). If the first turn is rolled
   back, `_rendered` -> "" and `_roll_back_partial` seq_rm's [0, pos] so the BOS
   leaves KV too -- next turn re-adds it. Verify. Also: after `compact()` keeps
   only the anchor, is the anchor's BOS still there? (anchor = turn_boundaries[0]
   starts at pos 0, never dropped -- should be fine, confirm.)
   -- UNFOUNDED, correct by construction, confirmed empirically against the
   actual gemma-3-1b model (BOS = token id 2):
   * First-turn rollback: `_roll_back_partial` uses `target = pos_before_request`
     which is 0 for a genuine first turn, so `seq_rm(slot, 0, pos)` evicts BOS.
     Measured: KV slot goes (0, N) -> (-1, -1), `s.pos` -> 0, `_rendered` -> "".
     `first_turn = not _rendered` is then True again and `user_turn` re-emits
     BOS (`inbox_tokens[0] == 2`). Symmetric.
   * compact(): `turn_boundaries[0]` is never in `droppable` (both `[1:-1]` and
     `[1:]` start at index 1); `seq_add` shifts only positions `>= turn.end`
     (> 0). BOS at pos 0 is never removed or moved. Confirmed anchor.start stays
     0 and `verify_against_full()` holds after compact-to-anchor.
   The `<bos>` token is invisible on the Qwen test model (add_bos_token=false),
   so the suite test (53b) locks only the model-independent half: slot emptied,
   `_rendered` cleared, next turn rebuilds without TemplateError.

4. **`_finish_turn` trailing-whitespace KV trim.** New. `n_trim` from a
   re-tokenisation diff of `reply` vs `reply.rstrip()`. Edge cases: reply is
   ALL whitespace (guarded by `stripped and`), n_trim computed wrong for a
   model whose whitespace merges into adjacent tokens, n_trim >= span. Verify
   `verify_against_full()` still holds after a trim, and formatter/KV agree.
   -- FIXED. All three edge cases were real, one of them a session-killer:
   * ALL-whitespace reply: the `stripped and` guard skipped the trim entirely,
     so `assistant_generated()` recorded the raw whitespace while a trimming
     template (gemma-3 `content | trim`) renders it as empty. Confirmed against
     gemma-3-1b: the next `user_turn` fails `full.startswith(_rendered)` and the
     session is torn down with "conversation state was lost -- start a new
     chat". Fix: drop the `stripped and` guard; when `stripped == ""` set
     `n_trim = reply_span` so every generated token leaves the KV.
   * BPE boundary merge: `n_trim` from re-tokenising the concatenated bytes can
     disagree with the tokens actually sampled, cutting into real content. Fix:
     trim only when `tok(stripped)` is a clean PREFIX of `tok(reply)`; otherwise
     leave the KV alone (a stray trailing-whitespace token is harmless -- the
     text-based prefix check still passes -- whereas a bad cut is not).
   * n_trim >= span: the guard bounded `n_trim` by `s.pos - s.pos_before_request`
     (the whole turn, incl. the user message + generation prompt). Tightened to
     `s.pos - s.pos_before_generation` (the reply span only) so a pathological
     count can never `seq_rm` into the prompt.
   Regression: test 06b locks the trim math on Qwen; the gemma desync path is
   verified offline (two models in one suite process is flaky).

5. **`_apply_control` TemplateError -> teardown.** New path. Confirm the
   session's slot is released (`_teardown` -> `_alloc.release`), the client
   gets exactly one FRAME_ERROR, and a NEW session on the same key afterwards
   starts clean.
   -- UNFOUNDED, path is correct. Verified two ways (Qwen, TemplateError forced
   by monkeypatching `user_turn`):
   * IDLE session -> `_begin_turn` raises: exactly one FRAME_ERROR
     ("conversation state was lost -- start a new chat"), session removed from
     the registry, slot `_alloc.release`d (in_use 1 -> 0), state DEAD; a fresh
     `get_or_create` on the same key gets a clean slot and runs a turn to
     FRAME_COMPLETE.
   * GENERATING session superseded, then `_begin_turn` raises: the OLD outbox
     gets exactly one "superseded", the NEW outbox exactly one "...start a new
     chat" -- never two frames to either. The outbox swap in `_begin_turn`
     happens before `user_turn`, so the two terminal frames always land on
     different queues. Slot released, session removed.
   Slot re-wipe on the next acquire is guaranteed by SlotAllocator (release ->
   _free -> acquire re-arms pending_wipe -> prepare wipes+verifies), so the new
   session cannot see the dead one's KV. Regression: test 03b.

6. **Model-switch re-exec mid-generation.** `os.execv` while other sessions are
   GENERATING. Their request sockets get connection-reset; the C++ side should
   surface an error frame, not hang. The control connection reconnects. Verify
   no half-written frames, no zombie state in the new process.
   -- Mostly browser-side (frame reader must treat a short read as an error;
   control channel resync is test 57, browser-skip). Reasoned through the
   Python side:
   * Python sockets are CLOEXEC by default, so `os.execv` closes every session
     request socket and the control socket -- exactly the connection-reset the
     clients need. No frame keeps being written: `execv` replaces the whole
     image, pool-worker threads included.
   * No zombie state: KV lives in process memory and goes with the image; the
     listen socket and pidfile are both unlinked before `execv`.
   * FOUND one real fragility. `os.execv` keeps the pid (deliberately -- so
     MalabrManager's `Terminate(pid)` still lands). `_reexec_with_model`
     unlinks the pidfile first, but best-effort (`except OSError`). If that
     unlink ever fails, the re-exec'd process runs `PidLock.acquire()`, reads a
     pidfile naming its OWN pid, `_alive()` says yes (it is us), and it refuses
     to start -- the switch leaves a dead server. Fix: `acquire()` treats
     `existing == os.getpid()` as unambiguously stale (a live foreign instance
     can never hold our pid) and reclaims it. Regression: test 29b; test 29
     rewritten to use a real foreign process for the genuine-rival case.

7. **`sending` latch 10s timeout (content.js).** If `generate()`'s callback
   takes >10s to return the id (very large prompt, slow first token), the latch
   clears and a second Enter can open a parallel generation -- the exact bug
   the latch fixes. Either bump to `frame_read_timeout_seconds` or clear the
   latch only on real completion. Browser-only to observe; reason about it.
   -- Reasoned + hardened (browser-only, no suite test possible). `generate()`'s
   callback carries ONLY the request id -- tokens/completion arrive separately
   on `onToken`/`onComplete` -- so a correct C++ impl returns it in ms and the
   window is not normally reachable. But "clear the latch only on real
   completion" is not an option: the guard exists precisely because the
   callback may NEVER fire (dropped extension message). So: (a) 10000 -> a named
   `GENERATE_ACK_TIMEOUT_MS = 15000`, matching the value `meta()` already uses
   in the same file for the same round-trip, so the net cannot trip on a merely
   slow call on a CPU-saturated box; (b) on expiry the guard now also tears
   down the half-open turn (error note, drop the caret, `setBusy(false)`) so the
   next Enter starts genuinely fresh instead of racing a late callback into a
   parallel generation. Idempotent via an `if (!sending) return` head.

8. **"New chat" does not tear down the server session** (client, contradicts
   §3 "full teardown, not a display clear"). Either wire a real teardown
   (needs a route or a control message -- `stop()` ends the turn but keeps the
   session; there is no "end session" verb) or change §3. Decide deliberately.
   -- DECIDED: keep §3, wire the teardown. §3 is tied to RQ1 ("session death =
   data death") and the engine already supports it (t03). The only gap was the
   client, and the page-facing API is generate()/stop() only -- so, like the
   model switcher, "New chat" now rides the meta channel: the panel sends
   `\x00MALABR::new`, and `_handle_meta("new")` calls `engine.cancel()` +
   `engine.wait_for_teardown([key])` before replying, so the panel's next
   generate() is guaranteed a fresh session rather than the old one (and its
   KV) reused. The panel clears the display immediately and the note now
   reports whether the server confirmed the teardown. Regression: test 03c.

9. **`apply_cpu_ceiling` partial failure.** busctl returns 0 but the scope has
   no cpu.max (systemd rejected the property silently); or busctl hangs (10s
   timeout guards it); or a second server start (PidLock should block it --
   confirm the lock is checked BEFORE `apply_cpu_ceiling`, it currently runs
   first in main()). Move `apply_cpu_ceiling` AFTER the PidLock check if not.
   -- FIXED / hardened:
   * ORDERING was wrong. `main()` called `apply_cpu_ceiling(cfg.cpu_max_cores)`
     as its very first action, before the single-instance probe. A rejected
     second start (browser restart that did not confirm the old child died)
     therefore created a transient `malabr-cpu-<pid>.scope` -- and could block
     up to busctl's 10s timeout -- before exiting 3 with nothing to do. Moved
     the ceiling to AFTER the probe's early return. Regression: test 29c.
   * SILENT PROPERTY DROP: busctl returning 0 is not proof the quota exists --
     systemd creates the scope and drops the CPU property without error when the
     cpu controller is not delegated to the user session. `apply_cpu_ceiling`
     now reads back `cpu.max` on our own cgroup (from `/proc/self/cgroup`) and
     logs "scope created but no cpu.max quota is in force" instead of a false
     "applied". Best-effort, wrapped; environment-specific so no suite test.
   * busctl HANG is already covered: `timeout=10` -> `SubprocessError` -> caught
     -> logs "not applied" -> continues on the duty throttle. With the ordering
     fix a rejected second start no longer pays that 10s.

10. **Aggregate guard `guard < 3*len(live)+4` bound.** If it exhausts the
    iteration bound while still over `limit`, it silently returns and the next
    decode may fail "no memory slot". Confirm the bound is generous enough, or
    have it fall through to rejecting the pending turn.
    -- The bound is fine in STEADY STATE (the guard runs every round, and
    Sigma(pos) cannot jump far past `limit` between rounds -- prefill is chunked
    to ~50ms, decode is +1/session, `n_ctx` is capped at 16384). It is only at
    risk on a discontinuity. But the concern is legitimate as robustness: the
    old code had TWO paths that returned still-over-limit (bound exhausted, and
    `if not freed: return` when every session is at its anchor floor), and both
    let the next `llama_decode` fail "no memory slot" -> RuntimeError ->
    round-failure retry.
    -- FIXED per the audit's own suggestion: `if not freed` now `break`s instead
    of returning, and after the loop, if still `>= limit`, the guard calls
    `_reject_pending_for_capacity()` -- rolls back PENDING turns (largest first,
    they have produced nothing) with one "server is at capacity -- try again"
    FRAME_ERROR until the pool is safe to decode. GENERATING streams are left
    untouched. Only if even that is not enough does it log "unrelievable" and
    return. Regression: test 40e.

11. **`_relieve_aggregate_pressure` reads `s.pos` without `_reg_lock`.** It is
    called on the engine thread from `_run_round` with a `sessions` list
    snapshotted under the lock, but `s.pos` is mutated by the engine thread
    itself -- fine. Confirm no connection-thread writes `s.pos`.
    -- UNFOUNDED, confirmed by enumerating every `s.pos` assignment in engine.py:
    `Session.__init__` (pre-registration, not yet visible), `_run_round` x2,
    `_roll_back_partial`, `_finish_turn`, `compact` -- all engine-thread only.
    Every connection-thread entry point (`get_or_create`, `evict_other_origins`,
    `cancel`, `stop_generation`, `submit`, `wait_for_teardown`, the `set_*` and
    `keys_*` helpers) sets flags or reads state; none touches `.pos`.
    `get_or_create` constructs a `Session` (pos=0) on the connection thread but
    only publishes it into `_sessions` under `_reg_lock`, and `_run_round`'s
    snapshot is taken under the same lock, so there is no torn read. `_reg_lock`
    guards the dict, not the fields; the fields are engine-thread-exclusive by
    construction. Regression: structural test 30b (fails if any connection-
    thread method grows a `.pos` assignment).

12. **§12 tests 40b / harness `session_budget` default.** Harness non-shared
    default is a hardcoded 2048; if a test sets `n_ctx` such that
    `n_ctx//n_seq_max != 2048` the compaction-trigger math in that test is off.
    Low priority -- only test 40/54 touch it and they set `s.budget` explicitly.
    -- FIXED. For the 4096/4 fixture the hardcoded 2048 put `needs_compaction`'s
    trigger at ~1945 in a partitioned sequence that llama.cpp hard-caps at 1024
    -- so an un-overridden non-shared session would hit the KV wall before
    compaction fired. Harness default now mirrors production
    (`config.n_ctx_per_session`): `n_ctx // n_seq_max` non-shared, `n_ctx // 2`
    shared-KV. The explicit `s.budget = FIX.n_ctx // FIX.n_seq_max` lines in
    t04/t09/t53/t54 are now redundant but harmless and left in place. Suite
    unchanged (no test depended on the old 2048). Regression: test 40f pins the
    harness engine's budget to the production formula.

13. **Re-run the §12 suite under `MALABR_SHARED_KV=1`** (set it in harness or a
    second pass). The suite currently runs partitioned only; shared-KV changes
    `Session.budget`, adds the aggregate guard call in `_run_round`, and the
    input-gate branch. Nothing should differ, but prove it.
    -- DONE. `harness.new_engine(shared_kv=None)` now reads
    `MALABR_TEST_SHARED_KV=1` for a whole-suite second pass (explicit
    True/False in a test still wins). Both passes are green and identical: the
    aggregate guard is a no-op while Sigma(pos) < n_ctx-64 (every non-shared
    test stays far under), and the shared-KV `_begin_turn` input gate computes
    the same `max_input` as the partitioned path for a single session and for
    the small multi-session tests. Only t40f -- which asserts the two budget
    formulas -- had to be pinned to explicit modes so it does not read the
    flipped default. The default suite run stays partitioned, by design.

## Known-not-a-bug (do not re-flag)

- `n_threads=3` "soft" CPU limit -- by design; the hard cap is `MALABR_CPU_MAX`.
- RSS flat with session count -- measured, correct (fixed n_ctx allocation).
- Foreground priority near-absolute -- by design (§9a), not a bug.
- Qwen3-0.6B giving poor answers -- model size, not MALABR.
- Chrome Prompt API comparison -- blocked by Chrome's perf-class gate on this
  hardware, not fixable here. Benchmarking resumes tomorrow with the
  llama-cpp-python baseline (`/bench` + `scripts/bench.html` are ready).
