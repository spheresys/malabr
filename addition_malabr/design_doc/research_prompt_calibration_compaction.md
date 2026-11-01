# Deep Research Prompt — Calibration & Compaction for MALABR

Research prompt for an assistant with web search and deep
research enabled. It's built to skip ground already covered this session —
read the "already known" list before researching, and don't spend budget
re-surfacing it. The goal is to find what's *beyond* it.

---

## System context (read first)

MALABR is a Chromium extension research prototype: one small LLM
(currently `Qwen3-0.6B`, GGUF, via `llama-cpp-python`'s low-level ctypes
layer) loaded once and shared across multiple isolated per-tab chat
sessions inside **one Python process**. Key constraints that make most
published serving literature only partially applicable:

- **CPU inference, one execution slot, no GPU.** Decode is memory-bandwidth
  bound. Measured on the dev machine (4 physical cores): ~43 tok/s at short
  context, dropping to ~5 tok/s at 6000 tokens of context (9x) — cost grows
  with position because every decode step reads that session's whole K/V
  history, not just the fixed model weights.
- **Batching helps far less than published GPU numbers suggest, and the
  benefit collapses with depth.** Measured: 1.65x aggregate throughput
  batching 4 sessions at short context; only ~12.5 tok/s aggregate (barely
  above single-session) batching 4 sessions at depth 1500 — because batching
  only shares the *weight-read* cost, not each session's own *K/V-read*
  cost, and the latter dominates at depth.
- **Sessions are isolated via `llama.cpp`'s multi-sequence API**
  (`seq_id`-tagged KV cache slots within one context), not separate
  processes — deliberately, to keep the shared-model memory win. Isolation
  is enforced by wiping a slot on *acquisition*, not release.
- **CPU governance is two-layer:** a real `cgroup cpu.max` quota at the
  whole-process level (process vs. the browser hosting it — kernel
  enforced, verified with `systemd-run --user --scope -p CPUQuota=`), plus
  software dispatch-order policy *inside* that budget, between sessions
  (cgroups cannot attach below the process boundary — no `seq_id`-level
  kernel primitive exists).
- **Memory is a single fixed pool** (`n_ctx`, allocated once at startup,
  measured to have flat RSS regardless of session count), divided among
  session slots (`n_seq_max`). No dynamic/elastic reallocation between
  sessions currently exists — a session cannot borrow an idle sibling's
  unused share.
- **Everything is ephemeral.** No conversation data is ever persisted to
  disk. A session dies with its tab; nothing survives a server restart.

---

## Already known — don't just re-surface these, go beyond them

**Scheduling / fairness literature already reviewed:** VTC (virtual token
counter fairness, OSDI'24), Sarathi-Serve (chunked prefill, stall-free
scheduling, OSDI'24), DLPM/D²LPM (locality-aware fair scheduling,
arXiv:2501.14312), Orca (iteration-level scheduling), FastServe (MLFQ
preemption), continuous batching in vLLM/SGLang/TensorRT-LLM generally.

**KV-cache / compaction literature already reviewed:** StreamingLLM
(attention sinks + sliding window), H2O (heavy-hitter eviction), SnapKV,
PyramidKV, CachedAttention/AttentionStore, InferCept, RadixAttention/SGLang
prefix caching, KIVI/KVQuant/TurboQuant (KV quantization).

**Production systems already investigated directly (source code and/or
public docs read, not assumed):**
- Chrome's on-device Prompt API — internal C++ engine has visibility-based
  dispatch priority (verified in Chromium source, undocumented publicly);
  public JS API's documented "session compacting" is a **developer-
  implemented** pattern (destroy session, call the Summarizer API for a
  real generation-cost summary, recreate a new session with the summary as
  `initialPrompts`) — not an engine-level primitive, no in-place KV
  modification.
- Ollama — documented strict FIFO, no priority, global (not per-client)
  queue bound.
- llama.cpp's own server — slot-based, `server_queue`/`server_slot`
  architecture, continuous batching, FIFO dispatch, no fairness/priority
  policy.

**MALABR's own current design (don't propose reinventing these, propose
improvements or alternatives to them specifically):**
- Compaction: whole-turn eviction, oldest-first, aligned to chat-template
  turn boundaries (`<|im_start|>`/`<|im_end|>`), via `llama_memory_seq_rm`/
  `llama_memory_seq_add` position-range surgery — mechanically verified,
  logit divergence from a fresh prefill measured (max|Δlogit|=4.56 at one
  tested config, likely from sliding-window attention, unconfirmed if
  model-general).
- A proposed (untested) cheap "stub" option — truncate a dropped turn to
  its first/last ~40 characters rather than deleting it entirely, pure
  string operation, no generation call.
- Calibration: a 5-phase startup script (thread sweep, per-position cost
  curve, joint aggregate-worst-case validation, output-cap-table
  generation, sanity-clamped fallback) — measured to complete in ~10s for
  the basic phases; found by direct testing that extrapolating the
  aggregate-worst-case from single-session data is unreliable (off by >3x
  at depth 1500) and that trial-to-trial noise is real and unquantified
  (up to 19% spread across 3 trials at one configuration).

---

## Research questions — CALIBRATION

1. **Production auto-tuning / cold-start calibration methodology.** How do
   real serving systems (vLLM, TensorRT-LLM, SGLang, Triton Inference
   Server, Ollama, LM Studio) determine thread counts, batch sizes, and
   memory pool sizes at deployment/startup time? Is there a standard
   "benchmark this machine before serving" pattern, or do they all rely on
   static config/environment detection? Specifically: does `llama.cpp`'s
   own `llama-bench` tool, or any wrapper around it, get used as a
   calibration step in any real deployment pipeline?
2. **Statistical rigor for micro-benchmarking / calibration.** What is
   accepted best practice for trial counts, warm-up iterations, outlier
   handling, and confidence reporting in systems micro-benchmarking
   specifically applicable to a ~10-second calibration budget (not a
   multi-hour academic benchmark suite)? Look at both academic systems-
   benchmarking-methodology literature (e.g., work on benchmarking
   pitfalls/statistical rigor in performance evaluation) and pragmatic
   approaches from adjacent domains — game engines' "benchmark your PC"
   features, ffmpeg/video encoders' hardware capability probing, JIT
   warm-up calibration in JVMs, cloud instance right-sizing tools.
3. **Joint CPU-quota + thread-count tuning.** Is there published work or
   an established practice for jointly calibrating a thread pool size
   against a *kernel-enforced* CPU quota (cgroup `cpu.max`/`CPUQuota`)
   specifically — i.e., choosing `n_threads` to match a fractional core
   budget rather than the full physical core count? This is a narrower
   question than general thread-pool tuning; look specifically for
   guidance on avoiding oversubscription *within* a cgroup quota.
4. **Functional models for decode-cost-vs-context-length.** Is there a
   published closed-form or empirically-validated model for how per-token
   decode latency scales with KV-cache depth on CPU (not GPU) inference —
   distinct from the well-known O(n) attention FLOPs argument, specifically
   addressing whether real measured curves are linear, super-linear, or
   otherwise, and why? (MALABR's own attempt to fit a simple
   fixed-weight + linear-KV-read model against real multi-trial data did
   not cleanly fit — worth checking if this non-linearity is documented
   elsewhere or is measurement-specific.)
5. **Aggregate/joint worst-case validation for multi-tenant resource
   configs.** Beyond MALABR's own ad hoc "fill every slot to depth and
   measure" approach — is there a more principled methodology (from
   multi-tenant systems / capacity planning literature) for validating
   that several resource parameters (pool size, tenant count, quota) are
   jointly safe under worst-case concurrent load, ideally without a full
   brute-force measurement at every candidate configuration?

## Research questions — COMPACTION

1. **What compaction/context-management strategies do real consumer chat
   products actually use** when a conversation approaches a context limit —
   the major hosted chat products? Look for engineering blog
   posts, public documentation, or credible technical reporting (not just
   marketing copy) on whether they silently truncate, summarize, warn the
   user, or use some other strategy. This is a UX-and-systems question, not
   purely an academic one.
2. **Structure-aware / turn-boundary-aware eviction — does this already
   have a name?** MALABR's own approach (evict whole conversational turns,
   oldest-first, aligned to chat-template boundaries, as opposed to an
   arbitrary token-position range) was arrived at independently — check
   whether this is documented prior art under a different name, in either
   academic KV-cache-management literature or production chat-serving
   systems, before treating it as novel.
3. **Non-generative / free compaction techniques specifically.** Given the
   constraint that compaction must NOT cost a real model generation call
   (ruling out LLM-based summarization, which several systems — including
   Chrome's documented "session compacting" — use), what other zero- or
   near-zero-cost context reduction techniques exist? Rule-based
   truncation, extractive (non-generative) summarization, structural
   compression, or anything else that reduces token count without an
   inference pass.
4. **KV-cache compression at the architecture level, as a complementary
   angle.** Multi-head latent attention (MLA, as used in DeepSeek-family
   models) and similar architectural techniques reduce KV-cache size
   fundamentally rather than at serving time — worth a survey of what
   exists here and whether any technique is applicable/retrofittable to
   models that weren't trained with it, purely as a landscape check (not
   necessarily to build).
5. **Isolated / tenant-safe prefix caching.** DLPM and RadixAttention-style
   cross-request prefix sharing conflict with MALABR's isolation model (a
   shared radix tree is a plausible side channel between sessions). Is
   there published work on prefix caching or KV reuse that preserves
   strict tenant isolation — i.e., sharing compute/cache benefit without
   sharing observable state or timing across tenants?
6. **Eviction granularity/scheduling: incremental vs. batch.** MALABR
   currently evicts in one large event at a 95%-of-budget threshold, as
   opposed to continuous small increments. Is there published guidance
   (from cache-replacement or memory-management literature generally, not
   necessarily LLM-specific) on which produces better perceived continuity
   or lower overhead for this kind of threshold-triggered eviction?

---

## What matters most in the answer

For every technique found, explicitly flag:
- **GPU-only or CPU-applicable?** Most serving literature assumes GPU +
  many parallel execution slots + continuous batching. State plainly when
  a technique's benefit doesn't transfer to a single CPU execution slot —
  this session already found batching's benefit collapses with context
  depth on CPU, so don't assume GPU-validated numbers carry over.
- **Requires a generation call, or free?** Central constraint — MALABR's
  compaction must stay near-zero-cost, no model inference.
- **Single-process multi-tenant, or process-per-tenant?** Most datacenter
  serving assumes dedicated resources per tenant/replica; MALABR's central
  constraint is many sessions sharing one process's one model instance.
- **Cite concretely** — paper/system name, what was actually measured or
  claimed, and whether it was independently verified by the reporting
  system or just claimed in a paper's own evaluation.

Prefer breadth with honest gaps over confident guessing — say plainly where
nothing relevant was found, rather than stretching a tangential result to
look like an answer.
