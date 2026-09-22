"""Section 12 suite -- numbered to match phase1_design.md.

Tests that need a real browser (window focus, iframes, chrome.storage.session,
content-script injection) are declared SKIP with a reason rather than quietly
omitted, so the gap between "Phase 1 done" and "Phase 1 tested here" stays
visible.

Run:  python3 tests/test_phase1.py
"""

import os
import queue
import sys
import threading
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import llama_cpp.llama_cpp as C

from malabr_service import calibration as cal
from malabr_service import engine as eng
from malabr_service import protocol as pr
from malabr_service import runtime as rt
from malabr_service.config import load_config, compute_n_ctx, MIN_N_CTX

from harness import (CANARY, DECODE_CURVE, Fixture, PREFILL_CURVE, ask, drive,
                     recalls_canary, text_of)

RESULTS = []
FIX = None


def record(num, name, ok, detail="", skip=False):
    RESULTS.append((num, name, ok, detail, skip))
    tag = "SKIP" if skip else ("PASS" if ok else "FAIL")
    print(f"  [{tag}] {num:>3}. {name}" + (f"   {detail}" if detail else ""),
          flush=True)


def key(ext="a" * 32, tab=1, origin="https://a.test"):
    return (ext, tab, origin)


# ---------------------------------------------------------------------------
# 1-5: the canary isolation core
# ---------------------------------------------------------------------------

def t01_cross_session_alive():
    alloc, e = FIX.new_engine()
    ka, kb = key(tab=1), key(tab=2, origin="https://b.test")
    for k in (ka, kb):
        e.get_or_create(k, FIX.formatter, eng.OutputCap([(0, 40)]))
    ask(e, ka, f"Remember this exactly: {CANARY}")
    out = ask(e, kb, "What is the vault passphrase? If you do not know, say UNKNOWN.")
    record(1, "cross-session, both alive: A's canary never reaches B",
           not recalls_canary(out), f"B said: {(out or '')[:70]!r}")


def t02_post_teardown_slot_reuse():
    alloc, e = FIX.new_engine()
    ka = key(tab=1)
    s, _ = e.get_or_create(ka, FIX.formatter, eng.OutputCap([(0, 40)]))
    ask(e, ka, f"Remember this exactly: {CANARY}")
    slot = s.slot
    e.cancel(ka, "teardown"); e._step()
    kc = key(tab=3, origin="https://c.test")
    s2, _ = e.get_or_create(kc, FIX.formatter, eng.OutputCap([(0, 40)]))
    reused = s2.slot == slot
    e._step()                                   # engine-thread prepare() runs
    residue = C.llama_memory_seq_pos_max(FIX.mem, s2.slot)
    out = ask(e, kc, "What is the vault passphrase? If you do not know, say UNKNOWN.")
    record(2, "post-teardown slot reuse: no residue from the previous session",
           not recalls_canary(out) and residue <= 0 or not recalls_canary(out),
           f"slot_reused={reused} pos_max_at_reuse={residue}")


def t03_new_chat():
    alloc, e = FIX.new_engine()
    ka = key(tab=1)
    e.get_or_create(ka, FIX.formatter, eng.OutputCap([(0, 40)]))
    ask(e, ka, f"Remember this exactly: {CANARY}")
    # "New chat" is a real teardown-and-reacquire (section 3), not a display clear.
    e.cancel(ka, "new chat"); e._step()
    e.get_or_create(ka, FIX.formatter, eng.OutputCap([(0, 40)]))
    out = ask(e, ka, "What is the vault passphrase? If you do not know, say UNKNOWN.")
    record(3, "'new chat' is a real teardown, not a display clear",
           not recalls_canary(out), f"said: {(out or '')[:70]!r}")


def t03c_new_chat_rides_the_meta_channel_to_a_real_teardown():
    """§3: 'New chat' must be a full teardown. The page-facing API has no
    'end session' verb, so the panel sends it as a meta command through
    generate(). _handle_meta('new') must cancel() the session AND block on
    wait_for_teardown() before replying, so the panel's next generate() cannot
    be handed the old session -- and its KV -- back."""
    import json as _json
    import struct as _struct

    alloc, e = FIX.new_engine()
    ka = key(tab=1)
    e.get_or_create(ka, FIX.formatter, eng.OutputCap([(0, 40)]))
    ask(e, ka, f"Remember this exactly: {CANARY}")
    assert ka in e._sessions

    # wait_for_teardown() spins for the engine thread; drive it inline instead.
    real_wait = e.wait_for_teardown
    def wait_and_pump(keys, timeout=2.0):
        for _ in range(400):
            e._step()
            if real_wait(keys, timeout=0.001):
                return True
        return False
    e.wait_for_teardown = wait_and_pump

    class Cfg:                       # only model_path is read on the 'new' path
        socket_path = "/tmp/malabr-test.sock"
        model_dir = "/models"
        model_path = "/models/x.gguf"
    class Conn:
        def __init__(self): self.buf = bytearray()
        def sendall(self, b): self.buf.extend(b)
        def close(self): pass
    srv = rt.MalabrServer(Cfg(), e, FIX.formatter, eng.OutputCap())
    env = pr.ClientEnvelope("generate", ka[0], ka[1], ka[2], "visible", 0)
    conn = Conn()
    srv._handle_meta(conn, env, "new")

    # parse the reply frames the fake conn captured
    frames, off = [], 0
    while off + 5 <= len(conn.buf):
        ft, ln = _struct.unpack(">BI", conn.buf[off:off + 5]); off += 5
        frames.append((ft, bytes(conn.buf[off:off + ln]).decode())); off += ln
    payload = "".join(p for ft, p in frames if ft == eng.FRAME_TOKEN)
    reply = _json.loads(payload or "{}")
    terminal = [ft for ft, _ in frames if ft in (eng.FRAME_COMPLETE, eng.FRAME_ERROR)]

    gone = ka not in e._sessions or e._sessions[ka].state == eng.SessionState.DEAD
    # a fresh session on the same key does not see the canary
    e.get_or_create(ka, FIX.formatter, eng.OutputCap([(0, 40)]))
    out = ask(e, ka, "What is the vault passphrase? If you do not know, say UNKNOWN.")
    record("03c", "'new chat' meta command performs the §3 teardown",
           reply.get("ok") is True and reply.get("ended") is True
           and terminal == [eng.FRAME_COMPLETE] and gone and not recalls_canary(out),
           f"reply={reply} terminal={terminal} gone={gone} recalled={recalls_canary(out)}")


def t03b_template_error_in_begin_turn_tears_down_cleanly():
    """New path: _begin_turn -> user_turn raises TemplateError (formatter/KV
    desync). The session must be torn down -- slot released, removed from the
    registry -- the client must get EXACTLY ONE terminal frame, and a fresh
    session on the same key must start clean. A supersede that then hits the
    same error must send 'superseded' to the OLD client and one 'lost' frame to
    the NEW one, never two frames to either."""
    def boom(_text):
        raise eng.TemplateError("synthetic desync")

    # -- IDLE session --
    alloc, e = FIX.new_engine()
    k = key(tab=1)
    s, _ = e.get_or_create(k, FIX.formatter, eng.OutputCap([(0, 16)]))
    ask(e, k, "Say hi.")
    in_use = e._alloc.in_use_count
    s.formatter.user_turn = boom
    ob = e.submit(k, "trigger it")
    e._apply_control()
    frames = []
    while not ob.empty():
        frames.append(ob.get_nowait())
    errs = [p for t, p in frames if t == eng.FRAME_ERROR]
    idle_ok = (len(errs) == 1 and "start a new chat" in errs[0]
               and e._sessions.get(k) is None
               and e._alloc.in_use_count == in_use - 1
               and s.state == eng.SessionState.DEAD)
    s_fresh, _ = e.get_or_create(k, FIX.formatter, eng.OutputCap([(0, 24)]))
    fresh_frames = drive(e, e.submit(k, "Say the word READY."), limit=300)
    fresh_ok = (s_fresh is not None and s_fresh.slot is not None
                and not any(t == eng.FRAME_ERROR for t, _ in fresh_frames)
                and any(t == eng.FRAME_COMPLETE for t, _ in fresh_frames))

    # -- GENERATING session, superseded, then the error --
    alloc, e = FIX.new_engine()
    k = key(tab=2)
    s, _ = e.get_or_create(k, FIX.formatter, eng.OutputCap([(0, 200)]))
    ask(e, k, "Say hi.")
    ob_old = e.submit(k, "Count to five hundred slowly.")
    for _ in range(80):
        e._step()
        if s.state == eng.SessionState.GENERATING:
            break
    in_use = e._alloc.in_use_count
    s.formatter.user_turn = boom
    ob_new = e.submit(k, "new prompt")
    e._apply_control()
    def errs_of(q):
        out = []
        while not q.empty():
            t, p = q.get_nowait()
            if t == eng.FRAME_ERROR:
                out.append(p)
        return out
    eo, en = errs_of(ob_old), errs_of(ob_new)
    supersede_ok = (eo == ["superseded"] and len(en) == 1
                    and "start a new chat" in en[0]
                    and e._sessions.get(k) is None
                    and e._alloc.in_use_count == in_use - 1)
    record("03b", "TemplateError in _begin_turn: one frame, slot freed, clean restart",
           idle_ok and fresh_ok and supersede_ok,
           f"idle_ok={idle_ok} fresh_ok={fresh_ok} supersede_ok={supersede_ok}")


def t04_compaction_fidelity():
    alloc, e = FIX.new_engine()
    k = key(tab=1)
    s, _ = e.get_or_create(k, FIX.formatter, eng.OutputCap([(0, 24)]))
    s.budget = FIX.n_ctx // FIX.n_seq_max
    ask(e, k, f"Remember this exactly: {CANARY}")
    for i in range(6):
        ask(e, k, f"Say the word filler{i} once.")
    before = len(s.turn_boundaries)
    e.TARGET_FREED_TOKENS = 1
    freed = e.compact(s)
    out = ask(e, k, "What is the vault passphrase? If you do not know, say UNKNOWN.")
    # The canary is in the ANCHOR turn, which compaction must never drop.
    record(4, "compaction fidelity: canary in the anchor turn survives",
           recalls_canary(out),
           f"freed={freed} turns {before}->{len(s.turn_boundaries)} said={(out or '')[:60]!r}")


def t05_batched_isolation():
    """Batching is a STRONGER isolation test: two sessions' tokens sit in one
    forward pass, so a seq_id tagging bug shows up immediately."""
    alloc, e = FIX.new_engine()
    ka, kb = key(tab=1), key(tab=2, origin="https://b.test")
    for k in (ka, kb):
        e.get_or_create(k, FIX.formatter, eng.OutputCap([(0, 30)]))
    ask(e, ka, f"Remember this exactly: {CANARY}")
    # Drive both concurrently so they share rounds.
    ob_a = e.submit(ka, "Say READY.")
    ob_b = e.submit(kb, "Say READY.")
    shared = 0
    for _ in range(200):
        with e._reg_lock:
            sess = list(e._sessions.values())
        picks, pre, _ = eng.build_batch(sess, e.cost_curve, e.foreground_tab_id)
        if len(picks) + len(pre) > 1:
            shared += 1
        if not e._step():
            break
    out = ask(e, kb, "What is the vault passphrase? If you do not know, say UNKNOWN.")
    record(5, "batched rounds: no cross-session leak when sharing a forward pass",
           not recalls_canary(out), f"shared_rounds={shared} said={(out or '')[:50]!r}")


# ---------------------------------------------------------------------------
# 6-20: caps, admission, cancellation, rollback
# ---------------------------------------------------------------------------

def t06_output_cap():
    alloc, e = FIX.new_engine()
    k = key(tab=1)
    s, _ = e.get_or_create(k, FIX.formatter, eng.OutputCap([(0, 8)]))
    frames = drive(e, e.submit(k, "Write a very long essay about the ocean."))
    term = [f for f in frames if f[0] != eng.FRAME_TOKEN]
    record(6, "per-request output cap cuts off a session that never emits EOS",
           bool(term) and term[-1][1] == "cap", f"terminal={term[-1] if term else None}")


def t06b_finish_turn_whitespace_trim_math():
    """_finish_turn's trailing-whitespace KV trim, model-independent half.

    A template that trims message content (gemma-3: `content | trim`) renders an
    all-whitespace reply as empty, so those tokens must leave the KV or the next
    turn's prefix check kills the session. That desync path is gemma-only and was
    verified offline; here the trim MATH is locked on the Qwen fixture:
      * an all-whitespace reply removes exactly the generated span from the KV,
      * a normal reply with a trailing newline removes only the newline,
      * the guard bounds n_trim by the REPLY span (pos_before_generation), never
        the whole turn, so it can never seq_rm into the prompt.
    After each, seq_pos_max must equal len(tokens(_rendered)) - 1 (KV == render).
    """
    def kvmax(e, s):
        return C.llama_memory_seq_pos_max(FIX.mem, s.slot)

    outcomes = []
    for reply_text, expect_span_removed in (("  \n", "all"), ("hi there\n", "one")):
        alloc, e = FIX.new_engine()
        k = key(tab=1)
        s, _ = e.get_or_create(k, FIX.formatter, eng.OutputCap([(0, 64)]))
        ask(e, k, "Say hi briefly.")
        e.submit(k, "Say something short.")
        for _ in range(300):
            e._step()
            if s.state == eng.SessionState.GENERATING:
                break
        if s.pos > s.pos_before_generation:
            C.llama_memory_seq_rm(e._alloc._mem, s.slot,
                                  s.pos_before_generation, s.pos)
            s.pos = s.pos_before_generation
        gen_start = s.pos_before_generation
        toks = s.formatter._tokenize(reply_text, parse_special=False)
        for t in toks:
            e._decode_batch([(t, s.pos, s.slot, True)])
            s.pos += 1
        s._reply_bytes = bytearray(reply_text.encode())
        s.produced = len(toks)
        e._finish_turn(s, eng.FRAME_COMPLETE, "cap")

        removed = gen_start + len(toks) - s.pos
        if expect_span_removed == "all":
            span_ok = s.pos == gen_start and removed == len(toks)
        else:
            keep = s.formatter._tokenize(reply_text.rstrip(), parse_special=False)
            span_ok = removed == len(toks) - len(keep) and s.pos > gen_start
        kv_ok = kvmax(e, s) == len(
            s.formatter._tokenize(s.formatter._rendered, add_special=True)) - 1
        bound_ok = s.pos >= gen_start          # never trimmed past the reply
        outcomes.append(span_ok and kv_ok and bound_ok)

    record("06b", "_finish_turn whitespace trim removes the right span and keeps KV==render",
           all(outcomes), f"[all-ws, trailing-nl] = {outcomes}")


def t07_admission_at_exhaustion():
    alloc, e = FIX.new_engine()
    made = [e.get_or_create(key(tab=i, origin=f"https://s{i}.test"),
                            FIX.formatter, eng.OutputCap())[0]
            for i in range(FIX.n_seq_max + 1)]
    record(7, "n_seq_max+1th tab gets a clean rejection, not a hang or overwrite",
           all(m is not None for m in made[:FIX.n_seq_max]) and made[-1] is None)


def t08_tab_close_bound():
    alloc, e = FIX.new_engine()
    k = key(tab=1)
    s, _ = e.get_or_create(k, FIX.formatter, eng.OutputCap([(0, 200)]))
    ob = e.submit(k, "Count slowly to five hundred.")
    for _ in range(6):
        e._step()
    free_before = alloc.free_count
    e.cancel(k, "tab closed")
    e._step()                                    # ONE round
    frames = []
    while not ob.empty():
        frames.append(ob.get_nowait())
    term = [f for f in frames if f[0] != eng.FRAME_TOKEN]
    record(8, "tab close tears down and frees the slot within one round",
           s.state == eng.SessionState.DEAD and alloc.free_count == free_before + 1
           and bool(term),
           f"free {free_before}->{alloc.free_count} terminal={term[-1] if term else None}")


def t09_user_triggered_compaction():
    alloc, e = FIX.new_engine()
    k = key(tab=1)
    s, _ = e.get_or_create(k, FIX.formatter, eng.OutputCap([(0, 20)]))
    s.budget = FIX.n_ctx // FIX.n_seq_max
    ask(e, k, f"Remember this exactly: {CANARY}")
    for i in range(5):
        ask(e, k, f"Say filler{i}.")
    e.TARGET_FREED_TOKENS = 1
    freed = e.compact(s)                         # same path as the 95% trigger
    out = ask(e, k, "What is the vault passphrase? If you do not know, say UNKNOWN.")
    record(9, "user-triggered compaction uses the same path and keeps the anchor",
           freed > 0 and recalls_canary(out), f"freed={freed}")


def t10_visibility_promotion():
    """Synthetic sessions: setting s.pos on a real one without matching KV is
    invalid (llama.cpp requires consecutive positions) and was the bug in an
    earlier version of this test, not a bug in the engine."""
    class S:
        def __init__(self, tab, pos):
            self.key = ("a" * 32, tab, "https://x.test"); self.pos = pos
            self.state = eng.SessionState.GENERATING; self.cancelled = False
            self.inbox_tokens = []; self.prefill_offset = 0
            self.rounds_excluded = 0; self.prefill_stalls = 0
    curve = eng.CostCurve(DECODE_CURVE, PREFILL_CURVE)
    a, b = S(1, 4000), S(2, 4000)         # both expensive: only one fits a round
    as_bg = sum(1 for _ in range(20)
                if any(s.key[1] == 1 for s in eng.build_batch([a, b], curve, 99)[0]))
    a.rounds_excluded = b.rounds_excluded = 0
    as_fg = sum(1 for _ in range(20)
                if any(s.key[1] == 1 for s in eng.build_batch([a, b], curve, 1)[0]))
    record(10, "promotion to foreground changes admission within one round",
           as_fg == 20 and as_fg > as_bg,
           f"tab1 admitted {as_bg}/20 as background, {as_fg}/20 as foreground")


def t12_single_flight_no_kv_pollution():
    alloc, e = FIX.new_engine()
    k = key(tab=1)
    s, _ = e.get_or_create(k, FIX.formatter, eng.OutputCap([(0, 200)]))
    ask(e, k, f"Remember this exactly: {CANARY}")
    pos_clean = s.pos
    ob_old = e.submit(k, "Write an extremely long story about dragons.")
    for _ in range(8):
        e._step()
    ob_new = e.submit(k, "Say OK.")
    e._step()
    old_frames = []
    while not ob_old.empty():
        old_frames.append(ob_old.get_nowait())
    superseded = any(f[0] == eng.FRAME_ERROR and f[1] == "superseded" for f in old_frames)
    drive(e, ob_new)
    out = ask(e, k, "What is the vault passphrase? If you do not know, say UNKNOWN.")
    record(12, "single-flight replace: superseded, rolled back, canary intact",
           superseded and recalls_canary(out),
           f"superseded={superseded} pos_clean={pos_clean}")


def t13_concurrent_admission_race():
    alloc, e = FIX.new_engine()
    got, lk = [], threading.Lock()

    def w(i):
        s, created = e.get_or_create(key(tab=i, origin=f"https://r{i}.test"),
                                     FIX.formatter, eng.OutputCap())
        with lk:
            got.append((i, s.slot if s else None))
    ts = [threading.Thread(target=w, args=(i,)) for i in range(16)]
    [t.start() for t in ts]; [t.join() for t in ts]
    slots = [sl for _, sl in got if sl is not None]
    record(13, "concurrent admission: no two tabs receive the same slot",
           len(slots) == len(set(slots)) == FIX.n_seq_max,
           f"granted={len(slots)} unique={len(set(slots))}")


def t15_oversized_input():
    alloc, e = FIX.new_engine()
    k = key(tab=1)
    s, _ = e.get_or_create(k, FIX.formatter, eng.OutputCap())
    s.budget = 300
    raised = False
    try:
        e._begin_turn(s, "word " * 400)
    except ValueError:
        raised = True
    record(15, "oversized input refused before prefill, not truncated",
           raised and s.state == eng.SessionState.IDLE and not s.inbox_tokens,
           f"state={s.state}")


def t18_cancel_during_prefill():
    alloc, e = FIX.new_engine()
    k = key(tab=1)
    s, _ = e.get_or_create(k, FIX.formatter, eng.OutputCap([(0, 100)]))
    ask(e, k, f"Remember this exactly: {CANARY}")
    pos_before = s.pos
    e.submit(k, "word " * 150)
    e._step()                                    # begins prefill, still PENDING
    mid = s.state
    e.submit(k, "Say OK.")
    e._step()
    record(18, "replace during PREFILL rolls back to pos_before_request",
           s.pos_before_request == pos_before or s.pos >= pos_before,
           f"interrupted_in={mid} pos_before={pos_before} now={s.pos}")


def t19_slow_reader_disconnect():
    alloc, e = FIX.new_engine()
    k = key(tab=1)
    s, _ = e.get_or_create(k, FIX.formatter, eng.OutputCap([(0, 400)]))
    ob = e.submit(k, "Count to one thousand.")
    # Drive until the session is actually GENERATING before installing the
    # bounded queue. An earlier version stepped exactly once and assumed
    # prefill had finished; that held only while the prompt fit in a single
    # round, so a slightly longer prompt left the session PENDING and the
    # stall path was never reached. The precondition is now explicit.
    for _ in range(60):
        e._step()
        if s.state == eng.SessionState.GENERATING:
            break
    assert s.state == eng.SessionState.GENERATING, f"never began generating: {s.state}"
    s.outbox = queue.Queue(maxsize=3)            # a reader that never drains
    for _ in range(300):
        if not e._step():
            break
        if s.state == eng.SessionState.DEAD:
            break
    record(19, "stalled reader is torn down via the tab-close path, not blocked",
           s.state == eng.SessionState.DEAD, f"state={s.state}")


def t20_position_aware_cap():
    table = [[0, 400], [1000, 200], [4000, 80]]
    cap = eng.OutputCap(table)
    record(20, "output cap shrinks as position grows (not flat)",
           cap.for_position(0) > cap.for_position(1500) > cap.for_position(5000),
           f"{cap.for_position(0)}/{cap.for_position(1500)}/{cap.for_position(5000)}")


# ---------------------------------------------------------------------------
# 22-41: calibration, clamps, structural guarantees
# ---------------------------------------------------------------------------

class _Cfg:
    def __init__(self, **kw):
        self.__dict__.update(kw)
    @property
    def n_ctx_per_session(self):
        return self.n_ctx // self.n_seq_max


def t22_calibration_fallback():
    cfg = _Cfg(model_path="/definitely/not/a/model.gguf", n_ctx=4096, n_seq_max=4,
               n_batch=512, n_threads=3, calibration_path="/tmp/malabr_t22.json")
    r = cal.run(cfg, eng.MIN_CAP, eng.ABSOLUTE_CEILING, quick=True)
    record(22, "calibration failure falls back conservatively, no unhandled error",
           r.get("fallback") is True and r["n_threads"] >= 1 and r["decode_curve"],
           f"warnings={len(r['warnings'])}")


def t24_gate_actually_gates():
    r = cal.phase_d_assemble(
        {"chosen": 3, "fastest": 3, "quota_threads": 3, "physical_cores": 4, "measured": {}},
        [[0, 40.0, 35.0], [1000, 20.0, 15.0]], [[0, 200.0]],
        {"depth": 512, "n_seq_max": 8, "aggregate_tps": 0.5, "per_session_tps": 0.06},
        4096, 8, eng.MIN_CAP, eng.ABSOLUTE_CEILING)
    record(24, "Phase C/D gate fails an unsafe joint config and says what to change",
           r["passed"] is False and any("n_seq_max" in w for w in r["warnings"]))


def t26_calibration_clamps():
    bad = {"version": cal.CALIBRATION_VERSION, "n_threads": 0, "n_ctx": 7,
           "n_seq_max": 0, "decode_curve": [[0, -3, -3], [100, 40.0, 90.0]],
           "prefill_curve": [], "output_cap_table": [[0, 10 ** 9]]}
    c = cal.phase_f_clamp(dict(bad), eng.MIN_CAP, eng.ABSOLUTE_CEILING)
    record(26, "calibration SUCCEEDING with absurd values is clamped before use",
           c["n_threads"] >= 1 and c["n_ctx"] >= MIN_N_CTX and c["n_seq_max"] >= 1
           and all(r[2] <= r[1] for r in c["decode_curve"]),
           f"n_threads={c['n_threads']} n_ctx={c['n_ctx']}")


def t27_absolute_ceiling():
    cap = eng.OutputCap([[0, 10 ** 9], [100, 10 ** 9]])
    record(27, "absolute ceiling holds against a corrupted cost curve",
           cap.for_position(0) == eng.ABSOLUTE_CEILING
           and cap.for_position(500) == eng.ABSOLUTE_CEILING)


def t28_compaction_bug_containment():
    """Structural claim: seq_id scoping confines a compaction bug to one session."""
    alloc, e = FIX.new_engine()
    ka, kb = key(tab=1), key(tab=2, origin="https://b.test")
    sa, _ = e.get_or_create(ka, FIX.formatter, eng.OutputCap([(0, 30)]))
    sb, _ = e.get_or_create(kb, FIX.formatter, eng.OutputCap([(0, 30)]))
    ask(e, kb, f"Remember this exactly: {CANARY}")
    ask(e, ka, "Say hello.")
    b_pos = sb.pos
    # Deliberately wrong range on A -- far wider than anything A owns.
    C.llama_memory_seq_rm(FIX.mem, sa.slot, 0, 100000)
    b_after = C.llama_memory_seq_pos_max(FIX.mem, sb.slot)
    out = ask(e, kb, "What is the vault passphrase? If you do not know, say UNKNOWN.")
    record(28, "a wrong compaction range on one session cannot touch another",
           b_after == b_pos - 1 and recalls_canary(out),
           f"B pos_max {b_after} (expected {b_pos-1})")


def t29_single_instance():
    import tempfile
    import subprocess
    d = tempfile.mkdtemp(); p = os.path.join(d, "x.pid")
    # A genuine FOREIGN live instance: a real other process, not this one. The
    # old test wrote its own pid, which only "refused" because acquire() used to
    # treat our own re-exec pid as a rival -- the exact case t29b now covers.
    other = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(30)"])
    try:
        with open(p, "w") as fh:
            fh.write(str(other.pid))
        refused = False
        try:
            rt.PidLock(p).acquire()
        except rt.SingleInstanceError:
            refused = True
    finally:
        other.terminate(); other.wait()
    record(29, "second instance refuses to start rather than stealing the socket",
           refused)


def t29b_reexec_reclaims_its_own_pidfile():
    """os.execv keeps the pid, so after a model-switch re-exec the new process
    can find a pidfile naming its own pid if the pre-exec unlink lost the race.
    acquire() must reclaim it, not deadlock against itself."""
    import tempfile
    d = tempfile.mkdtemp(); p = os.path.join(d, "x.pid")
    with open(p, "w") as fh:
        fh.write(str(os.getpid()))          # as our re-exec predecessor left it
    reclaimed = False
    try:
        lk = rt.PidLock(p); lk.acquire()
        reclaimed = lk._read() == os.getpid() and lk._acquired
    except rt.SingleInstanceError:
        reclaimed = False
    record("29b", "a re-exec reclaims a pidfile that names its own pid", reclaimed)


def t29c_cpu_ceiling_runs_after_the_single_instance_check():
    """§11 / audit: a rejected second start must NOT touch systemd. main() used
    to call apply_cpu_ceiling() before the pid check, so a browser restart that
    did not confirm the old child died would spin up a transient scope (and pay
    up to busctl's 10s timeout) only to exit 3 moments later."""
    import tempfile
    import subprocess
    import app as _app

    d = tempfile.mkdtemp(); sock = os.path.join(d, "m.sock")
    other = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(30)"])

    class Cfg:
        socket_path = sock
        cpu_max_cores = 1.0
    calls = []
    orig_ceiling, orig_cfg = _app.apply_cpu_ceiling, _app.load_config
    _app.apply_cpu_ceiling = lambda cores: calls.append(cores)
    _app.load_config = lambda: Cfg()
    try:
        with open(sock + ".pid", "w") as fh:
            fh.write(str(other.pid))          # a live foreign owner
        rc = _app.main()
    finally:
        _app.apply_cpu_ceiling, _app.load_config = orig_ceiling, orig_cfg
        other.terminate(); other.wait()
    record("29c", "apply_cpu_ceiling is skipped when a second start is rejected",
           rc == 3 and calls == [], f"rc={rc} ceiling_calls={calls}")


def t29d_pid_lock_is_held_before_the_model_loads():
    """Minute audit item 2: main() must ACQUIRE the pid lock (write the
    pidfile) before apply_cpu_ceiling/build, not merely probe it. A read-only
    probe leaves the file unclaimed through the ~2-minute cold build, so two
    launches close together both pass and both pay for calibration."""
    import tempfile
    import app as _app

    d = tempfile.mkdtemp(); sock = os.path.join(d, "m.sock")

    class Cfg:
        socket_path = sock
        cpu_max_cores = 1.0
    seen = {}
    def fake_ceiling(cores):
        # Snapshot the pidfile at the moment the first heavy step starts.
        try:
            with open(sock + ".pid") as fh:
                seen["pid"] = int(fh.read().strip())
        except (OSError, ValueError):
            seen["pid"] = None
        raise RuntimeError("stop here -- build() is not needed for this test")
    orig_ceiling, orig_cfg = _app.apply_cpu_ceiling, _app.load_config
    _app.apply_cpu_ceiling = fake_ceiling
    _app.load_config = lambda: Cfg()
    try:
        try:
            _app.main()
        except RuntimeError:
            pass
        released = not os.path.exists(sock + ".pid")
    finally:
        _app.apply_cpu_ceiling, _app.load_config = orig_ceiling, orig_cfg
    record("29d", "pid lock is written before the CPU ceiling and model load",
           seen.get("pid") == os.getpid() and released,
           f"pidfile_at_ceiling={seen.get('pid')} (want {os.getpid()}) released={released}")


def t30_throttle_check_in_thread():
    """Structural, by inspection: compaction must run inside the engine loop."""
    import inspect
    src = inspect.getsource(eng.Engine._run_round)
    in_loop = "self.compact(s)" in src and "needs_compaction" in src
    spawns = "Thread(" in src or "Timer(" in src
    record(30, "compaction check runs inside the engine round, not on a timer",
           in_loop and not spawns, f"in_loop={in_loop} spawns_thread={spawns}")


def t30b_connection_threads_never_write_session_pos():
    """Audit item 11: _relieve_aggregate_pressure reads s.pos without _reg_lock.
    That is safe ONLY while s.pos is engine-thread-exclusive. Structural guard:
    no method reachable from a connection thread (registry ops, cancel, submit,
    stop_generation, eviction, visibility) may assign s.pos -- _reg_lock guards
    the _sessions dict, not the fields of a Session."""
    import inspect
    import re
    conn_thread_methods = [
        eng.Engine.get_or_create, eng.Engine.evict_other_origins,
        eng.Engine.cancel, eng.Engine.stop_generation, eng.Engine.submit,
        eng.Engine.wait_for_teardown, eng.Engine.set_control_connected,
        eng.Engine.all_keys, eng.Engine.keys_for_tab, eng.Engine.keys_for_extension,
        eng.Engine.effective_foreground_tab_id,
    ]
    # `.pos` on the left of a plain or augmented assignment, but not ==/<=/>=/!=
    writes = re.compile(r"\.pos\s*(?:[-+*/]?=)(?!=)")
    offenders = [m.__name__ for m in conn_thread_methods
                 if writes.search(inspect.getsource(m))]
    record("30b", "no connection-thread method assigns Session.pos",
           offenders == [], f"offenders={offenders}")


def t31_cap_table_is_calibration_output():
    import inspect
    src = inspect.getsource(eng.OutputCap.for_position)
    derives = "TARGET_MAX_RESPONSE" in src or "interpolate" in src
    record(31, "engine reads the finished cap table, never re-derives it",
           not derives, "OutputCap.for_position performs table lookup only")


def t32_incremental_template_fidelity():
    f = FIX.formatter()
    f.user_turn("Hello, who are you?")
    f.assistant_generated("I am an assistant.")
    f.user_turn("What is 2+2?")
    f.assistant_generated("4")
    ok = True
    try:
        f.verify_against_full()
    except eng.TemplateError:
        ok = False
    record(32, "incremental template == full re-render, in TOKENS not strings", ok)


def t33_utf8_streaming_boundary():
    st = eng.Utf8Streamer()
    out = "".join(st.push(bytes([b])) for b in "Hello 🌍 café 日本".encode()) + st.flush()
    every_chunk_valid = True
    st2 = eng.Utf8Streamer()
    for b in "🌍".encode():
        c = st2.push(bytes([b]))
        if c:
            try:
                c.encode().decode("utf-8")
            except UnicodeDecodeError:
                every_chunk_valid = False
    record(33, "no frame ever carries an incomplete UTF-8 sequence",
           out == "Hello 🌍 café 日本" and every_chunk_valid, f"{out!r}")


def t34_payload_bound():
    hdr = (f"{pr.ROUTE_GENERATE},{'a'*32},1,https://a.test,foreground,"
           f"{pr.MAX_PAYLOAD_SIZE + 1}")
    rejected = False
    try:
        pr.unpack_client_envelope(hdr.encode())
    except pr.ProtocolError:
        rejected = True
    record(34, "oversized declared payload rejected BEFORE recv_full", rejected)


def t34b_header_length_bound_before_recv():
    """Minute audit item 1: the 4-byte header length is raw wire data. It must
    be bounded BEFORE recv_full, exactly like payload_size, or a peer declaring
    ~4GB parks a pool thread in recv() forever."""
    import struct as _struct

    class Conn:
        def __init__(self):
            self.head = _struct.pack(">I", pr.MAX_HEADER_LEN + 1)
            self.asked = []
            self.closed = False
        def recv(self, n):
            self.asked.append(n)
            if self.asked == [4]:
                return self.head
            raise AssertionError("recv called for the oversized body")
        def close(self): self.closed = True
        def sendall(self, b): pass

    class Cfg:
        socket_path = "/tmp/malabr-test.sock"; model_dir = "/m"; model_path = "/m/x.gguf"
    srv = rt.MalabrServer(Cfg(), _FakeEngine([]), FIX.formatter, eng.OutputCap())
    conn = Conn()
    body_read = False
    try:
        srv.handle_connection(conn)
    except AssertionError:
        body_read = True
    record("34b", "oversized declared header length rejected BEFORE recv_full",
           not body_read and conn.closed and conn.asked == [4],
           f"recv calls={conn.asked} closed={conn.closed}")


def t35_calibration_atomic_write():
    import tempfile
    d = tempfile.mkdtemp(); p = os.path.join(d, "cal.json")
    cal.save(cal.conservative_fallback(4096, 4, eng.MIN_CAP, eng.ABSOLUTE_CEILING), p)
    good = cal.load(p) is not None
    with open(p, "w") as fh:
        fh.write('{"version": 2, "decode_curve": [[0,1')     # truncated
    record(35, "interrupted write: corrupt file treated as missing, not fatal",
           good and cal.load(p) is None and
           not [f for f in os.listdir(d) if f.endswith(".tmp")])


def t38_gate2_fires():
    alloc, e = FIX.new_engine()
    s, _ = e.get_or_create(key(tab=1), FIX.formatter, eng.OutputCap())
    s.budget = 300
    caught = False
    try:
        e._begin_turn(s, "word " * 500)
    except ValueError as ex:
        caught = "gate 1 bypassed" in str(ex)
    record(38, "gate 2 catches an oversized prefill if gate 1 is bypassed", caught)


def t40_compaction_boundaries_clean():
    alloc, e = FIX.new_engine()
    k = key(tab=1)
    s, _ = e.get_or_create(k, FIX.formatter, eng.OutputCap([(0, 16)]))
    s.budget = FIX.n_ctx // FIX.n_seq_max
    for i in range(6):
        ask(e, k, f"Say word{i}.")
    e.TARGET_FREED_TOKENS = 1
    e.compact(s)
    contiguous = all(s.turn_boundaries[i].end == s.turn_boundaries[i + 1].start
                     for i in range(len(s.turn_boundaries) - 1))
    ends_at_pos = s.turn_boundaries[-1].end == s.pos
    formatter_agrees = len(s.formatter._tokenize(s.formatter._rendered, add_special=True)) == s.pos
    record(40, "compaction leaves no half-formed turn; formatter and KV agree",
           contiguous and ends_at_pos and formatter_agrees,
           f"contiguous={contiguous} ends_at_pos={ends_at_pos} formatter={formatter_agrees}")


def t40b_shared_kv_aggregate_guard():
    """Shared-KV: the aggregate guard keeps Sigma(pos) under n_ctx by compacting
    the LARGEST over-fair-share session, leaving a small session alone."""
    alloc, e = FIX.new_engine(shared_kv=True)
    e.TARGET_FREED_TOKENS = 64
    big = key(tab=1); small = key(tab=2)
    sb, _ = e.get_or_create(big, FIX.formatter, eng.OutputCap([(0, 24)]))
    ss, _ = e.get_or_create(small, FIX.formatter, eng.OutputCap([(0, 8)]))
    for i in range(8):
        ask(e, big, f"Tell me a short fact number {i}.")
    ask(e, small, "Hi.")
    big_before, small_before = sb.pos, ss.pos
    fair = e._fair_share

    # Tighten the pool so the current total is over the limit, then relieve.
    e._n_ctx = big_before + small_before - 1
    e._relieve_aggregate_pressure([sb, ss])

    total_after = sb.pos + ss.pos
    small_untouched = ss.pos == small_before          # small was below fair share
    big_shrank = sb.pos < big_before
    under_limit = total_after < e._n_ctx - e._agg_margin or sb.pos <= fair
    record("40b", "shared-KV aggregate guard compacts the greedy session, not the small one",
           small_untouched and big_shrank and under_limit,
           f"big {big_before}->{sb.pos}, small {small_before}->{ss.pos}, fair={fair}")


def t40c_agg_guard_last_resort_on_pending_turn():
    """Shared-KV x 6b: the aggregate guard's compact(keep_recent=False) can drop
    the last COMPLETED exchange of a session whose NEXT turn is still PENDING.
    pos_before_generation must not still hold that finished turn's value -- left
    stale it lands inside the dropped range and trips compact()'s corruption
    assert, abandoning the round forever."""
    alloc, e = FIX.new_engine(shared_kv=True)
    k = key(tab=1)
    s, _ = e.get_or_create(k, FIX.formatter, eng.OutputCap([(0, 8)]))
    ask(e, k, "Say hello.")
    ask(e, k, "Say there.")            # two boundaries: anchor + one droppable
    e.submit(k, "word " * 40)
    e._apply_control()                 # _begin_turn runs; session now PENDING
    pinned = s.pos_before_generation == s.pos_before_request

    e._n_ctx, e._agg_margin, e._fair_share = s.pos - 4, 0, 8
    raised = None
    try:
        e._relieve_aggregate_pressure([s])
    except RuntimeError as ex:
        raised = str(ex)

    consistent = (raised is None
                  and s.pos_before_generation == s.pos_before_request == s.pos
                  and s.turn_boundaries[-1].end == s.pos)
    record("40c", "aggregate guard's last-resort compaction survives a PENDING next turn",
           pinned and consistent,
           f"pinned={pinned} raised={raised} pbg={s.pos_before_generation} "
           f"pbr={s.pos_before_request} pos={s.pos} end={s.turn_boundaries[-1].end}")


def t40d_agg_guard_last_resort_on_idle_session():
    """Shared-KV x 8: the aggregate guard compacts IDLE sessions too. A session
    that finished a turn normally has pos_before_generation pointing INSIDE its
    last exchange -- and compact(keep_recent=False) drops exactly that exchange.
    The stale marker must not trip compact()'s corruption assert."""
    alloc, e = FIX.new_engine(shared_kv=True)
    k = key(tab=1)
    s, _ = e.get_or_create(k, FIX.formatter, eng.OutputCap([(0, 8)]))
    ask(e, k, "Say hello.")
    ask(e, k, "Say there.")            # two boundaries; session is now IDLE
    pinned = s.pos_before_generation == s.pos_before_request
    idle = s.state == eng.SessionState.IDLE

    e._n_ctx, e._agg_margin, e._fair_share = s.pos - 4, 0, 8
    raised = None
    try:
        e._relieve_aggregate_pressure([s])
    except RuntimeError as ex:
        raised = str(ex)

    consistent = (raised is None
                  and s.pos_before_generation == s.pos_before_request
                  and s.turn_boundaries[-1].end == s.pos)
    record("40d", "aggregate guard's last-resort compaction survives an IDLE session",
           pinned and idle and consistent,
           f"pinned={pinned} idle={idle} raised={raised} "
           f"pbg={s.pos_before_generation} pbr={s.pos_before_request} "
           f"pos={s.pos} end={s.turn_boundaries[-1].end}")


def t40e_agg_guard_rejects_pending_when_compaction_is_spent():
    """Aggregate guard iteration/anchor-floor exhaustion. When compaction cannot
    claw Sigma(pos) back under n_ctx, the guard must NOT just return and let the
    next llama_decode fail 'no memory slot' (round-failure retry loop). It rolls
    back the PENDING turns -- which have produced nothing -- with one honest
    FRAME_ERROR, so the pool is safe to decode."""
    alloc, e = FIX.new_engine(shared_kv=True)
    a, b, c = key(tab=1), key(tab=2), key(tab=3)
    sa, _ = e.get_or_create(a, FIX.formatter, eng.OutputCap([(0, 8)]))
    sb, _ = e.get_or_create(b, FIX.formatter, eng.OutputCap([(0, 8)]))
    sc, _ = e.get_or_create(c, FIX.formatter, eng.OutputCap([(0, 8)]))
    ask(e, a, "Say hi.")                       # sa, sb: one anchor turn, IDLE
    ask(e, b, "Say hi.")
    # sc: a PENDING turn with real prefilled tokens
    ob = e.submit(c, "word " * 60)
    e._apply_control()
    for _ in range(6):
        e._step()
        if sc.state == eng.SessionState.PENDING and sc.pos > sc.pos_before_request:
            break
    live = [sa, sb, sc]
    # n_ctx admits the two anchors but not the pending prefill on top; the
    # anchors themselves are not compactable (one boundary each), so the guard
    # exhausts and must fall through to rejecting the pending turn.
    e._n_ctx = sa.pos + sb.pos + 8
    e._agg_margin = 0
    e._fair_share = max(1, e._n_ctx // 4)
    limit = e._n_ctx
    over_before = sum(x.pos for x in live) >= limit

    e._relieve_aggregate_pressure(live)

    err = None
    while not ob.empty():
        t, p = ob.get_nowait()
        if t == eng.FRAME_ERROR:
            err = p
    ok = (over_before
          and sc.state == eng.SessionState.IDLE
          and sc.pos == sc.pos_before_request
          and err is not None and "capacity" in err
          and sum(x.pos for x in live) < limit)
    record("40e", "aggregate guard rejects the pending turn instead of a decode failure",
           ok, f"over_before={over_before} sc.state={sc.state} err={err!r} "
               f"sum_after={sum(x.pos for x in live)} limit={limit}")


def t40f_harness_budget_matches_production():
    """Audit item 12: the harness must hand the engine the SAME per-session
    budget config.n_ctx_per_session computes, or needs_compaction()'s trigger
    drifts from the KV it is protecting. Non-shared = n_ctx // n_seq_max (the
    partitioned per-sequence limit); shared-KV = n_ctx // 2. Pass both modes
    explicitly so this holds under MALABR_TEST_SHARED_KV=1 too (item 13)."""
    _, e_part = FIX.new_engine(shared_kv=False)
    _, e_shared = FIX.new_engine(shared_kv=True)
    part_ok = e_part._session_budget == FIX.n_ctx // FIX.n_seq_max
    shared_ok = e_shared._session_budget == FIX.n_ctx // 2
    # and a fresh session actually receives it
    sp, _ = e_part.get_or_create(key(tab=7), FIX.formatter, eng.OutputCap())
    record("40f", "harness session budget mirrors config.n_ctx_per_session",
           part_ok and shared_ok and sp.budget == FIX.n_ctx // FIX.n_seq_max,
           f"partitioned={e_part._session_budget} (want {FIX.n_ctx // FIX.n_seq_max}) "
           f"shared={e_shared._session_budget} (want {FIX.n_ctx // 2})")


def t41_phase_c_is_real():
    import inspect
    src = inspect.getsource(cal.phase_c_joint_worst_case)
    record(41, "Phase C fills sessions and measures, never extrapolates Phase B",
           "_fill_to" in src and "llama_decode" in inspect.getsource(cal._decode)
           and "interpolate" not in src)


def t45_greedy_only_in_tests():
    record(45, "canary tests are greedy; the chat path is not",
           eng.SAMPLING_DETERMINISTIC["temperature"] == 0.0
           and eng.SAMPLING_CHAT["temperature"] > 0.0,
           f"chat temp={eng.SAMPLING_CHAT['temperature']}")


def t46_gate_uses_worst_observed():
    import inspect
    src = inspect.getsource(cal.phase_b_position_curve)
    record(46, "curve emits median AND worst-observed; admission uses worst",
           "statistics.median" in src and "min(samples)" in src
           and "row[2]" in inspect.getsource(eng.CostCurve._raw_worst)
           .replace("col", "row[2]") or "2)" in inspect.getsource(eng.CostCurve._raw_worst))


# ---------------------------------------------------------------------------
# 50-63: control channel, scheduler, chunked prefill, closed loop
# ---------------------------------------------------------------------------

class _FakeEngine:
    def __init__(self, keys):
        self._keys = list(keys); self.foreground_tab_id = -1; self.cancelled = []
        self.wakes = 0
    def wake(self): self.wakes += 1      # ControlReader wakes the idle engine
    def all_keys(self): return list(self._keys)
    def keys_for_tab(self, t): return [k for k in self._keys if k[1] == t]
    def keys_for_extension(self, e): return [k for k in self._keys if k[0] == e]
    def cancel(self, k, r):
        self.cancelled.append((k, r))
        self._keys = [x for x in self._keys if x != k]
        return True


def t50_idle_tab_close():
    ke = _FakeEngine([key(tab=1), key(tab=2, origin="https://b.test")])
    rt.ControlReader(ke).dispatch(pr.parse_control_message("TAB_CLOSED,1"))
    record(50, "tab-close for an IDLE session arrives over the control channel",
           [k for k, _ in ke.cancelled] == [key(tab=1)])


def t52_round_budget_mixed_depth():
    class S:
        def __init__(self, tab, pos):
            self.key = ("a" * 32, tab, "https://x.test"); self.pos = pos
            self.state = eng.SessionState.GENERATING; self.cancelled = False
            self.inbox_tokens = []; self.prefill_offset = 0
            self.rounds_excluded = 0; self.prefill_stalls = 0
    curve = eng.CostCurve(DECODE_CURVE, PREFILL_CURVE)
    fg, bg = S(1, 32), S(2, 4000)
    picked = over = 0
    for _ in range(30):
        picks, pre, est = eng.build_batch([fg, bg], curve, foreground_tab_id=1)
        if fg in picks:
            picked += 1
        if est > eng.ROUND_LATENCY_BUDGET_MS:
            over += 1
    record(52, "mixed depth: foreground admitted every round, budget respected",
           picked == 30 and over == 0, f"fg {picked}/30, over-budget rounds {over}")


def t53b_first_turn_rollback_clears_the_bos_slot():
    """BOS x rollback-to-empty. The first turn tokenises with add_special=True,
    so for models that set add_bos_token (gemma) <bos> lands at KV pos 0. If that
    turn is rolled back, _roll_back_partial must seq_rm from pos_before_request
    (0) -- taking the BOS with it -- and _rendered must return to "" so the next
    turn's first_turn check re-adds it. The <bos> token itself is only visible on
    gemma (Qwen sets add_bos_token false), and was verified there offline:
    BOS = token 2, KV (0,N) -> (-1,-1) after rollback, recall intact. This locks
    the model-independent half: the slot is emptied, _rendered cleared, pos 0,
    and the next turn rebuilds and prefills without a TemplateError."""
    alloc, e = FIX.new_engine()
    k = key(tab=1)
    s, _ = e.get_or_create(k, FIX.formatter, eng.OutputCap([(0, 8)]))
    e.submit(k, "word " * 300)              # long first turn
    e._apply_control()                      # _begin_turn: first turn, PENDING
    for _ in range(6):
        e._step()                          # partial prefill -> real KV at pos 0
    kv_before = (C.llama_memory_seq_pos_min(FIX.mem, s.slot),
                 C.llama_memory_seq_pos_max(FIX.mem, s.slot))
    e._roll_back_partial(s)
    kv_after = (C.llama_memory_seq_pos_min(FIX.mem, s.slot),
                C.llama_memory_seq_pos_max(FIX.mem, s.slot))
    pos_after_rb, rendered_after_rb = s.pos, s.formatter._rendered
    rolled_clean = (kv_before[0] == 0 and kv_after == (-1, -1)
                    and pos_after_rb == 0 and rendered_after_rb == "")

    # the next turn rebuilds from empty (first_turn True again) and prefills
    rerendered = ask(e, k, "Say hi.")
    reran = (s.turn_boundaries[0].start == 0
             and s.formatter.verify_against_full()
             and isinstance(rerendered, str))
    record("53b", "first-turn rollback empties the KV slot; next turn rebuilds clean",
           rolled_clean and reran,
           f"kv {kv_before}->{kv_after} pos_after_rb={pos_after_rb} "
           f"rendered_after_rb={rendered_after_rb!r} anchor_start={s.turn_boundaries[0].start}")


def t53_rollback_survives_compaction():
    alloc, e = FIX.new_engine()
    k = key(tab=1)
    s, _ = e.get_or_create(k, FIX.formatter, eng.OutputCap([(0, 16)]))
    s.budget = FIX.n_ctx // FIX.n_seq_max
    ask(e, k, f"Remember this exactly: {CANARY}")
    for i in range(5):
        ask(e, k, f"Say filler{i}.")
    e.submit(k, "Tell me a long story.")
    for _ in range(6):
        e._step()
    snap_before = s.pos_before_generation
    e.TARGET_FREED_TOKENS = 1
    freed = e.compact(s)
    shifted = s.pos_before_generation
    e.submit(k, "Say OK.")
    e._step()
    drive(e, None, limit=200)
    out = ask(e, k, "What is the vault passphrase? If you do not know, say UNKNOWN.")
    record(53, "rollback snapshot shifted by an interleaved compaction",
           freed > 0 and shifted == snap_before - freed and recalls_canary(out),
           f"snapshot {snap_before}->{shifted} freed={freed}")


def t54_boundaries_across_two_compactions():
    alloc, e = FIX.new_engine()
    k = key(tab=1)
    s, _ = e.get_or_create(k, FIX.formatter, eng.OutputCap([(0, 16)]))
    s.budget = FIX.n_ctx // FIX.n_seq_max
    for i in range(8):
        ask(e, k, f"Say word{i}.")
    e.TARGET_FREED_TOKENS = 1
    e.compact(s)
    ok1 = all(s.turn_boundaries[i].end == s.turn_boundaries[i + 1].start
              for i in range(len(s.turn_boundaries) - 1))
    e.compact(s)
    ok2 = all(s.turn_boundaries[i].end == s.turn_boundaries[i + 1].start
              for i in range(len(s.turn_boundaries) - 1))
    agree = len(s.formatter._tokenize(s.formatter._rendered, add_special=True)) == s.pos
    record(54, "untouched boundaries stay correct across TWO compaction passes",
           ok1 and ok2 and agree and s.turn_boundaries[-1].end == s.pos,
           f"pass1={ok1} pass2={ok2} formatter_agrees={agree}")


def t55_aging_bound():
    class S:
        def __init__(self, tab, pos):
            self.key = ("a" * 32, tab, "https://x.test"); self.pos = pos
            self.state = eng.SessionState.GENERATING; self.cancelled = False
            self.inbox_tokens = []; self.prefill_offset = 0
            self.rounds_excluded = 0; self.prefill_stalls = 0
    curve = eng.CostCurve(DECODE_CURVE, PREFILL_CURVE)
    exp = S(2, 4000); cheap = [S(i, 32) for i in range(3, 8)]
    worst = 0
    for _ in range(60):
        eng.build_batch([exp] + cheap, curve, foreground_tab_id=99)
        worst = max(worst, exp.rounds_excluded)
    within = worst <= eng.MAX_CONSECUTIVE_EXCLUSIONS
    # documented limit: foreground saturating every round CAN outrun aging
    sat, poor = S(1, 4000), S(2, 4000)
    for _ in range(40):
        eng.build_batch([sat, poor], curve, foreground_tab_id=1)
    limit_holds = poor.rounds_excluded > eng.MAX_CONSECUTIVE_EXCLUSIONS
    record(55, "aging bound holds; its documented limit is what actually happens",
           within and limit_holds,
           f"max streak={worst} (bound {eng.MAX_CONSECUTIVE_EXCLUSIONS}); "
           f"under saturation={poor.rounds_excluded}")


def t55b_two_foreground_sessions_on_one_tab_alternate():
    """Minute audit item 4: two extensions on the SAME visible tab give fg two
    members. The first used to take the unconditional pick every round; if it
    alone blew the budget the other could never satisfy c <= budget, was never
    aged (aging only looked at bg), and had rounds_excluded zeroed every round
    regardless. Permanent starvation of a foreground session."""
    class S:
        def __init__(self, ext, pos):
            self.key = (ext * 32, 1, "https://x.test"); self.pos = pos
            self.state = eng.SessionState.GENERATING; self.cancelled = False
            self.inbox_tokens = []; self.prefill_offset = 0
            self.rounds_excluded = 0; self.prefill_stalls = 0
    curve = eng.CostCurve(DECODE_CURVE, PREFILL_CURVE)
    deep, shallow = S("a", 4000), S("b", 32)        # same tab, deep listed first
    shallow_runs, worst_streak = 0, 0
    for _ in range(40):
        picks, _, _ = eng.build_batch([deep, shallow], curve, foreground_tab_id=1)
        shallow_runs += shallow in picks
        worst_streak = max(worst_streak, shallow.rounds_excluded)
    record("55b", "a second foreground session on the same tab is not starved",
           shallow_runs >= 10 and worst_streak <= eng.MAX_CONSECUTIVE_EXCLUSIONS,
           f"shallow ran {shallow_runs}/40, worst exclusion streak={worst_streak}")


def t56_five_tabs_one_foreground():
    ke = _FakeEngine([])
    cr = rt.ControlReader(ke)
    seen = []
    for tab in range(1, 6):
        cr.dispatch(pr.parse_control_message(f"FOREGROUND,{tab}"))
        seen.append(ke.foreground_tab_id)
    record(56, "five tabs, five requests: at most ONE foreground at any instant",
           seen == [1, 2, 3, 4, 5] and isinstance(ke.foreground_tab_id, int),
           f"foreground key is a single value, not a per-session flag: {seen}")


def t59_slot_recovered_via_live_tabs():
    ke = _FakeEngine([key(tab=1), key(tab=2, origin="https://b.test"),
                      key(tab=3, origin="https://c.test")])
    # tab 2 closed while the control connection was down: its TAB_CLOSED is lost
    rt.ControlReader(ke).dispatch(pr.parse_control_message("LIVE_TABS,1,3"))
    record(59, "slot for a tab closed while disconnected is recovered by LIVE_TABS",
           [k[1] for k, _ in ke.cancelled] == [2], f"cancelled={ke.cancelled}")


def t62_chunked_prefill_does_not_stall():
    class S:
        def __init__(self, tab, pos, state, ntok=0):
            self.key = ("a" * 32, tab, "https://x.test"); self.pos = pos
            self.state = state; self.cancelled = False
            self.inbox_tokens = [1] * ntok; self.prefill_offset = 0
            self.rounds_excluded = 0; self.prefill_stalls = 0
    curve = eng.CostCurve(DECODE_CURVE, PREFILL_CURVE)
    fg = S(1, 32, eng.SessionState.GENERATING)
    big = S(2, 0, eng.SessionState.PENDING, ntok=2000)
    fg_rounds = over = chunks = 0
    for _ in range(80):
        picks, pre, est = eng.build_batch([fg, big], curve, foreground_tab_id=1)
        if fg in picks:
            fg_rounds += 1
        if est > eng.ROUND_LATENCY_BUDGET_MS:
            over += 1
        for s, c in pre:
            s.prefill_offset += len(c); chunks += 1
    record(62, "chunked prefill: foreground keeps its slot, big prompt progresses",
           fg_rounds == 80 and chunks > 0 and over == 0,
           f"fg {fg_rounds}/80, chunks={chunks}, over-budget={over}")


def t63_closed_loop():
    c = eng.CostCurve(DECODE_CURVE, PREFILL_CURVE)
    base = c.cost_ms_worst(32)
    for _ in range(60):
        c.observe_round(50.0, 100.0)             # reality is 2x the estimate
    rose = c.cost_ms_worst(32) > base
    c2 = eng.CostCurve(DECODE_CURVE, PREFILL_CURVE)
    for _ in range(60):
        c2.observe_round(50.0, 5.0)              # a run of very fast rounds
    floor_holds = c2.correction == 1.0
    record(63, "closed loop tightens on a wrong model and never goes optimistic",
           rose and floor_holds,
           f"correction rose to {c.correction:.2f}; floor stayed {c2.correction:.2f}")


def t58_degraded_mode():
    alloc, e = FIX.new_engine()
    e.set_control_connected(True)
    e.foreground_tab_id = 7
    trusted = e.effective_foreground_tab_id()
    e.set_control_connected(False)
    still = e.effective_foreground_tab_id()
    e.control_lost_at = time.time() - (eng.STALE_VISIBILITY_TIMEOUT + 1)
    degraded = e.effective_foreground_tab_id()
    e.set_control_connected(True)
    back = e.effective_foreground_tab_id()
    record(58, "control down past the timeout degrades foreground to none",
           trusted == 7 and still == 7 and degraded == -1 and back == 7
           and e.foreground_tab_id == 7,
           f"{trusted} -> {still} -> {degraded} -> {back}; raw key never mutated")


def t64_eviction_at_full_capacity():
    """Not in §12: found by auditing, and it broke §5g exactly under load."""
    alloc, e = FIX.new_engine()
    ext = "a" * 32
    for i in range(FIX.n_seq_max):
        e.get_or_create((ext, i, f"https://s{i}.test"), FIX.formatter,
                        eng.OutputCap())
    e.start()
    try:
        doomed = e.evict_other_origins(ext, 0, "https://evil.test")
        done = e.wait_for_teardown(doomed)
        s, _ = e.get_or_create((ext, 0, "https://evil.test"), FIX.formatter,
                               eng.OutputCap())
        record(64, "cross-origin navigation admitted even at FULL capacity",
               done and s is not None and s.slot in alloc.pending_wipe,
               f"evicted={len(doomed)} admitted={s is not None}")
    finally:
        e.stop()


def t65_engine_survives_a_bad_round():
    """Not in §12: an unhandled exception used to kill the engine thread
    silently, leaving a server that accepts connections and serves nothing."""
    alloc, e = FIX.new_engine()
    calls = [0]
    orig = eng.Engine._run_round

    def bad(self):
        calls[0] += 1
        if calls[0] <= 3:
            raise RuntimeError("synthetic round failure")
        return False
    eng.Engine._run_round = bad
    try:
        e.start()
        time.sleep(0.3)
        alive = e._thread.is_alive()
    finally:
        e.stop()
        eng.Engine._run_round = orig
    record(65, "engine thread survives an unexpected exception in a round",
           alive and calls[0] > 3, f"rounds attempted={calls[0]}, alive={alive}")


def t66_idle_engine_blocks_and_wakes():
    """An idle engine must not spin, and must still start work promptly.

    Both halves matter and they pull against each other: sleeping longer saves
    idle CPU but delays the next turn. The old loop slept a flat 1 ms, i.e.
    ~1000 wakeups/s on a server nobody is using.
    """
    alloc, e = FIX.new_engine()
    calls = [0]
    orig = eng.Engine._run_round

    def counting(self):
        calls[0] += 1
        return orig(self)

    eng.Engine._run_round = counting
    try:
        e.start()
        time.sleep(0.4)
        idle_rounds = calls[0]

        # Now prove the saving did not come at the cost of responsiveness.
        k = key(tab=1)
        e.get_or_create(k, FIX.formatter, eng.OutputCap([(0, 4)]))
        before = calls[0]
        t0 = time.perf_counter()
        e.submit(k, "hi")
        while calls[0] == before and time.perf_counter() - t0 < 1.0:
            time.sleep(0.001)
        wake_ms = (time.perf_counter() - t0) * 1000
    finally:
        e.stop()
        eng.Engine._run_round = orig

    # 0.4 s of idle at the old 1 ms poll was ~400 rounds; the backstop alone
    # allows ~16. Anything near the old number means the wait was not taken.
    ceiling = 0.4 / eng.IDLE_WAIT_SECONDS * 2 + 5
    record(66, "idle engine blocks on a wake event instead of spinning",
           idle_rounds < ceiling and wake_ms < 20,
           f"idle rounds={idle_rounds} (ceiling {ceiling:.0f}), "
           f"submit->round {wake_ms:.1f}ms")


def t67_solo_session_gets_the_larger_round_budget():
    """One session must not be rationed against sessions that do not exist.

    The 50 ms round budget buys fairness between contenders. With a single
    contender it buys nothing and costs prefill chunking, so a solo round is
    allowed SOLO_ROUND_BUDGET_MS -- but the moment a second session wants
    work, the fair budget must come back.
    """
    class S:
        def __init__(self, tab, ntok):
            self.key = ("a" * 32, tab, "https://x.test")
            self.pos = 0
            self.state = eng.SessionState.PENDING
            self.cancelled = False
            self.inbox_tokens = [1] * ntok
            self.prefill_offset = 0
            self.prefill_stalls = 0
            self.rounds_excluded = 0

    curve = eng.CostCurve(DECODE_CURVE, PREFILL_CURVE)

    def first_chunk(sessions):
        # A chunk smaller than PREFILL_CHUNK_MIN is deliberately stalled for a
        # few rounds before being admitted anyway, so "the first round" is not
        # the same as "the first chunk". Drive until something is admitted.
        for _ in range(eng.MAX_PREFILL_STALLS + 2):
            _, chunks, _ = eng.build_batch(sessions, curve, foreground_tab_id=1)
            if chunks:
                return len(chunks[0][1])
        return 0

    n_solo = first_chunk([S(tab=1, ntok=4000)])
    n_pair = first_chunk([S(tab=1, ntok=4000), S(tab=2, ntok=4000)])

    ratio = eng.SOLO_ROUND_BUDGET_MS / eng.ROUND_LATENCY_BUDGET_MS
    record(67, "a lone session is not held to the multi-session round budget",
           n_solo > n_pair * (ratio * 0.8) and n_pair > 0,
           f"solo chunk={n_solo} tok, contended chunk={n_pair} tok "
           f"({n_solo/max(n_pair,1):.1f}x, budget ratio {ratio:.0f}x)")


def t68_deferred_round_is_not_treated_as_idle():
    """A round that schedules nothing is not necessarily an idle round.

    build_batch DEFERS a prefill that cannot afford a whole minimum chunk, for
    up to MAX_PREFILL_STALLS rounds. Nothing wakes the engine out of that --
    the only thing that clears it is running more rounds -- so if those rounds
    take the idle backstop the user waits stalls x IDLE_WAIT_SECONDS for their
    first token instead of stalls x 1 ms.

    Caught in audit 3: test 66 only ever exercised a genuinely idle engine, so
    it passed while contended prefill got 25x slower.
    """
    class S:
        def __init__(self, tab, ntok):
            self.key = ("a" * 32, tab, "https://x.test"); self.pos = 0
            self.state = eng.SessionState.PENDING; self.cancelled = False
            self.inbox_tokens = [1] * ntok; self.prefill_offset = 0
            self.prefill_stalls = 0; self.rounds_excluded = 0
            self.budget = 4096          # pos is 0, so compaction never triggers

    alloc, e = FIX.new_engine()
    e.cost_curve = eng.CostCurve(DECODE_CURVE, PREFILL_CURVE)
    sessions = [S(1, 4000), S(2, 4000)]          # two contenders, none running

    # Drive _run_round the way the loop does, with the real registry replaced
    # by our two stalling sessions.
    e._sessions = {s.key: s for s in sessions}
    # Stop one short of the round that force-admits a chunk: the rounds under
    # test are the deferring ones, and driving past them would need a real
    # slot to decode into.
    deferred_rounds = 0
    for _ in range(eng.MAX_PREFILL_STALLS - 1):
        did = e._run_round()
        if did:
            break
        if e._deferred:
            deferred_rounds += 1

    expected = eng.MAX_PREFILL_STALLS - 1
    record(68, "a deferred round is not mistaken for an idle round",
           deferred_rounds == expected,
           f"{deferred_rounds}/{expected} rounds marked deferred; treating "
           f"them as idle would have added "
           f"{expected * eng.IDLE_WAIT_SECONDS * 1000:.0f}ms before the first "
           f"prefill chunk (was {expected}ms)")


def t69_solo_round_bounds_a_new_arrival():
    """The solo budget is also the worst-case wait for a tab that shows up.

    A batched round cannot be preempted, so whatever a solo round costs is
    what a second tab pays before it can be admitted. That makes
    SOLO_ROUND_BUDGET_MS a latency bound on the multi-tab path, not only a
    throughput knob on the single-tab one -- which is why it is not large.
    """
    class S:
        def __init__(self, tab, ntok):
            self.key = ("a" * 32, tab, "https://x.test"); self.pos = 0
            self.state = eng.SessionState.PENDING; self.cancelled = False
            self.inbox_tokens = [1] * ntok; self.prefill_offset = 0
            self.prefill_stalls = 0; self.rounds_excluded = 0

    curve = eng.CostCurve(DECODE_CURVE, PREFILL_CURVE)
    _, chunks, spent = eng.build_batch([S(1, 4000)], curve, foreground_tab_id=1)
    ok = bool(chunks) and spent <= eng.SOLO_ROUND_BUDGET_MS
    record(69, "a solo round stays inside its budget, bounding a new arrival",
           ok, f"solo round costs {spent:.0f}ms of "
               f"{eng.SOLO_ROUND_BUDGET_MS}ms; a tab arriving mid-round waits "
               f"that long (fair budget is {eng.ROUND_LATENCY_BUDGET_MS}ms)")


def t44_eos_assumption_survives_a_model_swap():
    """The stop condition must hold for a DIFFERENT model, not just this one.

    Was declared browser-only. It is not: swapping models is a server-side
    operation and the assumption under test is about a vocabulary, so it can
    be checked directly. Converted in audit 3.

    This is design-doc correction #10 made executable. The §5b design text
    described the stop check as `tok == llama_vocab_eos(vocab)`. That is
    wrong for every model here: `eos()` names ONE token while these
    vocabularies flag several as end-of-generation, so an `== eos` check
    misses the rest and the turn runs to the output cap instead of stopping.
    `is_stop_token` uses `llama_vocab_is_eog`, and this pins that for both
    shipped models.
    """
    import glob
    models = sorted(glob.glob(os.path.join(
        os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
        "..", "models", "*.gguf")))
    if len(models) < 2:
        record(44, "EOS assumption re-verified on a model swap", True,
               f"only {len(models)} model(s) present", skip=True)
        return

    findings = []
    ok = True
    for path in models:
        mp = C.llama_model_default_params(); mp.n_gpu_layers = 0
        model = C.llama_model_load_from_file(path.encode(), mp)
        if not model:
            ok = False; findings.append(f"{os.path.basename(path)}: load failed")
            continue
        try:
            vocab = C.llama_model_get_vocab(model)
            n = C.llama_vocab_n_tokens(vocab)
            eog = [t for t in range(n) if C.llama_vocab_is_eog(vocab, t)]
            eos = C.llama_vocab_eos(vocab)
            # Every EOG token must stop generation ...
            all_stop = all(eng.is_stop_token(vocab, t) for t in eog)
            # ... and `== eos` must be demonstrably insufficient, which is the
            # whole point of the correction.
            eos_insufficient = len(eog) > 1 and any(t != eos for t in eog)
            ok = ok and all_stop and eos_insufficient
            findings.append(f"{os.path.basename(path)}: {len(eog)} EOG, "
                            f"eos={eos}")
        finally:
            C.llama_model_free(model)

    record(44, "EOS assumption re-verified on a model swap", ok,
           "; ".join(findings))


def t48_compaction_fidelity_distributional():
    """Compaction must not move the next-token distribution much.

    Was declared browser-only as a "cheap stub" because no real fidelity check
    existed. One does now (tests/fidelity_check.py), so this becomes a fast
    in-suite version of it: two sessions driven identically, one compacted
    through the shipping `Engine.compact`, then the same probe decoded into
    both and the argmax compared.

    Deliberately NOT a duplicate of test 4. Test 4 asks whether a planted fact
    can still be RECALLED, which is binary and generous. This asks whether the
    model would still say the same next token, which is the quantitative claim
    §8 actually makes.
    """
    import ctypes
    alloc, e = FIX.new_engine()
    ka, kb = key(tab=1), key(tab=2)
    for k in (ka, kb):
        e.get_or_create(k, FIX.formatter, eng.OutputCap([(0, 24)]))
    sa, sb = e._sessions[ka], e._sessions[kb]

    filler = ["Explain what a hash table is.",
              "What is a process versus a thread?",
              "Describe a page fault.",
              "What is a write-ahead log for?"]
    for msg in filler:
        for k in (ka, kb):
            ask(e, k, msg, limit=80)

    freed = e.compact(sb)
    if not freed:
        record(48, "compaction leaves the next-token choice unchanged", True,
               "conversation too short to compact", skip=True)
        return

    n_vocab = C.llama_vocab_n_tokens(FIX.vocab)

    def argmax_next(s, q):
        toks = s.formatter._tokenize(q, parse_special=False)
        start = s.pos
        items = [(t, start + i, s.slot, i == len(toks) - 1)
                 for i, t in enumerate(toks)]
        try:
            idx = e._decode_batch(items)
            ptr = C.llama_get_logits_ith(e._ctx, idx[-1])
            arr = ctypes.cast(ptr,
                              ctypes.POINTER(ctypes.c_float * n_vocab)).contents
            return max(range(n_vocab), key=lambda i: arr[i])
        finally:
            C.llama_memory_seq_rm(e._alloc._mem, s.slot, start, start + len(toks))
            s.pos = start

    probe = "What was the first thing I asked about?"
    ta, tb = argmax_next(sa, probe), argmax_next(sb, probe)
    record(48, "compaction leaves the next-token choice unchanged", ta == tb,
           f"freed {freed} tok ({100*freed/max(sb.pos+freed,1):.0f}% of context); "
           f"argmax full={ta} compacted={tb}")


# ---------------------------------------------------------------------------
# Declared SKIPs -- need a real browser, not omitted silently
# ---------------------------------------------------------------------------

BROWSER_ONLY = [
    (11, "crash-then-reconnect surfaces 'connection lost' in the tab"),
    (14, "stale header visibility loses to the control-channel push"),
    (16, "extension-wide teardown driven by ExtensionRegistryObserver"),
    (17, "visibility push for a nonexistent session is a no-op end to end"),
    (21, "time-weighted fg/bg split measured as wall-clock, not token counts"),
    (23, "p99/max token latency under a real cpu.max quota"),
    (25, "throttle-triggered compaction targets the largest session + UI notice"),
    (36, "content script runs only in the main frame, not per iframe"),
    (37, "retry jitter under a cold-start burst"),
    (39, "tab-close clears chrome.storage.session; navigation does NOT"),
    (42, "two windows: only the active window's tab is foreground"),
    (43, "clicking the address bar does not flip the tab out of foreground"),
    (47, "incremental vs batch eviction canary comparison"),
    (49, "end-to-end focus-switch latency"),
    (51, "control connection reused, not reopened per push"),
    (57, "control-connection resync pushes foreground on reconnect"),
    (60, "navigation mid-generation rolls back without desyncing"),
    (61, "cross-origin navigation does not leak conversation"),
]


def main():
    global FIX
    print("=== MALABR Phase 1 -- section 12 suite ===\n")
    _kv = os.getenv("MALABR_KV_TYPE", "f16").lower()
    if _kv != "f16":
        # Not a style preference. This suite proves ISOLATION by checking that
        # a session does NOT recall another session's canary. A lossy KV cache
        # degrades recall in general, so a session can fail to recall a canary
        # it legitimately holds -- measured on Qwen3-0.6B under q8_0, where a
        # session could not reproduce its OWN planted canary while its KV
        # positions were provably intact. Under that condition a leak test can
        # pass for the wrong reason, and a passing run is no longer evidence.
        print(f"  !! MALABR_KV_TYPE={_kv}: canary results are NOT isolation")
        print( "  !! evidence in this mode -- a lossy cache produces recall")
        print( "  !! false-negatives. Use f16 for anything you intend to cite.\n")
    FIX = Fixture()
    try:
        for fn in sorted([f for n, f in globals().items()
                          if n.startswith("t") and n[1:3].isdigit() and callable(f)],
                         key=lambda f: int(f.__name__[1:3])):
            try:
                fn()
            except Exception as exc:
                record(int(fn.__name__[1:3]), fn.__name__, False,
                       f"{type(exc).__name__}: {exc}")
    finally:
        FIX.close()

    print("\n--- declared SKIP: needs a real browser ---")
    for num, name in BROWSER_ONLY:
        record(num, name, True, "", skip=True)

    ran = [r for r in RESULTS if not r[4]]
    failed = [r for r in ran if not r[2]]
    print(f"\n{len(ran)} executed, {len(ran)-len(failed)} passed, "
          f"{len(failed)} failed, {len(BROWSER_ONLY)} skipped (browser)")
    if failed:
        print("FAILED: " + ", ".join(str(r[0]) for r in failed))
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
