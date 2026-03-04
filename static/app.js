/* ================================================================
   Fontaine Bros. Digital Asset Manager — Client-side JS
   ================================================================ */

'use strict';

// ── Toast ──────────────────────────────────────────────────────
function showToast(msg, type = '') {
  const el = document.getElementById('toast');
  if (!el) return;
  el.textContent = msg;
  el.className   = 'toast' + (type ? ' ' + type : '');
  void el.offsetWidth;
  el.classList.add('show');
  clearTimeout(el._timer);
  el._timer = setTimeout(() => el.classList.remove('show'), 3500);
}

// ── API helpers ────────────────────────────────────────────────
async function apiPost(url, body) {
  const res = await fetch(url, {
    method:  'POST',
    headers: { 'Content-Type': 'application/json' },
    body:    JSON.stringify(body),
  });
  const data = await res.json();
  if (!res.ok) throw new Error(data.error || res.statusText);
  return data;
}

async function apiDelete(url) {
  const res = await fetch(url, { method: 'DELETE' });
  const data = await res.json();
  if (!res.ok) throw new Error(data.error || res.statusText);
  return data;
}

// ================================================================
//  NEW PROJECT MODAL
// ================================================================
function openNewProjectModal() {
  const modal = document.getElementById('newProjectModal');
  modal.classList.add('open');
  setTimeout(() => document.getElementById('newProjectName').focus(), 80);
}

function closeNewProjectModal(e) {
  if (e && e.target !== e.currentTarget) return;
  document.getElementById('newProjectModal').classList.remove('open');
  document.getElementById('newProjectName').value = '';
}

async function submitNewProject() {
  const input = document.getElementById('newProjectName');
  const name  = input.value.trim();
  if (!name) { input.focus(); return; }

  try {
    const res = await apiPost('/api/projects/create', { name });
    window.location.href = res.redirect;
  } catch (err) {
    showToast('❌ ' + err.message, 'error');
  }
}

document.addEventListener('keydown', e => {
  if (e.key === 'Escape') {
    const modal = document.getElementById('newProjectModal');
    if (modal && modal.classList.contains('open')) closeNewProjectModal();
  }
});

// ================================================================
//  PROJECT DETAIL — Edit / Save
// ================================================================
let _snap = {};

function startEdit() {
  _snap = {};
  document.querySelectorAll('.field-edit').forEach(el => {
    _snap[el.name] = el.value;
  });
  document.body.classList.add('editing');
  toggle('editBtn', false); toggle('saveBtn', true); toggle('cancelBtn', true);
}

function cancelEdit() {
  document.querySelectorAll('.field-edit').forEach(el => {
    if (_snap.hasOwnProperty(el.name)) el.value = _snap[el.name];
  });
  document.body.classList.remove('editing');
  toggle('editBtn', true); toggle('saveBtn', false); toggle('cancelBtn', false);
}

async function saveProject() {
  if (typeof PROJECT_ID === 'undefined') return;

  const data = {};
  document.querySelectorAll('.field-edit').forEach(el => { data[el.name] = el.value; });

  const btn = document.getElementById('saveBtn');
  btn.disabled   = true;
  btn.textContent = 'Saving…';

  try {
    await apiPost(`/api/project/${PROJECT_ID}/save`, data);

    // update read-only view
    document.querySelectorAll('.field-edit').forEach(el => {
      const view = el.closest('.info-field')?.querySelector('.field-view');
      if (view) view.textContent = el.value.trim() || '—';
    });

    document.body.classList.remove('editing');
    toggle('editBtn', true); toggle('saveBtn', false); toggle('cancelBtn', false);
    showToast('✅ Project saved!', 'success');
  } catch (err) {
    showToast('❌ ' + err.message, 'error');
  } finally {
    btn.disabled   = false;
    btn.textContent = '💾 Save Changes';
  }
}

function toggle(id, show) {
  const el = document.getElementById(id);
  if (el) el.style.display = show ? 'inline-flex' : 'none';
}

// ================================================================
//  PEOPLE
// ================================================================
function showAddPerson(cat) {
  document.getElementById(`add-${cat}`).style.display = 'flex';
  document.getElementById(`${cat}-name`).focus();
}
function hideAddPerson(cat) {
  document.getElementById(`add-${cat}`).style.display = 'none';
  document.getElementById(`${cat}-name`).value = '';
  document.getElementById(`${cat}-role`).value = '';
}

async function addPerson(cat) {
  const nameEl = document.getElementById(`${cat}-name`);
  const roleEl = document.getElementById(`${cat}-role`);
  const name   = nameEl.value.trim();
  if (!name) { nameEl.focus(); nameEl.style.borderColor = 'var(--red)'; setTimeout(() => nameEl.style.borderColor = '', 1500); return; }

  try {
    const res  = await apiPost(`/api/project/${PROJECT_ID}/people/add`,
                               { category: cat, name, role: roleEl.value.trim() });
    const listId  = cat === 'engineer' ? 'engineers-list' : 'team_member-list';
    const emptyId = cat === 'engineer' ? 'engineers-empty' : 'team_member-empty';
    const list    = document.getElementById(listId);
    document.getElementById(emptyId)?.remove();

    const chip = document.createElement('div');
    chip.className  = 'person-chip';
    chip.dataset.id = res.id;
    chip.innerHTML  = `
      <div class="person-info">
        <span class="person-name">${esc(name)}</span>
        ${roleEl.value.trim() ? `<span class="person-role">${esc(roleEl.value.trim())}</span>` : ''}
      </div>
      <button class="person-remove" onclick="removePerson(${res.id},this)" title="Remove">✕</button>`;
    list.appendChild(chip);
    hideAddPerson(cat);
    showToast(`✅ ${name} added`, 'success');
  } catch (err) { showToast('❌ ' + err.message, 'error'); }
}

async function removePerson(id, btn) {
  if (!confirm('Remove this person?')) return;
  try {
    await apiDelete(`/api/people/${id}/delete`);
    const chip = btn.closest('.person-chip');
    const list = chip.parentElement;
    chip.remove();
    if (!list.querySelector('.person-chip')) {
      const p = document.createElement('p');
      const cat = list.id.includes('engineer') ? 'engineers' : 'team_member';
      p.className = 'people-empty';
      p.id = `${cat}-empty`;
      p.textContent = `No ${cat === 'engineers' ? 'engineers' : 'team members'} added yet.`;
      list.appendChild(p);
    }
    showToast('Removed.', '');
  } catch (err) { showToast('❌ ' + err.message, 'error'); }
}

// ================================================================
//  IMAGE UPLOAD
// ================================================================
function handleDrop(e) {
  e.preventDefault();
  document.getElementById('uploadZone').classList.remove('drag-over');
  const files = e.dataTransfer?.files;
  if (files && files.length) uploadFiles(files);
}

function handleFileInput(files) {
  if (files && files.length) uploadFiles(files);
}

async function uploadFiles(files) {
  const zone     = document.getElementById('uploadZone');
  const progress = document.getElementById('uploadProgress');
  const bar      = document.getElementById('uploadProgressBar');
  const text     = document.getElementById('uploadProgressText');
  const inner    = zone.querySelector('.upload-zone-inner');

  inner.style.display   = 'none';
  progress.style.display = 'flex';
  text.textContent       = `Uploading ${files.length} file${files.length > 1 ? 's' : ''}…`;
  bar.style.width        = '10%';

  const form = new FormData();
  Array.from(files).forEach(f => form.append('files', f));

  try {
    const res  = await fetch(`/api/project/${PROJECT_ID}/upload`, { method: 'POST', body: form });
    const data = await res.json();

    bar.style.width = '100%';

    if (data.saved && data.saved.length) {
      // Remove empty-gallery placeholder
      document.getElementById('galleryEmpty')?.remove();

      const grid = document.getElementById('photoGrid');
      const before = IMAGES.length;

      data.saved.forEach((img, i) => {
        IMAGES.push(img);
        const idx  = before + i;
        const div  = document.createElement('div');
        div.className   = 'photo-thumb';
        div.dataset.id  = img.id;
        div.setAttribute('onclick', `openLightbox(${idx})`);
        div.innerHTML = `
          <img src="${img.url}" alt="${esc(img.original_name)}" loading="lazy" />
          <div class="thumb-overlay">
            <span class="thumb-filename">${esc(img.original_name)}</span>
            <div class="thumb-actions">
              <button class="thumb-action-btn" onclick="event.stopPropagation(); setHero(${img.id}, this)" title="Set as cover">⭐</button>
              <button class="thumb-action-btn thumb-del-btn" onclick="event.stopPropagation(); deleteImage(${img.id}, this)" title="Delete">🗑</button>
            </div>
          </div>`;
        grid.appendChild(div);
      });

      updatePhotoCount();

      // Auto-set hero if none yet
      if (!document.getElementById('heroImg') && data.saved[0]) {
        updateHeroDisplay(data.saved[0]);
      }

      showToast(`✅ ${data.saved.length} photo${data.saved.length > 1 ? 's' : ''} uploaded!`, 'success');
    }

    if (data.errors && data.errors.length) {
      showToast(`⚠️ ${data.errors[0]}`, 'error');
    }
  } catch (err) {
    showToast('❌ Upload failed: ' + err.message, 'error');
  } finally {
    setTimeout(() => {
      progress.style.display = 'none';
      bar.style.width        = '0%';
      inner.style.display    = 'flex';
    }, 600);
    // Reset file input
    const fi = document.getElementById('fileInput');
    if (fi) fi.value = '';
  }
}

// ================================================================
//  HERO IMAGE
// ================================================================
async function setHero(imageId, btn) {
  try {
    await apiPost(`/api/image/${imageId}/set_hero`, {});
    // Update all hero badges
    document.querySelectorAll('.hero-badge').forEach(b => b.remove());
    const thumb = btn?.closest('.photo-thumb');
    if (thumb) {
      const badge = document.createElement('span');
      badge.className   = 'hero-badge';
      badge.textContent = 'Cover';
      thumb.appendChild(badge);
    }
    // Update hero image
    const imgEl = thumb?.querySelector('img');
    if (imgEl) updateHeroDisplay({ url: imgEl.src, original_name: '' });
    showToast('⭐ Cover photo set!', 'success');
  } catch (err) { showToast('❌ ' + err.message, 'error'); }
}

function setHeroFromLb() {
  const img = IMAGES[lbIndex];
  if (!img) return;
  const thumb = document.querySelector(`.photo-thumb[data-id="${img.id}"]`);
  setHero(img.id, thumb?.querySelector('.thumb-action-btn'));
  closeLightbox();
}

function updateHeroDisplay(img) {
  const hero     = document.querySelector('.project-hero');
  const existing = document.getElementById('heroImg');
  if (!hero) return;

  hero.classList.remove('no-hero');
  const placeholder = hero.querySelector('.hero-placeholder');
  if (placeholder) placeholder.remove();

  if (existing) {
    existing.src = img.url;
  } else {
    const el   = document.createElement('img');
    el.src     = img.url;
    el.alt     = img.original_name || '';
    el.className = 'hero-img';
    el.id      = 'heroImg';
    hero.insertBefore(el, hero.firstChild);
  }
}

// ================================================================
//  DELETE IMAGE
// ================================================================
async function deleteImage(imageId, btn) {
  if (!confirm('Delete this photo permanently?')) return;
  try {
    await apiDelete(`/api/image/${imageId}/delete`);
    const thumb = btn.closest('.photo-thumb');
    const idx   = IMAGES.findIndex(i => i.id === imageId);
    if (idx !== -1) IMAGES.splice(idx, 1);

    // Re-bind lightbox indices
    document.querySelectorAll('.photo-thumb').forEach((el, i) => {
      if (el !== thumb) el.setAttribute('onclick', `openLightbox(${IMAGES.findIndex(im => im.id === parseInt(el.dataset.id))})`);
    });

    thumb.remove();
    updatePhotoCount();

    if (IMAGES.length === 0) {
      const grid = document.getElementById('photoGrid');
      const emp  = document.createElement('div');
      emp.id = 'galleryEmpty';
      emp.className = 'gallery-empty';
      emp.innerHTML = `<svg viewBox="0 0 64 64" fill="none"><circle cx="32" cy="32" r="28" fill="#f3f4f6"/><path d="M16 44 L32 22 L48 44" stroke="#d1d5db" stroke-width="2.5" fill="none" stroke-linejoin="round"/><circle cx="44" cy="24" r="5" fill="#d1d5db"/></svg><p>No photos yet.</p>`;
      grid.after(emp);
    }
    showToast('Photo deleted.', '');
  } catch (err) { showToast('❌ ' + err.message, 'error'); }
}

function updatePhotoCount() {
  const badge = document.getElementById('photoCountBadge');
  if (badge) badge.textContent = IMAGES.length;
}

// ================================================================
//  LIGHTBOX
// ================================================================
let lbIndex = 0;

function openLightbox(index) {
  if (!IMAGES || !IMAGES.length) return;
  lbIndex = index;
  updateLightbox();
  document.getElementById('lightbox').classList.add('open');
  document.addEventListener('keydown', lbKeyHandler);
}

function closeLightbox() {
  document.getElementById('lightbox').classList.remove('open');
  document.removeEventListener('keydown', lbKeyHandler);
}

function lbPrev() { lbIndex = (lbIndex - 1 + IMAGES.length) % IMAGES.length; updateLightbox(); }
function lbNext() { lbIndex = (lbIndex + 1) % IMAGES.length;                  updateLightbox(); }

function updateLightbox() {
  const img = IMAGES[lbIndex];
  if (!img) return;
  const url = img.url || `/uploads/${PROJECT_ID}/${img.filename}`;
  document.getElementById('lbImg').src     = url;
  document.getElementById('lbImg').alt     = img.original_name || img.filename;
  document.getElementById('lbCaption').textContent =
    `${img.original_name || img.filename}  (${lbIndex + 1} / ${IMAGES.length})`;
}

function lbKeyHandler(e) {
  if (e.key === 'ArrowLeft')  lbPrev();
  if (e.key === 'ArrowRight') lbNext();
  if (e.key === 'Escape')     closeLightbox();
}

// ================================================================
//  Utility
// ================================================================
function esc(str) {
  return String(str)
    .replace(/&/g,'&amp;').replace(/</g,'&lt;').replace(/>/g,'&gt;').replace(/"/g,'&quot;');
}

// Enter key in add-person inputs
document.addEventListener('keydown', e => {
  if (e.key !== 'Enter') return;
  const t = e.target;
  if (t.matches('#eng-name, #eng-role'))                 addPerson('engineer');
  if (t.matches('#team_member-name, #team_member-role')) addPerson('team_member');
});
