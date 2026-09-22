#!/usr/bin/env python3
"""MALABR inference server entry point.

Rationale: NOTES.md, app
"""

import os
import subprocess
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import llama_cpp.llama_cpp as C

from malabr_service import calibration as cal
from malabr_service import engine as eng
from malabr_service import runtime as rt
from malabr_service.config import KV_TYPE, load_config


def apply_cpu_ceiling(cores):
    """HARD CPU cap (§11): move THIS process into a transient systemd scope with
    a cpu.max quota, kernel-enforced. `cores` is a float core count; <=0 = off.

    Rationale: NOTES.md, app.apply_cpu_ceiling
    """
    if not cores or cores <= 0:
        return
    usec_per_sec = int(cores * 1_000_000)

    pid = os.getpid()
    try:
        # Inside the try  [notes: app.apply_cpu_ceiling]
        if "malabr-cpu" in open("/proc/self/cgroup").read():
            return                  # already scoped (e.g. after a model-switch re-exec)
        r = subprocess.run(
            ["busctl", "--user", "call", "org.freedesktop.systemd1",
             "/org/freedesktop/systemd1", "org.freedesktop.systemd1.Manager",
             "StartTransientUnit", "ssa(sv)a(sa(sv))",
             f"malabr-cpu-{pid}.scope", "fail",
             "2",
             "PIDs", "au", "1", str(pid),
             "CPUQuotaPerSecUSec", "t", str(usec_per_sec),
             "0"],
            capture_output=True, text=True, timeout=10)
        if r.returncode != 0:
            print(f"malabr: CPU ceiling not applied: {r.stderr.strip()}",
                  file=sys.stderr, flush=True)
        elif _cpu_max_quota_active():
            print(f"malabr: CPU ceiling {cores:g} cores applied "
                  f"(cpu.max quota {usec_per_sec}us/s)", file=sys.stderr, flush=True)
        else:
            # busctl returned 0 but there is no cpu.max quota on our cgroup  [notes: app.apply_cpu_ceiling]
            print("malabr: CPU ceiling scope created but no cpu.max quota is in "
                  "force -- running unthrottled (the duty throttle still applies)",
                  file=sys.stderr, flush=True)
    except (OSError, subprocess.SubprocessError) as exc:
        print(f"malabr: CPU ceiling not applied ({exc})", file=sys.stderr, flush=True)


def _cpu_max_quota_active():
    """Read back the effective cpu.max after StartTransientUnit succeeds.

    Rationale: NOTES.md, app._cpu_max_quota_active
    """
    import time as _t
    for _ in range(15):
        try:
            cg = None
            for line in open("/proc/self/cgroup").read().splitlines():
                if line.startswith("0::"):
                    cg = line[3:].strip()
                    break
            if cg is not None:
                with open("/sys/fs/cgroup" + cg + "/cpu.max") as fh:
                    if fh.read().split()[0] != "max":
                        return True         # a numeric quota is enforcing
        except (OSError, IndexError):
            pass
        _t.sleep(0.02)
    return False


def build(cfg, quick_calibration=False):
    """Construct model, context, engine and the output cap. Returns all four."""
    C.llama_backend_init()

    mp = C.llama_model_default_params()
    mp.n_gpu_layers = 0                       # CPU only: this runs beside the
                                              # browser, not on a spare GPU
    model = C.llama_model_load_from_file(cfg.model_path.encode(), mp)
    if not model:
        raise RuntimeError(f"could not load model at {cfg.model_path}")

    # Calibration BEFORE the real context, because Phase A picks n_threads and
    # the context is created once with that value.
    result, measured = cal.load_or_run(cfg, eng.MIN_CAP, eng.ABSOLUTE_CEILING,
                                       quick=quick_calibration)
    # An EXPLICIT MALABR_N_THREADS must win over the cached calibratio...  [notes: app.build]
    if os.getenv("MALABR_N_THREADS"):
        n_threads = cfg.n_threads
    else:
        n_threads = result.get("n_threads") or cfg.n_threads

    cp = C.llama_context_default_params()
    cp.n_ctx = cfg.n_ctx
    cp.n_seq_max = cfg.n_seq_max
    cp.n_batch = cfg.n_batch
    cp.n_threads = n_threads
    cp.n_threads_batch = n_threads
    # KV element type -- see config.KV_TYPE for the fidelity measurement that
    # justifies the default. Guarded by hasattr so an older llama_cpp that
    # lacks the enum keeps working at f16 rather than failing to start.
    _kv = {"f16": "GGML_TYPE_F16", "q8_0": "GGML_TYPE_Q8_0"}[KV_TYPE]
    if hasattr(C, _kv):
        cp.type_k = cp.type_v = getattr(C, _kv)
    else:
        print(f"malabr: llama_cpp has no {_kv}; KV stays at its default",
              file=sys.stderr, flush=True)
    if cfg.shared_kv and hasattr(cp, "kv_unified"):
        # One shared pool of n_ctx cells instead of n_seq_max hard slices,...  [notes: app.build]
        cp.kv_unified = True
    ctx = C.llama_init_from_model(model, cp)
    if not ctx:
        raise RuntimeError("llama context creation failed")

    vocab = C.llama_model_get_vocab(model)
    mem = C.llama_get_memory(ctx)
    allocator = eng.SlotAllocator(mem, cfg.n_seq_max)
    engine = eng.Engine(ctx, model, vocab, allocator,
                        sampling=eng.SAMPLING_CHAT,
                        n_ctx=cfg.n_ctx, n_seq_max=cfg.n_seq_max,
                        shared_kv=cfg.shared_kv,
                        session_budget=cfg.n_ctx_per_session,
                        cpu_duty=cfg.cpu_duty)
    output_cap = cal.apply_to_engine(result, engine, eng)

    print(f"malabr: model={os.path.basename(cfg.model_path)} "
          f"n_ctx={cfg.n_ctx} ({cfg.n_ctx_per_session}/session"
          f"{', shared-KV' if cfg.shared_kv else ''}) "
          f"n_seq_max={cfg.n_seq_max} n_threads={n_threads}"
          f"{f' cpu_duty={cfg.cpu_duty}' if cfg.cpu_duty < 1.0 else ''}"
          f"{f' cpu_max={cfg.cpu_max_cores:g}' if cfg.cpu_max_cores > 0 else ''}",
          flush=True)
    print(f"malabr: calibration {'measured' if measured else 'loaded'}"
          f"{' (FALLBACK)' if result.get('fallback') else ''}"
          f" passed={result.get('passed')}", flush=True)
    for w in result.get("warnings") or []:
        print(f"malabr: WARNING {w}", file=sys.stderr, flush=True)

    return model, ctx, engine, output_cap


def main():
    cfg = load_config()
    quick = os.getenv("MALABR_QUICK_CALIBRATION") == "1"
    model = ctx = engine = None
    lock = rt.PidLock(cfg.socket_path + ".pid")
    try:
        # Take the single-instance lock FOR REAL, first thing  [notes: app.main]
        lock.acquire()
        apply_cpu_ceiling(cfg.cpu_max_cores)   # before anything heavy, calibration included
        model, ctx, engine, output_cap = build(cfg, quick_calibration=quick)
        engine.start()
        rt.run_server(config=cfg, engine=engine,
                      formatter_factory=lambda: eng.ChatFormatter(model, vocab_of(model)),
                      output_cap=output_cap, lock=lock)
        return 0
    except rt.SingleInstanceError as exc:
        print(f"malabr: {exc}; exiting", file=sys.stderr, flush=True)
        return 3
    finally:
        if engine is not None:
            engine.stop()
        if ctx:
            C.llama_free(ctx)
        if model:
            C.llama_model_free(model)
        lock.release()                          # no-op if never acquired


def vocab_of(model):
    return C.llama_model_get_vocab(model)


if __name__ == "__main__":
    sys.exit(main() or 0)
