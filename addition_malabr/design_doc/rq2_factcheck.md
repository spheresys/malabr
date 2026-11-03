# RQ2 fact-check (2026-11-03)

Each item: the claim as stated, the verdict, and the code or run behind it.
Line numbers are as of `b295e2c`; paths under `addition_malabr/mserver/`.
Full trace and tables: `mserver/RQ2_NOTES.md`.

## A. The design, against the code

| # | Claim | Verdict | Evidence |
|---|---|---|---|
| 1 | n_ctx is sized at startup as "80% of available RAM" | **Wrong.** 0.5 × (MemAvailable − 2 GiB browser reserve − model file) / 107 KiB per token, clamped 4,096–16,384, rounded to a multiple of n_seq_max; `MALABR_N_CTX` overrides. The 80 is `QUOTA_PCT`, the CPU quota. The handoff's version ("2 GB reserve, minus the model, clamped") is right. | `config.py:26-47`, `:94-104`, `:207-209`; `QUOTA_PCT` `:38` |
| 2 | Calibration measures memory | **Wrong.** It measures CPU: thread sweep, decode and prefill speed by depth, all slots at once, then an output-cap table so a reply takes ≤ 15 s. It touches memory only through the output cap (64–512 tokens per reply). | `calibration.py:18`, `:22`, `:103`, `:147`, `:169`, `:194`, `:231`, `:249`, `:285`, `:477-485` |
| 3 | Which tokens count | **Everything in the cache:** template, prompts, attached page text, replies. `s.pos` is the next KV position; nothing is counted separately. | `engine.py:632-637` |
| 4 | Per-session soft limit is n_ctx/2 in shared KV | **Right** (default): max(n_ctx/n_seq_max, n_ctx/2), soft. Partitioned: n_ctx/n_seq_max, hard (llama.cpp's own wall). | `config.py:150`, `:159-167`, `:243` |
| 5 | The aggregate guard targets the greediest tab | **Right**, with a detail: the largest session *above its fair share* (n_ctx/n_seq_max); only if none is above, the largest overall. Shared KV only. | `engine.py:322-323`, `:700-709` |
| 6 | When compaction can't free enough | Per session: it frees what it can (maybe nothing) and returns; partitioned mode does nothing more. Shared: the guard drops the victim's recent turn as a last resort, then **rejects PENDING turns** ("server is at capacity — try again"), largest first, never GENERATING ones; then logs "unrelievable". A decode with no cell fails the round; 20 *consecutive* failures stop the engine, but any non-failing round resets the count. | `engine.py:711-750`, `:262-274`, `scheduler.py:20` |
| 7 | Compaction always keeps the first and the most recent turn | **Half right.** Per-session compaction keeps both (`turn_boundaries[1:-1]`). The guard's last resort (`keep_recent=False`) may drop the recent turn. The anchor is always kept. | `engine.py:644`, `:714` |

## B. Published claims

| Claim | Where | Verdict |
|---|---|---|
| RSS flat at ~1,139 MB from 1 to 8 sessions | `NOTES.md` (config entry); research statement | **Flat: confirmed. 1,139 MB: wrong for today's server.** It was an n_seq_max sweep at n_ctx 4,096. At n_ctx 16,384: 2,490.5 MB idle → 2,511.3 MB with 8 full sessions (+21 MB), both modes. |
| Compaction KL 0.0017–0.0032 vs noise floor 0.0002, 58–64% dropped, top-1 4/4 | `PROGRESS_REPORT.md:373-390`, handoff v7, `NOTES.md`, defence, pitches | **Holds, but the setup was unstated:** Qwen, n_ctx 4,096, 4 slots, *partitioned cache only*, 4 probes, generated twins. Rerun with 16 probes and forced twins: see the fidelity table below. Gemma costs about 8× more KL than Qwen. |
| "The anchor and the most recent turn always survive" | `PROGRESS_REPORT.md:328-331` | **True for per-session compaction only** (A7). |
| "Graceful degradation rather than rejection" | handoff, pitch | **Not after compaction is spent.** Shared KV rejects PENDING turns; partitioned mode fails decodes (C). |
| The anchor is recallable after compaction | `PROGRESS_REPORT.md:324` (test 4) | **Confirmed with controls:** 20/20 fresh secrets recalled in each mode; a compaction broken to drop the anchor is caught 20/20 by both detectors (scores −10.0 to −5.2 vs −0.00). |
| Greedy tab: 3.32 s vs 4.05 s, inconclusive | `PROGRESS_REPORT.md:337-343` | **Superseded** by `greedy_tab.py` (C). |
| A greedy neighbour "does not take context away from anyone" | `PROGRESS_REPORT.md:345-349` | True of context (no small tab was ever compacted). **False of latency and availability:** see C. |

### Fidelity rerun (16 probes, forced twins, equal length: stop at 80% of n_ctx/n_seq_max)

| Model | Cache | Dropped | Top-1 compacted / floor | Mean KL compacted | Noise floor mean (max) |
|---|---|---|---|---|---|
| Qwen3-0.6B | partitioned | 64% | 16/16 / 16/16 | 0.0032 | 0.0000 (0.0000) |
| Qwen3-0.6B | shared (unified) | 64% | 16/16 / 16/16 | 0.0038 | 0.0001 (0.0006) |
| gemma-3-1b | partitioned | 64% | 15/16 / 16/16 | 0.0271 | 0.0000 (0.0000) |
| gemma-3-1b | shared (unified) | 64% | 15/16 / 16/16 | 0.0774 | 0.0059 (0.0574) |

- The old Qwen numbers (0.0017–0.0032) reproduce.
- Gemma costs more, mostly on open-ended probes. The one flipped probe is
  "Which of the topics we covered is the most important?".
- Test 48: with six probes and a per-probe noise floor, compaction flips the
  "Translate 'good morning' into French" probe. That happens in every
  partitioned run, and in one of the two shared runs.

## C. Greedy tab, in one paragraph

**Partitioned, production size: one greedy tab takes a neighbour down.**
1. The greedy tab reached 2,035 of its 2,048 hard cells, holding only its
   anchor and one turn, so there was nothing to compact.
2. Gate 2 admitted the next 900-token page anyway, because it checks only
   the prompt's size.
3. Every round then failed (`rc=1`): 156,019 failures in 40 minutes. The
   engine never stopped, because deferred rounds reset the failure counter.
4. A small tab whose tokens shared the failing batch stalled about 600 s at
   a time, four times.

**Shared, production size:**
- One greedy tab fills to 99% of n_ctx/2 with no failures; the small tabs'
  turns get about 6× slower.
- Two greedy tabs can't fill the pool in 600 s, because large prefills
  starve; the guard was never exercised at this size.

**Shared, small pool (n_ctx 4,096):**
- With the guard on: no failures, 24/24 small turns.
- The guard made 3 last-resort cuts of the greedy tabs' recent pages, and 1
  cut of a *small* tab (517 → 130). That tab was 5 tokens over its fair
  share, while both greedy tabs had nothing droppable.
- With the guard off: 43,643 failed rounds, then the engine stopped itself
  for every tab.
- Verdict:
  - **"The guard reclaims from the greediest" holds**, but it moves on to
    smaller tabs when the greedy ones are at their floor.
  - **"Never shrinks a small tab to feed a greedy one" is false** in that
    case.

## D. Things I got wrong along the way (and fixed)

- The test fixture never built a unified cache, so every "shared KV" test
  before 2026-11-02 ran on a partitioned one. Fixed in `harness.py`; the
  RQ1 shared row redone on a unified cache.
- My first shared-cache noise floor (0.48 nats) compared twins that had
  *generated* their conversations; on the unified cache greedy generation
  drifts between twins, so they weren't twins. With teacher-forced twins the
  shared floor is 0.0001–0.0025 nats; the neighbour's content moves nothing
  (`RQ1_NOTES.md` §4b).
- The first greedy runs never filled the greedy tab's context: the
  scheduler starves big prefills, so a page took minutes. Added a fill
  phase before the small tabs start.
