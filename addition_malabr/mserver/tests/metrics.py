"""Small numeric helpers shared by the measurement scripts.

One copy on purpose: these used to be duplicated across fidelity_check,
kv_quant_check, baseline_bench and pipeline_bench, which meant a fix to one
copy would silently leave the others measuring the old way.
"""
import math


def softmax(logits):
    m = max(logits)
    exps = [math.exp(v - m) for v in logits]
    s = sum(exps)
    return [e / s for e in exps]


def kl(p, q, eps=1e-12):
    """KL(p || q) in nats. Terms where p is ~0 contribute nothing."""
    return sum(pi * math.log(pi / max(qi, eps))
               for pi, qi in zip(p, q) if pi > eps)


def js(p, q):
    """Jensen-Shannon divergence in nats; symmetric and bounded by ln 2."""
    m = [(a + b) / 2 for a, b in zip(p, q)]
    return 0.5 * kl(p, m) + 0.5 * kl(q, m)


def topk(p, k):
    return [i for i, _ in sorted(enumerate(p), key=lambda t: t[1], reverse=True)[:k]]


def pct(xs, p):
    """Nearest-rank percentile; nan for an empty sample."""
    if not xs:
        return float("nan")
    xs = sorted(xs)
    k = max(0, min(len(xs) - 1, int(round((p / 100.0) * (len(xs) - 1)))))
    return xs[k]
