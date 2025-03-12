chrome.runtime.onInstalled.addListener(() => {
  chrome.contextMenus.create({
    id: "sendToAPI",
    title: "Send to CLU",
    contexts: ["link"]
  });
});

chrome.contextMenus.onClicked.addListener((info, tab) => {
  if (info.menuItemId === "sendToAPI" && info.linkUrl) {
    chrome.storage.sync.get({ apiUrl: "" }, function(data) {
      const apiUrl = data.apiUrl;
      if (!apiUrl) {
        console.error("CLU URL is not set. Please set it in the options page.");
        return;
      }
      fetch(apiUrl, {
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
