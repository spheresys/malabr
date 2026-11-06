# Campaign report, 2026-11-05 to 2026-11-06

The campaign finished at 04:56 on 2026-11-06 on the frozen build, tag `design-freeze-2026-11` (`35e90668b6d4`). The numbers are in `BENCHMARKS_2026-11.md`. Nothing was pushed.

## Result

- **Trimmed plan:** all 21 approved runs finished. 20 runs were cut with your approval.
- **Biggest gain:** in W3 partitioned at 1 greedy tab, small-tab TTFT fell from 14.53 s to 0.67 s.
- **Biggest problems, both in shared mode:**
  - At 1 greedy tab, small-tab TTFT regressed from 4.61 s to 16.70 s.
  - In the small pool, every small-tab turn was refused.
- **Design:** frozen as planned. No MALABR code changed after the freeze.

## What passed

- **Suite on the frozen build:** partitioned 89/90 (only t48 fails), shared 90/90, 19 browser tests skipped.
- **Calibration:** 3 of 4 configurations measured.
- **MALABR NEW:** W1, W2, W4, W5 and R1 in both modes.
- **W3:** all kept runs, at 5 of 5 runs each.
- **Chrome:** `rss_3`, `rss_4` and 6 relative runs at 8 tabs.
- **Reports and integrity:** see the checks at the end of this file.

## What failed

| Item | What happened | Effect |
|---|---|---|
| Calibration, shared, n_ctx 4,096 | `decode: failed to find a memory slot`, on both tries | NEW shared small-pool run used the fallback curve |
| NEW shared, small pool | The run completed, but all small-tab turns were refused (`server is at capacity`) | No small-tab TTFT for this configuration |
| `relative2_8tabs_4` | rc=2 after 5 s on try 1; passed on try 2 | None; the error text wasn't kept |
| `ollama W3 greedy 2` | Stopped by the drive check at 12:39 | Redone in full; no data from the partial run |
| OLD shared W4 (2026-11-04) | Lost in a drive drop; the rerun was dropped by your decision | Reported as n = 3 from `console.txt` |

## What was skipped, and why

- **20 runs cut by your decision on 2026-11-05.** All are listed with reasons in `BENCHMARKS_2026-11.md`, under "Runs cut from the plan".
- **Page segments, joint batch cost, compute buffer in the memory budget, startup log fields, prefill changes at depth:** left out of the freeze as you instructed, and listed as limitations.
- **W2 survivor-slowdown diagnostic:** I mentioned doing one, but it wasn't in your plan and wasn't run. The slowdown is reported with its cause not established.
- **Chromium rebuild:** not done. Real-browser tab close still doesn't free the session (item 9).

## Decisions made on your behalf

1. NEW shared W4 ran 8 trials, matching the baselines' shared-mode n rather than 20, and ran last in its priority group, because one trial takes about 30 minutes.
2. OLD kept its 2026-11-04 calibrations, and NEW got fresh ones. The machine and build key lives in the calibration file's path rather than in code, so no code changed.
3. I kept the first Chrome relative files and saved the rerun as `relative2_*`.
4. After the freeze, I changed only test and report code: the Chrome relative script fix and the report generator. The generator now also includes `summary.md`.
5. The OLD tree was rebuilt twice after reboots, and the files it uses were checked against `610c233`.
6. After swap filled, I marked runs by the existing rule (over 80% at start). I also added a written caveat for the W3 runs at 1 greedy tab, where swap rose during the runs. You cleared swap by hand before NEW W3 at 2 greedy tabs.
7. Brave and VS Code stayed open during runs. Each run's load and available memory are recorded in `progress.log`.
8. I called t48 "appears deterministic" rather than "flaky", because it failed the same way each time I saw it.
9. The notes update went into `RQ2_NOTES.md`. There are no separate RQ3 or RQ4 notes files to update, and I didn't create any; those numbers are in `BENCHMARKS_2026-11.md`.

## Interruptions

| When | What | Handling |
|---|---|---|
| 2026-11-05 12:39–14:20 | USB drive dropped | Run stopped by the drive check; drive recovered, and 2 finished results survived |
| 2026-11-05 about 15:10 | Paused at your request to clear swap | You cleared it; the campaign resumed at 15:18 |
| 2026-11-05 16:28 | Paused at your request | Machine later rebooted; setup restored and resumed at 03:20 on 2026-11-06 |

## What's left

**Runs:** nothing from the trimmed plan. Running the 20 cut runs would take roughly 3–5 hours: most of it the OLD runs at 2 greedy tabs and in the small pool (about 2–4 h), plus `rss_5` and the 12 Chrome relative runs (under an hour).

**Open work, needing your decision:**
- W3 shared-mode diagnosis and a proposed fix, which you asked for and is next. Changing the code would need an unfreeze and new runs.
- Chromium rebuild for items 8 and 9.
- t48 and the shared 4,096 calibration failure, neither of which has been investigated.
- Partitioned small-pool outlier (one run had 2 errors and a p95 of 581 s), not explained or rerun.

## Integrity checks

- **`git fsck --full`:** exit code 0, no errors or missing objects, on 2026-11-06. It listed 20 dangling objects, which are unreferenced leftovers rather than damage, and I didn't remove them.
- **Commits:** local only, not pushed.
- **Second location:** everything is copied to `~/malabr_design_doc_copy/`, including this report and `BENCHMARKS_2026-11.md`.
