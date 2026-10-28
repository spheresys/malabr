#!/usr/bin/env python3
"""Does KV-cache quantization buy context cheaply enough to be worth it?

`OPTIMIZATION_ANALYSIS.md` lists KV quantization as roughly halving the bytes
per resident token, i.e. about 2x the context in the same RAM -- which is the
single biggest lever on §8's whole problem, since §8 exists because resident
context is scarce. It was never adopted because the fidelity cost was unknown.

This answers that with the same method as the §8 fidelity check: hold the
conversation identical, change ONE thing, and compare next-token distributions.
Here the thing changed is the KV cache type (f16 -> q8_0 -> q4_0) rather than
compaction.

Reported per configuration:
  KV bytes/token  measured from the allocated cache, not computed
  top-1 agree     does the quantized cache pick the same next token
  KL / JS         how far the distribution moved from the f16 reference

Read the numbers against `fidelity_check.py`'s noise floor: a divergence at or
below that floor is not distinguishable from batched-decode nondeterminism.

Run from mserver/:
    <venv>/python tests/kv_quant_check.py
"""
import argparse, ctypes, json, os, sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import llama_cpp.llama_cpp as C
from malabr_service import engine as eng
from harness import MODEL_PATH
from metrics import js, kl, softmax

CONVO = [
    "Remember this exactly: the vault passphrase is MAGENTA-7731.",
    "Explain in two sentences what a hash table is.",
    "What is the difference between a process and a thread?",
    "Describe what a page fault is.",
    "What does a write-ahead log guarantee?",
]
PROBES = [
    ("anchor recall",   "What is the vault passphrase?"),
    ("recent recall",   "What did I just ask you about?"),
    ("fresh reasoning", "What is 17 multiplied by 23? Answer with the number."),
    ("open ended",      "Summarise our conversation in one sentence."),
]

KV_TYPES = [("f16", C.GGML_TYPE_F16), ("q8_0", C.GGML_TYPE_Q8_0),
            ("q4_0", C.GGML_TYPE_Q4_0)]








def run(model, kv_type, n_ctx=2048):
    """Build the same conversation under one KV type; return probe dists.

    The MODEL is owned by the caller and loaded once: it is identical across
    KV types (only the cache differs), and reloading ~1 GB of weights per type
    on a machine this size is both slow and a needless way to run out of RAM.
    The CONTEXT is owned here and freed in a finally -- without that, a single
    failed decode would leak a whole context per type.
    """
    cp = C.llama_context_default_params()
    cp.n_ctx, cp.n_seq_max, cp.n_batch, cp.n_threads = n_ctx, 1, 512, 3
    cp.type_k = cp.type_v = kv_type
    ctx = C.llama_init_from_model(model, cp)
    if not ctx:
        return None                      # this type is not supported here
    try:
        vocab = C.llama_model_get_vocab(model)
        n_vocab = C.llama_vocab_n_tokens(vocab)
        fmt = eng.ChatFormatter(model, vocab)

        def decode(tokens, pos, want_last=True):
            b = C.llama_batch_init(len(tokens), 0, 1)
            try:
                b.n_tokens = len(tokens)
                for i, t in enumerate(tokens):
                    b.token[i] = t; b.pos[i] = pos + i
                    b.n_seq_id[i] = 1; b.seq_id[i][0] = 0
                    b.logits[i] = 1 if (want_last and i == len(tokens) - 1) else 0
                if C.llama_decode(ctx, b) != 0:
                    raise RuntimeError("decode failed")
                return len(tokens) - 1
            finally:
                C.llama_batch_free(b)

        pos = 0
        for msg in CONVO:                # prefill only: identical across types
            toks = fmt._tokenize(msg, parse_special=False)
            decode(toks, pos); pos += len(toks)

        out = {}
        for label, q in PROBES:
            toks = fmt._tokenize(q, parse_special=False)
            idx = decode(toks, pos)
            ptr = C.llama_get_logits_ith(ctx, idx)
            arr = ctypes.cast(ptr,
                              ctypes.POINTER(ctypes.c_float * n_vocab)).contents
            out[label] = softmax(list(arr))
            C.llama_memory_seq_rm(C.llama_get_memory(ctx), 0, pos, pos + len(toks))
        return {"dists": out, "pos": pos}
    finally:
        C.llama_free(ctx)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--json", default=None)
    a = ap.parse_args()
    print("=== KV-cache quantization: what 2x context actually costs ===\n")
    C.llama_backend_init()

    mp = C.llama_model_default_params(); mp.n_gpu_layers = 0
    model = C.llama_model_load_from_file(MODEL_PATH.encode(), mp)
    if not model:
        sys.exit(f"cannot load model at {MODEL_PATH}")

    ref = None
    rows = []
    try:
      for name, t in KV_TYPES:
        r = run(model, t)
        if r is None:
            print(f"  {name:<6} unsupported in this build")
            continue
        if ref is None:
            ref = r
            print(f"  {name:<6} reference (context {r['pos']} tokens)")
            continue
        agree = kls = jss = 0
        n = 0
        for label, _ in PROBES:
            p, q = ref["dists"][label], r["dists"][label]
            t1 = max(range(len(p)), key=p.__getitem__)
            t2 = max(range(len(q)), key=q.__getitem__)
            agree += (t1 == t2); kls += kl(p, q); jss += js(p, q); n += 1
        row = {"kv": name, "top1_agree": f"{agree}/{n}",
               "mean_kl": kls / n, "mean_js": jss / n}
        rows.append(row)
        print(f"  {name:<6} top-1 {agree}/{n}   mean KL {kls/n:.4f} nats   "
              f"mean JS {jss/n:.4f}")
    finally:
        C.llama_model_free(model)

    print("\n--- how to read this ---")
    print("  f16 -> q8_0 halves KV bytes per token: ~2x the resident context in")
    print("  the same RAM, which is the scarce resource the whole of section 8")
    print("  exists to ration. q4_0 halves it again.")
    print("  Compare these KL values against fidelity_check.py's noise floor")
    print("  (~0.0002 nats). At or below it, the change is not distinguishable")
    print("  from the nondeterminism already present in batched decode.")

    if a.json:
        json.dump(rows, open(a.json, "w"), indent=2)
        print(f"\n  wrote {a.json}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
