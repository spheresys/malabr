# MALABR — session handoff v5 (2026-09-22)

Start here in a fresh session. Supersedes v4 for anything that conflicts.

## State

Branch `chaitanya/v0`, HEAD `06f636f`, **pushed to both remotes** (spheresys and
devchaitanya both at `06f636f`; verify with `git ls-remote <url>
refs/heads/chaitanya/v0`, not the push output). Working tree clean.

On GitHub the work is on the **`chaitanya/v0`** branch, not `mtp`.

§12 suite: `60 executed, 60 passed, 20 skipped (browser)`, green in both
partitioned and `MALABR_TEST_SHARED_KV=1` modes.

## Operational facts that are easy to lose

- **Test venv**: `/home/chaitu/Desktop/vscode/malabr/bin/python` is the only
  interpreter with `llama_cpp`. The shell default (`chrome_env`) and system
  python do **not** have it.
  Run from `addition_malabr/mserver/`: `<venv> tests/test_phase1.py` (~45–90 s).
  Second pass: prefix `MALABR_TEST_SHARED_KV=1`.
- **Launch the browser** (from anywhere now — the script cd's to the repo root):
  `EXTENSION_DIR=addition_malabr/mserver/malabr_chat_extension addition_malabr/scripts/run_malabr.sh`
  A launch from the wrong cwd used to silently start no server at all
  (MalabrManager resolves `app.py` relative to chrome's cwd). Fixed in
  `run_malabr.sh`, but that is the first thing to suspect if the model picker
  is empty.
- Models: `addition_malabr/models/{Qwen3-0.6B-Q8_0,gemma-3-1b-it-Q4_K_M}.gguf`.
  Override with `MALABR_TEST_MODEL`. Gemma is the one that exercises BOS and
  `content | trim` behaviour; Qwen does not.
- `design_doc/` is **untracked on purpose** (personal notes). So is
  `mserver/calibration.json` (machine-specific, regenerated).
- Legacy backup outside the repo:
  `/media/chaitu/chaitanya/malabr/legacy_backup_2026-09-22/`.
- Old verbose commit messages preserved at branch `backup/v0-verbose-messages`
  (delete when you no longer want them).

## Code layout after this session

`engine.py` was 1893 lines; it is now five modules, with `engine.py`
re-exporting every name so `from malabr_service import engine as eng` and all
existing tests keep working unchanged:

    protocol.py    wire format, envelope validation, frame encoding
    slots.py       SlotAllocator + wipe-on-acquire (isolation)
    session.py     Session, Turn, OutputCap, SessionState
    formatter.py   ChatFormatter (template), Utf8Streamer
    scheduler.py   CostCurve, build_batch (round latency budget, aging)
    engine.py      Engine: the loop, compaction, aggregate guard
    runtime.py     UDS listener, routing, control reader, PidLock, meta commands
    config.py      startup-computed sizing
    calibration.py phases A–G
    app.py         entry point

Suggested reading order: `protocol` → `slots` → `session` → `formatter` →
`scheduler` → `engine` → `runtime` → `app`.

**Where the "why" went.** ~1500 lines of rationale comments moved verbatim into
`implementation_notes.md` under `## Code rationale, by module`, keyed
`module.Class.method`. The code keeps a one-line hint ending
`[notes: engine.Engine.compact]`, or a docstring line
`Rationale: implementation_notes.md, engine.Engine.compact`.
**Open decision:** that notes file is untracked, so those pointers resolve only
on this machine. If anyone else will read the code, track it (e.g. move to
`mserver/NOTES.md`).

## What was done this session

**Audit 1 — correctness, 13 items** (`AUDIT_PLAN.md`, all closed). Real bugs
fixed: a stale rollback snapshot could wedge the shared-KV pool permanently
(two variants); an all-whitespace reply killed the session on a trimming
template (gemma); a model-switch re-exec could leave a dead server; "New chat"
never tore down the server session (§3 contradiction — now a real teardown over
the meta channel); CPU ceiling ran before the duplicate-instance check; the
aggregate guard could give up and let the next decode fail; the send latch
could time out and allow parallel generation; the harness used the wrong
per-session budget. Four concerns verified unfounded and locked with tests.

**Audit 2 — line-by-line, 16 items** (`MINUTE_AUDIT.md`, all closed). Notably:
an unbounded read of the wire header length (same class already fixed for the
payload); the single-instance lock taken only *after* the ~2-minute model load;
a foreground session starved permanently when two extensions share a tab;
`/bench` bypassing the send latch.

**Cleanup**: module split, rationale moved, CSS below logic in `content.js`,
dead `protocol.read_frame`/`TERMINAL_FRAMES` removed, v3.0 flatbuffers server
and container files removed, commit messages rewritten to one-liners.

Reports written for the supervisor: `PROGRESS_REPORT.md` (problem→fix table)
and `OPTIMIZATION_ANALYSIS.md`.

## Open work, roughly in priority order

1. **Optimizations** — see `OPTIMIZATION_ANALYSIS.md`. Top three:
   engine idle loop wakes ~1000×/s (block on an Event instead — zero behaviour
   change, real idle-CPU saving); the panel re-renders the whole reply on every
   token (coalesce per animation frame); a lone session is still held to the
   50 ms multi-session round budget (biggest TTFT lever short of changing the
   model). Then KV-cache quantization (~2× context per byte, needs a fidelity
   check) and calibration refilling from zero per measured position.
2. **Page context** — `MAX_PAGE_CHARS = 4000` with a naive prefix `slice`, so
   most real pages are cut and usually the wrong part is kept. Fixes, in order:
   Readability-style extraction; relevance selection against the question;
   only then consider summarization. Encoding/"compression" tricks do **not**
   help — a BPE tokenizer punishes anything unlike natural text.
3. **§8 output-fidelity check** — logit divergence between full and compacted
   context has still never been run. It is RQ3's actual evidence.
4. Track `implementation_notes.md` or not (see above).
5. 33 doc corrections still not folded into `phase1_design.md`; ~20 §12 tests
   need a real browser.
6. Benchmarking: Chrome Prompt API comparison is blocked (perf-class gate);
   the llama-cpp-python baseline is the pivot. `/bench` and `scripts/bench.html`
   are ready.

## Research framing (from this session's discussion)

Asked whether per-session memory/CPU isolation for one shared local LLM is a
contribution. Honest position:

- **The mechanisms are prior art.** Iteration-level scheduling = Orca (OSDI
  '22). Token-denominated per-client fairness = VTC (OSDI '24). Paged KV +
  sequence preemption = vLLM (SOSP '23). Chunked prefill = Sarathi-Serve.
  Budgeted KV eviction / attention sink = H2O, StreamingLLM. Process CPU caps =
  cgroups. Those papers each own a *different layer*; they are not one method.
- **No library packages what MALABR does**: dynamic weighted per-session KV
  budgets with reclaim-from-the-greediest, plus per-session compute share, plus
  graceful context degradation, inside one local CPU process. llama.cpp's
  `--parallel` slots are static/equal/hard; serving engines cap concurrency and
  total tokens, not per-session quotas; LiteLLM-style quota lives in a proxy
  above the engine, not touching resident KV. So it is a legitimate engineering
  artefact even where the mechanisms are not novel research.
- **What is actually novel**, ranked: (1) the regime — shared single-model
  inference across *web origins*, in-browser, CPU-only, interactive foreground;
  (2) web-origin isolation as a correctness property plus the
  canary-against-deliberately-broken-implementation verification method; (3)
  rationing resident *context* rather than throughput, degrading gracefully
  under aggregate pressure; (4) clean rollback enforced *simultaneously* with
  compaction shifting every absolute position. Strip the browser/origin framing
  and little novelty remains — the paper is "safe interactive multi-origin local
  inference", with resource management as the means.
- **Per-decode-request CPU capping** (asked about): capping one `llama_decode`
  call as a whole is what the round-latency budget already does via admission
  control; capping one session's share *within* a batched call is not possible
  — the sequences are fused into the same kernels, and cgroups throttle threads
  over a time window, not slices of one call. A genuinely new accounting unit
  would be **per-turn compute time** (currently a turn is capped by tokens
  produced, not by engine time spent), which composes with what exists.
- **Other control signals** worth considering beyond tokens: PSI
  (`/proc/pressure/{cpu,memory}`) to make the caps reactive rather than
  fixed-threshold; per-session CPU time alongside token counts; browser-native
  user-attention signals (time since interaction, is-typing) that serving
  systems structurally cannot use.

## Conventions

- No co-author trailers or tool attribution in commits, ever.
- Commit messages: one line, imperative, `MALABR:` or `docs:` prefix. No test
  counts, no long bodies.
- Explain config/settings changes in text before applying them.
