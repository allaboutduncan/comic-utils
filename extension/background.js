// Cross-browser compatibility
const api = typeof chrome !== "undefined" ? chrome : browser;

api.runtime.onInstalled.addListener(() => {
  // Use removeAll before create to avoid "duplicate ID" errors on reload
  api.contextMenus.removeAll(() => {
    api.contextMenus.create({
      id: "sendToAPI",
      title: "Send to CLU",
      contexts: ["link"]
    });
  });
});

// Helper function to send link to API
function sendLinkToApi(linkUrl, sendResponse) {
  api.storage.local.get({ apiUrl: "", customHeaders: "" }, (data) => {
    const apiUrl = data.apiUrl.trim();
    if (!apiUrl) {
      console.error("CLU URL is not set.");
      if (sendResponse) sendResponse({ success: false, error: "API URL not set" });
      return;
    }

    let headers = { "Content-Type": "application/json" };

    if (data.customHeaders && data.customHeaders.trim()) {
      try {
        const userHeaders = JSON.parse(data.customHeaders);
        headers = { ...headers, ...userHeaders };
      } catch (e) {
        console.error("Invalid customHeaders JSON:", e);
      }
    }

    fetch(apiUrl, {
      method: "POST",
      headers: headers,
      body: JSON.stringify({ link: linkUrl })
    })
      .then(response => {
        if (!response.ok) throw new Error("Status: " + response.status);
        if (sendResponse) sendResponse({ success: true });
      })
      .catch(error => {
        console.error("Error sending link:", error);
        if (sendResponse) sendResponse({ success: false, error: error.message });
      });
  });
}

// Context Menu Listener
api.contextMenus.onClicked.addListener((info) => {
  if (info.menuItemId === "sendToAPI" && info.linkUrl) {
    sendLinkToApi(info.linkUrl);
  }
});

// Message Listener
api.runtime.onMessage.addListener((request, sender, sendResponse) => {
  if (request.action === "sendLink" && request.linkUrl) {
    sendLinkToApi(request.linkUrl, sendResponse);
    return true; // Keeps the message channel open for the async fetch
  }
});