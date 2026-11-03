#!/usr/bin/env python3
"""MALABR's relative score, repeated, so it can be compared honestly.

The relative score is  foreground TTFT / median TTFT across all tabs. A single
run of it is not a result: this project has already been caught twice drawing a
conclusion from n=1. This runs the MALABR arm N times under one configuration
and reports the distribution, so the comparison against Chrome's distribution
is between like and like.

Only the MALABR arm and only the realistic ordering (foreground arrives last),
because that is the cell being compared; the FIFO and equal-share arms are
covered by baseline_bench.py.

    <venv>/python tests/relative_repeat.py --runs 5 --tabs 8
"""
import argparse, json, os, statistics, sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from baseline_bench import run_malabr, pct          # noqa: E402
from harness import Fixture                          # noqa: E402


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--runs", type=int, default=5)
    ap.add_argument("--tabs", type=int, default=8)
    ap.add_argument("--cap", type=int, default=48)
    ap.add_argument("--json", default=None)
    ap.add_argument("--calibration", choices=("measured", "fixed"), default="measured",
                    help="cost curve: measured for this fixture's configuration "
                         "(harness.Fixture.use_measured_calibration), or the "
                         "harness's fixed DECODE_CURVE used before 2026-11-04")
    a = ap.parse_args()

    fg = a.tabs                       # foreground arrives LAST: the real case
    print(f"=== MALABR relative score, {a.runs} runs, {a.tabs} tabs, "
          f"foreground = tab {fg} (arrives last) ===\n")

    fix = Fixture(n_seq_max=max(4, a.tabs), n_ctx=4096, n_threads=3)
    if a.calibration == "measured":
        _cal, _path = fix.use_measured_calibration()
        print(f"  calibration: {_path} ({_cal['key']})", flush=True)
    rows = []
    try:
        # One UNCOUNTED warm-up. The first run pays for cold caches and the
        # cost-curve's closed loop settling, and it shows: an early measurement
        # gave 0.457 on run 1 against 0.075-0.107 on runs 2-5. Including it
        # would not be conservative, it would just be noisy.
        print("  warm-up (not counted)...", flush=True)
        run_malabr(fix, a.tabs, a.cap, fg, visibility=True)

        for r in range(1, a.runs + 1):
            ttft, makespan, keys = run_malabr(fix, a.tabs, a.cap, fg,
                                              visibility=True)
            vals = list(ttft.values())
            if len(vals) < a.tabs:
                print(f"  run {r}: only {len(vals)}/{a.tabs} tabs answered; skipped")
                continue
            fg_key = next(k for k in keys if k[1] == fg)
            med = statistics.median(vals)
            ratio = ttft[fg_key] / med
            rows.append({"run": r, "fg": ttft[fg_key], "median": med,
                         "p95": pct(vals, 95), "ratio": ratio})
            print(f"  run {r}: fg {ttft[fg_key]:5.2f}s  median {med:5.2f}s  "
                  f"p95 {pct(vals,95):5.2f}s  ratio {ratio:.3f}")
    finally:
        fix.close()

    if not rows:
        print("\nno usable runs")
        return 1
    ratios = [r["ratio"] for r in rows]
    print(f"\n  ratio  median {statistics.median(ratios):.3f}"
          f"   min {min(ratios):.3f}   max {max(ratios):.3f}"
          + (f"   stdev {statistics.stdev(ratios):.3f}" if len(ratios) > 1 else ""))
    if a.json:
        json.dump(rows, open(a.json, "w"), indent=2)
        print(f"  wrote {a.json}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
