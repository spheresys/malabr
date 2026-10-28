#!/usr/bin/env python3
"""MALABR vs the obvious alternative: one llama.cpp context per client.

The comparison that matters is not "is llama.cpp fast" -- it is the same model,
the same machine, the same requests, served two ways:

  FIFO         one shared context, one request at a time. What you get if you
               put a lock around a single llama.cpp context -- the honest naive
               baseline, and what most local-LLM wrappers actually do.
  PARALLEL     continuous batching with equal shares and NO visibility signal.
               This is llama.cpp `--parallel`, and VTC-style fairness, and in
               fact every serving engine: they cannot see tabs. Run inside our
               own engine with the foreground disabled, so model, machine,
               batching and budgets are all held constant and the ONLY
               difference from the arm below is the visibility signal itself.
  MALABR       the same engine with the foreground signal switched on.

Reported per configuration:
  TTFT p50/p95   time to the FIRST token of a request. The number a user feels.
  fg TTFT        the same, restricted to the foreground session -- the whole
                 point of visibility-aware admission.
  makespan       wall time until every request has finished. Throughput's view.

Why TTFT and not tokens/sec: both systems run the same model on the same CPU,
so aggregate token throughput is roughly a constant of the hardware. What
scheduling changes is WHO waits, and how long before anything appears.

Run from mserver/:
    <venv>/python tests/baseline_bench.py --tabs 4
    <venv>/python tests/baseline_bench.py --tabs 8 --json bench.json
"""
import argparse, json, os, sys, time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from malabr_service import engine as eng
from harness import Fixture
from metrics import pct  # noqa: F401  (relative_repeat imports it from here)

PROMPTS = [
    "Explain what a hash table is, in three sentences.",
    "What is the difference between a process and a thread?",
    "Describe the TCP three-way handshake.",
    "What is a race condition, and give one example?",
    "Explain what a page fault is.",
    "What does a write-ahead log guarantee?",
    "Summarise the CAP theorem.",
    "What is the purpose of a memory barrier?",
]




def run_malabr(fix, n_tabs, cap_tokens, foreground_tab, visibility=True):
    """All requests arrive at once; the engine schedules them.

    `visibility=False` is the llama.cpp `--parallel` arm. Setting the
    foreground to -1 removes the foreground-first rule and leaves continuous
    batching with aging and cheapest-first fill -- i.e. an equal-share
    scheduler with no idea which tab the user is looking at, which is what
    `--parallel` slots and VTC-style fairness both amount to.

    Everything else -- model, machine, batching, budgets, prompts -- is held
    constant, so the ONLY variable between this arm and the full engine is
    whether the scheduler may use the visibility signal. That is exactly the
    thing RQ3 asks about, and it is why this arm is more informative than
    benchmarking a different program.
    """
    alloc, e = fix.new_engine()
    e.foreground_tab_id = foreground_tab if visibility else -1
    e.control_connected = True
    keys = [("a" * 32, t, "https://a.test") for t in range(1, n_tabs + 1)]
    for k in keys:
        e.get_or_create(k, fix.formatter, eng.OutputCap([(0, cap_tokens)]))

    ttft, done = {}, {}
    t0 = time.perf_counter()
    boxes = {}
    for i, k in enumerate(keys):
        boxes[k] = e.submit(k, PROMPTS[i % len(PROMPTS)])

    pending = dict(boxes)
    e.start()
    try:
        deadline = t0 + 300
        while pending and time.perf_counter() < deadline:
            for k, box in list(pending.items()):
                try:
                    kind, _ = box.get(timeout=0.01)
                except Exception:
                    continue
                now = time.perf_counter()
                if kind == eng.FRAME_TOKEN and k not in ttft:
                    ttft[k] = now - t0
                if kind in (eng.FRAME_COMPLETE, eng.FRAME_ERROR):
                    done[k] = now - t0
                    pending.pop(k, None)
    finally:
        e.stop()
    makespan = max(done.values()) if done else float("nan")
    return ttft, makespan, keys


def run_sequential(fix, n_tabs, cap_tokens, foreground_tab):
    """One request at a time, FIFO -- a lock around a single context."""
    alloc, e = fix.new_engine()
    e.foreground_tab_id = foreground_tab
    e.control_connected = True
    keys = [("a" * 32, t, "https://a.test") for t in range(1, n_tabs + 1)]
    for k in keys:
        e.get_or_create(k, fix.formatter, eng.OutputCap([(0, cap_tokens)]))

    ttft = {}
    t0 = time.perf_counter()
    for i, k in enumerate(keys):
        box = e.submit(k, PROMPTS[i % len(PROMPTS)])
        # Drain this request to completion before the next one is admitted --
        # that IS the baseline; no other session can make progress meanwhile.
        while True:
            if not e._step():
                continue
            drained = False
            while not box.empty():
                kind, _ = box.get_nowait()
                now = time.perf_counter()
                if kind == eng.FRAME_TOKEN and k not in ttft:
                    ttft[k] = now - t0
                if kind in (eng.FRAME_COMPLETE, eng.FRAME_ERROR):
                    drained = True
            if drained:
                break
            if time.perf_counter() - t0 > 300:
                break
    return ttft, time.perf_counter() - t0, keys


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--tabs", type=int, default=4)
    ap.add_argument("--cap", type=int, default=48, help="max output tokens")
    ap.add_argument("--json", default=None)
    a = ap.parse_args()

    print("=== MALABR vs FIFO vs equal-share (llama.cpp --parallel) ===")
    print(f"  model   : {os.path.basename(os.environ.get('MALABR_TEST_MODEL','Qwen3-0.6B-Q8_0.gguf'))}")
    print(f"  tabs    : {a.tabs} concurrent requests")
    print(f"  cap     : {a.cap} output tokens/request")

    # WHERE THE FOREGROUND SITS IN THE QUEUE IS THE WHOLE EXPERIMENT.
    #
    # FIFO has no notion of a foreground, so its foreground latency is decided
    # entirely by arrival order. Measuring only the case where the foreground
    # happens to arrive first flatters FIFO for a reason that has nothing to do
    # with scheduling -- it was simply served first. The realistic case is the
    # opposite: background tabs are already generating and the user then turns
    # to a tab and asks something. That is "foreground last", and it is where a
    # visibility-aware scheduler is supposed to earn its keep.
    orders = [("foreground first", 1), ("foreground last", a.tabs)]

    fix = Fixture(n_seq_max=max(4, a.tabs), n_ctx=4096, n_threads=3)
    out = {}
    try:
        for label, fg in orders:
            print(f"\n  --- {label} (tab {fg}) ---")
            rows = {}
            arms = (
                ("fifo", lambda *x: run_sequential(*x)),
                ("parallel", lambda *x: run_malabr(*x, visibility=False)),
                ("malabr", lambda *x: run_malabr(*x, visibility=True)),
            )
            for name, fn in arms:
                ttft, makespan, keys = fn(fix, a.tabs, a.cap, fg)
                vals = list(ttft.values())
                fg_key = next((k for k in keys if k[1] == fg), None)
                rows[name] = {
                    "ttft_p50": pct(vals, 50), "ttft_p95": pct(vals, 95),
                    "ttft_foreground": ttft.get(fg_key, float("nan")),
                    "makespan": makespan, "answered": len(vals),
                }
                r = rows[name]
                print(f"  {name:<12} TTFT p50 {r['ttft_p50']:6.2f}s   "
                      f"p95 {r['ttft_p95']:6.2f}s   fg {r['ttft_foreground']:6.2f}s   "
                      f"makespan {r['makespan']:6.2f}s   ({r['answered']}/{a.tabs})")
            ml = rows["malabr"]
            for base in ("fifo", "parallel"):
                b = rows[base]
                print(f"      vs {base:<9} foreground TTFT "
                      f"{b['ttft_foreground']:.2f}s -> {ml['ttft_foreground']:.2f}s "
                      f"({b['ttft_foreground']/max(ml['ttft_foreground'],1e-9):.2f}x), "
                      f"median {b['ttft_p50']:.2f}s -> {ml['ttft_p50']:.2f}s "
                      f"({b['ttft_p50']/max(ml['ttft_p50'],1e-9):.2f}x)")
            out[label] = rows
    finally:
        fix.close()

    print("\n--- how to read this ---")
    print("  'foreground first' is the CONTROL: FIFO serves it immediately by")
    print("  accident of ordering, so MALABR cannot win and should not claim to.")
    print("  'foreground last' is the case the design targets.")
    print("  Makespan is expected to be no better, and may be slightly worse:")
    print("  batching the same model on the same cores does not create")
    print("  throughput. What changes is WHO waits.")

    if a.json:
        json.dump(out, open(a.json, "w"), indent=2)
        print(f"\n  wrote {a.json}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
