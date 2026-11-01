# MALABR Pivot — Open Design Questions & Known Gaps

This is a living list of design questions that surfaced while reading the current
(v3.0) codebase, which don't map cleanly onto the shared-inference pivot
(`research_problem_statement.md`) and don't yet have a settled answer. Revisit
each one when implementation (Phase 1+) actually starts — the goal here is just
to not lose the observation between now and then.

---

## 1. Socket connection lifecycle doesn't fit streaming generation

**Current design:** `MServerUDS::Send()` is strictly one-shot — connect, send
the full request, block until the full response is ready, read it all, close.
One fresh `MSocketUDS` per call, discarded immediately after.

**Why it doesn't map to the pivot:** LLM generation needs to stream tokens back
incrementally as they're produced, not deliver one complete response at the
end (§6.5 of the research doc already anticipates this — `llama-cpp-python`
exposes generation as a Python iterator specifically so the supervisor can
regain control between tokens). The current wire protocol and `Send()`
implementation both assume exactly one request → exactly one response, with
no mechanism for incremental delivery over one open connection.

**What needs deciding:**
- **Connection granularity.** Discussed and reasoned through: per-generation-call
  (open once per user message, close once that response finishes streaming) is
  almost certainly the right choice — UDS connection overhead is sub-millisecond
  and negligible against seconds of generation time, so there's no real
  performance case for holding a connection open across multiple chat turns.
  Per-token reconnection would be genuinely wrong. Per-whole-conversation is
  possible but adds real complexity (handling a long-lived connection dropping
  mid-conversation) for a saving that doesn't matter at UDS latencies — probably
  not worth it.
- **Wire protocol change.** The framing needs to support multiple response
  chunks flowing back over one connection before it closes, replacing the
  current single `[4B length][response]` pattern.
- **Where this touches:** `mserver_uds.cc`'s `Send()`/`GetHeaderPayload()` on
  the C++ side; `runtime.py`/`protocol.py`'s send/receive logic on the Python
  side.

**Working recommendation:** don't spend effort on connection *reuse* — spend it
on the streaming *framing* change. The socket-per-call pattern itself is fine;
what's missing is a way to send more than one chunk per call.

---

## 2. Training worker architecture has no token-level cancellation

**Current design:** `Supervisor` dispatches jobs to an `mp.Process` pool via
`train_queue`; each worker runs exactly one job to completion
(`train_one_job()`), with no mechanism to interrupt or preempt it mid-job.

**Why it doesn't map to the pivot:** §6.5 of the research doc requires checking
a cancel flag at *every token boundary* during generation (this is what makes
RQ3's lifecycle cancellation cheap — bounded by one token's time, not one
request's time). The current run-to-completion worker model has no concept of
a mid-job checkpoint at all — a job either finishes or it doesn't.

**What needs deciding:** does the `mp.Process`-per-job pattern get replaced
entirely for generation (e.g., a persistent worker holding the loaded model,
looping over tokens, checking a shared cancellation signal between each one),
or adapted in place? How does `Supervisor` track "this session's in-flight
generation, cancellable now" versus the current all-or-nothing job model?

**Where this touches:** `training.py`'s `trainer_worker()`/`train_one_job()`;
`Supervisor`'s job-tracking and result-loop logic.

---

---

## 3. Header scopes identity by extension only — no tab-level field; payload schema also needs new fields

**Current design:** `GetHeaderPayload()` (`mserver_uds.cc`) builds the header
as `route_ + "," + extension_id_ + "," + payload_size` — nothing else. On the
Python side, `protocol.py`'s `unpack_client_envelope()` parses exactly this
same 3-field CSV, nothing more. Identity, at the protocol level, is scoped to
**extension only**.

**Why it doesn't map to the pivot:** the session model needs
`(extension_id, tab_id)` — a *finer* scope than extension alone. Two
different tabs running the *same* extension (e.g. the coding-assistant
extension open in two tabs) need two independent chat sessions, but the
current header has no field to tell them apart — both would collapse onto
the same `extension_id_`, conflating two sessions into one.

**Two separate things need to change, not just one:**
- **Header:** add `tab_id` and `tab_visibility` fields (already planned at
  the design level in §6.3/§12 Phase 3 of the research doc — this entry just
  grounds it in the precise current code: `GetHeaderPayload()` on the C++
  side, `unpack_client_envelope()` on the Python side, both need to change
  together). Separately unresolved: *where* the C++ side actually sources
  `tab_id`/visibility from in the first place — `WebContents::GetVisibility()`
  and tab identification are still unexplored territory, not yet read.
- **Payload:** the FlatBuffer schema itself (`ml.fbs` → `ML.Request`) is
  currently shaped around `x`/`y` tensors for `fit`/`predict`/`score`. Chat
  needs a prompt field (and possibly conversation history, or just a
  `session_id` if history stays server-side) — this is a schema change, not
  just a header change, and it cascades: `ml.fbs` → regenerate both the
  Python (`mserver/ML/`) and JS (`malabr_js/ml_generated.js`) bindings →
  `malabr.idl` needs a new `GenerateRequest`-equivalent type → `malabr_api.h`/`.cc`
  needs a new `MalabrGenerateFunction`, following the existing four-class
  pattern.

**Where this touches:** `mserver_uds.cc` (`GetHeaderPayload`), `protocol.py`
(`unpack_client_envelope`), `ml.fbs` and its generated bindings on both
sides, `malabr.idl`, `malabr_api.h`/`.cc`.

---

*Add new entries here as they surface — during the rest of the code
walkthrough, or once Phase 1 implementation begins.*

---
---

# Session findings — 2026-08-20

Everything below was produced in one session: an upstream-Chromium review of the
Prompt API, a set of architecture ideas explored and either adopted or rejected,
and benchmarks run on this machine against `Qwen3-0.6B-Q8_0.gguf`. Entries 1–3
above predate this and are unchanged.

---

## 4. Chrome upstream has SHIPPED visibility-aware scheduling — RQ2 collides

**What changed.** The local checkout is Chromium **127.0.6519.0**, where the AI
stack had no scheduling at all. Current upstream `main` has moved on, and the
files were renamed (which is why earlier live-fetch attempts 404'd):

| local (127) | upstream main |
|---|---|
| `chrome/browser/ai/ai_manager_impl.{h,cc}` | `ai_manager.{h,cc}` |
| `chrome/browser/ai/ai_text_session.{h,cc}` | `ai_language_model.{h,cc}` |
| scheduling inside `on_device_model_service.cc` | `on_device_model_mojom_impl.{h,cc}` |

**What upstream now does.** `on_device_model.mojom` defines a `Priority` enum
(`kForeground` / `kBackground`) and a `Session::SetPriority` method (MinVersion
4, i.e. retrofitted). `ai_manager.cc` derives priority from real tab visibility:

```cpp
on_device_model::mojom::Priority GetPriorityFromVisibility(
    content::RenderFrameHost* rfh) {
  return rfh->GetVisibilityState() == content::PageVisibilityState::kVisible
      ? Priority::kForeground : Priority::kBackground;
}
```

and tracks it live — `AIManager` now inherits `content::RenderWidgetHostObserver`
and calls `context_bound_object_set_.SetPriority(...)` from
`RenderWidgetHostVisibilityChanged`. Service-side, `RunTaskIfPossible` scans the
pending list for the first foreground task before falling back to the head, and
reports `OnDeviceModel.QueueTime.{Foreground,Background}` to UMA.

**Impact on the thesis.** RQ2 as originally written ("does foreground-first
scheduling help?") is answered by shipped open-source code. §9.9's row claiming
Chrome's scheduling is "Not published" is now false and would not survive review.

**Also resolved:** `SetForceQueueingForTesting` (the open thread from the
previous session's handoff) is confirmed to be a **test-determinism hook, not a
policy** — `if (is_running_ || force_queueing_for_testing_) return;`. Do not
cite it as evidence of scheduling policy.

**Still unresolved:** when the priority code landed (gitiles log 403s; the GitHub
commit page only showed Jun–Aug 2026, none mentioning priority, so it predates
that window). This matters for framing it as prior vs. concurrent work. Also
unverified: whether the extension Prompt API path uses this same `AIManager`.

**Working recommendation:** stop claiming *"nobody does browser-context
scheduling."* Claim instead *"Chrome does exactly one policy, in one dimension,
with measurable gaps"* — a stronger position, because it replaces a hypothetical
baseline with a shipped, citable one. Pin a specific Chromium revision in the
writeup; this area churns.

---

## 5. Chrome's five verified scheduling holes

All read directly from upstream source. These survive the RQ2 collision and are
what the reshaped thesis is built on.

1. **Background starvation is unbounded.** Selection depends only on
   `IsForeground()` and null-ness. `PendingTask::start` exists but is used *only*
   for the UMA histogram, never for a decision. No aging, no wait-time term.
2. **Dead sessions are promoted, not cancelled.** The condition is
   `if (!session || session->IsForeground())` — a null (dead) session satisfies
   it, so work belonging to a **closed tab** jumps ahead of live background work.
   The inverse of the desired behaviour.
3. **Priority is one bit.** `PendingTask` is `{session weakptr, task, start}` —
   no extension id, no tab id. Two visible tabs are indistinguishable; per-client
   fairness is not expressible.
4. **No preemption.** The `is_running_` guard has no override; a task runs to
   completion.
5. **Nothing is bounded.** `sessions_` is an unbounded `std::set`,
   `pending_tasks_` an unbounded `std::list`. No session cap, no memory ceiling,
   no per-session budget, no queue bound. Verified from the header.

`SessionDisconnected` erases only from `sessions_` and never touches
`pending_tasks_` — so cancel-on-tab-close (old RQ3) remains entirely unaddressed
upstream.

---

## 6. Measured runtime characteristics — this machine, `Qwen3-0.6B-Q8_0`

Hardware: 8 logical / 4 physical cores, 14 GB RAM. Model file 610 MB.
All figures from `llama-cpp-python 0.3.34`, CPU only, `temperature=0.0`.

**Thread scaling (single instance, 128 tokens):**

| threads | tok/s |
|---|---|
| 1 | 21.2 |
| 2 | 34.6 |
| **4** | **43.4** ← optimum |
| 8 | 34.1 ← *worse than 4* |

**Use `n_threads=4`, not 8.** 8 threads is measurably worse — consistent with 4
physical cores plus hyperthreading.

**Two concurrent instances, 4 threads each:** 20.6 + 21.0 = **41.6 tok/s
aggregate** — *lower* than a single instance at 4 threads.

**Bandwidth check:**

```
   43.4 tok/s × 0.61 GB = 26.5 GB/s   (one instance)
   41.6 tok/s × 0.61 GB = 25.4 GB/s   (two instances)
```

Both hit the same ~26 GB/s ceiling. Decode is **memory-bandwidth-bound**: each
token requires streaming the entire weight file. The memory bus saturates at 4
threads; extra threads and extra processes have nothing left to consume.

**Per-token dispatch overhead: 1.1–1.3%** (batch call vs. returning to Python
every token, two trials). Token-granularity scheduling is effectively free.

**Memory:**

| | RSS |
|---|---|
| model loaded | 700 MB |
| + context, `n_ctx=4096` | 1139 MB |
| ⇒ context allocation | **439 MB ≈ 107 KB/token** |

Model load time: **3.10 s**.

**Caveat:** these are *decode* numbers. Prefill is compute-bound and may
parallelize differently — verify separately before generalizing.

---

## 7. Two-model (foreground / background) architecture — REJECTED

**The idea.** Run two model instances, one dedicated to foreground sessions and
one to background, so a foreground request never waits behind a background
generation. Kill the background instance when idle.

**Why it was attractive.** It attacks the single hardest limitation in the
design — §6.5's admission that priority cannot interrupt an in-flight request.
It also promised real cgroups (two processes = two cgroups = kernel-enforced CPU
shares) rather than userspace approximations. And §6.8 already half-anticipated
it, noting weights could be shared read-only via `mmap`. Notably this is
*process-per-tier* (bounded at 2), not the *process-per-tenant* (N) design §6.8
considered and rejected — a middle point the document never evaluated.

**Why it was rejected — measured, not argued:**

1. **No aggregate throughput gain.** 41.6 tok/s (pair) vs 43.4 (single). Decode
   is bandwidth-bound; two instances split one memory bus rather than adding
   capacity. `mmap` sharing does not help — it saves *capacity*, not
   *bandwidth*; both processes still stream those pages every token.
2. **It caps the foreground.** This is the decisive argument. A second instance
   permanently limits foreground to roughly half the machine. One model with
   weighted token dispatch can give foreground **100%**:

   | allocation | foreground | background |
   |---|---|---|
   | one model, strict | **43.4** | 0 |
   | one model, 9:1 | **39.1** | 4.3 |
   | one model, 1:1 | 21.7 | 21.7 |
   | two models | **21.0** | 20.6 |

   One model makes the foreground ~**2× faster** than two models do.
3. **The split is rigid.** Two models fixes the ratio at partition time; changing
   it means changing thread counts or cgroup weights, and 100:0 is unreachable
   without idling a whole process. One model sets the ratio *per token*.
4. **Worse under resource caps.** Two thread pools, two sets of compute buffers
   (439 MB each — weights share via mmap, buffers do not), more cache thrashing.
   Capping CPU/memory strengthens the case against it.

**What it would still buy** (recorded for completeness): prefill parallelism
(compute-bound, so genuinely parallelizable), crash isolation between tiers, and
guaranteed background forward progress. None outweighs the foreground cap.

**Also worth stating in related work:** multi-replica priority routing is
standard in datacenter serving, so this is not novel in the abstract. What is
unstudied is the single-consumer-machine regime where replicas contend for the
same cores and memory bus. The measurement above *is* that study, and a negative
result is still publishable — "just run two" is the first thing any reader
proposes.

---

## 8. Label-swap instead of state migration — superseded, but the insight survives

**The idea.** Rather than moving a session's state between models on a
foreground/background transition, just relabel *which model is the foreground
one*. Renaming a pointer instead of copying data.

**Why it's good.** It is genuinely near-free — writing a new `cpu.weight` to a
cgroup is a file write. It established the cost hierarchy that shaped everything
after it:

```
   relabel which model is "foreground"    ~microseconds
   copy the session's state to the other  ~12 ms   (llama_state_seq_get/set_data)
   recompute the context from scratch     ~seconds (what Chrome does)
```

**Why it doesn't generalize.** The label belongs to the *model*; priority belongs
to the *session*. With one session per model it is exactly right. With more
sessions than models it breaks twice: the label over-applies (co-resident
background sessions get promoted), and — more seriously — **the isolation
guarantee is per-worker, not per-session**. A foreground session sharing a worker
with a long-running background generation still waits behind it, and more CPU for
that worker just makes the background job finish faster.

**Better generalization** (recorded in case the two-model design is ever
revisited): don't tier the *models*, tier the *CPU allocation per request* —
raise a worker's weight while it serves foreground work. Sessions never move;
priority follows the work.

**Why it's superseded.** Entry 9 removes the problem entirely: with sessions
resident in one context, switching costs nothing at all, so there is nothing to
relabel or migrate.

---

## 9. Multi-sequence residency + token-granularity dispatch — RECOMMENDED DESIGN

**The key discovery.** Chrome's "one conversation at a time" is an artifact of
*its* architecture, not a constraint of ours. `llama-cpp-python 0.3.34` exposes,
at the **low-level ctypes layer**, everything the design needs:

| capability | symbol |
|---|---|
| N sequences resident at once | `llama_context_params.n_seq_max` (max 256) |
| per-token sequence tagging | `llama_batch.seq_id` / `n_seq_id` |
| per-sequence state transfer | `llama_state_seq_get_data` / `set_data` |
| per-sequence persistence | `llama_state_seq_save_file` / `load_file` |
| per-sequence KV eviction | `llama_memory_seq_rm` / `_cp` / `_keep` |
| interrupt a running decode | `llama_context_params.abort_callback` |
| separate decode/prefill threads | `n_threads` / `n_threads_batch` |

**Verified KV pool behaviour** (`n_ctx=4096`):

```
n_seq_max=1 → per-sequence context 4096, RSS 1139 MB
n_seq_max=2 → per-sequence context 2048, RSS 1139 MB
n_seq_max=4 → per-sequence context 1024, RSS 1140 MB
n_seq_max=8 → per-sequence context  512, RSS 1143 MB
```

The KV cache is **one fixed pool, divided among sequences**. Total memory is set
by `n_ctx` at allocation and does **not** grow with session count — 8 sessions
cost 4 MB more than 1. Each session's history lives in its own slice, tagged by
sequence id; attention reads only matching cells.

**What this buys over Chrome:**

```
   Chrome, request-granularity:  128-token background job → 2.95 s blocked
   token-granularity:            1 token                  → 0.023 s blocked
```

**A 128× reduction in head-of-line blocking, for 1.2% throughput** — measured on
this machine, with a real model.

**Cost, and it is real:** the high-level `Llama` class does **not** expose
`seq_id`; `save_state`/`load_state` operate on the whole context. Multi-sequence
requires driving `llama_batch` construction and `llama_decode` through ctypes
directly. This is genuine engineering work and belongs in the Phase 1 estimate.

**The consequence for RQ2:** memory is now a fixed pool with N sessions competing
for slices (4096 tokens ÷ 8 sessions = 512 each). The library provides the
eviction primitive (`llama_memory_seq_rm`) and **no policy**. That gap is the
research question, and it is now concrete: *given a fixed KV pool and N
competing sessions, how is it divided, and what does eviction cost on return?*

---

## 10. Three weaknesses versus Chrome — and how to close them

Chrome's design is worse on almost every axis measured above, but it is better on
three, and these must be addressed rather than ignored. Prof. Mondal works in
secure systems and will find #1 immediately.

### 10.1 Cross-session leakage is POSSIBLE (Chrome's is structurally impossible)

**Demonstrated, not hypothesized.** Test run this session:

```
session A writes 5 tokens to seq 0  →  seq0 pos_max=4   seq1 pos_max=-1
session B writes 2 tokens to seq 1  →  seq0 pos_max=4   seq1 pos_max=1   (independent ✓)
session A ends, seq 0 reused:
    before clearing            →  seq0 pos_max=4   ← A's context STILL RESIDENT
    after llama_memory_seq_rm  →  seq0 pos_max=-1
```

Sequences are genuinely isolated *while live*. But **nothing is cleared
automatically**. A new session handed a recycled `seq_id` without an explicit
`llama_memory_seq_rm` inherits the previous session's cells — it overwrites the
early positions and the tail survives, tagged as its own. That is a working
cross-session context leak, one missing call away.

Chrome cannot have this bug: it wipes and replays the context on every switch, so
nothing persists to leak. **Chrome buys isolation with performance; this design
buys performance and must enforce isolation explicitly.** State this tradeoff in
the thesis rather than letting a reviewer find it.

**Fixes, in order of strength:**

1. **Clear on acquire, not only on release.** The teardown path can be missed;
   the allocation path cannot. Call `llama_memory_seq_rm` when a `seq_id` is
   *handed out*, so a missed release is harmless. This is the single highest-value
   change — it moves the safety-critical operation to the point that cannot be
   skipped.
2. **Single chokepoint.** One `SequenceAllocator` is the only code that touches
   raw sequence ids; everything else receives an opaque handle. Reuse is forced
   (`seq_id` must be `< n_seq_max`), so it must be centrally managed.
3. **RAII / context manager.** Make the only way to obtain a sequence be
   `with registry.acquire(session_key) as seq:` so release cannot be forgotten.
4. **Runtime invariant assertion.** After acquiring, assert
   `llama_memory_seq_pos_max(mem, seq) == -1`. Cheap, and fails loudly at the
   moment of the bug rather than as a mysterious leak later.
5. **Generation counters.** Pair each `seq_id` with a generation number; sessions
   hold `(seq_id, generation)`. A stale reference to a recycled slot is rejected
   on mismatch. Standard slot-map pattern; prevents use-after-free-style aliasing.

**RQ4 must include this case:** end a session, start a new one on the same
sequence id, and check for canary residue. A naive implementation fails it.

### 10.2 Sequence lifecycle complexity (Chrome has one context, hard to get wrong)

Chrome's simplicity is a real advantage — there is almost nothing to manage. The
proposed design has a sequence lifecycle that must be correct.

**Fixes:**

- Items 2–5 above are the structural answer: if only one class can touch
  sequence ids, and the only way to get one is a context manager that clears on
  both ends, the complexity is contained rather than spread.
- **Invariant test after every teardown:** the set of sequences with
  `pos_max != -1` must exactly equal the set of live sessions in the registry.
  Cheap to assert, catches every lifecycle bug class.
- **Property-based test:** random sequences of open/generate/close across many
  sessions, asserting the invariant holds throughout and no canary ever appears.
  This is worth more than a handful of hand-written cases.

### 10.3 Idle memory is never reclaimed (Chrome kills its process after 5 min)

`kDefaultModelIdleTimeout = base::Minutes(5)` — an idle Chrome uses **zero**. The
proposed design holds 1139 MB whether or not anyone is using it. For RQ1 and RQ4,
**steady-state memory must be measured, not just active memory**, or the
comparison flatters this design unfairly.

**Fixes — and one that beats Chrome outright:**

1. **Two-level idle teardown.** Free the context after N minutes idle (releases
   the 439 MB pool, keeps the 700 MB model); free the model after a longer
   timeout. Reload cost measured at **3.10 s** — acceptable for background,
   noticeable for foreground.
2. **Hibernate sessions to disk before teardown.** `llama_state_seq_save_file`
   persists a single sequence; `llama_state_seq_load_file` restores it. Chrome's
   idle teardown **destroys every conversation**; this design can reclaim the
   same memory and restore context on wake. That is strictly better than Chrome
   on both axes, and the API already exists.
3. **Demand-sized pool.** `n_ctx` is fixed at context creation, but the context
   can be destroyed and recreated at a different size when session count changes
   materially. Expensive (loses resident KV unless hibernated first) but a real
   lever, and it composes with (2).

Item 2 is worth writing up as a contribution in its own right: *idle reclamation
without context loss*, which no surveyed system does.

---

## 11. Continuous batching — DEFERRED, with reasoning

**What it would do.** Decode several sessions in one forward pass. Because each
token requires streaming all weights, batching two sessions reads the weights
**once** and produces two tokens — roughly halving memory traffic per token. This
is the one technique that genuinely defeats the bandwidth ceiling in entry 6, and
`llama_batch` already supports it (per-token `seq_id`).

**Why it is currently out of scope.** §10 excludes it as "a valid optimization,
not the research question." That exclusion was written when scheduling meant
queue ordering; it is now in tension with the design, because batching changes
what the scheduler *expresses* — priority becomes slot allocation within a batch
rather than ordering between requests.

**The tradeoff, to be decided deliberately rather than by drift:**

- *Include:* technically correct, much better numbers, but moves closer to
  vLLM/Orca territory and leans harder on browser signals for novelty.
- *Exclude:* stays clearly distinct from datacenter serving, simpler to build,
  but optimizes ordering on a runtime that did not require ordering.

**Working recommendation:** adopt multi-sequence residency and token-granularity
dispatch (entry 9); keep batching explicitly out of scope but **name it** as the
reason reported service times are conservative. Revisit only if time permits
after the core RQs are answered.

---

## 12. Operational notes

- **`n_threads=4`, not 8** on this machine (entry 6).
- **The external drive dropped off the USB bus mid-session** (2026-08-20 15:22),
  taking the filesystem read-only and forcing a remount at
  `/run/media/chaitu/chaitanya` instead of `/media/chaitu/chaitanya`. Repo
  verified intact (same HEAD `fd54be76`, same working-tree state). Restore the
  original mount path before building — `out/Default` has absolute paths baked in
  and a 8.5-hour rebuild is the failure mode. Design docs are mirrored to
  `~/malabr_design_doc_backup/`. **Set up an off-drive git remote before Phase 1.**
- Models present: `Qwen3-0.6B-Q8_0.gguf` (610 MB, use for scheduling
  experiments), `gemma-3-1b-it-Q4_K_M.gguf` (769 MB).
- `llama-cpp-python 0.3.34` is installed in the venv at
  `/home/chaitu/Desktop/vscode/malabr/` — on the **internal** disk, so it
  survived the drive failure.

---

## 13. Open items from this session

- **Unmeasured:** whether alternating between two *different* resident sequences
  costs more than the 1.2% same-sequence dispatch overhead (cache locality). This
  is the load-bearing assumption under entry 9 and should be measured first.
- **Unmeasured:** prefill parallelism — entry 6's numbers are decode-only.
- **Unverified:** when Chrome's visibility-priority code landed (prior vs.
  concurrent work).
- **Unverified:** whether the extension Prompt API path uses the same
  `AIManager` as the web path — relevant to the multi-extension contention claim.
- **Undecided:** the batching scope question (entry 11).

---

# Session findings, part 2 — 2026-08-20 (continued)

Entries 4–13 covered the upstream Chrome review, the benchmark suite, and the
architecture ideas explored. This part covers the framing correction, context
compaction, consumption limits, and the restructured RQs.

---

## 14. The cgroup framing — what it is, and where it must NOT be overclaimed

**The framing.** cgroups enforce resource limits at *process* granularity. Shared
inference deliberately collapses N tenants into one process to get shared model
residency. A session is a `seq_id` — an integer tag on cells inside one shared KV
pool. There is no thread, no address space, no PID. **The kernel has nothing to
attach to.** Governance must therefore be rebuilt in userspace at session
granularity.

This is now defended on data, not preference: entry 7 rejected process-per-tier
on measurement (41.6 vs 43.4 tok/s, and a hard foreground cap), so the userspace
path is the *measured* choice, not the convenient one.

**The concrete mapping** — every row now has a real primitive behind it:

| cgroup controller | Kernel behaviour | Userspace analog | Actual primitive |
|---|---|---|---|
| `cpu.max` | X µs CPU per Y ms period | token slots per client per window | weighted selection in the decode loop (~1.2%) |
| `memory.max` | RSS ceiling, OOM-kill | per-session slice of the KV pool | `n_ctx_seq = n_ctx / n_seq_max`; `llama_memory_seq_rm` |
| `pids.max` | cap on process count | per-client sequence-slot + queue-depth cap | `n_seq_max` (hard limit, max 256) |
| freezer / kill | `SIGSTOP` / `SIGKILL` | cancel in-flight, purge queue, clear state | `abort_callback` + `llama_memory_seq_rm` |
| `cpu.stat` / `memory.stat` | per-cgroup accounting | per-client service time, per-session tokens | your own counters — nothing provides these |

**CRITICAL — do not claim "we reimplemented cgroups in userspace."** Token
buckets, weighted round-robin, and per-tenant admission control are standard;
every cloud API does them. Stated that way, this is a rate limiter, and a
reviewer will correctly call it engineering rather than research.

**The contribution is where the analogy BREAKS.** Four ways:

1. **The quantum is not fungible.** A CPU microsecond is interchangeable and
   switching costs ~1 µs against a millisecond quantum, so schedulers ignore it.
   Here switching between sessions costs single-digit percent (entry 17), and
   Chrome pays *seconds* because it recomputes. When switch cost is a meaningful
   fraction of the quantum, optimal quantum size becomes an empirical question.
   CPU schedulers never face this.
2. **Memory behaves as a cache, not a cap.** `memory.max` kills on breach.
   Evicting a session's KV kills nothing — it defers a re-prefill cost,
   proportional to what was evicted, and only paid if that session returns.
   That is cache replacement with a recompute penalty; no cgroup controller has
   that shape.
3. **The controllers are coupled.** `cpu.max` and `memory.max` are independent in
   the kernel. Here, evicting memory costs CPU (re-prefill), and granting more
   tokens accumulates more KV. The joint policy is non-obvious in a way separate
   cgroup controllers never are.
4. **The input is trusted browser context.** No serving system — Ollama,
   llama.cpp server, vLLM — receives verified tab visibility or tab-death events.

**The honest test** for every piece of this work: *does answering it require
measurement, or is the answer obvious once stated?* Implementing a token bucket
and bounding a queue are obvious (that is what you **build**). Optimal dispatch
quantum given measured switch cost, eviction policy under deferred recompute,
joint CPU/memory policy given the coupling, and whether governance overhead
erases the sharing win are not obvious (that is what you **study**).

**Claim this instead:** *"Session-granular resource governance in a shared
inference runtime is not a port of OS resource control, because the scheduling
quantum carries a switching cost, memory behaves as a cache with deferred
recompute, and the two are coupled. We characterise those costs and show what
policies they imply."*

---

## 15. Context compaction as a governance action — TESTED, works approximately

**The idea.** Instead of the binary keep-or-evict choice, *shrink* a session's
context under memory pressure — the same move long-context coding tools make
when a conversation gets long.

**Why this is the strongest single answer to "are we just renaming cgroups?"**
`memory.max` has exactly two verbs: allow and destroy. The kernel cannot compress
a tenant, because it does not know what the memory *is*. A userspace governor at
session granularity **can**. That is a governance action with no kernel
equivalent — an existence proof that this is more than a port.

**Primitives verified present:**

- `llama_memory_seq_rm(mem, seq_id, p0, p1)` — takes a **position range**, so
  partial eviction (drop the middle, keep the rest) is supported.
- `llama_memory_seq_add(mem, seq_id, p0, p1, delta)` — shifts positions, i.e.
  closes the hole left behind.
- `llama_state_seq_save_file` / `load_file` — hibernate a single session to disk.

**The resulting policy ladder** (fidelity as a function of browser state):

| Level | Mechanism | Cost to apply | What is lost |
|---|---|---|---|
| keep | — | none | nothing |
| drop middle | `seq_rm(p0,p1)` + `seq_add` | ~free | middle of the conversation |
| summarize | generate a summary, re-prefill | a full generation pass | semantic, unpredictable |
| hibernate | `llama_state_seq_save_file` | a disk write | nothing; reload cost later |
| evict | `seq_rm(-1,-1)` | free | everything |

Focused tab keeps full fidelity; hidden tab is compacted; long-dead tab
hibernates. **Visibility drives fidelity, not just dispatch order** — a dimension
no surveyed system has.

**TESTED, and the result is more interesting than "it works."**
Test: `A` (canary) + `B` (182 filler tokens) + `Q` (question), then remove `B`'s
position range and shift `Q` back; compare resulting logits against a fresh
prefill of `A` + `Q` alone.

```
pos_max after surgery = 28  (exactly as predicted — mechanism is sound)
max|Δlogit| = 4.56      mean|Δlogit| = 0.68
top-1 token : IDENTICAL
top-5 tokens: DIVERGE at rank 4
```

**Mid-context compaction is mechanically correct but NOT numerically equivalent
to a fresh prefill.** Same next token, measurably drifted distribution. Likely
cause: gemma-3 uses sliding-window attention on interleaved layers, so after
excision-and-shift those local windows cover different content than a clean
prefill would. The `LLAMA_STATE_SEQ_FLAGS_SWA_ONLY` flag suggests the library
treats SWA specially here.

**Why that is good news for the thesis:** error *accumulates*. One compaction
preserves the top token; ten compactions on a long-lived session may not. *How
far does a session drift under repeated compaction?* is a far better question
than *does it work?*

**Caveats:** one trial, one model, and gemma-3's SWA may be the entire
explanation. Repeat on `Qwen3-0.6B` (no SWA) before relying on it.

**Scope warning.** Semantic summarization changes what the model sees, so proving
"the answer is still good" is model-quality research — not systems research, and
a real scope risk. **Mitigation: canary recall.** Plant a fact early, compact,
ask for it. Binary, objective, and it reuses the same canary machinery RQ2's
isolation constraint already needs. Prefer *mechanical* compaction (drop range,
hibernate) over *semantic* (summarize) for exactly this reason.

**Also verified — a limitation:** `type_k` / `type_v` (KV quantization) live in
`llama_context_params`, so they are **context-wide, not per-session**. Per-session
KV precision is not available inside one context. Remove it from the ladder.

---

## 16. Consumption limits — three separate caps, and Chrome hands one to the attacker

**The gap.** Weighted dispatch controls the *rate* a session receives tokens, not
the *total*. A single 10,000-token response occupies the machine for minutes even
at a perfectly fair share. Three distinct limits are needed:

| Limit | Controls | Prevents |
|---|---|---|
| **Rate** — token bucket per client | tokens per second | sustained hogging |
| **Absolute** — max tokens per request | tokens in one generation | a single runaway response |
| **Concurrency** — queue depth, seq slots | requests at once | flooding |

Note this is the same rate-versus-total confusion as the `RLIMIT_CPU` bug in
`training.py` (entry 3 territory): a cumulative cap applied once per worker
lifetime accumulates across every job that worker runs.

**Chrome's loophole — the sharpest one found this session.** `on_device_model.mojom`
does define a per-request output cap:

```
// The maximum number of tokens that should be output from a call to
// Execute(). If not set, will output tokens until an end token or the
// maximum sequence length.
uint32? max_output_tokens;
```

It is **optional**, and it lives in `InputOptions` — *the struct the caller
supplies*. The limit on how much compute a client may consume is set **by that
client**. A careless extension omits it; a malicious one certainly does. There is
no runtime-side ceiling.

That is not enforcement, it is a suggestion — and it lands squarely on §6.7
threat actors 2 (confused deputy) and 3 (malicious extension). It is also a known
bug class already cited in §9.5: OWASP LLM10 "Unbounded Consumption," and the
vLLM advisory about an unbounded caller-supplied `n` parameter is the identical
shape.

**The elegant fix — the quantum IS the cap.** No separate mechanism is needed if a
request that exhausts its quantum **yields and requeues** rather than continuing:

```
dispatch A → generate K tokens → put A back in the queue
dispatch B → generate K tokens → put B back in the queue
```

A 10,000-token response becomes 10,000/K trips through the scheduler, and every
trip is an opportunity for a foreground session to go first. The long request is
neither killed nor truncated — it simply cannot monopolize, because it must
re-earn its turn. This is the OS answer: exceed your time slice and you are
preempted and requeued, not killed. Truncation would be the wrong call — it makes
the cap visible as a failure rather than as fairness.

**But keep an absolute ceiling as a backstop.** Requeueing bounds monopolization,
not total consumption, and models genuinely do fail to emit an end token. The
ceiling must have two properties Chrome's lacks:

1. **Runtime-owned, not caller-owned.** A client may request *less* than the
   ceiling, never more.
2. **Tier-dependent.** A visible tab might get 2,000 tokens; a hidden background
   tab 512. Visibility governs not only when you run but how much you may produce.

**Consequence for RQ1:** the dispatch quantum now has a *third* effect. Small
quantum = responsive, more switching cost, strong anti-hogging. Large quantum =
cheap, more blocking, weak anti-hogging. A three-way trade to characterise.

---

## 17. Measured: cross-sequence switch cost — NOISY, needs redoing

Three runs of the same test (alternating decodes between two resident sequences
vs. staying on one, 1024-token prefill each, 200 decodes):

```
run 1: +9.9%      run 2: +8.5%      run 3: +0.0%
baseline throughput drifted 28.0 – 30.7 tok/s between runs
```

**Do not quote a number from this.** What is supportable: zero decode errors, so
multi-sequence alternation genuinely works; and the cost is single-digit percent
at worst, nowhere near the seconds-scale recompute it replaces.

A clean measurement needs a quiet machine, a pinned CPU governor, more
iterations, and a median over many trials. **This is a load-bearing number** —
entry 14's points 1 and 3 both depend on it, and RQ1's quantum question thins out
considerably if switching turns out to be free.

**Two methodology traps hit while writing this test, both worth remembering:**

1. A first version reported **−45%** (alternating *faster*), which is impossible —
   it exceeded the measured memory-bandwidth ceiling. Cause: sequence 1 had a
   position gap after prefill, so `llama_decode` was erroring out silently.
   **Always check `llama_decode`'s return value.**
2. A second version crashed inside `llama_decode`: 1024 prefill tokens submitted
   against `n_batch=512`. **Chunk prefill to `n_batch`.**
3. **Always sanity-check throughput against the bandwidth ceiling**
   (`tok/s × model_GB` must stay under ~26 GB/s on this machine). This is what
   caught trap 1.

---

## 18. Industry landscape — Chrome is alone, and everyone else is FIFO

Researched this session. The answer is cleaner than expected: **every other system
has the sharing mechanism and no policy at all.**

| System | Shares one model? | Scheduling policy | Browser signals? |
|---|---|---|---|
| **Chrome Prompt API** | yes | binary foreground/background | **yes — the only one** |
| Edge Prompt API | yes, documented cross-site | unpublished | unknown |
| Firefox AI Runtime | no — forbids parallel engines | n/a | no |
| **Ollama** | yes (`OLLAMA_NUM_PARALLEL`) | **strict FIFO, no priority** | no |
| **llama.cpp server** | yes (slots + continuous batching) | FIFO over slots | no |
| **WebLLM + SharedWorker** | yes, across tabs | none | no |
| vLLM / SGLang | yes | rich, fairness-aware | no — datacenter |

**Ollama documents this exact problem almost verbatim:** *"a 10-token request
waits behind a 4,000-token generation with no way to skip ahead."* Head-of-line
blocking, named as a known limitation, in the most widely deployed local
inference server. It does bound its queue (`OLLAMA_MAX_QUEUE`, default 512,
returns 503 when full) — more than Chrome does — but the bound is global, not
per-client, so a noisy client still starves the rest.

**llama.cpp's own server already has the mechanism:** `server_slot` per sequence,
a `server_queue`, continuous batching. The multi-session infrastructure exists in
the stack being built on. What it lacks is any notion of *whose* request matters.

**WebLLM shares Chrome's flaw:** it "terminates and reinitializes engines on model
switches" — wipe-and-replay, for the same reason.

**Revised gap statement for §9:** *Chrome is the only system using browser context
to schedule local inference, and it uses one bit of it. Everyone else shares a
model and serves FIFO.* This is far stronger than "the policy is unpublished."

---

## 19. The restructured RQs

Old → new mapping:

| Original RQ | Finding | Disposition |
|---|---|---|
| RQ1 shared residency | Chrome already does it; it is the premise | folded into new RQ4 as the cost check |
| RQ2 visibility scheduling | **Chrome shipped exactly this** | becomes new RQ1, upgraded past Chrome |
| RQ3 cancel on tab close | Chrome doesn't, and inverts it | becomes new RQ3, plus admission control |
| RQ4 session isolation | Chrome covers confidentiality by wiping | becomes a **constraint inside new RQ2** |
| — | memory has no owner in any system | **new RQ2**, entirely new |

**RQ1 — When several tabs want the model at once, who goes next, and for how
long?** Chrome decides at request boundaries using one bit and never revisits.
Measured lever: 2.95 s → 23 ms head-of-line blocking. New content is the quantum
trade-off (responsiveness vs. switching cost vs. anti-hogging), which does not
exist in CPU scheduling because switching there is thousands of times cheaper
than the slice. Baseline: `MALABR-CHROME`, Chrome's exact algorithm reimplemented.

**RQ2 — When memory runs out, whose conversation gets shrunk, by how much, and
what does that cost?** Grounded in a fixed pool (`n_ctx_seq = n_ctx / n_seq_max`,
~107 KB/token, RSS flat 1→8 sessions). Three things make it research rather than a
rate limiter: shrinking is graded, not binary (entry 15's ladder); freeing memory
defers a CPU cost; and compaction drifts (measured). **Isolation is the
constraint on this RQ** — residency is what creates the recycled-`seq_id` residue
path, so "no cross-session residue" belongs here rather than as a separate
question.

**RQ3 — How do we stop one client hogging everything, and clean up after closed
tabs?** Per-client queue depth, sequence slots, **and tokens per request**
(entry 16). Plus tab-close purge and in-flight cancellation via `abort_callback`.

**RQ4 — Does the bookkeeping cost more than sharing saved?** Now has teeth
because the alternative was measured (entry 7). Also folds in old RQ1 honestly:
**measure steady-state, not just active** — Chrome uses zero after its 5-minute
idle teardown; a resident pool holds 1.1 GB regardless.

**On "upgrade, not alternative" (the decided framing):** implementing Chrome's
policy inside MALABR is necessary, not merely diplomatic. You cannot claim
"better than foreground-first" without foreground-first to measure against, and
comparing against real Chrome would confound model, engine, and hardware path.
`MALABR-CHROME` is a controlled baseline with one variable changed.

---

## 20. Updated open items

- **Redo the switch-cost measurement properly** (entry 17). Highest priority —
  RQ1's quantum question depends on it.
- **Repeat the compaction drift test on `Qwen3-0.6B`** (no SWA) to separate
  architecture-specific effects from general ones (entry 15).
- **Unmeasured:** prefill parallelism — all throughput numbers are decode-only.
- **Unverified:** when Chrome's visibility-priority code landed (prior vs.
  concurrent work).
- **Unverified:** whether the extension Prompt API path uses the same `AIManager`
  as the web path — relevant to the multi-extension contention claim.
- **Undecided:** the batching scope question (entry 11).
- **To do:** restore the original mount path before building (entry 12).

---

## 21. Chrome's "session compacting" — verified directly against the live docs, not secondhand

Fetched `developer.chrome.com/docs/ai/session-compacting` and
`session-management` directly (2026-08-21). Confirmed precisely:

**"Session compacting" is a developer-implemented recipe using public APIs,
not an engine feature:**

```js
session.destroy();
session = null;
session = await LanguageModel.create({ ..., initialPrompts: compacted });
```

`compacted` comes from calling the **Summarizer API** (a real model
generation) on message history, then feeding the summaries as
`initialPrompts` to a **brand-new session**. Three confirmed properties: (1)
not an engine primitive — the app author implements it themselves; (2) costs
a real generation call, not free; (3) destroys and recreates the whole
session — no in-place KV modification at all.

**This is precisely the "summarise" option MALABR's own compaction ladder
(§8 of `phase1_design.md`) considered and explicitly rejected for Phase 1** —
generation-pass cost, quality-uncertain output. Chrome's documented best
practice IS the expensive option MALABR avoids. Sharper contrast than
"session management vs. resource management": MALABR does cheaply
(`llama_memory_seq_rm` surgery, no generation call, session stays alive) what
Chrome's own docs recommend doing expensively.

**`contextUsage`/`contextWindow` are confirmed read-only monitoring only** —
no configuration surface, no cross-session allocation policy documented
anywhere. The only resource-management guidance in the docs: *"Each session
consumes memory... this may become a problem"* — i.e., the entire burden is
on the app developer to monitor and manually destroy sessions themselves.

**A gap the surface-level docs check alone would miss:** entry 4/5's C++
source investigation (`ai_manager.cc`'s `GetPriorityFromVisibility`) found
real, working visibility-based dispatch priority in Chrome's engine. The
public developer documentation says NOTHING about this — no scheduling, no
priority, no visibility-based allocation anywhere in the docs. **Chrome's
scheduling policy is real and verified in source, and entirely undocumented
to developers** — an internal implementation detail invisible from the
public API surface. Neither a docs-only nor a source-only investigation would
have found this alone; it only emerged from doing both, across two separate
points in this session.

**Recommended framing, sharper than "first of its kind" (which should not be
claimed without a full literature search):**

> Chrome's Prompt API documents per-session lifecycle and monitoring, and its
> own recommended context-management technique destroys and recreates the
> session via a full model generation call. Chrome's engine additionally
> implements visibility-based dispatch priority, verified in source but
> undocumented to developers and inaccessible through the public API. MALABR
> governs memory and CPU at the session level as a first-class, low-cost
> runtime mechanism — near-free KV surgery instead of a generation call,
> calibrated and quota-enforced CPU accounting instead of an internal,
> unexposed heuristic.

**Open question raised, not yet resolved:** does llama.cpp's KV-cache
allocation model support giving one `seq_id` a non-uniform/larger slice than
others dynamically, within one context — or is per-slot sizing uniform by
construction, meaning "elastic" memory sharing between sessions (a session
borrowing an idle sibling's unused capacity, the memory-side analog of the
already-elastic CPU scheduler) would require recreating the whole context?
Real technical gate on whether dynamic memory reallocation is buildable at
all, distinct from whether it's a good research direction — unverified.
