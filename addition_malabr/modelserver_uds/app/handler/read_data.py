import socket

from response import respond
from types_defs import Payload

def handle(conn: socket.socket, payload: Payload):
    respond(conn, "ok", "I am alive!")
