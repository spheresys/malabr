
// READ DATA
const readDataBtnEle = document.getElementById('readDataBtn');
const showReadDataResponseEle = document.getElementById('showReadDataResponse');

readDataBtnEle.addEventListener('click', () => {
  // Clear previous
  showReadDataResponseEle.textContent = '';
  showReadDataResponseEle.classList.remove('error');

  chrome.readServerUds.readData((response) => {
    if (chrome.runtime.lastError) {
      showReadDataResponseEle.textContent = 'Native Error: ' + chrome.runtime.lastError.message;
      showReadDataResponseEle.classList.add('error');
      return;
    }

    let parsedResponse;
    try {
      parsedResponse = JSON.parse(response);
    } catch {
      showReadDataResponseEle.textContent = 'Invalid JSON response.';
      showReadDataResponseEle.classList.add('error');
      return;
    }

    if (!parsedResponse.status) {
      showReadDataResponseEle.textContent = parsedResponse.message || 'Server returned an error.';
      showReadDataResponseEle.classList.add('error');
      return;
    }

    showReadDataResponseEle.textContent = parsedResponse.message || 'Success!';
    showReadDataResponseEle.classList.remove('error');
  });
});


// SEND DATA
const sendDataBtnEle = document.getElementById('sendDataBtn');
const sendDataIptEle = document.getElementById('sendDataIpt');
const sendDataErrorEle = document.getElementById('sendDataError');
const showsendDataResponseEle = document.getElementById('showsendDataResponse');

sendDataBtnEle.addEventListener('click', () => {
  const message = sendDataIptEle.value.trim();

  // Clear previous
  sendDataErrorEle.textContent = '';
  sendDataErrorEle.classList.remove('error');
  showsendDataResponseEle.textContent = '';
  showsendDataResponseEle.classList.remove('error');

  if (!message) {
    sendDataErrorEle.textContent = 'Please enter a message before sending.';
    sendDataErrorEle.classList.add('error');
    return;
  }

  chrome.readServerUds.sendData(message, (response) => {
    if (chrome.runtime.lastError) {
      sendDataErrorEle.textContent = 'Native Error: ' + chrome.runtime.lastError.message;
      sendDataErrorEle.classList.add('error');
      return;
    }

    let parsedResponse;
    try {
      parsedResponse = JSON.parse(response);
    } catch {
      sendDataErrorEle.textContent = 'Invalid JSON response.';
      sendDataErrorEle.classList.add('error');
      return;
    }

    if (parsedResponse.status == "error") {
      sendDataErrorEle.textContent = parsedResponse.message || 'Server returned an error.';
      sendDataErrorEle.classList.add('error');
      return;
    } else {
      showsendDataResponseEle.textContent = parsedResponse.message || 'Success!';
      // sendDataIptEle.value = '';
    }

  });
});


// LOAD BERT
const loadBertBtnEle = document.getElementById('loadBertBtn');
const showLoadBertResponseEle = document.getElementById('showLoadBertResponse');

loadBertBtnEle.addEventListener('click', () => {
  // Clear previous
  showLoadBertResponseEle.textContent = '';
  showLoadBertResponseEle.classList.remove('error');

  // show message loading
  showLoadBertResponseEle.textContent = 'Loading...';
  showLoadBertResponseEle.classList.add('loading');
  
  chrome.readServerUds.loadModelBERT((response) => {
    
    // removing the loading style
    showLoadBertResponseEle.textContent = '';
    showLoadBertResponseEle.classList.remove('loading');

    if (chrome.runtime.lastError) {
      showLoadBertResponseEle.textContent = 'Native Error: ' + chrome.runtime.lastError.message;
      showLoadBertResponseEle.classList.add('error');
      return;
    }

    let parsedResponse;
    try {
      parsedResponse = JSON.parse(response);
    } catch {
      showLoadBertResponseEle.textContent = 'Invalid JSON response.';
      showLoadBertResponseEle.classList.add('error');
      return;
    }

    if (parsedResponse.status == "error") {
      showLoadBertResponseEle.textContent = parsedResponse.message || 'Server returned an error.';
      showLoadBertResponseEle.classList.add('error');
      return;
    } else {
      showLoadBertResponseEle.textContent = parsedResponse.message || 'Success!';
      showLoadBertResponseEle.classList.remove(['error'])
    }

  });
});


function createQARequestBuffer(question, context) {
  const builder = new flatbuffers.Builder(1024);

  // Create strings in buffer
  const questionOffset = builder.createString(question);
  const contextOffset = builder.createString(context);

  // Build QARequest
  QAService.Payloads.QARequest.startQARequest(builder);
  QAService.Payloads.QARequest.addQuestion(builder, questionOffset);
  QAService.Payloads.QARequest.addContext(builder, contextOffset);
  const qaRequestOffset = QAService.Payloads.QARequest.endQARequest(builder);

  // Wrap in Root table with union type
  QAService.Payloads.Root.startRoot(builder);
  QAService.Payloads.Root.addPayloadType(builder, QAService.Payloads.AnyPayload.QARequest);
  QAService.Payloads.Root.addPayload(builder, qaRequestOffset);
  const rootOffset = QAService.Payloads.Root.endRoot(builder);

  // Finish with file identifier
  builder.finish(rootOffset, "QASV");

  // Return as Uint8Array
  return builder.asUint8Array();
}



// SINGLE INFERENCE BERT
const singleBertInferBtnEle = document.getElementById('singleBertInferBtn');
const bertQuestionInputEle = document.getElementById('bertQuestionInput');
const bertContextInputEle = document.getElementById('bertContextInput');
const bertInputErrorEle = document.getElementById('bertInputError');
const singleBertInferResponseEle = document.getElementById('singleBertInferResponse');

singleBertInferBtnEle.addEventListener('click', () => {
  const question = bertQuestionInputEle.value.trim();
  const context = bertContextInputEle.value.trim();

  bertInputErrorEle.textContent = '';
  bertInputErrorEle.classList.remove('error');
  singleBertInferResponseEle.textContent = '';
  singleBertInferResponseEle.classList.remove('error');

  if (!question || !context) {
    bertInputErrorEle.textContent = 'Please fill both question and context before sending.';
    bertInputErrorEle.classList.add('error');
    return;
  }

  // Create FlatBuffer payload
  const flatbufferPayload = createQARequestBuffer(question, context);

  // Send binary payload instead of JSON
  chrome.readServerUds.inferSingleBERT(flatbufferPayload, (response) => {
    if (chrome.runtime.lastError) {
      bertInputErrorEle.textContent = 'Native Error: ' + chrome.runtime.lastError.message;
      bertInputErrorEle.classList.add('error');
      return;
    }

    // Expect binary response — decode with FlatBuffers instead of JSON.parse
    const bytes = new Uint8Array(response);
    const buf = new flatbuffers.ByteBuffer(bytes);
    const root = QAService.Payloads.Root.getRootAsRoot(buf);

    if (root.payloadType() === QAService.Payloads.AnyPayload.QAResponse) {
      const qaResp = root.payload(new QAService.Payloads.QAResponse());
      singleBertInferResponseEle.textContent = qaResp.answer();
    } else {
      bertInputErrorEle.textContent = 'Unexpected payload type in response.';
      bertInputErrorEle.classList.add('error');
    }
  });
});
