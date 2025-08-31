#ifndef EXTENSIONS_BROWSER_API_READ_SERVER_UDS_ML_SERVER_UDS_H_
#define EXTENSIONS_BROWSER_API_READ_SERVER_UDS_ML_SERVER_UDS_H_

#include <shared_mutex>
#include <string>

#include "base/memory/weak_ptr.h"
#include "net/base/io_buffer.h"
#include "net/socket/unix_domain_client_socket_posix.h"

namespace extensions {

class MLServerUDS {
 public:
  MLServerUDS(const std::string& socket_path, const std::string& label);
  ~MLServerUDS();

  void Send(scoped_refptr<net::IOBuffer> payload,
            size_t payload_size,
            std::string fb_file_identifier,
            base::OnceCallback<void(std::string)> success_cb,
            base::OnceCallback<void(std::string)> error_cb);

  void Get(const std::string& payload,
           base::OnceCallback<void(std::string)> success_cb,
           base::OnceCallback<void(std::string)> error_cb);

  void Post(const std::string& payload,
            base::OnceCallback<void(std::string)> success_cb,
            base::OnceCallback<void(std::string)> error_cb);

  void Clear();
  //  protected:

 private:
  void ConnectToUnixSocket();
  void OnHeaderSend(int result);
  void OnConnected(int result);
  void OnDataWritten(int result);
  // Write next 64KB chunk
  void WriteNextChunk(const net::NetworkTrafficAnnotationTag& annotation);

  // Called when a chunk write completes
  void OnChunkWritten(scoped_refptr<net::IOBuffer> buf,
                      size_t expected,
                      const net::NetworkTrafficAnnotationTag& annotation,
                      int result);
  void OnDataRead(int result);
  std::string GetHeaderPayload();
  std::string CreateJSONStringPayload(const std::string& label,
                                      const std::string& method,
                                      const std::string& payload);

  std::string socket_path_;
  scoped_refptr<net::IOBuffer> payload_;
  size_t payload_size_;
  size_t bytes_sent_ = 0;
  std::string fb_file_identifier_;
  std::string label_;
  std::string model_name_;
  std::unique_ptr<net::UnixDomainClientSocket> socket_;
  scoped_refptr<net::IOBufferWithSize> read_buffer_;

  base::OnceCallback<void(std::string)> success_callback_;
  base::OnceCallback<void(std::string)> error_callback_;

  base::WeakPtrFactory<MLServerUDS> weak_ptr_factory_{this};
};

}  // namespace extensions

#endif  // EXTENSIONS_BROWSER_API_READ_SERVER_UDS_ML_SERVER_UDS_H_
