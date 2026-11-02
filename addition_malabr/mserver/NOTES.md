# MALABR — code rationale

The long "why" behind the inference server and the chat panel. It lives here
rather than inline so the modules read as code, and it is TRACKED so the
pointers in the source resolve for anyone who checks the repo out — not only on
the machine where the code was written.

Each entry is keyed `module.Class.method`. The code carries either a one-line
hint ending `[notes: module.Class.method]` or a docstring line
`Rationale: NOTES.md, module.name` at the spot the text came from.

Text is verbatim from the original comments and docstrings.


### slots

#### slots.(module)

See NOTES.md, 'Code rationale: slots'.

#### slots.SlotAllocator

The isolation rule, stated once because it is the whole design: WIPE ON
ACQUIRE, not only on release. A release can be skipped -- a bug, or a crash
between release and reuse. An acquire cannot: a session cannot exist without
passing through it. Putting the safety-critical step where it cannot be
skipped turns a missed release into a harmless inefficiency instead of a
cross-session data leak.

Measured, not assumed: a slot written to and handed back without a wipe
still reports llama_memory_seq_pos_max() == 12 for a 13-token conversation.
The KV really does persist; this class is what stops the next session
reading it.

THREAD SPLIT -- the non-obvious part, and a correction to section 7.
Section 7's sketch has acquire() call llama_memory_seq_rm directly, while
its own concurrency note says connection threads create sessions
concurrently. Those two statements cannot both hold. llama.h documents "The
API is thread-safe" exactly once, scoped to the Tokenization section; the
Memory section carries no such guarantee. Mutating KV from a connection
thread while the engine thread is inside llama_decode() on the same context
is therefore an undocumented data race, and section 7 separately names
single-threadedness as a load-bearing invariant.

So the work is split by thread, and the split is enforced, not documented:
  - acquire() / release()  -- ANY thread. Pure bookkeeping under a lock.
                              Touches no model state whatsoever.
  - prepare(slot)          -- ENGINE THREAD ONLY. Does the actual wipe.
  - assert_ready(slot)     -- the guard the decode path calls, which makes
                              "no token is ever decoded into an unwiped
                              slot" a checked property rather than a hope.

A slot handed out by acquire() is NOT yet safe to use. It carries a pending
wipe until the engine thread clears it. That is why every slot in _free is
treated as dirty regardless of how it got there.

#### slots.SlotAllocator.acquire

The returned slot is NOT usable yet -- it carries a pending wipe that
the engine thread must clear via prepare(). No model state is touched
here, deliberately: see the THREAD SPLIT note above.

#### slots.SlotAllocator.release

There is deliberately no wipe here. Section 7's sketch wiped on release
too, "belt and braces" -- but that wipe would have to run on a
connection thread, which is exactly the race described above. Dropping
it costs nothing, because the whole rationale for wipe-on-acquire is
that release cannot be trusted to happen at all. Every slot in _free is
treated as dirty regardless.

Double release, or a slot never acquired. Returning it to
_free would put one slot in the list twice, and two sessions
would then be handed the same KV sequence -- the exact leak
this class exists to prevent. Refuse instead.

#### slots.SlotAllocator.prepare

MUST be called on the engine thread, before the first decode into this
slot. Raises SlotWipeError if the slot cannot be proven empty, in which
case the slot is quarantined and never reused.

#### slots.SlotAllocator.assert_ready

This is what turns "we wipe before use" from a convention into a checked
invariant. A missed prepare() becomes a loud error at the decode call
instead of a silent cross-session KV leak.

#### slots.SlotAllocator.__init__

A lock is required, not defensive: connection threads create sessions
concurrently (one per tab, and tabs arrive together after a window
switch).

MEASURED, and NOT the failure mode first assumed here. An earlier
version of this comment claimed an unlocked pop() hands two tabs the
SAME slot. That is false -- list.pop() is atomic under the GIL, and a
32-thread x 20-trial probe produced zero duplicates with the lock
removed. The real race is the CHECK-THEN-ACT pair in acquire():
threads all pass "if not self._free", then race to pop an emptied
list. With the window widened to 2ms, no-lock produced 120 IndexErrors
across 160 acquires; with the lock, zero. So the damage is a crash in
session creation for a request that should have been cleanly rejected
as "no capacity" -- not a cross-session leak.

Slots whose wipe could not be verified. Deliberately leaked rather
than reused: losing capacity is recoverable, leaking a conversation
into another origin's session is not.

#### slots.SlotAllocator._wipe_and_verify

llama_memory_seq_rm is declared to return bool. On this model every
call returned True (verified: full, partial, and empty-slot removal),
so this branch is defensive rather than observed-necessary. It is
checked because llama.cpp returns false where partial removal is
unsupported -- the sliding-window-attention case, which is exactly
what section 8 compaction will exercise on models like gemma-3.

Independent confirmation. Trusting the return code alone would mean
trusting one bool; this asks the memory itself whether anything is
left. -1 is llama.cpp's "no positions in this sequence".

### formatter

#### formatter.(module)

See NOTES.md, 'Code rationale: formatter'.

#### formatter.ChatFormatter

Sessions keep their KV cache resident so each turn prefills only the NEW
tokens. Re-rendering and re-prefilling the whole history every turn would
defeat that entirely. So this class tracks exactly what text is already
represented in the KV and hands back only the delta.

Model-agnostic BY CONSTRUCTION. An earlier draft of section 6c specified
appending a hardcoded '<|im_start|>{role}\n{content}<|im_end|>\n' fragment.
That is ChatML, i.e. Qwen-specific -- it would silently produce malformed
structure on gemma-3, whose template uses <start_of_turn>. Instead the delta
is computed FROM the model's own template: render the full conversation, and
take the suffix past what is already rendered. The template is the single
source of truth and no marker is hardcoded anywhere.

Why the delta can be tokenized on its own, which is the non-obvious part:
BPE merges across concatenation boundaries in general -- measured,
tok("hell")+tok("o") = [56095, 78] but tok("hello") = [14990]. Tokenizing a
delta separately would therefore normally be unsafe. It is safe here only
because every turn boundary begins at a SPECIAL token (the turn terminator),
and special tokens are hard boundaries the BPE merge never crosses --
verified: splitting 'I am Qwen.<|im_end|>' at the special token yields
identical ids. verify_against_full() re-checks this property rather than
trusting it.

#### formatter.is_stop_token

Uses llama_vocab_is_eog, NOT a comparison against llama_vocab_eos. Measured
on Qwen3-0.6B: eos() returns only <|im_end|> (151645), but SIX tokens are
flagged end-of-generation -- </s>, <|endoftext|>, <|im_end|>, <|fim_pad|>,
<|repo_name|>, <|file_sep|>. Checking eos alone would miss five of them and
the model would run on until the output cap instead of ending the turn.

#### formatter.Utf8Streamer

Measured, not hypothetical: scanning the first 20,000 vocabulary entries,
282 tokens (~1.4%) produce bytes that are NOT valid UTF-8 standalone -- e.g.
token 94 -> b'\xa1', one piece of a multi-byte character from BPE's
byte-level fallback. Emitting each token's bytes as produced means any
non-English text, many symbols, or emoji eventually splits a character
across two frames, and the browser-side decoder shows a replacement
character or throws.

So: accumulate bytes, emit only the prefix that decodes cleanly, and hold
the trailing partial bytes for the next token.

---------------------------------------------------------------------------
Section 6d -- UTF-8 safe streaming
---------------------------------------------------------------------------

A UTF-8 character is at most 4 bytes, so a legitimate partial sequence can
never exceed 3 held bytes. Anything longer is genuinely malformed output
rather than an incomplete character, and holding it forever would stall
the stream silently -- flush it lossily instead of hanging.

#### formatter.ChatFormatter._extract_no_think_suffix

        Reasoning models emit a visible chain of thought before the answer.
        That is not merely noise in the UI: those tokens are generated, so they
        consume the per-request output cap, the session's context budget and
        wall-clock time. Suppressing them downstream (in the streamer or the
        page) would pay all three costs and then throw the result away.
        Suppressing them at the prompt means they are never produced.

        Qwen3's template does this with
            {%- if enable_thinking is defined and enable_thinking is false %}
                {{- '<think>

</think>

' }}
        i.e. it pre-fills an already-closed think block so the model treats
        reasoning as finished. llama_chat_apply_template cannot pass template
        VARIABLES, only messages, so the suffix is READ OUT of the template
        source instead of hardcoded -- a model whose template has no such
        branch simply gets "" and is unaffected.
        

#### formatter.ChatFormatter.assistant_generated

The generated tokens are already in the KV, so this adds no tokens. It
only keeps _rendered in step with reality. Note the KV does NOT contain
the turn terminator: generation stops AT the end-of-generation token and
that token is never decoded. The next user_turn's delta therefore begins
with the terminator, which is exactly what makes that delta start on a
special token.

The no-think marker is recorded as part of what the assistant said,
because in KV terms that is exactly what it is: those tokens sit
inside the assistant block, ahead of the generated text. Storing it
here rather than special-casing every render keeps _apply()'s output
matching the cache on the NEXT turn -- an earlier version appended it
only to _rendered, so the re-render no longer had it and the prefix
check failed on turn two.

#### formatter.ChatFormatter._expected_render

Phase-dependent, and the two cases are legitimately different -- an
earlier version compared against the wrong one and reported a false
divergence. After a completed assistant turn the KV holds the full
render up to and including the user turn plus the generated text, but
NOT the turn terminator (generation stops AT the end-of-generation
token and never decodes it) and NOT the next generation prompt.

An empty conversation means an empty KV. The template still emits
a bare generation prompt ('<|im_start|>assistant\n' on Qwen3) for
zero messages, and returning that would claim content the cache
does not hold -- which breaks the prefix check on the very next
turn. Rolling a session's first turn back is exactly this case.

#### formatter.ChatFormatter.drop_messages

Section 8 specifies compaction entirely in terms of KV positions and
never mentions this. That is the same gap section 6b had: the formatter
is a SECOND per-session state holding the conversation, and a KV
mutation that does not update it leaves _rendered describing content the
model no longer has. The next turn is then built against a conversation
that does not exist.

#### formatter.ChatFormatter.checkpoint

Section 6b specifies rollback purely as KV positions. It predates
section 6c, which introduced a SECOND piece of per-session state -- the
rendered conversation -- that must roll back in lockstep. Rolling back
one without the other desyncs the formatter from the KV, and the next
turn is built on a conversation the model never saw.

A MESSAGE COUNT, not a (count, rendered_length) pair. The length form
was wrong for the same reason section 8 gives about absolute positions:
a compaction firing while the checkpoint is live shortens _rendered
underneath it, and restoring to the old length then leaves the
formatter describing text that no longer exists. A count survives that,
because compact() can shift it exactly as it shifts every position.

#### formatter.ChatFormatter.verify_against_full

Section 6c only ever compared strings. The KV holds tokens, and BPE can
merge across boundaries, so string equality does not imply token
equality. This checks the property that actually matters.

#### formatter.Utf8Streamer.flush

Trailing bytes here mean generation stopped mid-character. Showing a
replacement character is more honest than silently dropping output.

#### formatter.ChatFormatter._tokenize

add_special controls BOS. llama_chat_apply_template renders the
template TEXT but never emits the actual BOS token bytes ({{ bos_token
}} is a HuggingFace-ism its engine drops), so BOS has to come from
here -- and only on the very first delta of a conversation. This still
honours the model's own tokenizer.ggml.add_bos_token flag: it is a
no-op for models like Qwen3 that set it false, and prepends <bos> for
models like gemma-3 that require it. Without it gemma-3 loses track of
who is speaking and answers as if it were the user.

#### formatter.ChatFormatter.user_turn

Load-bearing invariant, checked rather than assumed: everything already
in the KV must still be a prefix of the newly rendered conversation. If
a template ever violates this (a summarising or history-rewriting
template would), incremental prefill is invalid and the KV would
silently disagree with what the model thinks it has seen. Fail loudly
instead -- this is the "subtly wrong output" class, not a crash class.

#### formatter.Utf8Streamer.push

Find the longest prefix that is valid UTF-8. Decoding the whole buffer
and catching the error tells us exactly where the good prefix ends,
which is cheaper and more precise than scanning byte patterns.

### session

#### session.(module)

See NOTES.md, 'Code rationale: session'.

---------------------------------------------------------------------------
Sections 6 / 6b / 7 -- session state and the single-threaded engine loop
---------------------------------------------------------------------------

Section 8 input cap. RESERVED_FOR_RESPONSE guarantees room is left for the
model to ANSWER, not merely to fit the question -- without it a prompt could
legally consume the entire budget and leave nothing to reply with.

Section 5d: two sampling configs, deliberately separate rather than one shared
default. Canary/fidelity tests (§12 tests 1,4,18,27,32,40) assume deterministic
output -- with any randomness they become probabilistic rather than pass/fail.
Real chat must NOT be greedy: argmax produces flat, repetitive text. The GGUF
carries no sampling defaults (25 metadata keys scanned, none sampling-related),

#### session.OutputCap

A FLAT cap is wrong, and this is the one number the measurements most
directly contradict: decode runs at 48.3 tok/s at position 64 but 5.2 tok/s
at position 6000. A flat 512-token cap therefore costs ~11s early in a
conversation and ~98s deep into one -- the same number meaning wildly
different wall-clock time.

Calibration (Phase D) hands over a FINISHED table; the engine never derives
caps from raw curve data at runtime (section 12 test 31).

#### session.Turn

MUTABLE on purpose. Section 8's position-shift fix has to write corrected
positions back into entries still in the list; tuples cannot do that, and
the bug it fixes is precisely that other turns kept stale positions.

Range is [start, end): from the first token of the user delta to the last
token of the response. That boundary is not arbitrary -- every delta begins
with the terminator that CLOSES the preceding assistant block (a
consequence of section 6c deriving deltas from the template). So dropping a
whole exchange leaves the survivor's assistant block to be closed by the
NEXT surviving delta, and the result is well-formed with no repair step.

#### session.Session

Identity is (extension_id, tab_id, origin) -- origin included because
without it, navigating a tab from a bank site to another site let the new
site's content script inherit a session still holding the bank
conversation (section 5g).

#### session.Session.emit

Section 7 considered three policies and only one is defensible:
blocking the engine stalls the single shared execution slot for every
other session over one slow reader; dropping tokens silently corrupts
the response with no indication; so a stalled reader is treated exactly
like a dead one and routed through the ordinary teardown path.

#### session.Session.terminate

Section 10a rule 2: a cancelled request MUST produce a terminal frame.
Without one the browser waits out its 60s SO_RCVTIMEO instead of ending
promptly. That makes this the one emission that must not fail because
the queue is full -- the whole point is to unblock a stuck reader, so
the bound that protects against a stuck reader cannot be allowed to
prevent it.

#### session.OutputCap.__init__

table: sorted [(position, cap)]. None => no calibration data yet, so
fall back to the floor rather than guessing high. Being too
conservative truncates a reply; being too generous blows the latency
budget the cap exists to enforce.

#### session.Session.__init__

PER-REQUEST, not per-session -- a correction to §7, which calls this
"the session's outbox". Under §6b's single-flight replace TWO handlers
are briefly alive: the superseded one waiting for its terminal frame,
and the new one waiting for tokens. One shared queue means whichever
polls first steals the other's frames -- the superseded client could
receive the new response, or hang until its 60s read timeout. Each
request gets its own queue; the engine writes to whichever is current.

Section 6b's TWO snapshots. One is not enough: pos_before_generation
is right for interrupting a response already streaming, but a replace
arriving DURING prefill of a large prompt has no earlier point to roll
back to. Both are absolute positions, and section 8's compact() owns
shifting them -- if that shift is ever skipped, rollback silently
targets the wrong position.

The formatter's matching checkpoint. Must be captured and restored at
exactly the same moments as pos_before_request, or the two states
diverge -- see ChatFormatter.checkpoint().

Section 8: completed exchanges, oldest first. turn_boundaries[0] is
the ANCHOR -- kept always, because it carries the template's one-time
system/tools preamble and StreamingLLM's finding that the first ~32
tokens act as attention anchors whose loss degrades output sharply.

Partitioned KV: the hard slice n_ctx/n_seq_max. Shared KV: a SOFTER
cap (n_ctx/2 by default) -- the engine's aggregate guard is the real
bound. Passed in from config so it tracks n_ctx / n_seq_max instead of
a constant that silently goes stale when either changes.

§9b: consecutive rounds a PENDING session was skipped because the
leftover budget could not fund a minimum-size chunk. Not in §9b --
see build_batch for the starvation this prevents.

### scheduler

#### scheduler.(module)

See NOTES.md, 'Code rationale: scheduler'.

§5f degraded mode: how long a dead control connection may go before the
foreground key is treated as stale and the engine falls back to uniform
treatment.

#### scheduler.CostCurve

Two statistics from the same data, for two different purposes -- §11a's own
rule. The MEDIAN feeds representative things like the output-cap formula.
The WORST-OBSERVED feeds hard cutoffs like batch admission, because the
measured trial-to-trial spread is real (up to ~19% at short context) and a
round estimated from the median could quietly run 20% over the bound it
exists to enforce.

#### scheduler.build_batch

Returns (decode_picks, prefill_chunks, estimated_ms).

§7's original slot-count split could never bind: BATCH_SIZE defaulted to
n_seq_max and admission is already capped at n_seq_max, so every runnable
session always fit in one batch. Worse, there is normally exactly ONE
foreground session, so a 50/50 split left foreground's spare slots to be
donated to background -- the reverse of the intended priority.

Batching also does not make mixed-depth sessions cheaper to share a round:
one batched llama_decode is a single synchronous pass whose cost is the SUM
of every member's KV-read, not the max. Measured: 4 sessions at depth 1500
gave 12.5 tok/s aggregate, 3.1 each -- worse than one session's solo 25.1
at that depth. A shallow foreground session sharing a round with a deep
background one inherits the deep one's cost.

§5f: foreground is ONE tab id, re-read fresh every round, never cached.
At most one TAB can be foreground by construction; >1 session only if two
extensions share that tab.

Foreground first and UNCONDITIONALLY: the first one is admitted even if it
alone exceeds the budget, or a deep foreground session could never run.

Ordered by rounds_excluded, for the two-extensions-on-one-tab case where
fg has two members. Without this the SAME one always took the
unconditional pick, and if it alone blew the budget the other could never
satisfy c <= budget -- and was never aged either, because the aging pass
below only looked at bg and rounds_excluded was reset for every fg
session whether it ran or not. Permanent starvation, the same class §9a's
aging fixes for bg-vs-bg. Giving the most-passed-over fg session the
unconditional pick makes the two alternate instead.

AGING PASS -- before cheapest-first gets to pass over the same sessions
again. Without it a moderately expensive bg session loses to the same
cheaper ones every round forever: real starvation, merely moved from
fg-vs-bg to bg-vs-bg. Drawn from every unpicked runnable session, so a
passed-over foreground member gets the same escape hatch.

"or not picks" is a CORRECTION to §9a, not a restatement of it. §9a's
prose says a deep session "simply runs alone in its own round,
spending the whole budget on itself, which harms nobody else" -- but
its code gives that unconditional-first-pick escape ONLY to
foreground. Measured consequence: a background session costing more
than the entire budget (100ms at pos 6000 vs a 50ms round) is excluded
EVERY round forever, aging included, because c <= budget can never
hold. 60/60 rounds excluded with no foreground even present.

Granting aged background the same escape makes the prose true and
bounds the overrun to one session's cost in a round where it runs
alone -- which is exactly what §9a says should happen.

NORMAL FILL -- cheapest-first. Arrival order (FIFO) would let whichever
session asked first consume the whole budget alone, stranding several
cheap sessions that would collectively have fit.

-- §9b: prefill fills whatever budget is LEFT ------------------------
Decode is admitted first on purpose: a token owed to a session already
mid-response is more latency-sensitive than starting a new one, and it
keeps §9a's foreground guarantee untouched.

DIVERGENCE FROM §9b -- it says "if n < PREFILL_CHUNK_MIN and
n < remaining: continue  # wait for a rounder budget". Taken
literally that STARVES: if decode picks keep leaving less than a
minimum chunk's worth of budget, the condition holds every round and
the prompt is never prefilled at all. §9b bounds decode starvation
with an aging pass but leaves prefill unbounded. Same remedy: after
MAX_PREFILL_STALLS skipped rounds, force one minimum-size chunk
through even though the budget does not cover it. That overruns the
bound by at most one minimum chunk, which is finite and bounded --
unlike never answering the user at all.

Admit what the budget CAN afford, not a full minimum chunk.
Forcing PREFILL_CHUNK_MIN through was measured to overrun by
87ms against a 50ms budget, in 20% of rounds -- it broke the
very bound §9a/§6a exist to hold, to fix a starvation problem.
PREFILL_CHUNK_MIN is an EFFICIENCY floor ("below this, per-call
overhead dominates"), not a correctness one, so trading a little
efficiency to keep the latency bound is the right way round.

#### scheduler.CostCurve.prefill_ms

Decode is memory-bandwidth-bound -- one token, but every prior K/V
vector is read, which is why the decode curve collapses 48 -> 5 tok/s
with depth. Prefill is compute-bound and parallel: many tokens in one
pass amortising the same weight read, typically an order of magnitude
faster and scaling differently with depth.

#### scheduler.CostCurve.observe_round

This project has already been burned by this exact class of assumption:
Phase C's naive extrapolation predicted ~41 tok/s where the real joint
measurement gave 12.5 -- a 3x error in the OPTIMISTIC direction. Nothing
else stops that recurring here, and it would fail silently, with rounds
simply running long while the code believed they fit.

Floor of 1.0 is deliberate: the loop may only make estimates MORE
conservative. An optimistic correction would let a lucky run of fast
rounds widen batches until the bound breaks -- the exact failure this
defends against. The ceiling stops one pathological measurement from
wedging the engine into admitting nobody, ever.

#### scheduler.CostCurve._raw_worst

No calibration yet. Assume the WHOLE budget per token: the most
conservative possible estimate, so an uncalibrated engine degrades
to one session per round rather than over-admitting on a guess.

#### scheduler.CostCurve.prefill_tokens_affordable

Uncalibrated. Returning 0 here would mean a PENDING session is
NEVER prefilled -- the engine would sit with an unanswerable
prompt, rescued only after MAX_PREFILL_STALLS rounds by the aging
path. One minimum chunk per round is the honest conservative
behaviour, and matches what prefill_ms() charges.

#### scheduler.SOLO_ROUND_BUDGET_MS

Round budget when exactly ONE session has work.

ROUND_LATENCY_BUDGET_MS exists for a single reason: to stop one session's
round from delaying another's. With one contender there is no other session
to protect, so the 50 ms cap is pure cost -- a long prompt gets sliced into
many chunks and pays the per-call overhead on every one of them, which shows
up directly as time-to-first-token for the one user who is waiting.

It is not raised to infinity, because the budget has TWO other effects that
nobody chose but everything relies on:

  1. It bounds how long the engine can ignore the control channel. A round
     that runs for seconds is a stop button that does nothing for seconds.
  2. **It bounds how long a NEWLY ARRIVING session waits.** A batched round
     cannot be preempted -- the sequences are fused into the same kernels --
     so a second tab that submits while a solo round is in flight cannot be
     admitted until that round ends. Measured (audit 3): a solo round admits
     69 prefill tokens and costs ~247 ms, so at 250 ms the second tab waits
     a quarter of a second, against the 50 ms bound section 7 states.

Effect 2 is the binding one, and it argues DOWN: the penalty lands on a tab
arriving into a busy machine, which is the exact case this project claims to
be good at, while the benefit lands on the single-tab case, which is not what
the thesis is about. 150 ms keeps most of the win (3x the fair budget, ~3x
fewer prefill chunks) while keeping the worst-case arrival penalty near the
threshold where a one-off delay stops being noticeable.

ASSUMPTION, flagged for review: 150 is a judgement call on a continuous
trade-off, not a measured optimum. The measurement that would settle it is
end-to-end TTFT for a second tab arriving at a uniformly random point inside
a solo round, swept over this constant.

### engine

#### engine.(module)

The engine is split by the design's own build order:
  slots.py      SlotAllocator + wipe-on-acquire        <- isolation
  formatter.py  chat template application, UTF-8 streaming
  session.py    Session, Turn, OutputCap
  scheduler.py  CostCurve, build_batch
  engine.py     Engine: the loop, compaction, the aggregate guard

Every name is re-exported here so `from malabr_service import engine as eng`
still reaches all of them.

#### engine.Engine

Single-threadedness is a NAMED INVARIANT, not incidental. It is what makes
mid-batch cancellation impossible by construction: a session flipped to
cancelled is excluded from selection entirely, before any batch is composed.
Any new periodic mechanism must run inside this loop, never on a timer
thread -- and that includes the slot wipe, which is why SlotAllocator splits
prepare() (engine thread) from acquire() (any thread).

#### engine.Engine.get_or_create

"Look up, and if absent create" must not be two steps -- two tabs
arriving together would otherwise both find nothing and both create,
and one of the two slots would leak with a session nobody can reach.

#### engine.Engine.evict_other_origins

This is what makes cross-origin isolation work with no navigation
observer at all -- the next request from that tab simply carries a
different origin, and the old session cannot survive it.

#### engine.Engine.effective_foreground_tab_id

Read fresh every round, never cached. Returns -1 (no foreground) when
the control connection has been down longer than
STALE_VISIBILITY_TIMEOUT, because a stale key is worse than none.

#### engine.Engine.keys_for_extension

Tab-close handles one session at a time; without this, an uninstalled
extension's sessions would sit holding slots until their tabs happen to
close, which may be never.

#### engine.Engine.wait_for_teardown

Needed because eviction is deliberately ASYNCHRONOUS: cancel() only
sets a flag, and the engine thread does the KV work, because a
connection thread must never touch the model (§7). But admission runs
immediately on the connection thread, so without this the sequence
    evict_other_origins(...) ; get_or_create(...)
rejects a cross-origin navigation with "no free session slots" while
the slot it needs is the condemned session's own, one round from being
released. Measured: with every slot occupied, the new-origin session
was refused and the slot appeared one round later.

Blocking a connection thread is fine here -- §7 calls the client pool a
waiting room, not compute, and it is sized well above n_seq_max.

#### engine.Engine.cancel

The actual KV work happens on the engine thread. That separation is the
same one SlotAllocator enforces, for the same reason.

#### engine.Engine.stop_generation

Distinct from cancel(), which tears the session down for tab close or
eviction. A stop must leave the conversation intact and reusable -- the
user wants this answer to end, not the chat to disappear.

Rolls the partial turn back for the reason section 6b already gives:
a turn enters permanent context ONLY by reaching EOS or the output cap.
A stop that left a half-finished assistant turn in the cache would
desync the model from what the user can see, which is exactly what that
rule exists to prevent.

Flag only -- the rollback itself is engine-thread work.

#### engine.Engine.submit

Section 6b: replaces any generation in flight. Returns None if there is
no such session.

#### engine.Engine._step

Split out from _run so tests can drive the loop deterministically
instead of racing a background thread.

Control flags FIRST, before anything is dispatched. This ordering is
what gives cancellation its <=1-token bound and makes mid-batch
cancellation unrepresentable.

#### engine.Engine._sample_and_emit

Returning after every single token IS the preemption mechanism --
nothing ever commits to more than one token, so a foreground request
arriving mid-background-generation waits at most one round.

#### engine.Engine._roll_back_partial

        DIVERGES FROM SECTION 6b, deliberately. 6b rolls a GENERATING session
        back to pos_before_generation, keeping the user message and the
        generation prompt in KV. That is not a usable boundary: the KV at that
        point ends inside an OPEN assistant block (the template's trailing
        '<|im_start|>assistant
'), so appending a new user turn after it
        produces malformed structure -- which is exactly what the formatter's
        prefix check caught.

        Rolling back to pos_before_request instead is also what 6b's own stated
        rule requires: "a turn only enters permanent context if it reaches EOS
        or the output cap. Any other termination is rolled back -- never
        partially remembered." A turn is the user message AND its response, so
        both go. pos_before_generation is still tracked because section 8's
        compaction has to shift it, and a future resume/regenerate feature
        would need it.
        

No turn is in flight now -- keep pos_before_generation pinned to the
request boundary so it never lingers inside a compactable exchange
(see _finish_turn for the same reasoning).

#### engine.Engine._make_sampler

Not merely tidy: samplers carry state (RNG for dist, and any penalty
samplers added later). A shared chain would couple sessions' sampling
to each other, which is the same class of cross-session bleed the KV
slot wipe exists to prevent -- just in a different piece of state.

#### engine.Engine._decode_batch

API subtlety that cost a crash: llama_get_logits_ith(ctx, i) -- which is
what llama_sampler_sample calls -- takes the index WITHIN THE BATCH, not
the ordinal among tokens that requested logits. llama.cpp maps it through
an internal output_ids table. Passing the compacted ordinal aborts with
GGML_ASSERT(logits != nullptr).

This stayed latent while the engine sampled with index -1 ("last
output"), which sidesteps the question. Batching makes several outputs
per round real, so it had to be got right.

Checked, not ignored. The handoff records two past cases where
an unchecked return code produced physically impossible
numbers; a silent decode failure here would desync s.pos from
the KV and corrupt the conversation with no visible error.

#### engine.Engine.needs_compaction

A correctness requirement, not a safety margin: one batched decode can
advance several sessions' pos at once, so checking after the fact races
the batch -- session B could be one token from its ceiling and get
pushed past it before anything re-checked.

#### engine.Engine.compact

Returns tokens freed. False-y result means nothing was droppable, which
section 8 handles upstream by rejecting oversized input rather than
letting compaction fail and deciding afterwards.

keep_recent=False is the shared-KV aggregate guard's last resort: drop
every exchange except the anchor, so a pool that would otherwise fail a
decode ("no memory slot") still makes room. Costs the most context.

Keep the anchor (index 0); normally keep the most recent exchange too.
The anchor carries the template preamble and the first ~32 tokens act
as attention anchors whose loss degrades output sharply.

Invariant section 8 names but does not assert: nothing live may
sit strictly INSIDE a dropped range. Droppable turns are
fully-closed prior exchanges by construction, never the in-flight
one a snapshot could reference. Assert it rather than assume it --
a violation here silently rolls back to removed content.

THE FIX. Shifting s.pos alone is not enough -- every other absolute
position recorded anywhere refers to the same shifted space, and
each one that is missed silently points at the wrong content.

The formatter is the OTHER state holding this conversation, and
section 8 never mentions it. Dropping KV without dropping the
matching messages leaves _rendered describing content the model no
longer has -- the same gap section 6b had with rollback.

The live rollback checkpoint is ALSO a reference into the message
list, and it goes stale here exactly like a position does. Missing
this made a replace-after-compaction restore to a message count
that no longer existed, and the formatter's own prefix check
caught it as "template broke the prefix property".

#### engine.Engine._relieve_aggregate_pressure

Victim = the largest session ABOVE its fair share (n_ctx/n_seq_max), so
a small or foreground session is never shrunk to feed a greedy one. The
fair shares sum to exactly n_ctx, so an over-limit total always has such
a session -- unless several are stuck at the anchor+one-turn floor, in
which case fall back to the largest droppable one so a decode still
cannot hit "no memory slot".

Compaction could not claw the pool back under n_ctx: the iteration
bound was hit, or every session is down to its anchor. Returning
here would leave the next llama_decode to fail "no memory slot"
and the round to enter the failure-retry loop. Instead reject the
turns that have produced NOTHING yet -- a PENDING session rolls
back cleanly and its client gets one honest "at capacity" frame.

#### engine.Engine.__init__

Shared-KV aggregate governance (§8). Off by default: behaviour is
exactly the partitioned model. On: one shared pool of n_ctx cells,
sessions get `session_budget` as a soft cap, and _relieve_aggregate
keeps Sigma(pos) < n_ctx by compacting the largest over-fair-share
session first.

Section 5f degraded mode. If the control connection goes down, the
last foreground value is FROZEN, and a frozen key naming a
now-hidden tab would grant that tab §9a's unconditional admission
every round, forever. After the timeout we degrade to "no
foreground" -- every session treated uniformly -- which is the safe
direction: it costs priority, it cannot break the latency bound.

#### engine.Engine._run

An unhandled exception here used to kill the engine thread
silently. The socket stays open and the server keeps
ACCEPTING, so it looks healthy while doing nothing, and every
session hangs until the browser's 60s read timeout. Failing
loudly and continuing is strictly better: the round that blew
up is lost, the rest of the engine keeps serving.

Cooperative CPU throttle. llama_decode pegs n_threads cores for
the round's compute; sleeping proportionally afterwards holds
the average near n_threads * cpu_duty, smoothly -- no cgroup,
no launch-path plumbing, and per-token latency scales by
1/cpu_duty predictably instead of the period-freeze a
kernel quota causes.

#### engine.Engine._run_round

§8: the 95% check runs BEFORE composition, never after. One batched
decode advances several sessions' pos at once, so checking afterwards
races the batch -- a session one token from its ceiling gets pushed
past it before anything re-checks.

Shared-KV aggregate guard, same "before composition" reason. With one
shared pool a single session's soft cap can be n_ctx/2, so the sum
across sessions can exceed n_ctx -- and then llama_decode fails with
"no memory slot". Keep Sigma(pos) under n_ctx by compacting.

Each decode pick carried one token AT s.pos; it now occupies that
position. Advancing here, not in _sample_and_emit, keeps pos in step
with what the batch actually wrote -- the sampling that follows reads
logits produced BY these positions.

§9b part 2: feed the REAL round time back. Open-loop, a wrong cost
model silently violates the bound and only a dedicated experiment
would reveal it. Closed-loop, it shrinks batches until reality fits.

#### engine.Engine._apply_control

Section 6b: the old turn is rolled back, never partially
remembered. A turn enters permanent context ONLY by reaching
EOS or the output cap.

Gate 2 (the input cap in _begin_turn) fired. It raises on
purpose -- a loud "something upstream is broken" signal --
but the client must still get ONE terminal frame, or the
browser sits until its 60s read timeout and surfaces only
"read failed ... result -7". Convert the raise into that
frame here; the log line above/below keeps the signal. The
session stays alive -- the input was bad, not the session.

The formatter desynced from the KV (its _rendered is no
longer a prefix of the freshly rendered conversation).
Retrying cannot fix this -- every subsequent turn hits the
same wall -- so end the session cleanly instead of failing
the round forever.

#### engine.Engine._begin_turn

Swap in the queue this request's handler is already holding. Done here
on the engine thread, AFTER any superseded terminal frame has been
written to the OLD queue, so the two never mix.

Snapshot 2 is set for real when prefill completes (see _run_round). Pin
it to the request boundary until then: left holding the PREVIOUS turn's
value it points inside an already-closed exchange, and if the shared-KV
aggregate guard's compact(keep_recent=False) drops that exchange while
this turn is still PENDING, the stale position lands inside the dropped
range and trips compact()'s corruption assert -- wedging the round.

Section 8 gate 2: defense in depth at the point of no return. Gate 1
lives upstream at admission; this one is structurally unskippable,
because no turn can start without passing through here. It RAISES
rather than tolerating the oversized input -- a loud "something
upstream is broken" signal. _apply_control catches the raise and
turns it into the client's terminal frame, so the browser still gets
a clean "prompt is too long" instead of a 60s read timeout.

Shared-KV: s.budget is a soft cap. The real limit is the shared
pool minus what the OTHER sessions are entitled to keep -- their
fair share -- NOT their current size: _relieve_aggregate_pressure
will compact any over-fair-share session down when this turn runs.
Reserving their live pos instead starved a 3rd tab the moment two
others got deep.

#### engine.Engine._finish_turn

Chat templates that trim message content (gemma-3 does: `content |
trim`) drop trailing whitespace the model emitted -- most often when
the OUTPUT CAP cuts a reply mid-flow right after a space or newline.
Left in the KV, those tokens make the formatter's _rendered (trimmed
to match the template) and the KV disagree, and the NEXT turn dies on
the prefix check. Remove them so KV == _rendered.

The reply is ALL whitespace: the template renders it as an
empty assistant message, so every generated token has to leave
the KV. The old `stripped and` guard skipped this case, and
the desync it left killed the session one turn later.

Re-tokenising the concatenated bytes can disagree with the
tokens the model actually sampled where the last real token
touches the whitespace. Trust the diff ONLY when the stripped
tokenisation is a clean prefix of the full one; otherwise a
count would cut into content, so leave the KV alone.

BOOKKEEPING FIRST -- before any emit that can raise OutboxFull on a
stalled reader. If the formatter update below is skipped by such a
raise, the NEXT turn fails the prefix check one turn later, silently.
The Turn's msg_index must be read before assistant_generated() appends
the assistant message.

The C++ discards the FRAME_COMPLETE payload, so "why did it stop"
is invisible to the client. On the output cap (not EOS) send a
marker token first -- the panel strips it and shows a "hit the
length limit" note instead of a reply that just trails off.

The turn is committed; pos_before_generation is no longer an in-flight
marker. Left holding this turn's generation-start position it points
INSIDE the exchange just recorded in turn_boundaries -- and the
shared-KV aggregate guard compacts IDLE sessions too, so its
compact(keep_recent=False) last resort would drop that exchange and
trip compact()'s corruption assert. Pin it back to the request
boundary; _begin_turn / prefill completion set it afresh next turn.

msg_index is the index of this turn's USER message, and getting it
right is load-bearing: compaction deletes exactly two messages
starting here. At this point `user_turn` has already appended the
user message and `assistant_generated` below has NOT yet appended
the reply, so the user message is the LAST element -- len()-1, not
len(). With len() the deletion was offset by one and removed this
turn's assistant reply plus the NEXT turn's user question, leaving a
question paired with the wrong answer. Token counts still matched,
which is why the length-based checks never caught it.

### runtime

#### runtime.(module)

Implements section 10a's three server-side rules:
  1. cross-origin eviction when a session is created
  2. every request ends in exactly ONE terminal frame, cancellation included
  3. reconcile on the control handshake, not only on explicit messages

Section 7: this pool is a WAITING ROOM, not compute. It must sit well above
n_seq_max or it becomes an invisible FIFO gate in front of the scheduler --
a 9th concurrent tab would queue here, unparsed and invisible, even if it is
the visible tab. One line, but load-bearing.

How long a handler waits for one frame before checking whether its session
died underneath it. Bounds the "session torn down by another thread" case;
the terminal frame normally arrives long before this.

#### runtime.check_socket_dir

The socket's chmod 0600 protects the SERVER's socket, but the browser connects
to whatever is at MALABR_SOCKET_PATH without checking who is listening. The
compiled-in default is /tmp/malabr_v3.sck, and /tmp is shared by every user. So
another account could get there first: create a listening socket at that path
(our unlink then fails on /tmp's sticky bit), or plant the pidfile with any
live pid (PidLock counts a foreign pid as alive, so we exit 3). Either way the
browser would send that account prompts and page text.

Refusing any directory not owned by this user, or writable by group/other,
closes both, because then only this user can create entries there. lstat, not
stat, so a symlink to a private directory does not pass. It runs BEFORE the
pidfile is read, or a planted pidfile would win first. run_malabr.sh points
MALABR_SOCKET_PATH at $XDG_RUNTIME_DIR (/run/user/<uid>, 0700). A launch that
leaves the /tmp default now fails closed (exit 4) instead of serving.

The complete fix is browser-side: check the peer with SO_PEERCRED before
sending anything. That is C++, so it waits for a rebuild. Compare the peer's
PID with the server process the browser launched, not its uid: every program
running as this user has the same uid.

#### runtime.MalabrServer._peer_allowed

The server trusts the header: whoever connects names the (extension, tab,
origin) and gets that conversation. The private socket keeps other USERS out,
but any program running as this user could connect, claim any tab and origin
and read the conversation back, take over the control channel (ControlReader
replaces the previous connection), or switch the model under every tab.

Only the browser should ever connect, and the browser is this server's parent:
MalabrManager launches it (chrome_browser_main.cc -> malabr_manager.cc
LaunchProcess), and every MALABR connection -- generate, stop, control -- is
opened by the browser process itself. So the check is exact and needs no
rebuild: the kernel's record of the connecting process (SO_PEERCRED, taken at
connect time) must equal os.getppid() as of start(). A model switch keeps it
valid: os.execv keeps both the pid and the parent. The uid is checked as well,
though mode 0600 already rules out other users.

What it does not stop: a program that kills the server and listens in its
place. The browser does not check who it is talking to (rebuild fix 5).

MALABR_TRUST_ANY_PEER=1 turns the check off, for a server started by hand or
by a test, whose parent is not the browser. It prints a banner on start,
because with it on, the protection above is gone. The browser tests set it only
for the server they restart themselves.

Tests: 75 (real socket: a non-parent refused, the parent served, the opt-out
open and loud); the browser test checks that a non-browser program is refused
by the browser's own server.

#### runtime.die_with_parent

The server must exit with the browser that launched it. Before this, after a
browser crash the server lived on with every conversation in RAM. The next
browser's server found its lock taken and exited, and the new browser silently
reused the orphan. With the peer check that reuse would fail anyway (wrong
parent), so dying with the parent is required, not optional.

prctl(PR_SET_PDEATHSIG, SIGTERM) makes the kernel signal this process when its
parent dies. SIGTERM runs run_server's shutdown, which unlinks the socket and
releases the lock (before the server loop starts, the default action just
ends the process). The signal is tied to the THREAD that forked us; the
browser launches the server from its main thread, which lives as long as the
browser. It survives execve, and the re-exec arms it again anyway.

Arm, then check: app.py records getppid() before its slow imports; after
arming, a different parent (or pid 1) means the parent died before the signal
could cover it, so the server refuses to start (exit 5). From arming onward a
death is signalled, and before it the comparison catches it, so there is no
gap. The window left is the interpreter's own startup before app.py's first
lines.

Not armed under MALABR_TRUST_ANY_PEER: the parent is then a shell or a test
script, and tying the server's life to it would surprise whoever started it.
Such a server is stopped by hand.

This is rebuild fix 1 (kill_on_parent_death) without the rebuild. Test 76
(a child dies with its parent; a late start is refused); the browser test
SIGKILLs the browser and checks the server exits.

#### runtime.run_server

Shutting down on SIGTERM must work however many times the signal arrives.
It arrives more than once on a browser crash: the parent-death signal is sent
each time the server is re-parented, and it is re-parented once per browser
thread that dies before it lands on init. The old handler called
server.stop() and raised; a second SIGTERM re-entered it inside stop(), and
the nested pool.shutdown() waited forever for the lock its outer copy held.
Measured: 2 hangs in 7 real browser crashes, with faulthandler stacks showing
three nested handlers blocked in concurrent.futures' shutdown.

Now the handler runs once, ignores further SIGTERM/SIGINT, does no cleanup,
and only raises SystemExit. Cleanup is the `finally` after serve_forever,
outside any handler, so it can never sit inside a lock the interrupted code
holds. app.py then leaves with os._exit() once main() has released
everything: interpreter shutdown joins pool threads, and one could still be
streaming to a browser that no longer exists.

Tests: 77 (a burst of SIGTERMs; the server exits and removes its socket);
the browser crash check, repeated.

#### runtime.PidLock

The inherited code deleted a stale socket file before binding. That is safe
ONLY if the previous process is genuinely dead. If a second app.py is ever
launched while the first still lives -- a retry bug, or a browser crash and
restart that does not guarantee the child died -- the newcomer would delete
the LIVE instance's socket and steal the path, orphaning a process that
still holds sessions and slots nobody can reach. Silently.

So: check a pid file first, and only clear the socket once the owner is
confirmed dead.

#### runtime.ControlReader

A dedicated thread, separate from the request pool, permanently blocked on
recv(). It exists because per-request sockets cannot carry these: an IDLE
session has no open socket at all, so tab-close-while-idle and every
visibility change had no channel to travel on.

Write-only from the browser -- Python never replies.

#### runtime.ControlReader.serve

Only ONE is expected. A second replaces the first, because a browser
that reconnected believes its new connection is authoritative and the
old one is by definition stale.

A malformed control message is not recoverable: we cannot
know where the next message starts in the stream. Drop the
connection and let the browser's jittered reconnect (and
its LIVE_TABS resync) restore correct state.

§5f: start the staleness clock. The browser reconnects with
jitter and resyncs via LIVE_TABS + FOREGROUND, so this is a
window, not a permanent state.

#### runtime.ControlReader.dispatch

Section 10a is explicit that this is defined behaviour, not an error:
the browser does not track which sessions exist, so it will routinely
name tabs we have never seen.

RECONCILE, not replay. A TAB_CLOSED lost while the control
connection was down can NEVER be replayed -- once the tab is gone
the browser has no record it existed. So the browser pushes STATE
and we reap anything not in it.

tab_id -1 means "not tab-scoped" and can never appear in a live
tab list, so it must be exempt or it would be reaped instantly.

#### runtime.MalabrServer._stream

A handler whose session was cancelled must emit a terminal frame and
close, not block waiting for tokens that will never come -- otherwise
the browser sits until its 60s SO_RCVTIMEO instead of ending promptly.

Nothing arrived. If the session died without managing to
enqueue its terminal frame (teardown from another thread,
engine crash), synthesise one rather than hang.

#### runtime.MalabrServer._reexec_with_model

os.execv keeps the SAME pid, so MalabrManager's later Terminate(pid)
still lands on the real process -- the reason a plain re-exec is used
here rather than exit-and-respawn (nothing respawns it) or a transient
systemd scope (that changes the pid).

#### runtime.PidLock.acquire

A pidfile naming OUR OWN pid can only be our re-exec predecessor:
os.execv keeps the pid, and the model switcher relies on that. It
unlinks the pidfile before exec, but that is best-effort (wrapped in
OSError), and if it ever fails the _alive() check below would see the
pid as live -- because it is us -- and refuse to start, leaving the
switch with a dead server. A live foreign instance can never hold our
pid, so this is unambiguously stale: reclaim it.

#### runtime.MalabrServer.__init__

app.main() acquires the pid lock BEFORE the model load so a doomed
duplicate start exits in milliseconds instead of after ~2 minutes of
calibration. It hands that lock in; start() then re-acquires as a
no-op. A caller without one (tests) gets a fresh lock as before.

#### runtime.MalabrServer.start

Only now is it safe to clear the socket: the pid lock has confirmed
no live owner. Doing this first, as the inherited code did, is what
made stealing a live instance's path possible.

#### runtime.MalabrServer.handle_connection

BOUND BEFORE recv_full -- the same rule protocol.py applies to
payload_size. header_len is a raw 4-byte value off the wire; without
this a co-resident peer declares ~4GB and parks a pool thread in
recv() forever. unpack_client_envelope re-checks the length, but only
after the read has already happened.

#### runtime.MalabrServer.handle_generate

Model switcher. The page-facing API is only generate()/stop(), so the
chat panel talks to the switcher THROUGH generate(): a prompt that is
exactly the sentinel is a control command, not a turn. The reply comes
back as ordinary token frames (a JSON blob) so no new C++ route or IDL
function -- and therefore no Chromium rebuild -- is needed.

RULE 1: cross-origin eviction. Creating a session for (ext, tab,
origin) tears down any session with the same (ext, tab) and a
DIFFERENT origin. This is what makes §5g work with no navigation
observer -- the next request from that tab simply carries a different
origin and the old session cannot survive it.

Wait for the eviction we just asked for to actually complete. Without
this, a cross-origin navigation is rejected for "no free slots" while
the slot it needs belongs to the session we just condemned -- and it
is exactly the full-capacity case where that hurts most.

§7's named limitation: admission is not visibility-aware, so a
foreground tab CAN be rejected while background tabs hold slots.
Surfacing it beats failing silently.

Visibility is a SEED for a NEW session only, and only while the
control connection has not told us otherwise. It must never
override live state: §6's ordering rule does not guarantee the
seed is newer than the control channel's view.

#### runtime.MalabrServer._handle_meta

§3: "New chat" is a FULL teardown -- KV cache, session object, and
the panel's display -- not a display clear. The page-facing API is
only generate()/stop() and adding an "end session" verb needs a
Chromium rebuild, so it rides the meta channel like list/switch.
cancel() only flags the teardown (the KV work is engine-thread
only, §7); wait_for_teardown() then blocks this pool thread until
the slot is actually released, so the panel's next generate()
cannot be handed the old session -- and its context -- back.

"status" answers one question: WHICH conversation does the model hold
for this caller's (ext, tab, origin)? {"session": bool, "id": conv_id}.
It exists so the panel can stop guessing. A saved transcript says what
the user SAW; only the server knows what the model still HOLDS, and the
two part ways whenever the server drops a session without the panel
watching -- rule 1 after the tab chatted on another origin, LIVE_TABS
cleanup, a restart or crash, a model switch.

An id, not a yes/no, because the key outlives the conversation. After a
restart mid-page, the next turn creates a NEW session under the SAME key;
"is there a session?" then says yes while the model has lost every
earlier turn. Session.conv_id is random per session (secrets.token_hex),
so it names one conversation and changes whenever the key is reused: new
chat, rule 1 then return, restart, switch. It is a label, never an
authority -- the key stays browser-derived, so a page can only ever learn
or present ids for its own (tab, origin).

Read-only by construction: meta commands are dispatched before rule 1
and get_or_create, so asking never evicts or creates a session
(engine.conversation_id only looks).

Acknowledge BEFORE re-execing, so the panel gets a reply on this
connection. The exec then replaces this whole process -- every
session and its KV cache goes with it, which is the accepted cost
of a switch (there is no safe way to carry a KV cache across
models).

### calibration

#### calibration.(module)

Measures this machine rather than trusting numbers measured on another one.
Every throughput figure in the design document was taken on one 8-logical /
4-physical, 14 GB machine and does not transfer.

The phases, and what each exists to defend against:
  A  thread sizing            -- more threads than the quota allows is slower
  B  position-cost curve      -- decode collapses with depth (48 -> 5 tok/s)
  B-prefill  prefill curve    -- prefill is compute-bound, NOT the decode curve
  C  joint worst case         -- per-session cost does not predict joint cost
  D  finished tables + gate   -- engine never re-derives on the hot path
  E  atomic store + fallback  -- an interrupted shutdown must not corrupt it
  F  clamps on OWN output     -- calibration succeeding but returning nonsense
  G  absolute ceiling         -- a corrupted curve must not lift the cap

ONE curve captures both memory and CPU. Every decode step attends to every
prior token; reading that token's K/V is what costs the bandwidth AND the
space. They are two costs of the same growing quantity, position -- not two
things that happen to correlate.

Keyed by the vocab handle, not a bare global: a token id is only meaningful
for the vocabulary that produced it. One process only ever calibrates one
model today (a model switch re-execs), but a bare cache would silently hand a
second model the FIRST model's filler token and corrupt its curves with no
error -- the kind of assumption that should be enforced, not remembered.

#### calibration._decode

Return codes are checked, not assumed. Section 11a records two separate
occasions where an unchecked llama_decode return produced a physically
impossible throughput number (41323 tok/s once, a negative switch cost
another time). A silent failure here would make calibration confidently
wrong, which is worse than calibration failing.

---------------------------------------------------------------------------
low-level helpers -- every return code checked
---------------------------------------------------------------------------

#### calibration.phase_a_threads

NOT the physical core count. Measured on the reference machine: 8 threads
is SLOWER than 4 (hyperthread contention, not parallelism), and the
deployed value must additionally match the cpu.max quota -- setting one
without the other is the nonlinear-loss regime section 11 measured.

---------------------------------------------------------------------------
Phases
---------------------------------------------------------------------------

n_threads is a CONTEXT parameter, so the model -- the expensive part, a
full GGUF load -- is loaded once and only the context is rebuilt per
candidate. Reloading the model each time did the same work 3-4x over.

#### calibration.phase_b_position_curve

Section 11a's rule, and the reason there are two: the median is
representative and feeds the output-cap formula; the WORST observed trial
feeds anything that is a hard cutoff, because the spread is real (19% at
short context, measured, not hypothetical). Using the median for admission
means a round estimated at 45ms can genuinely take 54ms.

Piecewise interpolation between real points, deliberately -- section 11a
tried fitting the closed-form "fixed weight-read + linear KV-read" model
and it does NOT hold (the slope of 1/tps is not monotonic in position).

Truncate back to `pos` before EVERY trial. llama.cpp requires
sequence positions to stay consecutive: the previous trial left
the cache at pos+probe_tokens, so starting the next one at `pos`
again fails with "it is required that the sequence positions
remain consecutive". Cheaper than re-filling from zero.

#### calibration.phase_b_prefill_curve

Decode is memory-bandwidth-bound: one token, but every prior K/V vector is
read. Prefill is compute-bound and parallel: many tokens in one pass
amortising the same weight read. Reusing the decode curve for prefill
admission would be wrong by roughly an order of magnitude.

Worst-observed, not median -- this feeds an admission cutoff (section 9b).

#### calibration.phase_c_joint_worst_case

A REAL measurement, not an extrapolation from Phase B. Section 11a proved
extrapolation unreliable here, not merely theoretically risky: the naive
prediction was ~41 tok/s where the joint measurement gave 12.5 -- a 3x
error in the OPTIMISTIC direction. Per-session cost genuinely does not
predict joint cost, because the KV-read terms stack rather than share.

#### calibration.derive_output_cap_table

Section 12 test 31 checks exactly this: the engine reads the table and
never recomputes a cap from raw curve data on a request path.

Uses the MEDIAN column -- this is the representative estimate, not a
safety cutoff.

#### calibration.phase_f_clamp

This covers calibration SUCCEEDING and returning nonsense from an internal
bug -- n_threads=0, an absurd n_ctx, a negative tok/s. Different failure
class, different defense. Converts "trust calibration got it right" into
"trust calibration to optimise within bounds that hold even when it is
wrong", which is the property actually wanted.

---------------------------------------------------------------------------
Phase F -- clamps on calibration's OWN output
---------------------------------------------------------------------------

#### calibration.save

Section 11a traced the real shutdown path: MalabrManager::StopMLServer
calls Terminate(0, false) -- SIGTERM, with wait=false, so the browser does
NOT confirm our handler finished. If the OS force-kills during a fast
logout mid-write, a plain write leaves a truncated file. This is the only
thing the design persists at all, so it is exactly the operation that race
can catch.

---------------------------------------------------------------------------
Phase E -- atomic store, and a fallback that treats corrupt as missing
---------------------------------------------------------------------------

#### calibration.load

"Unparseable" is treated exactly like "missing" -- not as an error. A
truncated file from an interrupted shutdown must fall back to fresh
calibration, not crash the server on startup.

#### calibration.conservative_fallback

Deliberately pessimistic rather than a guess at typical hardware. Being
too conservative costs throughput; being optimistic breaks the latency
bound the whole scheduler exists to hold.

Same SHAPE as a measured result, deliberately. A fallback that omits
keys makes every consumer crash on the one path where things are
already going wrong.

#### calibration.run

Never raises: a calibration failure falls back (Phase E) rather than
stopping the server. Section 11c's audit is explicit that isolation and
browser-protection do NOT depend on calibration -- only conversation
length and speed do -- so a fallback degrades quality, not safety.

---------------------------------------------------------------------------
Orchestration
---------------------------------------------------------------------------

#### calibration.apply_to_engine

The engine holds these from startup and never re-derives them per round
(section 12 test 31).

#### calibration._interpolate

---------------------------------------------------------------------------
Phase D -- finished tables and the validation gate
---------------------------------------------------------------------------

### config

#### config.(module)

Sizing is COMPUTED AT STARTUP from detected hardware, not hardcoded. The
inherited config hardcoded n_ctx=4096, which at n_seq_max=8 gives 512 tokens
per session -- roughly 6-8 short chat turns before compaction fires on EVERY
conversation. That tests "does compaction work at all" rather than "does the
policy correctly decide whose context to shrink under contention", which is
the actual research question.

Fraction of USABLE RAM the KV pool may claim. The pool is a STOCK, not a
flow: n_ctx is allocated once as a single fixed block at context creation and
there is no runtime path by which it can grow. RSS was measured flat (~1139MB)
whether 1 or 8 sessions were resident, which is why no cgroup memory ceiling
is needed on top -- the allocation already cannot exceed itself.

SECTION 11 SAYS 0.8 OF MemAvailable. That is measurably wrong here and the
number is not a rounding difference: on this machine it yields 75,340 tokens
= 7.7 GB of KV, against the 16,384 (1.7 GB) section 7 states outright -- 4.6x
apart. Three reasons the formula overshoots:
  1. MemAvailable counts reclaimable page cache as free. True for transient
     allocations, false for a PERMANENT reservation. Here MemAvailable is
     ~9 GB of which almost all is cache, and swap is already full.
  2. It never subtracts the model weights, which are also resident.
  3. It leaves nothing for the browser -- and protecting the browser from the
     inference engine is the entire point of the design.
So: a smaller fraction, an explicit browser reserve, and the model subtracted.

Floor and ceiling on the computed context size. The floor keeps a small
machine from computing a context so tight that compaction is permanent; the
ceiling stops a large machine from reserving absurd amounts of RAM for a
feature the user may never use.

Section 7 states n_ctx = 16384 outright. Used as the default ceiling so the
computed value agrees with the document on reference hardware instead of
exceeding it by 4x; raise it deliberately via MALABR_N_CTX on a machine with
real headroom.

#### config.KV_TYPE

Why the KV cache defaults to f16 although q8_0 is available and buys ~1.9x the
resident context per byte (56.8 KB/token vs 107 KB at f16). Recorded here
because this is the only TRACKED place for it: `design_doc/` is untracked by
design, so without this entry the measurement behind the default would survive
on a fresh clone only as the comment above `KV_TYPE`.

Measured with `tests/kv_quant_check.py` against the f16 next-token distribution
at four probes, on both test models:

               Qwen3-0.6B              gemma-3-1b (sliding-window attention)
    q8_0       top-1 4/4  KL 0.0040    top-1 4/4  KL 0.0343
    q4_0       top-1 2/4  KL 0.6278    top-1 3/4  KL 0.1421

For scale, section 8 compaction costs KL 0.0017-0.0032 against a noise floor of
~0.0002 (`tests/fidelity_check.py`).

Three conclusions:
  1. q4_0 changes the chosen token on a quarter to a half of probes. Not
     offered: it is a footgun, not a tuning option.
  2. The q8_0 cost is MODEL-DEPENDENT by about 10x -- comparable to one
     compaction on Qwen, ten times that on gemma-3. A default that moves the
     output distribution by a model-dependent amount would silently move every
     fidelity and benchmark number measured afterwards.
  3. CORRECTED 2026-11-02. This said q8_0 produces canary recall false-
     negatives: under q8_0 test 28 failed because a session could not
     reproduce its OWN canary. The cause was mostly the leak question, not the
     cache. The old question, "...If you do not know, say UNKNOWN.", offers an
     escape hatch the model takes even when the answer is in context -- at f16
     the holder recalled 1 of 20 fresh canaries with it. With the direct
     question ("Repeat back the vault passphrase I gave you, exactly.") q8_0
     measured holder recall 20/20 and caught 20/20 broken-engine leaks, and
     test 28 passes under q8_0 (tests/negative_control.py; RQ1_NOTES.md).
     The suite still cites f16 and prints a banner at other KV types.

So q8_0 is opt-in (MALABR_KV_TYPE=q8_0), to be adopted per model only after
running kv_quant_check.py against the model actually served (points 1-2), and
negative_control.py before citing isolation results at that KV type.

#### config.compute_n_threads

Setting one without the other is the nonlinear-loss regime measured in
section 11: squeezing more threads than the quota allows forces the kernel
to preempt and context-switch them inside a shrunk slice -- pure overhead,
no extra work done.

#### config.compute_cpu_max_cores

So: half the logical CPUs, but never below n_threads + 1 so it cannot
throttle a normal decode. MALABR_CPU_MAX overrides ("N" cores, "N%", or
"0"/"off" to disable).

NOTE the two percent conventions in this module are different on purpose:
  MALABR_CPU_MAX=N%  -> N percent of ONE core (cpulimit's convention;
                        "150%" = 1.5 cores, machine-size-independent)
  QUOTA_PCT / MALABR_QUOTA_PCT -> percent of ALL physical cores

#### config.list_models

The chat panel's model switcher offers exactly this set; a switch names one
of these and the server re-execs with MALABR_MODEL_PATH pointed at it.

#### config.resolve_model

Returns None if the name is not one of the directory's .gguf files -- the
switcher must never be able to make the server exec an arbitrary path.

#### config.ServerConfig.n_ctx_per_session

Shared-KV: a SOFT cap (n_ctx/2 by default) -- the aggregate cap does the
real bounding. Partitioned: the HARD slice llama.cpp enforces.

#### config.ServerConfig

Shared-KV mode (MALABR_SHARED_KV=1). Off: the KV cache is pre-partitioned
into n_seq_max equal hard slices and a session cannot exceed n_ctx/n_seq_max
(llama.cpp enforces this). On: kv_unified=true, one shared pool of n_ctx
cells, each session gets a LARGER soft cap (n_ctx // shared_kv_soft_div,
default n_ctx/2) and compaction is driven by AGGREGATE pressure -- so 1-2
tabs get a big budget and many tabs degrade to roughly fair share.

Cooperative CPU throttle (MALABR_CPU_DUTY, 0<d<=1). The engine sleeps
proportionally after each round, so average CPU lands near n_threads * d
-- smoothly, and with no cgroup / launch-path plumbing. 1.0 = flat out.

HARD CPU ceiling in cores (MALABR_CPU_MAX). 0 = disabled. Default computed
by compute_cpu_max_cores() -- half the logical CPUs, never below
n_threads+1, so it is a runaway backstop, not a normal-operation limit.

#### config.load_config

root is the mserver/ package dir; models live one level up beside it, in
addition_malabr/models. Deriving model_dir from `root` put it in
mserver/models, where nothing is -- calibration fell straight through to
its Phase E fallback and reported a conservative curve as if measured.

An explicit value may exceed MAX_N_CTX on purpose (see the constant),
but it is still rounded down to a multiple of n_seq_max -- and a typo
below n_seq_max would round to 0 and fail deep inside llama.cpp with
nothing pointing back here. Floor it at one whole slot per sequence.

Section 4's ONE stated exception to "nothing durable": calibration
data is written to disk on purpose, so a cold start does not have to
re-measure the machine every time.

### protocol

#### protocol.(module)

Section 10a is the contract and the C++ side is already written to it; where
Python disagrees, Python is wrong. Every format here was checked against the
built C++ (mserver_uds.cc), not only against the prose.

Two connection kinds share one header format:
  request  ROUTE_MALABR_GENERATE_API, then payload_size bytes of prompt
  control  ROUTE_MALABR_CONTROL, payload_size=0, then push messages forever

A user-initiated stop. Its own route rather than a fifth control message,
because extensions/browser cannot include chrome/browser/malabr_manager.h
(Chromium layering) and so cannot reach the control connection at all. The
request header already carries the full session identity, so a stop needs
nothing the generate path does not already send.

Matches kMaxFramePayload in mserver_uds.cc. The bound exists because the
length is read straight off the wire: without it, a co-resident process with
direct socket access (threat actor 4) can name any size and make us allocate
it. Section 10 records this same bug on the C++ side; this is the mirror of
it, in the other direction.

---------------------------------------------------------------------------
Control connection -- section 10a's four message types
---------------------------------------------------------------------------

#### protocol.recv_full

Callers MUST bound `size` before calling -- see MAX_PAYLOAD_SIZE. This
function deliberately does not enforce a policy bound of its own, because
it is also used for small fixed-size reads; the bound belongs where the
size is parsed off the wire.

#### protocol.ClientEnvelope

    route,extension_id,tab_id,origin,visibility,payload_size

Everything except payload_size is browser-derived and unspoofable by the
page -- that is the whole basis of section 5's identity model. It is still
validated here, because the socket is reachable by any co-resident process.

#### protocol.unpack_client_envelope

The previous version split into 3 fields and raised otherwise, which
rejected every generate() and every control connection once the browser
started sending 6.

Every field is ASCII by construction: route and visibility are
literals, extension_id is a-p, tab_id is digits, and a serialized
origin is ASCII. Non-ASCII means the peer is not our C++.

Section 5g / malabr_api.cc:77. EVERY opaque origin serializes to the
literal "null" (RFC 6454), so two unrelated opaque-origin documents
would produce the SAME session key and share a session -- exactly the
cross-origin leak the origin field exists to close. The C++ already
refuses these; this is the second gate, because the peer may not be
our C++.

BOUND BEFORE recv_full -- the whole point. The inherited code checked only
for < 0, so a peer could claim any size and the server would try to
receive and buffer it.

#### protocol.parse_control_message

LIVE_TABS is STATE, not an event, and that distinction is the whole reason
it exists: a TAB_CLOSED lost while the control connection was down can never
be replayed, because once the tab is gone the browser has no record it ever
existed. Pushing the full live set on every (re)connect lets the server
reconcile instead of relying on having seen every event.

"LIVE_TABS" with no ids is legitimate and means NO live tabs, which
must reap everything. Treating it as malformed would leave every
session alive at exactly the moment they should all be freed.

#### protocol.ClientEnvelope.session_key

Without it, navigating one tab from a bank site to another site let the
new site's content script inherit a session still holding the bank
conversation.

#### protocol.ClientEnvelope.is_foreground_seed

It is ignored for an existing session, because the control connection's
FOREGROUND message is authoritative and ordering between the two is not
guaranteed. A stale seed must never override live state.

### app

#### app.(module)

Launched by MalabrManager as `python3 addition_malabr/mserver/app.py`
(chrome/browser/malabr_manager.cc:36).

Startup order matters and is not arbitrary:
  1. config       -- sizing computed from THIS machine, not baked in
  2. pid lock     -- acquired for real, first, so a duplicate start exits in
                     milliseconds instead of after a full model load
  3. CPU ceiling  -- after the lock, so a rejected start never creates a
                     systemd scope it will not use; before anything heavy
  4. model+ctx    -- one context, created once, owned by the engine thread
  5. calibration  -- measured or loaded from disk; feeds the scheduler
  6. engine       -- started before the socket exists, so the first request
                     never races an engine that is not running yet
  7. socket       -- last, because accepting a connection we cannot serve is
                     worse than making the browser retry its connect

#### app.apply_cpu_ceiling

A pure backstop -- the default (config.compute_cpu_max_cores) is half the
logical CPUs and always above n_threads, so it never bites normal
operation. It exists only so a bug or a pathological model cannot take the
whole machine; MALABR_CPU_DUTY is the smooth throttle that sets where usage
actually sits.

Why a self-move via busctl and not `systemd-run --scope python app.py`: that
leaves systemd-run as the parent and python as a child IN the scope, and
MalabrManager's Terminate(pid) on the parent does NOT propagate to python
(tested). Moving our OWN pid into the scope keeps the pid MalabrManager
tracks, has no intermediary to leak, and the empty scope is GC'd when we
exit. cpu is not delegated to our starting scope, so writing cpu.max
directly is impossible; systemd (the cgroup manager) enables the controller
when a CPU property is set on the new scope.

Best-effort: any failure (no busctl, not under a user systemd, denied) logs
and continues unthrottled -- the duty throttle still applies.

Inside the try: this function's contract is "any failure logs and
continues unthrottled", and an unreadable /proc (a hardened or
procfs-less environment) must not be the one exception that crashes
startup instead.

busctl returned 0 but there is no cpu.max quota on our cgroup.
systemd does this silently when the cpu controller is not
delegated to the user session -- the scope exists, the ceiling
does not. Say so rather than log a false success.

#### app._cpu_max_quota_active

A zero return code from busctl is not proof the quota exists: systemd will
create the scope and drop the CPU property without error when the cpu
controller is not delegated to the user session. The move into the new
scope can lag the call slightly, so retry briefly.

#### app.build

An EXPLICIT MALABR_N_THREADS must win over the cached calibration's pick --
otherwise the env var is silently a no-op whenever calibration.json exists,
which is almost always.

One shared pool of n_ctx cells instead of n_seq_max hard slices, so a
session can grow past n_ctx/n_seq_max. The engine's aggregate guard is
then what keeps the total within n_ctx.

#### app.main

Take the single-instance lock FOR REAL, first thing. This is spawned
by MalabrManager, so a duplicate start (a browser restart that did
not confirm the old child died) is expected, and exit 3 keeps it out
of the Chrome log as a crash. It must be a real acquire, not a
read-only probe: a probe leaves the pidfile unclaimed through the
~2-minute cold build(), so two launches close together both pass it
and both pay for a model load and calibration before one is turned
away at the socket. And it must precede apply_cpu_ceiling, or the
rejected start still spins up a systemd scope it will never use.

### bg.js

#### bg.js

---- transcript persistence (section 2) --------------------------------
chrome.storage.session, NOT storage.local: session storage is memory-backed
and dies with the browser, which is what section 4's "nothing durable"
rule requires. storage.local would write conversations to disk.

The display copy must be keyed like the KV session it mirrors. It used to be
keyed by ORIGIN alone ("malabr:" + location.origin, built in the content
script), while the server keys by (extension, tab, origin). Two failures
followed: a second tab on the same site, or a fresh tab, "restored" another
tab's transcript that its own KV session had never seen -- the very
display/model desync section 2 exists to prevent; and a CLOSED tab's
transcript outlived it, reappearing in the next tab on that site even though
TAB_CLOSED had wiped the conversation server-side.

The content script cannot learn its own tab id, so bg.js builds the key from
the message's `sender` (tab.id, origin) -- browser-attributed, like
malabr_api.cc's identity -- and deletes a tab's keys on tabs.onRemoved.

This also let storage.session go back to its default TRUSTED_CONTEXTS. The
old design opened it to content scripts (setAccessLevel
TRUSTED_AND_UNTRUSTED_CONTEXTS), so this extension's content script in ANY
site's renderer could read EVERY site's transcripts; a compromised renderer
for one site could read all of them. Now a content script can reach only the
key its own sender maps to.

Rule 1, mirrored. The server tears down a tab's other-origin sessions on the
new origin's first CHAT request (engine.evict_other_origins), lazily, not on
navigation. bg.js deletes the same tab's other-origin transcripts at the same
moment: the save that accompanies a chat turn carries evictOtherOrigins. Other
saves do not -- "New chat" and a dropped stale copy send no chat request, so
the server evicts nothing and neither may the transcript store, or Back would
show an empty panel over a conversation the model still holds (the reverse
desync). Lazy on both sides is also what keeps a pass-through origin (a login
bounce via accounts.example.com) from wiping anything. /bench sends chat
requests without saving; the status check in content.js restore covers that
and any other path the mirror misses.

### content.js

#### content.js (module)

MALABR Phase 1 chat panel.

#### content.js MAX_PAGE_CHARS

A CONTENT SCRIPT deliberately, not a popup or side panel: those have no real
tab_id and no meaningful visibility, so the browser could not derive the
identity the design depends on (phase1_design.md sections 2 and 5).
all_frames:false so one page is one session, not one per iframe (test 36).

#### content.js min

Section 8's input cap is budget - RESERVED_FOR_RESPONSE, i.e. ~1792 tokens
for a 2048-token session share. An oversized prompt is REJECTED outright
(compaction cannot help a single prompt that exceeds the whole budget), so
the page text plus the wrapper plus the user's question plus the running
conversation must all fit. At a worst-case ~3 chars/token that budget is
~5300 chars for the WHOLE prompt; 4000 for the page body leaves room for
the wrapper, the question, and a few turns of history before compaction.

#### content.js restore

Last 40 turns only: chrome.storage.session has a ~10MB quota shared by
the whole extension, and a turn can carry a page-sized reply. bg.js
re-applies the cap (MAX_TURNS) so it holds whatever a sender claims. Older
turns stop persisting across reloads; the server's KV still has them
until compaction, so nothing the model knows is lost, only the display.

The server is the source of truth. The panel stores the server's
conversation id (runtime "status") with its transcript, and redraws a saved
transcript only if the server holds a session with that same id; otherwise
the copy is dropped. Before this the panel assumed the session survived any
reload because its key does not change, and redrew transcripts the model had
lost: after Back to an origin that rule 1 had since evicted, after a server
restart or crash, after a model switch in another tab.
  - The id is learned after each reply (syncConversation), so a restart
    mid-page is caught on the first turn after it: the id changed, so the
    model holds only that latest exchange. The panel keeps just that
    exchange, says why, and saves it under the new id -- a reload then
    redraws exactly what the new server holds. If the user has already sent
    again by the time the answer lands, it waits for the next reply rather
    than trim under a live turn.
  - no answer (server down, old server without "status"), or a saved copy
    with no id yet: redraw, marked unconfirmed -- dropping on a transient
    failure would lose the display for nothing.
  - `restoring` holds off a send until the answer lands, so a new turn is
    never drawn above the restored ones; "New chat" cancels a pending
    restore.
  - Back/forward cache: the page returns with this script's memory, panel
    included, and restore() does not run. pageshow(persisted) asks again.
    This stays needed after the TAB_NAVIGATED rebuild: that clears the
    server and storage, not a frozen page's memory.

#### content.js esc

---- markdown (small, injection-safe) --------------------------------
The model emits markdown heavily. This renders the common subset and
NOTHING else: every character is HTML-escaped first, then a fixed set of
patterns is turned into a fixed set of safe tags. No raw HTML from the
model can survive -- it matters because page text (which the model may be
repeating) is attacker-controlled when "include page text" is on.

#### content.js hashStr

---- page text ----------------------------------------------------------
Which page text (if any) the server already has for this session. Reset on
"New chat" and on toggling the checkbox, so a deliberate re-check re-sends.

#### content.js meta

inFlight is only assigned in generate()'s
async callback, so without this a second
Enter/paste in the same frame slips past the
guard and opens a parallel generation --
section 6b allows exactly one per session.

How long to wait for generate()'s callback (which only hands back the
request id -- tokens arrive separately on onToken) before assuming the
extension message was dropped. Must exceed any legitimate round-trip: on a
CPU-saturated box under a large prompt the old 10s could expire while the
call was still healthy, dropping `sending` and letting a second Enter open
the parallel generation the latch exists to stop. Shared with meta().

---- model switcher ---------------------------------------------------
The page-facing API is only generate()/stop(), so a control command rides
IN a generate() call: the prompt is the sentinel below, and the server
answers with a JSON blob as ordinary token frames. No new IDL/C++ route.

#### content.js chromeLM

A real stop needs malabr.stop(), which exists only once the browser is
rebuilt with it. Feature-detected so the button never claims a capability
the binary does not have.

#### content.js clear

Same single-generation rule as a normal turn: a benchmark runs two
full generations back to back, and an Enter meanwhile must not open a
parallel one. Held via `sending` for the whole run.

A generation is already in flight for this tab. A second one must not
start alongside it: if a real stop is available, treat this as "stop
the current answer"; otherwise just swallow the keystroke.

Safety net: if generate()'s callback never comes back (dropped extension
message), don't leave the composer locked forever. On expiry also tear
down this turn's half-open UI, so the next Enter starts genuinely fresh
instead of racing a callback that might still be in flight.

Send the page ONCE per session. The server keeps the conversation in
its KV cache, so re-sending the whole page every turn just re-prefills
it, burns the context budget, and makes the model re-read it. Only
re-attach if the page text actually changed (SPA navigation, new tab
content) or the conversation was cleared.

Toggling the checkbox does NOT force a re-read: the page is already in the
server's context, and only a real change to the page text (hashed in ask())
or a "New chat" re-attaches it.

#### content.js pageText

---- page context: extract, then select ---------------------------------

This used to be `body.innerText.slice(0, 4000)`. On a real page that keeps
the nav, the cookie banner and the first half of the sidebar, and throws
away the article -- the cap was being spent almost entirely on the wrong
text. Two stages fix it, in this order for a reason:

  1. EXTRACT (question-independent). Find the element that actually holds
     the article. Deterministic, so the "send the page once per session"
     cache still works: the same page yields the same text every turn.
  2. SELECT (question-dependent, only if step 1 still overflows). Keep the
     paragraphs that bear on what was asked. This DOES vary per question,
     so it is hashed like any other page text and re-attached only when
     the selection actually changes.

What is deliberately NOT here is any attempt to compress or re-encode the
text. A BPE tokenizer prices anything unlike natural language at several
tokens per word, so "clever" encodings cost more context than they save.
Summarisation is the only real next step, and it costs a generation.

#### content.js textFrom

Read text from the LIVE tree, block by block, rather than cloning and
taking innerText of the clone.

The clone is the trap: innerText is defined in terms of RENDERED text, and
a detached node has no layout, so on a clone it quietly degrades to
textContent -- which keeps the source file's line wrapping and collapses
no whitespace. The result looks fine until something downstream tries to
split it into paragraphs and gets one block per source line instead.

Walking live elements also gives real paragraph boundaries for free: each
block contributes one entry, joined by a blank line.

#### content.js paint

Painting is COALESCED PER FRAME, not done per token.

Every repaint re-scans the whole reply (splitThink), re-parses all of it
as markdown, and replaces the body's innerHTML. Doing that once per token
makes the cost of a reply quadratic in its own length, and at decode rates
most of those frames are never shown anyway -- the tokens arrive faster
than the compositor paints.

A background tab gets no animation frames at all, so a hidden panel stops
re-rendering entirely. That is the right behaviour and not a lost update:
onComplete flushes synchronously (flushPaint), so the finished reply is
always fully rendered whether or not the tab was ever visible.

#### content.js STYLES

§3: a real teardown, not a display clear. The display goes now (instant
feedback); the server-side session + KV are ended over the meta channel
and the note reports whether that was confirmed.

The server may still be loading its model when the panel appears (cold
start, or MalabrManager just spawned it). A single failed list would leave
the model picker empty for the life of the tab, so retry with backoff
until it answers -- bounded, and only until the first success.
