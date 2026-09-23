#!/usr/bin/env python3
"""Whole-pipeline measurement, not just time-to-first-token.

TTFT is one number and it only describes the first instant of a request. What a
user actually lives with is the whole stream, and what a thesis needs is
evidence across the pipeline. This records EVERY token's arrival time for every
session and derives:

  TTFT                 p50 / p95 / max, and the foreground's own
  inter-token gap      p50 / p95 / max -- the smoothness of the stream AFTER
                       the first token. A scheduler that admits a session every
                       round produces a steady drip; one that runs sessions to
                       completion in turn produces a long stall then a burst.
  stall count          gaps > 1s, i.e. moments the text visibly stops
  completion time      p50 / p95, end to end per session
  throughput           aggregate tokens/s, and per-session
  Jain fairness index  over per-session throughput. 1.0 = perfectly equal
                       service, 1/n = one session took everything. This is the
                       standard fairness measure in the scheduling literature
                       and is what "equal share" arms should score well on.
  foreground share     fraction of all tokens that went to the foreground tab

Three arms, identical except for the scheduler (see baseline_bench.py):
FIFO, equal-share continuous batching (llama.cpp `--parallel`), and MALABR.

    <venv>/python tests/pipeline_bench.py --tabs 8 --runs 3
"""
import argparse, json, os, statistics as st, sys, time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from malabr_service import engine as eng
from harness import Fixture

PROMPTS = [
    "Explain in detail how a hash table handles collisions.",
    "Describe the difference between a process and a thread, with examples.",
    "Walk through the TCP three-way handshake step by step.",
    "Explain what a race condition is and how to prevent one.",
    "Describe in detail what happens during a page fault.",
    "Explain the guarantees a write-ahead log provides.",
    "Summarise the CAP theorem and its practical consequences.",
    "Explain what a memory barrier does and why it is needed.",
]
STALL_S = 1.0          # a gap this long is a visible pause in the text


def pct(xs, p):
    if not xs:
        return float("nan")
    xs = sorted(xs)
    k = max(0, min(len(xs) - 1, int(round((p / 100.0) * (len(xs) - 1)))))
    return xs[k]


def jain(xs):
    """Jain's fairness index: (sum x)^2 / (n * sum x^2). 1.0 is perfect."""
    xs = [x for x in xs if x is not None]
    if not xs or sum(xs) == 0:
        return float("nan")
    return (sum(xs) ** 2) / (len(xs) * sum(x * x for x in xs))


def collect(fix, arm, n_tabs, cap, fg_tab):
    """Drive one arm and return per-session token arrival times."""
    alloc, e = fix.new_engine()
    e.control_connected = True
    e.foreground_tab_id = fg_tab if arm == "malabr" else -1
    keys = [("a" * 32, t, "https://a.test") for t in range(1, n_tabs + 1)]
    for k in keys:
        e.get_or_create(k, fix.formatter, eng.OutputCap([(0, cap)]))

    arrivals = {k: [] for k in keys}          # seconds since t0, per token
    done = {}
    t0 = time.perf_counter()
    boxes = {k: e.submit(k, PROMPTS[i % len(PROMPTS)]) for i, k in enumerate(keys)}
    pending = dict(boxes)

    if arm == "fifo":
        # One request at a time, to completion: a lock around one context.
        for k in keys:
            box = boxes[k]
            while True:
                e._step()
                drained = False
                while not box.empty():
                    kind, _ = box.get_nowait()
                    now = time.perf_counter() - t0
                    if kind == eng.FRAME_TOKEN:
                        arrivals[k].append(now)
                    elif kind in (eng.FRAME_COMPLETE, eng.FRAME_ERROR):
                        done[k] = now; drained = True
                if drained or time.perf_counter() - t0 > 600:
                    break
        e.stop()
        return arrivals, done, max(done.values()) if done else float("nan")

    # Batched arms: the engine thread runs; we drain every box tightly so the
    # recorded gaps reflect the scheduler, not our polling interval.
    e.start()
    try:
        deadline = t0 + 600
        while pending and time.perf_counter() < deadline:
            moved = False
            for k, box in list(pending.items()):
                while not box.empty():
                    moved = True
                    kind, _ = box.get_nowait()
                    now = time.perf_counter() - t0
                    if kind == eng.FRAME_TOKEN:
                        arrivals[k].append(now)
                    elif kind in (eng.FRAME_COMPLETE, eng.FRAME_ERROR):
                        done[k] = now
                        pending.pop(k, None)
                        break
            if not moved:
                time.sleep(0.0005)
    finally:
        e.stop()
    return arrivals, done, max(done.values()) if done else float("nan")


def summarise(arrivals, done, makespan, fg_key):
    ttfts, gaps_all, stalls, per_tok = [], [], 0, {}
    stalls_fg = stalls_bg = 0
    bg_gaps = []
    for k, ts in arrivals.items():
        if not ts:
            continue
        ttfts.append(ts[0])
        g = [b - a for a, b in zip(ts, ts[1:])]
        gaps_all.extend(g)
        n_stall = sum(1 for x in g if x > STALL_S)
        stalls += n_stall
        # Split by class: a scheduler that buys foreground smoothness by
        # stalling background tabs must be seen to do exactly that, rather
        # than hiding it in one aggregate count.
        if k == fg_key:
            stalls_fg += n_stall
        else:
            stalls_bg += n_stall
            bg_gaps.extend(g)
        per_tok[k] = len(ts)

    tput = {k: (len(ts) / done[k]) for k, ts in arrivals.items()
            if ts and done.get(k)}
    total_tokens = sum(len(t) for t in arrivals.values())
    fg_tokens = len(arrivals.get(fg_key, []))
    fg_ts = arrivals.get(fg_key) or []
    fg_gaps = [b - a for a, b in zip(fg_ts, fg_ts[1:])]

    return {
        "ttft_p50": st.median(ttfts) if ttfts else float("nan"),
        "ttft_p95": pct(ttfts, 95),
        "ttft_max": max(ttfts) if ttfts else float("nan"),
        "ttft_fg": fg_ts[0] if fg_ts else float("nan"),
        "gap_p50": st.median(gaps_all) if gaps_all else float("nan"),
        "gap_p95": pct(gaps_all, 95),
        "gap_max": max(gaps_all) if gaps_all else float("nan"),
        "gap_fg_p95": pct(fg_gaps, 95),
        "stalls_over_1s": stalls,
        "stalls_fg": stalls_fg,
        "stalls_bg": stalls_bg,
        "gap_bg_p95": pct(bg_gaps, 95),
        "done_p50": st.median(list(done.values())) if done else float("nan"),
        "done_p95": pct(list(done.values()), 95),
        "makespan": makespan,
        "throughput_tok_s": total_tokens / makespan if makespan else float("nan"),
        "fairness_jain": jain(list(tput.values())),
        "fg_token_share": fg_tokens / total_tokens if total_tokens else float("nan"),
        "tokens_total": total_tokens,
        # Kept so the fairness index can be explained rather than asserted:
        # these are the per-session service rates it is computed from.
        "per_session_tok_s": {str(k[1]): round(v, 2)
                              for k, v in sorted(tput.items())},
    }


ARMS = ["fifo", "parallel", "malabr"]
FIELDS = [
    ("ttft_fg",          "foreground TTFT (s)",        "lower"),
    ("ttft_p50",         "TTFT p50 (s)",               "lower"),
    ("ttft_p95",         "TTFT p95 (s)",               "lower"),
    ("gap_p50",          "inter-token p50 (s)",        "lower"),
    ("gap_p95",          "inter-token p95 (s)",        "lower"),
    ("gap_fg_p95",       "foreground gap p95 (s)",     "lower"),
    ("gap_bg_p95",       "background gap p95 (s)",     "lower"),
    ("stalls_fg",        "stalls >1s, FOREGROUND",     "lower"),
    ("stalls_bg",        "stalls >1s, background",     "lower"),
    ("done_p50",         "completion p50 (s)",         "lower"),
    ("makespan",         "makespan (s)",               "lower"),
    ("throughput_tok_s", "throughput (tok/s)",         "higher"),
    ("fairness_jain",    "Jain fairness (1=equal)",    "higher"),
]


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--tabs", type=int, default=8)
    ap.add_argument("--cap", type=int, default=64)
    ap.add_argument("--runs", type=int, default=3)
    ap.add_argument("--json", default=None)
    a = ap.parse_args()

    fg_tab = a.tabs                      # foreground arrives LAST
    fg_key = ("a" * 32, fg_tab, "https://a.test")
    print(f"=== whole-pipeline benchmark: {a.tabs} tabs, foreground = tab "
          f"{fg_tab} (arrives last), {a.runs} counted run(s) + warm-up ===\n")

    fix = Fixture(n_seq_max=max(4, a.tabs), n_ctx=4096, n_threads=3)
    out = {arm: [] for arm in ARMS}
    try:
        print("  warm-up (not counted)...", flush=True)
        collect(fix, "malabr", a.tabs, a.cap, fg_tab)
        for r in range(1, a.runs + 1):
            for arm in ARMS:
                arrivals, done, ms = collect(fix, arm, a.tabs, a.cap, fg_tab)
                out[arm].append(summarise(arrivals, done, ms, fg_key))
                print(f"  run {r} {arm:<9} done", flush=True)
    finally:
        fix.close()

    med = {arm: {f: st.median([x[f] for x in runs]) for f, _, _ in FIELDS}
           for arm, runs in out.items() if runs}

    print(f"\n{'metric':<28}{'fifo':>12}{'parallel':>12}{'malabr':>12}   better")
    print("-" * 78)
    for f, label, better in FIELDS:
        row = "".join(f"{med[arm][f]:>12.3f}" for arm in ARMS)
        print(f"{label:<28}{row}   {better}")

    print("\n  medians over", a.runs, "run(s). 'parallel' is equal-share "
          "continuous batching")
    print("  (llama.cpp --parallel / VTC): same engine, visibility signal off.")
    if a.json:
        json.dump(out, open(a.json, "w"), indent=2)
        print(f"\n  wrote {a.json}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
