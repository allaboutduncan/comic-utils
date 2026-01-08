/**
 * Series page JavaScript - handles directory browsing and series mapping
 */

// Global state
let modalDirectoryData = null;
let currentModalFilter = 'all';
let currentPath = '/data';

/**
 * Open the directory mapping modal
 */
function openMappingModal() {
    // Start from mapped path parent or /data
    let startPath = '/data';
    if (currentMappedPath) {
        // Get parent directory of mapped path
        const lastSlash = currentMappedPath.lastIndexOf('/');
        if (lastSlash > 0) {
            startPath = currentMappedPath.substring(0, lastSlash);
        }
    }

    loadDirectories(startPath);
    const modal = new bootstrap.Modal(document.getElementById('directoryModal'));
    modal.show();
}

/**
 * Load directories from server
 */
function loadDirectories(path = '/data') {
    currentPath = path;

    const directoryList = document.getElementById('directory-list');
    directoryList.innerHTML = '<li class="list-group-item text-center text-muted"><span class="spinner-border spinner-border-sm me-2"></span>Loading...</li>';

    fetch(`/list-directories?path=${encodeURIComponent(path)}`)
        .then(response => response.json())
        .then(data => {
            modalDirectoryData = data;
            currentModalFilter = 'all';

            updateFilterBar(data.directories);
            renderDirectoryList(data, currentModalFilter);

            // Update path display
            document.getElementById('current-path-display').textContent = data.current_path;
        })
        .catch(error => {
            console.error('Error fetching directories:', error);
            directoryList.innerHTML = '<li class="list-group-item text-danger">Error loading directories</li>';
        });
}

/**
 * Update the alphabetical filter bar
 */
function updateFilterBar(directories) {
    const filterContainer = document.getElementById('directory-filter');
    if (!filterContainer) return;

    // Analyze available first letters
    let availableLetters = new Set();
    let hasNonAlpha = false;

    directories.forEach(dir => {
        const firstChar = dir.charAt(0).toUpperCase();
        if (firstChar >= 'A' && firstChar <= 'Z') {
            availableLetters.add(firstChar);
        } else {
            hasNonAlpha = true;
        }
    });

    // Build filter buttons
    let buttonsHtml = '<button type="button" class="btn btn-sm btn-outline-secondary active" onclick="filterDirectories(\'all\')">All</button>';

    if (hasNonAlpha) {
        buttonsHtml += '<button type="button" class="btn btn-sm btn-outline-secondary" onclick="filterDirectories(\'#\')">#</button>';
    }

    const sortedLetters = Array.from(availableLetters).sort();
    sortedLetters.forEach(letter => {
        buttonsHtml += `<button type="button" class="btn btn-sm btn-outline-secondary" onclick="filterDirectories('${letter}')">${letter}</button>`;
    });

    filterContainer.querySelector('.btn-group').innerHTML = buttonsHtml;
}

/**
 * Filter directories by letter
 */
function filterDirectories(filter) {
    currentModalFilter = filter;

    // Update button states
    const buttons = document.querySelectorAll('#directory-filter button');
    buttons.forEach(btn => {
        if (btn.textContent === filter || (filter === 'all' && btn.textContent === 'All')) {
            btn.classList.add('active');
        } else {
            btn.classList.remove('active');
        }
    });

    renderDirectoryList(modalDirectoryData, filter);
}

/**
 * Render the directory list
 */
function renderDirectoryList(data, filter) {
    const directoryList = document.getElementById('directory-list');
    directoryList.innerHTML = '';

    // Add "Go Back" option if there's a parent
    if (data.parent) {
        const backItem = document.createElement('li');
        backItem.className = 'list-group-item list-group-item-action d-flex align-items-center';
        backItem.style.cursor = 'pointer';
        backItem.innerHTML = '<i class="bi bi-arrow-left me-2 text-muted"></i><span class="text-muted">.. (Go Back)</span>';
        backItem.onclick = () => loadDirectories(data.parent);
        directoryList.appendChild(backItem);
    }

    // Filter directories
    let filteredDirs = data.directories || [];
    if (filter !== 'all') {
        if (filter === '#') {
            filteredDirs = filteredDirs.filter(dir => {
                const firstChar = dir.charAt(0).toUpperCase();
                return !(firstChar >= 'A' && firstChar <= 'Z');
            });
        } else {
            filteredDirs = filteredDirs.filter(dir => dir.charAt(0).toUpperCase() === filter);
        }
    }

    // Sort directories
    filteredDirs.sort((a, b) => a.localeCompare(b, undefined, { sensitivity: 'base' }));

    // Render directories
    filteredDirs.forEach(dir => {
        const item = document.createElement('li');
        item.className = 'list-group-item list-group-item-action d-flex justify-content-between align-items-center';

        // Left side - navigate into directory
        const leftDiv = document.createElement('div');
        leftDiv.className = 'd-flex align-items-center flex-grow-1';
        leftDiv.style.cursor = 'pointer';
        leftDiv.innerHTML = `<i class="bi bi-folder-fill me-2 text-warning"></i><span>${dir}</span>`;
        leftDiv.onclick = () => loadDirectories(data.current_path + '/' + dir);

        // Right side - select this directory
        const selectBtn = document.createElement('button');
        selectBtn.className = 'btn btn-sm btn-outline-success';
        selectBtn.innerHTML = '<i class="bi bi-check-lg"></i>';
        selectBtn.title = 'Select this folder';
        selectBtn.onclick = (e) => {
            e.stopPropagation();
            selectDirectory(data.current_path + '/' + dir);
        };

        item.appendChild(leftDiv);
        item.appendChild(selectBtn);
        directoryList.appendChild(item);
    });

    // Show message if no directories
    if (filteredDirs.length === 0 && !data.parent) {
        const emptyItem = document.createElement('li');
        emptyItem.className = 'list-group-item text-muted text-center';
        emptyItem.textContent = 'No directories found';
        directoryList.appendChild(emptyItem);
    }
}

/**
 * Select the current directory (from modal footer button)
 */
function selectCurrentDirectory() {
    selectDirectory(currentPath);
}

/**
 * Select a directory and save the mapping
 */
function selectDirectory(path) {
    if (!seriesData || !seriesData.id) {
        console.error('No series data available');
        alert('Error: Series data not available');
        return;
    }

    // Show loading state
    const modal = bootstrap.Modal.getInstance(document.getElementById('directoryModal'));
    const selectBtn = document.querySelector('#directoryModal .modal-footer .btn-primary');
    const originalText = selectBtn.innerHTML;
    selectBtn.disabled = true;
    selectBtn.innerHTML = '<span class="spinner-border spinner-border-sm me-1"></span>Saving...';

    // Save mapping via API
    fetch(`/api/series/${seriesData.id}/map`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
            mapped_path: path,
            series: seriesData
        })
    })
        .then(response => response.json())
        .then(data => {
            if (data.success) {
                // Update UI
                document.getElementById('mapped-path-display').innerHTML =
                    `<span class="text-success"><i class="bi bi-check-circle me-1"></i>${path}</span>`;

                // Update the button text and add remove/refresh buttons if not present
                const mappingCard = document.querySelector('#mapping .card-body');
                const buttonsDiv = mappingCard.querySelector('.d-flex.gap-2');

                // Check if remove button exists, if not add it
                if (!buttonsDiv.querySelector('.btn-outline-danger')) {
                    const removeBtn = document.createElement('button');
                    removeBtn.className = 'btn btn-outline-danger';
                    removeBtn.innerHTML = '<i class="bi bi-x-circle me-1"></i>Remove';
                    removeBtn.onclick = removeMappingConfirm;
                    buttonsDiv.insertBefore(removeBtn, buttonsDiv.firstChild);
                }

                // Update map button text
                const mapBtn = buttonsDiv.querySelector('.btn-outline-primary');
                mapBtn.innerHTML = '<i class="bi bi-folder-symlink me-1"></i>Change Location';

                // Check if refresh button exists, if not add it
                if (!buttonsDiv.querySelector('#refresh-btn')) {
                    const refreshBtn = document.createElement('button');
                    refreshBtn.className = 'btn btn-outline-success';
                    refreshBtn.id = 'refresh-btn';
                    refreshBtn.innerHTML = '<i class="bi bi-arrow-clockwise me-1"></i>Refresh';
                    refreshBtn.onclick = refreshCollectionStatus;
                    buttonsDiv.appendChild(refreshBtn);
                }

                // Close modal
                modal.hide();

                // Automatically check collection status
                setTimeout(() => refreshCollectionStatus(), 300);
            } else {
                alert('Failed to save mapping: ' + (data.error || 'Unknown error'));
            }
        })
        .catch(error => {
            console.error('Error saving mapping:', error);
            alert('Error saving mapping: ' + error.message);
        })
        .finally(() => {
            selectBtn.disabled = false;
            selectBtn.innerHTML = originalText;
        });
}

/**
 * Confirm removal of mapping
 */
function removeMappingConfirm() {
    if (!confirm('Remove the collection mapping for this series?')) {
        return;
    }

    if (!seriesData || !seriesData.id) {
        console.error('No series data available');
        return;
    }

    fetch(`/api/series/${seriesData.id}/mapping`, {
        method: 'DELETE'
    })
        .then(response => response.json())
        .then(data => {
            if (data.success) {
                // Update UI
                document.getElementById('mapped-path-display').innerHTML =
                    '<span class="text-muted">Not mapped to local collection</span>';

                // Remove the remove button
                const mappingCard = document.querySelector('#mapping .card-body');
                const buttonsDiv = mappingCard.querySelector('.d-flex.gap-2');
                const removeBtn = buttonsDiv.querySelector('.btn-outline-danger');
                if (removeBtn) {
                    removeBtn.remove();
                }

                // Update map button text
                const mapBtn = buttonsDiv.querySelector('.btn-outline-primary');
                mapBtn.innerHTML = '<i class="bi bi-folder-symlink me-1"></i>Map Location';
            } else {
                alert('Failed to remove mapping: ' + (data.error || 'Unknown error'));
            }
        })
        .catch(error => {
            console.error('Error removing mapping:', error);
            alert('Error removing mapping: ' + error.message);
        });
}


/**
 * Refresh the collection status without reloading the page
 */
function refreshCollectionStatus() {
    if (!seriesData || !seriesData.id) {
        console.error('No series data available');
        return;
    }

    const refreshBtn = document.getElementById('refresh-btn');
    const originalHtml = refreshBtn.innerHTML;

    // Show loading state
    refreshBtn.disabled = true;
    refreshBtn.innerHTML = '<span class="spinner-border spinner-border-sm me-1"></span>Checking...';

    fetch(`/api/series/${seriesData.id}/check-collection`)
        .then(response => response.json())
        .then(data => {
            if (data.success) {
                // Update table rows
                const tbody = document.querySelector('#issues tbody');
                if (tbody) {
                    tbody.querySelectorAll('tr').forEach(row => {
                        const issueNumCell = row.querySelector('td:first-child');
                        if (!issueNumCell) return;

                        // Extract issue number from cell text (e.g., "#1" -> "1")
                        const issueNumMatch = issueNumCell.textContent.match(/#(\S+)/);
                        if (!issueNumMatch) return;

                        const issueNum = issueNumMatch[1];
                        const status = data.issue_status[issueNum];

                        if (status) {
                            // Update row class
                            row.className = status.found ? 'table-success' : 'table-danger';

                            // Update cell content with icon
                            const iconClass = status.found ? 'check-circle-fill' : 'x-circle-fill';
                            const textClass = status.found ? 'text-success' : 'text-danger';
                            issueNumCell.className = `ps-4 fw-bold ${textClass}`;
                            issueNumCell.innerHTML = `<i class="bi bi-${iconClass} me-1"></i>#${issueNum}`;
                        }
                    });
                }

                // Update footer counts
                const footer = document.querySelector('#issues .card-footer small:first-child');
                if (footer) {
                    footer.innerHTML = `
                        <span class="text-success"><i class="bi bi-check-circle-fill me-1"></i>${data.found_count} found</span>
                        <span class="mx-2">|</span>
                        <span class="text-danger"><i class="bi bi-x-circle-fill me-1"></i>${data.missing_count} missing</span>
                    `;
                }

            } else {
                alert('Failed to refresh: ' + (data.error || 'Unknown error'));
            }
        })
        .catch(error => {
            console.error('Error refreshing collection status:', error);
            alert('Error refreshing: ' + error.message);
        })
        .finally(() => {
            refreshBtn.disabled = false;
            refreshBtn.innerHTML = originalHtml;
        });
}
