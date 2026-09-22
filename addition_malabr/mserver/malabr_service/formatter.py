"""Chat template application and UTF-8-safe streaming -- sections 6c, 6d.

Rationale: NOTES.md, formatter
"""

import llama_cpp.llama_cpp as C


class TemplateError(RuntimeError):
    """The model's chat template does not support incremental turn building."""


class ChatFormatter:
    """Builds prompt tokens for a session, one turn at a time (section 6c).

    Rationale: NOTES.md, formatter.ChatFormatter
    """

    def __init__(self, model, vocab, disable_thinking=True):
        self._model = model
        self._vocab = vocab
        tmpl = C.llama_model_chat_template(model, None)
        if not tmpl:
            raise TemplateError("model carries no chat template")
        self._tmpl = tmpl
        self._messages = []      # full logical history, for re-rendering
        self._rendered = ""      # EXACTLY the text currently represented in KV
        self._think_suffix = (self._extract_no_think_suffix(tmpl)
                              if disable_thinking else "")

    @staticmethod
    def _extract_no_think_suffix(tmpl):
        """The text this model's template appends to switch reasoning OFF.

        Rationale: NOTES.md, formatter.ChatFormatter._extract_no_think_suffix
        """
        try:
            source = tmpl.decode("utf-8") if isinstance(tmpl, bytes) else tmpl
        except (UnicodeDecodeError, AttributeError):
            return ""
        import re
        m = re.search(
            r"enable_thinking\s+is\s+false\s*%\}\s*\{\{-?\s*'((?:[^'\\]|\\.)*)'",
            source)
        if not m:
            return ""
        # The captured text is a Jinja string literal: unescape it the same way
        # Jinja would, so '\n' becomes a real newline.
        return m.group(1).encode().decode("unicode_escape")

    # -- rendering ------------------------------------------------------------

    def _apply(self, messages, add_generation_prompt):
        import ctypes
        arr = (C.llama_chat_message * len(messages))()
        keep = []                      # keep byte objects alive past the loop
        for i, (role, content) in enumerate(messages):
            rb, cb = role.encode(), content.encode()
            keep.append((rb, cb))
            arr[i].role = ctypes.c_char_p(rb)
            arr[i].content = ctypes.c_char_p(cb)
        size = 65536
        while True:
            buf = ctypes.create_string_buffer(size)
            n = C.llama_chat_apply_template(
                self._tmpl, arr, len(messages), add_generation_prompt, buf, size)
            if n < 0:
                raise TemplateError(f"llama_chat_apply_template failed ({n})")
            if n <= size:
                return buf.raw[:n].decode("utf-8")
            size = n + 1               # documented API: retry with the needed size

    def _tokenize(self, text, parse_special=True, add_special=False):
        import ctypes
        b = text.encode("utf-8")
        if not b:
            return []
        cap = len(b) + 64
        buf = (C.llama_token * cap)()
        # add_special controls BOS  [notes: formatter.ChatFormatter._tokenize]
        n = C.llama_tokenize(self._vocab, b, len(b), buf, cap,
                             add_special,
                             parse_special)
        if n < 0:
            raise TemplateError(f"llama_tokenize overflow ({n})")
        return list(buf[:n])

    # -- the two operations the engine actually calls --------------------------

    def user_turn(self, text):
        """Record a user message and return ONLY the new tokens to prefill."""
        first_turn = not self._rendered
        self._messages.append(("user", text))
        full = self._apply(self._messages, True)

        # Load-bearing invariant, checked rather than assumed  [notes: formatter.ChatFormatter.user_turn]
        if not full.startswith(self._rendered):
            raise TemplateError(
                "template broke the prefix property; incremental prefill is "
                "unsafe for this model")

        # Append the model's own "reasoning off" marker after the generation
        # prompt, exactly where its template would have put it.
        full += self._think_suffix
        delta = full[len(self._rendered):]
        self._rendered = full
        # Only the first delta of the conversation carries BOS (see _tokenize).
        return self._tokenize(delta, add_special=first_turn)

    def assistant_generated(self, text):
        """Record what the model actually produced.

        Rationale: NOTES.md, formatter.ChatFormatter.assistant_generated
        """
        # The no-think marker is recorded as part of what the assistant sa...  [notes: formatter.ChatFormatter.assistant_generated]
        self._messages.append(("assistant", self._think_suffix + text))
        self._rendered += text

    def _expected_render(self):
        """The text that SHOULD currently be in the KV.

        Rationale: NOTES.md, formatter.ChatFormatter._expected_render
        """
        if not self._messages:
            # An empty conversation means an empty KV  [notes: formatter.ChatFormatter._expected_render]
            return ""
        if self._messages[-1][0] == "assistant":
            return self._apply(self._messages[:-1], True) + self._messages[-1][1]
        return self._apply(self._messages, True) + self._think_suffix

    def drop_messages(self, start, count):
        """Remove messages that section 8 compaction evicted from the KV.

        Rationale: NOTES.md, formatter.ChatFormatter.drop_messages
        """
        del self._messages[start:start + count]
        self._rendered = self._expected_render()

    # -- checkpoint / restore, for section 6b's rollback -----------------------

    def checkpoint(self):
        """Capture enough state to undo an abandoned turn.

        Rationale: NOTES.md, formatter.ChatFormatter.checkpoint
        """
        return len(self._messages)

    def restore(self, cp):
        del self._messages[int(cp):]
        # Recompute rather than truncate: _expected_render() already knows what
        # the KV holds for each phase, so this cannot drift out of step with it.
        self._rendered = self._expected_render()

    # -- self-check for the section 12 harness ---------------------------------

    def verify_against_full(self):
        """Assert incremental building matches a from-scratch render, in TOKENS.

        Rationale: NOTES.md, formatter.ChatFormatter.verify_against_full
        """
        truth = self._tokenize(self._expected_render())
        rebuilt = self._tokenize(self._rendered)
        if truth != rebuilt:
            raise TemplateError(
                f"incremental render diverged from full render "
                f"({len(rebuilt)} tokens vs {len(truth)})")
        return True


def is_stop_token(vocab, token):
    """True if generation must stop at this token.

    Rationale: NOTES.md, formatter.is_stop_token
    """
    return bool(C.llama_vocab_is_eog(vocab, token))


# ----------------------------------------------------------------...  [notes: formatter.Utf8Streamer]

class Utf8Streamer:
    """Buffers token bytes so no frame ever splits a UTF-8 character.

    Rationale: NOTES.md, formatter.Utf8Streamer
    """

    # A UTF-8 character is at most 4 bytes, so a legitimate partial se...  [notes: formatter.Utf8Streamer]
    MAX_HELD_BYTES = 3

    def __init__(self):
        self._buf = bytearray()

    def push(self, raw):
        """Add token bytes; return the text safe to send now (may be '')."""
        self._buf.extend(raw)
        # Find the longest prefix that is valid UTF-8  [notes: formatter.Utf8Streamer.push]
        try:
            text = self._buf.decode("utf-8")
            self._buf.clear()
            return text
        except UnicodeDecodeError as e:
            good = self._buf[:e.start].decode("utf-8")
            held = self._buf[e.start:]
            if len(held) > self.MAX_HELD_BYTES:
                # Not a partial character -- genuinely invalid bytes. Emit a
                # replacement rather than holding them forever.
                good += held.decode("utf-8", "replace")
                held = bytearray()
            self._buf = bytearray(held)
            return good

    def flush(self):
        """End of stream: emit whatever is left, lossily if incomplete.

        Rationale: NOTES.md, formatter.Utf8Streamer.flush
        """
        if not self._buf:
            return ""
        text = self._buf.decode("utf-8", "replace")
        self._buf.clear()
        return text
