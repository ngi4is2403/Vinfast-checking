// main.js — UI interactivity

// ── Dark / Light Mode Toggle ──────────────────────────────────────
const THEME_KEY = 'parkev-theme';
const themeBtn  = document.getElementById('themeToggleBtn');
const themeIcon = document.getElementById('themeIcon');
const themeLabel = document.getElementById('themeLabel');

function applyTheme(theme) {
  document.documentElement.setAttribute('data-theme', theme);
  localStorage.setItem(THEME_KEY, theme);
  if (themeIcon)  themeIcon.textContent  = theme === 'light' ? '☀️' : '🌙';
  if (themeLabel) themeLabel.textContent = theme === 'light' ? 'Chế độ sáng' : 'Chế độ tối';
}

// Khởi tạo label đúng với theme đang dùng
applyTheme(localStorage.getItem(THEME_KEY) || 'dark');

if (themeBtn) {
  themeBtn.addEventListener('click', () => {
    const current = document.documentElement.getAttribute('data-theme') || 'dark';
    applyTheme(current === 'dark' ? 'light' : 'dark');
  });
}

// ── Logout Confirm Modal ──────────────────────────────────────
const logoutTrigger = document.getElementById('logoutTriggerBtn');
const logoutCancel  = document.getElementById('logoutCancelBtn');

// Query lazily — modal HTML nằm sau thẻ <script>, nên phải tìm lúc gọi
function openLogoutModal() {
  const m = document.getElementById('logoutModal');
  if (m) m.classList.add('open');
}
function closeLogoutModal() {
  const m = document.getElementById('logoutModal');
  if (m) m.classList.remove('open');
}

if (logoutTrigger) logoutTrigger.addEventListener('click', openLogoutModal);
if (logoutCancel)  logoutCancel.addEventListener('click', closeLogoutModal);

// Đóng khi click ra ngoài modal
document.addEventListener('click', (e) => {
  const m = document.getElementById('logoutModal');
  if (m && m.classList.contains('open') && e.target === m) closeLogoutModal();
});

// Đóng khi nhấn Escape
document.addEventListener('keydown', (e) => {
  if (e.key === 'Escape') closeLogoutModal();
});

// ── Flash messages auto-dismiss ──────────────────────────────────
document.querySelectorAll('.alert').forEach(el => {
  setTimeout(() => {
    el.style.transition = 'opacity .5s';
    el.style.opacity    = '0';
    setTimeout(() => el.remove(), 500);
  }, 4000);
});

// ── Tabs ─────────────────────────────────────────────────────────
document.querySelectorAll('[data-tab]').forEach(btn => {
  btn.addEventListener('click', () => {
    const target = btn.dataset.tab;
    const parent = btn.closest('.tabs-wrapper') || document;
    parent.querySelectorAll('.tab-btn').forEach(b => b.classList.remove('active'));
    parent.querySelectorAll('.tab-pane').forEach(p => p.classList.remove('active'));
    btn.classList.add('active');
    const pane = parent.querySelector(`#${target}`);
    if (pane) pane.classList.add('active');
  });
});

// ── Slot selector ─────────────────────────────────────────────────
const slotInput = document.getElementById('slot_id');
document.querySelectorAll('.slot-card[data-slot-id]').forEach(card => {
  card.addEventListener('click', () => {
    document.querySelectorAll('.slot-card').forEach(c => c.classList.remove('selected'));
    card.classList.add('selected');
    if (slotInput) slotInput.value = card.dataset.slotId;
  });
});

// ── Station selector ──────────────────────────────────────────────
const stationInput = document.getElementById('station_id');
document.querySelectorAll('.station-card[data-station-id]').forEach(card => {
  card.addEventListener('click', () => {
    document.querySelectorAll('.station-card').forEach(c => c.classList.remove('selected'));
    card.classList.add('selected');
    if (stationInput) stationInput.value = card.dataset.stationId;
    // Auto-set charge_type to match station
    const ctype = card.dataset.chargeType;
    const ctypeInput = document.getElementById('charge_type');
    if (ctypeInput && ctype) ctypeInput.value = ctype;
  });
});

// ── Vehicle type → filter slots (park page) ───────────────────────
const vehSelect = document.getElementById('vehicle_id');
if (vehSelect && document.getElementById('slots-container')) {
  vehSelect.addEventListener('change', async () => {
    const vid = vehSelect.value;
    const opt = vehSelect.options[vehSelect.selectedIndex];
    const vtype = opt.dataset.vtype || '';

    const res  = await fetch(`/api/slots?vehicle_type=${vtype}`);
    const data = await res.json();
    if (!data.success) return;

    const container = document.getElementById('slots-container');
    if (!container) return;

    const slots = data.data;
    if (!slots || !slots.length) {
      container.innerHTML = '<div class="empty-state"><div class="empty-icon">🅿️</div><p>Không có vị trí phù hợp.</p></div>';
      return;
    }

    // Group by zone
    const zoneA = slots.filter(s => s.zone === 'A');
    const zoneB = slots.filter(s => s.zone === 'B');

    let html = '';
    const renderZone = (label, list, icon) => {
      if (!list.length) return '';
      let h = `<div class="zone-label">${icon} ${label}</div><div class="slot-grid mb-4">`;
      list.forEach(s => {
        const stype = s.slot_type === 'small' ? 'Xe máy' : s.slot_type === 'large' ? 'Ô tô' : 'Tất cả';
        h += `<div class="slot-card" data-slot-id="${s.id}">
          <div class="slot-zone">Khu ${s.zone}</div>
          <div class="slot-code">${s.slot_code}</div>
          <div class="slot-type">${stype}</div>
          ${s.has_charging ? '<div class="slot-charging">⚡ Gần trụ sạc</div>' : ''}
        </div>`;
      });
      h += '</div>';
      return h;
    };
    html += renderZone('Khu A — Không sạc', zoneA, '🅿️');
    html += renderZone('Khu B — Có sạc', zoneB, '⚡');
    container.innerHTML = html;

    // Re-bind click
    container.querySelectorAll('.slot-card[data-slot-id]').forEach(card => {
      card.addEventListener('click', () => {
        container.querySelectorAll('.slot-card').forEach(c => c.classList.remove('selected'));
        card.classList.add('selected');
        if (slotInput) slotInput.value = card.dataset.slotId;
      });
    });
  });
}

// ── Modal helpers ─────────────────────────────────────────────────
function showModal(id) {
  const m = document.getElementById(id);
  if (m) {
    m.style.display = 'flex';
    setTimeout(() => m.classList.add('open'), 10);
  }
}
function hideModal(id) {
  const m = document.getElementById(id);
  if (m) {
    m.classList.remove('open');
    setTimeout(() => m.style.display = 'none', 200);
  }
}
document.querySelectorAll('[data-modal-open]').forEach(btn => {
  btn.addEventListener('click', () => showModal(btn.dataset.modalOpen));
});
document.querySelectorAll('[data-modal-close]').forEach(btn => {
  btn.addEventListener('click', () => hideModal(btn.dataset.modalClose));
});
