from transformers import pipeline
from response import respond
import socket
from types_defs import Payload

from .QAService.Payloads import Root
from .QAService.Payloads import QARequest
from .QAService.Payloads import QAResponse
from .QAService.Payloads import AnyPayload


_qa_pipeline = None

def parse_request(payload_bytes):
    # Get Root object
    root = Root.Root.GetRootAsRoot(payload_bytes, 0)

    if root.PayloadType() == AnyPayload.AnyPayload().QARequest:
        qa_req = QARequest.QARequest()
        qa_req.Init(root.Payload().Bytes, root.Payload().Pos)

        question = qa_req.Question().decode("utf-8")
        context = qa_req.Context().decode("utf-8")

        return question, context
    else:
        raise ValueError("Unsupported payload type")


def load_model(conn: socket.socket, payload: Payload) -> None:
    global _qa_pipeline
    if _qa_pipeline:
        respond(conn, "error", "Model already loaded.")
        return
    _qa_pipeline = pipeline(
        "question-answering",
        model="csarron/mobilebert-uncased-squad-v2",
        tokenizer="csarron/mobilebert-uncased-squad-v2"
    )
    respond(conn, "ok", "BERT model loaded.")

def infer(conn: socket.socket, payload: Payload) -> None:
    global _qa_pipeline
    if not _qa_pipeline:
        respond(conn, "error", "Model not loaded.")
        return
    try:
        print(f"Received payload: {payload}")
        question, context = parse_request(payload['payload_bytes'])
        print(f"Question: {question}")
        print(f"Context: {context}")
        # data = json.loads(payload['payload_bytes'].decode("utf-8"))
        # question = data.get("question")
        # context = data.get("context")
        if not question or not context:
            raise ValueError("Missing question or context")
        result = _qa_pipeline(question=question, context=context)
        respond(conn, "ok", "Answer: " + result["answer"])
    except Exception as e:
        respond(conn, "error", f"Inference failed: {e}")
