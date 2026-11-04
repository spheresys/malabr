"""Shared fixtures for the section 12 suite.

Section 12's own framing: every isolation test reuses ONE mechanism -- plant a
canary early, act, then check whether it leaked or survived. Recall is always
yes/no, never a quality score.

Sampling is GREEDY here and only here (section 5d, test 45): canary recall must
be deterministic pass/fail. The user-facing path uses SAMPLING_CHAT and must
not silently inherit this.
"""

import math
import os
import random
import re
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import llama_cpp.llama_cpp as C

from malabr_service import calibration as cal
from malabr_service import engine as eng
from malabr_service.session import Turn

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
# Detection is on the DIGITS: a model asked for a passphrase it was never given
# may invent a word, but not this number.
CANARY_DIGITS = "7731"


class Fixture:
    """One model + one context, reused across tests to keep the suite quick."""

    def __init__(self, n_seq_max=4, n_ctx=4096, n_threads=3, kv_unified=None):
        C.llama_backend_init()
        mp = C.llama_model_default_params()
        mp.n_gpu_layers = 0
        self.model = C.llama_model_load_from_file(MODEL_PATH.encode(), mp)
        if not self.model:
            raise RuntimeError(f"cannot load model at {MODEL_PATH}")
        cp = C.llama_context_default_params()
        cp.n_ctx, cp.n_seq_max = n_ctx, n_seq_max
        # Both, as app.build does: a batch runs on n_threads_batch.
        cp.n_batch, cp.n_threads, cp.n_threads_batch = 512, n_threads, n_threads
        # Mirror production's KV element type so the suite can be run under
        # whatever the server would actually allocate (MALABR_KV_TYPE=q8_0).
        _kv = {"f16": "GGML_TYPE_F16", "q8_0": "GGML_TYPE_Q8_0"}.get(
            os.getenv("MALABR_KV_TYPE", "f16").lower())
        if _kv and hasattr(C, _kv):
            cp.type_k = cp.type_v = getattr(C, _kv)
        # The CACHE must match the mode, as app.build does: shared-KV mode is
        # one unified pool of n_ctx cells; partitioned is n_seq_max hard slices.
        # Until 2026-11-02 the fixture never set this, so "shared KV" test runs
        # used shared-KV bookkeeping over a partitioned cache.
        if kv_unified is None:
            kv_unified = os.getenv("MALABR_TEST_SHARED_KV") == "1"
        self.kv_unified = bool(kv_unified)
        if self.kv_unified:
            cp.kv_unified = True
        self.ctx = C.llama_init_from_model(self.model, cp)
        if not self.ctx:
            raise RuntimeError("context creation failed")
        self.mem = C.llama_get_memory(self.ctx)
        self.vocab = C.llama_model_get_vocab(self.model)
        self.n_seq_max = n_seq_max
        self.n_ctx = n_ctx
        self.n_threads = n_threads
        self.calibration = None     # set by use_measured_calibration

    def use_measured_calibration(self, shared_kv=None, path=None):
        """Measure (or load) a calibration for THIS fixture's model, mode, KV
        type, n_ctx, n_seq_max and threads, and make new_engine use its curves
        instead of the fixed DECODE_CURVE. Benchmarks only: the suite keeps the
        fixed curve so scheduler tests are deterministic.

        Stored per key, so the old and new code trees load the same file.
        """
        if shared_kv is None:
            shared_kv = self.kv_unified
        budget = self.n_ctx // 2 if shared_kv else self.n_ctx // self.n_seq_max

        class _Cfg:
            pass
        cfg = _Cfg()
        cfg.model_path, cfg.n_ctx, cfg.n_seq_max = MODEL_PATH, self.n_ctx, self.n_seq_max
        cfg.n_batch, cfg.n_threads, cfg.n_threads_explicit = 512, self.n_threads, True
        cfg.shared_kv, cfg.n_ctx_per_session = shared_kv, budget
        key = cal.calibration_key(cfg)
        path = path or os.getenv("MALABR_BENCH_CALIBRATION") or os.path.join(
            os.path.dirname(os.path.abspath(__file__)),
            f"bench_calibration_{'shared' if shared_kv else 'partitioned'}"
            f"_{self.n_ctx}x{self.n_seq_max}_t{self.n_threads}.json")
        result = cal.load(path, key)
        if result is None:
            result = cal.run(cfg, eng.MIN_CAP, eng.ABSOLUTE_CEILING,
                             ctx=self.ctx, model=self.model)
            C.llama_memory_clear(self.mem, True)
            if not result.get("fallback"):
                cal.save(result, path)
        self.calibration = result
        return result, path

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
        if self.calibration is not None:
            cal.apply_to_engine(self.calibration, e, eng)
        else:
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


def recalls_canary(text, digits=CANARY_DIGITS):
    """Yes/no, never a score (section 12's framing). True if the secret's
    digits appear, allowing the separators a model puts between digits
    ("77 31", "7,731")."""
    squashed = re.sub(r"(?<=\d)[\s,.\-](?=\d)", "", text or "")
    return digits in squashed


# The question every leak test asks, of the holder (positive control) and of
# the other session (the leak check) alike. DIRECT on purpose: the old guarded
# form, "...If you do not know, say UNKNOWN.", offers an escape hatch that
# Qwen3-0.6B takes even when the answer is in its own context -- with it the
# holder recalled 1 of 20 fresh canaries, so the method missed 19 of 20 real
# leaks (tests/negative_control.py). Chrome's test found the same.
LEAK_QUESTION = "Repeat back the vault passphrase I gave you, exactly."

# Distinctive words for fresh secrets; the digits carry the detection.
CANARY_WORDS = ["MAGENTA", "PELICAN", "OBSIDIAN", "JUNIPER", "TANGERINE",
                "FALCON", "QUARTZ", "LAVENDER", "CINNABAR", "WALRUS",
                "SAFFRON", "HARPOON", "GLACIER", "MARIGOLD", "TOPAZ",
                "BADGER", "CRIMSON", "ORCHID", "COBALT", "MACKEREL"]


def fresh_secret(rng):
    """A newly generated secret: a word and six random digits. Greedy
    decoding makes one secret one data point, so trials vary the secret."""
    digits = f"{rng.randrange(10 ** 6):06d}"
    return f"The vault passphrase is {rng.choice(CANARY_WORDS)}-{digits}.", digits


# One generator per suite run, so the compaction tests plant fresh secrets each
# run. The seed is printed by the suite; MALABR_TEST_SEED replays a run.
SUITE_SEED = int(os.getenv("MALABR_TEST_SEED") or int.from_bytes(os.urandom(4), "big"))
SUITE_RNG = random.Random(SUITE_SEED)

LEAK_VARIANTS = ("correct", "stale_slot", "shared_sequence")

# Second detector's threshold, fixed before any run: the mean probability per
# digit at least 1/2 -- the model more likely than not to produce each digit.
# A session that never saw the secret should sit near 1/10 per digit.
LEAK_SCORE_THRESHOLD = math.log(0.5)


def secret_logprob(fix, e, s, text, digits):
    """The probability-based detector: mean log-probability per token of the
    secret's digits as session `s`'s answer to LEAK_QUESTION, teacher-forced
    after "The vault passphrase is WORD-". Reads the model's belief directly,
    so it does not depend on what the model chooses to say. Leaves the
    session's KV and formatter exactly as found."""
    import numpy as np
    if s.slot in e._alloc.pending_wipe:
        e._alloc.prepare(s.slot)                  # as before any first token
    cp = s.formatter.checkpoint()
    start = s.pos
    try:
        question = s.formatter.user_turn(LEAK_QUESTION)
        prefix = text[:text.index(digits)]
        pre = s.formatter._tokenize(prefix, parse_special=False)
        full = s.formatter._tokenize(prefix + digits, parse_special=False)
        if full[:len(pre)] != pre:                # tokenizer merged across the seam
            pre = []
        target = full[len(pre):]
        toks = question + full
        e._decode_batch([(t, start + i, s.slot, True) for i, t in enumerate(toks)])
        n_vocab = C.llama_vocab_n_tokens(fix.vocab)
        total = 0.0
        first = len(question) + len(pre)
        for j, tok in enumerate(target):
            row = np.ctypeslib.as_array(
                C.llama_get_logits_ith(fix.ctx, first + j - 1), shape=(n_vocab,))
            m = float(row.max())
            total += float(row[tok]) - (m + math.log(float(np.exp(row - m).sum())))
        return total / len(target)
    finally:
        C.llama_memory_seq_rm(fix.mem, s.slot, start, -1)
        s.formatter.restore(cp)


def leak_trial(fix, variant, text=CANARY, digits=CANARY_DIGITS, cap=40, score=False):
    """Plant a canary in session A, check A recalls it, then ask session B --
    on the real engine, or on a DELIBERATELY BROKEN one.

    correct          A torn down; B gets A's slot, wiped as usual. Must not leak.
    stale_slot       Same, but the wipe is skipped and B carries on at the
                     slot's next position, as a bug that forgot the wipe would.
                     (Starting B at position 0 instead is refused by llama.cpp
                     itself: positions in a sequence must be consecutive.)
    shared_sequence  A stays alive and B is handed A's sequence, as a bug
                     that mapped two keys to one slot would.

    Returns {"control", "leaked", "said", "reused", "score"}. The canary
    test works only if it reports a leak for both broken variants. With
    score=True, "score" is secret_logprob() for B, measured before B answers.
    """
    alloc, e = fix.new_engine()
    ka = ("a" * 32, 1, "https://a.test")
    kb = ("a" * 32, 2, "https://b.test")
    sa, _ = e.get_or_create(ka, fix.formatter, eng.OutputCap([(0, cap)]))
    ask(e, ka, f"Remember this exactly: {text}")
    control = recalls_canary(ask(e, ka, LEAK_QUESTION), digits)
    reused = None
    if variant in ("correct", "stale_slot"):
        slot = sa.slot
        e.cancel(ka, "trial teardown"); e._step()
        if variant == "stale_slot":
            alloc._wipe_and_verify = lambda _slot: None          # THE BUG
        sb, _ = e.get_or_create(kb, fix.formatter, eng.OutputCap([(0, cap)]))
        reused = sb.slot == slot
        if variant == "stale_slot":
            sb.pos = C.llama_memory_seq_pos_max(fix.mem, sb.slot) + 1
    elif variant == "shared_sequence":
        sb, _ = e.get_or_create(kb, fix.formatter, eng.OutputCap([(0, cap)]))
        sb.slot, sb.pos = sa.slot, sa.pos                          # THE BUG
        reused = True
    else:
        raise ValueError(variant)
    lp = secret_logprob(fix, e, sb, text, digits) if score else None
    said = ask(e, kb, LEAK_QUESTION) or ""
    return {"control": control, "leaked": recalls_canary(said, digits),
            "said": said, "reused": reused, "score": lp}


COMPACTION_FILLER = ["Say the word filler{} once.", "Name one colour, then say filler{}.",
                     "Reply with the word filler{} only."]


def compaction_trial(fix, broken, text, digits, fillers=6, cap=24):
    """Plant a secret in the ANCHOR turn, grow the conversation, compact once,
    then ask for the secret -- on the real compaction, or on a DELIBERATELY
    BROKEN one that drops the anchor.

    The broken variant still runs the shipping Engine.compact: a zero-length
    placeholder turn is put in front of the anchor, so the anchor becomes
    droppable (compact never protects anything but index 0). With
    TARGET_FREED_TOKENS = 1 the correct compaction drops the first middle
    turn, the broken one drops exactly the anchor.

    Returns {"recalled", "score", "freed", "said"}. Compaction fidelity is
    evidenced only if correct compaction keeps recall AND the broken one
    loses it.
    """
    alloc, e = fix.new_engine()
    k = ("a" * 32, 1, "https://a.test")
    s, _ = e.get_or_create(k, fix.formatter, eng.OutputCap([(0, cap)]))
    ask(e, k, f"Remember this exactly: {text}")
    for i in range(fillers):
        ask(e, k, COMPACTION_FILLER[i % len(COMPACTION_FILLER)].format(i))
    e.TARGET_FREED_TOKENS = 1
    placeholder = None
    if broken:
        placeholder = Turn(0, 0, "placeholder", -1)
        s.turn_boundaries.insert(0, placeholder)
    try:
        freed = e.compact(s)
    finally:
        if placeholder is not None and placeholder in s.turn_boundaries:
            s.turn_boundaries.remove(placeholder)
    lp = secret_logprob(fix, e, s, text, digits)
    said = ask(e, k, LEAK_QUESTION) or ""
    return {"recalled": recalls_canary(said, digits), "score": lp,
            "freed": freed, "said": said}


def force_turn(e, s, user, reply):
    """Add one exchange with a FIXED reply, decoded directly -- no sampling.

    Why: on the shared (unified) cache, generation is not reproducible across
    sessions. Two sessions given identical prompts get slightly different
    logits when their cells sit in a different ORDER (holes left by trims and
    probes get reused), a near-tie eventually flips, and from then on they
    hold different conversations. So "twins" built by generation are not
    twins there. Force-fed turns are identical by construction. Records the
    turn exactly as Engine._finish_turn does, so compaction treats it alike.
    """
    if s.slot in e._alloc.pending_wipe:
        e._alloc.prepare(s.slot)
    start = s.pos
    toks = s.formatter.user_turn(user)
    msg_index = len(s.formatter._messages) - 1
    toks = toks + s.formatter._tokenize(reply, parse_special=False)
    items = [(t, start + i, s.slot, i == len(toks) - 1) for i, t in enumerate(toks)]
    for i in range(0, len(items), 512):          # the fixture's n_batch
        e._decode_batch(items[i:i + 512])
    s.pos = start + len(toks)
    s.turn_boundaries.append(Turn(start, s.pos, "exchange", msg_index))
    s.formatter.assistant_generated(reply)
    s.pos_before_request = s.pos_before_generation = s.pos


def conversation_of(s):
    """The (user, reply) pairs a session holds, for replaying into others."""
    msgs, suffix, out = s.formatter._messages, s.formatter._think_suffix, []
    for i in range(0, len(msgs) - 1, 2):
        (ru, u), (ra, a) = msgs[i], msgs[i + 1]
        if ru == "user" and ra == "assistant":
            out.append((u, a[len(suffix):] if a.startswith(suffix) else a))
    return out
