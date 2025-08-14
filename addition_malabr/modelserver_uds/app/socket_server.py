import os
import socket
import struct

from types_defs import Payload
from router import route
from response import respond
import config
from logger import get_logger

logger = get_logger(__name__)
SOCKET_PATH = config.SOCKET_PATH

def recv_exact(sock, n):
    """Read exactly n bytes from the socket."""
    buf = b""
    while len(buf) < n:
        chunk = sock.recv(n - len(buf))
        if not chunk:
            raise ConnectionError("Socket closed unexpectedly")
        buf += chunk
    return buf

def run_socket_server():
    if os.path.exists(SOCKET_PATH):
        os.remove(SOCKET_PATH)

    server = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    server.bind(SOCKET_PATH)
    server.listen(1)

    print(f"Listening on {SOCKET_PATH}")

    try:
        while True:
            conn, _ = server.accept()
            with conn:
                try:
                    # Step 1: Read 4-byte header length
                    raw_len = recv_exact(conn, 4)
                    header_len = struct.unpack("!I", raw_len)[0]

                    # Step 2: Read header
                    header_bytes = recv_exact(conn, header_len)
                    header_str = header_bytes.decode("utf-8")
                    fb_id, label, payload_size_str = header_str.split(",")
                    payload_size = int(payload_size_str)

                    logger.debug(f"Header received: fb_id={fb_id}, label={label}, payload_size={payload_size}")

                    # Step 3: Read payload
                    payload_bytes = recv_exact(conn, payload_size)
                                        
                    # Step 4: Format the payload
                    payload: Payload = {
                        "label": label,
                        "payload_bytes": payload_bytes,
                        "fb_id": fb_id,
                        "payload_size": payload_size
                    }
                    
                    try:
                        route(conn, payload)
                    except Exception as e:
                        logger.error(f"Unhandled payload processing error: {e}")
                        respond(conn, "error", f"Unhandled payload processing error: {e}")

                except ConnectionError as ce:
                    logger.error(f"Connection error: {ce}")
                    respond(conn, "error", f"Connection error: {ce}")
                except Exception as e:
                    logger.error(f"Unhandled server error: {e}")
                    respond(conn, "error", f"Unhandled server error: {e}")
    finally:
        server.close()
        if os.path.exists(SOCKET_PATH):
            os.remove(SOCKET_PATH)