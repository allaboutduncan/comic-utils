// Helper to create the icon element
function createCluIcon(linkUrl) {
    const img = document.createElement('img');
    img.src = chrome.runtime.getURL('icons/icon48.png');
    img.style.cursor = 'pointer';
    img.style.marginLeft = '5px';
    img.style.verticalAlign = 'middle';
    img.title = 'Send to CLU';

    img.addEventListener('click', (e) => {
        e.preventDefault();
        e.stopPropagation();

        // Send message to background script
        chrome.runtime.sendMessage({
            action: "sendLink",
            linkUrl: linkUrl
        }, (response) => {
            if (response && response.success) {
                // Visual feedback (optional)
                img.style.opacity = '0.5';
                setTimeout(() => img.style.opacity = '1', 1000);
            } else {
                console.error("Failed to send link to CLU", response);
                alert("Failed to send link to CLU. Check console for details.");
            }
        });
    });

    return img;
}

// Function to process ComicBookPlus links
function processComicBookPlus() {
    const links = document.querySelectorAll('a[href*="/dload/"]');
    links.forEach(link => {
        // Avoid double injection
        if (link.nextElementSibling && link.nextElementSibling.src && link.nextElementSibling.src.includes('icons/icon48.png')) {
            return;
        }
        const icon = createCluIcon(link.href);
        link.parentNode.insertBefore(icon, link.nextSibling);
    });
}

// Function to process GetComics links
function processGetComics() {
    // Selector for GetComics: matches link with expected title or text, or perhaps specific structure
    // The user specified: title="PIXELDRAIN" or title="DOWNLOAD NOW" and href containing /dlds/
    const links = document.querySelectorAll('a[href*="/dlds/"]');

    links.forEach(link => {
        const title = (link.getAttribute('title') || "").toUpperCase();

        if (title.includes('PIXELDRAIN') || title.includes('DOWNLOAD NOW')) {
            // Avoid double injection
            if (link.nextElementSibling && link.nextElementSibling.src && link.nextElementSibling.src.includes('icons/icon48.png')) {
                return;
            }
            const icon = createCluIcon(link.href);
            link.parentNode.insertBefore(icon, link.nextSibling);
        }
    });
}

// Main execution
const hostname = window.location.hostname;

if (hostname.includes('comicbookplus.com')) {
    processComicBookPlus();
} else if (hostname.includes('getcomics.org')) {
    processGetComics();
}
