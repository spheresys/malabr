import socket

from response import respond
from types_defs import Payload


def handle(conn: socket.socket, payload: Payload):
    respond(conn, "ok", "Uppercased: " + payload["payload_bytes"].decode("utf-8").upper())
