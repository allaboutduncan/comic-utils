<script>
/* ============================================================
 *  File-manager JS (combined files.js + updated.js)
 * ============================================================
 */

/* ---------- global state ---------- */
let currentSourcePath        = '/data';
let currentDestinationPath   = '/data';
let deleteTarget             = '';
let deletePanel              = '';        // 'source' | 'destination'
let selectedFiles            = new Set(); // highlighted <li> paths
let lastClickedFile          = null;      // for SHIFT-range selection
let sourceDirectoriesData    = null;      // last payload for each panel
let destinationDirectoriesData = null;
let currentFilter            = { source: 'all', destination: 'all' };

/* ---------- helpers ---------- */
function normalizeFile(f) {                 // accepts "string" or {name,size,path}
  return (typeof f === 'object' && (f.name || f.path))
    ? f
    : { name: f, size: null, path: f };
}
function formatSize(bytes) {
  const units = ['B','KB','MB','GB','TB'];
  if (bytes == null || bytes === 0) return '0 B';
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
 *  SINGLE-FILE MOVE (modal + progress)
 * ============================================================
 */
function moveSingleItem(sourcePath, targetFolder) {
  const f = normalizeFile(sourcePath);
  const actual = f.path || f.name;
  const fileName = actual.split('/').pop();

  showMovingModal();
  setMovingStatus(`Moving ${fileName}`);
  updateMovingProgress(35); // initial progress

  moveItem(actual, `${targetFolder}/${fileName}`)
    .then(res => {
      if (!res.success) alert(res.error || 'Move failed.');
      updateMovingProgress(100);
    })
    .catch(err => {
      console.error(err);
      alert('Error moving file.');
    })
    .finally(() => {
      setTimeout(() => { hideMovingModal(); reloadBothPanels(); }, 400);
    });
}

/* ============================================================
 *  MULTI-FILE MOVE (loop fetch)
 * ============================================================
 */
function moveMultipleItems(paths, targetFolder, panel) {
  showMovingModal();
  const total = paths.length;
  let index = 0;

  function loop() {
    if (index >= total) {
      hideMovingModal(); reloadBothPanels(); return;
    }
    const f = normalizeFile(paths[index]);
    const source = f.path || f.name;
    const fileName = source.split('/').pop();

    setMovingStatus(`Moving ${fileName} (${index+1}/${total})`);
    updateMovingProgress(Math.floor((index / total) * 100));

    moveItem(source, `${targetFolder}/${fileName}`)
      .catch(err => alert(err?.error ?? 'Move failed'))
      .finally(() => { index++; loop(); });
  }
  loop();
}

/* ============================================================
 *  UI helpers (modal / progress / refresh)
 * ============================================================
 */
const movingModalEl    = document.getElementById('movingModal');
const movingModal      = new bootstrap.Modal(movingModalEl,{ backdrop:'static', keyboard:false });
const movingStatusText = document.getElementById('movingStatusText');
const movingBar        = document.getElementById('movingProgressBar');

function showMovingModal() { movingStatusText.textContent=''; updateMovingProgress(0); movingModal.show(); }
function hideMovingModal() { movingModal.hide(); }
function setMovingStatus(msg){ movingStatusText.textContent = msg; }
function updateMovingProgress(pct){ movingBar.style.width = pct+'%'; movingBar.setAttribute('aria-valuenow',pct); }
function reloadBothPanels(){ loadDirectories(currentSourcePath,'source'); loadDirectories(currentDestinationPath,'destination'); }
function refreshPanels(panel, ok){ if(ok) reloadBothPanels(); }

/* ============================================================
 *  List item builder (merged createListItem)
 * ============================================================
 */
function createListItem(itemName, fullPath, type, panel, isDraggable) {
  const li = document.createElement('li');
  li.className = 'list-group-item d-flex align-items-center justify-content-between';

  // inner formatSize
  function _fmtSize(b){ return formatSize(b); }

  const fd = normalizeFile(itemName);
  const name = fd.name;

  // Left icon + name
  const left = document.createElement('div'); left.className='d-flex align-items-center';
  if (name.toLowerCase()!=='parent') {
    const ico = document.createElement('i');
    ico.className = (type==='directory'?'bi bi-folder me-2':'bi bi-file-earmark-zip me-2');
    if(type==='directory') ico.style.color='#bf9300';
    left.appendChild(ico);
  }
  const span = document.createElement('span');
  if(type==='file' && fd.size!=null) span.innerHTML = `${name} <span class="text-info-emphasis small ms-2">(${_fmtSize(fd.size)})</span>`;
  else span.textContent = name;
  left.appendChild(span);

  // Right icons container
  const right = document.createElement('div'); right.className='icon-container';

  // directory-size info
  if(type==='directory'){
    const wrap = document.createElement('span'); wrap.className='me-2';
    const infoI = document.createElement('i');
    infoI.className='bi bi-info-circle'; infoI.style.cursor='pointer';
    const sizeSpan = document.createElement('span'); sizeSpan.className='text-info-emphasis small ms-2';
    infoI.onclick = e=>{
      e.stopPropagation();
      infoI.className='spinner-border spinner-border-sm';
      fetch(`/folder-size?path=${encodeURIComponent(fullPath)}`)
        .then(r=>r.json()).then(d=> sizeSpan.textContent = d.size!=null?`(${formatSize(d.size)})`:'(error)')
        .catch(_=> sizeSpan.textContent='(error)')
        .finally(()=> infoI.className='bi bi-info-circle');
    };
    wrap.appendChild(infoI); wrap.appendChild(sizeSpan);
    right.appendChild(wrap);
  }

  // rename & delete
  if(name!=='Parent'){
    const pen = document.createElement('i'); pen.className='bi bi-pencil'; pen.style.cursor='pointer';
    pen.onclick = e=>{
      e.stopPropagation();
      const inp = document.createElement('input');
      inp.type='text'; inp.className='edit-input'; inp.value=name;
      inp.addEventListener('click',ev=>ev.stopPropagation());
      inp.addEventListener('keypress',ev=>{ if(ev.key==='Enter') renameItem(fullPath,inp.value,panel); });
      li.replaceChild(inp,left);
      inp.focus(); inp.addEventListener('blur',()=>li.replaceChild(left,inp));
    };
    const del = document.createElement('i'); del.className='bi bi-trash'; del.style.cursor='pointer';
    del.onclick = e=>{
      e.stopPropagation(); deleteTarget=fullPath; deletePanel=panel;
      document.getElementById('deleteItemName').textContent=name;
      new bootstrap.Modal(document.getElementById('deleteModal')).show();
    };
    right.appendChild(pen); right.appendChild(del);
  }

  li.appendChild(left); li.appendChild(right);

  // selection for files
  if(type==='file'){
    li.setAttribute('data-fullpath',fullPath);
    li.addEventListener('click',e=>{
      if(e.ctrlKey||e.metaKey){
        e.preventDefault();
        if(selectedFiles.has(fullPath)){ selectedFiles.delete(fullPath); li.classList.remove('selected'); }
        else{ selectedFiles.add(fullPath); li.classList.add('selected'); }
        lastClickedFile=li;
      } else if(e.shiftKey){
        const items = Array.from(li.parentNode.querySelectorAll('li.list-group-item'))
          .filter(x=>x.getAttribute('data-fullpath'));
        let s = items.indexOf(lastClickedFile||li), eI = items.indexOf(li);
        const [min,iMax] = s<eI?[s,eI]:[eI,s];
        items.slice(min,iMax+1).forEach(x=>{ const p=x.getAttribute('data-fullpath'); selectedFiles.add(p); x.classList.add('selected'); });
      } else {
        selectedFiles.clear(); document.querySelectorAll('li.selected').forEach(x=>x.classList.remove('selected'));
        selectedFiles.add(fullPath); li.classList.add('selected'); lastClickedFile=li;
      }
      e.stopPropagation();
    });
    li.addEventListener('contextmenu',e=>e.preventDefault());
  }

  // directory navigation + drop
  if(type==='directory'){
    li.onclick = ()=>{ currentFilter[panel]='all'; loadDirectories(fullPath,panel); };
    if(name.toLowerCase()!=='parent'){
      li.addEventListener('dragover',e=>{ e.preventDefault(); e.stopPropagation(); li.classList.add('folder-hover'); });
      li.addEventListener('dragleave',e=>{ e.stopPropagation(); li.classList.remove('folder-hover'); });
      li.addEventListener('drop',e=>{
        e.preventDefault(); e.stopPropagation(); li.classList.remove('folder-hover');
        let items; try{ items=JSON.parse(e.dataTransfer.getData('text/plain')); if(!Array.isArray(items)) items=[items]; }
        catch{ items=[{path:e.dataTransfer.getData('text/plain'),type:'unknown'}]; }
        const valid = items.filter(it=>it.path.substring(0,it.path.lastIndexOf('/'))!==fullPath);
        if(!valid.length) return;
        if(valid.length===1 && valid[0].type==='file') moveSingleItem(valid[0].path,fullPath);
        else moveMultipleItems(valid.map(x=>x.path),fullPath,panel);
        selectedFiles.clear();
      });
    }
  }

  // dragstart payload
  if(isDraggable){
    li.classList.add('draggable'); li.draggable=true;
    li.addEventListener('dragstart',e=>{
      if(type==='file'){
        if(!selectedFiles.has(fullPath)){
          selectedFiles.clear(); document.querySelectorAll('li.selected').forEach(x=>x.classList.remove('selected'));
          selectedFiles.add(fullPath); li.classList.add('selected');
        }
        e.dataTransfer.setData('text/plain', JSON.stringify([...selectedFiles].map(p=>({path:p,type:'file'}))));
      } else {
        e.dataTransfer.setData('text/plain', JSON.stringify([{path:fullPath,type:'directory'}]));
      }
    });
  }

  return li;
}

/* ============================================================
 *  Directory listing / UI builders
 * ============================================================
 */
function updateFilterBar(panel, dirs) {
  const grp = document.getElementById(`${panel}-directory-filter`).querySelector('.btn-group');
  const letters = new Set(); let non = false;
  dirs.forEach(d=>{ const c=d[0].toUpperCase(); (/[A-Z]/.test(c)?letters.add(c):non=true); });
  let html = `<button class="btn btn-outline-secondary ${currentFilter[panel]==='all'?'active':''}" onclick="filterDirectories('all','${panel}')">All</button>`;
  if(non) html += `<button class="btn btn-outline-secondary ${currentFilter[panel]==='#'?'active':''}" onclick="filterDirectories('#','${panel}')">#</button>`;
  for(let i=65;i<=90;i++){ const L=String.fromCharCode(i); if(letters.has(L)) html+=`<button class="btn btn-outline-secondary ${currentFilter[panel]===L?'active':''}" onclick="filterDirectories('${L}','${panel}')">${L}</button>`; }
  grp.innerHTML = html;
}

function loadDirectories(path,panel){
  document.getElementById('btnDirectories').classList.add('active');
  document.getElementById('btnDownloads').classList.remove('active');
  const cont = document.getElementById(panel==='source'?'source-list':'destination-list');
  cont.innerHTML = `<div class="d-flex justify-content-center my-3"><button class="btn btn-primary" disabled><span class="spinner-grow spinner-grow-sm"></span> Loading...</button></div>`;
  fetch(`/list-directories?path=${encodeURIComponent(path)}`)
    .then(r=>r.json()).then(data=>{
      if(panel==='source'){ currentSourcePath=data.current_path; sourceDirectoriesData=data; }
      else { currentDestinationPath=data.current_path; destinationDirectoriesData=data; }
      updateBreadcrumb(panel,data.current_path);
      updateFilterBar(panel,data.directories);
      renderDirectoryListing(data,panel);
    }).catch(e=>{ console.error(e); cont.innerHTML='<div class="alert alert-danger">Error loading directory.</div>'; });
}

function renderDirectoryListing(data,panel){
  const cont=document.getElementById(panel==='source'?'source-list':'destination-list'); cont.innerHTML='';
  if(data.parent){ const pI=createListItem('Parent',data.parent,'directory',panel,false); pI.querySelector('span').innerHTML='<i class="bi bi-arrow-left-square me-2"></i> Parent'; cont.appendChild(pI); }
  const dirs = data.directories.filter(d=>{ const f=currentFilter[panel]; if(f==='all') return true; if(f==='#') return !/^[A-Za-z]/.test(d); return d[0].toUpperCase()===f; });
  dirs.forEach(d=>cont.appendChild(createListItem(d,`${data.current_path}/${d}`,'directory',panel,true)));
  if(currentFilter[panel]==='all') data.files.forEach(f=>cont.appendChild(createListItem(f,`${data.current_path}/${f}`,'file',panel,true)));
  if(panel==='destination'&&data.directories.length===0&&data.files.length===0){
    const dt=document.createElement('li'); dt.className='list-group-item text-center drop-target-item'; dt.textContent='... Drop Files Here';
    dt.addEventListener('dragover',e=>{e.preventDefault(); dt.classList.add('folder-hover');});
    dt.addEventListener('dragleave',e=>{ dt.classList.remove('folder-hover'); });
    dt.addEventListener('drop',e=>{
      e.preventDefault(); dt.classList.remove('folder-hover');
      let items; try{ items=JSON.parse(e.dataTransfer.getData('text/plain')); if(!Array.isArray(items)) items=[items]; }catch{ items=[{path:e.dataTransfer.getData('text/plain'),type:'unknown'}]; }
      const paths=items.map(i=>i.path);
      moveMultipleItems(paths,data.current_path,panel);
      selectedFiles.clear();
    });
    cont.appendChild(dt);
  }
}

function filterDirectories(letter,panel){
  currentFilter[panel] = currentFilter[panel]===letter?'all':letter;
  document.querySelectorAll(`#${panel}-directory-filter .btn`).forEach(b=>{
    b.classList.toggle('active', b.textContent===letter || (letter==='all'&&b.textContent==='All'));
  });
  if(panel==='source'&&sourceDirectoriesData) renderDirectoryListing(sourceDirectoriesData,panel);
  if(panel==='destination'&&destinationDirectoriesData) renderDirectoryListing(destinationDirectoriesData,panel);
}

function loadDownloads(path,panel){
  document.getElementById('btnDownloads').classList.add('active');
  document.getElementById('btnDirectories').classList.remove('active');
  const cont=document.getElementById(panel==='source'?'source-list':'destination-list');
  cont.innerHTML=`<div class=\"d-flex justify-content-center my-3\"><button class=\"btn btn-primary\" disabled><span class=\"spinner-grow spinner-grow-sm\"></span> Loading...</button></div>`;
  fetch(`/list-downloads?path=${encodeURIComponent(path)}`)
    .then(r=>r.json()).then(data=>{
      cont.innerHTML='';
      if(panel==='source'){ currentSourcePath=data.current_path; updateBreadcrumb('source',data.current_path); }
      else { currentDestinationPath=data.current_path; updateBreadcrumb('destination',data.current_path); }
      if(data.parent){ const pI=createListItem('Parent',data.parent,'directory',panel,false); pI.querySelector('span').innerHTML='<i class="bi bi-arrow-left-square me-2"></i> Parent'; cont.appendChild(pI);}      
      data.directories.forEach(d=>cont.appendChild(createListItem(d,`${data.current_path}/${d}`,'directory',panel,true)));
      data.files.forEach(f=>cont.appendChild(createListItem(f,`${data.current_path}/${f}`,'file',panel,true)));
    }).catch(e=>{ console.error(e); cont.innerHTML='<div class="alert alert-danger">Error loading downloads.</div>'; });
}

/* ============================================================
 *  Breadcrumb & filter utilities
 * ============================================================
 */
function updateBreadcrumb(panel,path){
  const bc=document.getElementById(panel==='source'?'source-path-display':'destination-path-display'); bc.innerHTML='';
  let soFar=''; path.split('/').filter(Boolean).forEach((part,i,arr)=>{
    soFar+=`/${part}`; const li=document.createElement('li'); li.className='breadcrumb-item';
    if(i===arr.length-1){ li.classList.add('active'); li.setAttribute('aria-current','page'); li.textContent=part; }
    else{ const a=document.createElement('a'); a.href='#'; a.textContent=part; a.onclick=e=>{e.preventDefault(); loadDirectories(soFar,panel);}; li.appendChild(a);} bc.appendChild(li);
  });
}

/* ============================================================
 *  Drop-target setup (full hover/scroll + updated drop)
 * ============================================================
 */
function setupDropEvents(el,panel){
  let interval=null;
  function start(dir){ if(interval)return; interval=setInterval(()=>{ el.scrollTop += dir==='down'?5:-5; },50); }
  function stop(){ clearInterval(interval); interval=null; }
  el.addEventListener('dragover',e=>{
    e.preventDefault(); el.classList.add('hover');
    const r=el.getBoundingClientRect(); const t=50;
    if(e.clientY-r.top< t) start('up');
    else if(r.bottom-e.clientY< t) start('down');
    else stop();
  });
  el.addEventListener('dragleave',e=>{ el.classList.remove('hover'); stop(); });
  el.addEventListener('drop',e=>{
    e.preventDefault(); el.classList.remove('hover'); stop();
    let items; try{ items=JSON.parse(e.dataTransfer.getData('text/plain')); if(!Array.isArray(items)) items=[items]; }catch{ items=[{path:e.dataTransfer.getData('text/plain'),type:'unknown'}]; }
    const target = panel==='source'?currentSourcePath:currentDestinationPath;
    const valid = items.filter(it=>it.path.substring(0,it.path.lastIndexOf('/'))!==target);
    if(!valid.length) return;
    if(valid.length===1 && valid[0].type==='file') moveSingleItem(valid[0].path,target);
    else moveMultipleItems(valid.map(x=>x.path),target,panel);
    selectedFiles.clear();
  });
}

/* ============================================================
 *  Create-folder modal handlers
 * ============================================================
 */
function openCreateFolderModal(){ document.getElementById('createFolderName').value=''; new bootstrap.Modal(document.getElementById('createFolderModal')).show(); }
function createFolder(){
  const nm=document.getElementById('createFolderName').value.trim(); if(!nm){ alert('Folder name cannot be empty.'); return; }
  const full=`${currentDestinationPath}/${nm}`;
  fetch('/create-folder',{ method:'POST', headers:{'Content-Type':'application/json'}, body:JSON.stringify({path:full}) })
    .then(r=>r.json()).then(d=>{ if(d.success){ reloadBothPanels(); } else alert(d.error||'Error creating folder'); })
    .catch(_=>alert('Unexpected error.'));
}

/* ============================================================
 *  Delete confirmation handler & key listeners
 * ============================================================
 */
document.getElementById('confirmDeleteBtn').addEventListener('click',()=>{
  const modalEl=document.getElementById('deleteModal');
  bootstrap.Modal.getInstance(modalEl).hide(); deleteItem(deleteTarget,deletePanel);
});
document.getElementById('createFolderName').addEventListener('keypress',e=>{ if(e.key==='Enter'){ e.preventDefault(); createFolder(); }});

/* ============================================================
 *  Boot-up: attach drop events & initial loads
 * ============================================================
 */
setupDropEvents(document.getElementById('source-list'),'source');
setupDropEvents(document.getElementById('destination-list'),'destination');
loadDirectories(currentSourcePath,'source');
loadDirectories(currentDestinationPath,'destination');
</script>
