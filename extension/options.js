document.addEventListener('DOMContentLoaded', function() {
  chrome.storage.sync.get({ apiUrl: "" }, function(data) {
    document.getElementById('apiUrl').value = data.apiUrl;
  });
  
  document.getElementById('save').addEventListener('click', function() {
    const apiUrl = document.getElementById('apiUrl').value;
    chrome.storage.sync.set({ apiUrl: apiUrl }, function() {
      alert('CLU URL saved.');
    });
  });
});
