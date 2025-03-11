chrome.runtime.onInstalled.addListener(() => {
  // Create context menu only for link elements.
  chrome.contextMenus.create({
    id: "sendToCLU",
    title: "Send to CLU",
    contexts: ["link"]
  });
});

chrome.contextMenus.onClicked.addListener((info, tab) => {
  if (info.menuItemId === "sendToCLU" && info.linkUrl) {
    // Retrieve the user-defined target URL from storage.
    chrome.storage.sync.get({ targetUrl: "" }, function(data) {
      const targetUrl = data.targetUrl;
      if (!targetUrl) {
        console.error("Target URL is not set. Please set it in the options page.");
        return;
      }
      // Send the link URL to the target URL.
      fetch(targetUrl, {
        method: "POST",
        headers: {
          "Content-Type": "application/json"
        },
        body: JSON.stringify({ link: info.linkUrl })
      })
      .then(response => {
        if (!response.ok) {
          throw new Error("Network response was not ok");
        }
        console.log("Link sent successfully");
      })
      .catch(error => {
        console.error("Error sending link:", error);
      });
    });
  }
});
