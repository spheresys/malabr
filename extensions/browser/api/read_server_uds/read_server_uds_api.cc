#include "extensions/browser/api/read_server_uds/read_server_uds_api.h"

#include "base/json/json_writer.h"
#include "base/values.h"
#include "extensions/common/api/read_server_uds.h"

/// tmp/shared-sockets/echo_socket
namespace extensions {

constexpr char kMLServerUDSPath[] = "/tmp/shared-sockets/echo_socket";

// ALL lable for ML server function handler
constexpr char kReadServerUdsReadDataFunctionLable[] = "LABEL_READ_DATA";
constexpr char kReadServerUdsSendDataFunctionLable[] = "LABEL_SEND_DATA";
constexpr char kReadServerUdsLoadModelBERTFunctionLable[] =
    "LABEL_LOAD_MODEL_BERT";
constexpr char kReadServerUdsInferSingleBERTFunctionLable[] =
    "LABEL_INFER_MODEL_BERT";

// -------------------------
// Read Server Read Data UDS
// -------------------------
ReadServerUdsReadDataFunction::ReadServerUdsReadDataFunction() = default;

ReadServerUdsReadDataFunction::~ReadServerUdsReadDataFunction() {
  if (!did_respond()) {
    LOG(ERROR) << "Function was destroyed without responding";
    Respond(Error("Function was destroyed without responding"));
  }
}

ExtensionFunction::ResponseAction ReadServerUdsReadDataFunction::Run() {
  AddRef();  // async

  auto ml_server = std::make_unique<extensions::MLServerUDS>(
      kMLServerUDSPath, kReadServerUdsReadDataFunctionLable);

  std::string payload = "GET /data\n";

  auto buffer = base::MakeRefCounted<net::StringIOBuffer>(payload);

  ml_server->Send(buffer.get(), buffer->size(), "fb-read",
                  base::BindOnce(&ReadServerUdsReadDataFunction::OnSuccess,
                                 weak_ptr_factory_.GetWeakPtr()),
                  base::BindOnce(&ReadServerUdsReadDataFunction::OnError,
                                 weak_ptr_factory_.GetWeakPtr()));

  // Important: hold the instance if needed
  ml_server_ = std::move(ml_server);

  return RespondLater();
}

void ReadServerUdsReadDataFunction::OnSuccess(std::string result) {
  Respond(WithArguments(base::Value(result)));
  Release();
}

void ReadServerUdsReadDataFunction::OnError(std::string error_msg) {
  Respond(Error(error_msg));
  Release();
}

void ReadServerUdsReadDataFunction::OnResponded() {
  LOG(INFO) << "ReadServerUdsReadDataFunction::OnResponded() Cleaning up";

  if (ml_server_) {
    ml_server_->Clear();  // First clean up state
    ml_server_.reset();   // Then destroy safely
  }

  // Other cleanup if needed
}

// -------------------------
// Read Server Send Data UDS
// -------------------------
// Constructor for ReadServerUdsSendDataFunction
ReadServerUdsSendDataFunction::ReadServerUdsSendDataFunction() = default;

// Destructor for ReadServerUdsSendDataFunction
ReadServerUdsSendDataFunction::~ReadServerUdsSendDataFunction() {
  if (!did_respond()) {
    LOG(ERROR) << "Function was destroyed without responding";
    Respond(Error("Function was destroyed without responding"));
  }
}

ExtensionFunction::ResponseAction ReadServerUdsSendDataFunction::Run() {
  LOG(INFO) << "ReadServerUdsSendDataFunction::Run() called";

  // Validate the presence of arguments
  EXTENSION_FUNCTION_VALIDATE(has_args());
  namespace send_data_api = extensions::api::read_server_uds::SendData;

  auto maybe_params = send_data_api::Params::Create(args());
  EXTENSION_FUNCTION_VALIDATE(maybe_params);

  const std::string payload = maybe_params->data;

  AddRef();  // async

  auto ml_server = std::make_unique<extensions::MLServerUDS>(
      kMLServerUDSPath, kReadServerUdsSendDataFunctionLable);

  auto buffer = base::MakeRefCounted<net::StringIOBuffer>(payload);

  ml_server->Send(buffer.get(), buffer->size(), "fb-read",
                  base::BindOnce(&ReadServerUdsSendDataFunction::OnSuccess,
                                 weak_ptr_factory_.GetWeakPtr()),
                  base::BindOnce(&ReadServerUdsSendDataFunction::OnError,
                                 weak_ptr_factory_.GetWeakPtr()));

  // Important: hold the instance if needed
  ml_server_ = std::move(ml_server);

  return RespondLater();
}

void ReadServerUdsSendDataFunction::OnSuccess(std::string result) {
  Respond(WithArguments(base::Value(result)));
  Release();
}

void ReadServerUdsSendDataFunction::OnError(std::string error_msg) {
  Respond(Error(error_msg));
  Release();
}

void ReadServerUdsSendDataFunction::OnResponded() {
  LOG(INFO) << "ReadServerUdsSendDataFunction::OnResponded() Cleaning up";

  if (ml_server_) {
    ml_server_->Clear();  // First clean up state
    ml_server_.reset();   // Then destroy safely
  }

  // Other cleanup if needed
}

// -------------------------
// Load Model BERT Endpoint
// -------------------------
ReadServerUdsLoadModelBERTFunction::ReadServerUdsLoadModelBERTFunction() =
    default;
ReadServerUdsLoadModelBERTFunction::~ReadServerUdsLoadModelBERTFunction() {
  if (!did_respond()) {
    LOG(ERROR) << "LoadModelBERT function destroyed without responding";
    Respond(Error("Function was destroyed without responding"));
  }
}

ExtensionFunction::ResponseAction ReadServerUdsLoadModelBERTFunction::Run() {
  LOG(INFO) << "ReadServerUdsLoadModelBERTFunction::Run() called";
  AddRef();  // async

  auto ml_server = std::make_unique<extensions::MLServerUDS>(
      kMLServerUDSPath, kReadServerUdsLoadModelBERTFunctionLable);

  std::string payload = "init the bert model\n";

  auto buffer = base::MakeRefCounted<net::StringIOBuffer>(payload);

  ml_server->Send(buffer, buffer->size(), "fb-load",
                  base::BindOnce(&ReadServerUdsLoadModelBERTFunction::OnSuccess,
                                 weak_ptr_factory_.GetWeakPtr()),
                  base::BindOnce(&ReadServerUdsLoadModelBERTFunction::OnError,
                                 weak_ptr_factory_.GetWeakPtr()));

  // Important: hold the instance if needed
  ml_server_ = std::move(ml_server);

  return RespondLater();
}

void ReadServerUdsLoadModelBERTFunction::OnSuccess(std::string result) {
  Respond(WithArguments(base::Value(result)));
  Release();
}

void ReadServerUdsLoadModelBERTFunction::OnError(std::string error_msg) {
  Respond(Error(error_msg));
  Release();
}

void ReadServerUdsLoadModelBERTFunction::OnResponded() {
  LOG(INFO) << "ReadServerUdsLoadModelBERTFunction::OnResponded() Cleaning up";

  if (ml_server_) {
    ml_server_->Clear();  // First clean up state
    ml_server_.reset();   // Then destroy safely
  }

  // Other cleanup if needed
}

// -------------------------
// Single Inference BERT Endpoint
// -------------------------
ReadServerUdsInferSingleBERTFunction::ReadServerUdsInferSingleBERTFunction() =
    default;
ReadServerUdsInferSingleBERTFunction::~ReadServerUdsInferSingleBERTFunction() {
  if (!did_respond()) {
    LOG(ERROR) << "InferSingleBERT function destroyed without responding";
    Respond(Error("Function was destroyed without responding"));
  }
}

ExtensionFunction::ResponseAction ReadServerUdsInferSingleBERTFunction::Run() {
  LOG(INFO) << "ReadServerUdsInferSingleBERTFunction::Run() called";
  // Validate the presence of arguments
  EXTENSION_FUNCTION_VALIDATE(has_args());
  namespace infer_single_bert_api =
      extensions::api::read_server_uds::InferSingleBERT;

  auto maybe_params = infer_single_bert_api::Params::Create(args());

  std::string data(reinterpret_cast<const char*>(maybe_params->request.payload.data()), maybe_params->request.payload.size());
  auto payload = base::MakeRefCounted<net::StringIOBuffer>(std::move(data));

  size_t payload_size = maybe_params->request.payload.size();

  AddRef();  // async

  auto ml_server = std::make_unique<extensions::MLServerUDS>(
      kMLServerUDSPath, kReadServerUdsInferSingleBERTFunctionLable);

  ml_server->Send(
      payload, payload_size, maybe_params->request.fb_id,
      base::BindOnce(&ReadServerUdsInferSingleBERTFunction::OnSuccess,
                     weak_ptr_factory_.GetWeakPtr()),
      base::BindOnce(&ReadServerUdsInferSingleBERTFunction::OnError,
                     weak_ptr_factory_.GetWeakPtr()));

  // Important: hold the instance if needed
  ml_server_ = std::move(ml_server);

  return RespondLater();
}

void ReadServerUdsInferSingleBERTFunction::OnSuccess(std::string result) {
  Respond(WithArguments(base::Value(result)));
  Release();
}

void ReadServerUdsInferSingleBERTFunction::OnError(std::string error_msg) {
  Respond(Error(error_msg));
  Release();
}

void ReadServerUdsInferSingleBERTFunction::OnResponded() {
  LOG(INFO)
      << "ReadServerUdsInferSingleBERTFunction::OnResponded() Cleaning up";

  if (ml_server_) {
    ml_server_->Clear();  // First clean up state
    ml_server_.reset();   // Then destroy safely
  }

  // Other cleanup if needed
}
}  // namespace extensions
