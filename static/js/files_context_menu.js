// ===========================
// Multi-file Context Menu Functions
// ===========================

let contextMenuPanel = null;

// Update the selection badge
function updateSelectionBadge() {
  const badge = document.getElementById('selectionBadge');
  const countSpan = document.getElementById('selectionCount');

  if (!badge || !countSpan) return;

  const count = selectedFiles.size;

  if (count > 0) {
    countSpan.textContent = `${count} selected`;
    badge.classList.add('show');
  } else {
    badge.classList.remove('show');
  }
}

// Show context menu for multi-file selection
function showFileContextMenu(event, panel) {
  const menu = document.getElementById('fileContextMenu');
  contextMenuPanel = panel;

  // Position the menu at the cursor
  menu.style.display = 'block';
  menu.style.left = event.pageX + 'px';
  menu.style.top = event.pageY + 'px';

  // Hide menu when clicking elsewhere
  setTimeout(() => {
    document.addEventListener('click', hideFileContextMenu);
  }, 10);
}

// Hide context menu
function hideFileContextMenu() {
  const menu = document.getElementById('fileContextMenu');
  menu.style.display = 'none';
  document.removeEventListener('click', hideFileContextMenu);
}

// Extract series name from filename
function extractSeriesName(filename) {
  // Remove file extension
  let name = filename.replace(/\.(cbz|cbr|pdf)$/i, '');

  // Remove common patterns: issue numbers, years, volume numbers
  // Pattern: Series Name 001 (2023) or Series Name #1 or Series Name v1 001
  name = name.replace(/\s+#?\d+\s*\(\d{4}\).*$/i, '');  // Remove " 001 (2023)" and everything after
  name = name.replace(/\s+#?\d+.*$/i, '');               // Remove " 001" or " #1" and everything after
  name = name.replace(/\s+v\d+.*$/i, '');                // Remove " v1" and everything after
  name = name.replace(/\s+\(\d{4}\).*$/i, '');           // Remove " (2023)" and everything after

  return name.trim();
}

// Get the most common series name from selected files
function getMostCommonSeriesName(filePaths) {
  const seriesCount = {};

  filePaths.forEach(path => {
    const filename = path.split('/').pop();
    const seriesName = extractSeriesName(filename);

    if (seriesName) {
      seriesCount[seriesName] = (seriesCount[seriesName] || 0) + 1;
    }
  });

  // Find the most common series name
  let maxCount = 0;
  let mostCommon = null;

  for (const [series, count] of Object.entries(seriesCount)) {
    if (count > maxCount) {
      maxCount = count;
      mostCommon = series;
    }
  }

  return mostCommon;
}

// Create folder with selected files
function createFolderWithSelection() {
  hideFileContextMenu();

  if (selectedFiles.size === 0) {
    alert('No files selected');
    return;
  }

  const filePaths = Array.from(selectedFiles);
  const seriesName = getMostCommonSeriesName(filePaths);

  if (!seriesName) {
    alert('Could not determine series name from selected files');
    return;
  }

  // Get the directory of the first selected file
  const firstFilePath = filePaths[0];
  const parentDir = firstFilePath.substring(0, firstFilePath.lastIndexOf('/'));
  const newFolderPath = `${parentDir}/${seriesName}`;

  // Create the folder
  fetch('/create-folder', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ path: newFolderPath })
  })
  .then(response => response.json())
  .then(result => {
    if (result.success) {
      // Move all selected files to the new folder
      moveMultipleItems(filePaths, newFolderPath, contextMenuPanel);
      selectedFiles.clear();
      updateSelectionBadge();

      // Refresh the view
      if (contextMenuPanel === 'source') {
        loadDirectories(currentSourcePath, 'source');
      } else {
        loadDirectories(currentDestinationPath, 'destination');
      }
    } else {
      // If folder already exists, just move the files
      if (result.error && result.error.includes('exists')) {
        moveMultipleItems(filePaths, newFolderPath, contextMenuPanel);
        selectedFiles.clear();
        updateSelectionBadge();

        // Refresh the view
        if (contextMenuPanel === 'source') {
          loadDirectories(currentSourcePath, 'source');
        } else {
          loadDirectories(currentDestinationPath, 'destination');
        }
      } else {
        alert('Error creating folder: ' + result.error);
      }
    }
  })
  .catch(error => {
    console.error('Error creating folder:', error);
    alert('Error creating folder: ' + error.message);
  });
}

// Show delete confirmation modal for multiple files
function showDeleteMultipleConfirmation() {
  hideFileContextMenu();

  if (selectedFiles.size === 0) {
    alert('No files selected');
    return;
  }

  const filePaths = Array.from(selectedFiles);
  const fileNames = filePaths.map(path => path.split('/').pop());

  // Update modal content
  document.getElementById('deleteMultipleCount').textContent = filePaths.length;

  const fileList = document.getElementById('deleteMultipleFileList');
  fileList.innerHTML = '';

  fileNames.forEach(name => {
    const li = document.createElement('li');
    li.className = 'list-group-item';
    li.textContent = name;
    fileList.appendChild(li);
  });

  // Show modal
  const modal = new bootstrap.Modal(document.getElementById('deleteMultipleModal'));
  modal.show();
}

// Delete multiple selected files
function deleteMultipleFiles() {
  const filePaths = Array.from(selectedFiles);

  // Close the modal
  const modal = bootstrap.Modal.getInstance(document.getElementById('deleteMultipleModal'));
  if (modal) modal.hide();

  // Delete each file
  let deletePromises = filePaths.map(filePath => {
    return fetch('/delete', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ target: filePath })
    })
    .then(response => response.json())
    .then(result => {
      if (result.success) {
        // Remove from UI
        const container = contextMenuPanel === 'source'
          ? document.getElementById('source-list')
          : document.getElementById('destination-list');
        const item = container.querySelector(`li[data-fullpath="${filePath}"]`);
        if (item) {
          item.classList.add('deleting');
          setTimeout(() => item.remove(), 200);
        }
        return { success: true, path: filePath };
      } else {
        return { success: false, path: filePath, error: result.error };
      }
    })
    .catch(error => {
      return { success: false, path: filePath, error: error.message };
    });
  });

  Promise.all(deletePromises).then(results => {
    const failures = results.filter(r => !r.success);

    selectedFiles.clear();
    updateSelectionBadge();
    document.querySelectorAll('li.list-group-item.selected').forEach(item => {
      item.classList.remove('selected');
      item.removeAttribute('data-selection-hint');
    });

    if (failures.length > 0) {
      alert(`${failures.length} file(s) failed to delete. Check console for details.`);
      console.error('Failed deletions:', failures);
    }

    // Refresh the view
    if (contextMenuPanel === 'source') {
      loadDirectories(currentSourcePath, 'source');
    } else {
      loadDirectories(currentDestinationPath, 'destination');
    }
  });
}

// Initialize context menu event listeners
document.addEventListener('DOMContentLoaded', function() {
  const contextCreateFolder = document.getElementById('contextCreateFolder');
  const contextDeleteFiles = document.getElementById('contextDeleteFiles');
  const confirmDeleteMultipleBtn = document.getElementById('confirmDeleteMultipleBtn');

  if (contextCreateFolder) {
    contextCreateFolder.addEventListener('click', function(e) {
      e.preventDefault();
      createFolderWithSelection();
    });
  }

  if (contextDeleteFiles) {
    contextDeleteFiles.addEventListener('click', function(e) {
      e.preventDefault();
      showDeleteMultipleConfirmation();
    });
  }

  if (confirmDeleteMultipleBtn) {
    confirmDeleteMultipleBtn.addEventListener('click', function() {
      deleteMultipleFiles();
    });
  }
});
