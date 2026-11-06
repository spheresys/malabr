# W3 shared-mode regression: diagnosis

**These are diagnostics, not results.** Each configuration ran n = 2, no warm-up, with swap at about 70% and the page at 3,000 tokens. Don't quote them as benchmark numbers. The frozen code wasn't changed: every hypothesis was applied at runtime by `diag.py`, and the benchmark numbers remain those in `BENCHMARKS_2026-11.md`.

## Finding

**NEW's prefill guarantee goes to whichever prompt arrived first.** In W3 that's the greedy page, so a deep 1-token decode of about 110 ms runs in nearly every round. A short prompt from another tab then gets one token per 5 rounds. A 26-token prompt therefore takes about 128 rounds × 105 ms ≈ 13.4 s.

OLD ran the same deep token only about once per 5-round cycle, so its short prompts took about 3.3 s.

## 1. Per round: NEW shared vs OLD shared, 1 greedy tab

Small-tab phase, medians over the first run; the second run matched.

| Measure | NEW shared | OLD shared |
|---|---|---|
| Rounds in the small-tab phase | 1,305 in 129 s | 455 in 33 s |
| Typical round during a small-tab wait | Greedy token only, or greedy plus 1 small token | Decode picks, or empty rounds between deep tokens |
| Greedy depth during the phase | 3,508 → 4,489 | 3,486 → 3,691 |
| Greedy prefill per round | 1 token | 1 token, in about 1 round per 5-round cycle while prompts wait |
| Small-tab prefill per chunk | 1–3 tokens | 1–6 tokens |
| Predicted round time | 48 ms | 40 ms |
| Actual round time | **115 ms** (p95 140) | 32 ms (p95 135) |
| Actual ÷ predicted | **2.40** | 0.98 |
| Correction factor | 2.06 | 1.33 |
| Round time during a small-tab wait | 121 ms | 48 ms in this run, 83 ms in the second |
| Small-tab median TTFT | 13.50 s | 3.36 s |

Sample NEW rounds, with session positions:

```
chunk [[10, 3512, 1, 2353], [1, 0, 1, 26], [2, 0, 1, 26], [3, 0, 1, 26]]  est 81  obs 105  corr 2.08
chunk [[10, 3517, 1, 2348], [1, 1, 1, 25], [2, 1, 1, 25], [3, 1, 1, 25]]  est 82  obs 105  corr 2.10
chunk [[10, 4044, 1, 1821]]  wait [[1, 120, 3, 4], [2, 119, 4, 3], [3, 119, 4, 2]]  est 49  obs 122  corr 2.19
```

Chunk format is `[tab, position, tokens in this chunk, tokens left]`. The greedy tab moves every round, and small tabs move once every 5 rounds (3,512 → 3,517 for one small-tab token).

**Admission decisions for the greedy tab's pages, NEW:**

| Page | Position | Need | Decision | Why |
|---|---|---|---|---|
| 1 | 0 | 2,990 | Admitted | Fits the 8,192 limit |
| 2 | 2,937 | 2,992 | Admitted | 5,929 fits the 8,192 limit |
| 3 | Not reached in the run | | | Would be about 8,921 and so over the limit |

OLD has no admission control to record.

## 2. Where small-tab time goes

For NEW shared's median small-tab turn, TTFT 13.50 s:

| Phase | Time | Share |
|---|---|---|
| Waiting before the first chunk | 0.53 s | 4% |
| First chunk to last chunk | 12.98 s | 96% |

The 96% isn't the small tab's own work. It needs 26 tokens at a shallow position, roughly 0.15 s from the calibrated curve. The time goes to about 128 rounds, of which about 26 carry a small-tab token, and each round costs about 105 ms because the greedy tab's deep token is in it. Rounds without a greedy token take about 28 ms, so **the slow rounds are caused by the deep session in the same batch.**

The machine was busy throughout (100% decode time in the phase), so nothing is idle.

## 3. Hypotheses

n = 2, labelled diagnostics. All are NEW shared, 1 greedy tab, except OLD.

| Configuration | Small-tab TTFT per run, s | Small-tab turn per run, s | Greedy pages (ok, refused) per run |
|---|---|---|---|
| NEW baseline | 13.50, 13.31 | 19.74, 18.58 | 1, 0 |
| OLD baseline | 3.36, 3.22 | 4.12, 3.96 | 1, 0 |
| (a) Prefill share off | 3.14, 3.14 | 5.34, 5.30 | 1, 0 |
| (b) OLD calibration, NEW code | 11.97, 12.02 | 17.77, 15.79 | 1, 0 |
| (c) Session limit 4,096 | 0.54, 0.53 | 1.48, 1.38 | 1, 9 |
| (d) Shortest waiting prompt first (added by me) | 0.53, 0.53 | 6.17, 6.16 | 1, 0 |

- **(a) Confirmed as the trigger.** With the share off, NEW behaves like OLD. It would also remove the guarantee added for RQ2 M8.
- **(b) Ruled out.** The two calibrations are nearly the same, and the result barely moves.
- **(c) Works, but by removing the deep work.** The greedy tab's second page is refused, so no deep tokens run. It also halves what shared mode offers each session, so it's a policy change rather than a repair.
- **(d) Fixes the ordering** with no refusals and no limit change. Greedy page speed in the small-tab phase is about the same: 6.9–7.0 tok/s vs 7.6–7.8. Turn time is still about 1.5× OLD's.

## 4. Why the greedy tabs completed so few pages yet were never refused

**They were never refused because they never reached the limit in the time available.**

- Page 1 fits and finishes during the head start, in 77–86 s.
- Page 2 is admitted at position 2,937 and is at about 3,400–3,500 when the small tabs start.
- From there it gets 1 token per round at about 110 ms, or about 7.7 tok/s, so its remaining 2,400 or so tokens need about 5 minutes. The run ends about 130 s later and closes the tab, so page 2 isn't counted.
- The refusal would only come at page 3, which is never reached.

This matches the benchmark: 5 pages over 5 runs. With 2 greedy tabs the benchmark's split (5 pages and 0) fits the same first-arrival rule, but that's inferred, because this diagnostic had no per-round run for it.

**The 1-token rate is itself underpriced.** At depth 4,000 the calibrated rate is about 40 tok/s, but a 1-token chunk really runs at about 9 tok/s. Calibration probes only 64- and 256-token chunks, so it doesn't see this, and the correction factor sits at about 2 to compensate. This is a separate problem from the ordering, and it remains after the fix.

## Small pool: why every small-tab turn was refused

The fallback calibration isn't the cause: this decision uses only positions. n = 2 diagnostic, n_ctx 4,096, 2 greedy tabs.

```
small admit pos 0 need 90 → wait; others [[11, 1772], [10, 1772], [2, 0], [3, 0]]
```

Each greedy tab holds 1,772 positions (2 pages). The pool check adds each other session's position or fair share (512), whichever is larger, so:

1,772 + 1,772 + 512 + 512 + 90 = **4,658, over the 4,032 limit** (4,096 minus a 64-token margin).

Nothing can be reclaimed, because a greedy tab's first and latest turns are both protected. Each small tab waits 20 s and is then refused. The two idle small tabs' unused fair shares are what tip it: without them the total is 3,634, which would fit. Admission logged about 13,600 "wait" verdicts per run. This is a separate bug from the ordering, and the proposed fix doesn't address it.

## Proposed smallest fix (not applied)

`design_doc/proposals_2026-11/shortest_prompt_first.patch`: one sort key in `build_batch`, plus test t94.

```python
pending.sort(key=lambda s: (s.key[1] != foreground_tab_id,
                            len(s.inbox_tokens) - s.prefill_offset))
```

| Measure, W3 shared, 1 greedy tab | Now, benchmark | Expected, from the n = 2 diagnostic |
|---|---|---|
| Small-tab median TTFT | 16.70 s | About 0.5 s |
| Small-tab turn | 22.29 s | About 6 s (OLD: 5.31 s) |
| Greedy page speed in that phase | About 7–8 tok/s | About the same |

Absolute diagnostic times ran below the benchmark's even without the fix (13.4 vs 16.7 s), so the ratio is more reliable than the exact figure.

**What it doesn't fix:**
- Small-pool refusals.
- The underpriced 1-token rounds, or throughput against llama.cpp and Ollama.
- Greedy tabs still get few pages at depth.

**Risks:**
- A large page could wait longer behind a stream of short prompts. The 5-round stall bound still guarantees it progress, but this hasn't been measured beyond the run above.
- It hasn't been tested with 2 greedy tabs or in the real browser.

**If you approve it:** full suite in both modes, then rerun NEW W3 (0, 1, 2 greedy tabs, guard off, small pool) and W1, which together take about 1.5–2.5 hours. W2, W4, W5 and R1 are less likely to change, but that's a judgement rather than a measurement, so the report should say they weren't rerun.

## Suite on a scratch copy with the patch applied

The frozen tree wasn't touched. Test t94 is new, so each mode has one more test than the frozen build.

| Mode | Executed | Passed | Failed | Skipped (browser) |
|---|---|---|---|---|
| Partitioned | 91 | 90 | 1 (t48, same result as the frozen build) | 19 |
| Shared | 91 | 91 | 0 | 19 |

These don't count as a report that the fix is done: it isn't applied, and the real-browser tests and benchmark reruns haven't been run with it.
