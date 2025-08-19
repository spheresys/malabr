import socket
from datetime import datetime

from response import respond
from types_defs import Payload

def handle(conn: socket.socket, payload: Payload):
    now = datetime.now()
    respond(conn, "ok", f"I am alive! {now}")