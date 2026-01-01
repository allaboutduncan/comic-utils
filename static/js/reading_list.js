console.log('reading_list.js loaded');

// Toast notification system
let currentProgressToast = null;

function getToastContainer() {
    let toastContainer = document.getElementById('toast-container');
    if (!toastContainer) {
        toastContainer = document.createElement('div');
        toastContainer.id = 'toast-container';
        toastContainer.className = 'toast-container position-fixed end-0 p-4';
        toastContainer.style.zIndex = '1100';
        toastContainer.style.top = '60px'; // Below navbar
        document.body.appendChild(toastContainer);
    }
    return toastContainer;
}

function showToast(message, type = 'info', duration = 5000) {
    console.log(`[Toast] ${type}: ${message}`);

    const toastContainer = getToastContainer();
    const toastId = 'toast-' + Date.now();
    const bgClass = type === 'success' ? 'bg-success' : type === 'error' ? 'bg-danger' : 'bg-primary';

    const toastHtml = `
        <div id="${toastId}" class="toast align-items-center text-white ${bgClass} border-0 show" role="alert">
            <div class="d-flex">
                <div class="toast-body">${message}</div>
                <button type="button" class="btn-close btn-close-white me-2 m-auto" data-bs-dismiss="toast"></button>
            </div>
        </div>
    `;

    toastContainer.insertAdjacentHTML('beforeend', toastHtml);
    const toastEl = document.getElementById(toastId);

    // Auto-hide after duration
    setTimeout(() => {
        if (toastEl && toastEl.parentNode) {
            toastEl.classList.remove('show');
            setTimeout(() => toastEl.remove(), 300);
        }
    }, duration);

    return toastEl;
}

function showProgressToast(message) {
    console.log(`[Progress] ${message}`);

    const toastContainer = getToastContainer();

    // Update existing progress toast or create new one
    if (currentProgressToast && currentProgressToast.parentNode) {
        const msgEl = currentProgressToast.querySelector('.progress-message');
        if (msgEl) {
            msgEl.textContent = message;
            console.log(`[Progress] Updated toast to: ${message}`);
        }
    } else {
        const toastHtml = `
            <div id="progress-toast" class="toast align-items-center text-white bg-primary border-0 show" role="alert">
                <div class="d-flex">
                    <div class="toast-body d-flex align-items-center">
                        <span class="spinner-border spinner-border-sm me-2 flex-shrink-0" role="status"></span>
                        <span class="progress-message">${message}</span>
                    </div>
                </div>
            </div>
        `;
        toastContainer.insertAdjacentHTML('beforeend', toastHtml);
        currentProgressToast = document.getElementById('progress-toast');
        console.log(`[Progress] Created new toast: ${message}`);
    }
}

function hideProgressToast() {
    if (currentProgressToast && currentProgressToast.parentNode) {
        currentProgressToast.remove();
        currentProgressToast = null;
    }
}

// Poll for import task completion
function pollImportStatus(taskId, filename) {
    console.log(`[Poll] Starting to poll for task: ${taskId}`);
    const pollInterval = 500; // Check every 500ms for more responsive updates

    function checkStatus() {
        fetch(`/api/reading-lists/import-status/${taskId}`)
            .then(response => response.json())
            .then(data => {
                console.log(`[Poll] Status: ${data.status}, processed: ${data.processed}/${data.total}, message: ${data.message}`);

                if (!data.success) {
                    hideProgressToast();
                    showToast('Import task not found', 'error');
                    return;
                }

                if (data.status === 'complete') {
                    hideProgressToast();
                    showToast(`Imported "${data.list_name}" (${data.processed} issues)`, 'success', 8000);
                    // Reload page to show the new list
                    setTimeout(() => window.location.reload(), 2000);
                } else if (data.status === 'error') {
                    hideProgressToast();
                    showToast(`Import failed: ${data.message}`, 'error', 10000);
                } else {
                    // Still processing, update progress toast
                    if (data.total > 0) {
                        showProgressToast(`Importing "${filename}"... ${data.processed}/${data.total} issues`);
                    } else {
                        showProgressToast(`Importing "${filename}"...`);
                    }
                    setTimeout(checkStatus, pollInterval);
                }
            })
            .catch(error => {
                console.error('Error checking import status:', error);
                setTimeout(checkStatus, pollInterval * 2); // Retry with longer delay
            });
    }

    // Show initial progress toast
    showProgressToast(`Starting import of "${filename}"...`);
    checkStatus();
}

function extractListNameFromFilename(filename) {
    // Remove .cbl extension
    let name = filename.replace(/\.cbl$/i, '');
    // Extract just the list name - remove [Publisher] and (date) prefix
    // Pattern: [Publisher] (YYYY-MM) List Name
    const match = name.match(/\]\s*\([^)]+\)\s*(.+)$/);
    if (match) {
        return match[1].trim();
    }
    return name;
}

function uploadCBL() {
    console.log('uploadCBL called');
    const fileInput = document.getElementById('cblFile');
    const file = fileInput.files[0];
    if (!file) {
        alert('Please select a file');
        return;
    }

    // Show loading state
    const btn = document.getElementById('uploadBtn');
    const cancelBtn = document.getElementById('uploadCancelBtn');
    btn.disabled = true;
    cancelBtn.disabled = true;
    btn.querySelector('.btn-text').classList.add('d-none');
    btn.querySelector('.btn-loading').classList.remove('d-none');

    // Extract clean list name from filename
    const listName = extractListNameFromFilename(file.name);

    const formData = new FormData();
    formData.append('file', file);

    fetch('/api/reading-lists/upload', {
        method: 'POST',
        body: formData
    })
        .then(response => response.json())
        .then(data => {
            console.log('Upload response:', data);
            if (data.success) {
                if (data.background && data.task_id) {
                    // Close modal and start polling
                    const modal = bootstrap.Modal.getInstance(document.getElementById('uploadCBLModal'));
                    if (modal) modal.hide();
                    pollImportStatus(data.task_id, listName);
                } else {
                    window.location.reload();
                }
            } else {
                alert('Error: ' + data.message);
            }
        })
        .catch(error => {
            console.error('Error:', error);
            alert('An error occurred during upload');
        })
        .finally(() => {
            // Reset loading state
            btn.disabled = false;
            cancelBtn.disabled = false;
            btn.querySelector('.btn-text').classList.remove('d-none');
            btn.querySelector('.btn-loading').classList.add('d-none');
        });
}

function extractListName(url) {
    // Extract and decode the filename from URL
    let filename = url.split('/').pop() || 'reading list';
    try {
        filename = decodeURIComponent(filename);
    } catch (e) {
        // If decoding fails, use as-is
    }
    // Remove .cbl extension
    filename = filename.replace(/\.cbl$/i, '');
    // Extract just the list name - remove [Publisher] and (date) prefix
    // Pattern: [Publisher] (YYYY-MM) List Name
    const match = filename.match(/\]\s*\([^)]+\)\s*(.+)$/);
    if (match) {
        return match[1].trim();
    }
    return filename;
}

function importGithub() {
    console.log('importGithub called');
    const urlInput = document.getElementById('githubUrl');
    const url = urlInput.value;
    if (!url) {
        alert('Please enter a URL');
        return;
    }

    // Show loading state
    const btn = document.getElementById('importBtn');
    const cancelBtn = document.getElementById('importCancelBtn');
    btn.disabled = true;
    cancelBtn.disabled = true;
    btn.querySelector('.btn-text').classList.add('d-none');
    btn.querySelector('.btn-loading').classList.remove('d-none');

    // Extract clean list name from URL for display
    const filename = extractListName(url);

    fetch('/api/reading-lists/import', {
        method: 'POST',
        headers: {
            'Content-Type': 'application/json'
        },
        body: JSON.stringify({ url: url })
    })
        .then(response => response.json())
        .then(data => {
            console.log('Import response:', data);
            if (data.success) {
                if (data.background && data.task_id) {
                    // Close modal and start polling
                    const modal = bootstrap.Modal.getInstance(document.getElementById('importGithubModal'));
                    if (modal) modal.hide();
                    pollImportStatus(data.task_id, filename);
                } else {
                    window.location.reload();
                }
            } else {
                alert('Error: ' + data.message);
            }
        })
        .catch(error => {
            console.error('Error:', error);
            alert('An error occurred during import');
        })
        .finally(() => {
            // Reset loading state
            btn.disabled = false;
            cancelBtn.disabled = false;
            btn.querySelector('.btn-text').classList.remove('d-none');
            btn.querySelector('.btn-loading').classList.add('d-none');
        });
}

function deleteReadingList(id) {
    if (!confirm('Are you sure you want to delete this reading list?')) {
        return;
    }

    fetch(`/api/reading-lists/${id}`, {
        method: 'DELETE'
    })
        .then(response => response.json())
        .then(data => {
            if (data.success) {
                window.location.reload();
            } else {
                alert('Error: ' + data.message);
            }
        })
        .catch(error => {
            console.error('Error:', error);
            alert('An error occurred');
        });
}

// Mapping Logic
let currentEntryId = null;
let selectedFilePath = null;
let mapModal = null;

function formatSearchTerm(series, number, volume, year) {
    // Use RENAME_PATTERN if defined, otherwise default format
    let pattern = (typeof RENAME_PATTERN !== 'undefined' && RENAME_PATTERN)
        ? RENAME_PATTERN
        : '{series_name} {issue_number}';

    // Replace ':' with ' -' in series name (e.g., "Batman: The Dark Knight" -> "Batman - The Dark Knight")
    let cleanSeries = (series || '').replace(/:/g, ' -');

    // Pad issue number to 3 digits
    const paddedNumber = number.toString().padStart(3, '0');

    // Replace placeholders
    let searchTerm = pattern
        .replace('{series_name}', cleanSeries)
        .replace('{series}', cleanSeries)
        .replace('{issue_number}', paddedNumber)
        .replace('{issue}', paddedNumber)
        .replace('{volume}', volume || '')
        .replace('{year}', year || '')
        .replace('{start_year}', volume || year || '');

    // Clean up any remaining empty placeholders and extra spaces
    searchTerm = searchTerm.replace(/\{[^}]+\}/g, '').replace(/\s+/g, ' ').trim();

    // Remove empty parentheses that might result from missing values
    searchTerm = searchTerm.replace(/\(\s*\)/g, '').trim();

    return searchTerm;
}

function openMapModal(entryId, series, number, volume, year) {
    currentEntryId = entryId;
    selectedFilePath = null;
    document.getElementById('mapTargetName').textContent = `${series} #${number}`;

    // Format search term using rename pattern
    const searchTerm = formatSearchTerm(series, number, volume, year);
    document.getElementById('fileSearchInput').value = searchTerm;

    document.getElementById('searchResults').innerHTML = '';
    document.getElementById('confirmMapBtn').disabled = true;

    if (!mapModal) {
        mapModal = new bootstrap.Modal(document.getElementById('mapFileModal'));
    }
    mapModal.show();

    // Auto search
    searchFiles();
}

function searchFiles(retryWithoutFirstWord = false) {
    let query = document.getElementById('fileSearchInput').value;
    if (!query) return;

    // If retrying, remove the first word (e.g., "The Flash 094" -> "Flash 094")
    if (retryWithoutFirstWord) {
        const words = query.split(' ');
        if (words.length > 1) {
            query = words.slice(1).join(' ');
            console.log(`[Search] Retrying without first word: "${query}"`);
        } else {
            // Only one word, can't retry
            return;
        }
    }

    const resultsDiv = document.getElementById('searchResults');
    resultsDiv.innerHTML = '<div class="text-center p-3"><div class="spinner-border text-primary" role="status"></div></div>';

    fetch(`/api/reading-lists/search-file?q=${encodeURIComponent(query)}`)
        .then(response => response.json())
        .then(results => {
            resultsDiv.innerHTML = '';
            if (results.length === 0) {
                // If no results and haven't tried without first word yet, retry
                if (!retryWithoutFirstWord) {
                    const words = document.getElementById('fileSearchInput').value.split(' ');
                    if (words.length > 1) {
                        console.log('[Search] No results, trying without first word...');
                        searchFiles(true);
                        return;
                    }
                }
                resultsDiv.innerHTML = '<div class="p-3 text-center text-muted">No files found</div>';
                return;
            }

            results.forEach(file => {
                const item = document.createElement('div');
                item.className = 'list-group-item list-group-item-action search-result-item';
                item.innerHTML = `
                <div class="d-flex w-100 justify-content-between">
                    <h6 class="mb-1 text-truncate">${file.name}</h6>
                    <small class="text-muted">${file.path.split('/').slice(-2, -1)[0]}</small>
                </div>
                <small class="text-muted text-break">${file.path}</small>
            `;
                item.onclick = () => selectFile(file.path, item);
                resultsDiv.appendChild(item);
            });
        })
        .catch(error => {
            console.error('Error:', error);
            resultsDiv.innerHTML = '<div class="text-danger p-3">Error searching files</div>';
        });

    // Add enter key listener
    const input = document.getElementById('fileSearchInput');
    input.onkeypress = function (e) {
        if (e.keyCode === 13) {
            searchFiles();
        }
    };
}

function selectFile(path, element) {
    selectedFilePath = path;

    // UI update
    document.querySelectorAll('.search-result-item').forEach(el => el.classList.remove('active'));
    element.classList.add('active');

    document.getElementById('confirmMapBtn').disabled = false;
}

function confirmMapping() {
    if (!currentEntryId || !selectedFilePath) return;

    fetch(`/api/reading-lists/${LIST_ID}/map`, {
        method: 'POST',
        headers: {
            'Content-Type': 'application/json'
        },
        body: JSON.stringify({
            entry_id: currentEntryId,
            file_path: selectedFilePath
        })
    })
        .then(response => response.json())
        .then(data => {
            if (data.success) {
                location.reload();
            } else {
                alert('Error: ' + data.message);
            }
        })
        .catch(error => {
            console.error('Error:', error);
            alert('An error occurred');
        });
}

function clearMapping() {
    if (!confirm('Are you sure you want to clear the mapping for this issue?')) return;

    selectedFilePath = null; // Send null to clear

    fetch(`/api/reading-lists/${LIST_ID}/map`, {
        method: 'POST',
        headers: {
            'Content-Type': 'application/json'
        },
        body: JSON.stringify({
            entry_id: currentEntryId,
            file_path: null
        })
    })
        .then(response => response.json())
        .then(data => {
            if (data.success) {
                location.reload();
            } else {
                alert('Error: ' + data.message);
            }
        })
        .catch(error => {
            console.error('Error:', error);
            alert('An error occurred');
        });
}

// ==========================================
// Comic Reader Functions
// ==========================================
let currentComicPath = null;
let currentComicPageCount = 0;
let comicReaderSwiper = null;

function openComicReader(filePath) {
    currentComicPath = filePath;

    const modal = document.getElementById('comicReaderModal');
    const titleEl = document.getElementById('comicReaderTitle');
    const pageInfoEl = document.getElementById('comicReaderPageInfo');

    modal.style.display = 'flex';
    document.body.style.overflow = 'hidden';

    const fileName = filePath.split(/[/\\]/).pop();
    titleEl.textContent = fileName;
    pageInfoEl.textContent = 'Loading...';

    // Encode path for URL - handle both forward and back slashes
    const encodedPath = filePath.replace(/\\/g, '/').split('/').map(encodeURIComponent).join('/');

    fetch(`/api/read/${encodedPath}/info`)
        .then(r => r.json())
        .then(data => {
            if (data.success) {
                initializeComicReader(data.page_count, 0);
            } else {
                alert('Failed to load comic: ' + (data.error || 'Unknown error'));
                closeComicReader();
            }
        })
        .catch(error => {
            console.error('Error loading comic:', error);
            alert('An error occurred while loading the comic.');
            closeComicReader();
        });
}

function closeComicReader() {
    const modal = document.getElementById('comicReaderModal');
    modal.style.display = 'none';
    document.body.style.overflow = '';

    if (comicReaderSwiper) {
        comicReaderSwiper.destroy(true, true);
        comicReaderSwiper = null;
    }
}

function initializeComicReader(pageCount, startPage) {
    currentComicPageCount = pageCount;
    const wrapper = document.getElementById('comicReaderWrapper');
    const pageInfoEl = document.getElementById('comicReaderPageInfo');

    wrapper.innerHTML = '';

    const encodedPath = currentComicPath.replace(/\\/g, '/').split('/').map(encodeURIComponent).join('/');

    for (let i = 0; i < pageCount; i++) {
        const slide = document.createElement('div');
        slide.className = 'swiper-slide';
        slide.innerHTML = `<img src="/api/read/${encodedPath}/page/${i}" alt="Page ${i + 1}" loading="lazy">`;
        wrapper.appendChild(slide);
    }

    comicReaderSwiper = new Swiper('#comicReaderSwiper', {
        slidesPerView: 1,
        spaceBetween: 0,
        keyboard: { enabled: true },
        navigation: {
            nextEl: '.swiper-button-next',
            prevEl: '.swiper-button-prev',
        },
        pagination: {
            el: '.swiper-pagination',
            type: 'progressbar',
        },
        on: {
            slideChange: function () {
                const currentPage = this.activeIndex + 1;
                pageInfoEl.textContent = `Page ${currentPage} of ${pageCount}`;
                // Update progress bar
                const progressFill = document.querySelector('.comic-reader-progress-fill');
                const progressText = document.querySelector('.comic-reader-progress-text');
                if (progressFill && progressText) {
                    const percent = Math.round((currentPage / pageCount) * 100);
                    progressFill.style.width = percent + '%';
                    progressText.textContent = percent + '%';
                }
            }
        }
    });

    pageInfoEl.textContent = `Page 1 of ${pageCount}`;
    // Initialize progress
    const progressFill = document.querySelector('.comic-reader-progress-fill');
    const progressText = document.querySelector('.comic-reader-progress-text');
    if (progressFill && progressText) {
        const percent = Math.round((1 / pageCount) * 100);
        progressFill.style.width = percent + '%';
        progressText.textContent = percent + '%';
    }
}

// Set up reader event listeners when DOM is ready
document.addEventListener('DOMContentLoaded', function () {
    const closeBtn = document.getElementById('comicReaderClose');
    if (closeBtn) {
        closeBtn.addEventListener('click', closeComicReader);
    }

    const overlay = document.querySelector('.comic-reader-overlay');
    if (overlay) {
        overlay.addEventListener('click', closeComicReader);
    }

    // Escape key to close reader
    document.addEventListener('keydown', function (e) {
        const modal = document.getElementById('comicReaderModal');
        if (e.key === 'Escape' && modal && modal.style.display === 'flex') {
            closeComicReader();
        }
    });
});
