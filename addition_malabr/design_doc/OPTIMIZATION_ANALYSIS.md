# MALABR -- where it can still be optimized

Ranked by expected payoff against risk. Everything under "Worth doing" changes
no observable behaviour except the resource it saves. Where a number is given it
is measured or derived from the code; otherwise it is an estimate and says so.

## Worth doing

### 1. Engine idle loop wakes ~1000 times a second
`Engine._run` sleeps 1 ms when a round finds no work, so an idle server spins
the engine thread a thousand times a second -- each wakeup takes the registry
lock, lists sessions, runs the control-flag pass and the batch composer. On a
machine where the whole point is not burdening the browser, that is measurable
idle CPU for nothing.
**Fix:** block on a `threading.Event`/`Condition` that `submit`, `cancel`,
`stop_generation` and the control reader set. Idle cost goes to zero; wake
latency stays sub-millisecond. Zero behaviour change. Small.

### 2. Chat panel re-renders the whole reply on every token
`onToken` calls `setBody()`, which re-runs the markdown renderer over the entire
accumulated reply and replaces the DOM each time. A 500-token reply is 500 full
renders -- quadratic string and DOM work on the browser's main thread while the
user is reading.
**Fix:** coalesce with `requestAnimationFrame` (at most one render per frame,
~60/s) or render only when a newline/fence boundary arrives. Same final output.
Small.

### 3. A lone session is held to the multi-session round budget
`ROUND_LATENCY_BUDGET_MS` (50 ms) exists so no session's round delays another's.
When only one session is runnable there is nobody to protect, but prefill is
still chunked to what fits in 50 ms, so a long prompt in a single tab takes
many more rounds than it needs to. This is the largest time-to-first-token
lever available without touching the model.
**Fix:** let `build_batch` widen the prefill budget when exactly one session is
active (foreground or not). A policy change, not a refactor; needs a test that
the multi-session bound is unchanged. Medium.

### 4. KV cache quantization
llama.cpp can store the KV cache as q8_0 instead of f16 (`type_k`/`type_v` on
the context params). Roughly halves bytes per resident token, so the same RAM
budget holds about twice the context -- or the same context at half the memory.
Small, measurable quality cost; worth benchmarking on the canary suite first.
Config-level change. Small to try, needs a fidelity check.

### 5. Calibration refills from zero for every measured position
`phase_b_position_curve` wipes the sequence and re-decodes filler tokens up to
each position in turn; positions are ascending, so most of that work repeats.
Only matters on a cold start with no cached `calibration.json`, but that is the
first-run experience.
**Fix:** extend the existing fill to the next position instead of wiping.
Small.

## Not worth doing (checked, left alone)

- `user_turn` re-renders the whole conversation through the chat template
  each turn. Linear in history length, string work only, tokenizes just the
  delta. Fine until conversations are very long.
- `_relieve_aggregate_pressure` re-sums session positions a few times per
  iteration; `build_batch` sorts and allocates per round. Sessions are at
  most `n_seq_max` (8), so these are microseconds.
- `_stream` polls the outbox at 250 ms; `wait_for_teardown` at 2 ms. Both
  bounded and only active while something is happening.
- The reply is tokenized twice in `_finish_turn` for the whitespace check --
  only when the reply has trailing whitespace, and only once per turn.

## The real ceiling

Everything above is scheduling and plumbing. Decode throughput itself is set by
the model size, quantization and the calibrated thread count; nothing in the
Python layer changes tokens-per-second. The levers that do are a smaller or
more heavily quantized model, and the KV quantization in item 4.
