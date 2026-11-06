"""DIAGNOSTIC ONLY, not a benchmark result. Patches behaviour at runtime; changes no file.

Run from a tree's mserver/ directory:
  diag.py LABEL HYP OUTDIR [--greedy N] [--n-ctx N] [--page N] [--runs N]
HYP: baseline | a (prefill share off) | b (OLD calibration) | c (session limit 4096)
     | d (shortest waiting prompt goes first)
"""
import argparse, json, os, sys, time
from types import SimpleNamespace

sys.path.insert(0, os.path.join(os.getcwd(), "tests"))
import campaign as K                                    # noqa: E402
from malabr_service import scheduler as sched           # noqa: E402

eng = K.eng
ap = argparse.ArgumentParser()
ap.add_argument("label"); ap.add_argument("hyp"); ap.add_argument("outdir")
ap.add_argument("--greedy", type=int, default=1)
ap.add_argument("--n-ctx", type=int, default=16384)
ap.add_argument("--page", type=int, default=3000)
ap.add_argument("--runs", type=int, default=2)
ap.add_argument("--old-cal", default="")
a = ap.parse_args()

if a.hyp == "a":
    sched.PREFILL_MIN_SHARE = 0.0

log = []
real_build = eng.build_batch


def remaining(s):
    return len(s.inbox_tokens) - s.prefill_offset


def build(sessions, curve, fg, *x, **k):
    if a.hyp == "d":
        sessions = sorted(sessions, key=lambda s: remaining(s) if s.state == eng.SessionState.PENDING else 0)
    t = time.time()
    picks, chunks, est = real_build(sessions, curve, fg, *x, **k)
    if picks or chunks:
        log.append({"k": "round", "t": t, "est": est, "corr_before": curve.correction,
                    "pick": [[s.key[1], s.pos] for s in picks],
                    "chunk": [[s.key[1], s.pos, len(c), remaining(s)] for s, c in chunks],
                    "wait": [[s.key[1], s.pos, remaining(s), s.prefill_stalls] for s in sessions
                             if s.state == eng.SessionState.PENDING and not any(s is c[0] for c in chunks)],
                    "n_gen": sum(s.state == eng.SessionState.GENERATING for s in sessions)})
    return picks, chunks, est


eng.build_batch = build
real_obs = eng.CostCurve.observe_round


def obs(self, est, seen):
    real_obs(self, est, seen)
    for r in reversed(log[-5:]):
        if r["k"] == "round" and "obs" not in r:
            r["obs"], r["corr"] = seen, self.correction
            break


eng.CostCurve.observe_round = obs
if hasattr(eng.Engine, "_admit"):
    real_admit = eng.Engine._admit

    def admit(self, s, need):
        v = real_admit(self, s, need)
        log.append({"k": "admit", "t": time.time(), "tab": s.key[1], "pos": s.pos, "need": need,
                    "verdict": None if v is None else str(v)[:50],
                    "others": [[o.key[1], o.pos] for o in self._live() if o is not s]})
        return v
    eng.Engine._admit = admit

real_send = K.Malabr.send


def send(self, tab, text, cap, on_token=None, timeout=K.TURN_TIMEOUT):
    t0 = time.time()
    seen = []

    def tok():
        if not seen:
            seen.append(time.time())
        if on_token:
            on_token()
    r = real_send(self, tab, text, cap, tok, timeout)
    log.append({"k": "turn", "tab": tab, "t0": t0, "first": seen[0] if seen else None, "end": time.time(),
                "tokens": r["tokens"], "error": r["error"]})
    return r


K.Malabr.send = send

sys_ = K.Malabr("shared", a.n_ctx)
if a.hyp == "b":
    sys_.fix.calibration = json.load(open(a.old_cal))
if a.hyp == "c":
    sys_.budget = sys_.limit = 4096
opt = SimpleNamespace(fill_seconds=120, small_turns=6, refusal_pause=2)
os.makedirs(a.outdir, exist_ok=True)
out = []
for r in range(a.runs):
    del log[:]
    row = K.w3_run(sys_, opt, a.greedy, a.page, f"diag-{a.greedy}-{r}")
    name = os.path.join(a.outdir, f"{a.label}_{r}")
    with open(name + ".jsonl", "w") as fh:
        for x in log:
            fh.write(json.dumps(x) + "\n")
    row = {k: v for k, v in row.items() if k != "compactions"}
    out.append(row)
    print("DIAGNOSTIC", a.label, r, "small ttft", row["small_ttft_median"], "turn", row["small_turn_median"],
          "errs", row["small_errors"], "greedy", {t: (g["ok"], g["refused"], g["other"]) for t, g in row["greedy"].items()},
          flush=True)
json.dump({"DIAGNOSTIC": True, "label": a.label, "hyp": a.hyp, "runs": out}, open(os.path.join(a.outdir, a.label + ".json"), "w"))
