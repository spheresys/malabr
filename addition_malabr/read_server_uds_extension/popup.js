document.getElementById('readDataBtn').addEventListener('click', () => {
  chrome.readServerUds.readData((response) => {
    // Step 1: Handle native-side errors
    if (chrome.runtime.lastError) {
      console.error('Native Error:', chrome.runtime.lastError.message);
      alert('Native Error: ' + chrome.runtime.lastError.message);
      return;
    }

    // Step 2: Try to parse the JSON response
    let parsedResponse;
    try {
      parsedResponse = JSON.parse(response);
    } catch (e) {
      console.error('Failed to parse JSON:', e);
      alert('Error: Invalid JSON response from native code.');
      return;
    }

    // Step 3: Optional domain-specific error check
    if (parsedResponse.status === false) {
      console.error('Server-side error:', parsedResponse);
      alert('Server Error: ' + (parsedResponse.message || 'Unknown error'));
      return;
    }

    // Step 4: All good — handle success
    console.log('Server Response:', parsedResponse);
    alert('Server Response: ' + JSON.stringify(parsedResponse));
  });
});
