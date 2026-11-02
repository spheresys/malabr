"""How often does the canary method miss a REAL leak?

Every isolation test in the §12 suite is a canary test: plant a secret in one
session, ask another for it, and read "not repeated" as "not leaked". That is
only evidence if the method reports the leaks that do happen. This runs the
method against deliberately broken engines (harness.leak_trial) over freshly
generated secrets, with two detectors:

  reply   the secret's six digits appear in the answer (what the suite uses)
  score   the model's mean probability per digit, teacher-forced
          (harness.secret_logprob), above 1/2 -- a threshold fixed in advance

and counts, per variant:

  self-recall   the session holding the secret repeats it (positive control)
  caught        a broken engine's leak is reported
  false alarm   the correct engine is reported as leaking

Miss rates are trusted only if self-recall is at least 19/20 at f16: a method
whose holder cannot repeat its own secret cannot catch anyone else repeating
it. The run says so, and exits 2, when the gate fails.

Greedy decoding makes one secret one data point, so the trials vary the
secret, not the seed. The secrets are random per run; the seed is printed and
NEGCTL_SEED replays it.

Run once per KV type -- the type is fixed when the context is created:
  python tests/negative_control.py [N]                      # f16
  MALABR_KV_TYPE=q8_0 python tests/negative_control.py [N]  # q8_0
"""

import json
import os
import random
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from harness import (LEAK_SCORE_THRESHOLD, LEAK_VARIANTS, Fixture,  # noqa: E402
                     fresh_secret, leak_trial)

GATE = 19 / 20


def main():
    n = int(sys.argv[1]) if len(sys.argv) > 1 else 20
    kv = os.getenv("MALABR_KV_TYPE", "f16").lower()
    shared = os.getenv("MALABR_TEST_SHARED_KV") == "1"
    seed = int(os.getenv("NEGCTL_SEED") or int.from_bytes(os.urandom(4), "big"))
    rng = random.Random(seed)
    print(f"KV {kv}, {'shared' if shared else 'partitioned'} KV, {n} secrets per "
          f"variant, seed {seed}", flush=True)
    fix = Fixture()
    rows = []
    t0 = time.time()
    try:
        for i in range(n):
            for v in LEAK_VARIANTS:
                text, digits = fresh_secret(rng)          # a new secret every trial
                r = leak_trial(fix, v, text=text, digits=digits, score=True)
                rows.append({"secret": text, "digits": digits, "variant": v,
                             "control": r["control"], "leaked": r["leaked"],
                             "score": r["score"], "said": r["said"][:80]})
                print(f"  {digits} {v:16s} control={r['control']!s:5s} "
                      f"reply={r['leaked']!s:5s} score={r['score']:6.2f} "
                      f"{r['said'][:44]!r}", flush=True)
    finally:
        fix.close()

    print(f"\n=== KV {kv}, {'shared' if shared else 'partitioned'} KV, {n} secrets "
          f"per variant, seed {seed}, {time.time() - t0:.0f}s ===")
    print(f"    score threshold: mean p(digit) > 1/2 (log {LEAK_SCORE_THRESHOLD:.2f})")
    summary = {"kv": kv, "shared_kv": shared, "n": n, "seed": seed}
    trusted = True
    for v in LEAK_VARIANTS:
        rs = [r for r in rows if r["variant"] == v]
        ctl = sum(r["control"] for r in rs)
        by_reply = sum(r["leaked"] for r in rs)
        by_score = sum(r["score"] > LEAK_SCORE_THRESHOLD for r in rs)
        scores = sorted(r["score"] for r in rs)
        label = "false alarms" if v == "correct" else "caught"
        print(f"  {v:16s} self-recall {ctl}/{n}   {label}: reply {by_reply}/{n}, "
              f"score {by_score}/{n}   score range [{scores[0]:.2f}, {scores[-1]:.2f}]")
        summary[v] = {"self_recall": ctl, "by_reply": by_reply, "by_score": by_score,
                      "score_min": scores[0], "score_max": scores[-1]}
        if ctl < GATE * n:
            trusted = False
    if trusted:
        print(f"  holder recall >= {GATE:.0%} in every variant: miss rates TRUSTED")
    else:
        print(f"  holder recall < {GATE:.0%} somewhere: miss rates NOT TRUSTED"
              + (" (the f16 gate)" if kv == "f16" else " -- the lossy-KV finding"))
    summary["trusted"] = trusted
    out = os.getenv("NEGCTL_OUT")
    if out:
        with open(out, "w") as fh:
            json.dump({"summary": summary, "rows": rows}, fh, indent=1)
    return 0 if trusted or kv != "f16" else 2


if __name__ == "__main__":
    sys.exit(main())
