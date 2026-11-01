# MALABR minute-improvements audit

Separate from `AUDIT_PLAN.md` (correctness bugs, all 13 items closed). This pass
is a close line-by-line read of the whole project -- `malabr_service/*.py`,
`app.py`, `malabr_chat_extension/content.js`, `bg.js`, and a spot-check of the
design docs -- looking for narrow robustness gaps, DRY/hygiene issues, and
documentation-accuracy nits. Nothing here is suite-breaking; each item below is
independently fixable and independently skippable.

## STATUS: complete -- all 16 fixed (5412550..067defe), suite 57 -> 60

Items 1, 2, 4 have regression tests (34b, 29d, 55b), each shown to fail on
the pre-fix code. Suite green in both partitioned and MALABR_TEST_SHARED_KV=1.

## Method

Read every production file start to end (not grep-driven), cross-checked
constants and idioms across files, and verified each finding against the
actual code paths involved (not just the comments). No suite run was needed
since no code was changed while compiling this list.

## Real, if narrow, robustness gaps

1. **`header_len` is read off the wire and `recv_full`'d before it is bounds
   -checked.** `runtime.py:handle_connection`:
   ```python
   (header_len,) = struct.unpack(">I", head)
   header = recv_full(conn, header_len)      # <-- unbounded read happens HERE
   env = unpack_client_envelope(header)      # MAX_HEADER_LEN is checked in here
   ```
   `unpack_client_envelope` checks `len(header_bytes) > MAX_HEADER_LEN` -- but
   only after `recv_full` has already tried to receive `header_len` bytes.
   `header_len` is a raw 4-byte big-endian value from the wire, up to ~4.29
   billion. This is exactly the bug class `payload_size` was fixed for
   (protocol.py's own comment: "BOUND BEFORE recv_full -- the whole point ...
   a peer could claim any size and the server would try to receive and buffer
   it") -- just not applied to the header length itself. A co-resident peer
   (the documented threat actor 4) can send a 4-byte header declaring a huge
   `header_len` and tie up a pool thread (of `CLIENT_POOL_WORKERS=64`)
   indefinitely, or force a large buffer if it cooperates.
   **Fix:** check `header_len > MAX_HEADER_LEN` in `handle_connection` right
   after unpacking it, before calling `recv_full`.

2. **The single-instance check in `main()` is read-only, not a real acquire.**
   ```python
   probe = rt.PidLock(cfg.socket_path + ".pid")
   existing = probe._read()
   if existing is not None and probe._alive(existing) and existing != os.getpid():
       return 3
   apply_cpu_ceiling(cfg.cpu_max_cores)
   model, ctx, engine, output_cap = build(cfg, quick_calibration=quick)   # can take ~2 min cold
   engine.start()
   rt.run_server(...)   # <-- the REAL PidLock.acquire() happens in here, via MalabrServer.start()
   ```
   The early check only reads and inspects the pidfile; it never calls
   `acquire()`. The actual lock is claimed much later, inside
   `MalabrServer.start()` (a *second*, separate `PidLock` instance
   constructed in `MalabrServer.__init__`). Two servers launched close
   together can both pass the early check (neither has written the pidfile
   yet) and both pay the full model-load + calibration cost -- up to ~2
   minutes cold -- before the real `acquire()` finally rejects one of them.
   **Fix:** have `main()` call `probe.acquire()` for real, before
   `apply_cpu_ceiling`/`build`, catching `SingleInstanceError` there; either
   pass the acquired lock into `MalabrServer` or make `MalabrServer.start()`
   skip acquiring when the pid is already the current process's.

3. **`apply_cpu_ceiling`'s early cgroup check is outside its own try/except.**
   ```python
   if "malabr-cpu" in open("/proc/self/cgroup").read():
       return
   ```
   sits before the `try:` that wraps the `subprocess.run(...)` call. The
   function's whole contract is "any failure logs and continues unthrottled"
   -- but an unreadable `/proc/self/cgroup` (restricted/hardened environment,
   no procfs) would raise here uncaught, crashing server startup instead of
   degrading. **Fix:** wrap this line in `try/except OSError: pass`, or move
   it inside the existing try block.

4. **Second-foreground-session starvation in `build_batch`.** `fg` is defined
   by tab id, and the docstring itself notes "`>1` session only if two
   extensions share that tab." The foreground loop admits the *first* fg
   session unconditionally even if it alone blows the round budget; a second
   fg session in the same round then needs `c <= budget`, which is false once
   the first has already spent past it. Worse: the aging pass only iterates
   `bg`, and
   ```python
   for s in fg:
       s.rounds_excluded = 0
   ```
   unconditionally zeroes `rounds_excluded` for every fg session every round
   *regardless of whether it was actually picked* -- so even if aging were
   extended to look at fg sessions, this line would still defeat it. A second
   foreground session can be excluded forever with no escape hatch, the exact
   failure class already fixed for bg-vs-bg (§9a aging), just not extended to
   fg-vs-fg. Narrow (needs two extensions on one visible tab) but a provable
   permanent-starvation path.

5. **`/bench` bypasses the send latch entirely.** In `content.js`'s `ask()`,
   the `/bench` branch dispatches and `return`s *before* the `if (inFlight ||
   sending)` guard is ever reached, and `bench()`/`runMalabr()` never set
   `sending`/`inFlight`. A user hitting Enter while a benchmark is running (it
   runs two full generations back to back) can open a second, genuinely
   parallel `chrome.malabr.generate()` call -- the exact race items 7/8 of the
   correctness audit fixed for the normal path, just not extended to the
   `/bench` path. Low severity (dev/debug feature only).

## Hygiene / DRY / dead weight

6. **`FRAME_TOKEN`/`FRAME_COMPLETE`/`FRAME_ERROR` are defined twice.**
   `protocol.py` is the documented source of truth ("Must match
   mserver_uds.cc's kFrame* values") and `runtime.py` imports from it.
   `engine.py` independently hardcodes the same three integers instead of
   importing them. No circular-import risk in fixing this (`protocol.py`
   doesn't import `engine.py`). A future renumbering in one file would fail
   loudly (`encode_frame` raises `ProtocolError` on an unrecognized type), not
   silently -- so this is a duplication/DRY issue, not a live bug -- but it's
   an easy one-line consolidation: `from .protocol import FRAME_TOKEN,
   FRAME_COMPLETE, FRAME_ERROR` in `engine.py`, dropping the local copy.

7. **`calibration._safe_token()`'s module-global cache has no vocab key.**
   ```python
   _SAFE_TOKEN = None
   def _safe_token(vocab):
       global _SAFE_TOKEN
       if _SAFE_TOKEN is None:
           ... derive from `vocab` ...
       return _SAFE_TOKEN
   ```
   Safe today only because every call in one process shares one model's
   vocab (by construction -- model switch re-execs into a fresh process).
   Nothing asserts or documents that invariant. If calibration were ever
   invoked twice with two different models in one process, this would
   silently return a token id from the *wrong* vocabulary, corrupting
   calibration with no error. Cheap fix: key the cache on `id(vocab)`.

8. **`engine.py`'s module docstring is stale.** It still reads "Only step 1
   is present so far" against a 5-step list (SlotAllocator, chat template,
   engine loop, compaction, scheduler) -- the file is 1884 lines and
   implements all five. Actively misleading to a new reader of the file.

9. **Imports scattered mid-file.** `engine.py` has `import queue` / `import
   time` at line 584, well after the top-of-file imports (`sys`, `threading`,
   `llama_cpp`). `runtime.py` defines `META_PREFIX = "\x00MALABR::"` sandwiched
   between two `from .` import statements. Purely cosmetic (no functional
   effect), but inconsistent with both files' otherwise-clean import blocks
   and would trip an import-sorter/linter.

10. **`phase_a_threads` reloads the whole model per thread candidate.**
    Sweeping `{1, 2, 4, cores}` thread counts reloads and frees the entire
    GGUF from scratch for each candidate (typically 3-4 full load/free
    cycles) purely to vary `cp.n_threads`, instead of loading the model once
    and only recreating the (cheap) context per candidate. OS page-cache
    softens the wall-clock cost after the first load, but it's avoidable
    redundant work on every cold start without a cached `calibration.json`.

11. **Two idioms for stripping `.gguf`.** `config.list_models()` uses
    `f[:-5]`; `runtime._handle_meta`'s `current` computation uses
    `os.path.splitext(os.path.basename(...))[0]` for the identical operation.
    Harmless, just inconsistent style for the same string operation.

## Documentation clarity (not bugs)

12. **`MALABR_CPU_MAX=N%` and `QUOTA_PCT`/`compute_n_threads` use different
    percent conventions in the same file.** `N%` for `MALABR_CPU_MAX` means
    "N% of *one* core" (the standard `cpulimit` convention: `50%` -> `0.5`
    cores flat, regardless of machine size). `QUOTA_PCT` and
    `compute_n_threads` mean "% of *all* logical/physical cores" elsewhere in
    `config.py`. Both conventions are individually normal, but having both
    unlabeled in one module invites a future misreading of `MALABR_CPU_MAX`.

13. **`MALABR_N_CTX` bypasses `MIN_N_CTX` with no floor guard.** The ceiling
    bypass (`MAX_N_CTX`) is documented and intentional ("raise it deliberately
    via MALABR_N_CTX on a machine with real headroom"); the floor bypass
    isn't called out, and the value is still rounded down to a multiple of
    `n_seq_max`. A typo'd `MALABR_N_CTX` smaller than `n_seq_max` rounds to
    `n_ctx=0`, which would fail context creation with an error far removed
    from the actual mistake. A one-line `max(n_seq_max, ...)` sanity floor
    would turn a cryptic llama.cpp failure into an actionable config error.

14. **`app.py`'s "Startup order" docstring has redundant numbering.** Step
    "0." ("config, then the single-instance check, THEN the CPU ceiling") and
    step "1. config" both mention config; a straight 1-6 renumbering would
    read more cleanly. (Introduced by this session's item-9 fix.)

15. **`content.js`'s transcript truncation has an unexplained magic number.**
    `save()` does `transcript.slice(-40)` with no comment -- presumably a
    `chrome.storage.session` quota safeguard, but nothing says so, and nothing
    tells the user their history beyond 40 turns silently stops persisting
    across reloads (the server's KV still has it; only the display doesn't).

16. **`refreshModels()` never retries.** It runs once, at panel load. If the
    server isn't up yet (cold start, or a launch misconfiguration like the
    cwd issue diagnosed earlier this session), the model dropdown stays empty
    for the rest of the tab's life with no retry and no user-facing way to
    recover short of reloading the page.

## Suggested handling

None of these are correctness bugs the way `AUDIT_PLAN.md`'s items were --
they're all independently optional. If asked to fix, group them the same way
the correctness audit did (one commit per item, suite re-run after each),
roughly in this priority order: 1, 2, 4 (real robustness), then 3, 5, 6, 7
(cheap, safe hardening/DRY), then 8-16 (documentation/consistency, batchable
into one or two commits).
