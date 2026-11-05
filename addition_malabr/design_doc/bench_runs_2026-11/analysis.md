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
