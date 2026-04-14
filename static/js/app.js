/* ══════════════════════════════════════════════
   OKR Tracker — Frontend JavaScript
   Vanilla JS, no framework dependencies
   ══════════════════════════════════════════════ */

// ---------- API Helpers ----------

async function apiPost(url, data) {
  try {
    const resp = await fetch(url, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(data),
    });
    const result = await resp.json();
    if (!result.ok && result.error) {
      showToast(result.error, 'error');
    }
    return result;
  } catch (e) {
    showToast('Network error: ' + e.message, 'error');
    return { ok: false, error: e.message };
  }
}

async function apiGet(url) {
  try {
    const resp = await fetch(url);
    return await resp.json();
  } catch (e) {
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

// ---------- Modal Helpers ----------

function openModal(id) {
  document.getElementById(id).classList.add('active');
}

function closeModal(id) {
  document.getElementById(id).classList.remove('active');
}

// Close modal on overlay click
document.addEventListener('click', function(e) {
  if (e.target.classList.contains('modal-overlay') && e.target.classList.contains('active')) {
    e.target.classList.remove('active');
  }
});

// Close modal on Escape
document.addEventListener('keydown', function(e) {
  if (e.key === 'Escape') {
    document.querySelectorAll('.modal-overlay.active').forEach(m => m.classList.remove('active'));
    closeConfirm();
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
  const params = new URLSearchParams(window.location.search);
  params.set('quarter', q);
  window.location.search = params.toString();
}

function changeCategory(cat) {
  const params = new URLSearchParams(window.location.search);
  params.set('category', cat);
  window.location.search = params.toString();
}

function refreshData() {
  apiGet('/api/refresh').then(() => {
    showToast('Data refreshed', 'success');
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
  // Update tab buttons
  document.querySelectorAll('.okr-tab').forEach((tab, i) => {
    tab.classList.toggle('active', i === idx);
  });
  // Show/hide cards
  document.querySelectorAll('.okr-card').forEach((card, i) => {
    card.style.display = i === idx ? 'block' : 'none';
  });
}

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
  const quarter = typeof CURRENT_QUARTER !== 'undefined' ? CURRENT_QUARTER :
    document.getElementById('quarterSelect')?.value || '';
  apiPost('/api/okr/add', {
    quarter: quarter,
    title: document.getElementById('okrTitle').value,
    description: document.getElementById('okrDesc').value,
    owner: document.getElementById('okrOwner').value,
    target_date: document.getElementById('okrDate').value,
    category: document.getElementById('okrCategory').value,
  }).then(r => {
    if (r.ok) {
      showToast('Objective created', 'success');
      closeModal('addOkrModal');
      setTimeout(() => location.reload(), 500);
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
  }).then(r => {
    if (r.ok) {
      showToast('Objective updated', 'success');
      closeModal('editOkrModal');
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
  }).then(r => {
    if (r.ok) {
      showToast('Objective moved', 'success');
      closeModal('moveOkrModal');
      setTimeout(() => location.reload(), 500);
    }
  });
}

function deleteOkr(okrId, quarter, category) {
  showConfirm('Delete Objective', 'This will permanently delete this objective and all its key results.', function() {
    apiPost('/api/okr/delete', { id: okrId, quarter: quarter, category: category })
      .then(r => {
        if (r.ok) {
          showToast('Objective deleted', 'success');
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
  openModal('addKrModal');
}

function submitAddKr() {
  const quarter = typeof CURRENT_QUARTER !== 'undefined' ? CURRENT_QUARTER :
    document.getElementById('quarterSelect')?.value || '';
  apiPost('/api/kr/add', {
    quarter: quarter,
    okr_id: document.getElementById('krOkrId').value,
    name: document.getElementById('krName').value,
    owner: document.getElementById('krOwner').value,
    target_value: parseFloat(document.getElementById('krTarget').value) || 0,
    baseline_value: parseFloat(document.getElementById('krBaseline').value) || 0,
    direction: document.getElementById('krDirection').value,
    unit: document.getElementById('krUnit').value,
  }).then(r => {
    if (r.ok) {
      showToast('Key Result created', 'success');
      closeModal('addKrModal');
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
  openModal('editKrModal');
}

function submitEditKr() {
  const quarter = typeof CURRENT_QUARTER !== 'undefined' ? CURRENT_QUARTER :
    document.getElementById('quarterSelect')?.value || '';
  apiPost('/api/kr/edit', {
    quarter: quarter,
    id: document.getElementById('editKrId').value,
    name: document.getElementById('editKrName').value,
    owner: document.getElementById('editKrOwner').value,
    target_value: parseFloat(document.getElementById('editKrTarget').value) || 0,
    baseline_value: parseFloat(document.getElementById('editKrBaseline').value) || 0,
    direction: document.getElementById('editKrDirection').value,
    unit: document.getElementById('editKrUnit').value,
  }).then(r => {
    if (r.ok) {
      showToast('Key Result updated', 'success');
      closeModal('editKrModal');
      setTimeout(() => location.reload(), 500);
    }
  });
}

function openUpdateKrModal(kr) {
  document.getElementById('updateKrId').value = kr.id;
  document.getElementById('updateKrOkrId').value = kr.okr_id;
  document.getElementById('updateKrInfo').textContent =
    kr.name + ' — Current: ' + kr.current_display + ' / Target: ' + kr.target_display;
  document.getElementById('updateKrValue').value = kr.current_value;
  openModal('updateKrModal');
}

function submitUpdateKr() {
  const quarter = typeof CURRENT_QUARTER !== 'undefined' ? CURRENT_QUARTER :
    document.getElementById('quarterSelect')?.value || '';
  apiPost('/api/kr/update', {
    quarter: quarter,
    id: document.getElementById('updateKrId').value,
    okr_id: document.getElementById('updateKrOkrId').value,
    value: document.getElementById('updateKrValue').value,
  }).then(r => {
    if (r.ok) {
      showToast('Value updated', 'success');
      closeModal('updateKrModal');
      setTimeout(() => location.reload(), 500);
    }
  });
}

function deleteKr(krId, okrId, quarter, category) {
  showConfirm('Delete Key Result', 'This will permanently delete this key result.', function() {
    apiPost('/api/kr/delete', { id: krId, okr_id: okrId, quarter: quarter, category: category })
      .then(r => {
        if (r.ok) {
          showToast('Key Result deleted', 'success');
          setTimeout(() => location.reload(), 500);
        }
      });
  });
}

// ---------- Notes ----------

function addNote(parentType, parentId, inputId) {
  const input = document.getElementById(inputId);
  const text = input.value.trim();
  if (!text) { showToast('Note cannot be empty', 'error'); return; }

  apiPost('/api/note/add', {
    parent_type: parentType,
    parent_id: parentId,
    text: text,
  }).then(r => {
    if (r.ok) {
      // Add note to the UI immediately
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
