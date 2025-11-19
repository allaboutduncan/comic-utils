/**
 * browse.js
 * Frontend logic for the visual file browser.
 * Handles directory fetching, grid rendering, lazy loading, navigation, and pagination.
 */

document.addEventListener('DOMContentLoaded', () => {
    // Initialize with the root path or path from URL
    const urlParams = new URLSearchParams(window.location.search);
    const initialPath = urlParams.get('path') || '';
    loadDirectory(initialPath);
});

// State
let currentPath = '';
let isLoading = false;
let allItems = []; // Stores all files and folders for the current directory
let currentPage = 1;
let itemsPerPage = 25;

// All Books mode state
let isAllBooksMode = false;
let allBooksData = null;
let folderViewPath = '';

/**
 * Load and display the contents of a directory.
 * @param {string} path - The directory path to load.
 */
async function loadDirectory(path) {
    if (isLoading) return;

    setLoading(true);
    currentPath = path;

    // Update URL without reloading
    const newUrl = new URL(window.location);
    if (path) {
        newUrl.searchParams.set('path', path);
    } else {
        newUrl.searchParams.delete('path');
    }
    window.history.pushState({ path }, '', newUrl);

    try {
        const response = await fetch(`/api/browse?path=${encodeURIComponent(path)}`);
        if (!response.ok) {
            throw new Error(`HTTP error! status: ${response.status}`);
        }
        const data = await response.json();

        renderBreadcrumbs(data.current_path);

        // Process and store all items
        allItems = [];

        // Process directories
        if (data.directories) {
            data.directories.forEach(dir => {
                allItems.push({
                    name: dir,
                    type: 'folder',
                    path: data.current_path ? `${data.current_path}/${dir}` : dir
                });
            });
        }

        // Process files
        if (data.files) {
            data.files.forEach(file => {
                allItems.push({
                    name: file.name,
                    type: 'file',
                    path: data.current_path ? `${data.current_path}/${file.name}` : file.name,
                    size: file.size,
                    hasThumbnail: file.has_thumbnail,
                    thumbnailUrl: file.thumbnail_url
                });
            });
        }

        // Reset to first page on new directory load
        currentPage = 1;

        // Reset All Books mode when loading a new directory
        isAllBooksMode = false;
        allBooksData = null;

        // Update button visibility
        updateViewButtons(path);

        renderPage();

    } catch (error) {
        console.error('Error loading directory:', error);
        showError(error.message);
    } finally {
        setLoading(false);
    }
}

/**
 * Update view toggle button visibility based on current path and mode
 * @param {string} path - Current directory path
 */
function updateViewButtons(path) {
    const allBooksBtn = document.getElementById('allBooksBtn');
    const folderViewBtn = document.getElementById('folderViewBtn');

    if (!allBooksBtn || !folderViewBtn) return;

    if (isAllBooksMode) {
        // In All Books mode: hide All Books, show Folder View
        allBooksBtn.style.display = 'none';
        folderViewBtn.style.display = 'inline-block';
    } else {
        // In Folder mode: show All Books (if not root), hide Folder View
        if (path === '' || path === '/') {
            allBooksBtn.style.display = 'none';
        } else {
            allBooksBtn.style.display = 'inline-block';
        }
        folderViewBtn.style.display = 'none';
    }
}

/**
 * Load all books recursively from current directory
 */
async function loadAllBooks() {
    if (isLoading) return;

    setLoading(true);
    folderViewPath = currentPath;  // Save current path to return to
    isAllBooksMode = true;

    try {
        const response = await fetch(`/api/browse-recursive?path=${encodeURIComponent(currentPath)}`);
        if (!response.ok) {
            throw new Error(`HTTP error! status: ${response.status}`);
        }
        const data = await response.json();

        allBooksData = data;
        allItems = data.files;  // Only files, no folders
        currentPage = 1;

        updateViewButtons(currentPath);
        renderPage();

    } catch (error) {
        console.error('Error loading all books:', error);
        showError('Failed to load all books: ' + error.message);
        // Reset state on error
        isAllBooksMode = false;
        allBooksData = null;
        updateViewButtons(currentPath);
    } finally {
        setLoading(false);
    }
}

/**
 * Return to normal folder view from All Books mode
 */
function returnToFolderView() {
    isAllBooksMode = false;
    allBooksData = null;
    loadDirectory(folderViewPath);
}


/**
 * Render the current page of items.
 */
function renderPage() {
    const startIndex = (currentPage - 1) * itemsPerPage;
    const endIndex = startIndex + itemsPerPage;
    const pageItems = allItems.slice(startIndex, endIndex);

    renderGrid(pageItems);
    renderPagination();
}

/**
 * Render the file and folder grid.
 * @param {Array} items - The list of items to render.
 */
function renderGrid(items) {
    const grid = document.getElementById('file-grid');
    const emptyState = document.getElementById('empty-state');
    const template = document.getElementById('grid-item-template');

    grid.innerHTML = '';

    if (items.length === 0 && allItems.length === 0) {
        grid.style.display = 'none';
        emptyState.style.display = 'block';
        return;
    }

    grid.style.display = 'grid';
    emptyState.style.display = 'none';

    // Create document fragment for better performance
    const fragment = document.createDocumentFragment();

    items.forEach(item => {
        const clone = template.content.cloneNode(true);
        const gridItem = clone.querySelector('.grid-item');
        const img = clone.querySelector('.thumbnail');
        const iconOverlay = clone.querySelector('.icon-overlay');
        const icon = iconOverlay.querySelector('i');
        const nameEl = clone.querySelector('.item-name');
        const metaEl = clone.querySelector('.item-meta');

        const actionsDropdown = clone.querySelector('.item-actions');

        // Set content
        nameEl.textContent = item.name;
        nameEl.title = item.name;

        if (item.type === 'folder') {
            gridItem.classList.add('folder');
            icon.className = 'bi bi-folder-fill';
            metaEl.textContent = 'Folder';

            // Hide actions for folders
            if (actionsDropdown) actionsDropdown.style.display = 'none';

            // Handle click for folders
            gridItem.onclick = () => loadDirectory(item.path);

        } else {
            gridItem.classList.add('file');
            metaEl.textContent = formatFileSize(item.size);

            // Handle actions menu
            if (actionsDropdown) {
                const btn = actionsDropdown.querySelector('button');
                if (btn) {
                    btn.onclick = (e) => {
                        e.stopPropagation();
                        // Bootstrap handles the dropdown toggle automatically
                    };
                }

                // Close dropdown on mouse leave with a small delay
                let leaveTimeout;
                actionsDropdown.onmouseleave = () => {
                    leaveTimeout = setTimeout(() => {
                        if (btn) {
                            const dropdown = bootstrap.Dropdown.getInstance(btn);
                            if (dropdown) {
                                dropdown.hide();
                            }
                        }
                    }, 300); // 300ms delay to allow moving to menu
                };

                // Cancel the close if mouse re-enters
                actionsDropdown.onmouseenter = () => {
                    if (leaveTimeout) {
                        clearTimeout(leaveTimeout);
                    }
                };

                // Bind actions
                const actions = {
                    '.action-crop': () => executeScript('crop', item.path),
                    '.action-remove-first': () => executeScript('remove', item.path),
                    '.action-edit': () => initEditMode(item.path),
                    '.action-rebuild': () => executeScript('single_file', item.path),
                    '.action-enhance': () => executeScript('enhance_single', item.path)
                };

                Object.entries(actions).forEach(([selector, handler]) => {
                    const el = actionsDropdown.querySelector(selector);
                    if (el) {
                        el.onclick = (e) => {
                            e.preventDefault();
                            e.stopPropagation();
                            handler();
                        };
                    }
                });
            }

            if (item.hasThumbnail) {
                // Set placeholder initially, real source in data-src for lazy loading
                img.src = '/static/images/loading.svg';
                img.dataset.src = item.thumbnailUrl;
                img.dataset.thumbnailPath = item.thumbnailUrl; // Store for polling
                img.classList.add('lazy');
                img.classList.add('polling'); // Always poll thumbnails until confirmed loaded

                // Handle error loading thumbnail
                img.onerror = function () {
                    this.src = '/static/images/error.svg';
                    this.classList.remove('lazy');
                    this.classList.remove('polling'); // Stop polling on error
                };

                // Handle successful load
                img.onload = function () {
                    // If we are polling, check status
                    if (this.classList.contains('polling')) {
                        pollThumbnail(this);
                    }
                };
            } else {
                // Generic file icon
                gridItem.classList.add('folder'); // Use folder style for icon overlay
                icon.className = 'bi bi-file-earmark-text';
                img.style.display = 'none';
            }

            // Handle click for files (optional: open/download)
            gridItem.onclick = () => {
                // For now, maybe just log or do nothing. 
                // Could implement preview or download later.
                console.log('Clicked file:', item.path);
            };
        }

        fragment.appendChild(clone);
    });

    grid.appendChild(fragment);

    // Initialize lazy loading
    initLazyLoading();
}


/**
 * Render pagination controls.
 */
function renderPagination() {
    const paginationNav = document.getElementById('pagination-controls');
    const paginationList = document.getElementById('pagination-list');

    if (allItems.length <= itemsPerPage) {
        paginationNav.style.display = 'none';
        return;
    }

    paginationNav.style.display = 'block';
    paginationList.innerHTML = '';

    const totalPages = Math.ceil(allItems.length / itemsPerPage);

    // Previous Button
    const prevLi = document.createElement('li');
    prevLi.className = `page-item ${currentPage === 1 ? 'disabled' : ''}`;
    prevLi.innerHTML = `<a class="page-link" href="#" onclick="changePage(${currentPage - 1}); return false;">Previous</a>`;
    paginationList.appendChild(prevLi);

    // Page Info (e.g., "Page 1 of 5")
    const infoLi = document.createElement('li');
    infoLi.className = 'page-item disabled';
    infoLi.innerHTML = `<span class="page-link text-dark">Page ${currentPage} of ${totalPages}</span>`;
    paginationList.appendChild(infoLi);

    // Next Button
    const nextLi = document.createElement('li');
    nextLi.className = `page-item ${currentPage === totalPages ? 'disabled' : ''}`;
    nextLi.innerHTML = `<a class="page-link" href="#" onclick="changePage(${currentPage + 1}); return false;">Next</a>`;
    paginationList.appendChild(nextLi);
}

/**
 * Change the current page.
 * @param {number} page - The page number to switch to.
 */
function changePage(page) {
    const totalPages = Math.ceil(allItems.length / itemsPerPage);
    if (page < 1 || page > totalPages) return;

    currentPage = page;
    renderPage();

    // Scroll to top of grid
    document.getElementById('file-grid').scrollIntoView({ behavior: 'smooth' });
}

/**
 * Change items per page.
 * @param {number} value - The number of items per page.
 */
function changeItemsPerPage(value) {
    itemsPerPage = parseInt(value);
    currentPage = 1;
    renderPage();
}

/**
 * Poll a thumbnail URL to check if it's ready.
 * @param {HTMLImageElement} imgElement - The image element to update
 */
function pollThumbnail(imgElement) {
    if (!imgElement.classList.contains('polling')) {
        return; // Stop if polling was cancelled
    }

    // Avoid multiple concurrent polls for the same image
    if (imgElement.dataset.isPolling === 'true') return;
    imgElement.dataset.isPolling = 'true';

    const thumbnailUrl = imgElement.dataset.thumbnailPath;
    if (!thumbnailUrl) {
        imgElement.dataset.isPolling = 'false';
        return;
    }

    // Add a cache-busting parameter to force a fresh check
    const checkUrl = thumbnailUrl + (thumbnailUrl.includes('?') ? '&' : '?') + '_check=' + Date.now();

    fetch(checkUrl, { method: 'HEAD' })
        .then(response => {
            imgElement.dataset.isPolling = 'false';

            // Check if we were redirected to the loading image or error image
            const isRedirectedToLoading = response.url.includes('loading.svg');
            const isRedirectedToError = response.url.includes('error.svg');

            // If we get a 200 AND it's not the loading/error image
            if (response.ok && response.status === 200 && !isRedirectedToLoading && !isRedirectedToError) {
                // Thumbnail is ready! 
                const newSrc = thumbnailUrl + (thumbnailUrl.includes('?') ? '&' : '?') + '_t=' + Date.now();

                // We found it's ready. Stop polling.
                imgElement.classList.remove('polling');

                // Update the image to the new version
                imgElement.src = newSrc;

            } else if (imgElement.classList.contains('polling')) {
                // Still generating, poll again in 2 seconds
                setTimeout(() => pollThumbnail(imgElement), 2000);
            }
        })
        .catch(error => {
            console.error('Error polling thumbnail:', error);
            imgElement.dataset.isPolling = 'false';
            // Retry after a longer delay on error
            if (imgElement.classList.contains('polling')) {
                setTimeout(() => pollThumbnail(imgElement), 5000);
            }
        });
}

/**
 * Update the breadcrumb navigation.
 * @param {string} path - The current directory path.
 */
function renderBreadcrumbs(path) {
    const breadcrumb = document.getElementById('breadcrumb');
    breadcrumb.innerHTML = '';

    // Always add Home/Root
    const homeLi = document.createElement('li');
    homeLi.className = 'breadcrumb-item';
    if (!path) {
        homeLi.classList.add('active');
        homeLi.textContent = 'Home';
    } else {
        const homeLink = document.createElement('a');
        homeLink.href = '#';
        homeLink.textContent = 'Home';
        homeLink.onclick = (e) => {
            e.preventDefault();
            loadDirectory('');
        };
        homeLi.appendChild(homeLink);
    }
    breadcrumb.appendChild(homeLi);

    if (!path) return;

    // Split path into segments
    // Handle both forward and backward slashes just in case, though API should normalize
    const segments = path.split(/[/\\]/).filter(Boolean);
    let builtPath = '';

    segments.forEach((segment, index) => {
        const isLast = index === segments.length - 1;
        const li = document.createElement('li');
        li.className = 'breadcrumb-item';

        // Reconstruct path for this segment
        // Note: We need to be careful about how we join. 
        // If the original path started with /, we might need to handle that, 
        // but usually the API returns a clean path relative to DATA_DIR or absolute.
        // For simplicity, we'll assume the API handles the path string correctly when passed back.
        if (index === 0) {
            // If the path is absolute (starts with / on linux or C:\ on windows), 
            // the split might behave differently. 
            // However, for the breadcrumb UI, we just want the folder names.
            // We'll reconstruct the path cumulatively.
            // Actually, let's just use the segments.
            builtPath = segment;
            // If the original path started with a separator that got split out, we might need to prepend it?
            // Let's assume the path passed to loadDirectory is what we want to pass back.
            // If path starts with /, split gives empty string first.
            if (path.startsWith('/')) builtPath = '/' + builtPath;
            else if (path.includes(':\\') && index === 0) {
                // Windows drive letter, keep it as is
            }
        } else {
            builtPath += '/' + segment;
        }

        if (isLast) {
            li.classList.add('active');
            li.textContent = segment;
        } else {
            const link = document.createElement('a');
            link.href = '#';
            link.textContent = segment;
            // Capture the current value of builtPath
            const clickPath = builtPath;
            link.onclick = (e) => {
                e.preventDefault();
                loadDirectory(clickPath);
            };
            li.appendChild(link);
        }
        breadcrumb.appendChild(li);
    });
}

/**
 * Initialize IntersectionObserver for lazy loading thumbnails.
 */
function initLazyLoading() {
    if ('IntersectionObserver' in window) {
        const imageObserver = new IntersectionObserver((entries, observer) => {
            entries.forEach(entry => {
                if (entry.isIntersecting) {
                    const img = entry.target;
                    if (img.dataset.src) {
                        img.src = img.dataset.src;
                        img.classList.remove('lazy');
                        observer.unobserve(img);
                    }
                }
            });
        });

        const lazyImages = document.querySelectorAll('img.lazy');
        lazyImages.forEach(img => {
            imageObserver.observe(img);
        });
    } else {
        // Fallback for older browsers
        const lazyImages = document.querySelectorAll('img.lazy');
        lazyImages.forEach(img => {
            img.src = img.dataset.src;
            img.classList.remove('lazy');
        });
    }
}

/**
 * Refresh the current view.
 */
function refreshCurrentView() {
    loadDirectory(currentPath);
}

/**
 * Toggle loading state UI.
 * @param {boolean} loading 
 */
function setLoading(loading) {
    isLoading = loading;
    const indicator = document.getElementById('loading-indicator');
    const grid = document.getElementById('file-grid');
    const empty = document.getElementById('empty-state');
    const pagination = document.getElementById('pagination-controls');

    if (loading) {
        indicator.style.display = 'block';
        grid.style.display = 'none';
        empty.style.display = 'none';
        if (pagination) pagination.style.display = 'none';
    } else {
        indicator.style.display = 'none';
        // grid display is handled in renderGrid
    }
}

/**
 * Show error message (simple alert for now).
 * @param {string} message 
 */
function showError(message) {
    // You could replace this with a Bootstrap toast or alert
    alert('Error: ' + message);
}

/**
 * Format file size bytes to human readable string.
 * @param {number} bytes 
 * @returns {string}
 */
function formatFileSize(bytes) {
    if (bytes === 0) return '0 Bytes';
    const k = 1024;
    const sizes = ['Bytes', 'KB', 'MB', 'GB', 'TB'];
    const i = Math.floor(Math.log(bytes) / Math.log(k));
    return parseFloat((bytes / Math.pow(k, i)).toFixed(2)) + ' ' + sizes[i];
}


// Handle browser back/forward buttons
window.onpopstate = (event) => {
    if (event.state && event.state.path !== undefined) {
        loadDirectory(event.state.path);
    } else {
        // Default to root if no state
        loadDirectory('');
    }
};

// -- File Action Execution Functions --

let currentEventSource = null;

/**
 * Show the global progress indicator
 */
function showProgressIndicator() {
    const progressContainer = document.getElementById('progress-container');
    if (progressContainer) {
        progressContainer.style.display = 'block';
    }
}

/**
 * Hide the global progress indicator
 */
function hideProgressIndicator() {
    const progressContainer = document.getElementById('progress-container');
    if (progressContainer) {
        progressContainer.style.display = 'none';
    }
}

/**
 * Refresh a specific thumbnail after an action completes
 * @param {string} filePath - The file path whose thumbnail should be refreshed
 */
function refreshThumbnail(filePath) {
    // Find the image element for this file path
    const grid = document.getElementById('file-grid');
    if (!grid) return;

    // Find all grid items
    const gridItems = grid.querySelectorAll('.grid-item.file');
    gridItems.forEach(item => {
        const nameEl = item.querySelector('.item-name');
        if (nameEl && nameEl.textContent === filePath.split('/').pop()) {
            const img = item.querySelector('.thumbnail');
            if (img && img.dataset.thumbnailPath) {
                // Force reload with cache busting
                const thumbnailUrl = img.dataset.thumbnailPath;
                const newSrc = thumbnailUrl + (thumbnailUrl.includes('?') ? '&' : '?') + '_refresh=' + Date.now();
                img.src = newSrc;
                console.log('Refreshed thumbnail for:', filePath);
            }
        }
    });
}

/**
 * Execute a script action on a file
 * @param {string} scriptType - The type of script to run (crop, remove, single_file, enhance_single)
 * @param {string} filePath - The path to the file to process
 */
function executeScript(scriptType, filePath) {
    if (!filePath) {
        showError("No file path provided");
        return;
    }

    if (currentEventSource) {
        currentEventSource.close();
        currentEventSource = null;
    }

    const url = `/stream/${scriptType}?file_path=${encodeURIComponent(filePath)}`;
    console.log(`Executing ${scriptType} on: ${filePath}`);
    console.log(`Connecting to: ${url}`);

    const eventSource = new EventSource(url);
    currentEventSource = eventSource;

    // Show progress container
    const progressContainer = document.getElementById('progress-container');
    const progressBar = document.getElementById('progress-bar');
    const progressText = document.getElementById('progress-text');

    if (progressContainer) {
        progressContainer.style.display = 'block';
        if (progressBar) {
            progressBar.style.width = '0%';
            progressBar.textContent = '0%';
            progressBar.setAttribute('aria-valuenow', '0');
        }
        if (progressText) {
            progressText.textContent = 'Initializing...';
        }
    }

    // Handle progress messages
    eventSource.onmessage = (event) => {
        const line = event.data.trim();

        // Skip empty keepalive messages
        if (!line) return;

        console.log('Progress:', line);

        // Update progress text with the message
        if (progressText) {
            progressText.textContent = line;
        }

        // Look for completion messages
        if (line.includes('completed') || line.includes('SUCCESS:')) {
            if (progressBar) {
                progressBar.style.width = '100%';
                progressBar.textContent = '100%';
                progressBar.setAttribute('aria-valuenow', '100');
            }
        }
    };

    eventSource.addEventListener("completed", () => {
        console.log('Script completed successfully');
        if (progressText) {
            progressText.textContent = 'Completed successfully!';
        }
        if (progressBar) {
            progressBar.style.width = '100%';
            progressBar.textContent = '100%';
        }

        eventSource.close();
        currentEventSource = null;

        // Refresh the thumbnail for this file
        refreshThumbnail(filePath);

        // Auto-hide progress after 3 seconds
        setTimeout(() => {
            hideProgressIndicator();
        }, 3000);
    });

    eventSource.onerror = () => {
        console.error('Error executing script');
        if (progressText) {
            progressText.textContent = 'Error occurred during processing';
        }

        eventSource.close();
        currentEventSource = null;

        // Auto-hide progress after 5 seconds
        setTimeout(() => {
            hideProgressIndicator();
        }, 5000);
    };
}

// ============================================================================
// INLINE EDIT FUNCTIONALITY
// ============================================================================

/**
 * Initialize edit mode for a CBZ file
 * @param {string} filePath - Path to the CBZ file to edit
 */
function initEditMode(filePath) {
    // Hide the file grid and other collection UI elements
    document.getElementById('file-grid').style.display = 'none';
    const paginationControls = document.getElementById('pagination-controls');
    if (paginationControls) paginationControls.style.display = 'none';

    // Show the edit section
    document.getElementById('edit').classList.remove('collapse');

    const container = document.getElementById('editInlineContainer');
    container.innerHTML = `<div class="d-flex justify-content-center my-3">
                                <button class="btn btn-primary" type="button" disabled>
                                    <span class="spinner-grow spinner-grow-sm" role="status" aria-hidden="true"></span>
                                    Unpacking CBZ File ...
                                </button>
                            </div>`;

    fetch(`/edit?file_path=${encodeURIComponent(filePath)}`)
        .then(response => {
            if (!response.ok) {
                throw new Error("Failed to load edit content.");
            }
            return response.json();
        })
        .then(data => {
            document.getElementById('editInlineContainer').innerHTML = data.modal_body;
            document.getElementById('editInlineFolderName').value = data.folder_name;
            document.getElementById('editInlineZipFilePath').value = data.zip_file_path;
            document.getElementById('editInlineOriginalFilePath').value = data.original_file_path;
            sortInlineEditCards();

            // Setup form submit handler to prevent page navigation
            setupSaveFormHandler();
        })
        .catch(error => {
            container.innerHTML = `<div class="alert alert-danger" role="alert">
                    <strong>Error:</strong> ${error.message}
                </div>`;
            showError(error.message);
        });
}

/**
 * Setup form submit handler for save functionality
 */
function setupSaveFormHandler() {
    const form = document.getElementById('editInlineSaveForm');
    if (!form) return;

    // Remove any existing submit handlers
    const newForm = form.cloneNode(true);
    form.parentNode.replaceChild(newForm, form);

    newForm.addEventListener('submit', function (e) {
        e.preventDefault();

        const formData = new FormData(newForm);
        const data = {
            folder_name: formData.get('folder_name'),
            zip_file_path: formData.get('zip_file_path'),
            original_file_path: formData.get('original_file_path')
        };

        // Show progress indicator
        showProgressIndicator();
        const progressText = document.getElementById('progress-text');
        if (progressText) {
            progressText.textContent = 'Saving CBZ file...';
        }

        fetch('/save', {
            method: 'POST',
            headers: {
                'Content-Type': 'application/json'
            },
            body: JSON.stringify(data)
        })
            .then(response => response.json())
            .then(result => {
                if (result.success) {
                    // Hide edit section and show collection grid
                    document.getElementById('edit').classList.add('collapse');
                    document.getElementById('file-grid').style.display = 'grid';
                    const paginationControls = document.getElementById('pagination-controls');
                    if (paginationControls && allItems.length > itemsPerPage) {
                        paginationControls.style.display = 'block';
                    }

                    // Clear edit container
                    document.getElementById('editInlineContainer').innerHTML = '';

                    // Refresh the current view to show updated thumbnail
                    setTimeout(() => {
                        refreshCurrentView();
                        hideProgressIndicator();
                    }, 500);
                } else {
                    showError('Error saving file: ' + (result.error || 'Unknown error'));
                    hideProgressIndicator();
                }
            })
            .catch(error => {
                console.error('Error:', error);
                showError('An error occurred while saving the file.');
                hideProgressIndicator();
            });
    });
}

/**
 * Enable inline editing of a filename
 * @param {HTMLElement} element - The filename span element
 */
function enableFilenameEdit(element) {
    console.log("enableFilenameEdit called");
    const input = element.nextElementSibling;
    if (!input) {
        console.error("No adjacent input found for", element);
        return;
    }
    element.classList.add('d-none');
    input.classList.remove('d-none');
    input.focus();
    input.select();

    let renameProcessed = false;

    function processRename(event) {
        if (renameProcessed) return;
        renameProcessed = true;
        performRename(input);
    }

    input.addEventListener('keydown', function (event) {
        if (event.key === 'Enter') {
            event.preventDefault();
            processRename(event);
            input.blur();
        }
    });

    input.addEventListener('blur', function (event) {
        processRename(event);
    }, { once: true });
}

/**
 * Sort inline edit cards by filename
 * Mimics file system sorting: alpha-numeric order with special characters first
 */
function sortInlineEditCards() {
    const container = document.getElementById('editInlineContainer');
    if (!container) return;

    // Get all card elements as an array
    const cards = Array.from(container.children);

    // Regex to check if the filename starts with a letter or a digit
    const alphanumRegex = /^[a-z0-9]/i;

    // Create an Intl.Collator instance for natural (alpha-numeric) sorting
    const collator = new Intl.Collator(undefined, { numeric: true, sensitivity: 'base' });

    cards.sort((a, b) => {
        const inputA = a.querySelector('.filename-input');
        const inputB = b.querySelector('.filename-input');
        const filenameA = inputA ? inputA.value : "";
        const filenameB = inputB ? inputB.value : "";

        // Determine if the filename starts with a letter or digit
        const aIsAlphaNum = alphanumRegex.test(filenameA);
        const bIsAlphaNum = alphanumRegex.test(filenameB);

        // Files starting with special characters should sort before those starting with letters or digits
        if (!aIsAlphaNum && bIsAlphaNum) return -1;
        if (aIsAlphaNum && !bIsAlphaNum) return 1;

        // Otherwise, use natural (alpha-numeric) sort order
        return collator.compare(filenameA, filenameB);
    });

    // Rebuild the container with the sorted cards
    container.innerHTML = '';
    cards.forEach(card => container.appendChild(card));
}

/**
 * Perform rename operation on a file
 * @param {HTMLInputElement} input - The input element containing the new filename
 */
function performRename(input) {
    const newFilename = input.value.trim();
    const folderName = document.getElementById('editInlineFolderName').value;
    const oldFilename = input.dataset.oldFilename || input.previousElementSibling.textContent.trim();

    // Cancel if the filename hasn't changed
    if (newFilename === oldFilename) {
        input.classList.add('d-none');
        input.previousElementSibling.classList.remove('d-none');
        return;
    }

    const oldPath = `${folderName}/${oldFilename}`;
    const newPath = `${folderName}/${newFilename}`;

    console.log("Renaming", oldPath, "to", newPath);

    fetch('/rename', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ old: oldPath, new: newPath })
    })
        .then(response => response.json())
        .then(data => {
            if (data.success) {
                const span = input.previousElementSibling;
                span.textContent = newFilename;
                span.classList.remove('d-none');
                input.classList.add('d-none');
                // After updating the filename, re-sort the inline edit cards.
                sortInlineEditCards();
            } else {
                showError('Error renaming file: ' + data.error);
            }
        })
        .catch(error => {
            console.error('Error:', error);
            showError('An error occurred while renaming the file.');
        });
}

/**
 * Delete an image card from the CBZ
 * @param {HTMLElement} buttonElement - The delete button element
 */
function deleteCardImage(buttonElement) {
    const colElement = buttonElement.closest('.col');
    if (!colElement) {
        console.error("Unable to locate column container for deletion.");
        return;
    }
    const span = colElement.querySelector('.editable-filename');
    if (!span) {
        console.error("No file reference found in column:", colElement);
        return;
    }
    const folderName = document.getElementById('editInlineFolderName').value;
    if (!folderName) {
        console.error("Folder name not found in #editInlineFolderName.");
        return;
    }
    const oldFilename = span.dataset.oldFilename || span.textContent.trim();
    const fullPath = `${folderName}/${oldFilename}`;

    fetch('/delete', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ target: fullPath })
    })
        .then(response => response.json())
        .then(data => {
            if (data.success) {
                colElement.classList.add("fade-out");
                setTimeout(() => {
                    colElement.remove();
                }, 300);
            } else {
                showError("Error deleting image: " + data.error);
            }
        })
        .catch(error => {
            console.error("Error:", error);
            showError("An error occurred while deleting the image.");
        });
}

/**
 * Crop left portion of image
 * @param {HTMLElement} buttonElement - The crop button element
 */
function cropImageLeft(buttonElement) {
    processCropImage(buttonElement, 'left');
}

/**
 * Crop center of image (splits into two)
 * @param {HTMLElement} buttonElement - The crop button element
 */
function cropImageCenter(buttonElement) {
    processCropImage(buttonElement, 'center');
}

/**
 * Crop right portion of image
 * @param {HTMLElement} buttonElement - The crop button element
 */
function cropImageRight(buttonElement) {
    processCropImage(buttonElement, 'right');
}

/**
 * Process crop operation
 * @param {HTMLElement} buttonElement - The crop button element
 * @param {string} cropType - Type of crop: 'left', 'center', or 'right'
 */
function processCropImage(buttonElement, cropType) {
    const colElement = buttonElement.closest('.col');
    if (!colElement) {
        console.error("Unable to locate column container.");
        return;
    }

    const span = colElement.querySelector('.editable-filename');
    if (!span) {
        console.error("No file reference found in column:", colElement);
        return;
    }

    const folderElement = document.getElementById('editInlineFolderName');
    if (!folderElement) {
        console.error("Folder name input element not found.");
        return;
    }

    const folderName = folderElement.value;
    if (!folderName) {
        console.error("Folder name is empty.");
        return;
    }

    const oldFilename = span.dataset.oldFilename || span.textContent.trim();
    const fullPath = `${folderName}/${oldFilename}`;

    fetch('/crop', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ target: fullPath, cropType: cropType })
    })
        .then(response => response.json())
        .then(data => {
            if (data.success) {
                const container = document.getElementById('editInlineContainer');

                // Remove the original card from the DOM
                colElement.remove();

                if (data.html) {
                    // Center crop returns full HTML cards
                    container.insertAdjacentHTML('beforeend', data.html);
                } else {
                    // Left/right crop returns single image + base64
                    const newCardHTML = generateCardHTML(data.newImagePath, data.newImageData);
                    container.insertAdjacentHTML('beforeend', newCardHTML);
                }

                // After insertion, sort the updated cards
                sortInlineEditCards();

            } else {
                showError("Error cropping image: " + data.error);
            }
        })
        .catch(error => {
            console.error("Error:", error);
            showError("An error occurred while cropping the image.");
        });
}

/**
 * Generate HTML for an image card
 * @param {string} imagePath - Path to the image
 * @param {string} imageData - Base64 encoded image data
 * @returns {string} HTML string for the card
 */
function generateCardHTML(imagePath, imageData) {
    // Extract filename_only from the full path for sorting and display purposes
    const filenameOnly = imagePath.split('/').pop();
    return `
    <div class="col">
        <div class="card h-100 shadow-sm">
            <div class="row g-0">
                <div class="col-3">
                    <img src="${imageData}" class="img-fluid rounded-start object-fit-scale border rounded" alt="${filenameOnly}">
                </div>
                <div class="col-9">
                    <div class="card-body">
                        <p class="card-text small">
                            <span class="editable-filename" data-rel-path="${imagePath}" onclick="enableFilenameEdit(this)">
                                ${filenameOnly}
                            </span>
                            <input type="text" class="form-control d-none filename-input form-control-sm" value="${filenameOnly}" data-rel-path="${imagePath}">
                        </p>
                        <div class="d-flex justify-content-end">
                            <div class="btn-group" role="group" aria-label="Basic example">
                                <button type="button" class="btn btn-outline-primary btn-sm" onclick="cropImageFreeForm(this)" title="Free Form Crop">
                                    <i class="bi bi-crop"></i> Free
                                </button>
                                <button type="button" class="btn btn-outline-secondary btn-sm" onclick="cropImageLeft(this)" title="Crop Image Left">
                                    <i class="bi bi-arrow-bar-left"></i> Left
                                </button>
                                <button type="button" class="btn btn-outline-secondary" onclick="cropImageCenter(this)" title="Crop Image Center">Middle</button>
                                <button type="button" class="btn btn-outline-secondary btn-sm" onclick="cropImageRight(this)" title="Crop Image Right">
                                    Right <i class="bi bi-arrow-bar-right"></i>
                                </button>
                                <button type="button" class="btn btn-outline-danger btn-sm" onclick="deleteCardImage(this)">
                                    <i class="bi bi-trash"></i>
                                </button>
                            </div>
                        </div>
                    </div>
                </div>
            </div>
        </div>
    </div>`;
}

// ============================================================================
// FREE-FORM CROP FUNCTIONALITY
// ============================================================================

// Crop state management
let cropData = {
    imagePath: null,
    startX: 0,
    startY: 0,
    endX: 0,
    endY: 0,
    isDragging: false,
    imageElement: null,
    colElement: null,
    isPanning: false,
    panStartX: 0,
    panStartY: 0,
    selectionLeft: 0,
    selectionTop: 0,
    spacebarPressed: false,
    wasDrawingBeforePan: false,
    savedWidth: 0,
    savedHeight: 0
};

/**
 * Open free-form crop modal for an image
 * @param {HTMLElement} buttonElement - The free crop button element
 */
function cropImageFreeForm(buttonElement) {
    const colElement = buttonElement.closest('.col');
    if (!colElement) {
        console.error("Unable to locate column container.");
        return;
    }

    const span = colElement.querySelector('.editable-filename');
    if (!span) {
        console.error("No file reference found in column:", colElement);
        return;
    }

    const folderElement = document.getElementById('editInlineFolderName');
    if (!folderElement) {
        console.error("Folder name input element not found.");
        return;
    }

    const folderName = folderElement.value;
    if (!folderName) {
        console.error("Folder name is empty.");
        return;
    }

    const oldFilename = span.dataset.oldFilename || span.textContent.trim();
    const fullPath = `${folderName}/${oldFilename}`;

    // Store the data for later use
    cropData.imagePath = fullPath;
    cropData.colElement = colElement;

    // Get the image source from the card
    const cardImg = colElement.querySelector('img');
    if (!cardImg) {
        console.error("No image found in card");
        return;
    }

    // Load the full-size image into the modal
    const cropImage = document.getElementById('cropImage');
    const cropModal = new bootstrap.Modal(document.getElementById('freeFormCropModal'));

    // Reset crop selection
    const cropSelection = document.getElementById('cropSelection');
    cropSelection.style.display = 'none';
    document.getElementById('confirmCropBtn').disabled = true;

    // Load image from the server
    fetch('/get-image-data', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ target: fullPath })
    })
        .then(response => response.json())
        .then(data => {
            if (data.success) {
                cropImage.src = data.imageData;
                cropImage.onload = function () {
                    setupCropHandlers();
                    cropModal.show();
                };
            } else {
                showError("Error loading image: " + data.error);
            }
        })
        .catch(error => {
            console.error("Error:", error);
            showError("An error occurred while loading the image.");
        });
}

/**
 * Setup event handlers for crop modal
 */
function setupCropHandlers() {
    const cropImage = document.getElementById('cropImage');
    const cropSelection = document.getElementById('cropSelection');
    const confirmBtn = document.getElementById('confirmCropBtn');
    const cropContainer = document.getElementById('cropImageContainer');

    // Remove any existing event listeners by cloning the element
    const newCropImage = cropImage.cloneNode(true);
    cropImage.parentNode.replaceChild(newCropImage, cropImage);
    cropData.imageElement = newCropImage;

    // Add keyboard listeners for spacebar
    document.addEventListener('keydown', handleKeyDown);
    document.addEventListener('keyup', handleKeyUp);

    // Attach mouse events to the container for better coverage
    cropContainer.addEventListener('mousedown', startCrop);
    document.addEventListener('mousemove', updateCrop);
    document.addEventListener('mouseup', endCrop);

    // Add mousedown listener to selection box for panning
    cropSelection.addEventListener('mousedown', function (e) {
        if (cropData.spacebarPressed) {
            startPan(e);
        }
    });

    function handleKeyDown(e) {
        if (e.key === ' ' || e.code === 'Space') {
            e.preventDefault();

            // Don't change mode if already in spacebar mode
            if (cropData.spacebarPressed) return;

            cropData.spacebarPressed = true;
            cropContainer.style.cursor = 'move';
            console.log('Spacebar pressed - switching to pan mode');

            // If we're currently drawing, pause drawing and switch to panning
            if (cropData.isDragging) {
                console.log('Pausing draw mode, entering pan mode');
                cropData.wasDrawingBeforePan = true;
                cropData.isDragging = false;
                cropData.isPanning = false; // Will start on next mouse move

                // Save current selection dimensions
                cropData.savedWidth = Math.abs(cropData.endX - cropData.startX);
                cropData.savedHeight = Math.abs(cropData.endY - cropData.startY);
            }
        }
    }

    function handleKeyUp(e) {
        if (e.key === ' ' || e.code === 'Space') {
            e.preventDefault();
            cropData.spacebarPressed = false;
            cropContainer.style.cursor = 'crosshair';
            console.log('Spacebar released - back to draw mode');

            // If we were panning, stop panning
            if (cropData.isPanning) {
                cropData.isPanning = false;
                console.log('Stopped panning');
            }

            // If we were drawing before pan, resume drawing
            if (cropData.wasDrawingBeforePan) {
                console.log('Resuming draw mode');
                cropData.isDragging = true;
                cropData.wasDrawingBeforePan = false;
            }
        }
    }

    function startPan(e) {
        e.preventDefault();
        e.stopPropagation();

        console.log('Start pan - spacebar pressed:', cropData.spacebarPressed);

        cropData.isPanning = true;
        cropData.panStartX = e.clientX;
        cropData.panStartY = e.clientY;

        // Get current position
        cropData.selectionLeft = parseInt(cropSelection.style.left) || 0;
        cropData.selectionTop = parseInt(cropSelection.style.top) || 0;

        document.addEventListener('mousemove', updatePan);
        document.addEventListener('mouseup', endPan);
    }

    function updatePan(e) {
        if (!cropData.isPanning) return;

        e.preventDefault();
        const deltaX = e.clientX - cropData.panStartX;
        const deltaY = e.clientY - cropData.panStartY;

        const newLeft = cropData.selectionLeft + deltaX;
        const newTop = cropData.selectionTop + deltaY;

        // Get container bounds (not image bounds)
        const containerRect = cropContainer.getBoundingClientRect();
        const selectionWidth = parseInt(cropSelection.style.width) || 0;
        const selectionHeight = parseInt(cropSelection.style.height) || 0;

        // Constrain to container bounds
        const constrainedLeft = Math.max(0, Math.min(newLeft, containerRect.width - selectionWidth));
        const constrainedTop = Math.max(0, Math.min(newTop, containerRect.height - selectionHeight));

        cropSelection.style.left = constrainedLeft + 'px';
        cropSelection.style.top = constrainedTop + 'px';

        console.log('Update pan - left:', constrainedLeft, 'top:', constrainedTop);

        // Update crop data coordinates
        cropData.startX = constrainedLeft;
        cropData.startY = constrainedTop;
        cropData.endX = constrainedLeft + selectionWidth;
        cropData.endY = constrainedTop + selectionHeight;
    }

    function endPan(e) {
        cropData.isPanning = false;
        document.removeEventListener('mousemove', updatePan);
        document.removeEventListener('mouseup', endPan);
        console.log('End pan');
    }

    function startCrop(e) {
        // Don't start drawing if spacebar is pressed (pan mode)
        if (cropData.spacebarPressed) {
            // If there's an existing selection, start panning it
            if (cropSelection.style.display !== 'none') {
                startPan(e);
            }
            return;
        }

        // Don't start if clicking on the selection itself (unless spacebar is pressed)
        if (e.target === cropSelection) return;

        cropData.isDragging = true;

        const rect = newCropImage.getBoundingClientRect();
        cropData.startX = e.clientX - rect.left;
        cropData.startY = e.clientY - rect.top;

        cropSelection.style.left = cropData.startX + 'px';
        cropSelection.style.top = cropData.startY + 'px';
        cropSelection.style.width = '0px';
        cropSelection.style.height = '0px';
        cropSelection.style.display = 'block';

        confirmBtn.disabled = true;
    }

    function updateCrop(e) {
        if (!cropData.isDragging) return;

        const rect = newCropImage.getBoundingClientRect();
        const currentX = e.clientX - rect.left;
        const currentY = e.clientY - rect.top;

        let width = currentX - cropData.startX;
        let height = currentY - cropData.startY;

        // Get the image's position and size within the container
        const imageRect = newCropImage.getBoundingClientRect();
        const containerRect = cropContainer.getBoundingClientRect();

        // Calculate image offset within container
        const imageOffsetX = imageRect.left - containerRect.left;
        const imageOffsetY = imageRect.top - containerRect.top;

        // Constrain start point to image bounds
        if (cropData.startX < imageOffsetX) {
            cropData.startX = imageOffsetX;
        }
        if (cropData.startY < imageOffsetY) {
            cropData.startY = imageOffsetY;
        }

        // Handle negative width/height (dragging in different directions)
        // Constrain the selection box to stay within image bounds
        let finalLeft, finalTop, finalWidth, finalHeight;

        if (width < 0) {
            finalLeft = Math.max(imageOffsetX, cropData.startX + width);
            finalWidth = cropData.startX - finalLeft;
            cropData.endX = finalLeft;
        } else {
            finalLeft = cropData.startX;
            finalWidth = Math.min(width, (imageOffsetX + imageRect.width) - cropData.startX);
            cropData.endX = finalLeft + finalWidth;
        }

        if (height < 0) {
            finalTop = Math.max(imageOffsetY, cropData.startY + height);
            finalHeight = cropData.startY - finalTop;
            cropData.endY = finalTop;
        } else {
            finalTop = cropData.startY;
            finalHeight = Math.min(height, (imageOffsetY + imageRect.height) - cropData.startY);
            cropData.endY = finalTop + finalHeight;
        }

        // Apply the constrained values to the selection box
        cropSelection.style.left = finalLeft + 'px';
        cropSelection.style.top = finalTop + 'px';
        cropSelection.style.width = finalWidth + 'px';
        cropSelection.style.height = finalHeight + 'px';
    }

    function endCrop(e) {
        if (!cropData.isDragging) return;

        cropData.isDragging = false;

        const rect = newCropImage.getBoundingClientRect();
        const currentX = e.clientX - rect.left;
        const currentY = e.clientY - rect.top;

        cropData.endX = currentX;
        cropData.endY = currentY;

        // Enable confirm button if a valid selection was made
        const width = Math.abs(cropData.endX - cropData.startX);
        const height = Math.abs(cropData.endY - cropData.startY);

        if (width > 10 && height > 10) {
            confirmBtn.disabled = false;
        } else {
            cropSelection.style.display = 'none';
        }
    }

    // Clean up all event listeners when modal is closed
    const modal = document.getElementById('freeFormCropModal');
    modal.addEventListener('hidden.bs.modal', function () {
        document.removeEventListener('keydown', handleKeyDown);
        document.removeEventListener('keyup', handleKeyUp);
        document.removeEventListener('mousemove', updateCrop);
        document.removeEventListener('mouseup', endCrop);
        cropContainer.removeEventListener('mousedown', startCrop);
    }, { once: true });
}

/**
 * Confirm and execute free-form crop
 */
function confirmFreeFormCrop() {
    const cropImage = document.getElementById('cropImage');
    const cropContainer = document.getElementById('cropImageContainer');
    const imageRect = cropImage.getBoundingClientRect();
    const containerRect = cropContainer.getBoundingClientRect();

    // Calculate image offset within container
    const imageOffsetX = imageRect.left - containerRect.left;
    const imageOffsetY = imageRect.top - containerRect.top;

    // Calculate the scale factor between displayed image and actual image
    const scaleX = cropImage.naturalWidth / cropImage.width;
    const scaleY = cropImage.naturalHeight / cropImage.height;

    // Get the crop coordinates relative to the container
    const displayX = Math.min(cropData.startX, cropData.endX);
    const displayY = Math.min(cropData.startY, cropData.endY);
    const displayWidth = Math.abs(cropData.endX - cropData.startX);
    const displayHeight = Math.abs(cropData.endY - cropData.startY);

    // Convert to coordinates relative to the image (subtract image offset)
    const imageRelativeX = displayX - imageOffsetX;
    const imageRelativeY = displayY - imageOffsetY;

    // Convert to actual image coordinates
    let actualX = imageRelativeX * scaleX;
    let actualY = imageRelativeY * scaleY;
    let actualWidth = displayWidth * scaleX;
    let actualHeight = displayHeight * scaleY;

    // Clamp coordinates to ensure they don't exceed actual image dimensions
    actualX = Math.max(0, Math.min(actualX, cropImage.naturalWidth));
    actualY = Math.max(0, Math.min(actualY, cropImage.naturalHeight));
    actualWidth = Math.min(actualWidth, cropImage.naturalWidth - actualX);
    actualHeight = Math.min(actualHeight, cropImage.naturalHeight - actualY);

    console.log('Image offset:', { imageOffsetX, imageOffsetY });
    console.log('Display coords:', { displayX, displayY, displayWidth, displayHeight });
    console.log('Image relative coords:', { imageRelativeX, imageRelativeY });
    console.log('Natural image size:', { width: cropImage.naturalWidth, height: cropImage.naturalHeight });
    console.log('Actual crop coordinates:', { x: actualX, y: actualY, width: actualWidth, height: actualHeight });

    // Send the crop request
    fetch('/crop-freeform', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
            target: cropData.imagePath,
            x: actualX,
            y: actualY,
            width: actualWidth,
            height: actualHeight
        })
    })
        .then(response => response.json())
        .then(data => {
            if (data.success) {
                // Close the modal
                const modalElement = document.getElementById('freeFormCropModal');
                const modalInstance = bootstrap.Modal.getInstance(modalElement);
                modalInstance.hide();

                // Update the cropped image in the existing card
                const cardImg = cropData.colElement.querySelector('img');
                if (cardImg) {
                    cardImg.src = data.newImageData;
                }

                // Add the backup image as a new card
                if (data.backupImagePath && data.backupImageData) {
                    const container = document.getElementById('editInlineContainer');
                    const newCardHTML = generateCardHTML(data.backupImagePath, data.backupImageData);
                    container.insertAdjacentHTML('beforeend', newCardHTML);

                    // Sort the cards after adding the new one
                    sortInlineEditCards();
                }

                showError("Free form crop completed successfully!");
            } else {
                showError("Error cropping image: " + data.error);
            }
        })
        .catch(error => {
            console.error("Error:", error);
            showError("An error occurred while cropping the image.");
        });
}
// ============================================================================
// MODAL-BASED EDIT FUNCTIONALITY
// ============================================================================

/**
 * Initialize edit mode - opens modal and loads CBZ contents
 * @param {string} filePath - Path to the CBZ file to edit
 */
function initEditMode(filePath) {
    // Open the edit modal
    const editModal = new bootstrap.Modal(document.getElementById('editCBZModal'));
    const container = document.getElementById('editInlineContainer');

    // Show loading spinner
    container.innerHTML = `<div class="d-flex justify-content-center my-3">
                                <button class="btn btn-primary" type="button" disabled>
                                    <span class="spinner-grow spinner-grow-sm" role="status" aria-hidden="true"></span>
                                    Unpacking CBZ File ...
                                </button>
                            </div>`;

    editModal.show();

    // Load CBZ contents
    fetch(`/edit?file_path=${encodeURIComponent(filePath)}`)
        .then(response => {
            if (!response.ok) {
                throw new Error("Failed to load edit content.");
            }
            return response.json();
        })
        .then(data => {
            document.getElementById('editInlineContainer').innerHTML = data.modal_body;
            document.getElementById('editInlineFolderName').value = data.folder_name;
            document.getElementById('editInlineZipFilePath').value = data.zip_file_path;
            document.getElementById('editInlineOriginalFilePath').value = data.original_file_path;
            sortInlineEditCards();
        })
        .catch(error => {
            container.innerHTML = `<div class="alert alert-danger" role="alert">
                    <strong>Error:</strong> ${error.message}
                </div>`;
            showError(error.message);
        });
}

/**
 * Save the edited CBZ file - sends form data and closes modal
 */
function saveEditedCBZ() {
    const form = document.getElementById('editInlineSaveForm');
    if (!form) {
        showError('Form not found');
        return;
    }

    // Show progress indicator
    showProgressIndicator();
    const progressText = document.getElementById('progress-text');
    if (progressText) {
        progressText.textContent = 'Saving CBZ file...';
    }

    // Create FormData from the form (sends as form data, not JSON)
    const formData = new FormData(form);

    fetch('/save', {
        method: 'POST',
        body: formData  // Send as form data, not JSON
    })
        .then(response => response.json())
        .then(result => {
            if (result.success) {
                // Close the modal
                const modalElement = document.getElementById('editCBZModal');
                const modalInstance = bootstrap.Modal.getInstance(modalElement);
                if (modalInstance) {
                    modalInstance.hide();
                }

                // Clear edit container
                document.getElementById('editInlineContainer').innerHTML = '';

                // Refresh the current view to show updated thumbnail
                setTimeout(() => {
                    refreshCurrentView();
                    hideProgressIndicator();
                }, 500);
            } else {
                showError('Error saving file: ' + (result.error || 'Unknown error'));
                hideProgressIndicator();
            }
        })
        .catch(error => {
            console.error('Error:', error);
            showError('An error occurred while saving the file.');
            hideProgressIndicator();
        });
}
