import socket
import struct
import os
import threading
from concurrent.futures import ThreadPoolExecutor

from types_defs import Payload
from response import respond
from logger import get_logger
from router_v2 import Router

logger = get_logger(__name__)

MAX_HEADER = 8 * 1024

class Server:
    def __init__(self, sock_path: str, router: Router, max_workers=10):
        self.sock_path = sock_path
        self.router = router
        self.max_workers = max_workers
        self.server_socket = None
        self.thread_pool = ThreadPoolExecutor(max_workers=self.max_workers)

    def start(self):
        self._setup_socket()
        print(f"[SERVER] Listening on {self.sock_path} with {self.max_workers} workers.")
        try:
            while True:
                conn, _ = self.server_socket.accept()
                self.thread_pool.submit(self._handle_request, conn)
        except KeyboardInterrupt:
            print("\n[SERVER] Shutdown signal received.")
        finally:
            self._shutdown()

    def _setup_socket(self):
        if os.path.exists(self.sock_path):
            os.remove(self.sock_path)

        self.server_socket = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        self.server_socket.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        self.server_socket.bind(self.sock_path)
        self.server_socket.listen(50)

    def _handle_request(self, conn: socket.socket):
        try:
            raw_len = self._recv_exact(conn, 4)
            header_len = struct.unpack("!I", raw_len)[0]

            header_bytes = self._recv_exact(conn, header_len)
            header_str = header_bytes.decode("utf-8")
            fb_id, label, payload_size_str = header_str.split(",")
            payload_size = int(payload_size_str)

            payload_bytes = self._recv_exact(conn, payload_size)

            payload: Payload = {
                "label": label,
                "payload_bytes": payload_bytes,
                "fb_id": fb_id,
                "payload_size": payload_size,
            }

            handler = self.router.get_handler(label)
            if not handler:
                respond(conn, "error", f"Unknown label: {label}")
                return

            handler(conn, payload)

        except Exception as e:
            logger.error(f"[Worker:{threading.get_ident()}] Error: {e}")
            try:
                respond(conn, "error", str(e))
            except Exception:
                pass
        finally:
            conn.close()

    def _recv_exact(self, conn: socket.socket, n: int) -> bytes:
        buf = b""
        while len(buf) < n:
            chunk = conn.recv(n - len(buf))
            if not chunk:
                raise ConnectionError("Socket closed unexpectedly")
            buf += chunk
        return buf

    def _shutdown(self):
        try:
            os.remove(self.sock_path)
        except FileNotFoundError:
            pass
        self.thread_pool.shutdown(wait=True)
        self.server_socket.close()
        print("[SERVER] Shutdown complete.")
