import { flatbuffers } from './flatbuffers.js';
import QAService from './qa_schema_generated.js';

// console.log('flatbuffers:', flatbuffers);
// console.log('QAService:', QAService);
// console.log('QAService.Payloads:', QAService.Payloads);
// console.log('QAService.Payloads.QARequest:', QAService.Payloads?.QARequest);

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

  const flatbufferPayload = createQARequestBuffer(question, context);

  chrome.readServerUds.inferSingleBERT({ payload: flatbufferPayload, fb_id: "QASV" }, (response) => {
    if (chrome.runtime.lastError) {
      bertInputErrorEle.textContent = 'Native Error: ' + chrome.runtime.lastError.message;
      bertInputErrorEle.classList.add('error');
      return;
    }

    // Normal JSON on
    response = JSON.parse(response)
    if (response.status == "ok") {
      singleBertInferResponseEle.textContent = response.message;
    } else if (response.status == "error") {
      singleBertInferResponseEle.textContent = response.message;
      bertInputErrorEle.classList.add('error');
    } else {
      bertInputErrorEle.textContent = 'Unexpected payload type in response.';
      bertInputErrorEle.classList.add('error');
    }
  });
});

function inferSingleBERTAsync(args) {
  return new Promise((resolve, reject) => {
    try {
      chrome.readServerUds.inferSingleBERT(args, (result) => {
        resolve(result);
      });
    } catch (err) {
      reject(err);
    }
  });
}

// SINGLE INFERENCE BENCHMARK BERT
const singleBertInferBenchmarkBtnEle = document.getElementById('singleBertBenchmarkBtn');
const singleBertInferBenchmarkIterationEle = document.getElementById('singleBertInferBenchmarkIteration');
const singleBertInferBenchmarkIterationTimeEle = document.getElementById('singleBertInferBenchmarkIterationTime');
const NO_OF_INTERATION = 1000
const NO_OF_WARMUP_INTERATION = 100

singleBertInferBenchmarkBtnEle.addEventListener('click', async () => {
  // Define fixed question and context 
  const question = "when was iit kgp founded?";
  const context = `
The Indian Institute of Technology Kharagpur (IIT Kharagpur or IIT-KGP) is a public institute of technology, research university, and autonomous institute established by the Government of India in Kharagpur, West Bengal. Founded in 1951, the institute is the first of the IITs to be established and is recognised as an Institute of National Importance. In 2019 it was awarded the status of Institute of Eminence by the Government of India.[4]

The institute was initially established to train engineers after India attained independence in 1947. However, over the years, the institute's academic capabilities diversified with offerings in management, law, architecture, humanities, medicine, etc. The institute has an 8.7-square-kilometre (2,100-acre) campus and has about 22,000 residents.
      
Foundation

Nalini Ranjan Sarkar, who recommended the set up of IIT's, along the lines of MIT
In 1946, a committee was set up by Sir Jogendra Singh, Member of the Viceroy's executive council, to consider the creation of higher technical institutions for the industrial development of India post World War II. This was followed by the creation of a 22-member committee headed by Nalini Ranjan Sarkar. In its interim report, the Sarkar Committee recommended the establishment of higher technical institutions in India, along the lines of the Massachusetts Institute of Technology and consulting from the University of Illinois at Urbana Champaign along with affiliated secondary institutions. The report urged that work should start with the speedy establishment of major institutions in the four-quarters of the country with the ones in the east and the west to be set up immediately.[6]


Jawaharlal Nehru laying the foundation stone in 1951
IIT Kharagpur Main Gate
IIT Kharagpur Main Entrance
IIT Kharagpur Main Entrance Gate
IIT Kharagpur Main Entrance Gate (Puri Gate)
On the grounds that West Bengal had the highest concentration of industries at the time, Bidhan Chandra Roy, the Chief Minister of West Bengal, persuaded Jawaharlal Nehru (India's first prime minister) to establish the first institute in West Bengal. The first Indian Institute of Technology was thus established in May 1950 as the Eastern Higher Technical Institute.[7] It was located in Esplanade East, Calcutta, and in September 1950 shifted to its permanent campus at Hijli, Kharagpur 120 kilometres (75 mi) south-west of Kolkata (formerly called Calcutta). Hijli had been used as a detention camp during the period of British rule in India, where Indian independence activists were imprisoned.[8]

IIT Kharagpur is the 4th oldest technical institute in the state after IIEST, Shibpur (established as B.E. College in 1856), Jadavpur University (established as the Bengal Technical Institute in 1906) and Rajabazar Science College (established as Calcutta University campus for Science and Technology in 1914). When the first session started in August 1951, there were 224 students and 42 teachers in the ten departments of the institute. The classrooms, laboratories and the administrative office were housed in the historic building of the Hijli Detention Camp (now known as Shaheed Bhawan), where political revolutionaries were imprisoned during the period of British colonial rule.[9] The office building had served as the headquarters of the Bomber Command of the U.S. 20th Air Force during World War II.


IIT Kharagpur main building
Early developments

The Hijli Detention Camp (photographed in 1951) served as IIT Kharagpur's first academic building
The name "Indian Institute of Technology" was adopted before the formal inauguration of the institute on 18 August 1951 by Maulana Abul Kalam Azad.[10] On 18 May 1956, a Bill (Bill no 36 of 1956)[11] was introduced in Lok Sabha to declare the institution known as the Indian Institute of Technology Kharagpur to be an institution of national importance and to provide for its incorporation and matters connected therewith. The motto of the institute, योगः कर्मसु कौशलम् is taken from the Bhagavad Gita, Chapter 2, Verse 50, and it has been translated by Sri Aurobindo as "Excellence in action is Yoga".[5] On 15 September 1956, the Indian Institute of Technology (Kharagpur) Act, 195 of Parliament received the assent of the President.[12] Prime Minister Nehru, in the first convocation address of IIT Kharagpur, said:[13]

Here in the place of that Hijli Detention Camp stands the fine monument of India, representing India's urges, India's future in the making. This picture seems to me symbolical of the changes that are coming to India.


The main building of the institute during construction (1955)

Nalanda Complex, 2018
The Srinivasa Ramanujan Complex was incorporated as another academic complex of the institute with Takshashila starting operation in 2002, Vikramshila in 2003, and Nalanda in 2012. The erstwhile Hijli Detention camp building, subsequently renamed as the Hijli Saheed Bhavan, hosts the Nehru Museum of Science & Technology and is an imposing building bearing resemblance to the Byzantine style of architecture.[14]

Nehru Museum
      `;

  const flatbufferPayload = createQARequestBuffer(question, context);

  // await benchmarkWithSequential({ payload: flatbufferPayload, fb_id: "QASV" }, 50, 5);
  // await benchmarkBurst({ payload: flatbufferPayload, fb_id: "QASV" }, 50, 5);
  await benchmarkWithConcurrency({ payload: flatbufferPayload, fb_id: "QASV" }, 50, 10, 5);
});

async function benchmarkWithConcurrency(payload, iterations, poolSize, warmup = 10) {
  // Warmup calls (not measured)
  for (let i = 0; i < warmup; i++) {
    await inferSingleBERTAsync(payload);
  }

  let completed = 0;
  let inFlight = 0;
  const latencies = [];

  return new Promise((resolve) => {
    function launchNext() {
      // stop condition: all iterations launched and completed
      if (completed >= iterations && inFlight === 0) {
        // Compute stats
        latencies.sort((a, b) => a - b);
        const avg = latencies.reduce((a, b) => a + b, 0) / latencies.length;
        const median = latencies[Math.floor(latencies.length / 2)];
        const p90 = latencies[Math.floor(latencies.length * 0.9)];
        const p99 = latencies[Math.floor(latencies.length * 0.99)];

        console.log(`Benchmark over ${iterations} iterations (concurrency=${poolSize}):`);
        console.log(`Avg: ${avg.toFixed(2)} ms, Median: ${median.toFixed(2)} ms, P90: ${p90.toFixed(2)} ms, P99: ${p99.toFixed(2)} ms`);

        const result = `Benchmark (N=${iterations}, concurrency=${poolSize}):\n`
          + `Avg: ${avg.toFixed(2)} ms\n`
          + `Median: ${median.toFixed(2)} ms\n`
          + `P90: ${p90.toFixed(2)} ms\n`
          + `P99: ${p99.toFixed(2)} ms`;

        singleBertInferBenchmarkIterationEle.textContent = "Iteration: " + iterations;
        singleBertInferBenchmarkIterationTimeEle.textContent = result;
        alert(result);

        resolve(latencies);
        return;
      }

      if (completed >= iterations) {
        return; // no more work to launch
      }

      inFlight++;
      const start = performance.now();

      inferSingleBERTAsync(payload)
        .then(() => {
          latencies.push(performance.now() - start);
        })
        .finally(() => {
          inFlight--;
          completed++;
          launchNext(); // launch the next request
        });
    }

    // Kick off initial pool
    for (let i = 0; i < poolSize && i < iterations; i++) {
      launchNext();
    }
  });
}

async function benchmarkWithSequential(payload, iterations, warmup = 10) {
  for (let i = 0; i < warmup; i++) {
    await inferSingleBERTAsync(payload);
  }

  // Measurement: run a fixed number of iterations.
  let latencies = [];

  for (let i = 0; i < iterations; i++) {
    const startTime = performance.now();
    await inferSingleBERTAsync(payload);
    const endTime = performance.now();
    latencies.push(endTime - startTime);
  }

  // Compute statistics.
  latencies.sort((a, b) => a - b);
  const sum = latencies.reduce((acc, cur) => acc + cur, 0);
  const avg = sum / latencies.length;
  const median = latencies[Math.floor(latencies.length / 2)];
  const p90 = latencies[Math.floor(latencies.length * 0.9)];

  console.log("Benchmark results HTTP:");
  console.log(`Average latency: ${avg.toFixed(2)} ms`);
  console.log(`Median latency: ${median.toFixed(2)} ms`);
  console.log(`90th percentile latency: ${p90.toFixed(2)} ms`);
  const result = `Benchmark over ${iterations} iterations:\nAverage: ${avg.toFixed(2)} ms\nMedian: ${median.toFixed(2)} ms\n90th Percentile: ${p90.toFixed(2)} ms`
  alert(result);
}

async function benchmarkBurst(payload, iterations, warmup = 10) {
  // Warmup (not measured)
  for (let i = 0; i < warmup; i++) {
    await inferSingleBERTAsync(payload);
  }

  // Launch all requests in parallel
  const startTimes = new Array(iterations);
  const promises = [];

  for (let i = 0; i < iterations; i++) {
    startTimes[i] = performance.now();
    promises.push(
      inferSingleBERTAsync(payload)
        .then(() => performance.now() - startTimes[i])
    );
  }

  const latencies = await Promise.all(promises);

  // Stats
  latencies.sort((a, b) => a - b);
  const avg = latencies.reduce((a, b) => a + b, 0) / latencies.length;
  const median = latencies[Math.floor(latencies.length / 2)];
  const p90 = latencies[Math.floor(latencies.length * 0.9)];
  const p99 = latencies[Math.floor(latencies.length * 0.99)];

  console.log(`Burst benchmark over ${iterations} parallel requests:`);
  console.log(`Avg: ${avg.toFixed(2)} ms, Median: ${median.toFixed(2)} ms, P90: ${p90.toFixed(2)} ms, P99: ${p99.toFixed(2)} ms`);

  const result = `Burst Benchmark (N=${iterations}):\n`
               + `Avg: ${avg.toFixed(2)} ms\n`
               + `Median: ${median.toFixed(2)} ms\n`
               + `P90: ${p90.toFixed(2)} ms\n`
               + `P99: ${p99.toFixed(2)} ms`;

  alert(result);

  return latencies;
}