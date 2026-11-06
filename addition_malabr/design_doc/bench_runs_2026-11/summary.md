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
