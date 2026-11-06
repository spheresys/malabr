# Interim numbers, 2026-11-05

Snapshot at 11:11 IST, while the campaign is still running. It uses existing result files only, and no new runs were started for it. These numbers aren't final and can be replaced by `BENCHMARKS_2026-11.md`.

**Format:** mean ± sample sd over n runs. A warm-up run is discarded before the n.

## Configuration

| System | Build | Pool and modes | Threads |
|---|---|---|---|
| MALABR NEW | `35e90668b6d4`, tag `design-freeze-2026-11` | n_ctx 16,384, 8 slots; partitioned 2,048 per session; shared soft limit 8,192 | 3 |
| MALABR OLD | `610c233` engine; tests and `calibration.py` from `83e1ae9ca6612` | Same | 3 |
| llama.cpp | b12078 | `-c 16384 -np 8`; partitioned 2,048 per slot; unified 16,384 | `-t 3 -tb 3` |
| Ollama | 0.40.14 | num_ctx 2,048 × 8 parallel; no unified option | 3 |
| Chrome | Gemini Nano CPU variant | Its own, 9,216 per session | Its own |

All systems except Chrome use the same Qwen3-0.6B Q8_0 GGUF. Chrome is a reference only, because it uses a different model. NEW uses calibrations measured on the frozen build. OLD uses its own from 2026-11-04.

**Caveats for every table:**
- Brave and VS Code stayed open during the runs, so the machine wasn't fully idle.
- Servers have no real visible tab. Their "visible" tab is just the one that asked last, or first in the control.
- Nothing has been rerun to check surprising results.

## 1. OLD vs NEW

### W1, scheduling (n = 5, 64-token cap, tabs 20 ms apart)

Background TTFT was not saved per tab. The closest measures are the median and p95 TTFT across all tabs, which include the visible tab.

| Scenario | System | Visible TTFT s | Median TTFT s | p95 TTFT s | Throughput tok/s | Stalls >1 s, visible / bg |
|---|---|---|---|---|---|---|
| 8 tabs, visible last | NEW partitioned | 0.55 ± 0.02 | 3.74 ± 0.38 | 5.74 ± 0.30 | 31.92 ± 1.09 | 0 / 0 |
| | OLD partitioned | 2.75 ± 1.40 | 6.15 ± 0.14 | 7.78 ± 0.35 | 30.06 ± 0.58 | 0 / 1.40 ± 0.55 |
| | NEW shared | 0.49 ± 0.01 | 2.58 ± 0.18 | 4.18 ± 0.18 | 35.05 ± 0.94 | 0 / 0 |
| | OLD shared | 1.72 ± 0.51 | 5.73 ± 0.23 | 7.40 ± 0.25 | 43.05 ± 0.87 | 0 / 0 |
| 4 tabs, visible last | NEW partitioned | 0.55 ± 0.11 | 1.27 ± 0.15 | 2.59 ± 0.29 | 31.86 ± 0.51 | 0 / 0.40 ± 0.89 |
| | OLD partitioned | 0.84 ± 0.17 | 2.12 ± 0.17 | 4.09 ± 0.21 | 30.97 ± 0.84 | 0 / 1.00 ± 0.00 |
| | NEW shared | 0.51 ± 0.12 | 1.05 ± 0.17 | 1.93 ± 0.23 | 33.66 ± 1.11 | 0 / 0 |
| | OLD shared | 0.58 ± 0.09 | 2.25 ± 0.14 | 4.12 ± 0.20 | 41.36 ± 1.23 | 0 / 0 |
| 8 tabs, visible first (control) | NEW partitioned | 0.25 ± 0.01 | 4.43 ± 0.30 | 6.67 ± 0.41 | 30.28 ± 0.65 | 0 / 0 |
| | OLD partitioned | 0.46 ± 0.13 | 7.41 ± 0.32 | 8.59 ± 0.22 | 29.10 ± 0.39 | 0 / 0 |
| | NEW shared | 0.25 ± 0.01 | 2.69 ± 0.24 | 4.24 ± 0.11 | 34.22 ± 0.71 | 0 / 0 |
| | OLD shared | 0.30 ± 0.02 | 5.93 ± 0.14 | 7.76 ± 0.14 | 42.46 ± 0.68 | 0 / 0 |

- **Better:** NEW's TTFT is lower than OLD's in every row.
- **Worse:** NEW shared throughput is about 19% below OLD shared at 8 tabs (35.05 vs 43.05 tok/s), and about 19% below at 4 tabs. Its 8-tab makespan rose from 11.90 ± 0.24 s to 14.61 ± 0.39 s.

### W2, closing tabs (n = 5, 8 tabs, 96-token cap)

Survivors produced the same 384 tokens in each run shown in this comparison. Speed-up is control time ÷ close time, paired by run.

| System | Survivors finish, control s | Survivors finish, close s | Speed-up | Tokens after close | Closed at s |
|---|---|---|---|---|---|
| NEW partitioned | 23.58 ± 0.63 | 14.45 ± 0.61 | 1.64 ± 0.10 | 0.00 ± 0.00 | 7.24 ± 0.52 |
| OLD partitioned | 24.17 ± 0.62 | 14.56 ± 0.61 | 1.66 ± 0.04 | 0.00 ± 0.00 | 7.59 ± 0.15 |
| NEW shared | 20.92 ± 0.58 | 12.02 ± 0.32 | 1.74 ± 0.07 | 0.00 ± 0.00 | 4.61 ± 0.18 |
| OLD shared | 17.58 ± 1.51 | 12.21 ± 0.65 | 1.45 ± 0.18 | 0.00 ± 0.00 | 7.71 ± 0.43 |

**Caveat:** NEW shared closed tabs about 3 s earlier than OLD shared, because tabs are closed once all four have a first token. An earlier close leaves more work to save, so 1.74 vs 1.45 isn't a clean improvement. NEW shared's control is also slower than OLD shared's (20.92 vs 17.58 s).

### W4, recall at overflow

| System | n | Limit per session | Control recalled | Overflow recalled | Failed fill turns | Compactions | Time per trial s |
|---|---|---|---|---|---|---|---|
| NEW partitioned | 20 | 2,048 | 20/20 | 20/20 | 0 | 4.00 ± 0.00 | 71.26 ± 0.89 |
| OLD partitioned | 20 | 2,048 | 20/20 | 20/20 | 0 | 2.00 ± 0.00 | 79.71 ± 0.62 |
| NEW shared | 8 | 8,192 | 8/8 | 8/8 | 1 trial had 1 timeout | 5.00 ± 0.00 | 1,920.09 ± 5.28 |
| OLD shared | **3** | 8,192 | 3/3 | 3/3 | All 3 trials had 3 | Not kept | 1,262, 1,489, 1,852 |

**Caveats:**
- OLD shared is n = 3, recovered from `console.txt` after a drive drop; there is no result file.
- Its recall is weaker evidence, because turns that failed never entered the context.
- Shared mode ran 8 trials rather than 20, because one trial takes about 30 minutes.

### W5, memory (n = 5, each session holds a page of about 1,400 tokens)

See section 3. Growth from 0 to 8 sessions was about the same for OLD and NEW, so NEW shows no memory improvement or regression here.

## 2. NEW vs llama.cpp and Ollama

### W1 (n = 5)

| Scenario | System | Visible TTFT s | Median TTFT s | p95 TTFT s | Throughput tok/s | Stalls >1 s, visible / bg |
|---|---|---|---|---|---|---|
| 8 tabs, visible last | NEW partitioned | 0.55 ± 0.02 | 3.74 ± 0.38 | 5.74 ± 0.30 | 31.92 ± 1.09 | 0 / 0 |
| | NEW shared | 0.49 ± 0.01 | 2.58 ± 0.18 | 4.18 ± 0.18 | 35.05 ± 0.94 | 0 / 0 |
| | llama.cpp partitioned | 1.31 ± 0.14 | 1.30 ± 0.14 | 1.31 ± 0.14 | 78.90 ± 0.98 | 0 / 0 |
| | llama.cpp unified | 1.94 ± 0.04 | 1.94 ± 0.04 | 1.94 ± 0.04 | 73.18 ± 1.10 | 0 / 3.00 ± 0.00 |
| | Ollama | 1.23 ± 0.04 | 1.23 ± 0.04 | 1.23 ± 0.04 | 78.36 ± 2.11 | 0 / 0 |
| 4 tabs, visible last | NEW partitioned | 0.55 ± 0.11 | 1.27 ± 0.15 | 2.59 ± 0.29 | 31.86 ± 0.51 | 0 / 0.40 ± 0.89 |
| | NEW shared | 0.51 ± 0.12 | 1.05 ± 0.17 | 1.93 ± 0.23 | 33.66 ± 1.11 | 0 / 0 |
| | llama.cpp partitioned | 0.72 ± 0.13 | 0.55 ± 0.11 | 0.72 ± 0.13 | 67.35 ± 3.61 | 0 / 0 |
| | llama.cpp unified | 0.89 ± 0.03 | 0.64 ± 0.02 | 0.89 ± 0.03 | 65.61 ± 1.64 | 0 / 0 |
| | Ollama | 0.66 ± 0.08 | 0.49 ± 0.04 | 0.66 ± 0.08 | 69.91 ± 2.80 | 0 / 0 |
| 8 tabs, visible first (control) | NEW partitioned | 0.25 ± 0.01 | 4.43 ± 0.30 | 6.67 ± 0.41 | 30.28 ± 0.65 | 0 / 0 |
| | NEW shared | 0.25 ± 0.01 | 2.69 ± 0.24 | 4.24 ± 0.11 | 34.22 ± 0.71 | 0 / 0 |
| | llama.cpp partitioned | 0.49 ± 0.01 | 1.29 ± 0.08 | 1.29 ± 0.08 | 77.62 ± 0.46 | 0 / 0 |
| | llama.cpp unified | 0.69 ± 0.11 | 1.69 ± 0.19 | 1.79 ± 0.05 | 72.15 ± 1.39 | 0.80 ± 0.45 / 1.60 ± 0.89 |
| | Ollama | 0.48 ± 0.01 | 1.28 ± 0.13 | 1.28 ± 0.13 | 78.99 ± 0.96 | 0.20 ± 0.45 / 0.20 ± 0.45 |

- **NEW is better for the visible tab:** at 8 tabs, its TTFT is 0.49–0.55 s vs 1.23–1.94 s.
- **NEW is worse overall:** at 8 tabs, its median tab waits 2–3× longer, its p95 is 2–4× longer, and its throughput is less than half (32–35 vs 73–79 tok/s). The servers batch all tabs together and don't favour any tab.
- **Likely cause, not proven:** the cost curve overprices batches, allowing about 2 decode picks per round where llama.cpp batches all 8.

### W2 (n = 5)

| System | Survivors finish, control s | Survivors finish, close s | Speed-up | Tokens after close | Closed at s |
|---|---|---|---|---|---|
| NEW partitioned | 23.58 ± 0.63 | 14.45 ± 0.61 | 1.64 ± 0.10 | 0.00 ± 0.00 | 7.24 ± 0.52 |
| NEW shared | 20.92 ± 0.58 | 12.02 ± 0.32 | 1.74 ± 0.07 | 0.00 ± 0.00 | 4.61 ± 0.18 |
| llama.cpp partitioned | 9.37 ± 0.14 | 11.09 ± 0.23 | 0.84 ± 0.01 | 4.20 ± 0.45 | 1.47 ± 0.06 |
| llama.cpp unified | 9.36 ± 0.18 | 6.50 ± 0.11 | 1.44 ± 0.03 | 4.00 ± 0.00 | 1.95 ± 0.04 |
| Ollama | 9.40 ± 0.26 | 11.23 ± 0.28 | 0.84 ± 0.02 | Not exposed | 1.51 ± 0.05 |

**Caveats:**
- NEW has the larger speed-up, but the servers finish sooner in absolute time, both with and without closing tabs.
- Close moments differ a lot between systems, so speed-ups compare only roughly.
- Tokens after close are measured differently: MALABR counts frames after the close, and llama.cpp uses its token counter minus delivered tokens.
- Survivors on llama.cpp partitioned and Ollama really do slow down after a close. The cause isn't established.

W4 is in section 4 and W5 is in section 3.

## 3. Memory: RSS growth per added session

W5 values are (RSS at 8 sessions − RSS at 0) ÷ 8. MALABR runs the real `app.py`, and RSS covers the server process only (Ollama: server + runner).

| System | n | RSS at 0 sessions MB | RSS at 8 sessions MB | Growth 0→8 MB | Per session MB |
|---|---|---|---|---|---|
| NEW partitioned | 5 | 2,530 | 2,550 | 19.36 ± 0.38 | 2.42 ± 0.05 |
| NEW shared | 5 | 2,530 | 2,550 | 19.94 ± 0.06 | 2.49 ± 0.01 |
| OLD partitioned | 5 | 2,524 | 2,544 | 19.59 ± 0.11 | 2.45 ± 0.01 |
| OLD shared | 5 | 2,524 | 2,544 | 19.96 ± 0.06 | 2.49 ± 0.01 |
| llama.cpp partitioned | 5 | 2,474 | 3,557 | 1,082.84 ± 0.00 | 135.36 ± 0.00 |
| llama.cpp unified | 5 | 2,474 | 3,554 | 1,079.96 ± 0.13 | 135.00 ± 0.02 |
| Ollama | 5 | 2,519 | 3,597 | 1,077.79 ± 8.60 | 134.72 ± 1.08 |

The reason for MALABR's lower growth wasn't investigated.

**Chrome, reference only, n = 1 per row:**

| Run | Status | Base MB | Sessions used | RSS MB | Per session MB | Session 6 |
|---|---|---|---|---|---|---|
| `rss_1` | **Pressured, not a headline** | 3,082 | 1→5 | 4,614→9,737 | About 1,331 | Usage 0, RSS 1,124 |
| `rss_2` | **Pressured, not a headline** | 3,071 | 1→3 | 4,595→7,129 | About 1,353 | Usage 0, RSS 303 |

**Caveats:**
- Each Chrome session is filled to about 8,208 tokens, against about 1,400 in W5, and the model differs. The per-session figure can't be compared directly with the table above.
- `rss_2` RSS falls at 4 sessions (6,987 MB) because memory was under pressure, so only sessions 1–3 are used.
- Session 6 is currently described as "stopped responding, cause not established". That check isn't finished.

## 4. Overflow behaviour (W4)

Each trial puts a fresh secret in the first turn, fills to 1.2× the limit, then asks for the secret directly. Each row uses the configuration in section 1.

| System | n | Control recalled | Overflow recalled | Reported or silent |
|---|---|---|---|---|
| NEW partitioned | 20 | 20/20 | 20/20 | No failure; compacted 4 times per trial |
| NEW shared | 8 | 8/8 | 8/8 | Compacted 5 times per trial; 1 trial had a fill turn hit the harness's 120 s timeout, with no secret lost |
| OLD partitioned | 20 | 20/20 | 20/20 | No failure; compacted 2 times per trial |
| OLD shared | **3** | 3/3 | 3/3 | Fill turns failed in all trials (3 each); see the caveat in section 1 |
| llama.cpp partitioned | 20 | 20/20 | 18/20 | **Reported:** HTTP 400 on 3 of 7 fill turns in all 20 trials; the 2 misses had wrong or incomplete digits, with no error |
| llama.cpp unified | 8 | 8/8 | 8/8 | **Reported:** HTTP 400 on 3 of 50 fill turns in all 8 trials |
| Ollama | 20 | 20/20 | **0/20** | **Silent:** no errors, and all 20 answers were "OK." |
| Chrome (reference) | 1 | Not this test | Earliest content not kept | **Reported as an event:** 1 `contextoverflow`, no error, and it kept answering |

**Caveats:**
- llama.cpp's recall doesn't show the secret survived an overflow, because the rejected turns never entered the context.
- Chrome used a different test, with 4 appends of about 12,000 characters, and no fresh-secret trial.

## 5. Isolation (R1)

The positive control is the session holding the secret, which should recall it. The negative control is another session, which should not.

| System | Trials | Positive control: holder recalled | Negative control: other session recalled (leaks) |
|---|---|---|---|
| NEW partitioned | 20 | 20/20 | 0/20 |
| NEW shared | 20 | 20/20 | 0/20 |
| OLD | Not run | – | – |
| llama.cpp partitioned | 20 | 19/20 | 0/20 |
| llama.cpp unified | 20 | 19/20 | 0/20 |
| Ollama | 20 | 19/20 | 0/20 |
| Chrome (reference, 5 checks, n = 1) | 1 run | Pass | Cross-origin, same-origin other tab and after `destroy()`: all pass, 0 leaks |

**Caveats:**
- OLD wasn't in the R1 plan. Its isolation evidence is the §12 suite.
- A baseline holder miss is a recall failure, not a leak.
- Zero leaks doesn't separate MALABR from the baselines in this test.

## 6. Stall fix: before vs after

`pipeline_bench`, n = 3, a smaller test than the campaign's W1.

| Build | Background stalls >1 s | Worst background gap s | Visible TTFT s | Throughput tok/s |
|---|---|---|---|---|
| Before the fix | 3.67 ± 0.58 (4, 3, 4) | 2.09 ± 0.37 | 0.14 ± 0.04 | 36.3 ± 1.0 |
| After the fix | 0.00 ± 0.00 (0, 0, 0) | 0.49 ± 0.04 | 0.12 ± 0.00 | 37.7 ± 1.2 |

**Caveats:**
- The test's memory mode isn't confirmed in the saved output.
- The "before" numbers survive only in the session transcript. The "after" numbers are in `proposals_2026-11/step4/stall_trial2.txt`.
- W1's OLD vs NEW stall counts in section 1 don't isolate this fix, because NEW also includes admission control and the calibration changes.
- NEW isn't stall-free: 1 of 5 runs at 4 tabs, partitioned, had 2 background stalls.

## 7. Test suite on the frozen build

| Mode | Executed | Passed | Failed | Skipped (browser) |
|---|---|---|---|---|
| Partitioned | 90 | 89 | 1 (t48) | 19 |
| Shared | 90 | 90 | 0 | 19 |

**t48:** the failure repeated with the same result on each run I've seen in partitioned mode (1 top-1 flip beyond the noise floor, mean KL 0.0699 vs floor 0.0000 nats). It's labelled flaky, but it looks deterministic.

## 8. Page-segment re-read cost (why it wasn't adopted)

Mean over n = 5. Re-read cost is the time with a re-read minus the time without. The patch was kept as evidence and not applied, so the page stays in the protected first turn.

| Mode | Page tokens | Depth tokens | With re-read s (min–max) | Without s | Re-read cost s |
|---|---|---|---|---|---|
| Partitioned | 900 | 0 | 8.26 (8.14–8.32) | 0.07 | **8.19** |
| Partitioned | 900 | 1,000 | 19.65 (19.52–19.82) | 0.34 | **19.31** |
| Shared | 900 | 0 | 8.27 (8.15–8.34) | 0.07 | **8.20** |
| Shared | 900 | 1,000 | 28.17 (27.92–28.24) | 0.34 | **27.83** |
| Shared | 900 | 4,000 | 82.51 (82.24–82.76) | 1.34 | **81.17** |
| Shared | 3,000 | 0 | 79.70 (77.72–79.84) | 0.09 | **79.62** |
| Shared | 3,000 | 1,000 | 162.90 (162.32–163.07) | 0.34 | **162.55** |
| Shared | 3,000 | 4,000 | 328.23 (327.48–329.26) | 1.35 | **326.88** |

**Caveat:** this was measured before the freeze on the patched candidate, with a fixture rather than the real browser. Standard deviations weren't saved.

## Pending, no numbers here

- **W3, 0 and 1 greedy tabs, all systems:** finished, but held for the final report as you asked.
- **W3, 2 greedy tabs:** llama.cpp ×2, Ollama, NEW ×2.
- **W3, guard off:** NEW shared.
- **W3, small pool:** NEW ×2, llama.cpp ×2, Ollama. NEW shared here has no measured calibration and will use the fallback curve.
- **Chrome memory:** `rss_3`, `rss_4`.
- **Chrome relative:** the 6 runs at 8 tabs, with the nonce and script fix.
- **Analysis and final reports:** Chrome session-6 check, `BENCHMARKS_2026-11.md`, `OVERNIGHT_2026-11-05.md`, `git fsck`, final commit and copy.
