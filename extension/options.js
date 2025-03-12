document.addEventListener('DOMContentLoaded', function() {
  const apiUrlField = document.getElementById('apiUrl');
  const headersField = document.getElementById('headers');
  const saveButton = document.getElementById('save');
  const status = document.getElementById('status');
  const savedMessage = document.getElementById('saved');

  // Load previously saved options from storage
  chrome.storage.sync.get({ apiUrl: '', customHeaders: '' }, (data) => {
    apiUrlField.value = data.apiUrl;
    headersField.value = data.customHeaders;
  });

  // Save the updated options with JSON validation for customHeaders
  saveButton.addEventListener('click', function() {
    const newApiUrl = apiUrlField.value.trim();
    const newHeaders = headersField.value.trim();

    // Validate customHeaders if provided.
    if (newHeaders) {
      try {
        JSON.parse(newHeaders);
      } catch (e) {
        status.textContent = 'Error: Custom Headers must be valid JSON.';
        status.style.color = 'red';
        return;
      }
    }

    // If validation passes, save the options.
    chrome.storage.sync.set(
      {
        apiUrl: newApiUrl,
        customHeaders: newHeaders
      },
      () => {
        status.textContent = '';
        savedMessage.textContent = 'Settings saved!';
        savedMessage.classList.remove('hidden');
        setTimeout(() => {
          savedMessage.classList.add('hidden');
        }, 2000);
      }
    );
  });
});
