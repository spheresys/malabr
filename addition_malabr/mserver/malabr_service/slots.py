"""KV slot allocation with wipe-on-acquire -- design section 7.

Rationale: NOTES.md, slots
"""

import threading

import llama_cpp.llama_cpp as C


class SlotWipeError(RuntimeError):
    """A KV slot could not be proven empty. The slot is quarantined, never reused."""


class SlotAllocator:
    """Hands out llama.cpp sequence ids (slots), guaranteeing each is empty.

    Rationale: NOTES.md, slots.SlotAllocator
    """

    def __init__(self, mem, n_seq_max):
        self._mem = mem
        # A lock is required, not defensive  [notes: slots.SlotAllocator.__init__]
        self._lock = threading.Lock()
        self._free = list(range(n_seq_max))
        self._in_use = set()
        # Slots handed out but not yet wiped by the engine thread. A slot here
        # must never receive a decoded token.
        self._pending_wipe = set()
        # Slots whose wipe could not be verified  [notes: slots.SlotAllocator.__init__]
        self._quarantined = set()

    # -- callable from ANY thread: bookkeeping only, no model calls ------------

    def acquire(self):
        """Reserve a slot, or return None if at capacity.

        Rationale: NOTES.md, slots.SlotAllocator.acquire
        """
        with self._lock:
            if not self._free:
                return None          # at capacity -- caller rejects the session
            slot = self._free.pop()
            self._in_use.add(slot)
            self._pending_wipe.add(slot)
            return slot

    def release(self, slot):
        """Return a slot to the pool. Bookkeeping only; no model call.

        Rationale: NOTES.md, slots.SlotAllocator.release
        """
        with self._lock:
            if slot in self._quarantined:
                return                       # stays out of circulation, silently
            if slot not in self._in_use:
                # Double release, or a slot never acquired  [notes: slots.SlotAllocator.release]
                raise ValueError(f"release of slot {slot} that is not in use")
            self._in_use.discard(slot)
            self._pending_wipe.discard(slot)
            self._free.append(slot)

    # -- ENGINE THREAD ONLY: everything below touches the model ----------------

    def prepare(self, slot):
        """Wipe a freshly acquired slot and PROVE it is empty.

        Rationale: NOTES.md, slots.SlotAllocator.prepare
        """
        with self._lock:
            if slot not in self._in_use:
                raise ValueError(f"prepare of slot {slot} that is not in use")

        try:
            self._wipe_and_verify(slot)
        except SlotWipeError:
            with self._lock:
                self._in_use.discard(slot)
                self._pending_wipe.discard(slot)
                self._quarantined.add(slot)   # NOT returned to _free
            raise

        with self._lock:
            self._pending_wipe.discard(slot)

    def assert_ready(self, slot):
        """Guard for the decode path: refuse to use a slot that is not wiped.

        Rationale: NOTES.md, slots.SlotAllocator.assert_ready
        """
        with self._lock:
            if slot in self._pending_wipe:
                raise SlotWipeError(
                    f"slot {slot} used before prepare() -- would expose the "
                    f"previous session's KV")
            if slot not in self._in_use:
                raise ValueError(f"slot {slot} is not in use")

    def _wipe_and_verify(self, slot):
        """Clear a sequence and PROVE it is empty. Both checks are load-bearing."""
        # llama_memory_seq_rm is declared to return bool  [notes: slots.SlotAllocator._wipe_and_verify]
        if not C.llama_memory_seq_rm(self._mem, slot, -1, -1):
            raise SlotWipeError(f"llama_memory_seq_rm returned false for slot {slot}")

        # Independent confirmation  [notes: slots.SlotAllocator._wipe_and_verify]
        pos_max = C.llama_memory_seq_pos_max(self._mem, slot)
        if pos_max != -1:
            raise SlotWipeError(
                f"slot {slot} still holds KV after wipe (pos_max={pos_max})")

    # -- introspection, for the section 12 harness ------------------------------

    @property
    def free_count(self):
        with self._lock:
            return len(self._free)

    @property
    def in_use_count(self):
        with self._lock:
            return len(self._in_use)

    @property
    def pending_wipe(self):
        with self._lock:
            return frozenset(self._pending_wipe)

    @property
    def quarantined(self):
        with self._lock:
            return frozenset(self._quarantined)
