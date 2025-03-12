chrome.runtime.onInstalled.addListener(() => {
  chrome.contextMenus.create({
    id: "sendToAPI",
    title: "Send to CLU",
    contexts: ["link"]
  });
});

chrome.contextMenus.onClicked.addListener((info, tab) => {
  if (info.menuItemId === "sendToAPI" && info.linkUrl) {
    // Retrieve both apiUrl and customHeaders from storage
    chrome.storage.sync.get({ apiUrl: "", customHeaders: "" }, function(data) {
      const apiUrl = data.apiUrl.trim();
      if (!apiUrl) {
        console.error("CLU URL is not set. Please set it in the options page.");
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
        body: JSON.stringify({ link: info.linkUrl })
      })
      .then(response => {
        if (!response.ok) {
          throw new Error("Network response was not ok. Status: " + response.status);
        }
        console.log("Link sent successfully");
      })
      .catch(error => {
        console.error("Error sending link:", error);
      });
    });
  }
});
