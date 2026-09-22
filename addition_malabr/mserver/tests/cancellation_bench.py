#!/usr/bin/env python3
"""RQ4 -- how much compute does cancel-on-tab-close actually recover?

Cancellation is implemented and its CORRECTNESS is covered (§12 tests 8, 19,
50, 59: the slot is freed within one round). What has never been measured is
the SAVING, which is the part RQ4 actually asks about.

The measurement has to count engine work, not frames delivered. Any system can
stop delivering tokens to a closed tab -- that costs nothing and saves nothing.
The question is whether the decode for those tokens still happens. So we count
tokens that entered `llama_decode` after the close, by wrapping `_decode_batch`.

Two arms, identical up to the close:
  RECLAIM   the tab close reaches the engine (MALABR): sessions are torn down
  DELIVERY  the close only stops the reader, as in a system with no lifecycle
            signal: generation runs to completion for tabs nobody is watching

Reported:
  wasted tokens   decode slots spent on closed tabs after the close
  survivor finish when the tabs still being watched actually complete

Run from mserver/:
    <venv>/python tests/cancellation_bench.py --tabs 8 --close 4
"""
import argparse, json, os, sys, time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from malabr_service import engine as eng
from harness import Fixture

PROMPTS = [
    "Explain in detail how a hash table handles collisions.",
    "Describe the difference between a process and a thread, with examples.",
    "Walk through the TCP three-way handshake step by step.",
    "Explain what a race condition is and how to prevent one.",
    "Describe what happens during a page fault, in detail.",
    "Explain the guarantees a write-ahead log provides.",
    "Summarise the CAP theorem and its practical consequences.",
    "Explain what a memory barrier does and why it is needed.",
]


def run(fix, n_tabs, n_close, cap, reclaim, close_after):
    alloc, e = fix.new_engine()
    e.foreground_tab_id = 1
    e.control_connected = True
    keys = [("a" * 32, t, "https://a.test") for t in range(1, n_tabs + 1)]
    for k in keys:
        e.get_or_create(k, fix.formatter, eng.OutputCap([(0, cap)]))

    closed = set(keys[n_tabs - n_close:])      # the LAST n_close tabs are closed
    survivors = [k for k in keys if k not in closed]

    # Count decode tokens per session, attributed by the engine's own picks.
    stats = {"after_close": 0, "after_close_closed": 0}
    closing_done = {"t": None}

    # Wrapping _decode_batch is the honest place: it is the call that costs.
    orig_decode = eng.Engine._decode_batch
    live_owner = {}

    def counting_decode(self, items):
        if closing_done["t"] is not None:
            stats["after_close"] += len(items)
            for (_tok, _pos, seq, _w) in items:
                k = live_owner.get(seq)
                if k in closed:
                    stats["after_close_closed"] += 1
        return orig_decode(self, items)

    eng.Engine._decode_batch = counting_decode
    boxes = {}
    try:
        t0 = time.perf_counter()
        for i, k in enumerate(keys):
            boxes[k] = e.submit(k, PROMPTS[i % len(PROMPTS)])
        # Slot -> key is snapshotted ONCE, which is only sound because every
        # session is created up front and none is torn down before the close.
        # If a survivor could ever re-acquire a closed session's slot, decode
        # for the survivor would be billed to the closed tab and the "wasted"
        # figure would be an overcount. Asserted rather than assumed.
        for k in keys:
            s = e._sessions[k]
            assert s.slot not in live_owner, "slot reused; attribution unsound"
            live_owner[s.slot] = k

        done, survivor_done = {}, {}
        deadline = t0 + 300
        closed_yet = False
        pending = dict(boxes)
        while pending and time.perf_counter() < deadline:
            e._step()
            now = time.perf_counter()
            if not closed_yet and now - t0 >= close_after:
                closed_yet = True
                closing_done["t"] = now
                if reclaim:
                    for k in closed:
                        e.cancel(k, "tab closed")
                # DELIVERY arm: do nothing. The reader stops caring; the engine
                # has no way to know, so the work continues.
            for k, box in list(pending.items()):
                drained = False
                while not box.empty():
                    kind, _ = box.get_nowait()
                    if kind in (eng.FRAME_COMPLETE, eng.FRAME_ERROR):
                        drained = True
                if drained:
                    done[k] = time.perf_counter() - t0
                    if k in survivors:
                        survivor_done[k] = done[k]
                    pending.pop(k, None)
        return {
            "decode_after_close": stats["after_close"],
            "wasted_on_closed": stats["after_close_closed"],
            "survivors_finished": max(survivor_done.values()) if survivor_done else float("nan"),
            "survivors_done": len(survivor_done),
        }
    finally:
        eng.Engine._decode_batch = orig_decode
        e.stop()


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--tabs", type=int, default=8)
    ap.add_argument("--close", type=int, default=4)
    ap.add_argument("--cap", type=int, default=96)
    ap.add_argument("--after", type=float, default=1.5,
                    help="seconds before the tabs are closed")
    ap.add_argument("--json", default=None)
    a = ap.parse_args()

    print("=== RQ4: what cancel-on-tab-close recovers ===")
    print(f"  {a.tabs} tabs generating; {a.close} of them closed "
          f"{a.after}s in; cap {a.cap} tokens\n")

    fix = Fixture(n_seq_max=max(4, a.tabs), n_ctx=4096, n_threads=3)
    out = {}
    try:
        for label, reclaim in (("delivery-only", False), ("reclaim (MALABR)", True)):
            r = run(fix, a.tabs, a.close, a.cap, reclaim, a.after)
            out[label] = r
            print(f"  {label:<18} decode tokens after close: {r['decode_after_close']:5d}"
                  f"   spent on closed tabs: {r['wasted_on_closed']:5d}"
                  f"   survivors done at {r['survivors_finished']:.2f}s")
    finally:
        fix.close()

    d, m = out["delivery-only"], out["reclaim (MALABR)"]
    print("\n--- summary ---")
    print(f"  compute spent on tabs nobody is watching: "
          f"{d['wasted_on_closed']} -> {m['wasted_on_closed']} tokens")
    if d["wasted_on_closed"]:
        print(f"  recovered: {100*(1-m['wasted_on_closed']/d['wasted_on_closed']):.0f}% "
              f"of the wasted decode")
    if d["survivors_finished"] and m["survivors_finished"]:
        print(f"  the tabs still being watched finish "
              f"{d['survivors_finished']/max(m['survivors_finished'],1e-9):.2f}x sooner")
    print("\n  The saving is real work, not undelivered frames: both arms stop")
    print("  showing tokens to a closed tab. Only one stops computing them.")

    if a.json:
        json.dump(out, open(a.json, "w"), indent=2)
        print(f"\n  wrote {a.json}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
