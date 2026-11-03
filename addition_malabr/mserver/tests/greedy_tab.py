"""RQ2 experiment: one greedy tab against three ordinary chats.

Three small tabs chat normally (short questions, short answers, a pause
between turns) while GREEDY tabs keep sending large pages, so their context
fills. Runs the real engine thread at production size -- n_ctx 16384,
n_seq_max 8, the calibrated cost curve, the session budget config.py would
give -- with one client thread per tab.

Recorded:
  timeline      every tab's token count (session pos), every 0.25 s
  compactions   who, when, how much, and whether the per-session trigger or
                the shared-KV aggregate guard did it
  rejections    every error frame a client received
  engine errors any round that raised (e.g. a decode with no room)
  latency       the small tabs' time to first token and total turn time

Scenarios (flags): --mode partitioned|shared, --greedy 0|1|2, --guard on|off.
--guard off replaces the aggregate guard with a no-op: the negative control
for what it prevents. In partitioned mode the guard never runs at all
(engine.py, _run_round), so --guard applies to shared mode only.

  python tests/greedy_tab.py --mode shared --greedy 1 --json out.json
"""

import argparse
import json
import math
import os
import statistics
import sys
import threading
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from harness import Fixture  # noqa: E402
from malabr_service import calibration as cal  # noqa: E402
from malabr_service import engine as eng  # noqa: E402

N_CTX, N_SEQ = 16384, 8
EXT = "a" * 32
SMALL_QUESTIONS = [
    "What is a hash table? Two sentences.",
    "How does a binary search work? Briefly.",
    "Name three uses of a queue.",
    "What is the difference between TCP and UDP? Briefly.",
    "What does a compiler do, in one sentence?",
    "Give one example of recursion.",
    "What is a deadlock? One sentence.",
    "Why is caching useful? Briefly.",
]
PAGE_SENTENCES = [
    "The committee reviewed the quarterly figures and noted a steady rise in demand.",
    "Several regional offices reported delays caused by supply shortages in the spring.",
    "Analysts expect the trend to continue, although forecasts remain uncertain.",
    "A new logistics partner was selected after a lengthy tendering process.",
    "Customer satisfaction scores improved in most regions during the period.",
    "The board approved additional investment in staff training and equipment.",
    "Energy costs rose sharply, affecting margins across manufacturing sites.",
    "Management outlined plans to expand into two new markets next year.",
]


def page_text(fmt, n_tokens):
    out, i = [], 0
    while True:
        out.append(f"{i + 1}. " + PAGE_SENTENCES[i % len(PAGE_SENTENCES)])
        i += 1
        if i % 20 == 0 and len(fmt._tokenize(" ".join(out), parse_special=False)) >= n_tokens:
            return " ".join(out)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--mode", choices=("partitioned", "shared"), required=True)
    ap.add_argument("--greedy", type=int, default=1)
    ap.add_argument("--guard", choices=("on", "off"), default="on")
    ap.add_argument("--turns", type=int, default=8)
    ap.add_argument("--think", type=float, default=1.0)
    ap.add_argument("--n-ctx", type=int, default=N_CTX,
                    help="pool size; smaller than production so the pool can actually "
                         "fill (and the guard act) within minutes on this CPU")
    ap.add_argument("--page", type=int, default=None, help="page size in tokens")
    ap.add_argument("--refusal-pause", type=float, default=2.0,
                    help="seconds a greedy tab waits after a refused page before "
                         "sending the next, as a person would (0: resend at once)")
    ap.add_argument("--json", default=None)
    a = ap.parse_args()
    shared = a.mode == "shared"
    n_ctx = a.n_ctx
    budget = max(n_ctx // N_SEQ, n_ctx // 2) if shared else n_ctx // N_SEQ  # config.n_ctx_per_session
    # Pages as large as the mode's input gate (budget - 256) comfortably admits.
    page_tokens = a.page or (3000 if shared else 900)

    fix = Fixture(n_seq_max=N_SEQ, n_ctx=n_ctx, n_threads=3, kv_unified=shared)
    alloc, e = fix.new_engine(shared_kv=shared, session_budget=budget)
    # A calibration measured for this run's own configuration (keyed; see
    # harness.Fixture.use_measured_calibration). Before 2026-11-04 this loaded
    # calibration.json whatever configuration it was measured under.
    _cal, _path = fix.use_measured_calibration(shared)
    cal.apply_to_engine(_cal, e, eng)
    print(f"  calibration: {_path}", flush=True)

    T0 = time.time()
    events, errors, turns, timeline = [], [], [], []
    in_agg = [False]
    real_compact, real_agg, real_step = e.compact, e._relieve_aggregate_pressure, e._step

    def compact_hook(s, keep_recent=True, **kw):
        before = s.pos
        freed = real_compact(s, keep_recent, **kw)
        if freed:
            events.append({"t": round(time.time() - T0, 2), "tab": s.key[1], "freed": freed,
                           "before": before, "after": s.pos, "keep_recent": keep_recent,
                           "by": "aggregate guard" if in_agg[0] else "session trigger"})
        return freed

    def agg_hook(sessions):
        in_agg[0] = True
        try:
            real_agg(sessions)
        finally:
            in_agg[0] = False

    def step_hook():
        try:
            return real_step()
        except Exception as exc:
            errors.append({"t": round(time.time() - T0, 2), "error": f"{type(exc).__name__}: {exc}"})
            raise

    e.compact = compact_hook
    e._relieve_aggregate_pressure = agg_hook if a.guard == "on" else (lambda sessions: None)
    e._step = step_hook

    small = [(EXT, t, f"https://small{t}.test") for t in (1, 2, 3)]
    greedy = [(EXT, 10 + g, f"https://greedy{g}.test") for g in range(a.greedy)]
    for k in small + greedy:
        e.get_or_create(k, fix.formatter, eng.OutputCap([(0, 64)]))
    page = page_text(fix.formatter(), page_tokens)
    stop = threading.Event()
    fill_done = []
    refused_once = set()        # greedy tabs that have had a page refused

    def run_turn(k, prompt):
        t_submit = time.time()
        ob = e.submit(k, prompt)
        if ob is None:
            return {"error": "session unavailable", "ttft": None, "total": 0.0, "tokens": 0}
        first, n, err = None, 0, None
        while True:
            try:
                ft, payload = ob.get(timeout=600)
            except Exception:
                err = "timeout"
                break
            if ft == eng.FRAME_TOKEN:
                n += 1
                first = first or time.time()
            elif ft == eng.FRAME_COMPLETE:
                break
            else:
                err = payload
                break
        return {"ttft": None if first is None else round(first - t_submit, 3),
                "total": round(time.time() - t_submit, 3), "tokens": n, "error": err}

    def small_client(k):
        for i in range(a.turns):
            r = run_turn(k, SMALL_QUESTIONS[i % len(SMALL_QUESTIONS)])
            turns.append({"tab": k[1], "turn": i, "t": round(time.time() - T0, 2), **r})
            time.sleep(a.think)

    def greedy_client(k):
        i = 0
        while not stop.is_set():
            r = run_turn(k, f"Here is the text of a page:\n\n{page}\n\n"
                            f"Summarise part {i + 1} of it in one sentence.")
            with e._reg_lock:
                s = e._sessions.get(k)
                pos = s.pos if s else None
            turns.append({"tab": k[1], "turn": i, "t": round(time.time() - T0, 2),
                          "pos_after": pos, **r})
            i += 1
            if (r["error"] or "").startswith(eng.REFUSED):
                refused_once.add(k)
                stop.wait(a.refusal_pause)

    def sampler():
        while not stop.is_set():
            with e._reg_lock:
                pos = {s.key[1]: s.pos for s in e._sessions.values()}
            timeline.append({"t": round(time.time() - T0, 2), "pos": pos,
                             "sum": sum(pos.values())})
            time.sleep(0.25)

    e.start()
    threads = [threading.Thread(target=sampler, daemon=True)]
    threads += [threading.Thread(target=greedy_client, args=(k,), daemon=True) for k in greedy]
    smalls = [threading.Thread(target=small_client, args=(k,)) for k in small]
    try:
        for t in threads:
            t.start()
        # Phase 1: the greedy tabs fill their context BEFORE the small tabs
        # start. With small tabs chatting, a page's prefill only gets what is
        # left of each 50 ms round after their decodes, so a greedy tab
        # started alongside them barely grows (first version of this
        # experiment: ~1,000 of 8,192 tokens in 45 s, nothing to measure).
        fill_deadline = time.time() + 600
        while greedy and time.time() < fill_deadline:
            with e._reg_lock:
                held = [e._sessions[k].pos for k in greedy if k in e._sessions]
            if held and min(held) >= 0.9 * budget:
                break
            # A tab whose page was refused can't grow any further, so waiting
            # for 90% would only run out the clock.
            if len(refused_once) == len(greedy):
                break
            time.sleep(0.5)
        fill_done.append(round(time.time() - T0, 2))
        for t in smalls:
            t.start()
        for t in smalls:
            t.join()
    finally:
        stop.set()
        time.sleep(0.5)
        e.stop()
        fix.close()

    # ---- report ----
    label = f"{a.mode}, greedy={a.greedy}, guard={a.guard if shared else 'n/a'}"
    print(f"=== greedy tab: {label}  (budget/session {budget}, n_ctx {N_CTX}, "
          f"page {page_tokens} tok, {time.time() - T0:.0f}s) ===".replace(f"n_ctx {N_CTX}", f"n_ctx {n_ctx}"))
    if greedy:
        print(f"  phase 1 (greedy tabs alone, until each holds 90% of its budget): "
              f"ended at {fill_done[0]}s" + ("" if fill_done[0] < 599 else " -- TIMED OUT")
              + (" (every greedy tab refused a page)" if len(refused_once) == len(greedy) else ""))
    tabs = sorted({k[1] for k in small + greedy})
    peak = {t: max((x["pos"].get(t, 0) for x in timeline), default=0) for t in tabs}
    print("  tab   role     peak tokens  compactions (session / aggregate)  freed")
    for t in tabs:
        ev = [x for x in events if x["tab"] == t]
        print(f"  {t:<5} {'greedy' if t >= 10 else 'small':<8} {peak[t]:>11}  "
              f"{sum(x['by'] == 'session trigger' for x in ev):>12} / "
              f"{sum(x['by'] == 'aggregate guard' for x in ev):<10} "
              f"{sum(x['freed'] for x in ev):>8}")
    print(f"  peak sum of tokens: {max((x['sum'] for x in timeline), default=0)} "
          f"(n_ctx {n_ctx})")
    errs = [x for x in turns if x["error"]]
    print(f"  error frames: {len(errs)}  " + "; ".join(
        f"tab {x['tab']}: {x['error'][:60]}" for x in errs[:6]))
    print(f"  engine round errors: {len(errors)}  " + "; ".join(
        x["error"][:70] for x in errors[:3]))
    st = [x for x in turns if x["tab"] < 10]
    ttft = [x["ttft"] for x in st if x["ttft"] is not None]
    tot = [x["total"] for x in st if not x["error"]]
    if ttft:
        p95 = sorted(ttft)[max(0, math.ceil(0.95 * len(ttft)) - 1)]   # nearest rank
        print(f"  small tabs: {len(st)} turns, TTFT median {statistics.median(ttft):.2f}s "
              f"p95 {p95:.2f}s max {max(ttft):.2f}s; turn time median "
              f"{statistics.median(tot) if tot else float('nan'):.2f}s")
    for g in greedy:
        gt = [x for x in turns if x["tab"] == g[1]]
        ok = [x for x in gt if not x["error"]]
        ref = [x for x in gt if (x["error"] or "").startswith(eng.REFUSED)]
        other = [x for x in gt if x["error"] and x not in ref]
        first_ref = ref[0] if ref else None
        print(f"  greedy tab {g[1]}: {len(gt)} pages sent, {len(ok)} admitted, "
              f"{len(ref)} refused, {len(other)} other errors"
              + (f"; first refusal at {first_ref['t']}s holding {first_ref['pos_after']} "
                 f"tokens: {first_ref['error'][len(eng.REFUSED):][:60]}" if first_ref else "")
              + (f"; other: {other[0]['error'][:60]}" if other else ""))
    if a.json:
        with open(a.json, "w") as fh:
            json.dump({"label": label, "mode": a.mode, "greedy": a.greedy, "guard": a.guard,
                       "fill_done": fill_done[0] if fill_done else None,
                       "budget": budget, "page_tokens": page_tokens, "n_ctx": n_ctx, "events": events,
                       "engine_errors": errors, "turns": turns, "timeline": timeline,
                       "peak": peak}, fh)
    return 0


if __name__ == "__main__":
    sys.exit(main())
