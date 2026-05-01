/* ══════════════════════════════════════════════
   OKR Tracker — Frontend JavaScript
   Vanilla JS, no framework dependencies
   ══════════════════════════════════════════════ */

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
  try {
    const resp = await fetch(url, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(data),
    });
    const result = await resp.json();
    hideLoading();
    if (!result.ok && result.error) {
      showToast(result.error, 'error');
    }
    return result;
  } catch (e) {
    hideLoading();
    showToast('Network error: ' + e.message, 'error');
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
    showToast('Data refreshed', 'success');
    showLoading('Reloading...');
    setTimeout(() => location.reload(), 300);
  });
}

// ---------- Mobile Sidebar ----------

function toggleSidebar() {
  const sidebar = document.getElementById('sidebar');
  const overlay = document.getElementById('mobileOverlay');
  sidebar.classList.toggle('mobile-open');
  overlay.classList.toggle('active');
}

// ---------- OKR Tab Switching ----------

function switchOkrTab(idx) {
  document.querySelectorAll('.okr-tab').forEach((tab, i) => {
    tab.classList.toggle('active', i === idx);
  });
  document.querySelectorAll('.okr-card').forEach((card, i) => {
    card.style.display = i === idx ? 'block' : 'none';
  });
  // Persist active tab in URL hash so reloads land on the same tab
  history.replaceState(null, '', '#okr-' + idx);
}

// On page load, restore tab from URL hash
document.addEventListener('DOMContentLoaded', function() {
  const hash = window.location.hash;
  if (hash && hash.startsWith('#okr-')) {
    const idx = parseInt(hash.replace('#okr-', ''), 10);
    if (!isNaN(idx) && document.querySelector('[data-okr-idx="' + idx + '"]')) {
      switchOkrTab(idx);
    }
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

  const quarter = typeof CURRENT_QUARTER !== 'undefined' ? CURRENT_QUARTER :
    document.getElementById('quarterSelect')?.value || '';
  apiPost('/api/okr/add', {
    quarter: quarter,
    title: document.getElementById('okrTitle').value,
    description: document.getElementById('okrDesc').value,
    owner: document.getElementById('okrOwner').value,
    target_date: document.getElementById('okrDate').value,
    category: document.getElementById('okrCategory').value,
  }, 'Creating objective...').then(r => {
    if (r.ok) {
      showToast('Objective created', 'success');
      closeModal('addOkrModal');
      showLoading('Reloading...');
      // Navigate to last tab (newly created OKR will be appended)
      const totalTabs = document.querySelectorAll('.okr-tab').length;
      setTimeout(() => {
        window.location.hash = '#okr-' + totalTabs;
        location.reload();
      }, 500);
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

  const quarter = typeof CURRENT_QUARTER !== 'undefined' ? CURRENT_QUARTER :
    document.getElementById('quarterSelect')?.value || '';
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
      showToast('Objective updated', 'success');
      closeModal('editOkrModal');
      showLoading('Reloading...');
      setTimeout(() => location.reload(), 500);
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
      showToast('Objective moved', 'success');
      closeModal('moveOkrModal');
      showLoading('Reloading...');
      setTimeout(() => location.reload(), 500);
    }
  });
}

function deleteOkr(okrId, quarter, category) {
  showConfirm('Delete Objective', 'This will permanently delete this objective and all its key results.', function() {
    apiPost('/api/okr/delete', { id: okrId, quarter: quarter, category: category }, 'Deleting objective...')
      .then(r => {
        if (r.ok) {
          showToast('Objective deleted', 'success');
          showLoading('Reloading...');
          setTimeout(() => location.reload(), 500);
        }
      });
  });
}

// ---------- KR CRUD ----------

function openAddKrModal(okrId) {
  document.getElementById('krOkrId').value = okrId;
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

  const quarter = typeof CURRENT_QUARTER !== 'undefined' ? CURRENT_QUARTER :
    document.getElementById('quarterSelect')?.value || '';
  apiPost('/api/kr/add', {
    quarter: quarter,
    okr_id: document.getElementById('krOkrId').value,
    name: document.getElementById('krName').value,
    owner: document.getElementById('krOwner').value,
    target_value: Math.round(parseFloat(document.getElementById('krTarget').value) || 0),
    baseline_value: Math.round(parseFloat(document.getElementById('krBaseline').value) || 0),
    direction: document.getElementById('krDirection').value,
    unit: document.getElementById('krUnit').value,
    description: document.getElementById('krDescription').value,
  }, 'Creating key result...').then(r => {
    if (r.ok) {
      showToast('Key Result created', 'success');
      closeModal('addKrModal');
      showLoading('Reloading...');
      // Stay on same OKR tab (hash is already set from switchOkrTab)
      setTimeout(() => location.reload(), 500);
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
  openModal('editKrModal');
}

function submitEditKr() {
  if (!validateRequired([
    { id: 'editKrName', label: 'Name' },
    { id: 'editKrTarget', label: 'Target Value' },
    { id: 'editKrBaseline', label: 'Baseline Value' },
  ])) return;

  const quarter = typeof CURRENT_QUARTER !== 'undefined' ? CURRENT_QUARTER :
    document.getElementById('quarterSelect')?.value || '';
  apiPost('/api/kr/edit', {
    quarter: quarter,
    id: document.getElementById('editKrId').value,
    name: document.getElementById('editKrName').value,
    owner: document.getElementById('editKrOwner').value,
    target_value: Math.round(parseFloat(document.getElementById('editKrTarget').value) || 0),
    baseline_value: Math.round(parseFloat(document.getElementById('editKrBaseline').value) || 0),
    direction: document.getElementById('editKrDirection').value,
    unit: document.getElementById('editKrUnit').value,
    description: document.getElementById('editKrDescription').value,
  }, 'Saving key result...').then(r => {
    if (r.ok) {
      showToast('Key Result updated', 'success');
      closeModal('editKrModal');
      showLoading('Reloading...');
      setTimeout(() => location.reload(), 500);
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

  const quarter = typeof CURRENT_QUARTER !== 'undefined' ? CURRENT_QUARTER :
    document.getElementById('quarterSelect')?.value || '';
  const krId = document.getElementById('updateKrId').value;
  const noteText = document.getElementById('updateKrNote').value.trim();

  apiPost('/api/kr/update', {
    quarter: quarter,
    id: krId,
    okr_id: document.getElementById('updateKrOkrId').value,
    value: Math.round(parseFloat(document.getElementById('updateKrValue').value) || 0),
  }, 'Updating value...').then(r => {
    if (r.ok) {
      closeModal('updateKrModal');
      if (noteText) {
        apiPost('/api/note/add', {
          parent_type: 'KR',
          parent_id: krId,
          text: noteText,
        }, 'Saving note...').then(() => {
          showToast('Value and note saved', 'success');
          showLoading('Reloading...');
          setTimeout(() => location.reload(), 500);
        });
      } else {
        showToast('Value updated', 'success');
        showLoading('Reloading...');
        setTimeout(() => location.reload(), 500);
      }
    }
  });
}

function deleteKr(krId, okrId, quarter, category) {
  showConfirm('Delete Key Result', 'This will permanently delete this key result.', function() {
    apiPost('/api/kr/delete', { id: krId, okr_id: okrId, quarter: quarter, category: category }, 'Deleting key result...')
      .then(r => {
        if (r.ok) {
          showToast('Key Result deleted', 'success');
          showLoading('Reloading...');
          setTimeout(() => location.reload(), 500);
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
        '<div class="note-text">' + escapeHtml(text) + '</div>';
      notesList.insertBefore(noteCard, input.closest('.note-form'));
      input.value = '';
      showToast('Note added', 'success');
    }
  });
}

function escapeHtml(text) {
  const div = document.createElement('div');
  div.textContent = text;
  return div.innerHTML;
}

// ---------- Page Load Loading State ----------

// Show loading on any full page navigation
window.addEventListener('beforeunload', function() {
  showLoading('Loading...');
});

// ---------- Recent Activity drawer ----------

function setActivityDrawer(open) {
  const drawer = document.getElementById('activityDrawer');
  const toggle = document.getElementById('activityToggle');
  const main = document.querySelector('.main-content');
  if (!drawer || !toggle || !main) return;
  if (open) {
    drawer.classList.add('open');
    toggle.style.display = 'none';
    main.classList.add('with-activity');
  } else {
    drawer.classList.remove('open');
    toggle.style.display = '';
    main.classList.remove('with-activity');
  }
  try { localStorage.setItem('okr_activity_open', open ? '1' : '0'); } catch (e) {}
}

function toggleActivityDrawer() {
  const drawer = document.getElementById('activityDrawer');
  if (!drawer) return;
  setActivityDrawer(!drawer.classList.contains('open'));
}

document.addEventListener('DOMContentLoaded', function() {
  if (!document.getElementById('activityDrawer')) return;
  let open = true;
  try {
    const stored = localStorage.getItem('okr_activity_open');
    if (stored === '0') open = false;
  } catch (e) {}
  setActivityDrawer(open);
});
