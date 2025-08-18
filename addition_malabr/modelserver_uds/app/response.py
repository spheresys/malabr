import socket
import json
from types_defs import RespondStatus

def respond(conn: socket.socket, status: RespondStatus, message: str) -> None:
    data = json.dumps({"status": status, "message": message})
    conn.sendall(data.encode('utf-8'))
