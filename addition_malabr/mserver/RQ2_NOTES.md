# RQ2 notes: memory, end to end

RQ2: can many tabs share one local model's memory, the KV cache, without one
tab's appetite hurting the others, and what does it cost the conversations
whose context is cut? This file traces how the code answers it, and how far
the tests back each part. Every claim cites the code that enforces it. Line
numbers are as of commit `b295e2c` (2026-11-02); paths are relative to
`addition_malabr/mserver/`.

Scope: KV memory only, meaning how much each conversation may hold, who
loses context when the pool is full, and what that loss costs the answers.
CPU scheduling is RQ3.

---

## 1. Who decides what

| Decision | Made by | Where |
|---|---|---|
| How big the pool is (n_ctx) | server, once at startup, from free RAM | `config.py:94-104` |
| How many conversations fit (n_seq_max) | config, default 8 | `config.py:42` |
| How much one conversation may hold | config, by mode | `config.py:159-167` |
| Whether a prompt is admitted | server, gate 2 | `engine.py:459-477` |
| When a conversation is cut | server, before each round | `engine.py:316-319`, `:632-637` |
| What is cut | server, oldest middle turns first | `engine.py:639-690` |
| Who is cut when the shared pool is full | server, aggregate guard | `engine.py:692-732` |
| Who is turned away when cutting isn't enough | server | `engine.py:734-750` |
| Which cells a token occupies | llama.cpp | not in this repo |

---

## 2. The budget, end to end

### 2a. How big the pool is (startup)

`compute_n_ctx` (`config.py:94-104`):

    n_ctx = 0.5 × (MemAvailable − 2 GiB browser reserve − model file size) / bytes per token

- MemAvailable, not MemTotal (`config.py:82-92`).
- `RAM_FRACTION_FOR_KV = 0.5` (`:30`), `BROWSER_RESERVE_BYTES = 2 GiB` (`:34`),
  `BYTES_PER_TOKEN` is the f16 figure, 107 KiB per token for Qwen3-0.6B
  (`:26`).
- Clamped to 4,096–16,384 (`:45-47`, `:101`) and rounded down to a multiple
  of n_seq_max (`:104`).
- `MALABR_N_CTX` overrides it and may exceed the ceiling (`:207-209`).

**Not "80% of available RAM".** The 80 in the code is `QUOTA_PCT = 80`
(`config.py:38`), the CPU quota. Section 11 of the design doc says "0.8 of
MemAvailable"; `NOTES.md` (config entry) records why that is wrong here: it
would reserve 7.7 GB of KV on this machine, it counts page cache as free, and
it leaves nothing for the model or the browser.

On this machine the formula hits the ceiling: n_ctx = 16,384, n_seq_max = 8.

The pool is allocated once, at context creation, and never grows (§4f).

### 2b. What calibration measures

Calibration (`calibration.py`) measures **CPU speed, not memory**. It sets
nothing about the KV budget.

| Phase | Measures | Where |
|---|---|---|
| A | decode speed against thread count | `:103` |
| B | decode tok/s by context depth; prefill speed by size and depth | `:147`, `:169` |
| C | all slots full at depth 1024, decoding together | `:194` |
| D | a table of output caps by position, so one reply takes at most 15 s (`TARGET_MAX_RESPONSE_SECONDS`, `:18`); refuses below 4 tok/s aggregate (`:22`) | `:231`, `:249` |
| F | clamps the caps to 64–512 | `:285` |

`apply_to_engine` (`:477-485`) hands the results to the scheduler's cost
curve and to the per-session output cap. The memory link is indirect: the
output cap (64–512 tokens, `session.py:12-13`, `:30-41`) bounds how much one
reply can add.

### 2c. What counts against a conversation's budget

`s.pos` is the next KV position of the session, so it counts **everything in
the cache**: the chat template, the system prompt, every user prompt
including attached page text, and every reply. Nothing is counted
separately.

### 2d. How much one conversation may hold

`n_ctx_per_session` (`config.py:159-167`):

| Mode | Budget per session | Kind of limit | At n_ctx 16,384, 8 slots |
|---|---|---|---|
| partitioned | n_ctx / n_seq_max | **hard**: llama.cpp gives each sequence only its own cells | 2,048 |
| shared KV | max(n_ctx / n_seq_max, n_ctx / soft_div), soft_div = 2 (`:150`, `MALABR_SESSION_SOFT_DIV`, `:243`) | **soft**: cells come from one pool; the server's own trigger enforces it | 8,192 |

So yes, n_ctx/2 in shared KV, by default.

### 2e. Admission: gate 2

`_begin_turn` (`engine.py:459-477`) refuses a prompt longer than:

- partitioned: `budget − 256` (`RESERVED_FOR_RESPONSE`, `session.py:62`);
- shared KV: the smaller of that and `n_ctx − Σ others' min(pos, fair share)
  − 64 − 256`, where fair share = n_ctx / n_seq_max (`engine.py:461-468`,
  `:53-54`).

**Gate 2 never looks at the session's own `pos`.** In partitioned mode that
is the gap behind the failure in §4d: a session already near its hard limit
is admitted a prompt that only fits an empty one.

### 2f. Compaction (per session)

- **When:** before every round, for every PENDING or GENERATING session at
  `pos ≥ 0.95 × budget` (`engine.py:316-319`, `:629`, `:632-637`). Never
  after a batch is composed.
- **What:** whole turns, oldest first, from `turn_boundaries[1:-1]`
  (`engine.py:644`), until at least `TARGET_FREED_TOKENS = 512` are freed
  (`:630`, `:648`). Every later position is shifted down (K-shift).
- **What survives:** turn 0 (the anchor) always. The most recent turn always
  **in per-session compaction**; the aggregate guard's last resort,
  `keep_recent=False` (`engine.py:714`), may drop it (§2g). "The anchor and
  the most recent turn always survive" is therefore true of per-session
  compaction, not of the guard.
- **When it can't free enough:** it frees what it can, possibly nothing, and
  returns. Nothing else happens in partitioned mode. A session holding only
  its anchor and one recent turn has nothing droppable.

### 2g. Aggregate guard (shared KV only)

`_relieve_aggregate_pressure` (`engine.py:692-732`), called before every
round when `shared_kv` (`:322-323`):

1. Fires when Σ pos over live sessions ≥ n_ctx − 64 (`:700-702`).
2. **Victim:** the largest session above its fair share (n_ctx / n_seq_max);
   if none is above, the largest overall (`:707-709`). So yes, the greediest
   tab, and a session at or below its fair share is cut only when nobody is
   above it.
3. It compacts the victim keeping the recent turn; if that frees nothing, it
   compacts again **without** keeping the recent turn (`:711-714`). Then the
   next victim, until the pool is below the limit or nothing can be freed;
   the loop is bounded at 3n + 4 iterations (`:705`).
4. **If compaction can't bring the pool under the limit**, it rolls back
   PENDING turns, largest session first, each with the error "server is at
   capacity — try again" (`:734-750`). It never touches a GENERATING session.
5. If even that isn't enough, it logs "aggregate KV pressure unrelievable"
   and lets the round run (`:725-730`).

So the shared design does reject under pressure. "Graceful degradation
rather than rejection" is true until compaction is spent, not after.

### 2h. When llama.cpp has no room anyway

A decode with no free cell fails (`llama_decode rc=1`); the round raises. The
engine counts consecutive failed rounds and stops after 20
(`scheduler.py:20`, `engine.py:262-271`). **But the counter resets after any
round that doesn't raise** (`engine.py:274`), including a round where the
scheduler deferred all work. §4d shows the consequence: 156,019 failed rounds
in 40 minutes without a stop.

---

## 3. Budget events

| Event | Partitioned | Shared KV | Code | Evidence |
|---|---|---|---|---|
| A session reaches 95% of its budget | oldest middle turns dropped, ≥ 512 tokens | same, against the soft budget | `engine.py:316-319`, `:639-690` | tests 04, 09, 47, 53; §4b |
| A session at 95% with only anchor + one turn | nothing can be dropped; the next prompt is still admitted | same, but the guard and the pool absorb it | `engine.py:644`, `:459-477` | §4d (partitioned failure) |
| A prompt longer than budget − 256 | refused (gate 2) | refused; also refused if it won't fit the room others leave | `engine.py:459-477` | test 38 |
| Σ pos reaches n_ctx − 64 | cannot happen (each sequence is walled) | guard compacts the greediest, then rejects PENDING turns | `engine.py:692-750` | §4d (small pool) |
| A decode finds no cell | round fails; retried every round | same | `engine.py:255-274` | §4d |
| 20 failed rounds in a row | engine stops (but deferred rounds reset the count: never reached in §4d) | engine stops, for every tab | `engine.py:265-271`, `:274` | §4d: partitioned 156,019 failures, no stop; shared small pool, guard off, stopped |

---

## 4. Tests

### 4a. Method

- **Recall is checked the RQ1 way** (`RQ1_NOTES.md` §4a): the direct question
  ("Repeat back the vault passphrase I gave you, exactly."), a fresh secret
  per run (a word and six random digits, `harness.py`, `fresh_secret`,
  seeded by `SUITE_SEED`, replayable with `MALABR_TEST_SEED`), and two
  detectors: the digits in the reply, and the teacher-forced mean
  log-probability per digit (threshold log ½).
- **Both modes, both caches.** `MALABR_TEST_SHARED_KV=1` now also builds a
  unified llama.cpp cache in the test fixture (`harness.py`, `Fixture`).
  Before 2026-11-02 every "shared" test ran on a partitioned cache.
- **Identical twins by teacher forcing.** On the unified cache, two sessions
  that *generate* the same conversation drift apart (§4c), so fidelity twins
  are now built by one reference session generating the conversation and the
  twins being force-fed it token for token (`harness.py`, `force_turn`).
  The run aborts if the twins' messages, tokens or positions differ.

### 4b. Does compaction keep the anchor? (`tests/compaction_control.py`, test 78)

20 fresh secrets per variant, planted in the anchor turn, then six filler
turns, then compaction (target lowered to 1 token so the whole middle goes).
The **broken** variant inserts a zero-length placeholder before the anchor,
so the real anchor becomes droppable: the negative control. Asked with the
direct question afterwards.

| Mode | Seed | Correct: recalled (reply / score) | Correct score | Broken: loss detected (reply / score) | Broken score |
|---|---|---|---|---|---|
| partitioned, f16 | 860084967 | 20/20 / 20/20 | −0.00 | 20/20 / 20/20 | −10.04 to −5.50 |
| shared KV, unified cache, f16 | 63043163 | 20/20 / 20/20 | −0.00 | 20/20 / 20/20 | −10.01 to −5.22 |

The method can tell a compaction that keeps the anchor from one that drops
it, every time, in both modes. Test 78 runs one trial of each in the suite.

### 4c. What does compaction cost? (`tests/fidelity_check.py`, test 48)

Three sessions hold the same conversation (§4a): A untouched, B compacted
through the shipping path, C untouched. At each probe the script reads the
next-token distribution of all three. **A vs C is the noise floor of that
mode**; A vs B is the cost of compaction, read against it. The conversation
is grown to 80% of n_ctx/n_seq_max in both modes (equal length, so the two
modes compare), with n_ctx 4,096 and 4 slots. 16 probes in six groups: what
the kept turns hold (anchor, recent), what the dropped middle held, a count,
questions that need no history (fresh), and open-ended ones.

| Model | Cache | Context dropped | Top-1 agreement, compacted / floor | Mean KL compacted | Mean KL noise floor | Max KL noise floor |
|---|---|---|---|---|---|---|
| Qwen3-0.6B Q8_0 | partitioned | 64% (868 → 313) | 16/16 / 16/16 | 0.0032 | 0.0000 | 0.0000 |
| Qwen3-0.6B Q8_0 | shared (unified) | 64% (868 → 313) | 16/16 / 16/16 | 0.0038 | 0.0001 | 0.0006 |
| gemma-3-1b Q4_K_M | partitioned | 64% (865 → 314) | 15/16 / 16/16 | 0.0271 | 0.0000 | 0.0000 |
| gemma-3-1b Q4_K_M | shared (unified) | 64% (865 → 314) | 15/16 / 16/16 | 0.0774 | 0.0059 | 0.0574 |

KV f16 throughout. Raw output (local, not tracked): `design_doc/rq2_runs_2026-11-02/fid_*`.

- **Qwen:** dropping two thirds of the conversation costs about 0.003–0.004
  nats and changes no probe's first token, in either mode.
- **Gemma costs about 8–20× more**, almost all of it in the open-ended
  group: "Which of the topics we covered is the most important?" changes its
  first token in both modes (the probability the compacted session gives A's top token is 0.43 and 0.41, against 0.78 and 0.82 for the uncompacted twin), and
  on the shared cache "What should I ask you next?" alone has KL 0.87,
  which is most of that row's mean. Without it the row's mean is 0.024.
- **Noise floor per mode.** Partitioned: 0.0000 on both models (twins in
  separate, identically filled regions). Shared: 0.0001 (Qwen) and 0.0059
  (gemma), from cell placement: identical tokens whose cells sit in a
  different order give slightly different sums (`RQ1_NOTES.md` §4b). Before
  2026-11-02 the only floor ever published, 0.0002, came from a partitioned
  cache (§5).
- **What this does not show.**
  - It reads the first token of the answer only.
  - The probes barely test lost content. The conversation cycles through 8
    topics, and compaction keeps the anchor plus the last four turns (Qwen:
    turns 0 and 10–13 of 14). The TCP-handshake question was asked twice,
    and its second copy survives, so "Did I ask you about the TCP
    handshake?" asks about kept content. Only "page fault" and "CAP theorem"
    exist solely in the dropped middle, and only the "list every topic"
    probe touches them.
  - Whether the kept content is still *there* is §4b's job: anchor recall
    20/20 in each mode, with a negative control.

**Test 48** is the same comparison in the suite, with six probes and a pass
rule that respects the noise floor: a probe fails only when B's top token
differs from A's **and** A's top-1 margin is larger than that probe's own
noise (max |p_A − p_C|).
- **Partitioned: it has failed in both runs since the rewrite**, on one probe, "Translate
  'good morning' into French". A's margin is 0.093, the noise 0.000, and
  compaction flips it. Mean KL compacted is 0.0699 against a floor of 0.0000.
- **Shared: it failed once and passed once.**
  - Failing run: the same probe, margin 0.090 against noise 0.010.
  - Passing run (final, seed 2894371582): 0 flips, mean KL 0.0264 against a
    floor of 0.0007.
  - The reference conversation is generated afresh each run, and on the
    unified cache generation depends on placement. So the shared test is
    not deterministic.

This is a real cost of compaction on Qwen, in a fresh question that needs no
history. The test is left failing on purpose rather than loosened.

**Generated twins don't work on the unified cache.** Two sessions that
generate the same conversation drift apart (placement noise changes a greedy
choice somewhere), so the "twins" hold different text. My first shared
floor, 0.48 nats, measured exactly that. The fidelity check and test 48 now
force-feed one reference conversation.

### 4d. A greedy tab (`tests/greedy_tab.py`)

Three small tabs ask 8 short questions each (output cap 64), while 0, 1 or 2
greedy tabs keep sending large pages. The script runs the real engine thread,
calibrated, with one client thread per tab. It records:
- every tab's `pos` every 0.25 s;
- every compaction (who, how much, per-session or guard);
- error frames and failed rounds;
- the small tabs' time to first token (TTFT) and turn time.

A fill phase runs the greedy tabs alone until each holds 90% of its budget
(600 s limit); then the small tabs start. `--guard off` replaces the aggregate
guard with a no-op, as the negative control. Raw output (local, not tracked):
`design_doc/rq2_runs_2026-11-02/greedy_*`.

**Production size** (n_ctx 16,384, 8 slots; pages of 900 tokens partitioned,
3,000 shared):

| Mode | Greedy tabs | Guard | Greedy peak (budget) | Compactions | Failed rounds / error frames | Small TTFT median / p95 / max | Small turn time median |
|---|---|---|---|---|---|---|---|
| partitioned | 0 | n/a | — | 0 | 0 / 0 | 1.23 / 2.59 / 2.87 s | 3.83 s |
| partitioned | 1 | n/a | 2,048 (2,048) | 0 | **156,019 / 6** | 2.07 / **597.97** / 598.14 s | 9.18 s |
| shared | 0 | on | — | 0 | 0 / 0 | 0.80 / 1.36 / 1.41 s | 2.42 s |
| shared | 1 | on | 8,098 (8,192) | 0 | 0 / 0 | 2.34 / 4.21 / 4.66 s | 15.40 s |
| shared | 1 | off | 8,083 (8,192) | 0 | 0 / 0 | 2.16 / 4.08 / 4.94 s | 14.39 s |
| shared | 2 | on | 6,311 + 3,863, fill timed out | 0 | 0 / 0 | 2.49 / 4.01 / 4.60 s | 16.43 s |
| shared | 2 | off | 6,469 + 4,317, fill timed out | 0 | 0 / 0 | 3.75 / 5.86 / 7.20 s | 19.01 s |

- **Partitioned: one greedy tab takes a neighbour down.**
  1. The greedy tab ended a turn at `pos` 2,035 of its 2,048-cell partition.
     It held only its anchor (0–1,017) and one recent turn (1,017–2,035), so
     compaction had nothing it may drop.
  2. Gate 2 checks only the prompt's own size (§2e), so it admitted the next
     900-token page.
  3. From t = 51 s every round failed (`llama_decode rc=1`, "failed to find
     a memory slot"), about 65 a second for 40 minutes: 156,019 failures.
  4. The engine never stopped: each round the scheduler deferred reset the
     failure counter (§2h).
  5. The failing batch also carried small tab 3's tokens. Tab 3 stalled
     four times, each until the greedy turn timed out (600 s) and rolled
     back: two turns timed out, two answered after 595 s and 598 s. Each
     new page did the same again.
- **Shared, one greedy tab:** it fills to 99% of its soft budget, and nothing
  fails. The small tabs' turns get about 6× slower (2.42 s → 15.40 s
  median), because they share the CPU with a 3,000-token prefill. The guard
  makes no difference here: one tab can't pass n_ctx/2, so the pool never
  fills.
- **Shared, two greedy tabs, production size:** the guard can't be
  exercised. The scheduler gives big prefills very little time, so two
  greedy tabs reached only 10–12k of 16,384 tokens in 600 s (about 18 tok/s
  combined, against 147–208 tok/s calibrated). That is a finding in its own
  right: prefill starvation keeps a greedy tab from filling the pool quickly.

**Small pool** (n_ctx 4,096, 8 slots, so a fair share of 512 and a soft
budget of 2,048; 900-token pages; shared KV; 2 greedy tabs):

| Guard | Guard compactions | Failed rounds | Small turns completed | Small TTFT median / p95 / max | Small turn time median |
|---|---|---|---|---|---|
| on | 4 (3 on greedy tabs, 1,018 each; 1 on small tab 2, 387) | 0 | 24/24 | 1.84 / 2.79 / 3.53 s | 6.10 s |
| off (negative control) | — | **43,643, then the engine stopped itself** | 3 before the first failure | — | — |

- **The guard keeps the shared pool alive.** Without it, the pool filled,
  every round failed, and about 11 minutes into the run 20 consecutive
  failures stopped the engine for every tab.
- **But it cut a small tab to feed the greedy ones.** At t = 188.9 s both
  greedy tabs were above fair share (1,195 and 1,330). Each held only its
  anchor and a page still being prefilled, so neither had anything
  droppable. The next session above fair share was small tab 2, at 517
  tokens, 5 over a fair share of 512. The guard cut it to 130, losing 75%
  of its conversation. This contradicts `NOTES.md`
  (`_relieve_aggregate_pressure`): "a small or foreground session is never
  shrunk to feed a greedy one".
- All three greedy compactions used the last resort (`keep_recent=False`),
  dropping the greedy tab's most recent page (§2f).
- No PENDING turn was rejected in either run.

### 4e. Suite (`tests/test_phase1.py`)

Final run, 2026-11-03, after the control-header fix (§12 test 79 added; test
80 is a browser-only skip):

| Mode | Seed | Executed | Passed | Failed | Browser-only skips |
|---|---|---|---|---|---|
| partitioned | 455115143 | 77 | 76 | 48 | 19 |
| shared KV (unified cache) | 2894371582 | 77 | 77 | — | 19 |

The RQ2 tests, as changed for this check:

| # | What it checks | Change | Last detail (partitioned / shared) |
|---|---|---|---|
| 04 | a fresh secret in the anchor survives compaction | direct question, fresh secret, logprob score | recalled, score −0.00 / recalled, score −0.00 |
| 09 | user-triggered compaction keeps the anchor | direct question, fresh secret | recalled / recalled |
| 47 | batch vs incremental eviction | compares tokens freed, not end positions; fresh secrets; digit detection | 393 freed both ways, 1 call vs 5 / 379 both ways |
| 48 | compaction changes no first token beyond the mode's noise floor | forced twins, 6 probes, per-probe noise | **fails** (translate) / passed this run, failed the run before |
| 53 | a rollback snapshot survives an interleaved compaction | fresh secret, direct question | recalled / recalled |
| 78 | negative control: a compaction that drops the anchor is detected | new | broken scores −7.84 / −6.51 |
| 79 | the browser's control header parses; near misses don't | new (control channel, not RQ2) | pass / pass |

Earlier runs on 2026-11-02, before the fixes, gave partitioned 76/76 and
shared 75/76 (only test 48 failing), seeds 2281719094 and 2315250287.

### 4f. Resident memory (`tests/rss_check.py`)

The real server (`app.py`), Qwen3-0.6B, n_ctx 16,384, 8 slots, VmRSS:

| Mode | Idle | 8 sessions, one short chat each | 8 sessions, each also holding a ~1,500-token page |
|---|---|---|---|
| shared KV (unified) | 2,490.5 MB | 2,507.9 MB | 2,511.3 MB |
| partitioned | 2,490.6 MB | 2,508.1 MB | 2,511.2 MB |

Flat: +21 MB (under 1%) from 0 to 8 full sessions, the same in both modes,
because the whole pool is allocated at startup. The level is about 2.5 GB,
**not ~1,139 MB**: that figure came from an n_seq_max sweep (1, 2, 4, 8
slots) at a fixed n_ctx of 4,096 (`design_doc/research_problem_statement_v2.md:208-216`,
`design_doc/open_design_questions.md:355-362`), not from today's server at
n_ctx 16,384. The level scales with n_ctx; the flatness doesn't depend on it.

---

### 4g. After admission control (2026-11-04)

Same scenarios as §4d, on the new code. The before columns come from §4d.
Each cell gives the small tabs' TTFT (median / p95) and their median turn time.

| Scenario | Before | After |
|---|---|---|
| Partitioned, no greedy tab | 1.23 / 2.59 s, 3.83 s | 0.55 / 1.16 s, 3.63 s |
| Partitioned, 1 greedy tab | 2.07 / 597.97 s, 9.18 s; 156,019 failed rounds | 0.52 / 1.12 s, 4.27 s; 0 failed rounds |
| Shared, no greedy tab | 0.80 / 1.36 s, 2.42 s | 0.32 / 0.49 s, 2.75 s |
| Shared, 1 greedy, guard on | 2.34 / 4.21 s, 15.40 s | 0.48 / 1.11 s, 5.91 s |
| Shared, 1 greedy, guard off | 2.16 / 4.08 s, 14.39 s | 0.47 / 1.05 s, 5.72 s |
| Shared, 2 greedy, guard on | 2.49 / 4.01 s, 16.43 s | 0.59 / 1.26 s, 6.52 s |
| Shared, 2 greedy, guard off | 3.75 / 5.86 s, 19.01 s | 0.69 / 1.32 s, 7.67 s |
| Small pool 4,096, guard on | 1.84 / 2.79 s, 6.10 s; small tab cut 517 → 130 | 0.48 / 0.89 s, 4.91 s; nothing compacted |
| Small pool 4,096, guard off | 43,643 failed rounds, engine stopped | 0.50 / 0.94 s, 5.30 s; 0 failed rounds |

- Every after run had 0 engine round errors.
- **The comparison flatters the change.** The greedy tab is now refused
  instead of growing, so it stops prefilling pages:
  - partitioned mode and the small pool: after one page (1,017 tokens);
  - shared mode: after two pages (6,095 tokens). A third page plus the
    largest reply cap doesn't fit the 8,192 soft cap, and own-turn
    compaction keeps the latest exchange.
- The huge error-frame counts in these runs (46k–329k) were the script
  resending a refused page at once. It now pauses after a refusal
  (`--refusal-pause`) and reports each greedy tab's admitted and refused pages.
- These runs loaded `calibration.json` measured partitioned at depths up to
  1,984 (`DESIGN_DECISIONS.md` D20). Later runs use a keyed calibration.

## 5. Where the published numbers came from

| Number | Where published | Setup behind it |
|---|---|---|
| KL 0.0017–0.0032 compacted, noise floor 0.0000–0.0002, 58–64% dropped, top-1 4/4 | `PROGRESS_REPORT.md:373-390`; handoff v7; `NOTES.md` (KV_TYPE); defence and pitch | `fidelity_check.py` at `5799f57` (2026-09-22): Qwen3-0.6B, fixture n_ctx 4,096, 4 slots, **partitioned cache** (the fixture never set `kv_unified`), 4 probes, *generated* twins; ranges across an unrecorded number of runs |
| ~1,139 MB flat from 1 to 8 sessions | `NOTES.md` (config entry) | n_seq_max sweep at n_ctx 4,096 (§4f) |
| "anchor and most recent turn always survive" | `PROGRESS_REPORT.md:328-331` | true of per-session compaction; the guard's last resort drops the recent turn (§2f) |
| "graceful degradation rather than rejection" | handoff, pitch | true until compaction is spent; then PENDING turns are rejected (§2g) |
| greedy tab 3.32 s vs 4.05 s, inconclusive | `PROGRESS_REPORT.md:337-343` | Chrome-side harness, greedy arm n = 1; superseded by §4d |

**The 0.0002 noise floor** is the partitioned cache's: two uncompacted
twins whose cells sit in separate, identically filled regions. On a
partitioned cache it is 0.0000 with forced twins (§4c). It was never a
shared-cache number.

---

## 6. Open limitations

These are findings, not fixes. Each fix would change the memory design, so
none is made without a decision.

**Status 2026-11-04:** M1–M5 are closed by admission control
(`DESIGN_DECISIONS.md` D29, `engine.Engine._admit`; tests 81–88). The
reruns are in §4g. M6 and M7 still stand.

- **M1. Gate 2 ignores the session's own position** (`engine.py:459-468`).
  In partitioned mode a nearly full session is admitted a prompt that only
  fits an empty one. With nothing droppable, every round then fails (§4d).
  Candidate fix: check `pos + prompt` against the hard cap, and reject or
  compact first.
- **M2. A session at its compaction floor can't be relieved.** Per-session
  compaction never drops the recent turn. A session holding only an anchor
  and one large turn is therefore stuck in partitioned mode, where no guard
  exists.
- **M3. The round-failure stop is defeated by deferred rounds**
  (`engine.py:274`). Any round that doesn't raise resets the counter,
  including one where the scheduler ran nothing. 156,019 failures went
  unstopped in partitioned mode. In shared mode, with the guard off, the
  stop did fire, and it took down every tab.
- **M4. A failing session takes its batch-mates with it.** One decode with no
  room fails the whole round, so other tabs in that batch make no progress
  (small tab 3, §4d).
- **M5. The guard can shrink a small tab to feed a greedy one.** "Above fair
  share" includes a short chat at 517 of 512. When the greedy tabs have
  nothing droppable, the guard moves on to it (§4d). The `NOTES.md` claim
  "never shrunk to feed a greedy one" is wrong in that case.
- **M6. The guard's last resort drops the most recent turn** (`engine.py:714`).
  "The anchor and the most recent turn always survive" holds only for
  per-session compaction.
- **M7. Shared mode rejects under pressure.** When compaction is spent,
  PENDING turns get "server is at capacity — try again" (`engine.py:734-750`).
  It is not "graceful degradation without rejection".
- **M8. Prefill starvation.** Large pages get very little prefill time. Two
  greedy tabs reached only 10–12k tokens in 600 s at production size, so
  the guard was never tested at that size.
- **M9. Fidelity reads the first token only.** Content survival is shown by
  §4b (anchor recall with a negative control), not by §4c.
- **M10. One model per measurement type.** RSS, the greedy-tab runs and the
  compaction controls use Qwen3-0.6B only; fidelity uses both models.
- **M11. Test 48 fails in both modes**, on one fresh question (§4c). Left
  failing.

---

## 7. Future work (hypothesis only)

On the unified cache, a session's output depends slightly on **where** its
cells sit (order, reused holes), and placement depends on other tabs'
activity. A tab's output may therefore depend very slightly on its
neighbours' activity, though not on their content (RQ1_NOTES §4b: a
neighbour's content moved the readout by 0.0000). Untested beyond the
noise-floor runs.
