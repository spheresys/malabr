"""Minimal Chrome DevTools Protocol client -- stdlib only.

Why hand-rolled: the test venv has neither `websockets` nor `websocket-client`,
and the benchmark harness should not add a dependency to a machine that is
already fragile about which interpreter has llama_cpp.

Implements just enough RFC 6455 for CDP: a client handshake, masked text
frames out, unmasked frames in, no extensions, no fragmentation on send.
"""
import base64, json, os, socket, struct, urllib.request, time


def http_json(port, path="/json/list", timeout=30.0):
    deadline = time.time() + timeout
    last = None
    while time.time() < deadline:
        try:
            with urllib.request.urlopen(f"http://127.0.0.1:{port}{path}", timeout=2) as r:
                return json.loads(r.read().decode())
        except Exception as e:                       # noqa: BLE001
            last = e
            time.sleep(0.25)
    raise RuntimeError(f"devtools endpoint never came up on {port}: {last}")


class WS:
    def __init__(self, url, timeout=180.0):
        assert url.startswith("ws://")
        rest = url[5:]
        hostport, _, path = rest.partition("/")
        host, _, port = hostport.partition(":")
        self.sock = socket.create_connection((host, int(port or 80)), timeout=10)
        self.sock.settimeout(timeout)
        key = base64.b64encode(os.urandom(16)).decode()
        req = (
            f"GET /{path} HTTP/1.1\r\nHost: {hostport}\r\n"
            "Upgrade: websocket\r\nConnection: Upgrade\r\n"
            f"Sec-WebSocket-Key: {key}\r\nSec-WebSocket-Version: 13\r\n\r\n"
        )
        self.sock.sendall(req.encode())
        buf = b""
        while b"\r\n\r\n" not in buf:
            chunk = self.sock.recv(4096)
            if not chunk:
                raise RuntimeError("handshake closed early")
            buf += chunk
        if b"101" not in buf.split(b"\r\n", 1)[0]:
            raise RuntimeError(f"handshake failed: {buf[:200]!r}")
        self._rx = buf.split(b"\r\n\r\n", 1)[1]
        self._id = 0

    # --- framing ---------------------------------------------------------
    def _recv_exact(self, n):
        while len(self._rx) < n:
            chunk = self.sock.recv(65536)
            if not chunk:
                raise RuntimeError("socket closed")
            self._rx += chunk
        out, self._rx = self._rx[:n], self._rx[n:]
        return out

    def send_text(self, data: str):
        payload = data.encode()
        n = len(payload)
        hdr = bytearray([0x81])
        if n < 126:
            hdr.append(0x80 | n)
        elif n < 65536:
            hdr.append(0x80 | 126); hdr += struct.pack(">H", n)
        else:
            hdr.append(0x80 | 127); hdr += struct.pack(">Q", n)
        mask = os.urandom(4)
        hdr += mask
        masked = bytes(b ^ mask[i % 4] for i, b in enumerate(payload))
        self.sock.sendall(bytes(hdr) + masked)

    def recv_text(self):
        while True:
            b0, b1 = self._recv_exact(2)
            opcode = b0 & 0x0F
            ln = b1 & 0x7F
            if ln == 126:
                ln = struct.unpack(">H", self._recv_exact(2))[0]
            elif ln == 127:
                ln = struct.unpack(">Q", self._recv_exact(8))[0]
            payload = self._recv_exact(ln) if ln else b""
            if opcode == 0x8:
                raise RuntimeError("server closed the websocket")
            if opcode == 0x9:                      # ping -> pong
                self.sock.sendall(b"\x8a\x80" + os.urandom(4))
                continue
            if opcode in (0x1, 0x0):
                return payload.decode("utf-8", "replace")
            # ignore binary/pong

    # --- CDP -------------------------------------------------------------
    def call(self, method, params=None, timeout=180.0):
        self._id += 1
        mid = self._id
        self.send_text(json.dumps({"id": mid, "method": method,
                                   "params": params or {}}))
        deadline = time.time() + timeout
        while time.time() < deadline:
            msg = json.loads(self.recv_text())
            if msg.get("id") == mid:
                if "error" in msg:
                    raise RuntimeError(f"{method}: {msg['error']}")
                return msg.get("result", {})
        raise TimeoutError(method)

    def evaluate(self, expr, timeout=180.0, await_promise=True):
        r = self.call("Runtime.evaluate",
                      {"expression": expr, "awaitPromise": await_promise,
                       "returnByValue": True, "userGesture": True},
                      timeout=timeout)
        if r.get("exceptionDetails"):
            d = r["exceptionDetails"]
            desc = (d.get("exception") or {}).get("description") or d.get("text")
            raise RuntimeError(f"JS threw: {desc}")
        return r["result"].get("value")

    def close(self):
        try:
            self.sock.close()
        except Exception:                            # noqa: BLE001
            pass
