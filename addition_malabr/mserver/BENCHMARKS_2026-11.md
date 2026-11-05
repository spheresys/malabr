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
| llamacpp_shared_W3_g0 | 5 | 16384 | 3000 | 0.89 ± 0.34 (0.68–1.50) | 1.37 ± 0.58 (1.05–2.41) | 1.85 ± 0.30 (1.67–2.37) | 0.0 ± 0.0 (0.0–0.0) | n/a | n/a | none |
| llamacpp_shared_W3_g1 | 5 | 16384 | 3000 | 3.17 ± 0.32 (2.76–3.64) | 89.00 ± 0.51 (88.44–89.83) | 4.01 ± 0.34 (3.57–4.48) | 0.0 ± 0.0 (0.0–0.0) | n/a | n/a | tab 10: 25 ok, 38 refused, 0 other |
| llamacpp_shared_W3_g2 | 5 | 16384 | 3000 | 5.21 ± 4.07 (2.18–10.79) | 66.58 ± 12.48 (52.21–86.54) | 93.57 ± 9.28 (77.71–101.59) | 8.8 ± 0.4 (8.0–9.0) | n/a | n/a | tab 10: 12 ok, 12 refused, 0 other; tab 11: 14 ok, 12 refused, 0 other |
| new_malabr_partitioned_W3_g0 | 5 | 16384 | 900 | 0.58 ± 0.04 (0.54–0.63) | 1.23 ± 0.04 (1.17–1.27) | 1.06 ± 0.03 (1.03–1.11) | 0.0 ± 0.0 (0.0–0.0) | 0 ± 0 (0–0) | 0 ± 0 (0–0) | none |
| new_malabr_partitioned_W3_g1 | 5 | 16384 | 900 | 0.67 ± 0.06 (0.60–0.76) | 1.48 ± 0.14 (1.38–1.69) | 1.29 ± 0.12 (1.10–1.41) | 0.0 ± 0.0 (0.0–0.0) | 0 ± 0 (0–0) | 0 ± 0 (0–0) | tab 10: 10 ok, 43 refused, 0 other |
| new_malabr_partitioned_W3_g2 | 5 | 16384 | 900 | 0.84 ± 0.12 (0.73–1.04) | 1.81 ± 0.67 (1.45–3.00) | 1.49 ± 0.15 (1.38–1.75) | 0.0 ± 0.0 (0.0–0.0) | 0 ± 0 (0–0) | 0 ± 0 (0–0) | tab 10: 10 ok, 108 refused, 0 other; tab 11: 10 ok, 46 refused, 0 other |
| new_malabr_shared_W3_g0 | 5 | 16384 | 3000 | 0.56 ± 0.04 (0.51–0.61) | 1.05 ± 0.05 (1.01–1.12) | 1.08 ± 0.09 (0.95–1.17) | 0.0 ± 0.0 (0.0–0.0) | 0 ± 0 (0–0) | 0 ± 0 (0–0) | none |
| new_malabr_shared_W3_g1 | 5 | 16384 | 3000 | 16.70 ± 1.78 (13.56–17.88) | 21.15 ± 0.24 (20.86–21.42) | 22.29 ± 1.00 (21.30–23.48) | 0.0 ± 0.0 (0.0–0.0) | 0 ± 0 (0–0) | 0 ± 0 (0–0) | tab 10: 5 ok, 0 refused, 0 other |
| new_malabr_shared_W3_g2 | 5 | 16384 | 3000 | 22.13 ± 0.34 (21.88–22.73) | 23.82 ± 0.54 (23.30–24.59) | 29.67 ± 0.33 (29.36–30.20) | 0.0 ± 0.0 (0.0–0.0) | 0 ± 0 (0–0) | 0 ± 0 (0–0) | tab 10: 5 ok, 0 refused, 0 other; tab 11: 0 ok, 0 refused, 0 other |
| old_malabr_partitioned_W3_g0 | 5 | 16384 | 900 | 0.66 ± 0.03 (0.62–0.71) | 1.55 ± 0.20 (1.34–1.75) | 1.13 ± 0.03 (1.09–1.17) | 0.0 ± 0.0 (0.0–0.0) | 0 ± 0 (0–0) | 0 ± 0 (0–0) | none |
| old_malabr_partitioned_W3_g1 | 5 | 16384 | 900 | 14.53 ± 0.82 (13.33–15.23) | 25.99 ± 0.52 (25.26–26.59) | 15.03 ± 0.81 (13.91–15.74) | 12.0 ± 0.0 (12.0–12.0) | 38785 ± 130 (38616–38930) | 0 ± 0 (0–0) | tab 10: 10 ok, 0 refused, 5 other |
| old_malabr_shared_W3_g0 | 5 | 16384 | 3000 | 0.61 ± 0.06 (0.54–0.67) | 1.50 ± 0.25 (1.30–1.91) | 1.04 ± 0.06 (0.96–1.10) | 0.0 ± 0.0 (0.0–0.0) | 0 ± 0 (0–0) | 0 ± 0 (0–0) | none |
| old_malabr_shared_W3_g1 | 5 | 16384 | 3000 | 4.61 ± 0.13 (4.41–4.75) | 5.51 ± 0.13 (5.36–5.69) | 5.31 ± 0.05 (5.26–5.39) | 0.0 ± 0.0 (0.0–0.0) | 0 ± 0 (0–0) | 0 ± 0 (0–0) | tab 10: 5 ok, 0 refused, 0 other |
| ollama_W3_g0 | 5 | 16384 | 900 | 0.94 ± 0.03 (0.92–0.99) | 1.12 ± 0.02 (1.10–1.15) | 1.74 ± 0.01 (1.72–1.75) | 0.0 ± 0.0 (0.0–0.0) | n/a | n/a | none |
| ollama_W3_g1 | 5 | 16384 | 900 | 10.17 ± 2.65 (6.59–13.26) | 15.24 ± 0.38 (14.91–15.83) | 16.45 ± 0.10 (16.28–16.55) | 0.0 ± 0.0 (0.0–0.0) | n/a | n/a | tab 10: 79 ok, 0 refused, 0 other |
| ollama_W3_g2 | 5 | 16384 | 900 | 6.71 ± 0.82 (5.38–7.57) | 26.99 ± 10.89 (8.53–37.15) | 32.51 ± 1.99 (29.22–34.20) | 0.0 ± 0.0 (0.0–0.0) | n/a | n/a | tab 10: 60 ok, 0 refused, 0 other; tab 11: 60 ok, 0 refused, 0 other |

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
| 8 tabs | rerun (nonce, script fixed) | 0 | not run | – |  |
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
| rss_3 | not run |  |  |  |  |  |  |  |
| rss_4 | not run |  |  |  |  |  |  |  |
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

