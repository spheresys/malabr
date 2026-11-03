"""Is the compaction-fidelity evidence trustworthy?

Tests 4, 9, 47 and 53 show a secret in the ANCHOR turn survives compaction.
That is evidence only if (a) it holds over many secrets, not one lucky one,
and (b) the same check notices when the anchor is NOT kept. This runs both
over freshly generated secrets (harness.compaction_trial):

  correct   the shipping Engine.compact, which keeps the anchor turn
  broken    the same code with the anchor made droppable, so it is dropped

with two detectors -- the reply repeats the secret's digits, and the model's
mean probability per digit (teacher-forced) is above 1/2 -- and reports:

  positive control   correct compaction: secret still recalled (want 20/20)
  negative control   broken compaction: loss detected (want 20/20)

The seed is printed; COMPCTL_SEED replays it.

  python tests/compaction_control.py [N]
  MALABR_TEST_SHARED_KV=1 python tests/compaction_control.py [N]
"""

import json
import os
import random
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from harness import (LEAK_SCORE_THRESHOLD, Fixture, compaction_trial,  # noqa: E402
                     fresh_secret)


def main():
    n = int(sys.argv[1]) if len(sys.argv) > 1 else 20
    kv = os.getenv("MALABR_KV_TYPE", "f16").lower()
    shared = os.getenv("MALABR_TEST_SHARED_KV") == "1"
    seed = int(os.getenv("COMPCTL_SEED") or int.from_bytes(os.urandom(4), "big"))
    rng = random.Random(seed)
    mode = "shared" if shared else "partitioned"
    print(f"KV {kv}, {mode} KV, {n} secrets per variant, seed {seed}", flush=True)
    fix = Fixture()
    rows = []
    t0 = time.time()
    try:
        for i in range(n):
            for broken in (False, True):
                text, digits = fresh_secret(rng)
                r = compaction_trial(fix, broken, text, digits)
                rows.append({"digits": digits, "broken": broken, **r})
                print(f"  {digits} {'broken ' if broken else 'correct'} freed={r['freed']:3d} "
                      f"recalled={r['recalled']!s:5s} score={r['score']:6.2f} "
                      f"{r['said'][:44]!r}", flush=True)
    finally:
        fix.close()

    good = [r for r in rows if not r["broken"]]
    bad = [r for r in rows if r["broken"]]
    print(f"\n=== compaction fidelity control: KV {kv}, {mode} KV, {n} secrets "
          f"per variant, seed {seed}, {time.time() - t0:.0f}s ===")
    print(f"  correct compaction: recalled by reply {sum(r['recalled'] for r in good)}/{n}, "
          f"by score {sum(r['score'] > LEAK_SCORE_THRESHOLD for r in good)}/{n}  "
          f"score range [{min(r['score'] for r in good):.2f}, {max(r['score'] for r in good):.2f}]")
    print(f"  broken compaction:  loss detected by reply {sum(not r['recalled'] for r in bad)}/{n}, "
          f"by score {sum(r['score'] <= LEAK_SCORE_THRESHOLD for r in bad)}/{n}  "
          f"score range [{min(r['score'] for r in bad):.2f}, {max(r['score'] for r in bad):.2f}]")
    out = os.getenv("COMPCTL_OUT")
    if out:
        with open(out, "w") as fh:
            json.dump({"kv": kv, "shared_kv": shared, "n": n, "seed": seed,
                       "rows": rows}, fh, indent=1)
    return 0


if __name__ == "__main__":
    sys.exit(main())
