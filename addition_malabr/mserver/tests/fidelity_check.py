#!/usr/bin/env python3
"""Section 8 output-fidelity check -- what compaction actually costs.

The section 12 suite answers compaction fidelity as a YES/NO: test 4 plants a
canary in the anchor turn and checks it can still be recalled. That proves the
anchor survives. It says nothing about how much the *distribution* moves, which
is the quantitative half of RQ2 ("...and what does that cost?").

Method. Two sessions are driven through the SAME conversation with greedy
sampling, so their KV contents are identical token for token. One is then
compacted through the engine's own `Engine.compact` -- not a reimplementation,
so what is measured is the shipping code path. Both are then prefilled with the
same probe question and the next-token distribution is read from each.

Reported per probe:
  top1 agree     does the compacted context pick the same next token
  top5 overlap   how much of the shortlist survives
  KL(full||cmp)  nats; the cost of using the compacted distribution when the
                 full one is right. Asymmetric on purpose: it punishes exactly
                 the failure that matters -- compaction assigning low
                 probability to something the full context knew was likely.
  JS            symmetric, bounded by ln2, for a scale-free read
  p_full(t*)    the compacted model's probability of the FULL model's choice

A baseline row is printed first: the same comparison between two UNCOMPACTED
sessions. That is not ceremony -- it is the noise floor. Batched decode is not
bit-exact across sequences, so a nonzero divergence there is the number every
compacted row has to be read against.

Run from mserver/:
    <venv>/python tests/fidelity_check.py
    <venv>/python tests/fidelity_check.py --turns 24 --json out.json
"""
import argparse
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import ctypes
import llama_cpp.llama_cpp as C

from malabr_service import engine as eng
from harness import Fixture, ask, conversation_of, force_turn
from metrics import js, kl, softmax, topk


# --- filler that makes a conversation grow without being noise ------------
# Real turns, so the dropped exchanges are the kind of thing compaction
# actually drops. Repeated cyclically until the budget is reached.
FILLER = [
    "Explain in two sentences what a hash table is.",
    "What is the difference between a process and a thread?",
    "Name three sorting algorithms and their average complexity.",
    "What does the TCP three-way handshake do?",
    "In one sentence, what is a race condition?",
    "What is the purpose of a write-ahead log?",
    "Describe what a page fault is.",
    "What is the CAP theorem?",
]

# Probes deliberately span the range compaction should and should not affect:
# what the KEPT turns hold (anchor, recent), what the DROPPED middle held,
# questions that need no conversation at all, and open-ended ones. 16, not 4:
# four probes gave one number per category, too few to tell a category's cost
# from one probe's luck.
PROBES = [
    ("anchor recall",    "What was the very first thing I asked you about?"),
    ("anchor recall 2",  "Repeat my first question word for word."),
    ("recent recall",    "What did I ask you about most recently?"),
    ("recent recall 2",  "What was your last answer about?"),
    ("middle recall",    "Did I ask you about the TCP handshake? Answer yes or no."),
    ("middle recall 2",  "List every topic I asked about, in order."),
    ("count",            "How many questions have I asked you so far?"),
    ("fresh arithmetic", "What is 17 multiplied by 23? Answer with the number."),
    ("fresh fact",       "What is the capital of Australia?"),
    ("fresh code",       "Write a Python one-liner that reverses a string."),
    ("fresh define",     "Define the word 'idempotent' in one sentence."),
    ("fresh translate",  "Translate 'good morning' into French."),
    ("open summary",     "Summarise our conversation in one sentence."),
    ("open next",        "What should I ask you next?"),
    ("open opinion",     "Which of the topics we covered is the most important?"),
    ("open style",       "Answer in one word: how do you feel?"),
]










class Probe:
    """Prefill a question into a session and read the next-token logits.

    Uses the engine's own `_decode_batch`, so the decode path is the shipping
    one. The session's `pos` is restored afterwards and the probe's tokens are
    removed from the KV, so a probe never perturbs the conversation it measures.
    """

    def __init__(self, fix, engine):
        self.fix = fix
        self.e = engine
        self.n_vocab = C.llama_vocab_n_tokens(fix.vocab)

    def next_token_dist(self, s, question):
        toks = s.formatter._tokenize(question, parse_special=False)
        start = s.pos
        items = [(t, start + i, s.slot, i == len(toks) - 1)
                 for i, t in enumerate(toks)]
        try:
            idx = self.e._decode_batch(items)
            ptr = C.llama_get_logits_ith(self.e._ctx, idx[-1])
            arr = ctypes.cast(
                ptr, ctypes.POINTER(ctypes.c_float * self.n_vocab)).contents
            return softmax(list(arr))
        finally:
            # Undo the probe unconditionally. Without the finally, a failed
            # decode would leave the probe's cells resident and `pos` moved,
            # and every LATER probe in the run would then measure a context
            # nobody intended -- quietly, as wrong numbers rather than an
            # error. The formatter is deliberately NOT touched: the probe must
            # not become part of the conversation it is measuring.
            C.llama_memory_seq_rm(self.e._alloc._mem, s.slot,
                                  start, start + len(toks))
            s.pos = start


def grow(engine, k, turns, budget_stop):
    """Drive a conversation until it is near the session budget."""
    n = 0
    for i in range(turns):
        ask(engine, k, FILLER[i % len(FILLER)], limit=120)
        n += 1
        s = engine._sessions.get(k) if hasattr(engine, "_sessions") else None
        if s is not None and s.pos >= budget_stop:
            break
    return n


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--turns", type=int, default=20)
    ap.add_argument("--topk", type=int, default=5)
    ap.add_argument("--json", default=None)
    a = ap.parse_args()

    fix = Fixture(n_seq_max=4, n_ctx=4096, n_threads=3)
    model = os.path.basename(os.getenv("MALABR_TEST_MODEL") or "Qwen3-0.6B-Q8_0.gguf")
    mode = "shared" if os.getenv("MALABR_TEST_SHARED_KV") == "1" else "partitioned"
    print(f"=== MALABR section 8 -- output fidelity under compaction ===\n"
          f"model {model}, {mode} KV, {len(PROBES)} probes\n")
    try:
        alloc, e = fix.new_engine()
        probe = Probe(fix, e)

        kR = ("a" * 32, 9, "https://a.test")   # generates the conversation
        kA = ("a" * 32, 1, "https://a.test")   # full context, reference
        kB = ("a" * 32, 2, "https://a.test")   # to be compacted
        kC = ("a" * 32, 3, "https://a.test")   # noise floor, never compacted
        for k in (kR, kA, kB, kC):
            e.get_or_create(k, fix.formatter, eng.OutputCap([(0, 48)]))

        sR, sA, sB, sC = (e._sessions[k] for k in (kR, kA, kB, kC))
        # The same conversation length in both modes, so they are comparable:
        # 80% of a partitioned slice. (80% of the shared budget, n_ctx/2, times
        # the four sessions this needs would not fit the fixture's pool.)
        stop = int(0.80 * fix.n_ctx // fix.n_seq_max)

        # One session GENERATES the conversation; A, B and C are force-fed it,
        # so they hold identical tokens. Growing each by generation is not
        # reproducible on the shared cache: a near-tie flips and the "twins"
        # become different conversations (harness.force_turn).
        print(f"generating a conversation to ~{stop} tokens (budget {sA.budget}), "
              f"then replaying it into three sessions")
        grow(e, kR, a.turns, stop)
        for user, reply in conversation_of(sR):
            for s_ in (sA, sB, sC):
                force_turn(e, s_, user, reply)
        e.cancel(kR, "done"); e._step()
        print(f"  posA={sA.pos} posB={sB.pos} posC={sC.pos}  "
              f"turns={len(sA.turn_boundaries)}\n")
        if not (sA.formatter._rendered == sB.formatter._rendered
                == sC.formatter._rendered and sA.pos == sB.pos == sC.pos):
            print("  ABORT: the three sessions do not hold identical tokens")
            return 1

        before = sB.pos
        freed = e.compact(sB)
        print(f"compacted B: freed {freed} tokens, "
              f"{before} -> {sB.pos} ({100*freed/max(before,1):.0f}% of context "
              f"dropped), turns {len(sB.turn_boundaries)}\n")
        if not freed:
            print("  compaction freed nothing -- conversation too short. "
                  "Raise --turns.")
            return 1

        hdr = (f"{'probe':<18}{'pair':<14}{'top1':>6}{'top'+str(a.topk):>7}"
               f"{'KL':>10}{'JS':>10}{'p(t*)':>9}")
        print(hdr); print("-" * len(hdr))

        rows = []
        for label, q in PROBES:
            pA = probe.next_token_dist(sA, q)
            pB = probe.next_token_dist(sB, q)
            pC = probe.next_token_dist(sC, q)
            for pair, p, qd in (("noise floor", pA, pC), ("compacted", pA, pB)):
                t_star = max(range(len(p)), key=p.__getitem__)
                t_other = max(range(len(qd)), key=qd.__getitem__)
                ov = len(set(topk(p, a.topk)) & set(topk(qd, a.topk)))
                row = {
                    "probe": label, "pair": pair,
                    "top1_agree": t_star == t_other,
                    "topk_overlap": ov, "k": a.topk,
                    "kl_nats": kl(p, qd), "js_nats": js(p, qd),
                    "p_compacted_of_full_argmax": qd[t_star],
                }
                rows.append(row)
                print(f"{label:<18}{pair:<14}"
                      f"{'yes' if row['top1_agree'] else 'NO':>6}"
                      f"{ov:>4}/{a.topk}"
                      f"{row['kl_nats']:>10.4f}{row['js_nats']:>10.4f}"
                      f"{row['p_compacted_of_full_argmax']:>9.4f}")

        comp = [r for r in rows if r["pair"] == "compacted"]
        base = [r for r in rows if r["pair"] == "noise floor"]
        print("\n--- summary ---")
        print(f"  context dropped        : {100*freed/max(before,1):.0f}%")
        print(f"  top-1 agreement        : {sum(r['top1_agree'] for r in comp)}"
              f"/{len(comp)} compacted, "
              f"{sum(r['top1_agree'] for r in base)}/{len(base)} noise floor")
        print(f"  mean KL (compacted)    : "
              f"{sum(r['kl_nats'] for r in comp)/len(comp):.4f} nats")
        print(f"  mean KL (noise floor)  : "
              f"{sum(r['kl_nats'] for r in base)/len(base):.4f} nats")
        cats = []
        for label, _q in PROBES:
            cat = label.split()[0]
            if cat not in cats:
                cats.append(cat)
        print("\n  by category        top-1 cmp / floor    mean KL cmp / floor")
        for cat in cats:
            cr = [r for r in comp if r["probe"].split()[0] == cat]
            br = [r for r in base if r["probe"].split()[0] == cat]
            print(f"  {cat:<18} {sum(r['top1_agree'] for r in cr)}/{len(cr)} / "
                  f"{sum(r['top1_agree'] for r in br)}/{len(br)}"
                  f"{sum(r['kl_nats'] for r in cr)/len(cr):>14.4f} / "
                  f"{sum(r['kl_nats'] for r in br)/len(br):.4f}")
        print("\n  Read the compacted row against the noise floor, not against "
              "zero.")

        if a.json:
            with open(a.json, "w") as f:
                json.dump({"model": model, "mode": mode, "freed": freed,
                           "before": before, "budget": sA.budget, "rows": rows},
                          f, indent=2)
            print(f"\n  wrote {a.json}")
        return 0
    finally:
        fix.close()


if __name__ == "__main__":
    sys.exit(main())
