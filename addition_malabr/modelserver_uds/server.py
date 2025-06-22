# server.py
import socket
import os

SOCKET_PATH = "/sockets/echo_socket"

if os.path.exists(SOCKET_PATH):
    os.remove(SOCKET_PATH)

with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as server_sock:
    server_sock.bind(SOCKET_PATH)
    server_sock.listen()
    print("Server is listening on", SOCKET_PATH)

    while True:
        conn, _ = server_sock.accept()
        with conn:
            print("Client connected.")

            # Read client ID (e.g., PID or name) as first message
            client_id = conn.recv(1024).decode().strip()
            print(f"Client ID: {client_id}")

            while True:
                data = conn.recv(1024)
                if not data:
                    print(f"Client {client_id} disconnected.")
                    break
                response = data.decode().upper()
                print(f"Received from {client_id}: {data.decode()}")
                conn.sendall(response.encode())
