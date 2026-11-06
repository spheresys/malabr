## Runs cut from the plan

On 2026-11-05 at 10:59 the user approved a trimmed list: 21 of the 41 remaining runs were kept and 20 were cut, to save an estimated 2–4 hours. Runs were chosen by what they measure, not by their results; none of the cut runs had been started.

| Cut run | n | Why it was cut | What is lost |
|---|---|---|---|
| OLD partitioned and OLD shared, W3, 2 greedy tabs | 2 | OLD is the slowest system here (64 min at 1 greedy tab) and already has the 1-greedy-tab result | No OLD vs NEW comparison at 2 greedy tabs |
| OLD shared, W3, 2 greedy tabs, guard off | 1 | NEW shared alone tests the guard on the frozen build | No OLD guard-off result |
| OLD shared guard on, OLD shared guard off, OLD partitioned guard on, W3 small pool | 3 | The OLD comparison stops at 1 greedy tab | No OLD vs NEW comparison in the small pool |
| NEW shared guard off, W3 small pool | 1 | Guard off is already measured at the production pool size | No guard-off result under scarce memory |
| Chrome `rss_5` | 1 | `rss_3` and `rss_4` give two runs for a reference system on a different model | Clean Chrome memory rests on n = 2 |
| Chrome relative rerun, 4 tabs (`relative2_4tabs_0`–`5`) | 6 | The 0.30→0.05 swing appeared at 8 tabs | Nonce fix not checked at 4 tabs |
| Chrome relative rerun, 8 tabs staggered (`relative2_8tabs_stagger_0`–`5`) | 6 | Same reason | Nonce fix not checked with staggered arrivals |

The tables above show these as "not run" or leave them out. No result is claimed for them.

## NEW shared small pool: calibration not measured

The calibration for NEW, shared KV, n_ctx 4,096 failed on both tries (`decode: failed to find a memory slot`; `FAILED: calibrate shared 4096` in `progress.log`). No calibration file exists for this configuration.

The small-pool run `new_malabr_shared_W3_g2_ctx4096` therefore did not use a measured calibration. The harness repeats the measurement when the run starts and, when it fails, uses the built-in fallback curve without saving it. Every other NEW configuration, including partitioned at 4,096, uses a measured calibration.

Scheduling figures from this one run are not like-for-like with the measured-calibration runs. The cause of the slot failure was not investigated, because the design was frozen.

## W2: survivor ratios against each system's own control

Survivors produced the same 384 tokens in every run, so token counts don't explain any ratio. Each range is across the 5 runs.

| System | Control s | After closing s | Ratio |
|---|---|---|---|
| llama.cpp partitioned | 9.27–9.59 | 10.73–11.32 | 0.84× |
| Ollama | 9.11–9.73 | 10.97–11.55 | 0.83× |
| llama.cpp unified | 9.06–9.47 | 6.39–6.63 | about 1.44× |

The slowdown is consistent in all 5 runs of both per-slot systems, so it is not noise. Survivors really finish 15–20% later after their neighbours are closed, while the same server with a unified cache speeds up. **The cause is not established:** no per-run server logs were kept, and no diagnostic was run.

## Memory caveats the † rule does not catch

† only checks swap when a run starts. Swap went from 3% at the start of `llama.cpp unified, W3, 1 greedy tab` to 64% at the start of the Ollama run, and was about 99% full during the Ollama run (10:37, runner RSS about 10 GB, load 14.7). NEW W3 at 1 greedy tab then started at 77% (partitioned) and 70% (shared), under the 80% limit. Treat these W3 runs as possibly affected by memory pressure even though they carry no †.

## W3: what the final runs show

Small-tab median TTFT, mean ± sd, n = 5. Page size is 900 tokens partitioned and 3,000 tokens shared.

| Greedy tabs | NEW partitioned | OLD partitioned | NEW shared | OLD shared |
|---|---|---|---|---|
| 0 | 0.58 ± 0.04 | 0.66 ± 0.03 | 0.56 ± 0.04 | 0.61 ± 0.06 |
| 1 | **0.67 ± 0.06**, 0 errors | 14.53 ± 0.82, 12 errors per run | **16.70 ± 1.78**, 0 errors | 4.61 ± 0.13, 0 errors |
| 2 | 0.84 ± 0.12, 0 errors | Cut | 22.13 ± 0.34, 0 errors | Cut |

- **Partitioned improved a lot.** At 1 greedy tab, small-tab TTFT fell from 14.53 s to 0.67 s, small-tab errors from 12 per run to 0, and failed rounds from about 38,785 per run to 0.
- **Shared regressed.** At 1 greedy tab, small-tab TTFT is about 3.6× OLD's (16.70 vs 4.61 s), and small-tab turns take about 4× as long (22.29 vs 5.31 s). OLD's result here is the better one.
- **Greedy tabs in NEW shared:** they completed 5 pages over 5 runs at 1 greedy tab, and 5 pages between both tabs at 2 greedy tabs, with no refusals.
- **The guard isn't the visible cause.** Guard off at 2 greedy tabs gave 21.38 ± 0.25 s, about the same as 22.13 ± 0.34 s with it on.
- **Cause (diagnostic, n = 2):** the prefill guarantee goes to the first-arrived prompt, so the greedy page's deep token runs in nearly every round and a short prompt gets one token per 5 rounds. See `design_doc/diagnostics_2026-11/W3_SHARED_DIAGNOSIS.md`. A one-line fix is proposed there and not applied, so these numbers are for the frozen build.

### Small pool (n_ctx 4,096, 2 greedy tabs)

- **NEW shared refused every small-tab turn:** 18 of 18 in each of the 5 runs, with `the server is at capacity`. There is no TTFT to report. No failed rounds were recorded, but small tabs got no service. The diagnosis shows why: each greedy tab holds 1,772 protected positions, and with the idle small tabs' fair shares the total exceeds the pool limit, so turns wait 20 s and are refused. This decision uses only positions, so the fallback calibration isn't the cause.
- **NEW partitioned:** median 0.62 ± 0.08 s. One run had 2 small-tab errors and a p95 of 581 s, which is why the p95 reads 117.43 ± 259.20. That outlier hasn't been explained or rerun.
- **llama.cpp unified:** median 1.12 ± 0.20 s, but 15 errors per run, including HTTP 500 `Context size has been exceeded`.
- **llama.cpp partitioned and Ollama:** medians 0.58 ± 0.07 s and 0.78 ± 0.04 s, with 0 errors. Their greedy tabs were refused every time.

## Chrome relative score: does the swing disappear?

**No.** With a per-run nonce and the script fixed, the 8-tab scores were 0.10, 0.61, 0.10, 0.35, 0.11 (run 0, discarded as warm-up, was 0.09). The first set was 0.60, 0.27, 0.05, 0.05, 0.05.

The nonce removed the settling at 0.05, which fits a prompt-cache effect in the first set but doesn't prove it. The large run-to-run swing remains, so Chrome's relative score at 8 tabs is unstable across runs, and its cause isn't established. n = 5, on a different model. `relative2_8tabs_4` failed once with rc=2 after 5 s and passed on retry. The error text wasn't kept.

## Chrome sixth session

In all 4 memory runs, session 6 reported usage 0 and process-tree RSS collapsed, to 1,124, 303, 832 and 1,121 MB. **Stopped responding, cause not established.**

- **Reproduced without a full swap:** `rss_3` started at 0% swap, and `rss_4` at 49%. Swap rose during `rss_3`, so memory pressure isn't completely ruled out.
- **Kernel log:** no OOM kill, segfault or Chrome entry during the `rss_3` and `rss_4` window.
- **chrome://crashes equivalent:** the harness profile has no `Crash Reports` folder and no recent `.dmp` files.
- **Chrome console:** not captured. The script catches `append` errors silently and stops filling, so no error text exists.

Clean memory growth (`rss_3`, `rss_4`, sessions 0→5): 1,310 ± 30 MB per session, n = 2, each session holding about 8,208 tokens. Chrome version: 153.0.8010.52.

## Other run notes

- **Memory:** swap was cleared by hand at about 15:15 on 2026-11-05, before NEW W3 at 2 greedy tabs. It was 29–30% at the start of NEW shared at 2 greedy tabs and guard off, and 0% at the start of the small-pool runs. No run started above the 80% limit.
- **Interruptions:** the drive dropped from 12:39 to 14:20 on 2026-11-05, and `ollama W3 greedy 2` was redone in full. The machine rebooted during the pause that followed; the OLD tree was rebuilt and checked against `610c233`, and only llama.cpp and Ollama ran from it afterwards.

## Fixed build: second set of numbers

Build `c859db3a5c2e`, tag `design-fix-2026-11-06`. It differs from the freeze by one change: the shortest waiting prompt now gets prefill first, after the visible tab. The tables above this section remain the frozen build's record; raw results for this set are in `raw_fix/`.

**Suite on this build:** partitioned 90/91 (only t48 fails), shared 91/91, 19 browser tests skipped.

**Rerun:** NEW W1 and W3 only, n = 5 each, using the freeze's calibrations. **Not rerun:** W2, W4, W5 and R1, nor any baseline, so their numbers still come from the frozen build or the earlier baseline runs. W4 was left out because one shared-mode run takes over 4 hours.

### W3 small tabs, frozen vs fixed (n = 5 each)

| Run | TTFT median s, frozen | fixed | p95 s, frozen | fixed | Turn s, frozen | fixed | Errors per run, frozen | fixed | Greedy pages over 5 runs (ok, refused), fixed |
|---|---|---|---|---|---|---|---|---|---|
| partitioned, 0 greedy | 0.58 ± 0.04 | 0.82 ± 0.04 | 1.23 ± 0.04 | 1.59 ± 0.10 | 1.06 ± 0.03 | 1.47 ± 0.09 | 0.0 ± 0.0 | 0.0 ± 0.0 | none |
| shared, 0 greedy | 0.56 ± 0.04 | 0.63 ± 0.04 | 1.05 ± 0.05 | 1.23 ± 0.10 | 1.08 ± 0.09 | 1.31 ± 0.16 | 0.0 ± 0.0 | 0.0 ± 0.0 | none |
| partitioned, 1 greedy | 0.67 ± 0.06 | 0.75 ± 0.08 | 1.48 ± 0.14 | 1.39 ± 0.06 | 1.29 ± 0.12 | 1.36 ± 0.09 | 0.0 ± 0.0 | 0.0 ± 0.0 | tab 10: 10, 43 |
| shared, 1 greedy | 16.70 ± 1.78 | 0.83 ± 0.04 | 21.15 ± 0.24 | 1.54 ± 0.11 | 22.29 ± 1.00 | 6.74 ± 0.07 | 0.0 ± 0.0 | 0.0 ± 0.0 | tab 10: 5, 0 |
| partitioned, 2 greedy | 0.84 ± 0.12 | 0.81 ± 0.05 | 1.81 ± 0.67 | 1.54 ± 0.11 | 1.49 ± 0.15 | 1.47 ± 0.08 | 0.0 ± 0.0 | 0.0 ± 0.0 | tab 10: 10, 86; tab 11: 10, 45 |
| shared, 2 greedy | 22.13 ± 0.34 | 0.93 ± 0.06 | 23.82 ± 0.54 | 2.47 ± 0.91 | 29.67 ± 0.33 | 7.66 ± 0.15 | 0.0 ± 0.0 | 0.0 ± 0.0 | tab 10: 5, 0; tab 11: 0, 0 |
| shared, 2 greedy, guard off | 21.38 ± 0.25 | 0.91 ± 0.09 | 23.66 ± 0.74 | 2.49 ± 1.27 | 29.55 ± 1.43 | 7.67 ± 0.20 | 0.0 ± 0.0 | 0.0 ± 0.0 | tab 10: 4, 0; tab 11: 1, 0 |
| partitioned, small pool | 0.62 ± 0.08 | 0.70 ± 0.02 | 117.43 ± 259.20 | 1.46 ± 0.23 | 1.25 ± 0.13 | 1.44 ± 0.07 | 0.4 ± 0.9 | 0.0 ± 0.0 | tab 10: 0, 45; tab 11: 0, 45 |
| shared, small pool | – | – | – | – | – | – | 18.0 ± 0.0 | 18.0 ± 0.0 | tab 10: 10, 348; tab 11: 10, 322 |

### W1, frozen vs fixed (n = 5 each)

| Scenario, mode | Visible TTFT s, frozen | fixed | Median TTFT s, frozen | fixed | p95 s, frozen | fixed | Throughput tok/s, frozen | fixed | Stalls >1 s visible/bg, fixed |
|---|---|---|---|---|---|---|---|---|---|
| 8 tabs, visible last, partitioned | 0.55 ± 0.02 | 0.52 ± 0.01 | 3.74 ± 0.38 | 2.91 ± 0.18 | 5.74 ± 0.30 | 5.55 ± 0.07 | 31.92 ± 1.09 | 32.63 ± 0.18 | 0.00 ± 0.00 / 0.00 ± 0.00 |
| 8 tabs, visible last, shared | 0.49 ± 0.01 | 0.48 ± 0.04 | 2.58 ± 0.18 | 2.01 ± 0.07 | 4.18 ± 0.18 | 3.80 ± 0.11 | 35.05 ± 0.94 | 36.13 ± 0.36 | 0.00 ± 0.00 / 0.00 ± 0.00 |
| 4 tabs, visible last, partitioned | 0.55 ± 0.11 | 0.44 ± 0.04 | 1.27 ± 0.15 | 1.09 ± 0.18 | 2.59 ± 0.29 | 2.33 ± 0.37 | 31.86 ± 0.51 | 33.63 ± 1.64 | 0.00 ± 0.00 / 0.00 ± 0.00 |
| 4 tabs, visible last, shared | 0.51 ± 0.12 | 0.42 ± 0.02 | 1.05 ± 0.17 | 0.88 ± 0.05 | 1.93 ± 0.23 | 1.72 ± 0.06 | 33.66 ± 1.11 | 35.36 ± 0.87 | 0.00 ± 0.00 / 0.00 ± 0.00 |
| 8 tabs, visible first (control), partitioned | 0.25 ± 0.01 | 0.22 ± 0.02 | 4.43 ± 0.30 | 4.28 ± 0.38 | 6.67 ± 0.41 | 6.73 ± 0.46 | 30.28 ± 0.65 | 30.33 ± 0.94 | 0.00 ± 0.00 / 0.00 ± 0.00 |
| 8 tabs, visible first (control), shared | 0.25 ± 0.01 | 0.25 ± 0.03 | 2.69 ± 0.24 | 2.27 ± 0.16 | 4.24 ± 0.11 | 3.98 ± 0.24 | 34.22 ± 0.71 | 34.35 ± 2.62 | 0.00 ± 0.00 / 0.00 ± 0.00 |

**What changed:**
- **W3 shared, regression fixed:** small-tab TTFT 16.70 → 0.83 s at 1 greedy tab, and 22.13 → 0.93 s at 2. TTFT is now better than OLD's 4.61 s at 1 greedy tab, but the turn takes 6.74 s against OLD's 5.31 s, so it's still about 27% slower to complete.
- **W1 improved a little in both modes:** at 8 tabs, median TTFT went 3.74 → 2.91 s partitioned and 2.58 → 2.01 s shared. Throughput is about the same.
- **Partitioned small-pool outlier:** the one run with a 581 s p95 didn't recur (p95 1.46 ± 0.23 s), and it remains unexplained.

**What didn't change:**
- **Small-pool shared refusals:** all 18 small-tab turns are still refused per run. This is a separate admission bug.
- **Greedy tabs in shared mode:** still 5 pages over 5 runs. At 2 greedy tabs, the second tab completed none, with guard on.
- **Throughput against llama.cpp and Ollama:** still less than half at 8 tabs.

**Unexplained:** at 0 greedy tabs, TTFT rose from 0.58 to 0.82 s partitioned and from 0.56 to 0.63 s shared, and turn time rose too. Other apps were using more memory during these reruns, with about 7 GB available against about 9 GB, but that hasn't been tested as the cause. W1 got faster on the same build.
