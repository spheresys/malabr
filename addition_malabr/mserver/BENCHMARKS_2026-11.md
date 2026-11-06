# Benchmarks, November 2026

Generated from `design_doc/bench_runs_2026-11/` (raw results, tracked). Frozen build `35e90668b6d4`, tag `design-freeze-2026-11`.

Every table is one workload. † = started with swap over 80% full. "not run" = no result file yet.

## Systems and configuration

| System | Build | Pool | Modes | Threads |
|---|---|---|---|---|
| MALABR NEW | `35e90668b6d4` (frozen) | n_ctx 16,384, 8 slots (small pool 4,096) | partitioned: 2,048 per session; shared: soft 8,192 | 3 |
| MALABR OLD | `610c233` engine; tests and `calibration.py` from `83e1ae9` | same | same | 3 |
| llama.cpp server | b12078, same GGUF | -c 16384 -np 8 | `--no-kv-unified`: 2,048; `--kv-unified`: 16,384 | -t 3 -tb 3 |
| Ollama | 0.40.14, same GGUF | num_ctx 2,048 × 8 parallel | no unified option | num_thread 3 |
| Chrome | stable 153, Gemini Nano CPU variant, `--disable-gpu` | its own (9,216 per session) | – | its own |

Model for all but Chrome: Qwen3-0.6B Q8_0. MALABR runs use a calibration measured for that build and configuration; OLD keeps the one measured for its baselines on 2026-11-04.

**Freeze includes:** admission control, the fair-share floor, per-session failure isolation, the prefill share, keyed calibration, shape-based KV sizing, and the stall fix.
**Freeze excludes:** page segments (the page stays in the protected first turn; `proposals_2026-11/page_segments.patch`, measured re-read cost 8–327 s), joint batch cost in `CostCurve`, the compute buffer in the memory budget, some startup log fields, and any change to prefill at depth (small chunks at depth run about 6× slower than large ones).

## Summary

Controlled comparison: MALABR NEW and OLD, llama.cpp and Ollama all use the same Qwen3-0.6B GGUF. Chrome is a reference only, on a different model. Mean ± sd unless stated; n = 5 except where the tables say otherwise.

**Two builds.** Tables below are for the frozen build, tag `design-freeze-2026-11`. A one-line fix for the W3 shared-mode regression then went into build `c859db3a5c2e`; W1 and W3 were rerun on it. See "Fixed build: second set of numbers".

### What improved from OLD to NEW

- **Visible-tab TTFT, 8 tabs:** 2.75 ± 1.40 → 0.55 ± 0.02 s partitioned, and 1.72 ± 0.51 → 0.49 ± 0.01 s shared.
- **Median TTFT across all tabs, 8 tabs:** 6.15 → 3.74 s partitioned, and 5.73 → 2.58 s shared.
- **W3 partitioned, 1 greedy tab:** small-tab TTFT 14.53 ± 0.82 → 0.67 ± 0.06 s, and small-tab errors 12 per run → 0. Admission control is the main change between these builds, but this comparison doesn't isolate it.
- **Background stalls, partitioned, 8 tabs:** 1.40 ± 0.55 → 0.

### What got worse from OLD to NEW

- **W3 shared, 1 greedy tab:** small-tab TTFT 4.61 ± 0.13 → **16.70 ± 1.78 s**, about 3.6× slower. Diagnostic runs trace it to the order in which waiting prompts get the prefill guarantee, and a one-line fix is proposed but not applied.
- **Shared throughput, 8 tabs:** 43.05 ± 0.87 → 35.05 ± 0.94 tok/s, about 19% lower, and makespan 11.90 → 14.61 s.

### What stayed about the same

- **W5 memory:** about 20 MB from 0 to 8 sessions on both builds.
- **W2 partitioned speed-up:** 1.66 ± 0.04 → 1.64 ± 0.10.
- **W4 recall, partitioned:** 20/20 on both builds.

### NEW against llama.cpp and Ollama

- **NEW wins on the visible tab and memory:** visible TTFT at 8 tabs is 0.49–0.55 s vs 1.23–1.94 s, and memory growth is about 2.5 MB per session vs about 135 MB.
- **NEW wins on overflow against Ollama:** 20/20 recall vs 0/20, with Ollama's loss silent. llama.cpp reports overflow with HTTP 400 rather than compacting.
- **NEW loses on total work:** throughput is 32–35 vs 73–79 tok/s, makespan about 2×, and median tab TTFT 2–3× longer at 8 tabs. The likely cause, not proven, is that the cost curve overprices batches.
- **W3 partitioned is level:** small-tab TTFT at 2 greedy tabs is 0.84 ± 0.12 s vs llama.cpp 1.04 ± 0.11 s, and much better than Ollama's 6.71 ± 0.82 s.
- **W3 shared is mixed:** at 1 greedy tab, NEW shared (16.70 s) is much slower than llama.cpp unified (3.17 s). llama.cpp unified then has 8.8 errors per run and 93.57 s small-tab turns at 2 greedy tabs, where NEW shared has 0 errors and 29.67 s turns.
- **Small pool, shared: NEW refuses all small-tab turns** (18 of 18 per run). This is the worst single result. The fix doesn't change it.
- **Isolation doesn't separate the systems:** 0 leaks in 20 trials for all of them.

### Limits on these numbers

- **Real-browser tab close isn't covered.** W2 closes tabs through the harness. In the real browser, closing a tab doesn't yet free its session (rebuild item 9).
- **Cuts:** 20 planned runs were cut, including every OLD run at 2 greedy tabs or in the small pool. See "Runs cut from the plan".
- **Fairness of the comparison:** servers have no real tabs, so "close" means cancel, and "visible" is only an arrival position. Chrome exposes no token probabilities, so it has no fidelity figure.


## W1: scheduling (RQ3)

Mean ± sd (min–max) over the n runs; a warm-up run is discarded. Tabs arrive in tab order, 20 ms apart. 64-token cap. Servers have no visible tab: their "visible" tab is just the one that asked last (or first, in the control).

### 8 tabs, visible last

| System | n | Visible TTFT s | Median TTFT s | p95 TTFT s | Relative | Visible gap p95 s | Bg gap p95 s | Stalls >1 s, visible / bg | Jain; tok/s; makespan s |
|---|---|---|---|---|---|---|---|---|---|
| MALABR NEW, partitioned | 5 | 0.55 ± 0.02 (0.53–0.59) | 3.74 ± 0.38 (3.26–4.15) | 5.74 ± 0.30 (5.33–6.01) | 0.15 ± 0.02 (0.13–0.17) | 0.12 ± 0.00 (0.12–0.13) | 0.37 ± 0.01 (0.36–0.38) | 0.0 ± 0.0 (0.0–0.0) / 0.0 ± 0.0 (0.0–0.0) | 0.75 ± 0.01 (0.74–0.77); 32 ± 1 (31–34); 16.1 ± 0.5 (15.2–16.6) |
| MALABR NEW, shared | 5 | 0.49 ± 0.01 (0.48–0.50) | 2.58 ± 0.18 (2.38–2.84) | 4.18 ± 0.18 (4.00–4.47) | 0.19 ± 0.01 (0.17–0.21) | 0.07 ± 0.01 (0.07–0.09) | 0.28 ± 0.01 (0.26–0.29) | 0.0 ± 0.0 (0.0–0.0) / 0.0 ± 0.0 (0.0–0.0) | 0.67 ± 0.01 (0.66–0.68); 35 ± 1 (34–36); 14.6 ± 0.4 (14.1–15.0) |
| MALABR OLD, partitioned | 5 | 2.75 ± 1.40 (1.04–4.07) | 6.15 ± 0.14 (6.00–6.38) | 7.78 ± 0.35 (7.45–8.36) | 0.44 ± 0.22 (0.17–0.64) | 0.12 ± 0.01 (0.11–0.13) | 0.22 ± 0.02 (0.20–0.25) | 0.0 ± 0.0 (0.0–0.0) / 1.4 ± 0.5 (1.0–2.0) | 0.71 ± 0.06 (0.66–0.83); 30 ± 1 (29–31); 17.0 ± 0.3 (16.7–17.4) |
| MALABR OLD, shared | 5 | 1.72 ± 0.51 (0.86–2.19) | 5.73 ± 0.23 (5.42–5.98) | 7.40 ± 0.25 (7.13–7.68) | 0.30 ± 0.08 (0.15–0.37) | 0.08 ± 0.01 (0.07–0.08) | 0.19 ± 0.04 (0.11–0.21) | 0.0 ± 0.0 (0.0–0.0) / 0.0 ± 0.0 (0.0–0.0) | 0.71 ± 0.04 (0.67–0.77); 43 ± 1 (42–44); 11.9 ± 0.2 (11.6–12.2) |
| llama.cpp, partitioned | 5 | 1.31 ± 0.14 (1.20–1.47) | 1.30 ± 0.14 (1.20–1.47) | 1.31 ± 0.14 (1.20–1.47) | 1.00 ± 0.00 (1.00–1.00) | 0.09 ± 0.01 (0.08–0.10) | 0.09 ± 0.01 (0.08–0.10) | 0.0 ± 0.0 (0.0–0.0) / 0.0 ± 0.0 (0.0–0.0) | 1.00 ± 0.00 (1.00–1.00); 79 ± 1 (78–80); 6.5 ± 0.1 (6.4–6.6) |
| llama.cpp, unified | 5 | 1.94 ± 0.04 (1.88–1.98) | 1.94 ± 0.04 (1.88–1.98) | 1.94 ± 0.04 (1.88–1.98) | 1.00 ± 0.00 (1.00–1.00) | 0.08 ± 0.00 (0.08–0.09) | 0.09 ± 0.00 (0.08–0.09) | 0.0 ± 0.0 (0.0–0.0) / 3.0 ± 0.0 (3.0–3.0) | 1.00 ± 0.00 (1.00–1.00); 73 ± 1 (72–75); 7.0 ± 0.1 (6.8–7.1) |
| Ollama | 5 | 1.23 ± 0.04 (1.18–1.27) | 1.23 ± 0.04 (1.18–1.27) | 1.23 ± 0.04 (1.18–1.27) | 1.00 ± 0.00 (1.00–1.00) | 0.09 ± 0.00 (0.08–0.09) | 0.09 ± 0.00 (0.09–0.10) | 0.0 ± 0.0 (0.0–0.0) / 0.0 ± 0.0 (0.0–0.0) | 1.00 ± 0.00 (1.00–1.00); 78 ± 2 (76–81); 6.5 ± 0.2 (6.3–6.7) |

### 4 tabs, visible last

| System | n | Visible TTFT s | Median TTFT s | p95 TTFT s | Relative | Visible gap p95 s | Bg gap p95 s | Stalls >1 s, visible / bg | Jain; tok/s; makespan s |
|---|---|---|---|---|---|---|---|---|---|
| MALABR NEW, partitioned | 5 | 0.55 ± 0.11 (0.47–0.74) | 1.27 ± 0.15 (1.05–1.45) | 2.59 ± 0.29 (2.19–3.01) | 0.43 ± 0.05 (0.39–0.51) | 0.07 ± 0.00 (0.07–0.08) | 0.29 ± 0.07 (0.18–0.33) | 0.0 ± 0.0 (0.0–0.0) / 0.4 ± 0.9 (0.0–2.0) | 0.88 ± 0.02 (0.85–0.89); 32 ± 1 (31–32); 8.0 ± 0.1 (7.9–8.2) |
| MALABR NEW, shared | 5 | 0.51 ± 0.12 (0.44–0.72) | 1.05 ± 0.17 (0.90–1.34) | 1.93 ± 0.23 (1.66–2.20) | 0.49 ± 0.03 (0.44–0.54) | 0.05 ± 0.00 (0.04–0.05) | 0.24 ± 0.01 (0.23–0.25) | 0.0 ± 0.0 (0.0–0.0) / 0.0 ± 0.0 (0.0–0.0) | 0.78 ± 0.02 (0.76–0.80); 34 ± 1 (32–35); 7.6 ± 0.2 (7.2–7.9) |
| MALABR OLD, partitioned | 5 | 0.84 ± 0.17 (0.73–1.15) | 2.12 ± 0.17 (1.98–2.39) | 4.09 ± 0.21 (3.79–4.32) | 0.39 ± 0.05 (0.35–0.48) | 0.07 ± 0.00 (0.06–0.07) | 0.16 ± 0.01 (0.15–0.18) | 0.0 ± 0.0 (0.0–0.0) / 1.0 ± 0.0 (1.0–1.0) | 0.85 ± 0.01 (0.84–0.86); 31 ± 1 (30–32); 8.3 ± 0.2 (8.0–8.6) |
| MALABR OLD, shared | 5 | 0.58 ± 0.09 (0.52–0.74) | 2.25 ± 0.14 (2.09–2.43) | 4.12 ± 0.20 (3.86–4.34) | 0.26 ± 0.04 (0.22–0.32) | 0.05 ± 0.00 (0.04–0.05) | 0.05 ± 0.00 (0.04–0.05) | 0.0 ± 0.0 (0.0–0.0) / 0.0 ± 0.0 (0.0–0.0) | 0.91 ± 0.00 (0.91–0.92); 41 ± 1 (40–43); 6.2 ± 0.2 (6.0–6.4) |
| llama.cpp, partitioned | 5 | 0.72 ± 0.13 (0.62–0.94) | 0.55 ± 0.11 (0.46–0.74) | 0.72 ± 0.13 (0.62–0.94) | 1.32 ± 0.03 (1.27–1.35) | 0.05 ± 0.00 (0.05–0.06) | 0.06 ± 0.01 (0.05–0.07) | 0.0 ± 0.0 (0.0–0.0) / 0.0 ± 0.0 (0.0–0.0) | 1.00 ± 0.00 (1.00–1.00); 67 ± 4 (64–73); 3.8 ± 0.2 (3.5–4.0) |
| llama.cpp, unified | 5 | 0.89 ± 0.03 (0.85–0.92) | 0.64 ± 0.02 (0.62–0.67) | 0.89 ± 0.03 (0.85–0.92) | 1.37 ± 0.01 (1.37–1.38) | 0.05 ± 0.00 (0.05–0.06) | 0.06 ± 0.01 (0.05–0.07) | 0.0 ± 0.0 (0.0–0.0) / 0.0 ± 0.0 (0.0–0.0) | 1.00 ± 0.00 (1.00–1.00); 66 ± 2 (64–68); 3.9 ± 0.1 (3.8–4.0) |
| Ollama | 5 | 0.66 ± 0.08 (0.63–0.80) | 0.49 ± 0.04 (0.47–0.56) | 0.66 ± 0.08 (0.63–0.80) | 1.35 ± 0.05 (1.33–1.44) | 0.05 ± 0.01 (0.05–0.07) | 0.06 ± 0.01 (0.05–0.07) | 0.0 ± 0.0 (0.0–0.0) / 0.0 ± 0.0 (0.0–0.0) | 1.00 ± 0.00 (1.00–1.00); 70 ± 3 (67–73); 3.7 ± 0.1 (3.5–3.8) |

### 8 tabs, visible first (control)

| System | n | Visible TTFT s | Median TTFT s | p95 TTFT s | Relative | Visible gap p95 s | Bg gap p95 s | Stalls >1 s, visible / bg | Jain; tok/s; makespan s |
|---|---|---|---|---|---|---|---|---|---|
| MALABR NEW, partitioned | 5 | 0.25 ± 0.01 (0.24–0.26) | 4.43 ± 0.30 (4.16–4.92) | 6.67 ± 0.41 (6.14–7.29) | 0.06 ± 0.00 (0.05–0.06) | 0.15 ± 0.00 (0.14–0.16) | 0.35 ± 0.01 (0.34–0.37) | 0.0 ± 0.0 (0.0–0.0) / 0.0 ± 0.0 (0.0–0.0) | 0.72 ± 0.01 (0.71–0.74); 30 ± 1 (29–31); 16.9 ± 0.4 (16.5–17.4) |
| MALABR NEW, shared | 5 | 0.25 ± 0.01 (0.23–0.27) | 2.69 ± 0.24 (2.30–2.90) | 4.24 ± 0.11 (4.11–4.39) | 0.09 ± 0.01 (0.09–0.10) | 0.07 ± 0.01 (0.07–0.08) | 0.27 ± 0.01 (0.26–0.28) | 0.0 ± 0.0 (0.0–0.0) / 0.0 ± 0.0 (0.0–0.0) | 0.63 ± 0.01 (0.61–0.64); 34 ± 1 (33–35); 15.0 ± 0.3 (14.5–15.3) |
| MALABR OLD, partitioned | 5 | 0.46 ± 0.13 (0.36–0.62) | 7.41 ± 0.32 (7.17–7.92) | 8.59 ± 0.22 (8.39–8.93) | 0.06 ± 0.01 (0.05–0.08) | 0.16 ± 0.00 (0.16–0.17) | 0.21 ± 0.01 (0.20–0.22) | 0.0 ± 0.0 (0.0–0.0) / 0.0 ± 0.0 (0.0–0.0) | 0.66 ± 0.01 (0.64–0.68); 29 ± 0 (29–30); 17.6 ± 0.2 (17.3–17.9) |
| MALABR OLD, shared | 5 | 0.30 ± 0.02 (0.27–0.33) | 5.93 ± 0.14 (5.76–6.13) | 7.76 ± 0.14 (7.55–7.92) | 0.05 ± 0.00 (0.05–0.06) | 0.08 ± 0.00 (0.08–0.08) | 0.21 ± 0.01 (0.21–0.22) | 0.0 ± 0.0 (0.0–0.0) / 0.0 ± 0.0 (0.0–0.0) | 0.72 ± 0.02 (0.70–0.74); 42 ± 1 (41–43); 12.1 ± 0.2 (11.9–12.4) |
| llama.cpp, partitioned | 5 | 0.49 ± 0.01 (0.49–0.50) | 1.29 ± 0.08 (1.25–1.43) | 1.29 ± 0.08 (1.25–1.43) | 0.38 ± 0.02 (0.34–0.40) | 0.09 ± 0.01 (0.09–0.10) | 0.09 ± 0.01 (0.09–0.10) | 0.0 ± 0.0 (0.0–0.0) / 0.0 ± 0.0 (0.0–0.0) | 1.00 ± 0.00 (1.00–1.00); 78 ± 0 (77–78); 6.6 ± 0.0 (6.5–6.7) |
| llama.cpp, unified | 5 | 0.69 ± 0.11 (0.62–0.89) | 1.69 ± 0.19 (1.37–1.83) | 1.79 ± 0.05 (1.72–1.85) | 0.42 ± 0.13 (0.36–0.65) | 0.10 ± 0.01 (0.09–0.12) | 0.10 ± 0.01 (0.09–0.12) | 0.8 ± 0.4 (0.0–1.0) / 1.6 ± 0.9 (0.0–2.0) | 1.00 ± 0.00 (1.00–1.00); 72 ± 1 (71–74); 7.1 ± 0.1 (6.9–7.2) |
| Ollama | 5 | 0.48 ± 0.01 (0.48–0.51) | 1.28 ± 0.13 (1.21–1.51) | 1.28 ± 0.13 (1.21–1.52) | 0.38 ± 0.03 (0.34–0.39) | 0.10 ± 0.01 (0.08–0.12) | 0.10 ± 0.01 (0.08–0.12) | 0.2 ± 0.4 (0.0–1.0) / 0.2 ± 0.4 (0.0–1.0) | 1.00 ± 0.00 (1.00–1.00); 79 ± 1 (78–80); 6.5 ± 0.1 (6.4–6.6) |

## W2: closing tabs (RQ4)

8 tabs, cap 96; tabs 1, 3, 5, 7 closed once all four have a first token. Each system closes at a different moment (last column), so speed-ups compare across systems only roughly. Wasted tokens: MALABR counts frames after the close; llama.cpp, its token counter minus delivered; Ollama exposes neither.

| System | n | Survivors finish, control s | Survivors finish, close s | Speed-up (control ÷ close) | Tokens after close | Closed at s |
|---|---|---|---|---|---|---|
| MALABR NEW, partitioned | 5 | 23.58 ± 0.63 (22.97–24.35) | 14.45 ± 0.61 (13.69–15.07) | 1.64 ± 0.10 (1.54–1.77) | 0.0 ± 0.0 (0.0–0.0) | 7.24 ± 0.52 (6.77–7.82) |
| MALABR NEW, shared | 5 | 20.92 ± 0.58 (20.13–21.67) | 12.02 ± 0.32 (11.56–12.38) | 1.74 ± 0.07 (1.68–1.82) | 0.0 ± 0.0 (0.0–0.0) | 4.61 ± 0.18 (4.39–4.82) |
| MALABR OLD, partitioned | 5 | 24.17 ± 0.62 (23.63–24.84) | 14.56 ± 0.61 (14.15–15.61) | 1.66 ± 0.04 (1.59–1.70) | 0.0 ± 0.0 (0.0–0.0) | 7.59 ± 0.15 (7.46–7.84) |
| MALABR OLD, shared | 5 | 17.58 ± 1.51 (16.50–20.10) | 12.21 ± 0.65 (11.72–13.32) | 1.45 ± 0.18 (1.25–1.72) | 0.0 ± 0.0 (0.0–0.0) | 7.71 ± 0.43 (7.37–8.45) |
| llama.cpp, partitioned | 5 | 9.37 ± 0.14 (9.27–9.59) | 11.09 ± 0.23 (10.73–11.32) | 0.84 ± 0.01 (0.84–0.86) | 4.2 ± 0.4 (4.0–5.0) | 1.47 ± 0.06 (1.38–1.54) |
| llama.cpp, unified | 5 | 9.36 ± 0.18 (9.06–9.47) | 6.50 ± 0.11 (6.39–6.63) | 1.44 ± 0.03 (1.41–1.48) | 4.0 ± 0.0 (4.0–4.0) | 1.95 ± 0.04 (1.91–2.02) |
| Ollama | 5 | 9.40 ± 0.26 (9.11–9.73) | 11.23 ± 0.28 (10.97–11.55) | 0.84 ± 0.02 (0.81–0.87) | – | 1.51 ± 0.05 (1.45–1.58) |

## W3: greedy tabs (RQ2)

3 small tabs chat (6 turns, 48-token cap, 1 s apart) while greedy tabs load pages, with a 120 s head start or until each greedy tab is refused. Pause 2 s after a refusal. Greedy counts are totals over the n runs. File names give tag, system, mode, greedy tabs, pool and guard.

| Run | n | n_ctx | Page tokens | Small TTFT median s | Small TTFT p95 s | Small turn s | Small errors | Failed rounds | Tokens compacted | Greedy pages (all runs) |
|---|---|---|---|---|---|---|---|---|---|---|
| llamacpp_partitioned_W3_g0 | 5 | 16384 | 900 | 0.93 ± 0.01 (0.91–0.95) | 1.12 ± 0.02 (1.11–1.16) | 1.73 ± 0.04 (1.69–1.79) | 0.0 ± 0.0 (0.0–0.0) | n/a | n/a | none |
| llamacpp_partitioned_W3_g1 | 5 | 16384 | 900 | 0.80 ± 0.10 (0.72–0.96) | 1.18 ± 0.07 (1.06–1.23) | 1.75 ± 0.17 (1.45–1.86) | 0.0 ± 0.0 (0.0–0.0) | n/a | n/a | tab 10: 10 ok, 37 refused, 0 other |
| llamacpp_partitioned_W3_g2 | 5 | 16384 | 900 | 1.04 ± 0.11 (0.87–1.15) | 1.38 ± 0.05 (1.31–1.45) | 2.07 ± 0.05 (2.02–2.15) | 0.0 ± 0.0 (0.0–0.0) | n/a | n/a | tab 10: 10 ok, 40 refused, 0 other; tab 11: 10 ok, 40 refused, 0 other |
| llamacpp_partitioned_W3_g2_ctx4096 | 5 | 4096 | 900 | 0.58 ± 0.07 (0.46–0.63) | 0.91 ± 0.04 (0.86–0.95) | 1.48 ± 0.04 (1.43–1.53) | 0.0 ± 0.0 (0.0–0.0) | n/a | n/a | tab 10: 0 ok, 35 refused, 0 other; tab 11: 0 ok, 35 refused, 0 other |
| llamacpp_shared_W3_g0 | 5 | 16384 | 3000 | 0.89 ± 0.34 (0.68–1.50) | 1.37 ± 0.58 (1.05–2.41) | 1.85 ± 0.30 (1.67–2.37) | 0.0 ± 0.0 (0.0–0.0) | n/a | n/a | none |
| llamacpp_shared_W3_g1 | 5 | 16384 | 3000 | 3.17 ± 0.32 (2.76–3.64) | 89.00 ± 0.51 (88.44–89.83) | 4.01 ± 0.34 (3.57–4.48) | 0.0 ± 0.0 (0.0–0.0) | n/a | n/a | tab 10: 25 ok, 38 refused, 0 other |
| llamacpp_shared_W3_g2 | 5 | 16384 | 3000 | 5.21 ± 4.07 (2.18–10.79) | 66.58 ± 12.48 (52.21–86.54) | 93.57 ± 9.28 (77.71–101.59) | 8.8 ± 0.4 (8.0–9.0) | n/a | n/a | tab 10: 12 ok, 12 refused, 0 other; tab 11: 14 ok, 12 refused, 0 other |
| llamacpp_shared_W3_g2_ctx4096 | 5 | 4096 | 900 | 1.12 ± 0.20 (0.76–1.23) | 5.17 ± 2.19 (1.25–6.26) | 0.92 ± 0.02 (0.90–0.94) | 15.0 ± 0.0 (15.0–15.0) | n/a | n/a | tab 10: 10 ok, 30 refused, 0 other; tab 11: 10 ok, 30 refused, 0 other |
| new_malabr_partitioned_W3_g0 | 5 | 16384 | 900 | 0.58 ± 0.04 (0.54–0.63) | 1.23 ± 0.04 (1.17–1.27) | 1.06 ± 0.03 (1.03–1.11) | 0.0 ± 0.0 (0.0–0.0) | 0 ± 0 (0–0) | 0 ± 0 (0–0) | none |
| new_malabr_partitioned_W3_g1 | 5 | 16384 | 900 | 0.67 ± 0.06 (0.60–0.76) | 1.48 ± 0.14 (1.38–1.69) | 1.29 ± 0.12 (1.10–1.41) | 0.0 ± 0.0 (0.0–0.0) | 0 ± 0 (0–0) | 0 ± 0 (0–0) | tab 10: 10 ok, 43 refused, 0 other |
| new_malabr_partitioned_W3_g2 | 5 | 16384 | 900 | 0.84 ± 0.12 (0.73–1.04) | 1.81 ± 0.67 (1.45–3.00) | 1.49 ± 0.15 (1.38–1.75) | 0.0 ± 0.0 (0.0–0.0) | 0 ± 0 (0–0) | 0 ± 0 (0–0) | tab 10: 10 ok, 108 refused, 0 other; tab 11: 10 ok, 46 refused, 0 other |
| new_malabr_partitioned_W3_g2_ctx4096 | 5 | 4096 | 900 | 0.62 ± 0.08 (0.50–0.71) | 117.43 ± 259.20 (1.25–581.11) | 1.25 ± 0.13 (1.07–1.38) | 0.4 ± 0.9 (0.0–2.0) | 0 ± 0 (0–0) | 0 ± 0 (0–0) | tab 10: 0 ok, 42 refused, 0 other; tab 11: 0 ok, 42 refused, 0 other |
| new_malabr_shared_W3_g0 | 5 | 16384 | 3000 | 0.56 ± 0.04 (0.51–0.61) | 1.05 ± 0.05 (1.01–1.12) | 1.08 ± 0.09 (0.95–1.17) | 0.0 ± 0.0 (0.0–0.0) | 0 ± 0 (0–0) | 0 ± 0 (0–0) | none |
| new_malabr_shared_W3_g1 | 5 | 16384 | 3000 | 16.70 ± 1.78 (13.56–17.88) | 21.15 ± 0.24 (20.86–21.42) | 22.29 ± 1.00 (21.30–23.48) | 0.0 ± 0.0 (0.0–0.0) | 0 ± 0 (0–0) | 0 ± 0 (0–0) | tab 10: 5 ok, 0 refused, 0 other |
| new_malabr_shared_W3_g2 | 5 | 16384 | 3000 | 22.13 ± 0.34 (21.88–22.73) | 23.82 ± 0.54 (23.30–24.59) | 29.67 ± 0.33 (29.36–30.20) | 0.0 ± 0.0 (0.0–0.0) | 0 ± 0 (0–0) | 0 ± 0 (0–0) | tab 10: 5 ok, 0 refused, 0 other; tab 11: 0 ok, 0 refused, 0 other |
| new_malabr_shared_W3_g2_ctx4096 | 5 | 4096 | 900 | – | – | – | 18.0 ± 0.0 (18.0–18.0) | 0 ± 0 (0–0) | 0 ± 0 (0–0) | tab 10: 10 ok, 365 refused, 0 other; tab 11: 10 ok, 316 refused, 0 other |
| new_malabr_shared_W3_g2_guardoff | 5 | 16384 | 3000 | 21.38 ± 0.25 (21.06–21.76) | 23.66 ± 0.74 (22.62–24.59) | 29.55 ± 1.43 (28.30–31.99) | 0.0 ± 0.0 (0.0–0.0) | 0 ± 0 (0–0) | 0 ± 0 (0–0) | tab 10: 4 ok, 0 refused, 0 other; tab 11: 1 ok, 0 refused, 0 other |
| old_malabr_partitioned_W3_g0 | 5 | 16384 | 900 | 0.66 ± 0.03 (0.62–0.71) | 1.55 ± 0.20 (1.34–1.75) | 1.13 ± 0.03 (1.09–1.17) | 0.0 ± 0.0 (0.0–0.0) | 0 ± 0 (0–0) | 0 ± 0 (0–0) | none |
| old_malabr_partitioned_W3_g1 | 5 | 16384 | 900 | 14.53 ± 0.82 (13.33–15.23) | 25.99 ± 0.52 (25.26–26.59) | 15.03 ± 0.81 (13.91–15.74) | 12.0 ± 0.0 (12.0–12.0) | 38785 ± 130 (38616–38930) | 0 ± 0 (0–0) | tab 10: 10 ok, 0 refused, 5 other |
| old_malabr_shared_W3_g0 | 5 | 16384 | 3000 | 0.61 ± 0.06 (0.54–0.67) | 1.50 ± 0.25 (1.30–1.91) | 1.04 ± 0.06 (0.96–1.10) | 0.0 ± 0.0 (0.0–0.0) | 0 ± 0 (0–0) | 0 ± 0 (0–0) | none |
| old_malabr_shared_W3_g1 | 5 | 16384 | 3000 | 4.61 ± 0.13 (4.41–4.75) | 5.51 ± 0.13 (5.36–5.69) | 5.31 ± 0.05 (5.26–5.39) | 0.0 ± 0.0 (0.0–0.0) | 0 ± 0 (0–0) | 0 ± 0 (0–0) | tab 10: 5 ok, 0 refused, 0 other |
| ollama_W3_g0 | 5 | 16384 | 900 | 0.94 ± 0.03 (0.92–0.99) | 1.12 ± 0.02 (1.10–1.15) | 1.74 ± 0.01 (1.72–1.75) | 0.0 ± 0.0 (0.0–0.0) | n/a | n/a | none |
| ollama_W3_g1 | 5 | 16384 | 900 | 10.17 ± 2.65 (6.59–13.26) | 15.24 ± 0.38 (14.91–15.83) | 16.45 ± 0.10 (16.28–16.55) | 0.0 ± 0.0 (0.0–0.0) | n/a | n/a | tab 10: 79 ok, 0 refused, 0 other |
| ollama_W3_g2 | 5 | 16384 | 900 | 6.71 ± 0.82 (5.38–7.57) | 26.99 ± 10.89 (8.53–37.15) | 32.51 ± 1.99 (29.22–34.20) | 0.0 ± 0.0 (0.0–0.0) | n/a | n/a | tab 10: 60 ok, 0 refused, 0 other; tab 11: 60 ok, 0 refused, 0 other |
| ollama_W3_g2_ctx4096 | 5 | 4096 | 900 | 0.78 ± 0.04 (0.72–0.82) | 1.18 ± 0.03 (1.14–1.21) | 1.81 ± 0.05 (1.74–1.86) | 0.0 ± 0.0 (0.0–0.0) | n/a | n/a | tab 10: 0 ok, 40 refused, 0 other; tab 11: 0 ok, 40 refused, 0 other |

## W4: premise at overflow (RQ2)

Fresh secret in the first turn, filled to 1.2× the session limit, then the direct question. A "recalled" with failed fill turns doesn't show the secret survived an overflow: the failed turns never entered the context.
n = 20, except the two where one trial fills 8–16k tokens: n = 8.

‡ OLD shared: the run was lost in the 2026-11-04 10:01 drive drop. These are the 3 trials in console.txt, n = 3, with no result file.

| System | n | Limit per session | Control recalled | Overflow recalled | Failures | Compactions | Time per trial |
|---|---|---|---|---|---|---|---|
| MALABR NEW, partitioned | 20 | 2,048 | 20/20 | 20/20 | 0 of 20 had a failed turn | 4.0 ± 0.0 (4.0–4.0) | 71 ± 1 (69–73) s |
| MALABR NEW, shared | 8 | 8,192 | 8/8 | 8/8 | 1 of 8 had a failed turn: timeout | 5.0 ± 0.0 (5.0–5.0) | 1920 ± 5 (1909–1925) s |
| MALABR OLD, partitioned | 20 | 2,048 | 20/20 | 20/20 | 0 of 20 had a failed turn | 2.0 ± 0.0 (2.0–2.0) | 80 ± 1 (78–80) s |
| MALABR OLD, shared ‡ | 3 | 8,192 | 3/3 | 3/3 | 3 of 3 had 3 failed fill turns | – | 21–31 min per trial |
| llama.cpp, partitioned | 20 | 2,048 | 20/20 | 18/20 | 20 of 20 had a failed turn: HTTP 400: {"error":{"code":400,"message":"request (2500 tokens) exceed | 0.0 ± 0.0 (0.0–0.0) | 15 ± 0 (13–16) s |
| llama.cpp, unified | 8 | 16,384 | 8/8 | 8/8 | 8 of 8 had a failed turn: HTTP 400: {"error":{"code":400,"message":"request (16763 tokens) excee | 0.0 ± 0.0 (0.0–0.0) | 393 ± 20 (381–442) s |
| Ollama | 20 | 2,048 | 20/20 | 0/20 | 0 of 20 had a failed turn | 0.0 ± 0.0 (0.0–0.0) | 18 ± 8 (16–53) s |

## W5: memory (RQ2)

RSS in MB of the server process (Ollama: server + runner), restarted per run, with each session holding a page of about 1,400 tokens. MALABR is the real `app.py`.

| System | n | 0 sessions | 1 sessions | 2 sessions | 4 sessions | 6 sessions | 8 sessions | Growth 0→8 | Limit per session |
|---|---|---|---|---|---|---|---|---|---|
| MALABR NEW, partitioned | 5 | 2530 | 2536 | 2538 | 2542 | 2546 | 2549 | 19 ± 0 (19–20) | 2,048 |
| MALABR NEW, shared | 5 | 2530 | 2536 | 2538 | 2542 | 2546 | 2550 | 20 ± 0 (20–20) | 8,192 |
| MALABR OLD, partitioned | 5 | 2524 | 2530 | 2532 | 2536 | 2540 | 2543 | 20 ± 0 (19–20) | 2,048 |
| MALABR OLD, shared | 5 | 2524 | 2530 | 2532 | 2536 | 2540 | 2544 | 20 ± 0 (20–20) | 8,192 |
| llama.cpp, partitioned | 5 | 2474 | 2504 | 2655 | 2955 | 3256 | 3557 | 1083 ± 0 (1083–1083) | 2,048 |
| llama.cpp, unified | 5 | 2474 | 2504 | 2655 | 2955 | 3255 | 3554 | 1080 ± 0 (1080–1080) | 16,384 |
| Ollama | 5 | 2519 | 2550 | 2700 | 2996 | 3297 | 3597 | 1078 ± 9 (1062–1083) | 2,048 |

## R1: isolation (RQ1)

Fresh secret per trial; the holder asks (positive control), then another session asks. OLD wasn't in the R1 plan: its isolation evidence is the §12 suite.

| System | n | Holder recalled | Other session recalled |
|---|---|---|---|
| MALABR NEW, partitioned | 20 | 20/20 | 0/20 |
| MALABR NEW, shared | 20 | 20/20 | 0/20 |
| llama.cpp, partitioned | 20 | 19/20 | 0/20 |
| llama.cpp, unified | 20 | 19/20 | 0/20 |
| Ollama | 20 | 19/20 | 0/20 |

## Chrome Prompt API (reference only)

A different model (Gemini Nano, CPU variant), so absolute numbers aren't comparable. Only each system's behaviour against itself is.

### Relative score (run 0 of 6 discarded as warm-up)

| Workload | Set | n | Visible TTFT s | Relative | Relative, run by run |
|---|---|---|---|---|---|
| 8 tabs | first set (no nonce; JSON save crashed) | 5 | 3.24 ± 3.86 (0.78–9.64) | 0.20 ± 0.24 (0.05–0.60) | 0.60, 0.27, 0.05, 0.05, 0.05 |
| 8 tabs | rerun (nonce, script fixed) | 5 | 4.65 ± 4.16 (1.85–11.30) | 0.25 ± 0.23 (0.10–0.61) | 0.10, 0.61, 0.10, 0.35, 0.11 |
| 4 tabs | first set (no nonce; JSON save crashed) | 5 | 1.89 ± 0.48 (1.67–2.75) | 0.24 ± 0.06 (0.21–0.34) | 0.34, 0.21, 0.21, 0.21, 0.21 |
| 4 tabs | rerun (nonce, script fixed) | 0 | not run | – |  |
| 8 tabs, visible 2 s late | first set (no nonce; JSON save crashed) | 5 | 65.60 ± 33.88 (10.65–99.81) | 0.79 ± 0.14 (0.55–0.88) | 0.85, 0.86, 0.88, 0.83, 0.55 |
| 8 tabs, visible 2 s late | rerun (nonce, script fixed) | 0 | not run | – |  |

### Memory (W5 equivalent)

RSS of the Chrome process tree in MB, each session filled to about 8,208 of its 9,216-token window. † Growth figures from these runs are not a headline.

| Run | Base | 1 sessions | 2 sessions | 3 sessions | 4 sessions | 5 sessions | 6 sessions | Session 6 usage |
|---|---|---|---|---|---|---|---|---|
| rss_1 † under memory pressure | 3082 | 4614 | 5896 | 7175 | 8461 | 9736 | 1124 | 0 |
| rss_2 † under memory pressure | 3071 | 4595 | 5875 | 7129 | 6986 | 7827 | 303 | 0 |
| rss_3 | 3084 | 4615 | 5896 | 7033 | 8248 | 9527 | 832 | 0 |
| rss_4 | 3067 | 4617 | 5882 | 7163 | 8444 | 9723 | 1121 | 0 |
| rss_5 | not run |  |  |  |  |  |  |  |

### Closing tabs (W2 equivalent)

```
close half the tabs      run 5: 0.95x
control (close nothing)  median speed-up 0.96x (n=5, min 0.95, max 0.97)
close half the tabs      median speed-up 0.95x (n=5, min 0.94, max 0.98)
Closing half the tabs changes the survivors' rate by 0.98x relative to the control.
Near 1.0 means closing a tab did NOT return its compute.
```

### Greedy neighbour, context (W3, W4 equivalent)

```
control (all normal)   median 4.74s (n=4)
one greedy neighbour   median 5.12s (n=4)
A greedy neighbour changes everyone else's TTFT by 1.08x
```

### Isolation (R1 equivalent)

```
planted
[PASS] positive control: canary IS recallable in its own session   A said: 'MAGENTA-181578'
[PASS] control: the session retains ANY prior turn at all   A said: 'You asked me to remember the vault passphrase.'
[PASS] cross-ORIGIN: origin B cannot reach origin A's canary   B said: 'I am programmed to be a safe and helpful AI assistant. I **cannot** re'
[PASS] same-origin, different tab: cannot reach the other's canary   A2 said: 'I am an AI and do not retain sensitive information, including vault pa'
[PASS] after destroy(): a new session in the SAME page sees nothing   A-new said: 'I am programmed to be a helpful and harmless AI assistant. Therefor
5 checks, 5 passed
```

## Runs with caveats

- `[2026-11-04 10:04:08] FAILED rc=143: --system malabr --tag old --mode shared --workload W4 --trials 8`
- `[2026-11-04 10:04:08] NOT COMPLETED (drive dropped 10:01, killed, no JSON; trials 0-2 in console.txt): --system malabr --tag old --mode shared --workload W4 --trials 8`
- `[2026-11-05 02:38:53] FAILED: calibrate shared 4096 | after 2 tries, rc=1`

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

## Real browser (fixed build)

Patched Chromium 127 built on 2026-08-28, the real panel and real `app.py`, driven over CDP, with n_ctx 16,384, 8 slots and 3 threads. These are the first numbers in this report that use the real browser. Times are measured in the panel, from clicking send to the first token. Brave stayed open during the runs, and page text is attached to each tab's first question.

### Hand-run tests

| Test | Result | Note |
|---|---|---|
| `test_panel_status.py` | 8/8 | |
| `test_transcript_keys.py` | 14/14 | |
| `test_page_context_patho.py` | 15/15 | |
| `test_page_context.py` | 9/9 | Second try. The first crashed reading the page before its body existed; no change between tries |
| `test_control_channel.py` | 4/5 | Second try, with quick calibration. The first gave up after 240 s during calibration. The one failure is the known tab-close check (2b, rebuild item 9) |

### Time to first token, n = 5, 1 warm-up round discarded

Mean ± sd. All 100 tab turns per mode were served, with no timeouts.

| Scenario | Mode | Visible tab s | Background median s | All-tab p95 s | Round total s |
|---|---|---|---|---|---|
| 8 tabs, visible last | Partitioned | 0.61 ± 0.07 | 3.73 ± 0.27 | 5.49 ± 0.37 | 6.17 ± 0.37 |
| | Shared | 0.48 ± 0.01 | 2.84 ± 0.05 | 4.20 ± 0.09 | 4.57 ± 0.02 |
| 4 tabs, visible last | Partitioned | 0.45 ± 0.02 | 1.63 ± 0.06 | 2.49 ± 0.07 | 2.95 ± 0.29 |
| | Shared | 0.44 ± 0.02 | 1.43 ± 0.05 | 2.06 ± 0.05 | 2.64 ± 0.24 |
| 8 tabs, visible first (control) | Partitioned | 0.49 ± 0.05 | 3.83 ± 0.17 | 5.59 ± 0.28 | 6.18 ± 0.38 |
| | Shared | 0.46 ± 0.02 | 2.82 ± 0.10 | 4.17 ± 0.12 | 4.78 ± 0.30 |

At 8 tabs, the real browser's visible-tab TTFT is 6–8× lower than its background median, and these times are close to the harness's for the same scenario. Replies aren't capped to a fixed token count here, so round totals are rougher than the harness's makespan. There's no throughput figure.

### Focus switch

From activating a tab to the server logging `control FOREGROUND`, while 8 tabs generate: **0.02 s** in both modes (max 0.03 s, n = 9 of 10; the first activation was already the active tab, so it produced no event). It shows that the event reaches the server, not when tokens change.

### Closing generating tabs

8 tabs generate, and 4 are closed once they all have a first token. Then a new tab asks. n = 5 per mode, with a fresh browser and server per run.

| Measure | Partitioned | Shared |
|---|---|---|
| New tab served after the closes | **0 of 5** | **0 of 5** |
| Closed at, s | 8.68 ± 0.28 | 5.89 ± 0.24 |

**Closing a tab in the real browser doesn't free its session**, even when it was closing mid-generation, and with 4 survivors and 4 closed tabs holding all 8 slots, a new tab can't get one. That's rebuild item 9. This extends the earlier measurement, which was of idle tabs.

**No speed-up figure is reported.** My control arm timed all 8 tabs rather than the same 4 survivors, and short replies were nearly done by the time of the close, so the finish times aren't comparable. W2's speed-up remains a server-harness result, and it's still not demonstrated in a real browser.

