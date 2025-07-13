import socket
import os
import json
from typing import Literal, TypedDict
from transformers import pipeline


# Global variable
qa_pipeline = None

class Payload(TypedDict):
    label: Literal['LABEL_READ_DATA',  'LABEL_SEND_DATA', 'LABEL_LOAD_MODEL_BERT', 'LABEL_INFER_MODEL_BERT']
    method: Literal['SEND', 'GET', 'POST']
    payload: str

def respond_back_string(conn: socket.socket, status: Literal["error" , "ok"], message: str)-> None:
    response_data = {"status": status, "message": message}
    response_json = json.dumps(response_data)

    conn.sendall(response_json.encode('utf-8'))
    print("Sent JSON response")
    return

def read_data(conn: socket.socket, payload: Payload):
    respond_back_string(conn, "ok", "I am alive!")
    return

def send_data(conn: socket.socket, payload: Payload):
    respond_back_string(conn, "ok", "Tranformed to uppercase: " + payload["payload"].upper())
    return

def load_model_bert(conn: socket.socket, payload: Payload):
    global qa_pipeline
    try:
        if qa_pipeline:
            message = "Bert model already loaded." 
            print(message)
            respond_back_string(conn, "error", message)
            return

        
        qa_pipeline = pipeline(
            "question-answering",
            model="csarron/mobilebert-uncased-squad-v2",
            tokenizer="csarron/mobilebert-uncased-squad-v2"
        )
        message = "Bert model loaded successfully"
        print(message)
        respond_back_string(conn, "ok", message)
    
    except Exception as e:
        print("error in loading bert", e)
        respond_back_string(conn, "error", "error in loading bert")
        
    return

def infer_model_bert(conn: socket.socket, payload: Payload):
    try:
        data = json.loads(payload["payload"])
    except Exception as e:
        message = "Error in parsion infer_model_bert data! with message: " + payload["payload"]
        print(message)
        respond_back_string(conn, "error", message)
        return
    
    if not data or 'question' not in data or 'context' not in data:
        message = "Payload must contain 'question' and 'context' fields."
        print(message)
        respond_back_string(conn, "error", message)
        return
    
    try:
        if not qa_pipeline:
            message = "Please load the bert model first!" 
            print(message)
            respond_back_string(conn, "error", message)
            return
        
        # it will be string because of single inference
        prediction = qa_pipeline(
            question=data['question'],
            context=data['context']
        )
        # print("Model prediction: ", prediction)
        respond_back_string(conn, "ok", "Answser: " + prediction["answer"])
        
    except Exception as e:
        message = "Inference error in model bert"
        print(message)
        respond_back_string(conn, "error", message)
    
    return

FUNCTION_LABELS = {
    "LABEL_READ_DATA": read_data,
    "LABEL_SEND_DATA": send_data,
    "LABEL_LOAD_MODEL_BERT": load_model_bert,
    "LABEL_INFER_MODEL_BERT": infer_model_bert
}

def route_to_label(conn: socket.socket, payload: Payload):
    label_type = payload['label']
    medthod_type = payload['method']
    label_function = FUNCTION_LABELS[label_type]
    if not label_function:
        message = f"Error: wrong label {label_type}"
        print(message)
        respond_back_string(conn, "error", message)
        return
    print(f"[{medthod_type}:{label_type}]")
    label_function(conn, payload)
    return

SOCKET_PATH = "/sockets/echo_socket"

# Remove existing socket if present
if os.path.exists(SOCKET_PATH):
    os.remove(SOCKET_PATH)

# Create UNIX domain socket
server = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
server.bind(SOCKET_PATH)
server.listen(1)

print(f"Server listening on {SOCKET_PATH}")

try:
    while True:
        conn, _ = server.accept()
        with conn:
            print("Client connected")

            data = conn.recv(1024)
            if not data:
                respond_back_string(conn, status="error", message="No data received")
                print("No data received")
                continue

            message = data.decode()
                        
            try:
                payload: Payload = json.loads(message)
                
            except json.JSONDecodeError:
                respond_back_string(conn, "error", "Malformed JSON")
                continue
                
            except Exception as e:
                print(f"Error in parsing the payload: {e}")
                respond_back_string(conn, status="error", message="Error in parsing the payload")
                continue
                
            try:
                route_to_label(conn, payload)
                continue
            except Exception as e:
                respond_back_string(conn, status="error", message="Error in route_to_label")
                print(f"Error in route_to_label: {e}")
                continue

except KeyboardInterrupt:
    print("\nShutting down server")
finally:
    server.close()
    os.remove(SOCKET_PATH)