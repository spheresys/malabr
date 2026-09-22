"""Shared fixtures for the section 12 suite.

Section 12's own framing: every isolation test reuses ONE mechanism -- plant a
canary early, act, then check whether it leaked or survived. Recall is always
yes/no, never a quality score.

Sampling is GREEDY here and only here (section 5d, test 45): canary recall must
be deterministic pass/fail. The user-facing path uses SAMPLING_CHAT and must
not silently inherit this.
"""

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import llama_cpp.llama_cpp as C

from malabr_service import engine as eng

MODEL_PATH = os.getenv(
    "MALABR_TEST_MODEL",
    os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(
        os.path.abspath(__file__)))), "models", "Qwen3-0.6B-Q8_0.gguf"))

# A curve measured on the development machine, so scheduler tests are
# deterministic instead of depending on whatever the host does today.
DECODE_CURVE = [[32, 46.0, 45.0], [256, 37.0, 31.5], [512, 31.6, 30.8],
                [960, 29.0, 27.0], [4000, 10.0, 8.0]]
PREFILL_CURVE = [[32, 279.0], [512, 195.0]]

CANARY = "The vault passphrase is MAGENTA-7731."
CANARY_TOKEN = "MAGENTA"


class Fixture:
    """One model + one context, reused across tests to keep the suite quick."""

    def __init__(self, n_seq_max=4, n_ctx=4096, n_threads=3):
        C.llama_backend_init()
        mp = C.llama_model_default_params()
        mp.n_gpu_layers = 0
        self.model = C.llama_model_load_from_file(MODEL_PATH.encode(), mp)
        if not self.model:
            raise RuntimeError(f"cannot load model at {MODEL_PATH}")
        cp = C.llama_context_default_params()
        cp.n_ctx, cp.n_seq_max = n_ctx, n_seq_max
        cp.n_batch, cp.n_threads = 512, n_threads
        # Mirror production's KV element type so the suite can be run under
        # whatever the server would actually allocate (MALABR_KV_TYPE=q8_0).
        _kv = {"f16": "GGML_TYPE_F16", "q8_0": "GGML_TYPE_Q8_0"}.get(
            os.getenv("MALABR_KV_TYPE", "f16").lower())
        if _kv and hasattr(C, _kv):
            cp.type_k = cp.type_v = getattr(C, _kv)
        self.ctx = C.llama_init_from_model(self.model, cp)
        if not self.ctx:
            raise RuntimeError("context creation failed")
        self.mem = C.llama_get_memory(self.ctx)
        self.vocab = C.llama_model_get_vocab(self.model)
        self.n_seq_max = n_seq_max
        self.n_ctx = n_ctx

    def new_engine(self, sampling=None, shared_kv=None, session_budget=None):
        # shared_kv=None means "test's choice": partitioned by default, but a
        # whole-suite shared-KV pass is available via MALABR_TEST_SHARED_KV=1
        # (audit item 13 -- prove the aggregate-guard / soft-cap / input-gate
        # branches change no observable behaviour). Tests that pass an explicit
        # True/False are honoured as-is.
        if shared_kv is None:
            shared_kv = os.getenv("MALABR_TEST_SHARED_KV") == "1"
        alloc = eng.SlotAllocator(self.mem, self.n_seq_max)
        if session_budget is None:
            # Match production (config.n_ctx_per_session): non-shared is the
            # partitioned per-sequence limit n_ctx // n_seq_max -- the same value
            # llama.cpp hard-caps each sequence at -- so needs_compaction() fires
            # before the KV physically fills, not after. Shared-KV gets the
            # larger n_ctx/2 soft cap and the aggregate guard polices the sum.
            # (Was a hardcoded 2048, which for the 4096/4 fixture put the
            # compaction trigger at ~1945 in a sequence that only holds 1024.)
            session_budget = self.n_ctx // 2 if shared_kv else self.n_ctx // self.n_seq_max
        e = eng.Engine(self.ctx, self.model, self.vocab, alloc,
                       sampling=sampling or eng.SAMPLING_DETERMINISTIC,
                       n_ctx=self.n_ctx, n_seq_max=self.n_seq_max,
                       shared_kv=shared_kv, session_budget=session_budget)
        e.cost_curve = eng.CostCurve(DECODE_CURVE, PREFILL_CURVE)
        return alloc, e

    def formatter(self):
        return eng.ChatFormatter(self.model, self.vocab)

    def close(self):
        if self.ctx:
            C.llama_free(self.ctx); self.ctx = None
        if self.model:
            C.llama_model_free(self.model); self.model = None


def drive(engine, outbox=None, limit=400, idle_limit=8):
    """Run engine rounds until a terminal frame or the engine goes quiet."""
    idle = 0
    frames = []
    for _ in range(limit):
        if engine._step():
            idle = 0
        else:
            idle += 1
            if idle > idle_limit:
                break
        if outbox is not None:
            while not outbox.empty():
                f = outbox.get_nowait()
                frames.append(f)
                if f[0] in (eng.FRAME_COMPLETE, eng.FRAME_ERROR):
                    return frames
    return frames


def text_of(frames):
    return "".join(p for k, p in frames if k == eng.FRAME_TOKEN)


def ask(engine, key, prompt, limit=400):
    """Submit a prompt, run to completion, return the generated text."""
    outbox = engine.submit(key, prompt)
    if outbox is None:
        return None
    return text_of(drive(engine, outbox, limit=limit))


def recalls_canary(text):
    """Yes/no, never a score (section 12's framing)."""
    return CANARY_TOKEN.lower() in (text or "").lower()
