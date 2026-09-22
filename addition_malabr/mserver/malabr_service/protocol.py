"""MALABR wire protocol -- see design_doc/phase1_design.md section 10a.

Rationale: NOTES.md, protocol
"""

import struct

ROUTE_GENERATE = "ROUTE_MALABR_GENERATE_API"
ROUTE_CONTROL = "ROUTE_MALABR_CONTROL"
# A user-initiated stop  [notes: protocol.(module)]
ROUTE_STOP = "ROUTE_MALABR_STOP"

# Response frame types (section 6). Must match mserver_uds.cc's kFrame* values.
FRAME_TOKEN = 0
FRAME_COMPLETE = 1
FRAME_ERROR = 2

# Matches kMaxFramePayload in mserver_uds.cc  [notes: protocol.(module)]
MAX_FRAME_PAYLOAD = 1024 * 1024

# Upper bound on an inbound prompt. The C++ caps its own outbound writes, but
# section 10a is explicit that the server cannot rely on the peer being ours.
MAX_PAYLOAD_SIZE = 1024 * 1024

# A 6-field header is short; anything large is a peer probing for an allocation.
MAX_HEADER_LEN = 4096

# Section 5: extension ids are exactly 32 characters drawn from a-p.
EXTENSION_ID_LEN = 32
_EXT_ID_CHARS = frozenset("abcdefghijklmnop")


class ProtocolError(ValueError):
    """Malformed input from the peer. Always close the connection."""


def recv_full(conn, size):
    """Read exactly `size` bytes, or return None if the peer closed early.

    Rationale: NOTES.md, protocol.recv_full
    """
    if size < 0:
        raise ProtocolError(f"negative read size {size}")
    if size == 0:
        return b""
    chunks = []
    got = 0
    while got < size:
        packet = conn.recv(size - got)
        if not packet:
            return None
        chunks.append(packet)
        got += len(packet)
    return b"".join(chunks)


class ClientEnvelope:
    """The 6-field header the browser sends on every connection.

    Rationale: NOTES.md, protocol.ClientEnvelope
    """

    __slots__ = ("route", "extension_id", "tab_id", "origin", "visibility",
                 "payload_size")

    def __init__(self, route, extension_id, tab_id, origin, visibility,
                 payload_size):
        self.route = route
        self.extension_id = extension_id
        self.tab_id = tab_id
        self.origin = origin
        self.visibility = visibility
        self.payload_size = payload_size

    @property
    def session_key(self):
        """Section 5g: origin is PART of the key.

        Rationale: NOTES.md, protocol.ClientEnvelope.session_key
        """
        return (self.extension_id, self.tab_id, self.origin)

    @property
    def is_foreground_seed(self):
        """Section 6: visibility is a SEED for a new session only.

        Rationale: NOTES.md, protocol.ClientEnvelope.is_foreground_seed
        """
        return self.visibility == "foreground"

    def __repr__(self):
        return (f"ClientEnvelope({self.route}, {self.extension_id[:6]}..., "
                f"tab={self.tab_id}, {self.origin}, {self.visibility}, "
                f"{self.payload_size}B)")


def unpack_client_envelope(header_bytes):
    """Parse and VALIDATE the 6-field header.

    Rationale: NOTES.md, protocol.unpack_client_envelope
    """
    if not header_bytes:
        raise ProtocolError("empty header")
    if len(header_bytes) > MAX_HEADER_LEN:
        raise ProtocolError(f"header too long ({len(header_bytes)})")

    try:
        header = header_bytes.decode("ascii")
    except UnicodeDecodeError as exc:
        # Every field is ASCII by construction  [notes: protocol.unpack_client_envelope]
        raise ProtocolError("header is not ASCII") from exc

    parts = header.split(",")
    if len(parts) != 6:
        raise ProtocolError(f"expected 6 header fields, got {len(parts)}")
    route, extension_id, tab_id_text, origin, visibility, size_text = parts

    if route not in (ROUTE_GENERATE, ROUTE_CONTROL, ROUTE_STOP):
        raise ProtocolError(f"unknown route {route!r}")

    if len(extension_id) != EXTENSION_ID_LEN or not _EXT_ID_CHARS.issuperset(extension_id):
        raise ProtocolError("malformed extension id")

    try:
        tab_id = int(tab_id_text)
    except ValueError as exc:
        raise ProtocolError(f"malformed tab id {tab_id_text!r}") from exc
    # -1 is the documented "not tab-scoped" value; anything below that is junk.
    if tab_id < -1:
        raise ProtocolError(f"invalid tab id {tab_id}")

    if not origin:
        raise ProtocolError("empty origin")
    if origin == "null":
        # Section 5g / malabr_api.cc:77  [notes: protocol.unpack_client_envelope]
        raise ProtocolError("opaque origin rejected")

    if visibility not in ("foreground", "background"):
        raise ProtocolError(f"invalid visibility {visibility!r}")

    try:
        payload_size = int(size_text)
    except ValueError as exc:
        raise ProtocolError(f"malformed payload size {size_text!r}") from exc
    if payload_size < 0:
        raise ProtocolError(f"negative payload size {payload_size}")
    # BOUND BEFORE recv_full -- the whole point  [notes: protocol.unpack_client_envelope]
    if payload_size > MAX_PAYLOAD_SIZE:
        raise ProtocolError(
            f"payload size {payload_size} exceeds {MAX_PAYLOAD_SIZE}")
    if route in (ROUTE_CONTROL, ROUTE_STOP) and payload_size != 0:
        raise ProtocolError(f"{route} must declare payload_size=0")

    return ClientEnvelope(route, extension_id, tab_id, origin, visibility,
                          payload_size)


def encode_frame(frame_type, payload):
    """[1B type][4B BE length][payload]."""
    if frame_type not in (FRAME_TOKEN, FRAME_COMPLETE, FRAME_ERROR):
        raise ProtocolError(f"unknown frame type {frame_type}")
    if isinstance(payload, str):
        payload = payload.encode("utf-8")
    if len(payload) > MAX_FRAME_PAYLOAD:
        raise ProtocolError(f"frame payload too large ({len(payload)})")
    return struct.pack(">BI", frame_type, len(payload)) + payload




# ----------------------------------------------------------------...  [notes: protocol.(module)]

MSG_FOREGROUND = "FOREGROUND"
MSG_TAB_CLOSED = "TAB_CLOSED"
MSG_LIVE_TABS = "LIVE_TABS"
MSG_EXT_UNLOADED = "EXT_UNLOADED"

# One control message is a short ASCII line. LIVE_TABS is the longest, carrying
# one id per live tab; a few thousand tabs would still fit well inside this.
MAX_CONTROL_MSG_LEN = 64 * 1024


class ControlMessage:
    __slots__ = ("kind", "tab_id", "tab_ids", "extension_id")

    def __init__(self, kind, tab_id=None, tab_ids=None, extension_id=None):
        self.kind = kind
        self.tab_id = tab_id
        self.tab_ids = tab_ids
        self.extension_id = extension_id

    def __repr__(self):
        detail = (self.extension_id if self.extension_id
                  else self.tab_ids if self.tab_ids is not None
                  else self.tab_id)
        return f"ControlMessage({self.kind}, {detail})"


def parse_control_message(raw):
    """Parse one control message.

    Rationale: NOTES.md, protocol.parse_control_message
    """
    if isinstance(raw, bytes):
        if len(raw) > MAX_CONTROL_MSG_LEN:
            raise ProtocolError(f"control message too long ({len(raw)})")
        try:
            raw = raw.decode("ascii")
        except UnicodeDecodeError as exc:
            raise ProtocolError("control message is not ASCII") from exc
    raw = raw.strip()
    if not raw:
        raise ProtocolError("empty control message")

    parts = raw.split(",")
    kind = parts[0]

    if kind == MSG_FOREGROUND:
        if len(parts) != 2:
            raise ProtocolError("FOREGROUND takes exactly one tab id")
        return ControlMessage(kind, tab_id=_tab_id(parts[1]))

    if kind == MSG_TAB_CLOSED:
        if len(parts) != 2:
            raise ProtocolError("TAB_CLOSED takes exactly one tab id")
        return ControlMessage(kind, tab_id=_tab_id(parts[1]))

    if kind == MSG_LIVE_TABS:
        # "LIVE_TABS" with no ids is legitimate and means NO live tabs, wh...  [notes: protocol.parse_control_message]
        ids = [_tab_id(p) for p in parts[1:] if p != ""]
        return ControlMessage(kind, tab_ids=ids)

    if kind == MSG_EXT_UNLOADED:
        if len(parts) != 2:
            raise ProtocolError("EXT_UNLOADED takes exactly one extension id")
        ext = parts[1]
        if len(ext) != EXTENSION_ID_LEN or not _EXT_ID_CHARS.issuperset(ext):
            raise ProtocolError("malformed extension id")
        return ControlMessage(kind, extension_id=ext)

    raise ProtocolError(f"unknown control message {kind!r}")


def _tab_id(text):
    try:
        value = int(text)
    except ValueError as exc:
        raise ProtocolError(f"malformed tab id {text!r}") from exc
    if value < -1:
        raise ProtocolError(f"invalid tab id {value}")
    return value


def read_control_message(conn):
    """Read one [4B BE length][ASCII] control message, or None if closed."""
    head = recv_full(conn, 4)
    if head is None:
        return None
    (length,) = struct.unpack(">I", head)
    if length > MAX_CONTROL_MSG_LEN:
        raise ProtocolError(f"control message too long ({length})")
    body = recv_full(conn, length)
    if body is None:
        return None
    return parse_control_message(body)
