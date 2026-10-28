"""MALABR inference engine -- the single engine thread. Sections 7-9b.

Rationale: NOTES.md, engine
"""

import queue
import sys
import threading
import time

import llama_cpp.llama_cpp as C

from .formatter import (ChatFormatter, TemplateError, Utf8Streamer,  # noqa: F401
                        is_stop_token)
from .protocol import FRAME_COMPLETE, FRAME_ERROR, FRAME_TOKEN
from .scheduler import (CostCurve, IDLE_WAIT_SECONDS,  # noqa: F401
                        MAX_CONSECUTIVE_EXCLUSIONS,
                        MAX_CONSECUTIVE_ROUND_ERRORS, MAX_CORRECTION,
                        MAX_PREFILL_STALLS, PREFILL_CHUNK_MIN,
                        ROUND_LATENCY_BUDGET_MS, SOLO_ROUND_BUDGET_MS,
                        STALE_VISIBILITY_TIMEOUT, build_batch)
from .session import (ABSOLUTE_CEILING, MAX_OUTBOX_TOKENS, MIN_CAP,  # noqa: F401
                      OutboxFull, OutputCap, RESERVED_FOR_RESPONSE, Session,
                      SessionState, Turn)
from .slots import SlotAllocator, SlotWipeError  # noqa: F401


SAMPLING_CHAT = {"temperature": 0.7, "top_p": 0.9, "top_k": 40}
SAMPLING_DETERMINISTIC = {"temperature": 0.0}


class Engine:
    """The single engine thread. Owns the model context; nothing else touches it.

    Rationale: NOTES.md, engine.Engine
    """

    def __init__(self, ctx, model, vocab, allocator, sampling=None, seed=0,
                 n_ctx=None, n_seq_max=None, shared_kv=False, session_budget=2048,
                 cpu_duty=1.0):
        self._ctx = ctx
        self._model = model
        self._vocab = vocab
        self._alloc = allocator
        self._sampling = sampling if sampling is not None else SAMPLING_CHAT
        self._seed = seed

        # Shared-KV aggregate governance (§8)  [notes: engine.Engine.__init__]
        self._shared_kv = shared_kv
        self._n_ctx = n_ctx or (session_budget * (n_seq_max or 8))
        self._n_seq_max = n_seq_max or 8
        self._session_budget = session_budget
        self._fair_share = max(1, self._n_ctx // self._n_seq_max)
        self._agg_margin = 64               # leave a little slack under n_ctx

        # Cooperative CPU throttle (§11): fraction of wall time the engine may
        # spend computing. 1.0 = flat out. See _run.
        self._cpu_duty = min(1.0, max(0.05, cpu_duty))

        self._sessions = {}                 # key -> Session
        self._reg_lock = threading.Lock()   # guards _sessions only
        self._running = False
        self._thread = None
        # Idle wake-up. The loop used to sleep a flat 1 ms whenever a round
        # found nothing to do, so a server with no traffic woke ~1000x/s
        # forever. Every producer of work now sets this, and the idle branch
        # blocks on it instead. IDLE_WAIT_SECONDS is a backstop, not the
        # mechanism: if a wake path is ever missed the loop still notices
        # within one round budget rather than hanging.
        self._wake = threading.Event()
        # "A round scheduled nothing" and "there is nothing to schedule" are
        # NOT the same state, and blocking on the second when it is really the
        # first is a latency bug: build_batch deliberately defers a prefill
        # that cannot afford a whole minimum chunk, for up to
        # MAX_PREFILL_STALLS rounds. Those rounds must stay cheap, because the
        # user is already waiting on that prompt. Set by _run_round.
        self._deferred = False
        self.foreground_tab_id = -1         # section 5f: ONE key, not a per-session flag
        # Section 5f degraded mode  [notes: engine.Engine.__init__]
        self.control_connected = False
        self.control_lost_at = time.time()
        self.round_errors = 0
        # Held from calibration's Phase D output at startup, never re-derived
        # per round (§12 test 31).
        self.cost_curve = CostCurve()

    # -- registry (called from connection threads) --------------------------

    def get_or_create(self, key, formatter_factory, output_cap):
        """Look up or create a session. ONE atomic critical section.

        Rationale: NOTES.md, engine.Engine.get_or_create
        """
        with self._reg_lock:
            s = self._sessions.get(key)
            if s is not None and s.state != SessionState.DEAD:
                return s, False
            slot = self._alloc.acquire()
            if slot is None:
                return None, False          # at capacity -- caller rejects
            s = Session(key, slot, formatter_factory(), output_cap,
                        session_budget=self._session_budget)
            self._sessions[key] = s
            return s, True

    def evict_other_origins(self, ext_id, tab_id, origin):
        """Section 10a rule 1: creating a session for (ext, tab, origin) tears
        down any session with the same (ext, tab) and a DIFFERENT origin.

        Rationale: NOTES.md, engine.Engine.evict_other_origins
        """
        with self._reg_lock:
            doomed = [k for k in self._sessions
                      if k[0] == ext_id and k[1] == tab_id and k[2] != origin]
        for k in doomed:
            self.cancel(k, "cross-origin eviction")
        return doomed

    def effective_foreground_tab_id(self):
        """The foreground key the scheduler should actually use.

        Rationale: NOTES.md, engine.Engine.effective_foreground_tab_id
        """
        if not self.control_connected and \
                (time.time() - self.control_lost_at) > STALE_VISIBILITY_TIMEOUT:
            return -1
        return self.foreground_tab_id

    def wake(self):
        """Wake the engine thread if it is idle-blocked.

        Public because the control reader needs it too: a foreground change
        alters admission without adding work, so a round can legitimately
        report "nothing to do" and block right as the decision changes.
        Always set the flag being signalled BEFORE calling this (see _run).
        """
        self._wake.set()

    def set_control_connected(self, connected):
        if connected:
            self.control_connected = True
        elif self.control_connected:
            self.control_connected = False
            self.control_lost_at = time.time()

    def all_keys(self):
        with self._reg_lock:
            return [k for k, s in self._sessions.items()
                    if s.state != SessionState.DEAD]

    def keys_for_tab(self, tab_id):
        with self._reg_lock:
            return [k for k, s in self._sessions.items()
                    if k[1] == tab_id and s.state != SessionState.DEAD]

    def keys_for_extension(self, extension_id):
        """Section 10's sweep: tear down EVERY session for one extension.

        Rationale: NOTES.md, engine.Engine.keys_for_extension
        """
        with self._reg_lock:
            return [k for k, s in self._sessions.items()
                    if k[0] == extension_id and s.state != SessionState.DEAD]

    def wait_for_teardown(self, keys, timeout=2.0):
        """Block until these sessions are actually gone, or the timeout expires.

        Rationale: NOTES.md, engine.Engine.wait_for_teardown
        """
        if not keys:
            return True
        deadline = time.time() + timeout
        pending = set(keys)
        while time.time() < deadline:
            with self._reg_lock:
                alive = {k for k in pending
                         if k in self._sessions
                         and self._sessions[k].state != SessionState.DEAD}
            if not alive:
                return True
            pending = alive
            time.sleep(0.002)
        return False

    def cancel(self, key, reason):
        """Mark a session for teardown. Safe from any thread: sets flags only.

        Rationale: NOTES.md, engine.Engine.cancel
        """
        with self._reg_lock:
            s = self._sessions.get(key)
        if s is None or s.state == SessionState.DEAD:
            return False                    # defined no-op (section 10a)
        s.cancelled = True
        s.cancel_reason = reason
        self.wake()
        return True

    def stop_generation(self, key, reason="stopped"):
        """User-initiated stop: end the turn, KEEP the session.

        Rationale: NOTES.md, engine.Engine.stop_generation
        """
        with self._reg_lock:
            s = self._sessions.get(key)
        if s is None or s.state == SessionState.DEAD:
            return False                    # defined no-op
        if s.state not in (SessionState.PENDING, SessionState.GENERATING):
            return False                    # nothing in flight
        s.stop_requested = reason
        self.wake()
        return True

    def submit(self, key, text):
        """Queue a new prompt and return THIS request's outbox.

        Rationale: NOTES.md, engine.Engine.submit
        """
        with self._reg_lock:
            s = self._sessions.get(key)
        if s is None or s.state == SessionState.DEAD:
            return None
        # The caller needs its queue reference immediately -- it will start
        # blocking on it before the engine thread reaches _begin_turn.
        outbox = queue.Queue(maxsize=MAX_OUTBOX_TOKENS)
        s.pending_outbox = outbox
        # Do NOT touch pos or KV here -- that is engine-thread work. Only the
        # flag is set; the loop performs the rollback at its next top.
        s.pending_replace = text
        # Flag first, wake second -- never the other way round (see _run).
        self.wake()
        return outbox

    # -- engine thread ------------------------------------------------------

    def start(self):
        self._running = True
        self._thread = threading.Thread(target=self._run, name="malabr-engine",
                                        daemon=True)
        self._thread.start()

    def stop(self, timeout=5.0):
        self._running = False
        self.wake()               # do not wait out an idle block to exit
        if self._thread is not None:
            self._thread.join(timeout)

    def _run(self):
        while self._running:
            t_start = time.perf_counter()
            try:
                did_work = self._step()
            except Exception as exc:
                # An unhandled exception here used to kill the engine thread silently  [notes: engine.Engine._run]
                self.round_errors += 1
                print(f"malabr: engine round failed ({type(exc).__name__}: {exc})",
                      file=sys.stderr, flush=True)
                if self.round_errors >= MAX_CONSECUTIVE_ROUND_ERRORS:
                    # Something is systematically broken, not transient. Stop
                    # rather than spin at full speed printing forever.
                    print("malabr: too many consecutive engine failures, stopping",
                          file=sys.stderr, flush=True)
                    self._running = False
                    return
                time.sleep(0.01)
                continue
            self.round_errors = 0
            if not did_work and self._deferred:
                # Work EXISTS, this round just could not afford it. Nobody is
                # going to wake us -- the thing that unblocks it is the stall
                # counter advancing, which only happens by running more rounds.
                # Keep the original short yield so a deferred prefill still
                # clears in milliseconds, not in stalls x IDLE_WAIT_SECONDS.
                time.sleep(0.001)
            elif not did_work:
                # Clearing AFTER the wait is what makes this race-free: a
                # producer that sets its flag and then wakes us at any point
                # either arrives before the wait (which then returns at once)
                # or after the clear (so the next wait returns at once). The
                # flag itself is always set before the wake, never after.
                self._wake.wait(IDLE_WAIT_SECONDS)
                self._wake.clear()
            elif self._cpu_duty < 1.0:
                # Cooperative CPU throttle  [notes: engine.Engine._run]
                busy = time.perf_counter() - t_start
                time.sleep(busy * (1.0 / self._cpu_duty - 1.0))

    def _step(self):
        """One iteration. Returns True if any work was done.

        Rationale: NOTES.md, engine.Engine._step
        """
        # Control flags FIRST, before anything is dispatched  [notes: engine.Engine._step]
        self._apply_control()

        try:
            return self._run_round()
        except OutboxFull:
            return True
        except SlotWipeError:
            return True

    def _run_round(self):
        """One batched round: prefill chunks and decode tokens in ONE pass."""
        with self._reg_lock:
            sessions = list(self._sessions.values())

        # §8: the 95% check runs BEFORE composition, never after  [notes: engine.Engine._run_round]
        for s in sessions:
            if s.state in (SessionState.PENDING, SessionState.GENERATING) \
                    and not s.cancelled and self.needs_compaction(s):
                self.compact(s)

        # Shared-KV aggregate guard, same "before composition" reason  [notes: engine.Engine._run_round]
        if self._shared_kv:
            self._relieve_aggregate_pressure(sessions)

        decode_picks, prefill_chunks, estimated_ms = build_batch(
            sessions, self.cost_curve, self.effective_foreground_tab_id())
        if not decode_picks and not prefill_chunks:
            # Distinguish "idle" from "deferred" for the caller -- see _run.
            self._deferred = any(
                s.state in (SessionState.PENDING, SessionState.GENERATING)
                and not s.cancelled for s in sessions)
            return False
        self._deferred = False

        # Slots must be provably wiped before ANY token enters them. On the
        # engine thread, which is the whole point of the prepare/acquire split.
        for s in decode_picks:
            self._alloc.assert_ready(s.slot)
        for s, _ in prefill_chunks:
            if s.slot in self._alloc.pending_wipe:
                self._alloc.prepare(s.slot)
            self._alloc.assert_ready(s.slot)

        items, owner = [], {}
        for s in decode_picks:
            owner[len(items)] = ("decode", s)
            items.append((s.next_token, s.pos, s.slot, True))
        for s, chunk in prefill_chunks:
            for i, tok in enumerate(chunk):
                last = (i == len(chunk) - 1)
                completes = last and (s.prefill_offset + len(chunk) >= len(s.inbox_tokens))
                if completes:
                    owner[len(items)] = ("prefill", s)
                items.append((tok, s.pos + i, s.slot, completes))

        t0 = time.perf_counter()
        self._decode_batch(items)
        observed_ms = (time.perf_counter() - t0) * 1000.0

        # Each decode pick carried one token AT s.pos  [notes: engine.Engine._run_round]
        for s in decode_picks:
            s.pos += 1

        # §9b part 2  [notes: engine.Engine._run_round]
        self.cost_curve.observe_round(estimated_ms, observed_ms)

        for s, chunk in prefill_chunks:
            s.pos += len(chunk)
            s.prefill_offset += len(chunk)
            if s.prefill_offset >= len(s.inbox_tokens):
                s.pos_before_generation = s.pos
                if s.sampler is None:
                    s.sampler = self._make_sampler(s)
                s.state = SessionState.GENERATING

        for batch_i, (kind, s) in owner.items():
            if s.state != SessionState.GENERATING or s.cancelled:
                continue
            try:
                # batch index, NOT the output ordinal -- see _decode_batch.
                self._sample_and_emit(s, batch_i)
            except OutboxFull as e:
                s.terminate(FRAME_ERROR, "reader stalled")
                self._teardown(s, str(e))
        return True

    def _sample_and_emit(self, s, logits_index):
        """Produce exactly ONE token for this session, then return.

        Rationale: NOTES.md, engine.Engine._sample_and_emit
        """
        tok = C.llama_sampler_sample(s.sampler, self._ctx, logits_index)
        cap = s.output_cap.for_position(s.pos)
        if is_stop_token(self._vocab, tok):
            self._finish_turn(s, FRAME_COMPLETE, "eos")
            return
        if s.produced >= cap:
            self._finish_turn(s, FRAME_COMPLETE, "cap")
            return
        C.llama_sampler_accept(s.sampler, tok)
        raw = self._token_bytes(tok)
        s._reply_bytes.extend(raw)
        text = s.streamer.push(raw)
        if text:
            s.emit(FRAME_TOKEN, text)
        s.next_token = tok
        s.produced += 1

    def _apply_control(self):
        with self._reg_lock:
            sessions = list(self._sessions.values())
        for s in sessions:
            if s.state == SessionState.DEAD:
                continue
            if s.cancelled:
                self._roll_back_partial(s)
                s.terminate(FRAME_ERROR, s.cancel_reason or "cancelled")
                self._teardown(s, s.cancel_reason)
                continue
            if s.stop_requested is not None:
                reason, s.stop_requested = s.stop_requested, None
                # Same rollback as a supersede, then the session goes idle and
                # stays available -- no teardown, no slot release.
                self._roll_back_partial(s)
                s.terminate(FRAME_ERROR, reason)
                continue
            if s.pending_replace is not None:
                text = s.pending_replace
                s.pending_replace = None
                # Section 6b  [notes: engine.Engine._apply_control]
                if s.state in (SessionState.PENDING, SessionState.GENERATING):
                    self._roll_back_partial(s)
                    s.terminate(FRAME_ERROR, "superseded")
                try:
                    self._begin_turn(s, text)
                except ValueError as exc:
                    # Gate 2 (the input cap in _begin_turn) fired  [notes: engine.Engine._apply_control]
                    print(f"malabr: {exc}", file=sys.stderr, flush=True)
                    s.terminate(FRAME_ERROR, str(exc))
                    s.state = SessionState.IDLE
                except TemplateError as exc:
                    # The formatter desynced from the KV (its _rendered is no longer a...  [notes: engine.Engine._apply_control]
                    print(f"malabr: formatter desync, ending session: {exc}",
                          file=sys.stderr, flush=True)
                    s.terminate(FRAME_ERROR,
                                "conversation state was lost -- start a new chat")
                    self._teardown(s, "formatter desync")

    def _begin_turn(self, s, text):
        # Swap in the queue this request's handler is already holding  [notes: engine.Engine._begin_turn]
        if s.pending_outbox is not None:
            s.outbox = s.pending_outbox
            s.pending_outbox = None
        s.pos_before_request = s.pos            # snapshot 1 (section 6b)
        # Snapshot 2 is set for real when prefill completes (see _run_round)  [notes: engine.Engine._begin_turn]
        s.pos_before_generation = s.pos
        s.formatter_cp_before_request = s.formatter.checkpoint()
        s.inbox_tokens = s.formatter.user_turn(text)
        # Section 8 gate 2  [notes: engine.Engine._begin_turn]
        max_input = s.budget - RESERVED_FOR_RESPONSE
        if self._shared_kv:
            # Shared-KV: s.budget is a soft cap  [notes: engine.Engine._begin_turn]
            with self._reg_lock:
                reserved = sum(min(o.pos, self._fair_share)
                               for o in self._sessions.values()
                               if o is not s and o.state != SessionState.DEAD)
            room = self._n_ctx - reserved - self._agg_margin
            max_input = min(max_input, room - RESERVED_FOR_RESPONSE)
        if len(s.inbox_tokens) > max_input:
            n_tokens = len(s.inbox_tokens)
            s.formatter.restore(s.formatter_cp_before_request)
            s.formatter_cp_before_request = None
            s.state = SessionState.IDLE
            s.inbox_tokens = []
            raise ValueError(
                f"gate 1 bypassed: prompt is too long -- {n_tokens} tokens, "
                f"but this conversation can accept at most {max(0, max_input)}")
        s.prefill_offset = 0
        s.produced = 0
        s.streamer = Utf8Streamer()
        s._reply_bytes = bytearray()
        s.state = SessionState.PENDING

    def _roll_back_partial(self, s):
        """Undo a turn that did not finish. Engine thread only.

        Rationale: NOTES.md, engine.Engine._roll_back_partial
        """
        if s.state not in (SessionState.PENDING, SessionState.GENERATING):
            return
        target = s.pos_before_request
        if s.pos > target:
            C.llama_memory_seq_rm(self._alloc._mem, s.slot, target, s.pos)
            s.pos = target
        # No turn is in flight now -- keep pos_before_generation pinned to...  [notes: engine.Engine._roll_back_partial]
        s.pos_before_generation = s.pos_before_request
        # The formatter must roll back with the KV, not after it.
        if s.formatter_cp_before_request is not None:
            s.formatter.restore(s.formatter_cp_before_request)
            s.formatter_cp_before_request = None
        s.state = SessionState.IDLE

    def _teardown(self, s, reason=None):
        s.state = SessionState.DEAD
        with self._reg_lock:
            if self._sessions.get(s.key) is s:
                del self._sessions[s.key]
        try:
            self._alloc.release(s.slot)
        except ValueError:
            pass                                # already released
        if s.sampler is not None:
            C.llama_sampler_free(s.sampler)
            s.sampler = None

    # -- the two model-touching steps ---------------------------------------

    def _make_sampler(self, s):
        """One sampler PER SESSION, never shared.

        Rationale: NOTES.md, engine.Engine._make_sampler
        """
        params = C.llama_sampler_chain_default_params()
        chain = C.llama_sampler_chain_init(params)
        temp = self._sampling.get("temperature", 0.0)
        if temp <= 0.0:
            C.llama_sampler_chain_add(chain, C.llama_sampler_init_greedy())
        else:
            if "top_k" in self._sampling:
                C.llama_sampler_chain_add(
                    chain, C.llama_sampler_init_top_k(int(self._sampling["top_k"])))
            if "top_p" in self._sampling:
                C.llama_sampler_chain_add(
                    chain, C.llama_sampler_init_top_p(float(self._sampling["top_p"]), 1))
            C.llama_sampler_chain_add(chain, C.llama_sampler_init_temp(float(temp)))
            # Seed per session so a test can reproduce one session's stream
            # without every session sharing one global sequence.
            C.llama_sampler_chain_add(
                chain, C.llama_sampler_init_dist(self._seed + (s.slot * 7919)))
        return chain

    def _decode_batch(self, items):
        """items: [(token, pos, seq_id, want_logits)] -> batch indices with logits.

        Rationale: NOTES.md, engine.Engine._decode_batch
        """
        n = len(items)
        batch = C.llama_batch_init(n, 0, 1)
        try:
            batch.n_tokens = n
            idx = []
            for i, (tok, pos, seq, want) in enumerate(items):
                batch.token[i] = tok
                batch.pos[i] = pos
                batch.n_seq_id[i] = 1
                batch.seq_id[i][0] = seq
                batch.logits[i] = 1 if want else 0
                if want:
                    idx.append(i)
            rc = C.llama_decode(self._ctx, batch)
            if rc != 0:
                # Checked, not ignored  [notes: engine.Engine._decode_batch]
                raise RuntimeError(f"llama_decode failed rc={rc}")
            return idx
        finally:
            C.llama_batch_free(batch)

    def _finish_turn(self, s, frame_type, reason):
        reply = bytes(s._reply_bytes).decode("utf-8", "replace")

        # Chat templates that trim message content (gemma-3 does  [notes: engine.Engine._finish_turn]
        stripped = reply.rstrip()
        if stripped != reply:
            # Only the tokens generated THIS turn are candidates -- never the
            # user message or the generation prompt ahead of them.
            reply_span = s.pos - s.pos_before_generation
            if not stripped:
                # The reply is ALL whitespace  [notes: engine.Engine._finish_turn]
                n_trim = reply_span
            else:
                try:
                    full_toks = s.formatter._tokenize(reply, parse_special=False)
                    keep_toks = s.formatter._tokenize(stripped, parse_special=False)
                except Exception:
                    full_toks = keep_toks = None
                # Re-tokenising the concatenated bytes can disagree with the token...  [notes: engine.Engine._finish_turn]
                if full_toks is not None and full_toks[:len(keep_toks)] == keep_toks:
                    n_trim = len(full_toks) - len(keep_toks)
                else:
                    n_trim = 0
            if 0 < n_trim <= reply_span:
                C.llama_memory_seq_rm(self._alloc._mem, s.slot,
                                      s.pos - n_trim, s.pos)
                s.pos -= n_trim
                reply = stripped

        # BOOKKEEPING FIRST -- before any emit that can raise OutboxFull o...  [notes: engine.Engine._finish_turn]
        # msg_index is this turn's USER message: len()-1, not len()  [notes: engine.Engine._finish_turn]
        s.turn_boundaries.append(
            Turn(s.pos_before_request, s.pos, "exchange",
                 len(s.formatter._messages) - 1))
        s.formatter.assistant_generated(reply)

        # Now the fallible part. A full outbox here must NOT prevent the
        # terminal frame -- terminate() bypasses the bound for exactly that.
        try:
            tail = s.streamer.flush()
            if tail:
                s.emit(FRAME_TOKEN, tail)
            # The C++ discards the FRAME_COMPLETE payload, so "why did it stop...  [notes: engine.Engine._finish_turn]
            if frame_type == FRAME_COMPLETE and reason == "cap":
                s.emit(FRAME_TOKEN, "\x00MALABR:cap")
        except OutboxFull:
            pass
        print(f"malabr:   turn done ({reason}) produced~{len(s._reply_bytes)}B "
              f"pos={s.pos}", file=sys.stderr, flush=True)
        s.terminate(frame_type, reason)
        s.state = SessionState.IDLE
        s.produced = 0
        # The turn is committed  [notes: engine.Engine._finish_turn]
        s.pos_before_generation = s.pos_before_request
        # Its formatter checkpoint is now meaningless too. Nothing reads it
        # again -- the next _begin_turn overwrites it before any rollback could
        # -- but leaving it set meant compaction kept shifting a dead value.
        s.formatter_cp_before_request = None

    # -- section 8: compaction ----------------------------------------------

    COMPACT_TRIGGER = 0.95
    TARGET_FREED_TOKENS = 512

    def needs_compaction(self, s):
        """Checked BEFORE a session enters a batch, never after.

        Rationale: NOTES.md, engine.Engine.needs_compaction
        """
        return s.pos >= s.budget * self.COMPACT_TRIGGER

    def compact(self, s, keep_recent=True):
        """Drop whole oldest exchanges, shifting EVERY live absolute position.

        Rationale: NOTES.md, engine.Engine.compact
        """
        # Keep the anchor (index 0)  [notes: engine.Engine.compact]
        droppable = s.turn_boundaries[1:-1] if keep_recent else s.turn_boundaries[1:]
        freed = 0
        for turn in list(droppable):
            if freed >= self.TARGET_FREED_TOKENS:
                break
            n = turn.end - turn.start
            if n <= 0:
                continue

            # Invariant section 8 names but does not assert  [notes: engine.Engine.compact]
            for name, val in (("pos_before_request", s.pos_before_request),
                              ("pos_before_generation", s.pos_before_generation)):
                if turn.start < val < turn.end:
                    raise RuntimeError(
                        f"{name}={val} lies inside dropped range "
                        f"[{turn.start},{turn.end}) -- compaction would corrupt it")

            C.llama_memory_seq_rm(self._alloc._mem, s.slot, turn.start, turn.end)
            C.llama_memory_seq_add(self._alloc._mem, s.slot, turn.end, -1, -n)

            # THE FIX. Shifting s.pos alone is not enough -- every other absol...  [notes: engine.Engine.compact]
            for other in s.turn_boundaries:
                if other is turn:
                    continue
                if other.start >= turn.end:
                    other.start -= n
                    other.end -= n
            if s.pos_before_request >= turn.end:
                s.pos_before_request -= n
            if s.pos_before_generation >= turn.end:
                s.pos_before_generation -= n
            s.pos -= n

            # The formatter is the OTHER state holding this conversation, and...  [notes: engine.Engine.compact]
            s.formatter.drop_messages(turn.msg_index, 2)
            for other in s.turn_boundaries:
                if other is not turn and other.msg_index > turn.msg_index:
                    other.msg_index -= 2
            # The live rollback checkpoint is ALSO a reference into the messag...  [notes: engine.Engine.compact]
            if s.formatter_cp_before_request is not None \
                    and s.formatter_cp_before_request > turn.msg_index:
                s.formatter_cp_before_request -= 2

            s.turn_boundaries.remove(turn)
            freed += n
        return freed

    def _relieve_aggregate_pressure(self, sessions):
        """Shared-KV: keep Sigma(live pos) under n_ctx by compacting.

        Rationale: NOTES.md, engine.Engine._relieve_aggregate_pressure
        """
        live = [s for s in sessions if s.state != SessionState.DEAD]
        if not live:
            return
        limit = self._n_ctx - self._agg_margin
        if sum(s.pos for s in live) < limit:
            return
        before = sum(s.pos for s in live)
        guard = 0
        while sum(s.pos for s in live) >= limit and guard < 3 * len(live) + 4:
            guard += 1
            over = sorted((s for s in live if s.pos > self._fair_share),
                          key=lambda s: s.pos, reverse=True)
            pool = over or sorted(live, key=lambda s: s.pos, reverse=True)
            freed = 0
            for victim in pool:
                freed = self.compact(victim)          # keep the recent turn
                if not freed:
                    freed = self.compact(victim, keep_recent=False)   # last resort
                if freed:
                    break
            if not freed:
                break                                # compaction is spent

        remaining = sum(s.pos for s in live)
        if remaining >= limit:
            # Compaction could not claw the pool back under n_ctx  [notes: engine.Engine._relieve_aggregate_pressure]
            rejected = self._reject_pending_for_capacity(live, limit)
            remaining = sum(s.pos for s in live)
            if remaining >= limit:
                print("malabr: aggregate KV pressure unrelievable "
                      f"(sum pos={remaining}, n_ctx={self._n_ctx}, "
                      f"rejected {rejected} pending)",
                      file=sys.stderr, flush=True)
                return
        print(f"malabr: aggregate guard compacted {before} -> {remaining} "
              f"(limit {limit})", file=sys.stderr, flush=True)

    def _reject_pending_for_capacity(self, live, limit):
        """Roll back PENDING turns, largest first, until the shared pool is back
        under n_ctx. They have generated nothing, so a rollback plus one
        FRAME_ERROR is clean and recoverable -- letting the decode fail is not.
        GENERATING sessions are left alone: killing a stream the user is
        watching to make room for someone else's turn is the worse trade.
        """
        pending = sorted((s for s in live if s.state == SessionState.PENDING),
                         key=lambda s: s.pos, reverse=True)
        rejected = 0
        for s in pending:
            if sum(o.pos for o in live) < limit:
                break
            self._roll_back_partial(s)
            s.terminate(FRAME_ERROR, "server is at capacity -- try again")
            rejected += 1
        return rejected

    def _token_bytes(self, tok):
        import ctypes
        buf = (ctypes.c_char * 256)()
        n = C.llama_token_to_piece(self._vocab, tok, buf, 256, 0, True)
        if n < 0:
            raise RuntimeError(f"llama_token_to_piece failed ({n})")
        return buf.raw[:n]
