import socket

from response import respond
from types_defs import Payload
from handler import read_data, send_data, qa_model
from logger import get_logger

logger = get_logger(__name__)


def route(conn: socket.socket, payload: Payload):
    label = payload["label"]

    ROUTES = {
        "LABEL_READ_DATA": read_data.handle,
        "LABEL_SEND_DATA": send_data.handle,
        "LABEL_LOAD_MODEL_BERT": qa_model.load_model,
        "LABEL_INFER_MODEL_BERT": qa_model.infer
    }

    handler = ROUTES.get(label)
    if handler:
        # logger.debug(label)
        handler(conn, payload)
    else:
        logger.error(f"Unknown label: {label}")
        respond(conn, "error", f"Unknown label: {label}")
