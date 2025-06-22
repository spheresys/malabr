#include <sys/socket.h>
#include <sys/un.h>
#include <unistd.h>
#include <iostream>
#include <cstring>

const char SOCKET_PATH[] = "/tmp/shared-sockets/echo_socket";

int main() {
    int sock = socket(AF_UNIX, SOCK_STREAM, 0);
    if (sock < 0) {
        perror("socket");
        return 1;
    }

    sockaddr_un addr;
    std::memset(&addr, 0, sizeof(addr));
    addr.sun_family = AF_UNIX;
    std::strcpy(addr.sun_path, SOCKET_PATH);

    if (connect(sock, (struct sockaddr*)&addr, sizeof(addr)) < 0) {
        perror("connect");
        return 1;
    }

    // Send client ID (PID)
    pid_t pid = getpid();
    std::string client_id = "client_" + std::to_string(pid);
    send(sock, client_id.c_str(), client_id.size(), 0);

    std::string message;
    char buffer[1024];

    // while (true) {
        std::cout << "Enter message (type 'exit' to quit): ";
        std::getline(std::cin, message);

        // if (message == "exit") break;

        send(sock, message.c_str(), message.size(), 0);

        int len = recv(sock, buffer, sizeof(buffer) - 1, 0);
        if (len > 0) {
            buffer[len] = '\0';
            std::cout << "Response: " << buffer << std::endl;
        } else {
            std::cerr << "Server disconnected.\n";
            // break;
        }
    // }

    close(sock);
    return 0;
}
