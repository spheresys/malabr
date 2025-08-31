import socket
from typing import Literal, TypedDict, Callable, Dict

LabelType = Literal[
    "LABEL_READ_DATA",
    "LABEL_SEND_DATA",
    "LABEL_LOAD_MODEL_BERT",
    "LABEL_INFER_MODEL_BERT"
]

class Payload(TypedDict):
    label: LabelType
    payload_bytes: bytes
    payload_size: int
    fb_id: str

HandlerType = Callable[[socket.socket, Payload], None]
RoutesType = Dict[LabelType, HandlerType]

RespondStatus = Literal["ok", "error"]

