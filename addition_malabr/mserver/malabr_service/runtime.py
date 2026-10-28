"""Server bootstrap: UDS listener, request routing, control reader thread.

Rationale: NOTES.md, runtime
"""

import json
import os
import signal
import socket
import struct
import sys
import threading
import time
from concurrent.futures import ThreadPoolExecutor

from .config import list_models, load_config, resolve_model
from .protocol import (
    FRAME_COMPLETE, FRAME_ERROR, FRAME_TOKEN, MAX_HEADER_LEN,
    MSG_EXT_UNLOADED, MSG_FOREGROUND, MSG_LIVE_TABS, MSG_TAB_CLOSED,
    ProtocolError, ROUTE_CONTROL, ROUTE_STOP,
    encode_frame, read_control_message, recv_full, unpack_client_envelope,
)

# Chat-panel control commands, tunnelled through generate() (see handle_generate).
# A prompt that begins with this is a command, never a turn.
META_PREFIX = "\x00MALABR::"

# Section 7: this pool is a WAITING ROOM, not compute  [notes: runtime.(module)]
CLIENT_POOL_WORKERS = 64

# How long a handler waits for one frame before checking whether i...  [notes: runtime.(module)]
OUTBOX_POLL_SECONDS = 0.25


class SingleInstanceError(RuntimeError):
    pass


class PidLock:
    """Refuse to start if another live instance holds the socket.

    Rationale: NOTES.md, runtime.PidLock
    """

    def __init__(self, path):
        self.path = path
        self._acquired = False

    def acquire(self):
        if self._acquired:
            return                          # idempotent: main() acquires early
        existing = self._read()
        # A pidfile naming OUR OWN pid can only be our re-exec predecessor  [notes: runtime.PidLock.acquire]
        if existing is not None and existing != os.getpid() and self._alive(existing):
            raise SingleInstanceError(
                f"another malabr server is running (pid {existing})")
        tmp = f"{self.path}.{os.getpid()}.tmp"
        with open(tmp, "w") as fh:
            fh.write(str(os.getpid()))
        os.replace(tmp, self.path)          # atomic
        self._acquired = True

    def release(self):
        if not self._acquired:
            return
        try:
            if self._read() == os.getpid():
                os.unlink(self.path)
        except OSError:
            pass
        self._acquired = False

    def _read(self):
        try:
            with open(self.path) as fh:
                return int(fh.read().strip())
        except (OSError, ValueError):
            return None

    @staticmethod
    def _alive(pid):
        if pid <= 0:
            return False
        try:
            os.kill(pid, 0)                 # signal 0 tests existence only
        except ProcessLookupError:
            return False
        except PermissionError:
            return True                     # exists, owned by someone else
        return True


class ControlReader:
    """Owns the ONE control connection. Section 5e / 10a.

    Rationale: NOTES.md, runtime.ControlReader
    """

    def __init__(self, engine):
        self._engine = engine
        self._thread = None
        self._conn = None

    def serve(self, conn):
        """Take ownership of a control connection and read it to EOF.

        Rationale: NOTES.md, runtime.ControlReader.serve
        """
        old, self._conn = self._conn, conn
        if old is not None:
            try:
                old.shutdown(socket.SHUT_RDWR)
            except OSError:
                pass
            try:
                old.close()
            except OSError:
                pass

        self._engine.set_control_connected(True)
        print("malabr: control connection established", file=sys.stderr, flush=True)
        try:
            while True:
                try:
                    msg = read_control_message(conn)
                except ProtocolError as exc:
                    # A malformed control message is not recoverable  [notes: runtime.ControlReader.serve]
                    print(f"malabr: bad control message, dropping: {exc}",
                          file=sys.stderr)
                    return
                if msg is None:
                    return                  # browser closed
                self.dispatch(msg)
        finally:
            if self._conn is conn:
                self._conn = None
                # §5f: start the staleness clock  [notes: runtime.ControlReader.serve]
                self._engine.set_control_connected(False)
            try:
                conn.close()
            except OSError:
                pass

    def dispatch(self, msg):
        """Apply one control message. Unknown tab/extension is a NO-OP.

        Rationale: NOTES.md, runtime.ControlReader.dispatch
        """
        eng = self._engine
        if msg.kind == MSG_FOREGROUND:
            # A single attribute write. Atomic under the GIL, no lock needed,
            # and read fresh by build_batch on the very next round (§5f).
            eng.foreground_tab_id = msg.tab_id
            # A promotion changes ADMISSION, not the amount of work, so a round
            # that was excluding everything can legitimately report "no work"
            # and idle-block. Wake it so §12 test 10's "within one round" holds
            # exactly rather than within the idle backstop.
            eng.wake()
            print(f"malabr: control FOREGROUND -> tab {msg.tab_id}",
                  file=sys.stderr, flush=True)
            return

        if msg.kind == MSG_TAB_CLOSED:
            for key in eng.keys_for_tab(msg.tab_id):
                eng.cancel(key, "tab closed")
            return

        if msg.kind == MSG_LIVE_TABS:
            # RECONCILE, not replay  [notes: runtime.ControlReader.dispatch]
            live = set(msg.tab_ids)
            for key in eng.all_keys():
                tab = key[1]
                if tab != -1 and tab not in live:
                    eng.cancel(key, "tab no longer live")
            return

        if msg.kind == MSG_EXT_UNLOADED:
            for key in eng.keys_for_extension(msg.extension_id):
                eng.cancel(key, "extension unloaded")
            return

    def start(self, conn):
        self._thread = threading.Thread(
            target=self.serve, args=(conn,), name="malabr-control", daemon=True)
        self._thread.start()


def eng_stop(engine, env):
    return engine.stop_generation(env.session_key, "stopped")


class MalabrServer:
    """UDS listener + request routing."""

    def __init__(self, cfg, engine, formatter_factory, output_cap, lock=None):
        self._cfg = cfg
        self._engine = engine
        self._formatter_factory = formatter_factory
        self._output_cap = output_cap
        self._control = ControlReader(engine)
        # app.main() acquires the pid lock BEFORE the model load so a doom...  [notes: runtime.MalabrServer.__init__]
        self._lock = lock or PidLock(cfg.socket_path + ".pid")
        self._server = None
        self._pool = None
        self._running = False

    # -- lifecycle ---------------------------------------------------------

    def start(self):
        self._lock.acquire()                # raises if another instance lives
        # Only now is it safe to clear the socket  [notes: runtime.MalabrServer.start]
        if os.path.exists(self._cfg.socket_path):
            os.unlink(self._cfg.socket_path)

        self._server = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        self._server.bind(self._cfg.socket_path)
        self._server.listen(128)
        os.chmod(self._cfg.socket_path, 0o600)   # this user only
        self._pool = ThreadPoolExecutor(max_workers=CLIENT_POOL_WORKERS)
        self._running = True

    def stop(self):
        self._running = False
        for closer in (self._server,):
            if closer is not None:
                try:
                    closer.close()
                except OSError:
                    pass
        if self._pool is not None:
            self._pool.shutdown(wait=False)
        if os.path.exists(self._cfg.socket_path):
            try:
                os.unlink(self._cfg.socket_path)
            except OSError:
                pass
        self._lock.release()

    def serve_forever(self):
        while self._running:
            try:
                conn, _ = self._server.accept()
            except OSError:
                if self._running:
                    raise
                return
            self._pool.submit(self._guard, conn)

    def _guard(self, conn):
        try:
            self.handle_connection(conn)
        except Exception as exc:                     # never kill a pool worker
            print(f"malabr: connection error: {exc}", file=sys.stderr)
            try:
                conn.close()
            except OSError:
                pass

    # -- routing -----------------------------------------------------------

    def handle_connection(self, conn):
        head = recv_full(conn, 4)
        if head is None:
            conn.close()
            return
        (header_len,) = struct.unpack(">I", head)
        # BOUND BEFORE recv_full -- the same rule protocol.py applies to p...  [notes: runtime.MalabrServer.handle_connection]
        if header_len > MAX_HEADER_LEN:
            conn.close()
            return
        header = recv_full(conn, header_len)
        if header is None:
            conn.close()
            return
        env = unpack_client_envelope(header)         # validates and bounds

        if env.route == ROUTE_STOP:
            # Identity comes from the header, exactly as it does for generate,
            # so a page cannot stop a session that is not its own.
            stopped = eng_stop(self._engine, env)
            self._send(conn, FRAME_COMPLETE, "stopped" if stopped else "nothing to stop")
            try:
                conn.close()
            except OSError:
                pass
            return

        if env.route == ROUTE_CONTROL:
            # Hand the socket to the dedicated reader thread and do NOT close
            # it here -- it lives for the whole browser session.
            self._control.start(conn)
            return

        try:
            self.handle_generate(conn, env)
        finally:
            try:
                conn.close()
            except OSError:
                pass

    def handle_generate(self, conn, env):
        prompt = recv_full(conn, env.payload_size)   # size already bounded
        if prompt is None:
            return
        try:
            text = prompt.decode("utf-8")
        except UnicodeDecodeError:
            self._send(conn, FRAME_ERROR, "prompt is not valid UTF-8")
            return

        # Model switcher  [notes: runtime.MalabrServer.handle_generate]
        if text.startswith(META_PREFIX):
            self._handle_meta(conn, env, text[len(META_PREFIX):])
            return

        eng = self._engine
        # RULE 1: cross-origin eviction  [notes: runtime.MalabrServer.handle_generate]
        doomed = eng.evict_other_origins(env.extension_id, env.tab_id, env.origin)
        print(f"malabr: generate ext={env.extension_id[:6]} tab={env.tab_id} "
              f"origin={env.origin} vis={env.visibility} chars={len(text)} "
              f"evicted={len(doomed)}", file=sys.stderr, flush=True)
        # Wait for the eviction we just asked for to actually complete  [notes: runtime.MalabrServer.handle_generate]
        eng.wait_for_teardown(doomed)

        session, created = eng.get_or_create(
            env.session_key, self._formatter_factory, self._output_cap)
        print(f"malabr:   -> session {'CREATED (no prior context)' if created else 'reused'}"
              f" turns_so_far={len(getattr(session, 'turn_boundaries', [])) if session else 0}",
              file=sys.stderr, flush=True)
        if session is None:
            # §7's named limitation  [notes: runtime.MalabrServer.handle_generate]
            self._send(conn, FRAME_ERROR, "no free session slots")
            return
        if created and env.tab_id != -1 and env.is_foreground_seed \
                and eng.foreground_tab_id == -1:
            # Visibility is a SEED for a NEW session only, and only while the...  [notes: runtime.MalabrServer.handle_generate]
            eng.foreground_tab_id = env.tab_id

        outbox = eng.submit(env.session_key, text)
        if outbox is None:
            self._send(conn, FRAME_ERROR, "session unavailable")
            return
        self._stream(conn, session, outbox)

    def _stream(self, conn, session, outbox):
        """Pump frames until exactly ONE terminal frame. Rule 2.

        Rationale: NOTES.md, runtime.MalabrServer._stream
        """
        import queue as _q
        while True:
            try:
                frame_type, payload = outbox.get(timeout=OUTBOX_POLL_SECONDS)
            except _q.Empty:
                # Nothing arrived  [notes: runtime.MalabrServer._stream]
                if session.state == "dead":
                    self._send(conn, FRAME_ERROR, "session ended")
                    return
                continue
            if not self._send(conn, frame_type, payload):
                return                       # peer gone; engine sees the drop
            if frame_type in (FRAME_COMPLETE, FRAME_ERROR):
                return

    @staticmethod
    def _send(conn, frame_type, payload):
        try:
            conn.sendall(encode_frame(frame_type, payload))
            return True
        except OSError:
            return False

    # -- model switcher --------------------------------------------------------

    def _reply_json(self, conn, obj):
        """Answer a meta command the same shape a generate() does: the JSON as
        one token frame, then a clean terminal frame. The panel accumulates the
        token text for the request id it used and parses it as JSON."""
        self._send(conn, FRAME_TOKEN, json.dumps(obj))
        self._send(conn, FRAME_COMPLETE, "")
        try:
            conn.close()
        except OSError:
            pass

    def _handle_meta(self, conn, env, command):
        command = command.strip().rstrip("\x00").strip()
        model_dir = self._cfg.model_dir
        current = os.path.splitext(os.path.basename(self._cfg.model_path))[0]

        if command == "list":
            self._reply_json(conn, {"current": current,
                                    "available": list_models(model_dir)})
            return

        if command == "new":
            # §3: "New chat" is a FULL teardown -- KV cache, session object, a...  [notes: runtime.MalabrServer._handle_meta]
            ended = self._engine.cancel(env.session_key, "new chat")
            if ended:
                self._engine.wait_for_teardown([env.session_key])
            self._reply_json(conn, {"ok": True, "ended": bool(ended)})
            return

        if command.startswith("switch "):
            name = command[len("switch "):].strip()
            if name == current:
                self._reply_json(conn, {"ok": True, "noop": True,
                                        "current": current})
                return
            target = resolve_model(model_dir, name)
            if target is None:
                self._reply_json(conn, {"ok": False,
                                        "error": f"unknown model {name!r}"})
                return
            # Acknowledge BEFORE re-execing, so the panel gets a reply on this...  [notes: runtime.MalabrServer._handle_meta]
            self._reply_json(conn, {"ok": True, "switching": name})
            print(f"malabr: model switch requested -> {name}; re-execing",
                  file=sys.stderr, flush=True)
            threading.Timer(0.4, self._reexec_with_model, args=(target,)).start()
            return

        self._reply_json(conn, {"ok": False,
                                "error": f"unknown meta command {command!r}"})

    def _reexec_with_model(self, model_path):
        """Replace this process with a fresh server on `model_path`.

        Rationale: NOTES.md, runtime.MalabrServer._reexec_with_model
        """
        # Let the acknowledgement frame clear the socket buffer before anything
        # is torn down.
        time.sleep(0.2)
        try:
            self.stop()                     # close + unlink the listen socket
        except Exception:
            pass
        try:
            os.unlink(self._cfg.socket_path + ".pid")
        except OSError:
            pass
        os.environ["MALABR_MODEL_PATH"] = os.path.abspath(model_path)
        app = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                           "app.py")
        os.execv(sys.executable, [sys.executable, "-u", app])


def run_server(config=None, engine=None, formatter_factory=None,
               output_cap=None, lock=None):
    cfg = config or load_config()
    server = MalabrServer(cfg, engine, formatter_factory, output_cap, lock=lock)
    server.start()

    def shutdown(*_):
        server.stop()
        raise SystemExit(0)

    signal.signal(signal.SIGINT, shutdown)
    signal.signal(signal.SIGTERM, shutdown)
    print("malabr server listening on", cfg.socket_path)
    try:
        server.serve_forever()
    finally:
        server.stop()
