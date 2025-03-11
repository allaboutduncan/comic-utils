document.addEventListener('DOMContentLoaded', function() {
  // Load the saved target URL.
  chrome.storage.sync.get({ targetUrl: "" }, function(data) {
    document.getElementById('targetUrl').value = data.targetUrl;
  });
  
  // Save the target URL when the button is clicked.
  document.getElementById('save').addEventListener('click', function() {
    const targetUrl = document.getElementById('targetUrl').value;
    chrome.storage.sync.set({ targetUrl: targetUrl }, function() {
      alert('Target URL saved.');
    });
  });
});
