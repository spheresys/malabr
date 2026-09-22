"""Runtime configuration -- see design_doc/phase1_design.md section 11.

Rationale: NOTES.md, config
"""

import os
from dataclasses import dataclass, field


# KV cache element type.
#
# f16 is llama.cpp's default. q8_0 stores the same cache in roughly half the
# bytes (8 bits plus one scale per 32 elements, ~1.06 bytes/element against
# 2.0), which buys ~2x the resident context for the same RAM -- and resident
# context is the scarce resource the whole of section 8 exists to ration.
#
# MEASURED before adopting (tests/kv_quant_check.py), against the f16
# distribution at four probes, on BOTH test models:
#
#            Qwen3-0.6B              gemma-3-1b (SWA)
#   q8_0     top-1 4/4  KL 0.0040    top-1 4/4  KL 0.0343
#   q4_0     top-1 2/4  KL 0.6278    top-1 3/4  KL 0.1421
#
# Two conclusions, and the second is why the default below is f16:
#
#   1. q4_0 changes the chosen token on a quarter to a half of probes. It is a
#      footgun, not a tuning option, and is not offered here.
#   2. **The q8_0 cost is MODEL-DEPENDENT, by an order of magnitude.** On Qwen
#      it is ~0.004 nats, comparable to what §8 compaction already costs
#      (0.0017-0.0032 against a ~0.0002 noise floor) -- easy to accept. On
#      gemma-3 it is ~0.034, roughly ten times compaction's cost. Top-1
#      agreement held on both, so nothing is broken either way; but a default
#      that silently moves the output distribution by a model-dependent amount
#      would also silently move every fidelity and benchmark number measured
#      afterwards.
#
# So q8_0 stays OPT-IN: set MALABR_KV_TYPE=q8_0 to take ~1.9x the resident
# context per byte, having first run tests/kv_quant_check.py against the model
# actually being served. Adopting it as the default is a judgement call that
# belongs to whoever owns the research claims, not to a config file.
KV_TYPE = os.getenv("MALABR_KV_TYPE", "f16").lower()
if KV_TYPE not in ("f16", "q8_0"):
    raise ValueError(f"MALABR_KV_TYPE must be f16 or q8_0, got {KV_TYPE!r}")

# Measured on this project's test models: ~107 KB of KV per token of context
# at f16. Scaled by the element size for other types, so the sizing in §11
# tracks the cache actually allocated instead of a stale f16 assumption.
#
# NOTE: changing KV_TYPE changes this, and calibration measures real timings
# against a real cache -- so `calibration.json` MUST be regenerated after a
# change here, not carried over.
_BYTES_PER_TOKEN_F16 = 107 * 1024
_KV_BYTES_PER_ELEMENT = {"f16": 2.0, "q8_0": 34.0 / 32.0}
BYTES_PER_TOKEN = int(_BYTES_PER_TOKEN_F16
                      * _KV_BYTES_PER_ELEMENT[KV_TYPE] / 2.0)

# Fraction of USABLE RAM the KV pool may claim  [notes: config.(module)]
RAM_FRACTION_FOR_KV = 0.5

# Never hand the KV pool memory the browser will need. Chromium with a handful
# of tabs sits comfortably above this; it is a floor, not a generous estimate.
BROWSER_RESERVE_BYTES = 2 * 1024 ** 3

# Reserve 20% of CPU for the browser hosting the engine. 100% is unrealistic:
# it starves the browser and everything else on the machine.
QUOTA_PCT = 80

# Concurrent tabs. A REAL TRADEOFF, not a free parameter: more slots divides
# less context per tab, whatever n_ctx turns out to be.
DEFAULT_N_SEQ_MAX = 8

# Floor and ceiling on the computed context size  [notes: config.(module)]
MIN_N_CTX = 4096
# Section 7 states n_ctx = 16384 outright  [notes: config.(module)]
MAX_N_CTX = 16384


def detect_physical_cores():
    """Physical, not logical. Measured on this project's machine: 8 logical /
    4 physical, and 8 threads is SLOWER than 4 -- hyperthread contention, not
    parallelism. Counting logical CPUs here would bake that loss in."""
    try:
        import psutil
        n = psutil.cpu_count(logical=False)
        if n:
            return n
    except Exception:
        pass
    # /proc/cpuinfo: count distinct (physical id, core id) pairs.
    try:
        pairs, phys, core = set(), None, None
        with open("/proc/cpuinfo") as fh:
            for line in fh:
                if line.startswith("physical id"):
                    phys = line.split(":")[1].strip()
                elif line.startswith("core id"):
                    core = line.split(":")[1].strip()
                elif not line.strip() and phys is not None and core is not None:
                    pairs.add((phys, core)); phys = core = None
        if phys is not None and core is not None:
            pairs.add((phys, core))
        if pairs:
            return len(pairs)
    except OSError:
        pass
    logical = os.cpu_count() or 1
    return max(1, logical // 2)          # assume SMT rather than overcommit


def detect_available_ram_bytes():
    """MemAvailable, not MemTotal -- the engine shares the machine."""
    try:
        with open("/proc/meminfo") as fh:
            for line in fh:
                if line.startswith("MemAvailable:"):
                    return int(line.split()[1]) * 1024
    except OSError:
        pass
    return 2 * 1024 ** 3                  # conservative fallback


def compute_n_ctx(n_seq_max=DEFAULT_N_SEQ_MAX, available_bytes=None,
                  model_bytes=0):
    avail = available_bytes if available_bytes is not None else detect_available_ram_bytes()
    usable = avail - BROWSER_RESERVE_BYTES - model_bytes
    if usable <= 0:
        return MIN_N_CTX                  # tiny machine: floor, do not go negative
    raw = int(usable * RAM_FRACTION_FOR_KV / BYTES_PER_TOKEN)
    n_ctx = max(MIN_N_CTX, min(raw, MAX_N_CTX))
    # llama.cpp divides the pool evenly, so keep n_ctx a whole multiple of
    # n_seq_max; otherwise the remainder is silently unusable.
    return (n_ctx // n_seq_max) * n_seq_max


def compute_n_threads(physical_cores=None):
    """Derived from the SAME quota as the cgroup limit, deliberately.

    Rationale: NOTES.md, config.compute_n_threads
    """
    cores = physical_cores if physical_cores is not None else detect_physical_cores()
    return max(1, round(cores * QUOTA_PCT / 100))


def compute_cpu_max_cores(n_threads=None):
    """HARD CPU ceiling, in cores. A pure backstop, not a normal-operation
    limit: memory governance caps sessions long before CPU is the bottleneck
    (RSS is a fixed n_ctx allocation, flat with session count), and a single
    session cannot monopolise CPU anyway -- input/output caps bound the work
    per turn, the loop yields after every token, and MALABR_CPU_DUTY throttles
    the duty cycle. This just stops a bug or a pathological model from taking
    the whole machine.

    Rationale: NOTES.md, config.compute_cpu_max_cores
    """
    logical = os.cpu_count() or 2
    nt = n_threads if n_threads is not None else compute_n_threads()
    return float(max(logical // 2, nt + 1))


@dataclass(frozen=True)
class ServerConfig:
    socket_path: str
    base_dir: str
    model_dir: str
    model_path: str
    calibration_path: str

    n_ctx: int
    n_seq_max: int
    n_threads: int
    n_batch: int
    quota_pct: int

    client_pool_workers: int

    # Shared-KV mode (MALABR_SHARED_KV=1)  [notes: config.ServerConfig]
    shared_kv: bool = False
    shared_kv_soft_div: int = 2

    # Cooperative CPU throttle (MALABR_CPU_DUTY, 0<d<=1)  [notes: config.ServerConfig]
    cpu_duty: float = 1.0

    # HARD CPU ceiling in cores (MALABR_CPU_MAX)  [notes: config.ServerConfig]
    cpu_max_cores: float = 0.0

    @property
    def n_ctx_per_session(self):
        """The per-session budget section 8's compaction trigger measures against.

        Rationale: NOTES.md, config.ServerConfig.n_ctx_per_session
        """
        if self.shared_kv:
            return max(self.n_ctx // self.n_seq_max,
                       self.n_ctx // max(1, self.shared_kv_soft_div))
        return self.n_ctx // self.n_seq_max


def list_models(model_dir):
    """Every *.gguf in the model directory, by bare name (no extension).

    Rationale: NOTES.md, config.list_models
    """
    try:
        names = sorted(os.path.splitext(f)[0] for f in os.listdir(model_dir)
                       if f.endswith(".gguf"))
    except OSError:
        names = []
    return names


def resolve_model(model_dir, name):
    """Map a bare model name from list_models() back to a full path.

    Rationale: NOTES.md, config.resolve_model
    """
    if name not in list_models(model_dir):
        return None
    return os.path.join(model_dir, name + ".gguf")


def load_config(base_dir=None):
    # root is the mserver/ package dir  [notes: config.load_config]
    root = base_dir or os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    n_seq_max = int(os.getenv("MALABR_N_SEQ_MAX", DEFAULT_N_SEQ_MAX))

    env_ctx = os.getenv("MALABR_N_CTX")
    model_dir = os.getenv("MALABR_MODEL_DIR",
                          os.path.join(os.path.dirname(root), "models"))
    model_path = os.getenv("MALABR_MODEL_PATH",
                           os.path.join(model_dir, "Qwen3-0.6B-Q8_0.gguf"))
    try:
        model_bytes = os.path.getsize(model_path)
    except OSError:
        model_bytes = 0
    if env_ctx:
        # An explicit value may exceed MAX_N_CTX on purpose (see the const...  [notes: config.load_config]
        n_ctx = max(n_seq_max, (int(env_ctx) // n_seq_max) * n_seq_max)
    else:
        n_ctx = compute_n_ctx(n_seq_max, model_bytes=model_bytes)

    n_threads = int(os.getenv("MALABR_N_THREADS", compute_n_threads()))
    cpu_max_env = (os.getenv("MALABR_CPU_MAX") or "").strip().lower()
    if cpu_max_env in ("0", "off", "none", "disabled"):
        cpu_max_cores = 0.0
    elif not cpu_max_env:
        cpu_max_cores = compute_cpu_max_cores(n_threads)
    elif cpu_max_env.endswith("%"):
        cpu_max_cores = float(cpu_max_env[:-1]) / 100.0
    else:
        cpu_max_cores = float(cpu_max_env)

    return ServerConfig(
        socket_path=os.getenv("MALABR_SOCKET_PATH", "/tmp/malabr_v3.sck"),
        base_dir=root,
        model_dir=model_dir,
        model_path=model_path,
        # Section 4's ONE stated exception to "nothing durable"  [notes: config.load_config]
        calibration_path=os.getenv("MALABR_CALIBRATION_PATH",
                                   os.path.join(root, "calibration.json")),
        n_ctx=n_ctx,
        n_seq_max=n_seq_max,
        n_threads=n_threads,
        n_batch=int(os.getenv("MALABR_N_BATCH", "512")),
        quota_pct=int(os.getenv("MALABR_QUOTA_PCT", QUOTA_PCT)),
        # Section 7: a WAITING ROOM, not compute. Must sit well above n_seq_max
        # or the pool becomes an invisible FIFO gate in front of the scheduler.
        client_pool_workers=int(os.getenv("MALABR_CLIENT_WORKERS", "64")),
        shared_kv=os.getenv("MALABR_SHARED_KV") == "1",
        shared_kv_soft_div=int(os.getenv("MALABR_SESSION_SOFT_DIV", "2")),
        cpu_duty=float(os.getenv("MALABR_CPU_DUTY", "1.0")),
        cpu_max_cores=cpu_max_cores,
    )
