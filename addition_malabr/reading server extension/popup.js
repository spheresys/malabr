document.addEventListener('DOMContentLoaded', function () {
  const loadModelBERTButton = document.getElementById('loadModelBERT');
  const inferSingleButton = document.getElementById('inferSingleButton');
  const inferBatchButton = document.getElementById('inferBatchButton');
  const benchmarkInferenceButton = document.getElementById('benchmarkInferenceButton');
  const trainModelButton = document.getElementById('trainModel');
  const benchmarkInferenceResultEle = document.getElementById('benchmarkInferenceResult');

  // Inputs for single inference.
  const questionInput = document.getElementById('questionInput');
  const contextInput = document.getElementById('contextInput');

  // Input for batch inference (expects a JSON array).
  const batchInput = document.getElementById('batchInput');

  // Load MobileBERT Model.
  if (loadModelBERTButton) {
    loadModelBERTButton.addEventListener('click', () => {
      chrome.readServer.loadModelBERT((response) => {
        try {
          const parsedResponse = JSON.parse(response);
          if (parsedResponse.status) {
            console.log('Model loaded:', parsedResponse.status);
            alert('Model loaded: ' + parsedResponse.status);
          } else {
            console.error('Error:', parsedResponse.error);
            alert('Error: ' + parsedResponse.error);
          }
        } catch (e) {
          console.error('Failed to parse load model response:', e);
          // alert('Error: Failed to parse load model response');
        }
      });
    });
  } else {
    console.error("Button with ID 'loadModelBERT' not found in DOM.");
  }

  // Single Inference.
  if (inferSingleButton && questionInput && contextInput) {
    inferSingleButton.addEventListener('click', () => {
      const question = questionInput.value.trim();
      const context = contextInput.value.trim();
      if (!question || !context) {
        alert('Please provide both a question and a context.');
        return;
      }
      const payload = { question: question, context: context };
      const jsonPayload = JSON.stringify(payload);
      console.log("Single inference payload:", jsonPayload);
      chrome.readServer.inferSingleBERT(jsonPayload, (response) => {
        try {
          const parsedResponse = JSON.parse(response);
          if (parsedResponse.answer) {
            console.log('Single inference result:', parsedResponse.answer);
            alert('Answer: ' + parsedResponse.answer);
          } else {
            console.error('Error:', parsedResponse.error);
            alert('Error: ' + parsedResponse.error);
          }
        } catch (e) {
          console.error('Failed to parse single inference response:', e);
          alert('Error: Failed to parse inference response');
        }
      });
    });
  } else {
    console.error("Single inference elements not found in DOM.");
  }

  // Batch Inference.
  if (inferBatchButton && batchInput) {
    inferBatchButton.addEventListener('click', () => {
      let batchData;
      try {
        batchData = JSON.parse(batchInput.value.trim());
      } catch (e) {
        alert('Invalid JSON for batch input. Please provide a valid JSON array.');
        return;
      }
      const jsonPayload = JSON.stringify(batchData);
      console.log("Batch inference payload:", jsonPayload);
      chrome.readServer.inferBatchBERT(jsonPayload, (response) => {
        try {
          const parsedResponse = JSON.parse(response);
          console.log('Batch inference result:', parsedResponse);
          alert('Batch Inference Result: ' + JSON.stringify(parsedResponse));
        } catch (e) {
          console.error('Failed to parse batch inference response:', e);
          alert('Error: Failed to parse batch inference response');
        }
      });
    });
  } else {
    console.error("Batch inference elements not found in DOM.");
  }

  function inferSingleBERTAsync(args) {
    return new Promise((resolve, reject) => {
      try {
        chrome.readServer.inferSingleBERT(args, (result) => {
          resolve(result);
        });
      } catch (err) {
        reject(err);
      }
    });
  }


  // Benchmark Inference.
  if (benchmarkInferenceButton) {
    benchmarkInferenceButton.addEventListener('click', async () => {
      const NO_OF_INTERATION = 1000
      const NO_OF_WARMUP_INTERATION = 100

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

      const payload = JSON.stringify({ question, context });
      // await benchmarkWithSequential(payload, 50, 5);
      // await benchmarkBurst(payload, 50, 5);
      await benchmarkWithConcurrency(payload, 50, 10, 2);

    });
  } else {
    console.error("Button with ID 'benchmarkInferenceButton' not found in DOM.");
  }

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

  // Training with client-side timing
  if (trainModelButton) {
    trainModelButton.addEventListener('click', () => {
      const startTime = performance.now(); // Start timing here
      chrome.readServer.trainModel((response) => {
        const endTime = performance.now(); // End timing when response is received
        const clientTime = endTime - startTime;

        try {
          const parsedResponse = JSON.parse(response);
          if (parsedResponse.training_time_ms && parsedResponse.accuracy) {
            console.log('Training result:', parsedResponse);
            // Display both client-side and backend timing
            alert(`Training completed:\nClient-side Time: ${clientTime.toFixed(2)} ms\nBackend Time: ${parsedResponse.training_time_ms.toFixed(2)} ms\nAccuracy: ${(parsedResponse.accuracy * 100).toFixed(2)}%`);
          } else {
            console.error('Error:', parsedResponse.error);
            alert('Error: ' + parsedResponse.error);
          }
        } catch (e) {
          console.error('Failed to parse training response:', e);
          alert('Error: Failed to parse training response');
        }
      });
    });
  } else {
    console.error("Button with ID 'trainModel' not found in DOM.");
  }
});

