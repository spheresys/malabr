"""Startup calibration -- see design_doc/phase1_design.md section 11a.

Rationale: NOTES.md, calibration
"""

import json
import os
import statistics
import time

import llama_cpp.llama_cpp as C

from .config import MAX_N_CTX, MIN_N_CTX, QUOTA_PCT, detect_physical_cores

CALIBRATION_VERSION = 2

# Phase D: no single response should take longer than this to generate.
TARGET_MAX_RESPONSE_SECONDS = 15

# Phase C/D gate: below this, the configuration will throttle badly under
# ordinary multi-tab use and n_seq_max or n_ctx should come down first.
MIN_ACCEPTABLE_AGGREGATE_TPS = 4.0

# Section 11a measured a real 19% trial-to-trial spread at short context even
# after taking a median of 3. Three is the floor, not a comfortable margin.
TRIALS_PER_POINT = 3


class CalibrationError(RuntimeError):
    pass


# ----------------------------------------------------------------...  [notes: calibration._decode]

def _decode(ctx, tokens, pos0, seq_ids, want_last_logits=True):
    """Decode `tokens` into every sequence in seq_ids at pos0..

    Rationale: NOTES.md, calibration._decode
    """
    n = len(tokens) * len(seq_ids)
    batch = C.llama_batch_init(n, 0, 1)
    try:
        i = 0
        for seq in seq_ids:
            for j, tok in enumerate(tokens):
                batch.token[i] = tok
                batch.pos[i] = pos0 + j
                batch.n_seq_id[i] = 1
                batch.seq_id[i][0] = seq
                batch.logits[i] = 1 if (want_last_logits and j == len(tokens) - 1) else 0
                i += 1
        batch.n_tokens = n
        rc = C.llama_decode(ctx, batch)
        if rc != 0:
            raise CalibrationError(f"llama_decode failed rc={rc}")
    finally:
        C.llama_batch_free(batch)


def _fill_to(ctx, vocab, seq, target_pos, chunk=256):
    """Grow one sequence to target_pos with filler tokens."""
    C.llama_memory_seq_rm(C.llama_get_memory(ctx), seq, -1, -1)
    pos = 0
    filler = [_safe_token(vocab)] * chunk
    while pos < target_pos:
        n = min(chunk, target_pos - pos)
        _decode(ctx, filler[:n], pos, [seq], want_last_logits=(pos + n >= target_pos))
        pos += n
    return pos


# Keyed by the vocab handle, not a bare global  [notes: calibration.(module)]
_SAFE_TOKEN = {}


def _safe_token(vocab):
    """A single ordinary token to use as filler. Cached per vocab."""
    key = int(vocab) if not isinstance(vocab, int) else vocab
    tok = _SAFE_TOKEN.get(key)
    if tok is None:
        buf = (C.llama_token * 8)()
        n = C.llama_tokenize(vocab, b" the", 4, buf, 8, False, False)
        if n <= 0:
            raise CalibrationError("could not tokenize filler")
        tok = _SAFE_TOKEN[key] = int(buf[0])
    return tok


def _time_decode_tokens(ctx, vocab, seq, pos, n_tokens):
    """Time n_tokens of single-token decode at `pos`. Returns tok/s."""
    tok = _safe_token(vocab)
    t0 = time.perf_counter()
    for k in range(n_tokens):
        _decode(ctx, [tok], pos + k, [seq])
    dt = time.perf_counter() - t0
    if dt <= 0:
        raise CalibrationError("non-positive elapsed time")
    return n_tokens / dt


# ----------------------------------------------------------------...  [notes: calibration.phase_a_threads]

def phase_a_threads(model_path, candidates=None, probe_tokens=24, n_ctx=1024):
    """Sweep n_threads and pick the measured optimum.

    Rationale: NOTES.md, calibration.phase_a_threads
    """
    cores = detect_physical_cores()
    if candidates is None:
        candidates = sorted({1, 2, 4, cores})
    results = {}
    # n_threads is a CONTEXT parameter, so the model -- the expensive...  [notes: calibration.phase_a_threads]
    mp = C.llama_model_default_params(); mp.n_gpu_layers = 0
    model = C.llama_model_load_from_file(model_path.encode(), mp)
    if not model:
        raise CalibrationError(f"could not load model at {model_path}")
    try:
        vocab = C.llama_model_get_vocab(model)
        for n in candidates:
            if n < 1:
                continue
            cp = C.llama_context_default_params()
            cp.n_ctx, cp.n_seq_max, cp.n_batch, cp.n_threads = n_ctx, 1, 512, n
            ctx = C.llama_init_from_model(model, cp)
            if not ctx:
                raise CalibrationError("context creation failed")
            try:
                _fill_to(ctx, vocab, 0, 32)
                results[n] = _time_decode_tokens(ctx, vocab, 0, 32, probe_tokens)
            finally:
                C.llama_free(ctx)
    finally:
        C.llama_model_free(model)
    if not results:
        raise CalibrationError("no thread candidates measured")
    best = max(results, key=results.get)
    # The deployed value is capped by the quota regardless of what measured
    # fastest: an unthrottled optimum is not achievable under the runtime quota.
    quota_threads = max(1, round(cores * QUOTA_PCT / 100))
    return {"measured": {str(k): v for k, v in results.items()},
            "fastest": best,
            "chosen": min(best, quota_threads),
            "quota_threads": quota_threads,
            "physical_cores": cores}


def phase_b_position_curve(ctx, vocab, positions, trials=TRIALS_PER_POINT,
                           probe_tokens=16, seq=0):
    """Decode tok/s at several depths. Emits BOTH statistics.

    Rationale: NOTES.md, calibration.phase_b_position_curve
    """
    rows = []
    mem = C.llama_get_memory(ctx)
    for pos in positions:
        _fill_to(ctx, vocab, seq, pos)
        samples = []
        for _ in range(trials):
            # Truncate back to `pos` before EVERY trial  [notes: calibration.phase_b_position_curve]
            C.llama_memory_seq_rm(mem, seq, pos, -1)
            samples.append(_time_decode_tokens(ctx, vocab, seq, pos, probe_tokens))
        rows.append([int(pos),
                     float(statistics.median(samples)),
                     float(min(samples))])          # worst observed
    rows.sort()
    return rows


def phase_b_prefill_curve(ctx, vocab, sizes, depths, trials=2, seq=1):
    """Prompt-processing throughput. A SEPARATE curve, not the decode one.

    Rationale: NOTES.md, calibration.phase_b_prefill_curve
    """
    tok = _safe_token(vocab)
    rows = []
    for depth in depths:
        best_tps = None
        for size in sizes:
            for _ in range(trials):
                _fill_to(ctx, vocab, seq, depth)
                t0 = time.perf_counter()
                _decode(ctx, [tok] * size, depth, [seq])
                dt = time.perf_counter() - t0
                if dt <= 0:
                    continue
                tps = size / dt
                best_tps = tps if best_tps is None else min(best_tps, tps)
        if best_tps is not None:
            rows.append([int(depth), float(best_tps)])
    rows.sort()
    return rows


def phase_c_joint_worst_case(ctx, vocab, n_seq_max, depth, probe_tokens=8):
    """Every slot full, every session at depth, batched, under the real quota.

    Rationale: NOTES.md, calibration.phase_c_joint_worst_case
    """
    seqs = list(range(n_seq_max))
    for s in seqs:
        _fill_to(ctx, vocab, s, depth)
    tok = _safe_token(vocab)
    t0 = time.perf_counter()
    for k in range(probe_tokens):
        _decode(ctx, [tok], depth + k, seqs)
    dt = time.perf_counter() - t0
    if dt <= 0:
        raise CalibrationError("non-positive elapsed time in phase C")
    total = probe_tokens * len(seqs)
    return {"depth": int(depth), "n_seq_max": int(n_seq_max),
            "aggregate_tps": total / dt,
            "per_session_tps": (total / dt) / len(seqs)}


# ----------------------------------------------------------------...  [notes: calibration._interpolate]

def _interpolate(rows, pos, col):
    lo = rows[0]
    for row in rows:
        if row[0] <= pos:
            lo = row
        else:
            span = row[0] - lo[0]
            if span <= 0:
                return lo[col]
            f = (pos - lo[0]) / span
            return lo[col] + f * (row[col] - lo[col])
    return lo[col]


def derive_output_cap_table(decode_rows, min_cap, absolute_ceiling,
                            breakpoints=None):
    """FINISHED lookup table, not raw tok/s for someone else to interpret.

    Rationale: NOTES.md, calibration.derive_output_cap_table
    """
    if breakpoints is None:
        breakpoints = [r[0] for r in decode_rows]
    table = []
    for pos in breakpoints:
        tps = _interpolate(decode_rows, pos, 1)
        cap = TARGET_MAX_RESPONSE_SECONDS * tps
        # Phase G's ceiling applies HERE and again at lookup time in the
        # engine. Two gates, because a table can also be hand-edited.
        table.append([int(pos), int(max(min_cap, min(cap, absolute_ceiling)))])
    return table


def phase_d_assemble(threads, decode_rows, prefill_rows, joint, n_ctx,
                     n_seq_max, min_cap, absolute_ceiling):
    """Assemble outputs and run the pass/fail gate."""
    warnings = []
    passed = True
    if joint and joint["aggregate_tps"] < MIN_ACCEPTABLE_AGGREGATE_TPS:
        passed = False
        warnings.append(
            f"joint worst case {joint['aggregate_tps']:.1f} tok/s is below the "
            f"{MIN_ACCEPTABLE_AGGREGATE_TPS} tok/s service floor; reduce "
            f"n_seq_max or n_ctx before starting")
    if len(decode_rows) < 2:
        warnings.append("fewer than 2 curve points; interpolation is degenerate")

    return {
        "version": CALIBRATION_VERSION,
        "measured_at": time.time(),
        "passed": passed,
        "warnings": warnings,
        "n_threads": threads["chosen"],
        "n_threads_detail": threads,
        "n_ctx": int(n_ctx),
        "n_seq_max": int(n_seq_max),
        "quota_pct": QUOTA_PCT,
        # [pos, tps_median, tps_worst] -- exactly the shape CostCurve wants
        "decode_curve": decode_rows,
        # [pos, tok_per_s_worst]
        "prefill_curve": prefill_rows,
        "joint_worst_case": joint,
        "output_cap_table": derive_output_cap_table(
            decode_rows, min_cap, absolute_ceiling),
    }


# ----------------------------------------------------------------...  [notes: calibration.phase_f_clamp]

def phase_f_clamp(result, min_cap, absolute_ceiling):
    """Distinct from Phase E, which covers calibration ERRORING.

    Rationale: NOTES.md, calibration.phase_f_clamp
    """
    cores = detect_physical_cores()
    result["n_threads"] = max(1, min(int(result.get("n_threads") or 1), cores))
    n_ctx = int(result.get("n_ctx") or MIN_N_CTX)
    result["n_ctx"] = max(MIN_N_CTX, min(n_ctx, MAX_N_CTX))
    result["n_seq_max"] = max(1, int(result.get("n_seq_max") or 1))

    clean = []
    for row in result.get("decode_curve") or []:
        try:
            pos, med, worst = int(row[0]), float(row[1]), float(row[2])
        except (TypeError, ValueError, IndexError):
            continue
        if pos < 0 or med <= 0 or worst <= 0:
            continue
        # Worst can never be better than median; if it is, the two were
        # swapped or miscomputed somewhere upstream.
        clean.append([pos, med, min(worst, med)])
    result["decode_curve"] = sorted(clean)

    pre = []
    for row in result.get("prefill_curve") or []:
        try:
            pos, tps = int(row[0]), float(row[1])
        except (TypeError, ValueError, IndexError):
            continue
        if pos >= 0 and tps > 0:
            pre.append([pos, tps])
    result["prefill_curve"] = sorted(pre)

    capped = []
    for row in result.get("output_cap_table") or []:
        try:
            pos, cap = int(row[0]), int(row[1])
        except (TypeError, ValueError, IndexError):
            continue
        # Phase G, applied again: the ceiling holds whatever the table says.
        capped.append([max(0, pos), max(min_cap, min(cap, absolute_ceiling))])
    result["output_cap_table"] = sorted(capped)
    return result


# ----------------------------------------------------------------...  [notes: calibration.save]

def save(result, path):
    """Write via temp file + rename. Atomic on the same filesystem.

    Rationale: NOTES.md, calibration.save
    """
    tmp = f"{path}.{os.getpid()}.tmp"
    directory = os.path.dirname(path) or "."
    os.makedirs(directory, exist_ok=True)
    with open(tmp, "w") as fh:
        json.dump(result, fh, indent=2)
        fh.flush()
        os.fsync(fh.fileno())          # rename is atomic; the CONTENT still
                                       # has to be on disk before it happens
    os.replace(tmp, path)
    return path


def load(path):
    """Return a stored result, or None.

    Rationale: NOTES.md, calibration.load
    """
    try:
        with open(path) as fh:
            data = json.load(fh)
    except (OSError, ValueError):
        return None
    if not isinstance(data, dict) or data.get("version") != CALIBRATION_VERSION:
        return None
    if not data.get("decode_curve"):
        return None
    return data


def conservative_fallback(n_ctx, n_seq_max, min_cap, absolute_ceiling):
    """Phase E: what to use when calibration itself fails.

    Rationale: NOTES.md, calibration.conservative_fallback
    """
    return {
        "version": CALIBRATION_VERSION,
        "measured_at": time.time(),
        "passed": False,
        "warnings": ["calibration failed; conservative fallback in use"],
        "n_threads": 1,
        # Same SHAPE as a measured result, deliberately  [notes: calibration.conservative_fallback]
        "n_threads_detail": {"measured": {}, "fastest": 1, "chosen": 1,
                             "quota_threads": 1,
                             "physical_cores": detect_physical_cores()},
        "n_ctx": int(n_ctx),
        "n_seq_max": int(n_seq_max),
        "quota_pct": QUOTA_PCT,
        "decode_curve": [[0, 10.0, 5.0], [2048, 5.0, 2.5]],
        "prefill_curve": [[0, 200.0]],
        "joint_worst_case": None,
        "output_cap_table": [[0, min_cap]],
        "fallback": True,
    }


# ----------------------------------------------------------------...  [notes: calibration.run]

def run(cfg, min_cap, absolute_ceiling, quick=False, ctx=None, model=None):
    """Run every phase and return a clamped, ready-to-use result.

    Rationale: NOTES.md, calibration.run
    """
    budget = cfg.n_ctx_per_session
    if quick:
        positions = [32, min(256, budget - 1)]
        prefill_sizes, prefill_depths = [32], [32]
        joint_depth = 64
        trials = 1
    else:
        # Span the intended per-session range, 5-6 points, per section 11a.
        positions = sorted({32, budget // 8, budget // 4, budget // 2,
                            int(budget * 0.75), budget - 64})
        positions = [p for p in positions if 0 < p < budget]
        prefill_sizes, prefill_depths = [64, 256], [32, budget // 2]
        joint_depth = min(budget // 2, 1500)
        trials = TRIALS_PER_POINT

    owns = ctx is None
    try:
        threads = (phase_a_threads(cfg.model_path)
                   if not quick else
                   {"measured": {}, "fastest": cfg.n_threads,
                    "chosen": cfg.n_threads,
                    "quota_threads": cfg.n_threads,
                    "physical_cores": detect_physical_cores()})

        if owns:
            mp = C.llama_model_default_params(); mp.n_gpu_layers = 0
            model = C.llama_model_load_from_file(cfg.model_path.encode(), mp)
            if not model:
                raise CalibrationError("model load failed")
            cp = C.llama_context_default_params()
            cp.n_ctx = cfg.n_ctx
            cp.n_seq_max = cfg.n_seq_max
            cp.n_batch = cfg.n_batch
            cp.n_threads = threads["chosen"]
            ctx = C.llama_init_from_model(model, cp)
            if not ctx:
                raise CalibrationError("context creation failed")
        vocab = C.llama_model_get_vocab(model)

        decode_rows = phase_b_position_curve(ctx, vocab, positions, trials=trials)
        prefill_rows = phase_b_prefill_curve(ctx, vocab, prefill_sizes,
                                             prefill_depths, trials=1)
        joint = phase_c_joint_worst_case(ctx, vocab, cfg.n_seq_max, joint_depth)

        result = phase_d_assemble(threads, decode_rows, prefill_rows, joint,
                                  cfg.n_ctx, cfg.n_seq_max, min_cap,
                                  absolute_ceiling)
    except Exception as exc:                      # Phase E
        result = conservative_fallback(cfg.n_ctx, cfg.n_seq_max, min_cap,
                                       absolute_ceiling)
        result["warnings"].append(f"{type(exc).__name__}: {exc}")
    finally:
        if owns:
            if ctx:
                C.llama_free(ctx)
            if model:
                C.llama_model_free(model)

    # Phase F always runs, on measured AND fallback results alike.
    return phase_f_clamp(result, min_cap, absolute_ceiling)


def load_or_run(cfg, min_cap, absolute_ceiling, quick=False, force=False):
    """Use stored calibration if usable, otherwise measure and store it."""
    if not force:
        cached = load(cfg.calibration_path)
        if cached is not None:
            return phase_f_clamp(cached, min_cap, absolute_ceiling), False
    result = run(cfg, min_cap, absolute_ceiling, quick=quick)
    try:
        save(result, cfg.calibration_path)
    except OSError:
        pass                    # persisting is an optimisation, not a
                                # correctness requirement
    return result, True


def apply_to_engine(result, engine, engine_module):
    """Hand the finished tables to the engine.

    Rationale: NOTES.md, calibration.apply_to_engine
    """
    engine.cost_curve = engine_module.CostCurve(
        decode_table=result.get("decode_curve") or None,
        prefill_table=result.get("prefill_curve") or None)
    return engine_module.OutputCap(result.get("output_cap_table") or None)
