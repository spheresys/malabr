#ifndef EXTENSIONS_BROWSER_API_READ_SERVER_UDS_API_H_
#define EXTENSIONS_BROWSER_API_READ_SERVER_UDS_API_H_

#include "extensions/browser/extension_function.h"
#include "services/network/public/cpp/simple_url_loader.h"
#include <memory>
#include <string>

namespace extensions {

class ReadServerUdsReadDataFunction : public ExtensionFunction {
public:
  DECLARE_EXTENSION_FUNCTION("readServerUds.readData", READSERVER_READDATA)

  ReadServerUdsReadDataFunction();
protected:
  ~ReadServerUdsReadDataFunction() override;
private:
  ResponseAction Run() override;
  void OnJsonLoaded(std::unique_ptr<std::string> response_body);
  void OnResponded() override;

  std::unique_ptr<network::SimpleURLLoader> url_loader_;
  base::WeakPtrFactory<ReadServerUdsReadDataFunction> weak_ptr_factory_{this};
};

class ReadServerUdsSendDataFunction : public ExtensionFunction {
public:
  DECLARE_EXTENSION_FUNCTION("readServerUds.sendData", READSERVER_SENDDATA)

  ReadServerUdsSendDataFunction();
protected:
  ~ReadServerUdsSendDataFunction() override;
private:
  ResponseAction Run() override;
  void OnDataSent(std::unique_ptr<std::string> response_body);
  void OnResponded() override;

  std::unique_ptr<network::SimpleURLLoader> url_loader_;
  base::WeakPtrFactory<ReadServerUdsSendDataFunction> weak_ptr_factory_{this};
};

class ReadServerUdsUploadTrainingDataFunction : public ExtensionFunction {
 public:
  DECLARE_EXTENSION_FUNCTION("readServerUds.uploadTrainingData",
                             READSERVER_UPLOADTRAININGDATA)

  ReadServerUdsUdsUploadTrainingDataFunction();

 protected:
  ~ReadServerUdsUploadTrainingDataFunction() override;

  // ExtensionFunction:
  ResponseAction Run() override;

 private:
  // Methods
  void GenerateSyntheticData();
  void StartUploadingData();
  void UploadNextChunk();
  void OnChunkUploaded(std::unique_ptr<std::string> response_body);
  void RespondWithError(const std::string& error_message);
  void RespondWithSuccess();

  // Member variables
  std::string training_data_;
  size_t chunk_size_;
  size_t offset_;

  // Declare url_loader_
  std::unique_ptr<network::SimpleURLLoader> url_loader_;

};

class ReadServerUdsTrainModelFunction : public ExtensionFunction {
 public:
  DECLARE_EXTENSION_FUNCTION("readServerUds.trainModel", READSERVER_TRAINMODEL)
  
  ReadServerUdsTrainModelFunction();
 protected:
  ~ReadServerUdsTrainModelFunction() override;

 private:
  ResponseAction Run() override;
  void OnTrainModelResponse(std::unique_ptr<std::string> response_body);

  std::unique_ptr<network::SimpleURLLoader> url_loader_;
};


class ReadServerUdsInferenceFunction : public ExtensionFunction {
 public:
  DECLARE_EXTENSION_FUNCTION("readServerUds.inference", READSERVER_INFERENCE)  

  ReadServerUdsInferenceFunction();
 protected:
  ~ReadServerUdsInferenceFunction() override;

 private:
  ResponseAction Run() override;
  void OnInferenceResponse(std::unique_ptr<std::string> response_body);

  std::unique_ptr<network::SimpleURLLoader> url_loader_;
};

// New API for loading MobileBERT model.
class ReadServerUdsLoadModelBERTFunction : public ExtensionFunction {
  public:
   DECLARE_EXTENSION_FUNCTION("readServerUds.loadModelBERT", READSERVER_LOADMODEL_BERT)
   ReadServerUdsLoadModelBERTFunction();
  protected:
   ~ReadServerUdsLoadModelBERTFunction() override;
  private:
   ResponseAction Run() override;
   void OnResponse(std::unique_ptr<std::string> response_body);
   std::unique_ptr<network::SimpleURLLoader> url_loader_;
   base::WeakPtrFactory<ReadServerUdsLoadModelBERTFunction> weak_ptr_factory_{this};
 };
 
 // New API for single inference.
 class ReadServerUdsInferSingleBERTFunction : public ExtensionFunction {
  public:
   DECLARE_EXTENSION_FUNCTION("readServerUds.inferSingleBERT", READSERVER_INFER_SINGLE_BERT)
   ReadServerUdsInferSingleBERTFunction();
  protected:
   ~ReadServerUdsInferSingleBERTFunction() override;
  private:
   ResponseAction Run() override;
   void OnResponse(std::unique_ptr<std::string> response_body);
   std::unique_ptr<network::SimpleURLLoader> url_loader_;
 };
 
 // New API for batch inference.
 class ReadServerUdsInferBatchBERTFunction : public ExtensionFunction {
  public:
   DECLARE_EXTENSION_FUNCTION("readServerUds.inferBatchBERT", READSERVER_INFER_BATCH_BERT)
   ReadServerUdsInferBatchBERTFunction();
  protected:
   ~ReadServerUdsInferBatchBERTFunction() override;
  private:
   ResponseAction Run() override;
   void OnResponse(std::unique_ptr<std::string> response_body);
   std::unique_ptr<network::SimpleURLLoader> url_loader_;
 };


}  // namespace extensions

#endif // EXTENSIONS_BROWSER_API_READ_SERVER_UDS_API_H_