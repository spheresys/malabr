import socket
import select
import threading
import struct
import os
from concurrent.futures import ThreadPoolExecutor

from types_defs import Payload
from response import respond
from logger import get_logger
from router_v2 import Router

logger = get_logger(__name__)

class Server:
    def __init__(self, sock_path: str, router: Router, max_workers=5):
        self.sock_path = sock_path
        self.router = router
        self.max_workers = max_workers
        self.server_socket = None
        self.epoll = None
        self.connections = {}
        self.thread_pool = ThreadPoolExecutor(max_workers=self.max_workers)

    def start(self):
        self._setup_socket()
        print(f"[SERVER] Listening on {self.sock_path} with {self.max_workers} workers.")
        try:
            self._event_loop()
        except KeyboardInterrupt:
            print("\n[SERVER] Shutdown signal received.")
        finally:
            self._shutdown()

    def _setup_socket(self):
        if os.path.exists(self.sock_path):
          os.remove(self.sock_path)
          
        self.server_socket = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        self.server_socket.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        self.server_socket.setblocking(False)
        self.server_socket.bind(self.sock_path)
        self.server_socket.listen(50)
        self.epoll = select.epoll()
        self.epoll.register(self.server_socket.fileno(), select.EPOLLIN)

    def _event_loop(self):
        while True:
            events = self.epoll.poll(1)
            for fileno, event in events:
                if fileno == self.server_socket.fileno():
                    self._accept_connection()
                elif event & select.EPOLLIN:
                    client_socket = self.connections.get(fileno)
                    if client_socket:
                        self.epoll.unregister(fileno)
                        del self.connections[fileno]
                        self.thread_pool.submit(self._worker_task, self.router, client_socket)


    def _accept_connection(self):
        conn, _ = self.server_socket.accept()
        conn.setblocking(False)
        self.epoll.register(conn.fileno(), select.EPOLLIN)
        self.connections[conn.fileno()] = conn

    def recv_exact(self, conn: socket.socket, n: int):
      """Read exactly n bytes from the socket."""
      buf = b""
      while len(buf) < n:
          chunk = conn.recv(n - len(buf))
          if not chunk:
              raise ConnectionError("Socket closed unexpectedly")
          buf += chunk
      return buf
    
    def _worker_task(self, router: Router, conn: socket.socket):
        """Worker task now reads the header and routes with the payload."""
        try:
            conn.setblocking(True)
            
             # Step 1: Read 4-byte header length
            raw_len = self.recv_exact(conn, 4)
            header_len = struct.unpack("!I", raw_len)[0]

            # Step 2: Read header
            header_bytes = self.recv_exact(conn, header_len)
            header_str = header_bytes.decode("utf-8")
            fb_id, label, payload_size_str = header_str.split(",")
            payload_size = int(payload_size_str)

            logger.debug(f"[WORK_IDENTIFIER:{threading.get_ident()}] Header received: fb_id={fb_id}, label={label}, payload_size={payload_size}")

            # Step 3: Read payload
            payload_bytes = self.recv_exact(conn, payload_size)
                                
            # Step 4: Format the payload
            payload: Payload = {
                "label": label,
                "payload_bytes": payload_bytes,
                "fb_id": fb_id,
                "payload_size": payload_size
            }
            
            handler = router.get_handler(label)
            if not handler:
                logger.error(f"[WORKER:{threading.get_ident()}] No handler for label {label}")
                respond(conn, "error", f"Unknown label: {label}")
                return
            
            handler(conn, payload)
            

        except Exception as e:
            print(f"[WORK_IDENTIFIER:{threading.get_ident()}] Error during task execution: {e}")
        finally:
            conn.close()

    def _shutdown(self):
        os.remove(self.sock_path)
        self.thread_pool.shutdown(wait=True)
        self.epoll.close()
        self.server_socket.close()
        print("[SERVER] Shutdown complete.")