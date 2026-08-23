/* ══════════════════════════════════════════════
   OKR Tracker — Frontend JavaScript
   Vanilla JS, no framework dependencies
   ══════════════════════════════════════════════ */

// ---------- Shared helpers ----------

// CURRENT_QUARTER is defined on the tracker page; the select is the fallback
// for any page that renders the picker without the global.
function currentQuarter() {
  if (typeof CURRENT_QUARTER !== 'undefined' && CURRENT_QUARTER) return CURRENT_QUARTER;
  return document.getElementById('quarterSelect')?.value || '';
}

// Escapes for both text nodes and quoted attribute values.
function esc(s) {
  return String(s)
    .replace(/&/g, '&amp;').replace(/</g, '&lt;')
    .replace(/>/g, '&gt;').replace(/"/g, '&quot;');
}

// Every mutation currently re-renders the page; keep that in one place so the
// toast/close/reload sequence can't drift between call sites.
function reloadAfter(message, opts) {
  const o = opts || {};
  if (message) showToast(message, 'success');
  if (o.modal) closeModal(o.modal);
  showLoading('Reloading...');
  setTimeout(() => {
    if (o.beforeReload) o.beforeReload();
    location.reload();
  }, o.delay || 100);
}

// ---------- Loading Overlay ----------

function showLoading(message) {
  const overlay = document.getElementById('loadingOverlay');
  const text = document.getElementById('loadingText');
  if (text) text.textContent = message || 'Loading...';
  if (overlay) overlay.classList.add('active');
}

function hideLoading() {
  const overlay = document.getElementById('loadingOverlay');
  if (overlay) overlay.classList.remove('active');
}

// ---------- API Helpers (with loading state) ----------

async function apiPost(url, data, loadingMsg) {
  showLoading(loadingMsg || 'Saving...');
  const controller = new AbortController();
  const timeout = setTimeout(() => controller.abort(), 30000); // 30s hard timeout
  try {
    const resp = await fetch(url, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(data),
      signal: controller.signal,
    });
    clearTimeout(timeout);
    // If the server returned HTML (e.g. session expired → login redirect), handle gracefully
    const ct = resp.headers.get('content-type') || '';
    if (!ct.includes('application/json')) {
      hideLoading();
      showToast('Session expired — redirecting to login...', 'error');
      setTimeout(() => { window.location.href = '/login'; }, 1500);
      return { ok: false };
    }
    const result = await resp.json();
    hideLoading();
    if (!result.ok && result.error) {
      showToast(result.error, 'error');
    }
    return result;
  } catch (e) {
    clearTimeout(timeout);
    hideLoading();
    if (e.name === 'AbortError') {
      showToast('Request timed out — please try again.', 'error');
    } else {
      showToast('Network error — please try again.', 'error');
    }
    return { ok: false, error: e.message };
  }
}

async function apiGet(url, loadingMsg) {
  showLoading(loadingMsg || 'Loading...');
  try {
    const resp = await fetch(url);
    const result = await resp.json();
    hideLoading();
    return result;
  } catch (e) {
    hideLoading();
    showToast('Network error: ' + e.message, 'error');
    return { ok: false };
  }
}

// ---------- Toast Notifications ----------

function showToast(message, type = 'info') {
  const container = document.getElementById('toastContainer');
  const toast = document.createElement('div');
  toast.className = 'toast ' + type;
  toast.textContent = message;
  container.appendChild(toast);
  setTimeout(() => {
    toast.style.opacity = '0';
    toast.style.transform = 'translateX(100%)';
    toast.style.transition = 'all 0.3s ease';
    setTimeout(() => toast.remove(), 300);
  }, 4000);
}

// ---------- Form Validation ----------

function validateRequired(fields) {
  // fields: [{id: 'elementId', label: 'Field Name'}, ...]
  let valid = true;
  let firstInvalid = null;
  // Clear previous error states
  fields.forEach(f => {
    const el = document.getElementById(f.id);
    if (el) el.classList.remove('input-error');
  });
  // Check each field
  const missing = [];
  fields.forEach(f => {
    const el = document.getElementById(f.id);
    if (!el) return;
    const val = el.value.trim();
    if (!val) {
      el.classList.add('input-error');
      missing.push(f.label);
      valid = false;
      if (!firstInvalid) firstInvalid = el;
    }
  });
  if (!valid) {
    showToast('Required: ' + missing.join(', '), 'error');
    if (firstInvalid) firstInvalid.focus();
  }
  return valid;
}

// Remove error state on input
document.addEventListener('input', function(e) {
  if (e.target.classList.contains('input-error')) {
    e.target.classList.remove('input-error');
  }
});

// ---------- Modal Helpers ----------

function openModal(id) {
  const modal = document.getElementById(id);
  modal.classList.add('active');
  // Focus first visible input
  setTimeout(() => {
    const firstInput = modal.querySelector('input:not([type=hidden]), textarea, select');
    if (firstInput) firstInput.focus();
  }, 100);
}

function closeModal(id) {
  document.getElementById(id).classList.remove('active');
  // Clear error states when closing
  document.querySelectorAll('#' + id + ' .input-error').forEach(el => el.classList.remove('input-error'));
}

// Close modal on overlay click
document.addEventListener('click', function(e) {
  if (e.target.classList.contains('modal-overlay') && e.target.classList.contains('active')) {
    e.target.classList.remove('active');
  }
});

// ---------- Enter Key & Escape ----------

document.addEventListener('keydown', function(e) {
  if (e.key === 'Escape') {
    document.querySelectorAll('.modal-overlay.active').forEach(m => m.classList.remove('active'));
    closeConfirm();
    return;
  }

  if (e.key === 'Enter') {
    // Don't trigger on textareas (allow newlines)
    if (e.target.tagName === 'TEXTAREA') return;

    // Check for active modal and find its submit button
    const activeModal = document.querySelector('.modal-overlay.active .modal');
    if (activeModal) {
      e.preventDefault();
      const submitBtn = activeModal.querySelector('.modal-actions .btn-primary');
      if (submitBtn) submitBtn.click();
      return;
    }

    // Check for confirm dialog
    const confirmOverlay = document.getElementById('confirmOverlay');
    if (confirmOverlay && confirmOverlay.classList.contains('active')) {
      e.preventDefault();
      confirmAction();
      return;
    }
  }
});

// ---------- Confirm Dialog ----------

let _confirmCallback = null;

function showConfirm(title, text, callback) {
  document.getElementById('confirmTitle').textContent = title;
  document.getElementById('confirmText').textContent = text;
  _confirmCallback = callback;
  document.getElementById('confirmOverlay').classList.add('active');
}

function closeConfirm() {
  document.getElementById('confirmOverlay').classList.remove('active');
  _confirmCallback = null;
}

function confirmAction() {
  if (_confirmCallback) _confirmCallback();
  closeConfirm();
}

// ---------- Navigation ----------

function changeQuarter(q) {
  showLoading('Loading quarter...');
  const params = new URLSearchParams(window.location.search);
  params.set('quarter', q);
  window.location.search = params.toString();
}

function changeCategory(cat) {
  showLoading('Loading category...');
  const params = new URLSearchParams(window.location.search);
  params.set('category', cat);
  window.location.search = params.toString();
}

function refreshData() {
  apiGet('/api/refresh', 'Refreshing data...').then(() => {
    reloadAfter('Data refreshed', { delay: 300 });
  });
}

// ---------- Objective switching (Focus Board) ----------

// Match by data-okr-idx (a stable identity, not DOM position) so drag-reordering
// the drawer does not change which card each row points to.
function switchOkrTab(idx) {
  const key = String(idx);
  document.querySelectorAll('.oitem').forEach(item => {
    item.classList.toggle('sel', item.dataset.okrIdx === key);
  });
  document.querySelectorAll('.okr-card').forEach(card => {
    card.style.display = card.dataset.okrIdx === key ? 'block' : 'none';
  });
  history.replaceState(null, '', '#okr-' + idx);
  closeDrawer();
  // On phones the drawer and detail are separate screens — reveal the detail.
  document.getElementById('shell')?.classList.add('showing-detail');
  const pane = document.getElementById('detailPane');
  if (pane) pane.scrollTop = 0;
  // Charts built inside a hidden card measured the wrong width — re-measure.
  window.resizeOkrCharts?.();
}

// Tabs within one objective: 0 = Key Results, 1 = Notes, 2 = History
function switchOkrPanel(btn, okrIdx, panelIdx) {
  const head = btn.closest('.okr-tabs');
  if (head) head.querySelectorAll('.otab').forEach(t => t.classList.toggle('on', t === btn));
  document.querySelectorAll('.opanel[data-okr-panel="' + okrIdx + '"]').forEach(p => {
    p.classList.toggle('on', p.dataset.panel === String(panelIdx));
  });
  window.resizeOkrCharts?.();
}

// Phone-only: go back from the detail screen to the objective list
function showObjectiveList() {
  document.getElementById('shell')?.classList.remove('showing-detail');
}

// Charts are sized explicitly, so every window resize needs a re-fit.
// Debounced — resize fires continuously while dragging a window edge.
let _chartResizeTimer = null;
window.addEventListener('resize', function() {
  clearTimeout(_chartResizeTimer);
  _chartResizeTimer = setTimeout(() => window.resizeOkrCharts?.(), 120);
});

// ---------- Objective drawer (tablet overlay) ----------

function toggleDrawer() {
  const d = document.getElementById('objDrawer');
  const s = document.getElementById('drawerScrim');
  if (!d) return;
  const open = d.classList.toggle('open');
  if (s) s.classList.toggle('on', open);
}

function closeDrawer() {
  document.getElementById('objDrawer')?.classList.remove('open');
  document.getElementById('drawerScrim')?.classList.remove('on');
}

// ---------- Account menu ----------

function toggleAccountMenu(e) {
  if (e) e.stopPropagation();
  const m = document.getElementById('accountMenu');
  const s = document.getElementById('accountScrim');
  if (!m) return;
  const open = m.classList.toggle('open');
  if (s) s.classList.toggle('on', open);
}

function closeAccountMenu() {
  document.getElementById('accountMenu')?.classList.remove('open');
  document.getElementById('accountScrim')?.classList.remove('on');
}

// ---------- Objective reordering (drag-and-drop, sticky per user) ----------

function _okrOrderKey() {
  const email = (typeof USER_EMAIL !== 'undefined' && USER_EMAIL) ? USER_EMAIL : 'anon';
  const q = currentQuarter() || 'na';
  return 'okrOrder:' + email + ':' + q;
}

function _loadOkrOrder() {
  try {
    const raw = localStorage.getItem(_okrOrderKey());
    return raw ? JSON.parse(raw) : null;
  } catch (e) {
    return null;
  }
}

function _saveOkrOrder(ids) {
  try { localStorage.setItem(_okrOrderKey(), JSON.stringify(ids)); } catch (e) {}
}

// Reorder the drawer rows (and matching detail sections) to the saved order.
// Objectives not in the saved order — e.g. newly created — fall in at the end.
function applySavedOkrOrder() {
  const saved = _loadOkrOrder();
  if (!saved || !Array.isArray(saved) || !saved.length) return;

  const list = document.getElementById('objList');
  if (!list) return;
  const cards = Array.from(document.querySelectorAll('.okr-card'));
  const cardParent = cards.length ? cards[0].parentNode : null;

  const itemsById = new Map(
    Array.from(list.querySelectorAll('.oitem')).map(t => [t.dataset.okrId, t])
  );
  const cardsById = new Map(cards.map(c => [c.dataset.okrId, c]));

  const seen = new Set();
  saved.forEach(id => {
    const t = itemsById.get(id);
    if (t) { list.appendChild(t); seen.add(id); }
    const c = cardsById.get(id);
    if (c && cardParent) cardParent.appendChild(c);
  });
  itemsById.forEach((t, id) => { if (!seen.has(id)) list.appendChild(t); });
  cardsById.forEach((c, id) => { if (!seen.has(id) && cardParent) cardParent.appendChild(c); });
}

function initOkrDragging() {
  const list = document.getElementById('objList');
  if (!list) return;
  let dragging = null;

  list.addEventListener('dragstart', function(e) {
    const item = e.target.closest('.oitem');
    if (!item) return;
    dragging = item;
    item.classList.add('dragging');
    if (e.dataTransfer) {
      e.dataTransfer.effectAllowed = 'move';
      // Firefox requires setData for the drag to fire at all
      e.dataTransfer.setData('text/plain', item.dataset.okrId || '');
    }
  });

  list.addEventListener('dragend', function() {
    if (dragging) dragging.classList.remove('dragging');
    dragging = null;
  });

  list.addEventListener('dragover', function(e) {
    if (!dragging) return;
    e.preventDefault();
    if (e.dataTransfer) e.dataTransfer.dropEffect = 'move';
    const target = e.target.closest('.oitem');
    if (!target || target === dragging) return;
    const rect = target.getBoundingClientRect();
    const before = (e.clientY - rect.top) < rect.height / 2;
    list.insertBefore(dragging, before ? target : target.nextSibling);
  });

  list.addEventListener('drop', function(e) {
    if (!dragging) return;
    e.preventDefault();
    const ids = Array.from(list.querySelectorAll('.oitem'))
      .map(t => t.dataset.okrId)
      .filter(Boolean);
    _saveOkrOrder(ids);
  });
}

const _isPhone = () => window.matchMedia('(max-width: 640px)').matches;

document.addEventListener('DOMContentLoaded', function() {
  if (!document.getElementById('objList')) return;
  applySavedOkrOrder();
  initOkrDragging();

  // Restore the objective from the URL hash, else select the first.
  let restored = false;
  const hash = window.location.hash;
  if (hash && hash.startsWith('#okr-')) {
    const idx = parseInt(hash.replace('#okr-', ''), 10);
    if (!isNaN(idx) && document.querySelector('.oitem[data-okr-idx="' + idx + '"]')) {
      switchOkrTab(idx);
      restored = true;
    }
  }
  // On wide screens both panes show at once, so always reveal the detail.
  if (!_isPhone()) document.getElementById('shell')?.classList.add('showing-detail');
  else if (!restored) showObjectiveList();
});

window.addEventListener('resize', function() {
  if (!_isPhone()) {
    closeDrawer();
    document.getElementById('shell')?.classList.add('showing-detail');
  }
});

// ---------- Notes Toggle ----------

function toggleNotes(btn) {
  const notesList = btn.nextElementSibling;
  const arrow = btn.querySelector('.toggle-arrow');
  if (notesList.style.display === 'none') {
    notesList.style.display = 'block';
    if (arrow) arrow.style.transform = 'rotate(180deg)';
  } else {
    notesList.style.display = 'none';
    if (arrow) arrow.style.transform = 'rotate(0deg)';
  }
}

// ---------- OKR CRUD ----------

function openAddOkrModal() {
  document.getElementById('okrTitle').value = '';
  document.getElementById('okrDesc').value = '';
  document.getElementById('okrOwner').value = '';
  document.getElementById('okrDate').value = '';
  openModal('addOkrModal');
}

function submitAddOkr() {
  if (!validateRequired([
    { id: 'okrTitle', label: 'Title' },
    { id: 'okrOwner', label: 'Owner' },
  ])) return;

  const quarter = currentQuarter();
  apiPost('/api/okr/add', {
    quarter: quarter,
    title: document.getElementById('okrTitle').value,
    description: document.getElementById('okrDesc').value,
    owner: document.getElementById('okrOwner').value,
    target_date: document.getElementById('okrDate').value,
    category: document.getElementById('okrCategory').value,
  }, 'Creating objective...').then(r => {
    if (r.ok) {
      // The new objective is appended, so it lands at the end of the list.
      // (This counted '.okr-tab', a class the Focus Board layout removed, so it
      //  always resolved to 0 and sent you back to the first objective.)
      const newIdx = document.querySelectorAll('.oitem').length;
      reloadAfter('Objective created', {
        modal: 'addOkrModal',
        delay: 500,
        beforeReload: () => { window.location.hash = '#okr-' + newIdx; },
      });
    }
  });
}

function openEditOkrModal(okr) {
  document.getElementById('editOkrId').value = okr.id;
  document.getElementById('editOkrTitle').value = okr.title;
  document.getElementById('editOkrDesc').value = okr.description || '';
  document.getElementById('editOkrOwner').value = okr.owner || '';
  document.getElementById('editOkrDate').value = okr.target_date || '';
  document.getElementById('editOkrCategory').value = okr.category || '';
  openModal('editOkrModal');
}

function submitEditOkr() {
  if (!validateRequired([
    { id: 'editOkrTitle', label: 'Title' },
    { id: 'editOkrOwner', label: 'Owner' },
  ])) return;

  const quarter = currentQuarter();
  apiPost('/api/okr/edit', {
    quarter: quarter,
    id: document.getElementById('editOkrId').value,
    title: document.getElementById('editOkrTitle').value,
    description: document.getElementById('editOkrDesc').value,
    owner: document.getElementById('editOkrOwner').value,
    target_date: document.getElementById('editOkrDate').value,
    category: document.getElementById('editOkrCategory').value,
  }, 'Saving objective...').then(r => {
    if (r.ok) {
      reloadAfter('Objective updated', { modal: 'editOkrModal' });
    }
  });
}

function openMoveOkrModal(okrId, currentQuarter) {
  document.getElementById('moveOkrId').value = okrId;
  document.getElementById('moveOkrOldQuarter').value = currentQuarter;
  openModal('moveOkrModal');
}

function submitMoveOkr() {
  apiPost('/api/okr/move', {
    id: document.getElementById('moveOkrId').value,
    old_quarter: document.getElementById('moveOkrOldQuarter').value,
    new_quarter: document.getElementById('moveOkrQuarter').value,
  }, 'Moving objective...').then(r => {
    if (r.ok) {
      reloadAfter('Objective moved', { modal: 'moveOkrModal' });
    }
  });
}

function deleteOkr(okrId, quarter, category) {
  showConfirm('Delete Objective', 'This will permanently delete this objective and all its key results.', function() {
    apiPost('/api/okr/delete', { id: okrId, quarter: quarter, category: category }, 'Deleting objective...')
      .then(r => {
        if (r.ok) {
          reloadAfter('Objective deleted');
        }
      });
  });
}

// ---------- KR CRUD ----------

function openAddKrModal(okrId, category) {
  document.getElementById('krOkrId').value = okrId;
  document.getElementById('krOkrCategory').value = category || '';
  document.getElementById('krName').value = '';
  document.getElementById('krOwner').value = '';
  document.getElementById('krTarget').value = '';
  document.getElementById('krBaseline').value = '0';
  document.getElementById('krUnit').value = '';
  document.getElementById('krDescription').value = '';
  openModal('addKrModal');
}

function submitAddKr() {
  if (!validateRequired([
    { id: 'krName', label: 'Name' },
    { id: 'krTarget', label: 'Target Value' },
    { id: 'krBaseline', label: 'Baseline Value' },
  ])) return;

  const quarter = currentQuarter();
  apiPost('/api/kr/add', {
    quarter: quarter,
    okr_id: document.getElementById('krOkrId').value,
    category: document.getElementById('krOkrCategory').value || '',
    name: document.getElementById('krName').value,
    owner: document.getElementById('krOwner').value,
    target_value: Math.round(parseFloat(document.getElementById('krTarget').value) || 0),
    baseline_value: Math.round(parseFloat(document.getElementById('krBaseline').value) || 0),
    direction: document.getElementById('krDirection').value,
    unit: document.getElementById('krUnit').value,
    description: document.getElementById('krDescription').value,
  }, 'Creating key result...').then(r => {
    if (r.ok) {
      // The hash is already set by switchOkrTab, so the reload lands back
      // on the objective the KR was added to.
      reloadAfter('Key Result created', { modal: 'addKrModal' });
    }
  });
}

function openEditKrModal(kr) {
  document.getElementById('editKrId').value = kr.id;
  document.getElementById('editKrName').value = kr.name;
  document.getElementById('editKrOwner').value = kr.owner || '';
  document.getElementById('editKrTarget').value = kr.target_value;
  document.getElementById('editKrBaseline').value = kr.baseline_value;
  document.getElementById('editKrDirection').value = kr.direction;
  document.getElementById('editKrUnit').value = kr.unit || '';
  document.getElementById('editKrDescription').value = kr.description || '';

  // Objective selector — only offer Objectives the user is allowed to place KRs in.
  // Always include the current parent OKR so the field renders sensibly even when
  // that OKR sits outside the user's normally-creatable categories.
  const sel = document.getElementById('editKrOkrId');
  if (sel) {
    const allowed = (typeof IS_ADMIN !== 'undefined' && IS_ADMIN)
      ? null
      : new Set(typeof CREATABLE_CATEGORIES !== 'undefined' ? CREATABLE_CATEGORIES : []);
    const options = (typeof ALL_OKRS !== 'undefined' ? ALL_OKRS : []).filter(o =>
      String(o.id) === String(kr.okr_id) || allowed === null || allowed.has(o.category)
    );
    sel.innerHTML = options.map(o => {
      const label = o.category ? `${o.title} — ${o.category}` : o.title;
      const selected = String(o.id) === String(kr.okr_id) ? ' selected' : '';
      return `<option value="${esc(o.id)}"${selected}>${esc(label)}</option>`;
    }).join('');
    sel.dataset.original = kr.okr_id;
  }

  openModal('editKrModal');
}

function submitEditKr() {
  if (!validateRequired([
    { id: 'editKrName', label: 'Name' },
    { id: 'editKrTarget', label: 'Target Value' },
    { id: 'editKrBaseline', label: 'Baseline Value' },
  ])) return;

  const quarter = currentQuarter();
  apiPost('/api/kr/edit', {
    quarter: quarter,
    id: document.getElementById('editKrId').value,
    okr_id: document.getElementById('editKrOkrId')?.value || undefined,
    name: document.getElementById('editKrName').value,
    owner: document.getElementById('editKrOwner').value,
    target_value: Math.round(parseFloat(document.getElementById('editKrTarget').value) || 0),
    baseline_value: Math.round(parseFloat(document.getElementById('editKrBaseline').value) || 0),
    direction: document.getElementById('editKrDirection').value,
    unit: document.getElementById('editKrUnit').value,
    description: document.getElementById('editKrDescription').value,
  }, 'Saving key result...').then(r => {
    if (r.ok) {
      reloadAfter('Key Result updated', { modal: 'editKrModal' });
    }
  });
}

function openUpdateKrModal(kr) {
  document.getElementById('updateKrId').value = kr.id;
  document.getElementById('updateKrOkrId').value = kr.okr_id;
  document.getElementById('updateKrInfo').textContent =
    kr.name + ' \u2014 Current: ' + kr.current_display + ' / Target: ' + kr.target_display;
  document.getElementById('updateKrValue').value = kr.current_value;
  document.getElementById('updateKrNote').value = '';
  openModal('updateKrModal');
}

function submitUpdateKr() {
  if (!validateRequired([
    { id: 'updateKrValue', label: 'New Value' },
  ])) return;

  const quarter = currentQuarter();
  const krId = document.getElementById('updateKrId').value;
  const noteText = document.getElementById('updateKrNote').value.trim();

  // Single combined call — the server saves the note alongside the value
  // when a `note` field is included. No separate /api/note/add round-trip.
  apiPost('/api/kr/update', {
    quarter: quarter,
    id: krId,
    okr_id: document.getElementById('updateKrOkrId').value,
    value: Math.round(parseFloat(document.getElementById('updateKrValue').value) || 0),
    note: noteText,
  }, noteText ? 'Saving value and note...' : 'Updating value...').then(r => {
    if (r.ok) {
      reloadAfter(noteText ? 'Value and note saved' : 'Value updated',
                  { modal: 'updateKrModal' });
    }
  });
}

function deleteKr(krId, okrId, quarter, category) {
  showConfirm('Delete Key Result', 'This will permanently delete this key result.', function() {
    apiPost('/api/kr/delete', { id: krId, okr_id: okrId, quarter: quarter, category: category }, 'Deleting key result...')
      .then(r => {
        if (r.ok) {
          reloadAfter('Key Result deleted');
        }
      });
  });
}

// ---------- Notes ----------

function addNote(parentType, parentId, inputId) {
  const input = document.getElementById(inputId);
  const text = input.value.trim();
  if (!text) { showToast('Note cannot be empty', 'error'); input.classList.add('input-error'); input.focus(); return; }

  apiPost('/api/note/add', {
    parent_type: parentType,
    parent_id: parentId,
    text: text,
  }, 'Adding note...').then(r => {
    if (r.ok) {
      const notesList = input.closest('.notes-list');
      const noteCard = document.createElement('div');
      noteCard.className = 'note-card';
      noteCard.innerHTML = '<div class="note-meta"><span class="note-author">' + r.author +
        '</span><span class="note-timestamp">' + r.timestamp + '</span></div>' +
        '<div class="note-text">' + esc(text) + '</div>';
      notesList.insertBefore(noteCard, input.closest('.note-form'));
      input.value = '';
      showToast('Note added', 'success');
    }
  });
}


// ---------- Page Load Loading State ----------

// Show loading on any full page navigation
window.addEventListener('beforeunload', function() {
  showLoading('Loading...');
});

// ---------- Recent Activity drawer ----------

function setActivityDrawer(open) {
  const drawer = document.getElementById('activityDrawer');
  if (!drawer) return;
  drawer.classList.toggle('open', !!open);
  try { localStorage.setItem('okr_activity_open', open ? '1' : '0'); } catch (e) {}
}

function toggleActivityDrawer() {
  const drawer = document.getElementById('activityDrawer');
  if (!drawer) return;
  setActivityDrawer(!drawer.classList.contains('open'));
}

function markActivityRead() {
  const badge = document.getElementById('activityBadge');
  const currentCount = badge ? parseInt(badge.textContent || '0', 10) : 0;
  try { localStorage.setItem('okr_activity_read_count', String(currentCount)); } catch (e) {}
  if (badge) badge.style.display = 'none';
}

document.addEventListener('DOMContentLoaded', function() {
  if (!document.getElementById('activityDrawer')) return;
  // The drawer overlays content in this layout, so it starts closed unless the
  // user explicitly left it open last time.
  let open = false;
  try { open = localStorage.getItem('okr_activity_open') === '1'; } catch (e) {}
  setActivityDrawer(open);

  // Hide the badge if the count hasn't grown since the user hit "Read all"
  try {
    const readCount = parseInt(localStorage.getItem('okr_activity_read_count') || '-1', 10);
    const badge = document.getElementById('activityBadge');
    if (badge && readCount >= parseInt(badge.textContent || '0', 10)) {
      badge.style.display = 'none';
    }
  } catch (e) {}
});
