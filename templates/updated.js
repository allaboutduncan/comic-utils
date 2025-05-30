<script>
/* ============================================================
 *  File-manager JS (full file)
 * ============================================================
 */

/* ---------- global state ---------- */
let currentSourcePath        = '/data';
let currentDestinationPath   = '/data';
let deleteTarget             = '';
let deletePanel              = '';        // 'source' | 'destination'
let selectedFiles            = new Set(); // currently highlighted <li> paths
let lastClickedFile          = null;      // for SHIFT-range
let sourceDirectoriesData    = null;      // last payload for the panel
let destinationDirectoriesData = null;
let currentFilter            = { source: 'all', destination: 'all' };

/* ---------- helper ---------- */
function normalizeFile(f) {                 // accepts "string" or {name,size,path}
  return (typeof f === 'object' && (f.name || f.path))
    ? f
    : { name: f, size: null };
}
function formatSize(bytes) {
  const units = ['B','KB','MB','GB','TB'];
  if (bytes === 0 || bytes == null) return '0 B';
  const i = Math.floor(Math.log(bytes) / Math.log(1024));
  return (bytes / 1024 ** i).toFixed(1) + ' ' + units[i];
}

/* ============================================================
 *  REST helpers (rename / delete / move / create-folder)
 * ============================================================
 */
function renameItem(oldPath, newName, panel) {
  const parts = oldPath.split('/');
  parts[parts.length - 1] = newName;
  fetch('/rename', {
    method : 'POST',
    headers: { 'Content-Type':'application/json' },
    body   : JSON.stringify({ old: oldPath, new: parts.join('/') })
  })
  .then(r => r.json())
  .then(r => refreshPanels(panel, r.success))
  .catch(console.error);
}

function deleteItem(target, panel) {
  fetch('/delete', {
    method : 'POST',
    headers: { 'Content-Type':'application/json' },
    body   : JSON.stringify({ target })
  })
  .then(r => r.json())
  .then(r => refreshPanels(panel, r.success))
  .catch(console.error);
}

function moveItem(source, destination) {
  return fetch('/move', {
    method : 'POST',
    headers: { 'Content-Type':'application/json' },
    body   : JSON.stringify({ source, destination })
  }).then(r => r.json());
}

/* ============================================================
 *  SINGLE-FILE MOVE  (spinner, no SSE)
 * ============================================================
 */
function moveSingleItem(sourcePath, targetFolder) {
  const actualPath = typeof sourcePath === 'object'
                   ? (sourcePath.path || sourcePath.name)
                   : sourcePath;
  const fileName   = actualPath.split('/').pop();

  /* modal prep */
  showMovingModal();
  setMovingStatus(`Moving ${fileName}`);
  updateMovingProgress(35);               // indeterminate feel

  moveItem(actualPath, `${targetFolder}/${fileName}`)
    .then(res => {
      if (!res.success) alert(res.error || 'Move failed.');
      updateMovingProgress(100);
    })
    .catch(err => {
      console.error(err);
      alert('Error moving file.');
    })
    .finally(() => {
      setTimeout(() => {
        hideMovingModal();
        reloadBothPanels();
      }, 400);
    });
}

/* ============================================================
 *  MULTI-FILE MOVE (loop fetch)
 * ============================================================
 */
function moveMultipleItems(paths, targetFolder, panel) {
  showMovingModal();
  const total = paths.length;
  let index   = 0;

  function loop() {
    if (index >= total) {
      hideMovingModal();
      reloadBothPanels();
      return;
    }
    const fileObj   = normalizeFile(paths[index]);
    const source    = typeof fileObj === 'string'
                    ? fileObj
                    : (fileObj.path || fileObj.name);
    const fileName  = source.split('/').pop();

    setMovingStatus(`Moving ${fileName} (${index+1}/${total})`);
    updateMovingProgress(Math.floor((index / total) * 100));

    moveItem(source, `${targetFolder}/${fileName}`)
      .catch(err => alert(err?.error ?? 'Move failed'))
      .finally(() => { index++; loop(); });
  }
  loop();
}

/* ============================================================
 *  UI helpers  (modal / progress / refresh)
 * ============================================================
 */
const movingModalEl    = document.getElementById('movingModal');
const movingModal      = new bootstrap.Modal(movingModalEl,{backdrop:'static',keyboard:false});
const movingStatusText = document.getElementById('movingStatusText');
const movingBar        = document.getElementById('movingProgressBar');

function showMovingModal() { movingStatusText.textContent=''; updateMovingProgress(0); movingModal.show(); }
function hideMovingModal() { movingModal.hide(); }
function setMovingStatus(msg){ movingStatusText.textContent = msg; }
function updateMovingProgress(pct){
  movingBar.style.width = pct+'%';
  movingBar.ariaValueNow = pct;
}
function reloadBothPanels(){
  loadDirectories(currentSourcePath,'source');
  loadDirectories(currentDestinationPath,'destination');
}
function refreshPanels(panel, ok){
  if(!ok) return;
  if(panel === 'source')   loadDirectories(currentSourcePath,'source');
  else                     loadDirectories(currentDestinationPath,'destination');
}

/* ============================================================
 *  Directory list / UI builder  (createListItem etc.)
 *  (only the parts that changed are annotated; rest is original)
 * ============================================================
 */
function createListItem(itemName, fullPath, type, panel, isDraggable) {
  /* ——— same as your original until dragstart ——— */
  /* (body left unchanged for brevity in this comment; keep yours) */

  /* DRAGSTART — attach payload */
  if (isDraggable) {
    li.classList.add('draggable');
    li.draggable = true;
    li.addEventListener('dragstart', e=>{
      if(type==='file'){
        if(!selectedFiles.has(fullPath)){
          selectedFiles.clear();
          document.querySelectorAll('li.selected').forEach(li=>li.classList.remove('selected'));
          selectedFiles.add(fullPath); li.classList.add('selected');
        }
        e.dataTransfer.setData('text/plain',
          JSON.stringify([...selectedFiles].map(p=>({path:p,type:'file'}))));
      }else{
        e.dataTransfer.setData('text/plain', JSON.stringify([{path:fullPath,type:'directory'}]));
      }
    });
  }
  return li;
}

/* ============================================================
 *  Panel loading / filtering / downloads  (unchanged except
 *  calling reloadBothPanels instead of duplicated code)
 * ============================================================
 */

/* ------- loadDirectories(), renderDirectoryListing(), etc. -------
 * (keep your existing definitions – unchanged from the snippet you
 *   provided; they work with the new moveSingleItem/moveMultipleItems)
 */

/* ============================================================
 *  Drop-targets (uses new move* functions)
 * ============================================================
 */
function setupDropEvents(el, panel){
  /* identical hover/scroll code ... */
  el.addEventListener('drop', e=>{
    e.preventDefault(); el.classList.remove('hover'); /* stop scroll */
    let items;
    try{ items = JSON.parse(e.dataTransfer.getData('text/plain')); }
    catch{ items=[{path:e.dataTransfer.getData('text/plain'),type:'unknown'}]; }

    const target = panel==='source'?currentSourcePath:currentDestinationPath;
    const valid  = items.filter(it=>it.path.substring(0,it.path.lastIndexOf('/'))!==target);
    if(!valid.length) return;

    if(valid.length===1 && valid[0].type==='file')
         moveSingleItem(valid[0].path, target);
    else moveMultipleItems(valid.map(it=>it.path), target, panel);

    selectedFiles.clear();
  });
}

/* ============================================================
 *  Boot-up
 * ============================================================
 */
setupDropEvents(document.getElementById('source-list'),'source');
setupDropEvents(document.getElementById('destination-list'),'destination');
loadDirectories(currentSourcePath,'source');
loadDirectories(currentDestinationPath,'destination');

/* ============================================================
 *  Remaining original helpers (createFolder, filter bar, etc.)
 *  are unchanged – include them from your latest working copy.
 * ============================================================
 */
</script>
