chrome.runtime.onInstalled.addListener(() => {
  chrome.contextMenus.create({
    id: "sendToAPI",
    title: "Send to CLU",
    contexts: ["link"]
  });
});

// Helper function to send link to API
function sendLinkToApi(linkUrl, sendResponse) {
  // Retrieve both apiUrl and customHeaders from storage
  chrome.storage.sync.get({ apiUrl: "", customHeaders: "" }, function (data) {
    const apiUrl = data.apiUrl.trim();
    if (!apiUrl) {
      console.error("CLU URL is not set. Please set it in the options page.");
      if (sendResponse) sendResponse({ success: false, error: "API URL not set" });
      return;
    }

    // Build the default headers object
    let headers = { "Content-Type": "application/json" };

    // If customHeaders are provided, try to parse and merge them
    if (data.customHeaders && data.customHeaders.trim()) {
      try {
        const userHeaders = JSON.parse(data.customHeaders);
        headers = { ...headers, ...userHeaders };
      } catch (e) {
        console.error("Invalid customHeaders JSON:", e);
        // You could choose to return here or simply ignore the custom headers
      }
    }

    fetch(apiUrl, {
      method: "POST",
      headers: headers,
      body: JSON.stringify({ link: linkUrl })
    })
      .then(response => {
        if (!response.ok) {
          throw new Error("Network response was not ok. Status: " + response.status);
        }
        console.log("Link sent successfully");
        if (sendResponse) sendResponse({ success: true });
      })
      .catch(error => {
        console.error("Error sending link:", error);
        if (sendResponse) sendResponse({ success: false, error: error.message });
      });
  });
}

chrome.contextMenus.onClicked.addListener((info, tab) => {
  if (info.menuItemId === "sendToAPI" && info.linkUrl) {
    sendLinkToApi(info.linkUrl);
  }
});

// Listener for messages from content script
chrome.runtime.onMessage.addListener((request, sender, sendResponse) => {
  if (request.action === "sendLink" && request.linkUrl) {
    sendLinkToApi(request.linkUrl, sendResponse);
    return true; // Will respond asynchronously
  }
});
