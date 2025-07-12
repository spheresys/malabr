# server.py
import socket
import os
import json
from typing import Literal, TypedDict

# ALL type of request
# {"label":"LABEL_READ_DATA","method":"SEND","payload":"GET /data\n"}
# {"status": "ok", "body": message.upper()}

# {"label":"LABEL_SEND_DATA","method":"SEND","payload":"Hello Server"}
# {"status": "error", "body": message.upper()}

# {"label":"LABEL_LOAD_MODEL_BERT","method":"SEND","payload":"init the bert model\n"}
# {"status": true, "key": "value", "message": "{\"LABEL\":\"LABEL_LOAD_MODEL_BERT\",\"METHOD\":\"SEND\",\"PAYLOAD\":\"INIT THE BERT MODEL\\N\"}"}

# {"label":"LABEL_INFER_MODEL_BERT","method":"SEND","payload":"{\"question\":\"me\",\"context\":\"you\"}"}
# {"status": true, "key": "value", "message": "{\"LABEL\":\"LABEL_INFER_MODEL_BERT\",\"METHOD\":\"SEND\",\"PAYLOAD\":\"{\\\"QUESTION\\\":\\\"ME\\\",\\\"CONTEXT\\\":\\\"YOU\\\"}\"}"}

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

    return

def infer_model_bert(conn: socket.socket, payload: Payload):
    
    return

def wrong_label(conn: socket.socket):
    respond_back_string(conn, status="error", message="error: unknown label")
    print("Recived unknown label")
    return

FUNCTION_LABELS = {
    "LABEL_READ_DATA": read_data,
    "LABEL_SEND_DATA": send_data,
    "LABEL_LOAD_MODEL_BERT": load_model_bert,
    "LABEL_INFER_MODEL_BERT": infer_model_bert
}

def route_to_label(conn: socket.socket, payload: Payload):
    label_function = FUNCTION_LABELS[payload['label']]
    if not label_function:
        wrong_label(conn)
        return
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
            print(f"Received: {message}")
            
            try:
                payload: Payload = json.loads(message)
                print('Pase json message: ', payload)
                
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