#include "extensions/browser/api/read_server_uds/ml_server_uds.h"

#include <string>
#include <arpa/inet.h>

#include "base/json/json_reader.h"
#include "base/json/json_writer.h"
#include "base/logging.h"
#include "base/task/single_thread_task_runner.h"
#include "base/values.h"
#include "content/public/browser/browser_thread.h"
#include "net/base/io_buffer.h"
#include "net/socket/unix_domain_client_socket_posix.h"

namespace extensions {

MLServerUDS::MLServerUDS(const std::string& socket_path,
                         const std::string& label)
    : socket_path_(socket_path), label_(label), weak_ptr_factory_(this) {}

MLServerUDS::~MLServerUDS() {
  LOG(INFO) << "MLServerUDS destroyed";
  DCHECK(!socket_) << "Socket must be cleared before destruction!";
}

void MLServerUDS::Send(scoped_refptr<net::IOBuffer> payload, size_t payload_size, std::string fb_file_identifier,
                       base::OnceCallback<void(std::string)> success_cb,
                       base::OnceCallback<void(std::string)> error_cb) {
  payload_ = payload;
  payload_size_ = payload_size;
  fb_file_identifier_= fb_file_identifier;
  success_callback_ = std::move(success_cb);
  error_callback_ = std::move(error_cb);

  content::GetIOThreadTaskRunner({})->PostTask(
      FROM_HERE, base::BindOnce(&MLServerUDS::ConnectToUnixSocket,
                                weak_ptr_factory_.GetWeakPtr()));
}

void MLServerUDS::Clear() {
  LOG(INFO) << "MLServerUDS::Clear() called";

  read_buffer_ = nullptr;
  payload_->Release();
  weak_ptr_factory_.InvalidateWeakPtrs();

  if (socket_) {
    auto temp_socket = std::move(socket_);
    content::GetIOThreadTaskRunner({})->PostTask(
        FROM_HERE, base::BindOnce(
                       [](std::unique_ptr<net::UnixDomainClientSocket> s) {
                         // Socket safely destroyed on IO thread
                       },
                       std::move(temp_socket)));
  }

  success_callback_.Reset();
  error_callback_.Reset();

  LOG(INFO) << "MLServerUDS internal state cleared";
}

void MLServerUDS::ConnectToUnixSocket() {
  base::FilePath path(socket_path_);
  LOG(INFO) << "Creating UnixDomainClientSocket to path: " << path.value();

  socket_ = std::make_unique<net::UnixDomainClientSocket>(
      path.value(), false /* use_abstract_namespace */);

  int result = socket_->Connect(base::BindOnce(&MLServerUDS::OnHeaderSend,
                                               weak_ptr_factory_.GetWeakPtr()));

  if (result == net::OK) {
    LOG(INFO) << "OnHeaderSend() synchronously";
    OnHeaderSend(result);
  } else if (result == net::ERR_IO_PENDING) {
    LOG(INFO) << "Connection pending, waiting for callback";
  } else {
    LOG(ERROR) << "Connect failed: " << result;
    content::GetUIThreadTaskRunner({})->PostTask(
        FROM_HERE,
        base::BindOnce(std::move(error_callback_), "Connect failed"));
  }
}

void MLServerUDS::OnHeaderSend(int result){
  if (result != net::OK) {
    LOG(ERROR) << "OnHeaderSend: Socket connection failed: " << result;
    content::GetUIThreadTaskRunner({})->PostTask(
        FROM_HERE,
        base::BindOnce(std::move(error_callback_), "OnHeaderSend: Socket connection failed"));
    return;
  }

  std::string header_payload = GetHeaderPayload();
  LOG(INFO) << "Socket connected, sending the header ";

  auto header_buffer = base::MakeRefCounted<net::StringIOBuffer>(header_payload);

  net::NetworkTrafficAnnotationTag annotation =
      net::DefineNetworkTrafficAnnotation("ml_server_uds_sending_header", R"(
        semantics {
          sender: "MLServerUDS"
          description: "Sends the header to a local UDS server."
          trigger: "Extension request."
          data: "fb fileidentifier, label, payload length"
          destination: LOCAL
        }
        policy {
          cookies_allowed: NO
          setting: "This cannot be disabled in settings."
        })");

  int write_result =
      socket_->Write(header_buffer.get(), header_buffer->size(),
                     base::BindOnce(&MLServerUDS::OnConnected,
                                    weak_ptr_factory_.GetWeakPtr()),
                     annotation);

  if (write_result == static_cast<int>(header_buffer->size())) {
    LOG(INFO) << "OnConnected: Header Write synchronous";
    OnConnected(write_result);
  } else if (write_result == net::ERR_IO_PENDING) {
    LOG(INFO) << "Header Write pending";
  } else {
    LOG(ERROR) << "Header Write failed: " << write_result;
    content::GetUIThreadTaskRunner({})->PostTask(
        FROM_HERE, base::BindOnce(std::move(error_callback_), "Header Write failed"));
  }
}

void MLServerUDS::OnConnected(int result) {
  if (result <= 0) {
    LOG(ERROR) << "[OnConnected]: Socket connection failed: " << result;
    content::GetUIThreadTaskRunner({})->PostTask(
        FROM_HERE,
        base::BindOnce(std::move(error_callback_), "Socket connection failed"));
    return;
  }

  LOG(INFO) << "OnConnected: sending the payload ";

  net::NetworkTrafficAnnotationTag annotation =
      net::DefineNetworkTrafficAnnotation("ml_server_uds_write", R"(
        semantics {
          sender: "MLServerUDS"
          description: "Sends a message to a local UDS server."
          trigger: "Extension request."
          data: "Arbitrary payload string."
          destination: LOCAL
        }
        policy {
          cookies_allowed: NO
          setting: "This cannot be disabled in settings."
        })");

  int write_result =
      socket_->Write(payload_.get(), payload_size_,
                     base::BindOnce(&MLServerUDS::OnDataWritten,
                                    weak_ptr_factory_.GetWeakPtr()),
                     annotation);

  if (write_result == static_cast<int>(payload_->size())) {
    LOG(INFO) << "Write synchronous";
    OnDataWritten(write_result);
  } else if (write_result == net::ERR_IO_PENDING) {
    LOG(INFO) << "Write pending";
  } else {
    LOG(ERROR) << "Write failed: " << write_result;
    content::GetUIThreadTaskRunner({})->PostTask(
        FROM_HERE, base::BindOnce(std::move(error_callback_), "Write failed"));
  }
}

void MLServerUDS::OnDataWritten(int result) {
  if (result <= 0) {
    LOG(ERROR) << "Write error: " << result;
    content::GetUIThreadTaskRunner({})->PostTask(
        FROM_HERE, base::BindOnce(std::move(error_callback_), "Write failed"));
    return;
  }

  LOG(INFO) << "Write successful: " << result << " bytes";

  read_buffer_ = base::MakeRefCounted<net::IOBufferWithSize>(4096);

  int read_result = socket_->Read(
      read_buffer_.get(), 4096,
      base::BindOnce(&MLServerUDS::OnDataRead, weak_ptr_factory_.GetWeakPtr()));

  if (read_result != net::ERR_IO_PENDING && read_result <= 0) {
    LOG(ERROR) << "Read error: " << read_result;
    content::GetUIThreadTaskRunner({})->PostTask(
        FROM_HERE, base::BindOnce(std::move(error_callback_), "Read failed"));
  } else {
    LOG(INFO) << "Read started";
  }
}

void MLServerUDS::OnDataRead(int result) {
  if (result <= 0) {
    LOG(ERROR) << "Read failed: " << result;
    content::GetUIThreadTaskRunner({})->PostTask(
        FROM_HERE, base::BindOnce(std::move(error_callback_), "Read failed"));
    return;
  }

  std::string response(read_buffer_->data(), result);
  LOG(INFO) << "Received: " << response;

  content::GetUIThreadTaskRunner({})->PostTask(
      FROM_HERE,
      base::BindOnce(std::move(success_callback_), std::move(response)));
}

std::string MLServerUDS::CreateJSONStringPayload(const std::string& label,
                                                 const std::string& method,
                                                 const std::string& message) {
  base::Value::Dict dict;
  dict.Set("label", label);
  dict.Set("method", method);
  dict.Set("payload", message);

  std::string json_str;
  base::JSONWriter::Write(dict, &json_str);
  return json_str;
}

std::string MLServerUDS::GetHeaderPayload() {
    // 1. Construct the header string
    std::string header = fb_file_identifier_ + "," + label_ + "," + std::to_string(payload_size_);

    // 2. Compute its length
    uint32_t header_len = static_cast<uint32_t>(header.size());

    // 3. Convert length to network byte order (big endian)
    uint32_t header_len_net = htonl(header_len);

    // 4. Build final output: 4-byte length prefix + header
    std::string out;
    out.reserve(sizeof(header_len_net) + header.size());
    out.append(reinterpret_cast<const char*>(&header_len_net), sizeof(header_len_net));
    out.append(header);

    return out;
}

}  // namespace extensions
