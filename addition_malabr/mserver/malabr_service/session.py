"""Per-session state, output cap and turn bookkeeping -- sections 6, 6b, 8.

Rationale: NOTES.md, session
"""

import queue
import secrets

from .formatter import Utf8Streamer


ABSOLUTE_CEILING = 512
MIN_CAP = 64

# Section 7: bounds the OUTPUT side, which none of the KV bounds cover. If the
# reader stalls, the engine would otherwise produce into an unread queue forever.
MAX_OUTBOX_TOKENS = 2 * ABSOLUTE_CEILING


class OutputCap:
    """Position-aware cap on tokens per response (section 8's context-growth fix).

    Rationale: NOTES.md, session.OutputCap
    """

    def __init__(self, table=None):
        # table: sorted [(position, cap)]  [notes: session.OutputCap.__init__]
        self._table = sorted(table) if table else None

    def for_position(self, pos):
        if self._table is None:
            return MIN_CAP
        cap = self._table[0][1]
        for position, value in self._table:
            if pos >= position:
                cap = value
            else:
                break
        # Phase G's ceiling is applied here too, not only at table-build time,
        # so a corrupted or hand-edited table still cannot exceed it.
        return max(MIN_CAP, min(int(cap), ABSOLUTE_CEILING))


# ----------------------------------------------------------------...  [notes: session.(module)]

# Exactly one TERMINAL frame (COMPLETE or ERROR) ends every request -- see
# terminate() for why that is unconditional rather than best-effort.


class SessionState:
    IDLE = "idle"
    PENDING = "pending"          # prompt accepted, prefill not finished
    GENERATING = "generating"    # producing response tokens
    DEAD = "dead"                # torn down; never dispatched again


class OutboxFull(RuntimeError):
    """The client stopped reading. Section 7's policy is to disconnect it."""


# Section 8 input cap  [notes: session.(module)]
RESERVED_FOR_RESPONSE = 256


class Turn:
    """One complete exchange (user delta + generated response) in KV.

    Rationale: NOTES.md, session.Turn
    """

    __slots__ = ("start", "end", "role", "msg_index")

    def __init__(self, start, end, role, msg_index):
        self.start = start
        self.end = end
        self.role = role
        self.msg_index = msg_index      # index of the USER message in formatter

    def __repr__(self):
        return f"Turn({self.start},{self.end},{self.role},m={self.msg_index})"


class Session:
    """One conversation, pinned to one KV slot.

    Rationale: NOTES.md, session.Session
    """

    def __init__(self, key, slot, formatter, output_cap, session_budget=2048):
        self.key = key
        # Names THIS conversation, not the key: a new session on the same key
        # (after new chat, rule 1, a restart) gets a new id.  [notes: runtime.MalabrServer._handle_meta]
        self.conv_id = secrets.token_hex(8)
        self.slot = slot
        self.formatter = formatter
        self.output_cap = output_cap

        self.state = SessionState.IDLE
        self.pos = 0                      # absolute KV position
        self.produced = 0                 # tokens emitted this response
        self.inbox_tokens = []            # prompt tokens awaiting prefill
        self.prefill_offset = 0
        # PER-REQUEST, not per-session -- a correction to §7, which calls...  [notes: session.Session.__init__]
        self.outbox = queue.Queue(maxsize=MAX_OUTBOX_TOKENS)
        self.pending_outbox = None
        self.streamer = Utf8Streamer()
        self.sampler = None               # per-session: see Engine._make_sampler
        self._reply_bytes = bytearray()   # what the model produced this turn

        # Section 6b's TWO snapshots  [notes: session.Session.__init__]
        self.pos_before_request = 0
        self.pos_before_generation = 0
        # The formatter's matching checkpoint  [notes: session.Session.__init__]
        self.formatter_cp_before_request = None

        # Section 8: completed exchanges, oldest first  [notes: session.Session.__init__]
        self.turn_boundaries = []
        # Partitioned KV  [notes: session.Session.__init__]
        self.budget = session_budget

        # §9a aging: consecutive rounds this session was passed over.
        self.rounds_excluded = 0
        # §9b: consecutive rounds a PENDING session was skipped because th...  [notes: session.Session.__init__]
        self.prefill_stalls = 0
        # The token this session must decode next round. Set when sampled,
        # consumed when the next batch is composed.
        self.next_token = None

        # Checked at the TOP of the loop, before dispatch -- same <=1-token
        # bound as tab-close cancellation, same flag pattern, no new machinery.
        self.pending_replace = None
        self.cancelled = False
        self.cancel_reason = None
        # Set by stop_generation(). Unlike `cancelled` this ends the TURN, not
        # the session.
        self.stop_requested = None

    # -- outbox ------------------------------------------------------------

    def emit(self, frame_type, payload):
        """Queue one frame. Raises OutboxFull if the reader has stalled.

        Rationale: NOTES.md, session.Session.emit
        """
        try:
            self.outbox.put_nowait((frame_type, payload))
        except queue.Full:
            raise OutboxFull(f"session {self.key} outbox full ({MAX_OUTBOX_TOKENS})")

    def terminate(self, frame_type, payload=""):
        """Emit the single terminal frame, bypassing the queue bound if needed.

        Rationale: NOTES.md, session.Session.terminate
        """
        try:
            self.outbox.put_nowait((frame_type, payload))
        except queue.Full:
            try:
                self.outbox.get_nowait()          # make room by dropping oldest
            except queue.Empty:
                pass
            try:
                self.outbox.put_nowait((frame_type, payload))
            except queue.Full:
                pass                              # reader is gone entirely


# Section 5d  [notes: session.(module)]
