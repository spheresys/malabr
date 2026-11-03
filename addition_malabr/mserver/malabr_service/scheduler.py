"""Batch composition by latency budget, and the closed loop -- sections 9a, 9b.

Rationale: NOTES.md, scheduler
"""

from .session import SessionState


ROUND_LATENCY_BUDGET_MS = 50      # the bound §7 already commits to elsewhere
MAX_CONSECUTIVE_EXCLUSIONS = 5    # aging bound; no measured value yet
PREFILL_CHUNK_MIN = 32            # below this, per-call overhead dominates
MAX_PREFILL_STALLS = 5            # see build_batch -- not in §9b, see note there
MAX_CORRECTION = 8.0              # §9b leaves this unnamed; see observe_round

# §5f degraded mode  [notes: scheduler.(module)]
STALE_VISIBILITY_TIMEOUT = 10.0

# Consecutive failed engine rounds before giving up. Transient failures are
# survivable; a systematic one should stop rather than spin. Counts only
# failures no single session can be blamed for, and only rounds that tried
# work reset it: a deferred or idle round is not a success.
MAX_CONSECUTIVE_ROUND_ERRORS = 20

# Consecutive rounds in which one session's own decode failed before its turn
# is rolled back with an error. Other sessions are not affected.
MAX_SESSION_DECODE_FAILURES = 3

# Fraction of each round's budget held back for prefill while a prompt is
# waiting to be prefilled. Decode may use the rest. Without it a large page
# got only what decode left, about 18 tok/s for two pages together (RQ2 M8).
PREFILL_MIN_SHARE = 0.25

# How long a turn that does not fit the shared pool may wait for room before
# it is refused. Below the browser's 60 s frame-read timeout.
ADMISSION_WAIT_SECONDS = 20.0

# How long an idle engine round blocks waiting to be woken. This is a BACKSTOP:
# every producer of work wakes the engine explicitly, so in normal operation the
# wait is cut short and this value never shows up in latency. It bounds the cost
# of a wake path that is ever missed or added later without a wake -- one round
# budget late, not a hang. Kept at ROUND_LATENCY_BUDGET_MS/2 so even that worst
# case stays inside the bound §7 already commits to.
IDLE_WAIT_SECONDS = (ROUND_LATENCY_BUDGET_MS / 1000.0) / 2

# Round budget when exactly ONE session has work; bounds a new arrival's wait.  [notes: scheduler.SOLO_ROUND_BUDGET_MS]
SOLO_ROUND_BUDGET_MS = 150


class CostCurve:
    """Per-position cost estimates, with a closed loop on top (§9b part 2).

    Rationale: NOTES.md, scheduler.CostCurve
    """

    def __init__(self, decode_table=None, prefill_table=None):
        # decode_table: [(pos, tps_median, tps_worst)]
        # prefill_table: [(pos, tok_per_s_worst)]
        self._decode = sorted(decode_table) if decode_table else None
        self._prefill = sorted(prefill_table) if prefill_table else None
        self.correction = 1.0

    # -- interpolation -----------------------------------------------------

    @staticmethod
    def _lookup(table, pos, col):
        lo = table[0]
        for row in table:
            if row[0] <= pos:
                lo = row
            else:
                hi = row
                span = hi[0] - lo[0]
                if span <= 0:
                    return lo[col]
                f = (pos - lo[0]) / span
                return lo[col] + f * (hi[col] - lo[col])
        return lo[col]

    def _raw_worst(self, pos):
        if not self._decode:
            # No calibration yet  [notes: scheduler.CostCurve._raw_worst]
            return ROUND_LATENCY_BUDGET_MS
        tps = self._lookup(self._decode, pos, 2)
        if tps <= 0:
            return ROUND_LATENCY_BUDGET_MS
        return 1000.0 / tps

    def cost_ms_worst(self, pos):
        return self._raw_worst(pos) * self.correction

    def cost_ms_median(self, pos):
        if not self._decode:
            return ROUND_LATENCY_BUDGET_MS
        tps = self._lookup(self._decode, pos, 1)
        return ROUND_LATENCY_BUDGET_MS if tps <= 0 else 1000.0 / tps

    # -- prefill (§9b Phase B-prefill) --------------------------------------

    def prefill_ms(self, n_tokens, pos=0):
        """Prefill cost is NOT decode cost; reusing the decode curve is wrong.

        Rationale: NOTES.md, scheduler.CostCurve.prefill_ms
        """
        if n_tokens <= 0:
            return 0.0
        if not self._prefill:
            # Uncalibrated: refuse to pretend. Charging the full budget makes
            # exactly one minimum-size chunk affordable per round.
            return float(ROUND_LATENCY_BUDGET_MS)
        tps = self._lookup(self._prefill, pos, 1)
        if tps <= 0:
            return float(ROUND_LATENCY_BUDGET_MS)
        return (n_tokens * 1000.0 / tps) * self.correction

    def prefill_tokens_affordable(self, budget_ms, pos=0):
        if budget_ms <= 0:
            return 0
        if not self._prefill:
            # Uncalibrated  [notes: scheduler.CostCurve.prefill_tokens_affordable]
            return PREFILL_CHUNK_MIN
        tps = self._lookup(self._prefill, pos, 1)
        if tps <= 0:
            return 0
        return int((budget_ms / 1000.0) * tps / max(self.correction, 1e-9))

    # -- the closed loop ----------------------------------------------------

    def observe_round(self, estimated_ms, observed_ms):
        """Feed the real round time back, so a wrong model degrades not lies.

        Rationale: NOTES.md, scheduler.CostCurve.observe_round
        """
        if estimated_ms <= 0 or observed_ms < 0:
            return
        ratio = observed_ms / estimated_ms
        # EWMA so one slow round -- a GC pause, a cpu.max throttle window --
        # cannot swing admission, but sustained divergence does.
        self.correction = 0.9 * self.correction + 0.1 * ratio
        # Floor of 1.0 is deliberate  [notes: scheduler.CostCurve.observe_round]
        self.correction = max(1.0, min(self.correction, MAX_CORRECTION))


def build_batch(sessions, cost_curve, foreground_tab_id,
                budget_ms=ROUND_LATENCY_BUDGET_MS,
                solo_budget_ms=SOLO_ROUND_BUDGET_MS):
    """Compose one round by SPENDING A LATENCY BUDGET, not by filling slots.

    Rationale: NOTES.md, scheduler.build_batch
    """
    runnable = [s for s in sessions
                if s.state == SessionState.GENERATING and not s.cancelled]
    pending = [s for s in sessions
               if s.state == SessionState.PENDING and not s.cancelled]

    # One contender means nobody to be fair to; see SOLO_ROUND_BUDGET_MS.
    # Counted over sessions with actual work, not over live sessions: eight
    # idle tabs plus one active one is still a solo round.
    contenders = len({id(s) for s in runnable} | {id(s) for s in pending})
    if contenders <= 1:
        budget_ms = solo_budget_ms

    # §5f: foreground is ONE tab id, re-read fresh every round, never cached  [notes: scheduler.build_batch]
    fg = [s for s in runnable if s.key[1] == foreground_tab_id]
    bg = [s for s in runnable if s.key[1] != foreground_tab_id]

    picks, budget = [], float(budget_ms)
    spent = 0.0
    # Held back for prefill while some prompt still needs it (PREFILL_MIN_SHARE).
    # Decode spends `budget - held`; prefill gets whatever is left, never less
    # than `held`. The first decode pick is still unconditional.
    held = budget * PREFILL_MIN_SHARE if any(
        len(s.inbox_tokens) > s.prefill_offset for s in pending) else 0.0

    # Foreground first and UNCONDITIONALLY  [notes: scheduler.build_batch]
    for s in sorted(fg, key=lambda s: -s.rounds_excluded):
        c = cost_curve.cost_ms_worst(s.pos)
        if not picks or c <= budget - held:
            picks.append(s); budget -= c; spent += c

    # AGING PASS -- before cheapest-first gets to pass over the same s...  [notes: scheduler.build_batch]
    picked = set(id(s) for s in picks)
    aged = sorted([s for s in runnable
                   if id(s) not in picked and s.rounds_excluded >= MAX_CONSECUTIVE_EXCLUSIONS],
                  key=lambda s: -s.rounds_excluded)
    for s in aged:
        c = cost_curve.cost_ms_worst(s.pos)
        # "or not picks" is a CORRECTION to §9a, not a restatement of it  [notes: scheduler.build_batch]
        if c <= budget - held or not picks:
            picks.append(s); picked.add(id(s)); budget -= c; spent += c

    # NORMAL FILL -- cheapest-first  [notes: scheduler.build_batch]
    remaining = sorted(((cost_curve.cost_ms_worst(s.pos), s)
                        for s in runnable if id(s) not in picked),
                       key=lambda cs: cs[0])
    for c, s in remaining:
        if c <= budget - held:
            picks.append(s); picked.add(id(s)); budget -= c; spent += c

    # Counted for foreground too: an fg member that lost this round must age
    # like anyone else, or the alternation above never triggers.
    for s in runnable:
        s.rounds_excluded = 0 if id(s) in picked else s.rounds_excluded + 1

    # -- §9b: prefill fills whatever budget is LEFT ------------------...  [notes: scheduler.build_batch]
    prefill_chunks = []
    # Never less than the held share, even when one unconditional decode pick
    # already overran the round: the round then runs over budget by at most
    # that share, instead of starving the prompt.
    budget = max(budget, held)
    guaranteed = False      # the held share has gone to a prompt this round
    pending.sort(key=lambda s: s.key[1] != foreground_tab_id)
    for s in pending:
        remaining_toks = len(s.inbox_tokens) - s.prefill_offset
        if remaining_toks <= 0:
            continue
        affordable = cost_curve.prefill_tokens_affordable(budget, s.pos)
        n = min(remaining_toks, affordable)

        # DIVERGENCE FROM §9b -- it says "if n < PREFILL_CHUNK_MIN and n <...  [notes: scheduler.build_batch]
        if n < PREFILL_CHUNK_MIN and n < remaining_toks and \
                not (held and not guaranteed and n >= 1):
            # The first waiting prompt (the visible tab's, if it has one) is
            # exempt: it always gets the held share, however small. The tokens
            # ride in the same batch as decode, so the per-call overhead the
            # minimum chunk guards against does not arise.
            s.prefill_stalls += 1
            if s.prefill_stalls < MAX_PREFILL_STALLS:
                continue
            # Admit what the budget CAN afford, not a full minimum chunk  [notes: scheduler.build_batch]
            n = max(1, min(affordable, remaining_toks))
        if n <= 0:
            continue
        s.prefill_stalls = 0
        guaranteed = True
        chunk = s.inbox_tokens[s.prefill_offset:s.prefill_offset + n]
        prefill_chunks.append((s, chunk))
        c = cost_curve.prefill_ms(n, s.pos)
        budget -= c; spent += c

    return picks, prefill_chunks, spent
