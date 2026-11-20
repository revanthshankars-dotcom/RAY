/* RAY interface - vanilla JS, no build step, no external assets.
   Every panel is bound to the real local API; nothing here is mocked. */

'use strict';

const state = {
  status: null,
  providers: null,
  dashboard: null,
  settings: null,
  section: 'home',
  eventsCursor: 0,
  approvals: [],
  taskId: null,
  chat: [],
  listening: false,
  lastSystem: null,
  calendarMonth: null,
  timers: {},
};

const ICONS = {
  home: 'M3 11.5 12 4l9 7.5M5.5 10v9h13v-9',
  assistant: 'M4 5h16v11H9l-5 4V5Z',
  voice: 'M12 3a3 3 0 0 1 3 3v6a3 3 0 0 1-6 0V6a3 3 0 0 1 3-3ZM5.5 11a6.5 6.5 0 0 0 13 0M12 17.5V21',
  computer: 'M3.5 5h17v10.5h-17zM8 20h8M12 15.5V20',
  files: 'M3.5 7.5h6l2 2h9v9h-17z',
  browser: 'M12 3a9 9 0 1 0 0 18 9 9 0 0 0 0-18Zm0 0c3 3.5 3 14.5 0 18m0-18c-3 3.5-3 14.5 0 18M3.6 9h16.8M3.6 15h16.8',
  productivity: 'M4 6h16M4 12h11M4 18h7',
  calendar: 'M4.5 6.5h15v13h-15zM8 3.5v5M16 3.5v5M4.5 11h15',
  notes: 'M6 3.5h9l4 4v13H6zM14.5 3.5v4.5H19M9 12h7M9 16h5',
  tasks: 'M4 6.5l2 2 3.5-3.5M4 13l2 2 3.5-3.5M13 7h7M13 14h7M13 20h7',
  reminders: 'M12 4a5.5 5.5 0 0 0-5.5 5.5v3.5L4.5 17h15L17.5 13V9.5A5.5 5.5 0 0 0 12 4Zm-2 16h4',
  automation: 'M5 8h14M5 16h14M9 5v6M15 13v6',
  agents: 'M12 3.5 5 7v5c0 4 3 7.4 7 8.5 4-1.1 7-4.5 7-8.5V7l-7-3.5ZM12 8v4M12 15h.01',
  knowledge: 'M5 4.5h6a3 3 0 0 1 3 3V20a2.5 2.5 0 0 0-2.5-2.5H5zM19 4.5h-2a3 3 0 0 0-3 3V20a2.5 2.5 0 0 1 2.5-2.5H19z',
  media: 'M9 18V7l10-2v11M9 18a3 3 0 1 1-6 0 3 3 0 0 1 6 0Zm10-2a3 3 0 1 1-6 0 3 3 0 0 1 6 0Z',
  system: 'M4 16.5V20h16v-3.5M7 16.5V10M12 16.5V5M17 16.5V12',
  security: 'M12 3.5 5 6.5v5c0 4.2 3 7.7 7 9 4-1.3 7-4.8 7-9v-5l-7-3ZM9.5 11.8l1.8 1.8 3.4-3.6',
  analytics: 'M4 19.5h16M6.5 16V11M11 16V6.5M15.5 16v-6M20 16V9',
  downloads: 'M12 4v10m0 0 4-4m-4 4-4-4M5 19h14',
  developer: 'M9 8l-4 4 4 4M15 8l4 4-4 4',
  settings: 'M12 15.2a3.2 3.2 0 1 0 0-6.4 3.2 3.2 0 0 0 0 6.4ZM19 12a7 7 0 0 0-.1-1.2l2-1.5-2-3.4-2.3 1a7 7 0 0 0-2-1.2L14.2 3h-4l-.4 2.5a7 7 0 0 0-2 1.2l-2.3-1-2 3.4 2 1.5a7 7 0 0 0 0 2.4l-2 1.5 2 3.4 2.3-1a7 7 0 0 0 2 1.2l.4 2.5h4l.4-2.5a7 7 0 0 0 2-1.2l2.3 1 2-3.4-2-1.5c.1-.4.1-.8.1-1.2Z',
  calculator: 'M6 3.5h12v17H6zM9 7h6M8.5 10.5h1M12 10.5h1M15.5 10.5h1M8.5 14h1M12 14h1M15.5 14h1M8.5 17.5h1M12 17.5h1M15.5 17.5h1',
  notepad: 'M7 3.5h8l4 4v13H7zM15 3.5v4.5h4M10 12h6M10 15.5h4',
  screenshot: 'M3.5 7.5h4l1.5-2h6l1.5 2h4v11h-17zM12 15.4a3 3 0 1 0 0-6 3 3 0 0 0 0 6Z',
  command: 'M12 3.2l1.9 5.4 5.4 1.9-5.4 1.9L12 17.8l-1.9-5.4L4.7 10.5l5.4-1.9z',
  cpu: 'M7 7h10v10H7zM9.5 3.5v3M14.5 3.5v3M9.5 17.5v3M14.5 17.5v3M3.5 9.5h3M3.5 14.5h3M17.5 9.5h3M17.5 14.5h3',
  memory: 'M5 6.5h14v11H5zM8.5 10h7M8.5 13.5h4M9 3.5v3M15 3.5v3M9 17.5v3M15 17.5v3',
  disk: 'M4.5 6.5h15v5h-15zM4.5 12.5h15v5h-15zM7 9h.01M7 15h.01',
  network: 'M12 20V9M7.5 12.5a6.5 6.5 0 0 1 9 0M4 9a11 11 0 0 1 16 0',
  battery: 'M3.5 8h15v8h-15zM20 10.5v3M6 10.5v3',
  folder: 'M3.5 7.5h6l2 2h9v9h-17z',
  search: 'M11 4.5a6.5 6.5 0 1 0 0 13 6.5 6.5 0 0 0 0-13ZM20 20l-4.4-4.4',
  plus: 'M12 5v14M5 12h14',
  external: 'M14 4h6v6M20 4l-8.5 8.5M18 14v6H4V6h6',
  lock: 'M6 11h12v9H6zM8.5 11V8a3.5 3.5 0 0 1 7 0v3',
  moon: 'M20 14.5A8 8 0 0 1 9.5 4 8.2 8.2 0 1 0 20 14.5Z',
  restart: 'M20 12a8 8 0 1 1-2.4-5.7M20 4v4.5h-4.5',
  power: 'M12 3.5V11M7.2 6.4a7 7 0 1 0 9.6 0',
  timer: 'M12 7v5l3 2M9 3.5h6M12 20.5a7 7 0 1 0 0-14 7 7 0 0 0 0 14Z',
  globe: 'M12 3a9 9 0 1 0 0 18 9 9 0 0 0 0-18Zm0 0c3 3.5 3 14.5 0 18m0-18c-3 3.5-3 14.5 0 18M3.6 9h16.8M3.6 15h16.8',
  bell: 'M12 3a6 6 0 0 0-6 6v4l-1.5 3h15L18 13V9a6 6 0 0 0-6-6ZM9.5 19a2.5 2.5 0 0 0 5 0',
  mic: 'M12 3a3 3 0 0 1 3 3v6a3 3 0 0 1-6 0V6a3 3 0 0 1 3-3ZM5.5 11a6.5 6.5 0 0 0 13 0M12 17.5V21',
  film: 'M4 5h16v14H4zM8 5v14M16 5v14M4 9h4M4 15h4M16 9h4M16 15h4',
  music: 'M9 18V7l10-2v11M9 18a3 3 0 1 1-6 0 3 3 0 0 1 6 0Zm10-2a3 3 0 1 1-6 0 3 3 0 0 1 6 0Z',
  check: 'M4.5 12.5l5 5 10-11',
  scan: 'M4 8V5.5A1.5 1.5 0 0 1 5.5 4H8M16 4h2.5A1.5 1.5 0 0 1 20 5.5V8M20 16v2.5a1.5 1.5 0 0 1-1.5 1.5H16M8 20H5.5A1.5 1.5 0 0 1 4 18.5V16M4 12h16',
};

const NAV = [
  { group: 'Workspace' },
  { id: 'home', label: 'Home' },
  { id: 'search', label: 'Search' },
  { id: 'assistant', label: 'AI Assistant' },
  { id: 'voice', label: 'Voice Assistant' },
  { id: 'computer', label: 'System Control' },
  { id: 'files', label: 'Files' },
  { id: 'browser', label: 'Browser' },
  { group: 'Productivity' },
  { id: 'productivity', label: 'Productivity' },
  { id: 'calendar', label: 'Calendar' },
  { id: 'notes', label: 'Notes' },
  { id: 'tasks', label: 'Tasks' },
  { id: 'reminders', label: 'Reminders' },
  { group: 'Intelligence' },
  { id: 'agents', label: 'Agent Manager' },
  { id: 'automation', label: 'Automation' },
  { id: 'knowledge', label: 'Knowledge Base' },
  { id: 'media', label: 'Media Center' },
  { group: 'System' },
  { id: 'system', label: 'System Monitor' },
  { id: 'security', label: 'Security Center' },
  { id: 'analytics', label: 'Analytics' },
  { id: 'downloads', label: 'Downloads' },
  { id: 'developer', label: 'Developer' },
  { id: 'settings', label: 'Settings' },
];

/* ------------------------------------------------------------------ utils */
const $ = (sel, root = document) => root.querySelector(sel);
const $$ = (sel, root = document) => Array.from(root.querySelectorAll(sel));
const view = () => $('#view-inner');

function esc(value) {
  if (value === null || value === undefined) return '';
  return String(value)
    .replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;')
    .replace(/"/g, '&quot;').replace(/'/g, '&#39;');
}

function icon(name, size = 16) {
  const path = ICONS[name] || ICONS.home;
  return `<svg viewBox="0 0 24 24" width="${size}" height="${size}" fill="none" stroke="currentColor"
    stroke-width="1.7" stroke-linecap="round" stroke-linejoin="round"><path d="${path}"/></svg>`;
}

function bytes(n) {
  if (n === null || n === undefined) return '—';
  const units = ['B', 'KB', 'MB', 'GB', 'TB'];
  let value = Number(n);
  let i = 0;
  while (value >= 1024 && i < units.length - 1) { value /= 1024; i += 1; }
  return `${value.toFixed(i === 0 ? 0 : 1)} ${units[i]}`;
}

function num(n, digits = 0) {
  if (n === null || n === undefined) return '—';
  return Number(n).toLocaleString(undefined, { maximumFractionDigits: digits });
}

function clamp(n, min, max) { return Math.max(min, Math.min(max, n)); }

function timeLabel(value) {
  if (!value) return '—';
  const text = String(value);
  const date = new Date(text.length <= 10 ? `${text}T00:00` : text);
  if (Number.isNaN(date.getTime())) return text;
  return date.toLocaleString(undefined, { day: '2-digit', month: 'short', hour: '2-digit', minute: '2-digit' });
}

function shortTime(value) {
  if (!value) return '—';
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return String(value);
  return date.toLocaleTimeString(undefined, { hour: '2-digit', minute: '2-digit' });
}

function relative(value) {
  if (!value) return '';
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return '';
  const diff = (Date.now() - date.getTime()) / 1000;
  if (Math.abs(diff) < 60) return 'just now';
  if (Math.abs(diff) < 3600) return `${Math.round(Math.abs(diff) / 60)} min ${diff > 0 ? 'ago' : 'from now'}`;
  if (Math.abs(diff) < 86400) return `${Math.round(Math.abs(diff) / 3600)} h ${diff > 0 ? 'ago' : 'from now'}`;
  return date.toLocaleDateString();
}

async function api(path, options = {}) {
  const init = { headers: { 'Content-Type': 'application/json' }, ...options };
  if (init.body && typeof init.body !== 'string') init.body = JSON.stringify(init.body);
  const response = await fetch(path, init);
  const type = response.headers.get('content-type') || '';
  if (!type.includes('application/json')) {
    const text = await response.text();
    if (!response.ok) throw new Error(text || `HTTP ${response.status}`);
    return text;
  }
  const payload = await response.json();
  if (!response.ok || payload.ok === false) {
    const error = new Error(payload.error || `HTTP ${response.status}`);
    error.status = response.status;
    error.payload = payload;
    throw error;
  }
  return payload.data;
}

function toast(title, body = '', kind = 'info', ttl = 4200) {
  const node = document.createElement('div');
  node.className = `toast ${kind}`;
  node.innerHTML = `<strong>${esc(title)}</strong>${body ? `<span class="muted small">${esc(body)}</span>` : ''}`;
  $('#toasts').appendChild(node);
  setTimeout(() => {
    node.style.opacity = '0';
    node.style.transform = 'translateX(14px)';
    node.style.transition = 'all 180ms';
    setTimeout(() => node.remove(), 200);
  }, ttl);
}

function openModal(html) {
  $('#modal-card').innerHTML = html;
  $('#modal-overlay').classList.remove('hidden');
}
function closeModal() {
  $('#modal-overlay').classList.add('hidden');
  $('#modal-card').innerHTML = '';
}

function confirmAction(title, body, confirmLabel = 'Confirm') {
  return new Promise((resolve) => {
    openModal(`
      <h3>${esc(title)}</h3>
      <p class="muted small">${esc(body)}</p>
      <div class="modal-actions">
        <button class="btn ghost" data-confirm="0">Cancel</button>
        <button class="btn danger" data-confirm="1">${esc(confirmLabel)}</button>
      </div>`);
    $('#modal-card').addEventListener('click', (event) => {
      const button = event.target.closest('[data-confirm]');
      if (!button) return;
      closeModal();
      resolve(button.dataset.confirm === '1');
    }, { once: true });
  });
}

/* ------------------------------------------------------------------- nav */
function renderNav() {
  const nav = $('#nav');
  nav.innerHTML = NAV.map((item) => {
    if (item.group) return `<div class="nav-group-label">${esc(item.group)}</div>`;
    const active = state.section === item.id ? 'active' : '';
    return `<button class="nav-item ${active}" data-section="${item.id}" title="${esc(item.label)}">
      ${icon(item.id)}<span>${esc(item.label)}</span></button>`;
  }).join('');
}

const DOCK = [
  { id: 'home', icon: 'home', label: 'Home' },
  { id: 'assistant', icon: 'assistant', label: 'Assistant' },
  { id: 'files', icon: 'files', label: 'Files' },
  { id: 'media', icon: 'media', label: 'Media' },
  { id: 'security', icon: 'security', label: 'Security' },
  { id: 'settings', icon: 'settings', label: 'Settings' },
];

function renderDock() {
  const dock = $('#dock');
  if (!dock) return;
  dock.innerHTML = DOCK.map((item) => `
    <button class="dock-item ${state.section === item.id ? 'active' : ''}" data-act="go"
      data-section="${item.id}" title="${esc(item.label)}" aria-label="${esc(item.label)}">${icon(item.icon, 19)}</button>`).join('')
    + '<span class="dock-sep"></span>'
    + `<button class="dock-item" data-act="palette" title="Command center (Ctrl+Space)" aria-label="Command center">
        <svg viewBox="0 0 24 24" width="19" height="19" fill="none" stroke="currentColor" stroke-width="1.8">
          <circle cx="11" cy="11" r="7"/><path d="m20 20-3.5-3.5"/></svg></button>`;
}

function startClock() {
  const tick = () => {
    const now = new Date();
    const clock = $('#topbar-time');
    if (clock) clock.textContent = now.toLocaleTimeString(undefined, { hour: '2-digit', minute: '2-digit' });
    const date = $('#topbar-date');
    if (date) date.textContent = now.toLocaleDateString(undefined, { weekday: 'short', day: '2-digit', month: 'short', year: 'numeric' });
    const hero = $('#hero-time');
    if (hero) hero.textContent = now.toLocaleTimeString(undefined, { hour: '2-digit', minute: '2-digit' });
  };
  tick();
  setInterval(tick, 10000);
}

function go(section) {
  if (section === 'search') { openPalette(); return; }
  if (!section) section = 'home';
  state.section = section;
  if (location.hash !== `#/${section}`) history.replaceState(null, '', `#/${section}`);
  renderNav();
  renderDock();
  Object.values(state.timers).forEach((id) => clearInterval(id));
  state.timers = {};
  const renderer = VIEWS[section] || VIEWS.home;
  const label = (NAV.find((item) => item.id === section) || {}).label || 'Home';
  $('#view-title').textContent = label;
  $('#view').scrollTop = 0;
  renderer().catch((error) => {
    view().innerHTML = `<div class="card"><div class="empty">${esc(error.message)}</div></div>`;
  });
}

/* -------------------------------------------------------------- command bar */
const SUGGESTIONS = [
  'Open my documents folder',
  'Find the latest PDF I downloaded',
  'Check my laptop performance',
  'Remind me to exercise at 6pm',
  'Add task Submit the Companies Act notes tomorrow at 5pm',
  'Organise my Downloads folder',
  'Create a folder named Law Projects in documents',
  'Search the web for the Bharatiya Nyaya Sanhita',
  'Take a screenshot',
  'Play my REVX playlist',
  'What is on my schedule today',
  'Lock the computer',
];

function openPalette() {
  $('#palette-overlay').classList.remove('hidden');
  const input = $('#palette-input');
  input.value = '';
  input.focus();
  renderPalette();
}

function closePalette() {
  $('#palette-overlay').classList.add('hidden');
}

function renderPalette(filter = '') {
  const body = $('#palette-body');
  const query = filter.trim().toLowerCase();
  const matches = SUGGESTIONS.filter((item) => !query || item.toLowerCase().includes(query));
  body.innerHTML = `
    <div class="palette-hint">Type a request and press Enter. RAY plans it, runs it, and reports what actually happened.</div>
    ${matches.map((item) => `<button class="suggestion" data-suggest="${esc(item)}">
      ${icon('assistant')}<span>${esc(item)}</span><span class="kind">suggestion</span></button>`).join('')}
    ${query && !matches.length ? `<div class="empty">Press Enter to send this to RAY</div>` : ''}`;
}

async function runCommand(text, options = {}) {
  text = (text || '').trim();
  if (!text) return null;
  closePalette();
  if (options.chat) return runInChat(text);
  openModal(`
    <h3>Working on it</h3>
    <p class="muted small" id="cmd-text">${esc(text)}</p>
    <div class="steps" id="cmd-steps"><div class="step"><span class="idx">…</span>
      <span>Planning with RAY</span><span class="faint small">thinking</span></div></div>
    <div class="modal-actions"><button class="btn ghost" id="cmd-cancel">Cancel</button>
      <button class="btn" id="cmd-close" disabled>Close</button></div>`);
  $('#cmd-cancel').addEventListener('click', async () => {
    if (state.taskId) await api('/api/command/cancel', { method: 'POST', body: { task_id: state.taskId } }).catch(() => {});
    closeModal();
  });

  const started = Date.now();
  try {
    const result = await api('/api/command', { method: 'POST', body: { text, speak: true } });
    state.taskId = result.task_id;
    renderCommandResult(result);
    if (result.status === 'awaiting_approval') {
      toast('Approval needed', 'Open the bell icon or approve in the dialog.', 'warn');
      setTimeout(loadApprovals, 400);
    } else if (result.speak) {
      speak(result.speak);
    }
    bumpActivity();
    return result;
  } catch (error) {
    openModal(`<h3>That did not work</h3><p class="muted small">${esc(error.message)}</p>
      <div class="modal-actions"><button class="btn" data-act="close-modal">Close</button></div>`);
    return null;
  } finally {
    state.lastCommandMs = Date.now() - started;
  }
}

function renderCommandResult(result) {
  const steps = (result.outcomes || []).map((outcome) => {
    const cls = outcome.requires_approval ? 'wait' : outcome.ok ? 'ok' : 'fail';
    const mark = outcome.requires_approval ? '!' : outcome.ok ? '✓' : '×';
    return `<div class="step ${cls}">
      <span class="idx">${mark}</span>
      <span><span class="tool">${esc(outcome.tool)}</span> ${esc(outcome.error || outcome.why || '')}</span>
      <span class="faint small">${outcome.duration_ms} ms</span></div>`;
  }).join('');
  const pending = result.outcomes?.find((outcome) => outcome.requires_approval);
  openModal(`
    <h3>${result.status === 'completed' ? 'Done' : result.status === 'awaiting_approval' ? 'Waiting for your approval' : esc(result.status)}</h3>
    <p class="muted small">${esc(result.text)} · ${esc(result.agent)} · ${result.duration_ms} ms</p>
    ${steps ? `<div class="steps">${steps}</div>` : ''}
    ${result.answer ? `<div class="card tight" style="margin-top:12px;white-space:pre-wrap">${esc(result.answer)}</div>` : ''}
    ${result.error ? `<div class="banner error" style="margin-top:10px">${esc(result.error)}</div>` : ''}
    <div class="modal-actions">
      ${pending ? `<button class="btn danger" id="cmd-deny">Deny</button>
                   <button class="btn primary" id="cmd-approve">Approve and continue</button>` : ''}
      <button class="btn" data-act="close-modal">Close</button>
    </div>`);
  if (pending) {
    $('#cmd-approve').addEventListener('click', async () => {
      try {
        const approval = await api('/api/approvals', {
          method: 'POST',
          body: { approval_id: pending.requires_approval, approved: true, task_id: result.task_id },
        });
        closeModal();
        if (approval.resumed) renderCommandResult(approval.resumed);
        toast('Approved', 'RAY continued the task.', 'ok');
      } catch (error) { toast('Could not approve', error.message, 'error'); }
    });
    $('#cmd-deny').addEventListener('click', async () => {
      await api('/api/approvals', { method: 'POST', body: { approval_id: pending.requires_approval, approved: false } })
        .catch((error) => toast('Could not deny', error.message, 'error'));
      closeModal();
      loadApprovals();
    });
  }
}

async function runInChat(text) {
  go('assistant');
  await new Promise((resolve) => setTimeout(resolve, 120));
  const input = $('#chat-input');
  if (input) { input.value = text; }
  return sendChat();
}

/* ----------------------------------------------------------------- events */
function bumpActivity() {
  loadApprovals();
}

async function loadApprovals() {
  try {
    const data = await api('/api/approvals');
    state.approvals = data.pending || [];
    const badge = $('#approval-count');
    if (state.approvals.length) {
      badge.textContent = state.approvals.length;
      badge.classList.remove('hidden');
    } else {
      badge.classList.add('hidden');
    }
    $('#dot-engine').className = `dot ${data.policy?.emergency_stop ? 'bad' : 'ok'}`;
    $('#label-engine').textContent = data.policy?.emergency_stop ? 'Emergency stop engaged' : 'Engine online';
  } catch (error) { /* offline is handled by status polling */ }
}

async function pollEvents() {
  try {
    const data = await api(`/api/events?after=${state.eventsCursor}`);
    state.eventsCursor = data.latest || state.eventsCursor;
    for (const event of data.events || []) {
      handleEvent(event);
    }
  } catch (error) { /* ignore */ }
  setTimeout(pollEvents, 2500);
}

function handleEvent(event) {
  const payload = event.payload || {};
  if (event.kind === 'notification' && payload.title) {
    toast(payload.title, payload.body || '', payload.kind === 'error' ? 'error' : 'info');
    if (state.section === 'home') setTimeout(() => { if (state.section === 'home') VIEWS.home(); }, 400);
  }
  if (event.kind === 'reminder_due') {
    const reminder = payload.reminder || {};
    toast('Reminder', reminder.title || '', 'warn', 9000);
  }
  if (event.kind === 'approval') {
    loadApprovals();
    toast('Approval requested', `${payload.tool} (level ${payload.level})`, 'warn', 7000);
  }
  if (event.kind === 'workflow_run') {
    toast(`Automation: ${payload.workflow}`, `Status ${payload.status}`, payload.status === 'ok' ? 'ok' : 'warn');
  }
  if (event.kind === 'speak_request' && payload.text) {
    speak(payload.text);
  }
  if (event.kind === 'security') {
    loadApprovals();
  }
}

/* ------------------------------------------------------------------ voice */
let currentAudio = null;

function speak(text, options = {}) {
  if (!text) return;
  if (!state.status?.voice_enabled && !options.force) {
    if (!state.voiceWarned) {
      state.voiceWarned = true;
      toast('Voice is off', 'Enable it in Settings → Voice to hear RAY speak.', 'warn');
    }
    return;
  }
  const body = { text: text.slice(0, 900), force: true };
  fetch('/api/voice/speak', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(body),
  }).then(async (response) => {
    const type = response.headers.get('content-type') || '';
    if (!type.startsWith('audio/')) {
      const payload = await response.json().catch(() => ({}));
      toast('Voice unavailable', payload.error || 'Fish Audio is not configured.', 'warn');
      return;
    }
    const blob = await response.blob();
    const url = URL.createObjectURL(blob);
    if (currentAudio) { currentAudio.pause(); }
    currentAudio = new Audio(url);
    currentAudio.volume = clamp(Number(state.settings?.config?.voice?.volume ?? 0.9), 0, 1);
    currentAudio.playbackRate = clamp(Number(state.settings?.config?.voice?.speed ?? 1), 0.5, 2);
    currentAudio.addEventListener('ended', () => {
      const node = $('#speak-status');
      if (node) node.textContent = '';
    });
    currentAudio.play()
      .then(() => { const node = $('#speak-status'); if (node) node.textContent = 'Playing'; })
      .catch(() => toast('Playback blocked', 'Click the page once, then try again.', 'warn'));
  }).catch((error) => toast('Voice failed', error.message, 'error'));
}

function initRecognition() {
  const Recognition = window.SpeechRecognition || window.webkitSpeechRecognition;
  if (!Recognition) return null;
  const recognition = new Recognition();
  recognition.lang = 'en-IN';
  recognition.continuous = false;
  recognition.interimResults = true;
  return recognition;
}

/* ---------------------------------------------------------------- charts */
function lineChart(canvas, series, options = {}) {
  if (!canvas) return;
  const dpr = window.devicePixelRatio || 1;
  const width = canvas.clientWidth || 400;
  const height = canvas.clientHeight || 150;
  canvas.width = width * dpr;
  canvas.height = height * dpr;
  const ctx = canvas.getContext('2d');
  ctx.scale(dpr, dpr);
  ctx.clearRect(0, 0, width, height);
  const spark = Boolean(options.spark);
  const pad = spark ? { l: 0, r: 0, t: 3, b: 3 } : { l: 30, r: 8, t: 10, b: 18 };
  const colors = options.colors || ['#2fd6e8', '#9d8cff'];
  const max = Math.max(options.max || 10, ...series.flatMap((s) => s.values));
  ctx.strokeStyle = 'rgba(29,42,68,0.7)';
  ctx.lineWidth = 1;
  for (let i = 0; i <= 4 && !spark; i += 1) {
    const y = pad.t + ((height - pad.t - pad.b) * i) / 4;
    ctx.beginPath(); ctx.moveTo(pad.l, y); ctx.lineTo(width - pad.r, y); ctx.stroke();
    ctx.fillStyle = 'rgba(107,122,150,0.85)';
    ctx.font = '9px ui-monospace, monospace';
    const value = Math.round(max - (max * i) / 4);
    ctx.fillText(String(value), 4, y + 3);
  }
  series.forEach((entry, index) => {
    const values = entry.values;
    if (!values.length) return;
    ctx.beginPath();
    values.forEach((value, i) => {
      const x = pad.l + ((width - pad.l - pad.r) * i) / Math.max(1, values.length - 1);
      const y = pad.t + (height - pad.t - pad.b) * (1 - value / max);
      if (i === 0) ctx.moveTo(x, y); else ctx.lineTo(x, y);
    });
    ctx.strokeStyle = colors[index % colors.length];
    ctx.lineWidth = 2;
    ctx.stroke();
    ctx.lineTo(width - pad.r, height - pad.b);
    ctx.lineTo(pad.l, height - pad.b);
    ctx.closePath();
    const gradient = ctx.createLinearGradient(0, pad.t, 0, height);
    gradient.addColorStop(0, `${colors[index % colors.length]}33`);
    gradient.addColorStop(1, 'rgba(0,0,0,0)');
    ctx.fillStyle = gradient;
    ctx.fill();
  });
}

/* ------------------------------------------------------------------ views */
const VIEWS = {};

/* ------------------------------------------------------------------- home */
const PROVIDERS = [
  {
    name: 'cursor',
    label: 'Cursor',
    kind: 'cli',
    role: 'primary reasoning · chat · code (official Cursor CLI)',
    docs: 'https://cursor.com/cli',
    env: 'RAY_CURSOR_API_KEY',
    optional: false,
  },
  {
    name: 'gemini',
    label: 'Gemini',
    kind: 'key',
    role: 'documents · research · images · tool planning',
    keyUrl: 'https://aistudio.google.com/apikey',
    env: 'RAY_GEMINI_API_KEY',
    optional: false,
  },
  {
    name: 'fish',
    label: 'Fish Audio',
    kind: 'key',
    role: 'natural speech',
    keyUrl: 'https://fish.audio',
    env: 'RAY_FISH_API_KEY',
    optional: false,
  },
  {
    name: 'grok',
    label: 'Grok',
    kind: 'key',
    role: 'legacy reasoning engine (optional, off by default)',
    keyUrl: 'https://console.x.ai',
    env: 'RAY_GROK_API_KEY',
    optional: true,
  },
];

const CURSOR_INSTALL = {
  unix: 'curl https://cursor.com/install -fsS | bash',
  windows: "irm 'https://cursor.com/install?win32=true' | iex",
  login: 'cursor-agent login',
};

function describeProvider(name) {
  const list = state.status?.provider_detail?.providers || [];
  return list.find((entry) => entry.name === name) || null;
}

function providerState(name) {
  const health = (state.dashboard?.providers || {})[name] || {};
  const status = (state.status?.providers || {})[name] || {};
  const described = describeProvider(name) || {};
  return {
    available: Boolean(health.available ?? status.available ?? described.available),
    configured: Boolean(described.configured ?? status.configured ?? health.configured),
    detail: health.detail || described.detail || status.detail || '',
    binary: described.binary || '',
    kind: name === 'cursor' ? 'cli' : 'key',
  };
}

function providerTone(entry) {
  const current = providerState(entry.name);
  if (current.available) return { tone: 'ok', label: entry.kind === 'cli' ? 'ready' : 'connected' };
  if (current.configured) return { tone: 'warn', label: entry.kind === 'cli' ? 'needs sign-in' : 'unreachable' };
  return { tone: 'bad', label: entry.kind === 'cli' ? 'CLI not found' : 'no key' };
}

function openCursorHelp() {
  const current = providerState('cursor');
  openModal(`
    <h3>Cursor as RAY's reasoning engine</h3>
    <p class="muted small">Cursor has no public chat API, so RAY drives the official Cursor CLI in
      read-only print mode (<span class="mono">--mode ask</span>). Replies can never edit files or run
      commands on this PC.</p>
    <div class="list">
      <div class="list-item" style="align-items:flex-start"><span class="grow">
        <span class="title">1 · Install the CLI</span>
        <div class="meta mono">${esc(CURSOR_INSTALL.unix)}</div>
        <div class="meta">Windows PowerShell: <span class="mono">${esc(CURSOR_INSTALL.windows)}</span></div>
      </span></div>
      <div class="list-item" style="align-items:flex-start"><span class="grow">
        <span class="title">2 · Sign in</span>
        <div class="meta mono">${esc(CURSOR_INSTALL.login)}</div>
        <div class="meta">…or store a Cursor API key in Settings; RAY passes it as <span class="mono">CURSOR_API_KEY</span>.</div>
      </span></div>
      <div class="list-item">
        <span class="grow"><span class="title">3 · Check it</span>
          <div class="meta">${esc(current.detail || 'Install the CLI, then run the test.')}</div></span>
        <button class="btn sm" data-act="credential-test" data-name="cursor">Test</button>
      </div>
    </div>
    <div class="faint tiny" style="margin-top:10px">If the CLI is missing, chat falls back to Gemini automatically.
      Tool planning always runs on a tool-capable model, because the CLI cannot call RAY's 80 tools.</div>
    <div class="modal-actions">
      <button class="btn primary" data-act="go" data-section="settings">Open settings</button>
      <button class="btn" data-act="close-modal">Close</button>
    </div>`);
}

function openKeyHelp() {
  openModal(`
    <h3>Connect your AI</h3>
    <p class="muted small">Cursor is the default reasoning engine and needs no key — just the CLI and a
      sign-in. Gemini adds documents, research and tool planning; Fish Audio speaks. Keys are encrypted
      with a key file inside RAY's data folder and never displayed again.</p>
    ${PROVIDERS.map((provider) => `
      <div class="list-item" style="align-items:flex-start">
        <span class="grow">
          <span class="title">${esc(provider.label)}${provider.optional ? ' <span class="faint tiny">(optional)</span>' : ''}</span>
          <div class="meta">${esc(provider.role)}</div>
          ${provider.kind === 'cli'
            ? `<div class="meta mono">${esc(CURSOR_INSTALL.unix)}</div>
               <div class="meta">then <span class="mono">${esc(CURSOR_INSTALL.login)}</span> · <a href="${provider.docs}" target="_blank" rel="noreferrer noopener">${esc(provider.docs)}</a></div>`
            : `<div class="meta">Get a key: <a href="${provider.keyUrl}" target="_blank" rel="noreferrer noopener">${esc(provider.keyUrl)}</a></div>
               <div class="meta mono">or set ${esc(provider.env)}</div>`}
        </span>
      </div>`).join('')}
    <div class="banner warn" style="margin-top:12px">
      Never paste a key into a chat or a source file — enter it in Settings so it stays encrypted on your PC.
    </div>
    <div class="modal-actions">
      <button class="btn primary" data-act="go" data-section="settings">Open settings</button>
      <button class="btn" data-act="close-modal">Close</button>
    </div>`);
}

function providersCard() {
  const entries = PROVIDERS.filter(
    (provider) => !provider.optional || providerState(provider.name).configured || providerState(provider.name).available,
  );
  const rows = entries.map((provider) => {
    const current = providerState(provider.name);
    const { tone, label } = providerTone(provider);
    const action = current.available
      ? `<button class="btn sm ghost" data-act="credential-test" data-name="${provider.name}">Test</button>`
      : provider.kind === 'cli'
        ? '<button class="btn sm primary" data-act="cursor-help">Set up</button>'
        : '<button class="btn sm primary" data-act="go" data-section="settings">Add key</button>';
    return `<div class="provider-row">
      <span class="provider-dot ${tone}" title="${esc(label)}"></span>
      <div>
        <div class="strong">${esc(provider.label)}</div>
        <div class="faint tiny">${esc(current.detail || provider.role)}</div>
      </div>
      <div class="row">
        <span class="pill ${tone}">${esc(label)}</span>
        ${action}
      </div>
    </div>`;
  }).join('');
  const online = entries.some((provider) => providerState(provider.name).available);
  return `<div class="card">
    <div class="card-head"><h2>AI providers</h2><span class="spacer"></span>
      <span class="pill ${online ? 'ok' : 'warn'}">${online ? 'online' : 'offline'}</span></div>
    ${rows}
    <div class="row wrap" style="margin-top:10px">
      <span class="faint tiny">Local first. Nothing is sent until you ask.</span>
      <span class="spacer"></span>
      <button class="btn sm ghost" data-act="key-help">Setup guide</button>
    </div>
  </div>`;
}

function kpiCard(id, label, value, unit, hint, tone, iconName, values, max) {
  const list = (values && values.length ? values : [0, 0]).slice(-44).join(',');
  const color = tone === 'violet' ? '#9b8cff' : tone === 'amber' ? '#fbbf24' : tone === 'pink' ? '#f472b6' : '#3ddcf5';
  return `<div class="kpi">
    <div class="kpi-top"><span class="kpi-icon ${tone}">${icon(iconName, 15)}</span>${esc(label)}</div>
    <div class="kpi-value">${value}<small>${unit}</small></div>
    <div class="kpi-label">${esc(hint)}</div>
    <canvas class="spark" id="spark-${id}" data-values="${list}" data-max="${max || 100}" data-color="${color}"></canvas>
  </div>`;
}

function drawSparks() {
  $$('.spark').forEach((canvas) => {
    const values = String(canvas.dataset.values || '').split(',').map(Number).filter((value) => !Number.isNaN(value));
    lineChart(canvas, [{ values: values.length > 1 ? values : [0, 0] }], {
      spark: true,
      colors: [canvas.dataset.color || '#3ddcf5'],
      max: Number(canvas.dataset.max) || 100,
    });
  });
}

function miniCalendar(month) {
  const weeks = (month && month.weeks) || [];
  const head = ['M', 'T', 'W', 'T', 'F', 'S', 'S']
    .map((day) => `<div class="faint tiny center">${day}</div>`).join('');
  const cells = weeks.flat().map((day) => {
    const dayNumber = Number(String(day.date).slice(8, 10));
    const title = day.events && day.events.length ? day.events.map((event) => event.title).join(', ') : '';
    return `<button class="mini-day ${day.is_today ? 'today' : ''} ${day.in_month ? '' : 'out'}"
      data-act="calendar-day" data-date="${day.date}" title="${esc(title)}">${dayNumber}${day.events && day.events.length ? '<i></i>' : ''}</button>`;
  }).join('');
  return `<div class="card">
    <div class="card-head"><h2>${esc((month && month.label) || 'Calendar')}</h2><span class="spacer"></span>
      <button class="btn sm ghost" data-act="go" data-section="calendar">Open</button></div>
    <div class="mini-cal">${head}${cells}</div>
  </div>`;
}




function usageCard(analytics) {
  const usage = analytics?.usage?.totals || {};
  const focus = analytics?.focus || {};
  const cache = analytics?.cache || {};
  const database = analytics?.database || {};
  return `<div class="stat-row" style="justify-content:space-between">
      <div class="stat"><div class="n">${num(usage.calls || 0)}</div><div class="l">AI calls</div></div>
      <div class="stat"><div class="n">${num((usage.input_tokens || 0) + (usage.output_tokens || 0))}</div><div class="l">tokens</div></div>
      <div class="stat"><div class="n">${num(usage.cached || 0)}</div><div class="l">cached</div></div>
      <div class="stat"><div class="n">${num(focus.total_minutes || 0)}m</div><div class="l">focus</div></div>
      <div class="stat"><div class="n">${bytes(database.size_bytes || 0)}</div><div class="l">database</div></div>
      <div class="stat"><div class="n">${num(cache.live || 0)}</div><div class="l">cache entries</div></div>
    </div>
    <div class="faint tiny" style="margin-top:10px">Totals come from RAY's local usage log · nothing leaves this PC unless a provider call needs it.</div>`;
}


function updateStatusChips(system) {
  const net = $('#status-net');
  const netLabel = $('#status-net-label');
  if (net && netLabel) {
    const down = Number(system?.network?.download_bps || 0);
    netLabel.textContent = rate(down);
    net.classList.remove('off');
  }
  const battery = $('#status-battery');
  const batteryLabel = $('#status-battery-label');
  if (battery && batteryLabel) {
    const pack = system?.battery;
    if (pack) {
      batteryLabel.textContent = `${Math.round(pack.percent)}%${pack.plugged ? ' ⚡' : ''}`;
      battery.classList.remove('off');
      battery.classList.toggle('warn', pack.percent <= 30 && !pack.plugged);
      battery.classList.toggle('bad', pack.percent <= 15 && !pack.plugged);
    } else {
      battery.classList.add('off');
    }
  }
}

function ringTile({ label, percent, center, hint, color, unknown }) {
  const known = !unknown && typeof percent === 'number' && !Number.isNaN(percent);
  const value = known ? clamp(percent, 0, 100) : 0;
  const circumference = 2 * Math.PI * 22;
  const offset = circumference * (1 - value / 100);
  return `<div class="ring-tile">
    <div class="ring-sm ${known ? '' : 'none'}">
      <svg viewBox="0 0 54 54">
        <circle cx="27" cy="27" r="22" fill="none" stroke="rgba(120,190,255,0.16)" stroke-width="6"/>
        <circle cx="27" cy="27" r="22" fill="none" stroke="${color}" stroke-width="6"
          stroke-linecap="round" stroke-dasharray="${circumference}"
          stroke-dashoffset="${known ? offset : circumference}"/>
      </svg>
      <div class="ring-value">${known ? center || `${Math.round(percent)}%` : '—'}</div>
    </div>
    <div class="meta">
      <span class="name">${esc(label)}</span>
      <span class="val">${known ? (center || `${Math.round(percent)}%`) : 'n/a'}</span>
      <span class="hint">${esc(hint || '')}</span>
    </div>
  </div>`;
}

function tempReading(system) {
  const temps = system?.temperatures || {};
  if (!temps.available || !Array.isArray(temps.sensors) || !temps.sensors.length) {
    return { known: false, reason: temps.reason || 'no temperature sensor exposed' };
  }
  const hottest = temps.sensors.reduce((best, sensor) => (
    sensor.current_c > (best ? best.current_c : -Infinity) ? sensor : best
  ), null);
  if (!hottest) return { known: false, reason: 'no readings' };
  return { known: true, celsius: hottest.current_c, label: hottest.label || hottest.chip };
}

function featureGrid() {
  return NAV.filter((item) => item.id && item.id !== 'search')
    .map((item) => `<button class="feature" data-act="go" data-section="${item.id}" title="Open ${esc(item.label)}">
        ${icon(item.id, 19)}<span class="label">${esc(item.label)}</span></button>`).join('');
}

const QUICK_TOOLS = [
  { label: 'Note', icon: 'notepad', tone: '', act: 'go', section: 'notes' },
  { label: 'Calculator', icon: 'calculator', tone: 'violet', act: 'launch-app', target: 'calculator' },
  { label: 'Focus timer', icon: 'timer', tone: 'amber', act: 'go', section: 'productivity' },
  { label: 'Screenshot', icon: 'screenshot', tone: 'green', act: 'screenshot' },
  { label: 'System report', icon: 'system', tone: 'pink', act: 'quick', cmd: 'check my system' },
  { label: 'Command', icon: 'command', tone: '', act: 'palette' },
];

const BROWSER_LINKS = [
  { label: 'Google', letter: 'G', url: 'https://www.google.com' },
  { label: 'YouTube', letter: 'Y', url: 'https://www.youtube.com' },
  { label: 'Wikipedia', letter: 'W', url: 'https://en.wikipedia.org' },
  { label: 'GitHub', letter: 'G', url: 'https://github.com' },
];

function chatMessageMarkup(message) {
  const user = message.role === 'user';
  return `<div class="chat-msg ${user ? 'user' : ''}">
    <span class="who">${user ? 'You' : 'R'}</span>
    <div class="chat-bubble ${message.kind || ''}">${esc(message.text)}</div>
  </div>`;
}

function homeChatGreeting() {
  const name = String(state.dashboard?.user_name || '').trim();
  return `Hi${name ? ` ${name}` : ''} — ask me anything, or pick a quick tool. Replies come from your configured provider; anything that changes this PC still goes through RAY's permission checks.`;
}

function paintHomeChat() {
  const log = $('#home-chat-log');
  if (!log) return;
  const messages = state.homeChat?.messages || [];
  log.innerHTML = messages.length
    ? messages.map(chatMessageMarkup).join('')
    : `<div class="chat-msg"><span class="who">R</span><div class="chat-bubble">${esc(homeChatGreeting())}</div></div>`;
  log.scrollTop = log.scrollHeight;
}

async function sendHomeChat(text) {
  state.homeChat = state.homeChat || { agent: 'assistant', messages: [] };
  state.homeChat.messages.push({ role: 'user', text });
  state.homeChat.messages.push({ role: 'ray', text: 'Thinking…', kind: 'thinking' });
  paintHomeChat();
  try {
    const result = await api('/api/command', {
      method: 'POST',
      body: { text, agent: state.homeChat.agent || 'assistant' },
    });
    const reply = result.answer || result.error || (result.status === 'completed' ? 'Done.' : 'No reply.');
    state.homeChat.messages[state.homeChat.messages.length - 1] = { role: 'ray', text: reply };
  } catch (error) {
    state.homeChat.messages[state.homeChat.messages.length - 1] = {
      role: 'ray', text: `I could not reach a model: ${error.message}`, kind: 'error',
    };
  }
  paintHomeChat();
}

VIEWS.home = async function () {
  const [data, live, notes, month, analytics, security, automation, agents, media] = await Promise.all([
    api('/api/dashboard'),
    api('/api/system?history=48').catch(() => ({ history: [], snapshot: null })),
    api('/api/notes').catch(() => ({ notes: [] })),
    api('/api/calendar?view=month').catch(() => ({ weeks: [] })),
    api('/api/analytics').catch(() => null),
    api('/api/security').catch(() => null),
    api('/api/automation').catch(() => null),
    api('/api/agents').catch(() => ({ agents: [] })),
    api('/api/media?limit=60').catch(() => ({ library: [] })),
  ]);
  state.dashboard = data;

  const system = live.snapshot || data.system || {};
  const history = live.history || [];
  const cpu = system.cpu?.percent ?? 0;
  const memory = system.memory?.percent ?? 0;
  const diskPercent = system.disks?.[0]?.percent ?? 0;
  const temp = tempReading(system);
  const tempPercent = temp.known ? Math.min(100, (temp.celsius / 110) * 100) : 0;
  const warnings = system.warnings || [];
  const name = String(data.user_name || '').trim();
  const initial = (name || 'R').charAt(0).toUpperCase();
  const now = new Date();
  const dayPercent = Math.round(((now.getHours() * 60 + now.getMinutes()) / 1440) * 100);
  const policy = security?.policy || data.security || {};
  const pending = (security?.pending || []).length;
  const auditTotal = security?.stats?.total || 0;
  const online = !data.offline;

  const workflows = automation?.workflows || [];
  const agentList = agents.agents || [];
  const library = media.library || [];
  const audioCount = library.filter((item) => item.kind === 'audio').length;
  const videoCount = library.filter((item) => item.kind === 'video').length;

  view().innerHTML = `
    ${data.offline ? `<div class="banner">
      <span>RAY is offline: local tools still work. Install the Cursor CLI or add a Gemini key to enable chat, research and tool planning.</span>
      <button class="btn sm primary" data-act="key-help">Setup guide</button></div>` : ''}
    ${warnings.length ? `<div class="banner warn">⚠ ${esc(warnings.join(' · '))}</div>` : ''}

    <section class="home-cmd">
      <div class="card">
        <div class="hero-main">
          <div class="hero-avatar">${esc(initial)}</div>
          <div style="min-width:0">
            <h2>${esc(data.greeting)}${name ? `, ${esc(name)}` : ''}!</h2>
            <div class="sub">${esc(data.date_label)} · ${data.tasks_stats.open} open task(s) · ${data.reminders.length} reminder(s) in the next day</div>
            <div class="progress" title="Day ${dayPercent}% elapsed"><i style="width:${dayPercent}%"></i></div>
            <div class="row wrap" style="margin-top:10px">
              <span class="pill ${online ? 'ok' : 'warn'}">${online ? 'AI connected' : 'offline mode'}</span>
              <span class="pill ${policy.emergency_stop ? 'bad' : 'ok'}">${policy.emergency_stop ? 'emergency stop' : 'permissions active'}</span>
              <span class="pill info">${data.automation.active}/${data.automation.workflows} automations</span>
            </div>
          </div>
        </div>
        <div class="quote" style="margin-top:16px">
          <p>"A better tomorrow starts with what you do today."</p>
          <div class="by">RAY · running locally on this PC</div>
        </div>
      </div>

      <div class="card orb-card">
        <div class="orb-stage" aria-hidden="true">
          <div class="orb"><div class="orb-core">RAY</div></div>
        </div>
        <div class="orb-word">Always Here. Always Ahead.</div>
        <div class="orb-sub">${online ? 'connected and ready' : 'offline · local tools ready'}</div>
        <div class="orb-actions">
          <button class="btn sm primary" data-act="go" data-section="voice">Talk to RAY</button>
          <button class="btn sm" data-act="palette">Command</button>
          <button class="btn sm ghost" data-act="go" data-section="assistant">Ask RAY</button>
        </div>
      </div>

      <div class="card">
        <div class="card-head"><h2>System overview</h2><span class="spacer"></span>
          <button class="btn sm ghost" data-act="go" data-section="system">Details</button></div>
        <div class="ring-grid">
          ${ringTile({ label: 'CPU', percent: cpu, hint: `${system.cpu?.cores_logical ?? '—'} cores`, color: '#3ddcf5' })}
          ${ringTile({ label: 'RAM', percent: memory, hint: `${bytes(system.memory?.used_bytes)} / ${bytes(system.memory?.total_bytes)}`, color: '#9b8cff' })}
          ${ringTile({ label: 'Disk', percent: diskPercent, hint: `${bytes(system.disks?.[0]?.free_bytes)} free`, color: '#fbbf24' })}
          ${ringTile({
            label: 'Temp',
            percent: tempPercent,
            center: temp.known ? `${Math.round(temp.celsius)}°` : '—',
            hint: temp.known ? (temp.label || 'sensor') : 'no sensor exposed',
            color: '#f472b6',
            unknown: !temp.known,
          })}
        </div>
        <div class="stat-row" style="margin-top:12px;justify-content:space-between">
          <div class="stat"><div class="n">${rate(system.network?.download_bps)}</div><div class="l">download</div></div>
          <div class="stat"><div class="n">${rate(system.network?.upload_bps)}</div><div class="l">upload</div></div>
          <div class="stat"><div class="n">${esc(system.uptime?.human || '—')}</div><div class="l">uptime</div></div>
        </div>
        <div class="faint tiny" style="margin-top:8px">${esc(system.identity?.hostname || 'this PC')} · ${esc(system.identity?.platform || '')} ${esc(system.identity?.release || '')}</div>
      </div>
    </section>

    <section class="grid cols-3">
      <div class="card chat-card">
        <div class="card-head"><h2>AI Assistant</h2><span class="spacer"></span>
          <div class="chat-tabs" id="home-chat-tabs">
            ${[['assistant', 'Chat'], ['research', 'Research'], ['system', 'Analyze']].map(([id, label]) => `
              <button class="tab ${(state.homeChat?.agent || 'assistant') === id ? 'active' : ''}"
                data-act="home-chat-tab" data-agent="${id}">${label}</button>`).join('')}
          </div>
        </div>
        <div class="chat-log" id="home-chat-log"></div>
        <form class="chat-form" id="home-chat-form">
          <label class="field" style="flex:1;margin:0">
            <input id="home-chat-input" type="text" autocomplete="off" spellcheck="false"
              placeholder="${(state.homeChat?.agent === 'research') ? 'Research a topic with sources…' : (state.homeChat?.agent === 'system') ? 'Ask about this PC…' : 'Ask anything…'}" />
          </label>
          <button class="btn primary" type="submit" id="home-chat-send">Send</button>
        </form>
        <div class="faint tiny" style="margin-top:6px">${online ? 'Answers come from your configured provider.' : 'No provider configured yet — chat will say so instead of guessing.'}</div>
      </div>

      <div class="card">
        <div class="card-head"><h2>Today's schedule</h2><span class="spacer"></span>
          <button class="btn sm ghost" data-act="go" data-section="calendar">View calendar</button></div>
        ${scheduleList(data.schedule)}
        <div class="section-title" style="margin-top:14px">Reminders</div>
        ${data.reminders.length ? data.reminders.slice(0, 4).map((item) => `
          <div class="list-item"><span class="pill info">${esc(timeLabel(item.due_at))}</span>
            <span class="grow"><span class="title">${esc(item.title)}</span></span>
            <button class="btn sm ghost" data-act="snooze" data-id="${item.id}">Snooze 10m</button></div>`).join('')
          : '<div class="empty">No reminders in the next 24 hours.</div>'}
      </div>

      <div class="card">
        <div class="card-head"><h2>Quick tools</h2><span class="spacer"></span>
          <span class="faint tiny">real actions</span></div>
        <div class="quick-grid">
          ${QUICK_TOOLS.map((tool) => `
            <button class="quick ${tool.tone || ''}" data-act="${tool.act}"
              ${tool.section ? `data-section="${tool.section}"` : ''}
              ${tool.target ? `data-target="${tool.target}"` : ''}
              ${tool.cmd ? `data-cmd="${esc(tool.cmd)}"` : ''}>
              ${icon(tool.icon, 19)}<span>${esc(tool.label)}</span></button>`).join('')}
        </div>
        <div class="mini-note" style="margin-top:12px">Screenshots, folder opens and power actions can ask for approval — that is the safety policy, not a failure.</div>
        <div class="section-title" style="margin-top:14px">Recent apps</div>
        ${(data.recent_apps || []).length ? data.recent_apps.slice(0, 3).map((app) => `
          <div class="list-item"><span class="grow"><span class="title">${esc(app.name)}</span>
            <div class="meta">${esc(relative(app.last_launched))}</div></span>
            <button class="btn sm ghost" data-act="launch-app" data-target="${esc(app.name)}">Open</button></div>`).join('')
          : '<div class="empty">Apps you launch appear here.</div>'}
      </div>
    </section>

    <section class="grid cols-3">
      <div class="card span-2">
        <div class="card-head"><h2>All features at your command</h2><span class="spacer"></span>
          <span class="pill">${NAV.filter((item) => item.id && item.id !== 'search').length} sections</span>
          <span class="faint tiny">every tile is live</span></div>
        <div class="feature-grid">${featureGrid()}</div>
      </div>

      <div class="card">
        <div class="card-head"><h2>Security &amp; privacy</h2><span class="spacer"></span>
          <span class="pill ${policy.emergency_stop ? 'bad' : 'ok'}" id="scan-indicator">${policy.emergency_stop ? 'halted' : 'active'}</span></div>
        <div class="row" style="gap:12px;align-items:center">
          <span class="kpi-icon" style="width:46px;height:46px;border-radius:14px">${icon('security', 22)}</span>
          <div class="grow">
            <div class="strong">${esc(policy.mode || 'standard')} mode</div>
            <div class="faint tiny">${pending} approval(s) waiting · ${num(auditTotal)} audited actions</div>
          </div>
        </div>
        <div class="row wrap" style="margin-top:12px">
          <button class="btn sm primary" data-act="security-scan">Run full check</button>
          <button class="btn sm ghost" data-act="go" data-section="security">Open center</button>
        </div>
        <div class="scan-results" id="scan-results"></div>
      </div>
    </section>

    <section class="grid cols-3">
      <div class="card">
        <div class="card-head"><h2>Automation &amp; agents</h2><span class="spacer"></span>
          <button class="btn sm ghost" data-act="go" data-section="automation">Manage</button></div>
        ${workflows.length ? workflows.slice(0, 4).map((workflow) => `
          <div class="list-item">
            <span class="grow"><span class="title">${esc(workflow.name)}</span>
              <div class="meta">${esc(workflow.trigger?.type || 'manual')}${workflow.last_run ? ` · last run ${esc(relative(workflow.last_run))}` : ' · never run'}${
                workflow.run_count ? ` · ${workflow.run_count} run(s)` : ''}</div></span>
            <span class="pill ${workflow.enabled ? 'ok' : ''}">${workflow.enabled ? 'on' : 'off'}</span>
          </div>`).join('') : '<div class="empty">No workflows yet.</div>'}
        <div class="section-title" style="margin-top:14px">Agents</div>
        <div class="row wrap">
          ${agentList.slice(0, 5).map((agent) => `<span class="pill" title="${esc(agent.description || '')}">${esc(agent.title || agent.name)}</span>`).join('')}
          <span class="pill info">${agentList.length} total</span>
        </div>
      </div>

      <div class="card">
        <div class="card-head"><h2>Performance today</h2><span class="spacer"></span>
          <button class="btn sm ghost" data-act="go" data-section="analytics">Analytics</button></div>
        ${usageCard(analytics)}
      </div>

      <div class="card">
        <div class="card-head"><h2>Recent files</h2><span class="spacer"></span>
          <button class="btn sm ghost" data-act="go" data-section="files">All files</button></div>
        ${data.recent_files.length ? data.recent_files.slice(0, 5).map((file) => `
          <div class="list-item"><span class="grow"><span class="title">${esc(file.name)}</span>
            <div class="meta">${esc(file.size_human)} · ${esc(relative(file.modified))}</div></span>
            <button class="btn sm ghost" data-act="open-file" data-path="${esc(file.path)}">Open</button></div>`).join('')
          : '<div class="empty">Nothing recent in Downloads, Documents or Desktop.</div>'}
      </div>
    </section>

    <section class="grid cols-4">
      <div class="card">
        <div class="card-head"><h2>System actions</h2></div>
        <div class="sys-actions">
          <button class="sys-action" data-act="power" data-action="lock">${icon('lock', 20)}Lock</button>
          <button class="sys-action" data-act="power" data-action="sleep">${icon('moon', 20)}Sleep</button>
          <button class="sys-action danger" data-act="power" data-action="restart">${icon('restart', 20)}Restart</button>
          <button class="sys-action danger" data-act="power" data-action="shutdown">${icon('power', 20)}Shut down</button>
        </div>
        <div class="mini-note" style="margin-top:10px">Restart and shutdown require explicit approval. Log out is not exposed by RAY yet.</div>
      </div>

      <div class="card">
        <div class="card-head"><h2>Voice &amp; control</h2></div>
        <div class="wave" aria-hidden="true">${'<i></i>'.repeat(16)}</div>
        <div class="center faint tiny" style="margin-bottom:8px">${data.voice_enabled ? 'Voice replies enabled' : 'Voice output is off in Settings'}</div>
        <button class="btn sm primary" data-act="go" data-section="voice" style="width:100%">${icon('mic', 16)} Talk to RAY</button>
        <div class="mini-note center" style="margin-top:8px">Speech recognition runs in your browser; audio never leaves this PC.</div>
      </div>

      <div class="card">
        <div class="card-head"><h2>Media</h2><span class="spacer"></span>
          <span class="pill">${library.length} file(s)</span></div>
        <div class="media-stats">
          <div class="stat"><div class="n">${audioCount}</div><div class="l">audio</div></div>
          <div class="stat"><div class="n">${videoCount}</div><div class="l">video</div></div>
          <div class="stat"><div class="n">${(media.playlists || []).length}</div><div class="l">playlists</div></div>
        </div>
        ${library.length ? `<div class="list-item"><span class="grow"><span class="title">${esc(library[0].title || library[0].name)}</span>
            <div class="meta">${esc(library[0].kind)} · ${esc(library[0].size_human)}</div></span>
            <button class="btn sm ghost" data-act="play-media" data-query="${esc(library[0].title || library[0].name)}">Play</button></div>`
          : '<div class="empty">No music or video folders found yet.</div>'}
        <button class="btn sm ghost" data-act="go" data-section="media" style="width:100%;margin-top:8px">Open media center</button>
        <div class="mini-note" style="margin-top:8px">Playback uses your default Windows player.</div>
      </div>

      <div class="card">
        <div class="card-head"><h2>Browser</h2><span class="spacer"></span>
          <button class="btn sm ghost" data-act="go" data-section="browser">Research</button></div>
        <div class="browser-links">
          ${BROWSER_LINKS.map((link) => `
            <button class="browser-link" data-act="browser-open" data-url="${esc(link.url)}" title="Open ${esc(link.url)}">
              <span class="dotletter">${link.letter}</span>${esc(link.label)}</button>`).join('')}
        </div>
        <form class="browser-form" id="browser-url-form">
          <input type="text" id="browser-url" placeholder="https://…" autocomplete="off" spellcheck="false" />
          <button class="btn sm" type="submit">Go</button>
        </form>
        <div class="mini-note" style="margin-top:8px">Links open in your default browser; RAY does not embed pages.</div>
      </div>
    </section>

    <section class="grid cols-4">
      <div class="card">
        <div class="card-head"><h2>Tasks</h2><span class="spacer"></span>
          <button class="btn sm ghost" data-act="go" data-section="tasks">All</button></div>
        ${data.tasks.length ? data.tasks.slice(0, 4).map((task) => `
          <div class="list-item">
            <button class="btn sm ghost" data-act="complete-task" data-id="${task.id}" title="Mark done">✓</button>
            <span class="grow"><span class="title">${esc(task.title)}</span>
              <div class="meta">${esc(task.priority)}${task.due_at ? ` · ${esc(timeLabel(task.due_at))}` : ''}</div></span>
            ${task.overdue ? '<span class="pill bad">overdue</span>' : ''}
          </div>`).join('') : '<div class="empty">Nothing open. Add a task when you are ready.</div>'}
      </div>

      <div class="card">
        <div class="card-head"><h2>Notes</h2><span class="spacer"></span>
          <button class="btn sm ghost" data-act="go" data-section="notes">Open</button></div>
        ${notes.notes.length ? notes.notes.slice(0, 4).map((note) => `
          <button class="list-item" data-act="go" data-section="notes">
            <span class="grow"><span class="title">${note.pinned ? '📌 ' : ''}${esc(note.title)}</span>
              <div class="meta">${esc(note.category)} · ${esc(relative(note.updated_at))}</div></span>
          </button>`).join('') : '<div class="empty">No notes yet.</div>'}
      </div>

      ${miniCalendar(month)}

      <div class="card">
        <div class="card-head"><h2>Shortcuts</h2></div>
        <div class="shortcut-list">
          <div class="shortcut"><span>Command center</span><span class="kbd">Ctrl Space</span></div>
          <div class="shortcut"><span>Command center (alt)</span><span class="kbd">Ctrl K</span></div>
          <div class="shortcut"><span>Close overlays</span><span class="kbd">Esc</span></div>
          <div class="shortcut"><span>Send chat</span><span class="kbd">Enter</span></div>
        </div>
        <div class="mini-note" style="margin-top:10px">Windows shortcuts (Win+E, Alt+Tab) stay with the OS.</div>
      </div>
    </section>

    <section class="grid cols-3">
      <div class="card span-2">
        <div class="card-head"><h2>Notifications</h2><span class="spacer"></span>
          <span class="pill">${data.unread_notifications} unread</span></div>
        ${data.notifications.length ? data.notifications.slice(0, 4).map((item) => `
          <div class="list-item"><span class="grow"><span class="title">${esc(item.title)}</span>
            <div class="meta">${esc(item.body || '')}</div></span>
            <span class="faint tiny">${esc(relative(item.ts))}</span></div>`).join('')
          : '<div class="empty">Reminders, automations and scans report here.</div>'}
      </div>
      <div class="card quote">
        <p>"Discipline is choosing between what you want now and what you want most."</p>
        <div class="by">RAY · today's reminder</div>
      </div>
    </section>`;

  paintHomeChat();
  drawSparks();
  updateStatusChips(system);

  const form = $('#home-chat-form');
  if (form) {
    const input = $('#home-chat-input');
    form.addEventListener('submit', (event) => {
      event.preventDefault();
      const text = input.value.trim();
      if (!text) return;
      input.value = '';
      sendHomeChat(text);
    });
    input.focus();
  }

  const urlForm = $('#browser-url-form');
  if (urlForm) {
    const urlInput = $('#browser-url');
    urlForm.addEventListener('submit', async (event) => {
      event.preventDefault();
      const url = urlInput.value.trim();
      if (!url) return;
      try {
        await api('/api/browser/open', { method: 'POST', body: { url } });
        toast('Opened in your browser', url, 'ok');
      } catch (error) { toast('Could not open', error.message, 'error'); }
    });
  }

  const tick = () => {
    const node = $('#hero-time');
    if (node) node.textContent = new Date().toLocaleTimeString(undefined, { hour: '2-digit', minute: '2-digit' });
  };
  state.timers.clock = setInterval(tick, 15000);
  state.timers.sys = setInterval(async () => {
    if (state.section !== 'home') return;
    try {
      const next = await api('/api/system?history=1');
      const snapshot = next.snapshot;
      state.lastSystem = next;
      updateStatusChips(snapshot);
    } catch (error) { /* transient */ }
  }, 8000);
};

function rate(bps) {
  if (!bps) return '0 B/s';
  if (bps < 1024) return `${Math.round(bps)} B/s`;
  if (bps < 1024 * 1024) return `${(bps / 1024).toFixed(1)} KB/s`;
  return `${(bps / 1048576).toFixed(1)} MB/s`;
}

function scheduleList(events) {
  if (!events || !events.length) return '<div class="empty">Nothing scheduled today. Add an event from the Calendar.</div>';
  return events.map((event) => `
    <div class="list-item">
      <span class="pill info">${esc(shortTime(event.start_at))}</span>
      <span class="grow"><span class="title">${esc(event.title)}</span>
        ${event.location ? `<div class="meta">${esc(event.location)}</div>` : ''}</span>
      <span class="faint tiny">${esc(event.end_at ? `to ${shortTime(event.end_at)}` : '')}</span>
    </div>`).join('');
}

/* -------------------------------------------------------------- assistant */
VIEWS.assistant = async function () {
  view().innerHTML = `
    <div class="grid cols-3" style="align-items:start">
      <div class="card span-2">
        <div class="card-head"><h2>Conversation</h2><span class="spacer"></span>
          <span class="pill" id="assistant-provider">—</span>
          <button class="btn sm ghost" id="clear-chat">Clear</button></div>
        <div class="chat" id="chat-log"></div>
        <div class="composer">
          <textarea id="chat-input" placeholder="Ask RAY, or tell it what to do… (Enter to send, Shift+Enter for a new line)"></textarea>
          <button class="btn primary" id="chat-send">Send</button>
        </div>
        <div class="row small faint" style="margin-top:8px">
          <label class="switch"><input type="checkbox" id="chat-speak" ${state.settings?.config?.voice?.auto_speak ? 'checked' : ''}><span class="track"></span>Speak replies</label>
          <span class="push"></span>
          <span>Tools: ${state.status?.tools?.count || 0}</span>
        </div>
      </div>
      <div class="card">
        <div class="card-head"><h2>Capabilities</h2></div>
        <p class="faint small">RAY picks a specialist agent automatically. Everything it does is validated, permission-gated and logged.</p>
        <div class="list" style="margin-top:10px">
          ${(state.status?.agents || []).slice(0, 12).map((agent) => `
            <div class="list-item"><span class="grow"><span class="title">${esc(agent.title)}</span>
              <div class="meta">${esc(agent.description)}</div></span></div>`).join('')}
        </div>
      </div>
    </div>`;

  renderChat();
  const input = $('#chat-input');
  const send = async () => {
    const text = input.value.trim();
    if (!text) return;
    input.value = '';
    await sendChat(text);
  };
  $('#chat-send').addEventListener('click', send);
  input.addEventListener('keydown', (event) => {
    if (event.key === 'Enter' && !event.shiftKey) { event.preventDefault(); send(); }
  });
  $('#clear-chat').addEventListener('click', () => { state.chat = []; renderChat(); });
  const speakToggle = $('#chat-speak');
  if (speakToggle) speakToggle.addEventListener('change', () => {
    state.chatSpeak = speakToggle.checked;
  });
  state.chatSpeak = speakToggle ? speakToggle.checked : false;
  const provider = $('#assistant-provider');
  const active = Object.entries(state.status?.providers || {}).filter(([, value]) => value.available).map(([key]) => key);
  provider.textContent = active.length ? active.join(' + ') : 'offline';
  provider.className = `pill ${active.length ? 'ok' : 'warn'}`;
};

function renderChat() {
  const log = $('#chat-log');
  if (!log) return;
  if (!state.chat.length) {
    log.innerHTML = `<div class="msg"><div class="avatar">R</div><div class="bubble">Ready.
Try: "summarise the PDF in my Downloads", "what is on my schedule tomorrow", or "open Chrome and search for the Companies Act".
Without API keys I can still run every local command.</div></div>`;
    return;
  }
  log.innerHTML = state.chat.map((message) => {
    if (message.role === 'task') {
      const steps = (message.outcomes || []).map((outcome) => `
        <div class="step ${outcome.ok ? 'ok' : outcome.requires_approval ? 'wait' : 'fail'}">
          <span class="idx">${outcome.ok ? '✓' : outcome.requires_approval ? '!' : '×'}</span>
          <span><span class="tool">${esc(outcome.tool)}</span> ${esc(outcome.error || outcome.why || '')}</span>
          <span class="faint small">${outcome.duration_ms} ms</span></div>`).join('');
      return `<div class="msg"><div class="avatar">R</div><div class="bubble">${esc(message.answer || '')}
        ${steps ? `<div class="steps">${steps}</div>` : ''}
        <div class="faint tiny" style="margin-top:8px">${esc(message.agent)} · ${message.duration_ms} ms · ${esc(message.status)}</div></div></div>`;
    }
    return `<div class="msg ${message.role}"><div class="avatar">${message.role === 'user' ? 'You' : 'R'}</div>
      <div class="bubble">${esc(message.content)}</div></div>`;
  }).join('');
  log.scrollTop = log.scrollHeight;
}

async function sendChat(text) {
  const input = $('#chat-input');
  const value = (text || input?.value || '').trim();
  if (!value) return;
  if (input) input.value = '';
  state.chat.push({ role: 'user', content: value });
  state.chat.push({ role: 'assistant', content: '…' });
  renderChat();
  try {
    const history = state.chat.filter((message) => message.role === 'user' || message.role === 'assistant')
      .slice(-12).map((message) => ({ role: message.role, content: message.content }));
    const result = await api('/api/command', { method: 'POST', body: { text: value, history } });
    state.chat.pop();
    state.chat.push({
      role: 'task', agent: result.agent, status: result.status, outcomes: result.outcomes,
      answer: result.answer || result.error || '', duration_ms: result.duration_ms,
    });
    state.taskId = result.task_id;
    if (state.chatSpeak && result.speak) speak(result.speak);
    if (result.status === 'awaiting_approval') { loadApprovals(); toast('Approval needed', 'Check the bell icon.', 'warn'); }
  } catch (error) {
    state.chat.pop();
    state.chat.push({ role: 'system', content: error.message });
  }
  renderChat();
}

/* ------------------------------------------------------------------ voice */
VIEWS.voice = async function () {
  const status = await api('/api/voice/status');
  const config = state.settings?.config?.voice || {};
  view().innerHTML = `
    <div class="grid cols-2" style="align-items:start">
      <div class="card">
        <div class="card-head"><h2>Speak</h2><span class="spacer"></span>
          <span class="pill ${status.fish_configured ? 'ok' : 'warn'}">${status.fish_configured ? 'Fish Audio ready' : 'Fish Audio not configured'}</span></div>
        <button class="mic ${state.listening ? 'listening' : ''}" id="mic" title="Push to talk">${icon('voice', 30)}</button>
        <p class="center small faint" id="mic-status" style="margin-top:10px">
          ${state.listening ? 'Listening… click to stop' : 'Click to speak. The microphone is only active while you hold this open.'}
        </p>
        <div class="col" style="margin-top:16px">
          <label class="field"><span>Say something</span>
            <textarea id="speak-text" placeholder="Type a sentence and RAY will speak it with Fish Audio">Good evening. Your system is healthy and you have two tasks left for today.</textarea>
          </label>
          <div class="row">
            <button class="btn primary" id="speak-btn">Speak</button>
            <button class="btn ghost" id="save-voice">Save voice id</button>
            <span class="faint small" id="speak-status"></span>
          </div>
        </div>
      </div>
      <div class="card">
        <div class="card-head"><h2>Voice settings</h2></div>
        <div class="col">
          <label class="switch"><input type="checkbox" id="voice-enabled" ${status.enabled ? 'checked' : ''}><span class="track"></span>Enable voice output</label>
          <label class="field"><span>Fish Audio voice id (reference_id)</span>
            <input type="text" id="voice-id" value="${esc(status.voice_id || '')}" placeholder="paste a voice model id from fish.audio" />
          </label>
          <label class="field"><span>Speed</span>
            <input type="range" id="voice-speed" min="0.6" max="1.6" step="0.05" value="${esc(config.speed ?? 1)}" />
          </label>
          <label class="switch"><input type="checkbox" id="voice-wake" ${status.wake_word.enabled ? 'checked' : ''}><span class="track"></span>Wake word “${esc(status.wake_word.word)}” while this page is open</label>
          <div class="banner info small">
            Speech recognition uses your browser's engine (Chrome or Edge). Audio is captured only when you
            click the microphone, and RAY shows a red pulse while it listens. Nothing is recorded to disk.
          </div>
          <div class="banner small">
            Voices are generated by Fish Audio using your own API key. RAY does not ship a voice model and never
            claims a free tier exists - check your account limits in the Fish Audio dashboard.
          </div>
          <button class="btn" id="load-voices">Load my Fish Audio voices</button>
          <div id="voices"></div>
        </div>
      </div>
    </div>`;

  let recognition = initRecognition();
  const mic = $('#mic');
  const micStatus = $('#mic-status');
  mic.addEventListener('click', () => {
    if (!recognition) {
      toast('No speech recognition', 'This browser does not expose the Web Speech API. Use Edge or Chrome, or install faster-whisper for local transcription.', 'warn', 7000);
      return;
    }
    if (state.listening) {
      recognition.stop();
      return;
    }
    recognition.onresult = async (event) => {
      let transcript = '';
      for (let i = event.resultIndex; i < event.results.length; i += 1) {
        transcript += event.results[i][0].transcript;
      }
      if (event.results[event.results.length - 1].isFinal) {
        micStatus.textContent = `Heard: “${transcript}”`;
        const result = await runCommand(transcript);
        if (result?.speak) speak(result.speak);
      } else {
        micStatus.textContent = `“${transcript}”`;
      }
    };
    recognition.onerror = (event) => {
      state.listening = false;
      mic.classList.remove('listening');
      micStatus.textContent = `Microphone error: ${event.error}`;
    };
    recognition.onend = () => {
      state.listening = false;
      mic.classList.remove('listening');
      micStatus.textContent = 'Click to speak.';
      if (status.wake_word.enabled && recognition) {
        setTimeout(() => { try { recognition.start(); } catch (e) {} }, 700);
      }
    };
    try {
      recognition.start();
      state.listening = true;
      mic.classList.add('listening');
      micStatus.textContent = 'Listening…';
    } catch (error) {
      toast('Could not start', error.message, 'error');
    }
  });

  $('#speak-btn').addEventListener('click', () => {
    const text = $('#speak-text').value.trim();
    if (!text) return;
    $('#speak-status').textContent = 'Generating with Fish Audio…';
    speak(text, { force: true });
  });

  $('#save-voice').addEventListener('click', async () => {
    await api('/api/settings', { method: 'POST', body: { values: { 'voice.voice_id': $('#voice-id').value.trim() } } });
    toast('Saved', 'Voice id updated.', 'ok');
  });
  $('#voice-enabled').addEventListener('change', async (event) => {
    await api('/api/settings', { method: 'POST', body: { values: { 'voice.enabled': event.target.checked } } });
    state.status.voice_enabled = event.target.checked;
    toast('Voice', event.target.checked ? 'Voice output enabled' : 'Voice output disabled', 'ok');
  });
  $('#voice-speed').addEventListener('change', async (event) => {
    await api('/api/settings', { method: 'POST', body: { values: { 'voice.speed': Number(event.target.value) } } });
  });
  $('#voice-wake').addEventListener('change', async (event) => {
    await api('/api/settings', { method: 'POST', body: { values: { 'voice.wake_word_enabled': event.target.checked } } });
    if (event.target.checked) toast('Wake word', 'Listening while this page stays open.', 'info');
    else if (recognition) { try { recognition.stop(); } catch (e) {} }
  });
  $('#load-voices').addEventListener('click', async () => {
    const box = $('#voices');
    box.innerHTML = '<div class="faint small">Loading…</div>';
    try {
      const data = await api('/api/providers/voices');
      box.innerHTML = (data.voices || []).length
        ? data.voices.map((voice) => `<div class="list-item"><span class="grow"><span class="title">${esc(voice.title)}</span>
            <div class="meta mono">${esc(voice.id)}</div></span>
            <button class="btn sm" data-act="use-voice" data-id="${esc(voice.id)}">Use</button></div>`).join('')
        : `<div class="empty">${esc(data.error || 'No voice models on this account.')}</div>`;
    } catch (error) {
      box.innerHTML = `<div class="empty">${esc(error.message)}</div>`;
    }
  });
};

/* --------------------------------------------------------------- computer */
VIEWS.computer = async function () {
  const [installed, running, system] = await Promise.all([
    api('/api/apps'), api('/api/apps?running=1'), api('/api/system?history=1'),
  ]);
  const controls = state.settings?.capabilities || {};
  view().innerHTML = `
    <div class="grid cols-3" style="align-items:start">
      <div class="card">
        <div class="card-head"><h2>Controls</h2></div>
        <div class="col">
          <button class="btn" data-act="screenshot">Take a screenshot</button>
          <div class="row"><span class="faint small">Volume</span><span class="push"></span>
            <button class="btn sm" data-act="volume" data-direction="down">−</button>
            <button class="btn sm" data-act="volume" data-direction="mute">Mute</button>
            <button class="btn sm" data-act="volume" data-direction="up">+</button></div>
          <label class="field"><span>Exact volume</span>
            <input type="range" id="volume-range" min="0" max="100" value="50" /></label>
          <label class="field"><span>Brightness</span>
            <input type="range" id="brightness-range" min="0" max="100" value="60" /></label>
          <div class="row wrap">
            <button class="btn" data-act="panel" data-panel="settings">Windows settings</button>
            <button class="btn" data-act="panel" data-panel="task_manager">Task Manager</button>
            <button class="btn" data-act="panel" data-panel="terminal">Terminal</button>
          </div>
          <div class="section-title" style="margin-top:14px">Power</div>
          <div class="row wrap">
            <button class="btn" data-act="power" data-action="lock">Lock</button>
            <button class="btn" data-act="power" data-action="sleep">Sleep</button>
            <button class="btn danger" data-act="power" data-action="restart">Restart</button>
            <button class="btn danger" data-act="power" data-action="shutdown">Shut down</button>
          </div>
          <div class="faint tiny">Lock and sleep run immediately. Restart and shut down always ask for explicit approval.</div>
          ${controls.input_automation ? `<div class="faint tiny">Keyboard/mouse automation: ${esc(controls.input_automation[1])}</div>` : ''}
        </div>
      </div>

      <div class="card span-2">
        <div class="card-head"><h2>Applications</h2><span class="spacer"></span>
          <input type="search" id="app-filter" placeholder="Filter installed apps" style="max-width:240px" />
          <button class="btn sm ghost" id="tab-installed">Installed</button>
          <button class="btn sm ghost" id="tab-running">Running</button></div>
        <div id="apps-box" style="max-height:420px;overflow:auto"></div>
      </div>
    </div>

    <div class="grid cols-2">
      <div class="card">
        <div class="card-head"><h2>Launch by name</h2></div>
        <div class="row"><input type="text" id="launch-target" placeholder="e.g. chrome, notepad, code" />
          <button class="btn primary" id="launch-btn">Launch</button></div>
        <div class="faint small" style="margin-top:8px">RAY resolves applications from the Start Menu, App Paths registry and your PATH. It refuses anything it cannot actually find.</div>
      </div>
      <div class="card">
        <div class="card-head"><h2>Machine</h2></div>
        <table class="table">
          <tr><td>Host</td><td class="strong">${esc(system.snapshot.identity.hostname)}</td></tr>
          <tr><td>OS</td><td class="strong">${esc(system.snapshot.identity.platform)} ${esc(system.snapshot.identity.release)}</td></tr>
          <tr><td>Processor</td><td class="strong">${esc(system.snapshot.identity.processor || '—')}</td></tr>
          <tr><td>Cores</td><td class="strong">${system.snapshot.cpu.cores_physical || '—'} physical / ${system.snapshot.cpu.cores_logical} logical</td></tr>
          <tr><td>Memory</td><td class="strong">${bytes(system.snapshot.memory.total_bytes)}</td></tr>
        </table>
      </div>
    </div>`;

  const renderApps = (mode) => {
    const query = ($('#app-filter')?.value || '').toLowerCase();
    const apps = mode === 'running' ? running.apps : installed.apps;
    const filtered = apps.filter((app) => !query || app.name.toLowerCase().includes(query));
    $('#apps-box').innerHTML = filtered.length
      ? filtered.slice(0, 200).map((app) => `
          <div class="list-item">
            <span class="grow"><span class="title">${esc(app.name)}</span>
              <div class="meta mono">${esc(app.path ? app.path.slice(0, 90) : '')}</div></span>
            ${mode === 'running'
              ? `<span class="meta">${num(app.memory_mb, 1)} MB</span>
                 <button class="btn sm" data-act="focus-app" data-name="${esc(app.name)}">Switch</button>
                 <button class="btn sm danger" data-act="close-app" data-name="${esc(app.name)}">Close</button>`
              : `<button class="btn sm" data-act="launch-app" data-target="${esc(app.name)}">Launch</button>`}
          </div>`).join('')
      : '<div class="empty">Nothing found.</div>';
  };
  let mode = 'installed';
  renderApps(mode);
  $('#tab-installed').addEventListener('click', () => { mode = 'installed'; renderApps(mode); });
  $('#tab-running').addEventListener('click', () => { mode = 'running'; renderApps(mode); });
  $('#app-filter').addEventListener('input', () => renderApps(mode));

  $('#launch-btn').addEventListener('click', async () => {
    const target = $('#launch-target').value.trim();
    if (!target) return;
    try {
      const result = await api('/api/apps/launch', { method: 'POST', body: { target } });
      toast('Launched', result.launched, 'ok');
    } catch (error) { toast('Could not launch', error.message, 'error'); }
  });
  $('#volume-range').addEventListener('change', async (event) => {
    try { await api('/api/system/volume', { method: 'POST', body: { percent: Number(event.target.value) } }); }
    catch (error) { toast('Volume', error.message, 'warn'); }
  });
  $('#brightness-range').addEventListener('change', async (event) => {
    try { await api('/api/system/brightness', { method: 'POST', body: { percent: Number(event.target.value) } }); }
    catch (error) { toast('Brightness', error.message, 'warn'); }
  });
};

/* ------------------------------------------------------------------ files */
VIEWS.files = async function () {
  const listing = await api('/api/files');
  view().innerHTML = `
    <div class="grid cols-3" style="align-items:start">
      <div class="card span-2">
        <div class="card-head"><h2>Files</h2><span class="spacer"></span>
          <button class="btn sm ghost" data-act="open-file" data-path="${esc(listing.parent || '')}">Up</button>
          <button class="btn sm ghost" id="refresh-files">Refresh</button></div>
        <div class="row" style="margin-bottom:10px">
          <input type="text" id="path-input" value="${esc(listing.path)}" />
          <button class="btn" id="go-path">Go</button>
        </div>
        <div class="row" style="margin-bottom:10px">
          <input type="search" id="file-search" placeholder="Search this machine for a file…" />
          <button class="btn primary" id="search-btn">Search</button>
        </div>
        <div id="file-list">${fileList(listing)}</div>
      </div>
      <div class="card">
        <div class="card-head"><h2>Insights</h2></div>
        <div id="insights"><div class="faint small">Loading insights…</div></div>
      </div>
    </div>`;

  $('#refresh-files').addEventListener('click', () => VIEWS.files());
  $('#go-path').addEventListener('click', async () => {
    const path = $('#path-input').value.trim();
    try {
      const next = await api(`/api/files?path=${encodeURIComponent(path)}`);
      $('#path-input').value = next.path;
      $('#file-list').innerHTML = fileList(next);
    } catch (error) { toast('Cannot open', error.message, 'error'); }
  });
  $('#search-btn').addEventListener('click', async () => {
    const query = $('#file-search').value.trim();
    if (!query) return;
    try {
      const result = await api(`/api/files/search?q=${encodeURIComponent(query)}&limit=40`);
      $('#file-list').innerHTML = result.results.length
        ? result.results.map((file) => `
            <div class="list-item"><span class="grow"><span class="title">${esc(file.name)}</span>
              <div class="meta mono">${esc(file.path)}</div>
              <div class="meta">${esc(file.size_human)} · ${esc(relative(file.modified))}</div></span>
              <button class="btn sm ghost" data-act="open-file" data-path="${esc(file.path)}">Open</button>
              <button class="btn sm ghost" data-act="reveal-file" data-path="${esc(file.path)}">Folder</button>
            </div>`).join('')
        : `<div class="empty">No files matched “${esc(query)}”. Searched ${num(result.scanned)} entries.</div>`;
    } catch (error) { toast('Search failed', error.message, 'error'); }
  });

  try {
    const insights = await api('/api/files/insights');
    const plan = insights.organisation || { plan: [] };
    $('#insights').innerHTML = `
      <div class="section-title">Downloads tidy-up</div>
      <p class="faint small">${plan.plan.length} file(s) could be sorted, ${plan.unmatched.length} left alone.</p>
      ${plan.plan.length ? `<div class="col" style="margin-top:8px">
        <button class="btn sm" id="apply-organise">Apply plan</button>
        <div class="list" style="max-height:160px;overflow:auto">
          ${plan.plan.slice(0, 12).map((item) => `<div class="list-item"><span class="grow">
            <span class="title">${esc(item.file)}</span><div class="meta">→ ${esc(item.category)}</div></span></div>`).join('')}
        </div></div>` : ''}
      <div class="section-title" style="margin-top:14px">Largest files</div>
      <div class="list">${(insights.large || []).slice(0, 6).map((file) => `
        <div class="list-item"><span class="grow"><span class="title">${esc(file.name)}</span>
          <div class="meta">${esc(file.size_human)}</div></span>
          <button class="btn sm ghost" data-act="reveal-file" data-path="${esc(file.path)}">Show</button></div>`).join('')
        || '<div class="empty">Nothing over 50 MB.</div>'}</div>
      <div class="section-title" style="margin-top:14px">Duplicates</div>
      <div class="list">${(insights.duplicates || []).slice(0, 4).map((group) => `
        <div class="list-item"><span class="grow"><span class="title">${esc(group.count)} copies · ${esc(group.size_human)} each</span>
          <div class="meta mono">${esc(group.paths[0] || '')}</div></span></div>`).join('')
        || '<div class="empty">No duplicates found.</div>'}</div>`;

    const applyBtn = $('#apply-organise');
    if (applyBtn) {
      applyBtn.addEventListener('click', async () => {
        const ok = await confirmAction('Move files?', `RAY will move ${plan.plan.length} file(s) into folders inside Downloads.`, 'Move them');
        if (!ok) return;
        try {
          const result = await api('/api/files/organise', { method: 'POST', body: { plan: plan.plan } });
          toast('Organised', `${result.count} file(s) moved, ${result.failed.length} failed.`, 'ok');
          VIEWS.files();
        } catch (error) { toast('Failed', error.message, 'error'); }
      });
    }
  } catch (error) {
    $('#insights').innerHTML = `<div class="empty">${esc(error.message)}</div>`;
  }
};

function fileList(listing) {
  if (!listing?.entries?.length) return '<div class="empty">This folder is empty.</div>';
  return listing.entries.map((entry) => `
    <div class="list-item">
      <span style="width:18px;color:${entry.is_dir ? '#9d8cff' : '#6b7a96'}">${entry.is_dir ? '▣' : '▤'}</span>
      <span class="grow"><span class="title">${esc(entry.name)}</span>
        <div class="meta">${entry.is_dir ? 'folder' : esc(entry.size ? bytes(entry.size) : '')} · ${esc(relative(entry.modified))}</div></span>
      ${entry.is_dir
        ? `<button class="btn sm ghost" data-act="browse-files" data-path="${esc(entry.path)}">Open</button>`
        : `<button class="btn sm ghost" data-act="open-file" data-path="${esc(entry.path)}">Open</button>`}
      <button class="btn sm ghost" data-act="reveal-file" data-path="${esc(entry.path)}">Reveal</button>
    </div>`).join('');
}

/* ---------------------------------------------------------------- browser */
VIEWS.browser = async function () {
  const data = await api('/api/browser/history');
  view().innerHTML = `
    <div class="grid cols-3" style="align-items:start">
      <div class="card span-2">
        <div class="card-head"><h2>Search &amp; read</h2></div>
        <div class="row"><input type="search" id="web-query" placeholder="Search the web (DuckDuckGo, no API key needed)" />
          <button class="btn primary" id="web-btn">Search</button></div>
        <div id="web-results" style="margin-top:12px"></div>
        <div class="section-title" style="margin-top:16px">Read a page</div>
        <div class="row"><input type="text" id="page-url" placeholder="https://…" />
          <button class="btn" id="page-btn">Read</button></div>
        <div id="page-text" class="small" style="margin-top:10px"></div>
      </div>
      <div class="card">
        <div class="card-head"><h2>Research</h2></div>
        <p class="faint small">RAY searches, reads the top pages, then answers with citations. If nothing can be read it says so instead of inventing sources.</p>
        <textarea id="research-q" placeholder="e.g. What changed in the Bharatiya Nyaya Sanhita compared with the IPC?"></textarea>
        <button class="btn primary" id="research-btn" style="margin-top:8px">Research</button>
        <div id="research-out" class="small" style="margin-top:10px"></div>
        <div class="section-title" style="margin-top:14px">Bookmarks</div>
        <div class="list">${(data.bookmarks || []).slice(0, 12).map((mark) => `
          <div class="list-item"><span class="grow"><span class="title">${esc(mark.title)}</span>
            <div class="meta mono">${esc(mark.url.slice(0, 60))}</div></span>
            <button class="btn sm ghost" data-act="open-url" data-url="${esc(mark.url)}">Open</button></div>`).join('')
          || '<div class="empty">No bookmarks yet.</div>'}</div>
      </div>
    </div>`;

  $('#web-btn').addEventListener('click', async () => {
    const query = $('#web-query').value.trim();
    if (!query) return;
    const box = $('#web-results');
    box.innerHTML = '<div class="faint small">Searching…</div>';
    try {
      const result = await api(`/api/browser/search?q=${encodeURIComponent(query)}`);
      box.innerHTML = result.results.map((item) => `
        <div class="list-item"><span class="grow"><span class="title">${esc(item.title)}</span>
          <div class="meta">${esc(item.snippet || '')}</div>
          <div class="meta mono">${esc(item.url)}</div></span>
          <button class="btn sm ghost" data-act="open-url" data-url="${esc(item.url)}">Open</button>
          <button class="btn sm ghost" data-act="read-page" data-url="${esc(item.url)}">Read</button>
        </div>`).join('') || '<div class="empty">No results.</div>';
    } catch (error) { box.innerHTML = `<div class="banner error">${esc(error.message)}</div>`; }
  });

  $('#page-btn').addEventListener('click', async () => {
    const url = $('#page-url').value.trim();
    if (!url) return;
    await readPage(url);
  });

  $('#research-btn').addEventListener('click', async () => {
    const question = $('#research-q').value.trim();
    if (!question) return;
    const box = $('#research-out');
    box.innerHTML = '<div class="faint small">Searching, reading sources, then synthesising…</div>';
    try {
      const result = await api('/api/browser/research', { method: 'POST', body: { question } });
      box.innerHTML = `
        ${result.error ? `<div class="banner">${esc(result.error)}</div>` : ''}
        <div class="card tight" style="margin-top:8px;white-space:pre-wrap">${esc(result.report || 'No report generated.')}</div>
        <div class="section-title" style="margin-top:10px">Sources</div>
        ${(result.sources || []).map((source, index) => `<div class="list-item">
          <span class="faint mono">[${index + 1}]</span>
          <span class="grow"><span class="title">${esc(source.title)}</span>
            <div class="meta mono">${esc(source.url)}</div>
            <div class="meta">${source.ok ? `${num(source.chars)} chars read` : `failed: ${esc(source.error)}`}</div></span>
  </div>`).join('')}`;
    } catch (error) { box.innerHTML = `<div class="banner error">${esc(error.message)}</div>`; }
  });
};

async function readPage(url) {
  const box = $('#page-text');
  if (!box) return;
  box.innerHTML = '<div class="faint small">Fetching…</div>';
  try {
    const page = await api('/api/browser/fetch', { method: 'POST', body: { url } });
    box.innerHTML = page.ok
      ? `<h4>${esc(page.title)}</h4><div class="mono-wrap">${esc(page.url)}</div>
         <div style="margin-top:10px;white-space:pre-wrap;max-height:340px;overflow:auto">${esc(page.text)}</div>`
      : `<div class="banner error">${esc(page.error)}</div>`;
  } catch (error) { box.innerHTML = `<div class="banner error">${esc(error.message)}</div>`; }
}

/* ------------------------------------------------------------ productivity */
VIEWS.productivity = async function () {
  const [focus, tasks, notes] = await Promise.all([api('/api/analytics'), api('/api/tasks'), api('/api/notes')]);
  const stats = focus.focus;
  view().innerHTML = `
    <div class="grid cols-4">
      <div class="card"><div class="metric"><span class="label">Focus this week</span>
        <span class="value">${num(stats.total_minutes)}m</span>
        <span class="hint">${stats.sessions} sessions</span></div></div>
      <div class="card"><div class="metric"><span class="label">Tasks open</span>
        <span class="value">${tasks.stats.open}</span><span class="hint">${tasks.stats.completed_today} done today</span></div></div>
      <div class="card"><div class="metric"><span class="label">Completion rate</span>
        <span class="value">${tasks.stats.completion_rate}%</span><span class="hint">${tasks.stats.done} of ${tasks.stats.total}</span></div></div>
      <div class="card"><div class="metric"><span class="label">Notes</span>
        <span class="value">${notes.stats.total}</span><span class="hint">${notes.stats.pinned} pinned</span></div></div>
    </div>
    <div class="grid cols-2" style="align-items:start">
      <div class="card">
        <div class="card-head"><h2>Focus timer</h2></div>
        <div class="row"><input type="number" id="focus-min" value="25" min="5" max="120" style="width:90px" />
          <input type="text" id="focus-label" placeholder="What are you focusing on?" />
          <button class="btn primary" id="focus-start">Start</button></div>
        <div id="focus-state" class="faint small" style="margin-top:10px">No session running.</div>
      </div>
      <div class="card">
        <div class="card-head"><h2>Habits</h2></div>
        <div class="row"><input type="text" id="habit-name" placeholder="New habit, e.g. Read 20 pages" />
          <button class="btn" id="habit-add">Add</button></div>
        <div id="habits" class="list" style="margin-top:10px"></div>
      </div>
    </div>`;

  $('#habits').innerHTML = `
    <div class="empty">Habits are stored locally. Create one and RAY will track streaks.
      <div style="margin-top:8px"><button class="btn sm" data-act="quick" data-cmd="Add task Read 20 pages every day">Add a daily reading task</button></div>
    </div>`;

  $('#focus-start').addEventListener('click', async () => {
    const minutes = Number($('#focus-min').value || 25);
    const label = $('#focus-label').value.trim();
    try {
      const session = await api('/api/tools/call', {
        method: 'POST', body: { tool: 'start_focus', args: { minutes, label } },
      });
      $('#focus-state').innerHTML = `Session started for ${minutes} minutes${label ? ` · ${esc(label)}` : ''}. RAY will remember it in your statistics.`;
      toast('Focus started', `${minutes} minutes`, 'ok');
    } catch (error) { toast('Could not start', error.message, 'error'); }
  });
};

/* --------------------------------------------------------------- calendar */
VIEWS.calendar = async function () {
  const target = state.calendarMonth || {};
  const data = await api(`/api/calendar?view=month${target.year ? `&year=${target.year}&month=${target.month}` : ''}`);
  const monthLabel = data.label;
  const today = new Date();
  view().innerHTML = `
    <div class="grid cols-3" style="align-items:start">
      <div class="card span-2">
        <div class="card-head"><h2>${esc(monthLabel)}</h2><span class="spacer"></span>
          <button class="btn sm" data-act="calendar-month" data-year="${data.month === 1 ? data.year - 1 : data.year}" data-month="${data.month === 1 ? 12 : data.month - 1}">‹</button>
          <button class="btn sm" data-act="calendar-month" data-year="${today.getFullYear()}" data-month="${today.getMonth() + 1}">Today</button>
          <button class="btn sm" data-act="calendar-month" data-year="${data.month === 12 ? data.year + 1 : data.year}" data-month="${data.month === 12 ? 1 : data.month + 1}">›</button>
        </div>
        <div class="grid" style="grid-template-columns:repeat(7,1fr);gap:6px" id="calendar-grid">
          ${['Mon', 'Tue', 'Wed', 'Thu', 'Fri', 'Sat', 'Sun'].map((day) => `<div class="faint tiny center">${day}</div>`).join('')}
          ${data.weeks.flat().map((day) => `
            <div class="card tight" style="min-height:78px;padding:8px;${day.is_today ? 'border-color:rgba(47,214,232,0.5)' : ''};${day.in_month ? '' : 'opacity:0.42'}">
              <div class="faint tiny">${esc(day.date.slice(8))}</div>
              ${day.events.slice(0, 3).map((event) => `<div class="tiny" style="color:var(--cyan);overflow:hidden;text-overflow:ellipsis;white-space:nowrap">${esc(shortTime(event.start_at))} ${esc(event.title)}</div>`).join('')}
            </div>`).join('')}
        </div>
      </div>
      <div class="card">
        <div class="card-head"><h2>New event</h2></div>
        <div class="col">
          <label class="field"><span>Title</span><input type="text" id="event-title" placeholder="Study session" /></label>
          <label class="field"><span>Start</span><input type="datetime-local" id="event-start" /></label>
          <label class="field"><span>End</span><input type="datetime-local" id="event-end" /></label>
          <label class="field"><span>Location</span><input type="text" id="event-location" placeholder="optional" /></label>
          <button class="btn primary" id="event-add">Add event</button>
          <div class="section-title" style="margin-top:12px">Today</div>
          <div id="today-events" class="list"></div>
        </div>
      </div>
    </div>`;

  const todayData = await api('/api/calendar?view=day');
  $('#today-events').innerHTML = todayData.events.length
    ? todayData.events.map((event) => `<div class="list-item"><span class="pill info">${esc(shortTime(event.start_at))}</span>
        <span class="grow"><span class="title">${esc(event.title)}</span></span>
        <button class="btn sm ghost" data-act="delete-event" data-id="${event.id}">✕</button></div>`).join('')
    : '<div class="empty">Nothing today.</div>';

  const now = new Date();
  const iso = new Date(now.getTime() - now.getTimezoneOffset() * 60000).toISOString().slice(0, 16);
  $('#event-start').value = iso;
  $('#event-end').value = iso;

  $('#event-add').addEventListener('click', async () => {
    const title = $('#event-title').value.trim();
    if (!title) { toast('Add a title', '', 'warn'); return; }
    try {
      await api('/api/calendar', {
        method: 'POST',
        body: {
          title,
          start_at: $('#event-start').value,
          end_at: $('#event-end').value || null,
          location: $('#event-location').value.trim(),
        },
      });
      toast('Event added', title, 'ok');
      VIEWS.calendar();
    } catch (error) { toast('Could not add', error.message, 'error'); }
  });
};

/* ------------------------------------------------------------------ notes */
VIEWS.notes = async function () {
  const data = await api('/api/notes');
  let selected = data.notes[0] || null;
  const renderEditor = () => {
    if (!selected) {
      $('#note-editor').innerHTML = '<div class="empty">Select a note or create one.</div>';
      return;
    }
    $('#note-editor').innerHTML = `
      <div class="row"><input type="text" id="note-title" value="${esc(selected.title)}" />
        <button class="btn sm" id="note-pin">${selected.pinned ? 'Unpin' : 'Pin'}</button>
        <button class="btn sm danger" data-act="delete-note" data-id="${selected.id}">Delete</button></div>
      <div class="row" style="margin:8px 0"><input type="text" id="note-category" value="${esc(selected.category)}" placeholder="category" /></div>
      <textarea id="note-body" style="min-height:320px">${esc(selected.body)}</textarea>
      <div class="row" style="margin-top:8px"><button class="btn primary" id="note-save">Save</button>
        <span class="faint small">Autosaves when you change something and stop typing.</span></div>`;
    let timer = null;
    const save = async () => {
      try {
        selected = await api(`/api/notes/${selected.id}`, {
          method: 'PATCH',
          body: {
            title: $('#note-title').value,
            body: $('#note-body').value,
            category: $('#note-category').value,
          },
        });
        toast('Saved', '', 'ok', 1200);
        VIEWS.notes();
      } catch (error) { toast('Could not save', error.message, 'error'); }
    };
    $('#note-save').addEventListener('click', save);
    ['#note-title', '#note-body', '#note-category'].forEach((selector) => {
      $(selector).addEventListener('input', () => {
        clearTimeout(timer);
        timer = setTimeout(save, 1500);
      });
    });
    $('#note-pin').addEventListener('click', async () => {
      await api(`/api/notes/${selected.id}`, { method: 'PATCH', body: { pinned: !selected.pinned } });
      VIEWS.notes();
    });
  };

  view().innerHTML = `
    <div class="grid cols-3" style="align-items:start">
      <div class="card">
        <div class="card-head"><h2>Notes</h2><span class="spacer"></span>
          <button class="btn sm primary" id="note-new">New</button></div>
        <input type="search" id="note-search" placeholder="Search notes" style="margin-bottom:8px" />
        <div class="list" id="note-list" style="max-height:520px;overflow:auto">
          ${data.notes.map((note) => `
            <button class="list-item" data-note="${note.id}" style="width:100%;text-align:left">
              <span class="grow"><span class="title">${note.pinned ? '📌 ' : ''}${esc(note.title)}</span>
                <div class="meta">${esc(note.category)} · ${esc(relative(note.updated_at))}</div></span>
            </button>`).join('') || '<div class="empty">No notes yet.</div>'}
        </div>
      </div>
      <div class="card span-2" id="note-editor"></div>
    </div>`;

  renderEditor();
  $$('#note-list [data-note]').forEach((element) => {
    element.addEventListener('click', () => {
      selected = data.notes.find((note) => String(note.id) === element.dataset.note);
      renderEditor();
    });
  });
  $('#note-new').addEventListener('click', async () => {
    try {
      selected = await api('/api/notes', { method: 'POST', body: { title: 'New note', body: '', category: 'general' } });
      VIEWS.notes();
    } catch (error) { toast('Could not create', error.message, 'error'); }
  });
  $('#note-search').addEventListener('input', async (event) => {
    const query = event.target.value.trim();
    if (!query) { $('#note-list').innerHTML = ''; VIEWS.notes(); return; }
    const result = await api(`/api/notes?q=${encodeURIComponent(query)}`);
    $('#note-list').innerHTML = result.notes.map((note) => `
      <button class="list-item" data-note="${note.id}" style="width:100%;text-align:left">
        <span class="grow"><span class="title">${esc(note.title)}</span>
          <div class="meta">${esc(note.category)}</div></span></button>`).join('')
      || '<div class="empty">No match.</div>';
  });
};

/* ------------------------------------------------------------------ tasks */
VIEWS.tasks = async function () {
  const data = await api('/api/tasks?status=all&include_done=1');
  view().innerHTML = `
    <div class="grid cols-4">
      <div class="card"><div class="metric"><span class="label">Open</span><span class="value">${data.stats.open}</span></div></div>
      <div class="card"><div class="metric"><span class="label">Overdue</span><span class="value">${data.stats.overdue}</span></div></div>
      <div class="card"><div class="metric"><span class="label">Done today</span><span class="value">${data.stats.completed_today}</span></div></div>
      <div class="card"><div class="metric"><span class="label">Completion</span><span class="value">${data.stats.completion_rate}%</span></div></div>
    </div>
    <div class="card">
      <div class="card-head"><h2>Add a task</h2></div>
      <div class="form-row">
        <label class="field" style="grid-column:span 2"><span>Title</span><input type="text" id="task-title" placeholder="What needs doing?" /></label>
        <label class="field"><span>Priority</span>
          <select id="task-priority"><option>low</option><option selected>normal</option><option>high</option><option>urgent</option></select></label>
        <label class="field"><span>Due</span><input type="datetime-local" id="task-due" /></label>
        <label class="field"><span>Category</span><input type="text" id="task-category" placeholder="general" /></label>
        <label class="field"><span>Repeat</span>
          <select id="task-repeat"><option value="">never</option><option>daily</option><option>weekdays</option><option>weekly</option><option>monthly</option></select></label>
      </div>
      <div class="row" style="margin-top:10px"><button class="btn primary" id="task-add">Add task</button></div>
    </div>
    <div class="card">
      <div class="card-head"><h2>All tasks</h2><span class="spacer"></span>
        <div class="tabs" id="task-tabs">
          <button class="tab active" data-filter="open">Open</button>
          <button class="tab" data-filter="all">All</button>
          <button class="tab" data-filter="done">Done</button>
        </div></div>
      <div class="table-wrap"><table class="table" id="task-table">${taskTable(data.tasks)}</table></div>
    </div>`;

  $('#task-add').addEventListener('click', async () => {
    const title = $('#task-title').value.trim();
    if (!title) { toast('Add a title', '', 'warn'); return; }
    try {
      await api('/api/tasks', {
        method: 'POST',
        body: {
          title,
          priority: $('#task-priority').value,
          due_at: $('#task-due').value || null,
          category: $('#task-category').value.trim() || 'general',
          recurrence: $('#task-repeat').value || null,
        },
      });
      toast('Task added', title, 'ok');
      VIEWS.tasks();
    } catch (error) { toast('Could not add', error.message, 'error'); }
  });
  $$('#task-tabs .tab').forEach((tab) => tab.addEventListener('click', () => {
    const filter = tab.dataset.filter;
    $$('#task-tabs .tab').forEach((node) => node.classList.toggle('active', node === tab));
    const rows = filter === 'all' ? data.tasks : data.tasks.filter((task) => filter === 'done' ? task.status === 'done' : task.status !== 'done');
    $('#task-table').innerHTML = taskTable(rows);
  }));
};

function taskTable(tasks) {
  if (!tasks.length) return '<tr><td colspan="5"><div class="empty">No tasks here yet.</div></td></tr>';
  return `<thead><tr><th>Task</th><th>Priority</th><th>Due</th><th>Status</th><th></th></tr></thead>
    <tbody>${tasks.map((task) => `
      <tr>
        <td class="strong">${esc(task.title)}${task.recurrence ? `<span class="faint tiny"> · ${esc(task.recurrence)}</span>` : ''}</td>
        <td>${esc(task.priority)}</td>
        <td>${task.due_at ? `<span class="${task.overdue ? 'pill bad' : ''}">${esc(timeLabel(task.due_at))}</span>` : '—'}</td>
        <td>${esc(task.status)}</td>
        <td style="text-align:right;white-space:nowrap">
          ${task.status !== 'done' ? `<button class="btn sm ghost" data-act="complete-task" data-id="${task.id}">Done</button>` : ''}
          <button class="btn sm ghost danger" data-act="delete-task" data-id="${task.id}">✕</button>
        </td>
      </tr>`).join('')}</tbody>`;
}

/* -------------------------------------------------------------- reminders */
VIEWS.reminders = async function () {
  const data = await api('/api/reminders?all=1');
  view().innerHTML = `
    <div class="card">
      <div class="card-head"><h2>New reminder</h2></div>
      <div class="row">
        <input type="text" id="rem-title" placeholder="Remind me to…" />
        <input type="datetime-local" id="rem-due" style="max-width:220px" />
        <select id="rem-repeat" style="max-width:150px"><option value="">once</option><option>daily</option><option>weekdays</option><option>weekly</option></select>
        <button class="btn primary" id="rem-add">Set</button>
      </div>
    </div>
    <div class="card">
      <div class="card-head"><h2>Active</h2><span class="spacer"></span><span class="pill">${data.stats.active} active</span></div>
      <div class="table-wrap"><table class="table">
        <thead><tr><th>Reminder</th><th>When</th><th>Repeat</th><th></th></tr></thead>
        <tbody>${data.reminders.length ? data.reminders.map((item) => `
          <tr><td class="strong">${esc(item.title)}</td><td>${esc(timeLabel(item.due_at))}</td>
            <td>${esc(item.recurrence || 'once')}${item.enabled ? '' : ' · fired'}</td>
            <td style="text-align:right"><button class="btn sm ghost" data-act="snooze" data-id="${item.id}">Snooze 10m</button>
              <button class="btn sm ghost danger" data-act="delete-reminder" data-id="${item.id}">✕</button></td></tr>`).join('')
          : '<tr><td colspan="4"><div class="empty">No reminders yet.</div></td></tr>'}</tbody>
      </table></div>
    </div>`;

  const now = new Date(Date.now() + 3600000 - new Date().getTimezoneOffset() * 60000).toISOString().slice(0, 16);
  $('#rem-due').value = now;
  $('#rem-add').addEventListener('click', async () => {
    const title = $('#rem-title').value.trim();
    if (!title) { toast('Add a title', '', 'warn'); return; }
    try {
      await api('/api/reminders', {
        method: 'POST',
        body: { title, due_at: $('#rem-due').value, recurrence: $('#rem-repeat').value || null },
      });
      toast('Reminder set', title, 'ok');
      VIEWS.reminders();
    } catch (error) { toast('Could not set', error.message, 'error'); }
  });
};

/* ------------------------------------------------------------- automation */
VIEWS.automation = async function () {
  const data = await api('/api/automation');
  view().innerHTML = `
    <div class="grid cols-3" style="align-items:start">
      <div class="card span-2">
        <div class="card-head"><h2>Workflows</h2><span class="spacer"></span>
          <span class="pill ${data.engine.enabled ? 'ok' : 'bad'}">engine ${data.engine.enabled ? 'on' : 'off'}</span>
          <span class="pill">${data.scheduler.running ? 'scheduler running' : 'scheduler stopped'}</span></div>
        <div class="table-wrap"><table class="table">
          <thead><tr><th>Name</th><th>Trigger</th><th>Last run</th><th>Runs</th><th></th></tr></thead>
          <tbody>${data.workflows.map((workflow) => `
            <tr>
              <td class="strong">${esc(workflow.name)}<div class="faint tiny">${esc(workflow.description)}</div></td>
              <td class="mono tiny">${esc(triggerLabel(workflow.trigger))}</td>
              <td>${workflow.last_run ? esc(relative(workflow.last_run)) : 'never'}</td>
              <td class="num">${workflow.run_count}</td>
              <td style="text-align:right;white-space:nowrap">
                <button class="btn sm ghost" data-act="workflow-toggle" data-id="${workflow.id}" data-enabled="${workflow.enabled ? 0 : 1}">${workflow.enabled ? 'Disable' : 'Enable'}</button>
                <button class="btn sm ghost" data-act="workflow-run" data-id="${workflow.id}">Run</button>
                <button class="btn sm ghost danger" data-act="workflow-delete" data-id="${workflow.id}">✕</button>
              </td>
            </tr>`).join('')}</tbody>
        </table></div>
      </div>
      <div class="card">
        <div class="card-head"><h2>New workflow</h2></div>
        <div class="col">
          <label class="field"><span>Name</span><input type="text" id="wf-name" placeholder="Evening review" /></label>
          <label class="field"><span>Trigger</span>
            <select id="wf-trigger">
              <option value="daily">Every day at…</option>
              <option value="every">Every N minutes</option>
              <option value="file_added">When a file appears</option>
              <option value="on_start">When RAY starts</option>
              <option value="manual">Only when I run it</option>
            </select></label>
          <label class="field"><span>Time / minutes / folder</span><input type="text" id="wf-value" placeholder="18:00 or 30 or C:\\Users\\me\\Downloads" /></label>
          <label class="field"><span>Action</span>
            <select id="wf-action">
              <option value="notify">Show a notification</option>
              <option value="tool:list_tasks">Read my tasks</option>
              <option value="tool:list_schedule">Read my schedule</option>
              <option value="tool:system_report">Read system health</option>
              <option value="tool:plan_downloads_organisation">Plan a Downloads tidy-up</option>
            </select></label>
          <label class="field"><span>Message (for notifications)</span><input type="text" id="wf-message" placeholder="Time to review the day" /></label>
          <button class="btn primary" id="wf-add">Create workflow</button>
          <div class="faint tiny">Destructive tools can never run automatically. Level 2+ actions pause for your approval.</div>
        </div>
      </div>
    </div>
    <div class="card">
      <div class="card-head"><h2>Run history</h2></div>
      <div class="table-wrap"><table class="table">
        <thead><tr><th>When</th><th>Workflow</th><th>Trigger</th><th>Status</th></tr></thead>
        <tbody>${data.runs.length ? data.runs.map((run) => `
          <tr><td>${esc(timeLabel(run.started_at))}</td><td>#${run.workflow_id}</td>
            <td>${esc(run.trigger)}</td><td><span class="pill ${run.status === 'ok' ? 'ok' : 'warn'}">${esc(run.status)}</span></td></tr>`).join('')
          : '<tr><td colspan="4"><div class="empty">No runs yet.</div></td></tr>'}</tbody>
      </table></div>
    </div>`;

  $('#wf-add').addEventListener('click', async () => {
    const name = $('#wf-name').value.trim();
    const type = $('#wf-trigger').value;
    const value = $('#wf-value').value.trim();
    const action = $('#wf-action').value;
    const message = $('#wf-message').value.trim();
    if (!name) { toast('Name the workflow', '', 'warn'); return; }
    let trigger = { type };
    if (type === 'daily') trigger = { type, at: value || '18:00' };
    if (type === 'every') trigger = { type, minutes: Number(value) || 60 };
    if (type === 'file_added') trigger = { type, folder: value || 'Downloads' };
    const actions = action.startsWith('tool:')
      ? [{ type: 'tool', tool: action.slice(5), args: {} }, { type: 'notify', title: name, body: message || 'Automation ran.' }]
      : [{ type: 'notify', title: name, body: message || 'Automation ran.' }];
    try {
      await api('/api/automation', { method: 'POST', body: { name, trigger, actions, enabled: true } });
      toast('Workflow created', name, 'ok');
      VIEWS.automation();
    } catch (error) { toast('Could not create', error.message, 'error'); }
  });
};

function triggerLabel(trigger) {
  if (!trigger) return 'manual';
  if (trigger.type === 'daily') return `daily ${trigger.at}`;
  if (trigger.type === 'every') return `every ${trigger.minutes}m`;
  if (trigger.type === 'file_added') return `file in ${String(trigger.folder).split(/[\\/]/).pop()}`;
  if (trigger.type === 'on_start') return 'on start';
  return trigger.type;
}

/* ----------------------------------------------------------------- agents */
VIEWS.agents = async function () {
  const data = await api('/api/agents');
  view().innerHTML = `
    <div class="grid cols-3" style="align-items:start">
      <div class="card span-2">
        <div class="card-head"><h2>Specialist agents</h2><span class="spacer"></span>
          <span class="pill">${data.agents.length} agents · shared permissions</span></div>
        <div class="grid cols-2">
          ${data.agents.map((agent) => `
            <div class="card tight">
              <div class="row"><strong>${esc(agent.title)}</strong>
                <span class="push pill violet">${agent.max_steps} steps</span></div>
              <p class="faint small" style="margin-top:6px">${esc(agent.description)}</p>
              <div class="mono-wrap" style="margin-top:6px">${esc(agent.tools.slice(0, 6).join(', '))}${agent.tools.length > 6 ? ' …' : ''}</div>
              <button class="btn sm" style="margin-top:8px" data-act="agent-run" data-agent="${esc(agent.name)}">Run a task</button>
            </div>`).join('')}
        </div>
      </div>
      <div class="card">
        <div class="card-head"><h2>Recent runs</h2></div>
        <div class="list">
          ${data.recent_runs.length ? data.recent_runs.map((run) => `
            <div class="list-item"><span class="grow"><span class="title">${esc(run.goal.slice(0, 70))}</span>
              <div class="meta">${esc(run.agent)} · ${run.steps} steps · ${esc(relative(run.started_at))}</div></span>
              <span class="pill ${run.status === 'completed' ? 'ok' : 'warn'}">${esc(run.status)}</span></div>`).join('')
            : '<div class="empty">No agent runs yet.</div>'}
        </div>
      </div>
    </div>`;
};

/* ---------------------------------------------------------------- knowledge */
VIEWS.knowledge = async function () {
  const data = await api('/api/knowledge');
  view().innerHTML = `
    <div class="grid cols-3" style="align-items:start">
      <div class="card">
        <div class="card-head"><h2>Index</h2></div>
        <div class="stat-row">
          <div class="stat"><div class="n">${num(data.status.files)}</div><div class="l">files</div></div>
          <div class="stat"><div class="n">${esc(data.status.size_human)}</div><div class="l">indexed size</div></div>
        </div>
        <div class="col" style="margin-top:12px">
          <button class="btn primary" id="index-run">${data.running ? 'Indexing…' : 'Index now'}</button>
          <div class="faint small" id="index-progress">
            ${data.progress?.seen ? `${data.progress.seen} scanned, ${data.progress.indexed} stored` : 'Idle'}
          </div>
          <div class="section-title" style="margin-top:10px">Watched folders</div>
          ${(data.status.roots || []).map((root) => `<div class="list-item">
            <span class="grow"><span class="title mono small">${esc(root.path)}</span>
              <div class="meta">${root.file_count} files · ${root.last_indexed ? esc(relative(root.last_indexed)) : 'never indexed'}</div></span>
            <button class="btn sm ghost danger" data-act="index-remove" data-path="${esc(root.path)}">Remove</button></div>`).join('')
            || '<div class="empty">No folders watched.</div>'}
          <div class="row" style="margin-top:8px"><input type="text" id="root-path" placeholder="Add a folder path" />
            <button class="btn" id="root-add">Add</button></div>
        </div>
      </div>
      <div class="card span-2">
        <div class="card-head"><h2>Search your documents</h2></div>
        <div class="row"><input type="search" id="kb-query" placeholder="Search inside indexed documents" />
          <button class="btn primary" id="kb-search">Search</button></div>
        <div id="kb-results" style="margin-top:10px"></div>
        <div class="section-title" style="margin-top:14px">Read a document</div>
        <div class="row"><input type="text" id="doc-path" placeholder="Path to a PDF, DOCX, XLSX, PPTX or text file" />
          <button class="btn" id="doc-open">Extract text</button></div>
        <div id="doc-out" class="small" style="margin-top:10px"></div>
      </div>
    </div>`;

  $('#index-run').addEventListener('click', async () => {
    const button = $('#index-run');
    button.disabled = true;
    button.textContent = 'Indexing…';
    try {
      const result = await api('/api/knowledge/index', { method: 'POST', body: {} });
      toast('Index complete', `${result.seen} scanned · ${result.indexed} stored · ${result.skipped} skipped`, 'ok', 6000);
      VIEWS.knowledge();
    } catch (error) {
      toast('Index failed', error.message, 'error');
      button.disabled = false;
      button.textContent = 'Index now';
    }
  });
  $('#root-add').addEventListener('click', async () => {
    const path = $('#root-path').value.trim();
    if (!path) return;
    try {
      await api('/api/knowledge/roots', { method: 'POST', body: { action: 'add', path } });
      toast('Folder added', path, 'ok');
      VIEWS.knowledge();
    } catch (error) { toast('Could not add', error.message, 'error'); }
  });
  $('#kb-search').addEventListener('click', async () => {
    const query = $('#kb-query').value.trim();
    if (!query) return;
    const box = $('#kb-results');
    box.innerHTML = '<div class="faint small">Searching…</div>';
    try {
      const result = await api(`/api/knowledge/search?q=${encodeURIComponent(query)}`);
      box.innerHTML = result.results.length
        ? result.results.map((row) => `<div class="list-item"><span class="grow">
            <span class="title">${esc(row.name)}</span>
            <div class="meta">${esc(row.snippet || row.preview || '')}</div>
            <div class="meta mono">${esc(row.path)}</div></span>
            <button class="btn sm ghost" data-act="open-file" data-path="${esc(row.path)}">Open</button></div>`).join('')
        : `<div class="empty">Nothing indexed matched. ${esc(result.mode)}</div>`;
    } catch (error) { box.innerHTML = `<div class="banner error">${esc(error.message)}</div>`; }
  });
  $('#doc-open').addEventListener('click', async () => {
    const path = $('#doc-path').value.trim();
    if (!path) return;
    const box = $('#doc-out');
    box.innerHTML = '<div class="faint small">Extracting…</div>';
    try {
      const document = await api('/api/knowledge/document', { method: 'POST', body: { path, max_chars: 12000 } });
      box.innerHTML = `
        <div class="row"><strong>${esc(document.name)}</strong>
          <span class="pill">${document.chars} chars${document.pages ? ` · ${document.pages} pages` : ''}</span></div>
        ${document.note ? `<div class="banner" style="margin-top:8px">${esc(document.note)}</div>` : ''}
        <div style="margin-top:10px;white-space:pre-wrap;max-height:320px;overflow:auto">${esc(document.text || '(no text)')}</div>`;
    } catch (error) { box.innerHTML = `<div class="banner error">${esc(error.message)}</div>`; }
  });
};

/* ------------------------------------------------------------------ media */
VIEWS.media = async function () {
  const data = await api('/api/media');
  view().innerHTML = `
    <div class="grid cols-3" style="align-items:start">
      <div class="card span-2">
        <div class="card-head"><h2>Library</h2><span class="spacer"></span>
          <input type="search" id="media-search" placeholder="Filter" style="max-width:220px" /></div>
        <div id="media-list" class="list" style="max-height:560px;overflow:auto">${mediaList(data.library)}</div>
      </div>
      <div class="card">
        <div class="card-head"><h2>Playlists</h2></div>
        <div class="list">${(data.playlists || []).length ? data.playlists.map((playlist) => `
          <div class="list-item"><span class="grow"><span class="title">${esc(playlist.name)}</span>
            <div class="meta">${playlist.tracks} tracks</div></span>
            <button class="btn sm" data-act="play-media" data-query="${esc(playlist.name)}">Play</button></div>`).join('')
          : '<div class="empty">No .m3u or .pls playlists found in your media folders.</div>'}</div>
        <div class="section-title" style="margin-top:14px">Folders scanned</div>
        ${(data.roots || []).map((root) => `<div class="mono-wrap">${esc(root)}</div>`).join('')}
        <div class="banner info small" style="margin-top:12px">
          RAY plays files that already exist on this machine through your default player.
          It does not download, rip or bypass DRM.
        </div>
      </div>
    </div>`;
  $('#media-search').addEventListener('input', async (event) => {
    const query = event.target.value.trim();
    const result = await api(`/api/media?q=${encodeURIComponent(query)}`);
    $('#media-list').innerHTML = mediaList(result.library);
  });
};

function mediaList(tracks) {
  if (!tracks.length) return '<div class="empty">No media found in your Music, Videos or Downloads folders.</div>';
  return tracks.map((track) => `
    <div class="list-item"><span class="grow"><span class="title">${esc(track.title)}</span>
      <div class="meta">${esc(track.kind)} · ${esc(track.size_human)} · ${esc(shortTime(track.modified))}</div></span>
      <button class="btn sm" data-act="play-media" data-query="${esc(track.title)}">Play</button>
      <button class="btn sm ghost" data-act="reveal-file" data-path="${esc(track.path)}">Show</button></div>`).join('');
}

/* ----------------------------------------------------------------- system */
VIEWS.system = async function () {
  const data = await api('/api/system?history=90');
  const snapshot = data.snapshot;
  view().innerHTML = `
    <div class="grid cols-4">
      <div class="card"><div class="metric"><span class="label">CPU</span><span class="value">${snapshot.cpu.percent.toFixed(0)}%</span>
        <div class="hint">${snapshot.cpu.cores_physical || '?'} physical · ${snapshot.cpu.cores_logical} logical</div></div></div>
      <div class="card"><div class="metric"><span class="label">Memory</span><span class="value">${snapshot.memory.percent.toFixed(0)}%</span>
        <div class="hint">${bytes(snapshot.memory.used_bytes)} / ${bytes(snapshot.memory.total_bytes)}</div></div></div>
      <div class="card"><div class="metric"><span class="label">Disk</span><span class="value">${(snapshot.disks[0]?.percent || 0).toFixed(0)}%</span>
        <div class="hint">${bytes(snapshot.disks[0]?.free_bytes)} free on ${esc(snapshot.disks[0]?.mount || '—')}</div></div></div>
      <div class="card"><div class="metric"><span class="label">Uptime</span><span class="value" style="font-size:18px">${esc(snapshot.uptime.human)}</span>
        <div class="hint">boot ${esc(new Date(snapshot.uptime.boot_time * 1000).toLocaleString())}</div></div></div>
    </div>
    <div class="grid cols-3" style="align-items:start">
      <div class="card span-2">
        <div class="card-head"><h2>Last 90 samples</h2><span class="spacer"></span>
          <span class="pill info">CPU</span><span class="pill violet">Memory</span></div>
        <canvas class="chart" id="sys-chart"></canvas>
        <div class="faint tiny" style="margin-top:6px">Samples are taken while this page is open. No fake historical data.</div>
      </div>
      <div class="card">
        <div class="card-head"><h2>Sensors</h2></div>
        ${snapshot.temperatures.available
          ? snapshot.temperatures.sensors.map((sensor) => `<div class="list-item"><span class="grow"><span class="title">${esc(sensor.label)}</span>
              <div class="meta">${esc(sensor.chip)}</div></span><span class="pill">${sensor.current_c}°C</span></div>`).join('')
          : `<div class="empty">${esc(snapshot.temperatures.reason)}</div>`}
        <div class="section-title" style="margin-top:12px">Disks</div>
        ${snapshot.disks.map((disk) => `<div class="list-item"><span class="grow"><span class="title">${esc(disk.mount)}</span>
          <div class="meta">${esc(disk.filesystem)} · ${bytes(disk.free_bytes)} free</div></span>
          <span class="pill ${disk.percent > 92 ? 'bad' : ''}">${disk.percent}%</span></div>`).join('')}
      </div>
    </div>
    <div class="card">
      <div class="card-head"><h2>Processes</h2><span class="spacer"></span>
        <button class="btn sm ghost" id="sys-refresh">Refresh</button>
        <button class="btn sm ghost" data-act="panel" data-panel="task_manager">Task Manager</button></div>
      <div class="table-wrap"><table class="table">
        <thead><tr><th>Process</th><th>PID</th><th class="num">CPU %</th><th class="num">RAM</th><th>User</th></tr></thead>
        <tbody>${data.processes.map((process) => `
          <tr><td class="strong">${esc(process.name)}</td><td class="mono">${process.pid}</td>
            <td class="num">${process.cpu_percent.toFixed(1)}</td>
            <td class="num">${process.memory_mb.toFixed(1)} MB</td><td class="small">${esc(process.user || '')}</td></tr>`).join('')}</tbody>
      </table></div>
    </div>`;

  const draw = (history) => lineChart($('#sys-chart'), [
    { values: history.map((sample) => sample.cpu) },
    { values: history.map((sample) => sample.memory) },
  ]);
  draw(data.history);
  $('#sys-refresh').addEventListener('click', () => VIEWS.system());
  state.timers.system = setInterval(async () => {
    if (state.section !== 'system') return;
    try {
      const live = await api('/api/system?history=90');
      draw(live.history);
    } catch (error) { /* ignore */ }
  }, 8000);
};

/* --------------------------------------------------------------- security */
VIEWS.security = async function () {
  const data = await api('/api/security');
  const policy = data.policy;
  view().innerHTML = `
    ${data.server_exposed ? `<div class="banner">
      ⚠ The interface is bound beyond localhost. Anyone on this network could reach it. Switch
      Settings → Appearance → interface host back to 127.0.0.1 when you are done.</div>` : ''}
    ${data.config_problems.length ? `<div class="banner error">${data.config_problems.map(esc).join(' · ')}</div>` : ''}
    <div class="grid cols-3" style="align-items:start">
      <div class="card">
        <div class="card-head"><h2>Emergency stop</h2></div>
        <p class="faint small">Freezes every action above read-only until you release it.</p>
        <button class="btn ${policy.emergency_stop ? 'primary' : 'danger'}" id="emergency" style="margin-top:10px">
          ${policy.emergency_stop ? 'Release emergency stop' : 'Engage emergency stop'}</button>
        <div class="section-title" style="margin-top:16px">Session</div>
        <div class="row"><span class="faint small">Locked</span><span class="push"></span>
          <button class="btn sm" id="lock-toggle">${policy.locked ? 'Unlock' : 'Lock'}</button></div>
        <div class="section-title" style="margin-top:16px">Pending approvals</div>
        <div class="list" id="approval-list">
          ${data.pending.length ? data.pending.map((approval) => `
            <div class="card tight"><div class="row"><strong>${esc(approval.tool)}</strong>
              <span class="push pill warn">level ${approval.level}</span></div>
              <div class="faint small">${esc(approval.reason)}</div>
              <div class="mono-wrap">${esc(JSON.stringify(approval.args).slice(0, 200))}</div>
              <div class="row" style="margin-top:8px">
                <button class="btn sm danger" data-act="approval" data-id="${approval.id}" data-approved="0">Deny</button>
                <button class="btn sm primary" data-act="approval" data-id="${approval.id}" data-approved="1">Approve</button></div>
            </div>`).join('') : '<div class="empty">Nothing waiting.</div>'}
        </div>
      </div>
      <div class="card span-2">
        <div class="card-head"><h2>Policy</h2></div>
        <div class="form-row">
          <label class="field"><span>Mode</span>
            <select id="policy-mode">
              <option value="standard" ${policy.mode === 'standard' ? 'selected' : ''}>Standard (recommended)</option>
              <option value="trusted" ${policy.mode === 'trusted' ? 'selected' : ''}>Trusted (skip level 2 prompts)</option>
            </select></label>
          <label class="field"><span>Idle lock (minutes, 0 = off)</span>
            <input type="number" id="policy-lock" min="0" max="240" value="${esc(state.settings?.config?.permissions?.session_lock_minutes ?? 0)}" /></label>
          <label class="switch"><input type="checkbox" id="policy-shell" ${policy.allow_shell ? 'checked' : ''}><span class="track"></span>Allow allow-listed commands</label>
          <label class="switch"><input type="checkbox" id="policy-l2" ${state.settings?.config?.permissions?.level2_confirmation ? 'checked' : ''}><span class="track"></span>Confirm level 2 actions</label>
          <label class="switch"><input type="checkbox" id="policy-l3" ${state.settings?.config?.permissions?.level3_confirmation ? 'checked' : ''}><span class="track"></span>Confirm level 3 actions</label>
        </div>
        <div class="section-title" style="margin-top:16px">Protected locations (never modified)</div>
        <div class="mono-wrap">${data.sensitive_paths.map(esc).join(' · ')}</div>
        <div class="section-title" style="margin-top:16px">Audit log</div>
        <div class="table-wrap" style="max-height:320px;overflow:auto"><table class="table">
          <thead><tr><th>When</th><th>Action</th><th>Tool</th><th>Level</th><th>Result</th></tr></thead>
          <tbody>${data.audit.slice(0, 60).map((entry) => `
            <tr><td class="small">${esc(timeLabel(entry.ts))}</td><td class="strong">${esc(entry.action)}</td>
              <td class="mono small">${esc(entry.tool || '—')}</td><td>${entry.permission_level}</td>
              <td><span class="pill ${entry.result === 'ok' ? 'ok' : entry.result === 'blocked' ? 'warn' : 'bad'}">${esc(entry.result)}</span></td></tr>`).join('')}</tbody>
        </table></div>
      </div>
    </div>`;

  $('#emergency').addEventListener('click', async () => {
    const active = !policy.emergency_stop;
    if (active) {
      const ok = await confirmAction('Emergency stop', 'RAY will refuse every action that changes anything until you release it.', 'Engage');
      if (!ok) return;
    }
    await api('/api/security/emergency', { method: 'POST', body: { active } });
    toast('Emergency stop', active ? 'Engaged' : 'Released', active ? 'warn' : 'ok');
    VIEWS.security();
  });
  $('#lock-toggle').addEventListener('click', async () => {
    await api('/api/security/lock', { method: 'POST', body: { locked: !policy.locked } });
    VIEWS.security();
  });
  $('#policy-mode').addEventListener('change', async (event) => {
    await api('/api/security/policy', { method: 'POST', body: { mode: event.target.value } });
    toast('Policy updated', '', 'ok');
  });
  $('#policy-lock').addEventListener('change', async (event) => {
    await api('/api/security/policy', { method: 'POST', body: { session_lock_minutes: Number(event.target.value) } });
  });
  $('#policy-shell').addEventListener('change', async (event) => {
    await api('/api/security/policy', { method: 'POST', body: { allow_shell: event.target.checked } });
    toast('Shell access', event.target.checked ? 'Allow-listed commands enabled' : 'Disabled', 'ok');
  });
  $('#policy-l2').addEventListener('change', (event) => api('/api/security/policy', { method: 'POST', body: { level2_confirmation: event.target.checked } }).catch(() => {}));
  $('#policy-l3').addEventListener('change', (event) => api('/api/security/policy', { method: 'POST', body: { level3_confirmation: event.target.checked } }).catch(() => {}));
};

/* -------------------------------------------------------------- analytics */
VIEWS.analytics = async function () {
  const data = await api('/api/analytics');
  const providerRows = data.usage.by_provider || [];
  view().innerHTML = `
    <div class="grid cols-4">
      <div class="card"><div class="metric"><span class="label">AI calls today</span><span class="value">${num(data.usage.totals.calls)}</span>
        <div class="hint">${num(data.usage.totals.failures)} failed · ${num(data.usage.totals.cached)} cached</div></div></div>
      <div class="card"><div class="metric"><span class="label">Tokens</span><span class="value">${num(data.usage.totals.input_tokens + data.usage.totals.output_tokens)}</span>
        <div class="hint">${num(data.usage.totals.input_tokens)} in · ${num(data.usage.totals.output_tokens)} out</div></div></div>
      <div class="card"><div class="metric"><span class="label">Speech characters</span><span class="value">${num(data.usage.totals.characters)}</span>
        <div class="hint">Fish Audio</div></div></div>
      <div class="card"><div class="metric"><span class="label">Database</span><span class="value">${bytes(data.database.size_bytes)}</span>
        <div class="hint">${num(data.database.counts.audit_log)} audit entries</div></div></div>
    </div>
    <div class="grid cols-3" style="align-items:start">
      <div class="card span-2">
        <div class="card-head"><h2>Provider usage</h2></div>
        <div class="table-wrap"><table class="table">
          <thead><tr><th>Provider</th><th class="num">Calls</th><th class="num">OK</th><th class="num">Failed</th><th class="num">Cached</th><th class="num">Tokens</th></tr></thead>
          <tbody>${providerRows.length ? providerRows.map((row) => `
            <tr><td class="strong">${esc(row.provider)}</td><td class="num">${row.calls}</td><td class="num">${row.successes}</td>
              <td class="num">${row.failures}</td><td class="num">${row.cached}</td>
              <td class="num">${num((row.input_tokens || 0) + (row.output_tokens || 0))}</td></tr>`).join('')
            : '<tr><td colspan="6"><div class="empty">No AI calls recorded today.</div></td></tr>'}</tbody>
        </table></div>
        <div class="section-title" style="margin-top:16px">Agent activity</div>
        <div class="table-wrap"><table class="table">
          <thead><tr><th>Agent</th><th class="num">Runs</th><th class="num">Completed</th></tr></thead>
          <tbody>${(data.agents || []).map((row) => `<tr><td class="strong">${esc(row.agent)}</td>
            <td class="num">${row.runs}</td><td class="num">${row.completed || 0}</td></tr>`).join('')
            || '<tr><td colspan="3"><div class="empty">No runs yet.</div></td></tr>'}</tbody>
        </table></div>
      </div>
      <div class="card">
        <div class="card-head"><h2>Today</h2></div>
        <div class="stat-row">
          <div class="stat"><div class="n">${data.tasks.open}</div><div class="l">open tasks</div></div>
          <div class="stat"><div class="n">${data.focus.total_minutes}</div><div class="l">focus minutes</div></div>
          <div class="stat"><div class="n">${data.notes.total}</div><div class="l">notes</div></div>
          <div class="stat"><div class="n">${data.memories.total}</div><div class="l">memories</div></div>
          <div class="stat"><div class="n">${data.index.files}</div><div class="l">indexed files</div></div>
        </div>
        <div class="section-title" style="margin-top:14px">Recent provider errors</div>
        <div class="list">${(data.errors || []).length ? data.errors.map((error) => `
          <div class="list-item"><span class="grow"><span class="title">${esc(error.provider)} · ${esc(error.kind)}</span>
            <div class="meta">${esc(error.error)}</div></span></div>`).join('')
          : '<div class="empty">No provider errors.</div>'}</div>
        <div class="section-title" style="margin-top:14px">Cache</div>
        <div class="faint small">${data.cache.entries} cached responses (${data.cache.live} live)</div>
      </div>
    </div>`;
};

/* -------------------------------------------------------------- downloads */
VIEWS.downloads = async function () {
  const data = await api('/api/downloads');
  view().innerHTML = `
    <div class="grid cols-3" style="align-items:start">
      <div class="card span-2">
        <div class="card-head"><h2>Downloads</h2><span class="spacer"></span>
          <span class="pill mono">${esc(data.path)}</span>
          <button class="btn sm ghost" data-act="reveal-file" data-path="${esc(data.path)}">Open folder</button></div>
        ${fileList(data)}
        <div class="faint tiny" style="margin-top:8px">Click a file to open it. Bringing the file to RAY is always your call.</div>
      </div>
      <div class="card">
        <div class="card-head"><h2>Organise</h2></div>
        <p class="faint small">${data.organisation.plan.length} file(s) can be sorted into categories.</p>
        <div class="list" style="max-height:260px;overflow:auto;margin-top:8px">
          ${data.organisation.plan.map((item) => `<div class="list-item"><span class="grow">
            <span class="title">${esc(item.file)}</span><div class="meta">→ ${esc(item.category)}</div></span></div>`).join('')
            || '<div class="empty">Nothing to organise.</div>'}
        </div>
        ${data.organisation.plan.length ? '<button class="btn primary" id="dl-organise" style="margin-top:10px">Apply plan</button>' : ''}
        ${data.organisation.unmatched.length ? `<div class="faint tiny" style="margin-top:8px">Leaving ${data.organisation.unmatched.length} file(s) alone (no matching category).</div>` : ''}
      </div>
    </div>`;
  const button = $('#dl-organise');
  if (button) {
    button.addEventListener('click', async () => {
      const ok = await confirmAction('Move files', `RAY will move ${data.organisation.plan.length} file(s) into subfolders of Downloads.`, 'Move');
      if (!ok) return;
      try {
        const result = await api('/api/files/organise', { method: 'POST', body: { plan: data.organisation.plan } });
        toast('Organised', `${result.count} moved, ${result.failed.length} failed`, 'ok');
        VIEWS.downloads();
      } catch (error) { toast('Failed', error.message, 'error'); }
    });
  }
};

/* -------------------------------------------------------------- developer */
VIEWS.developer = async function () {
  const [tools, backups] = await Promise.all([api('/api/tools'), api('/api/backups')]);
  view().innerHTML = `
    <div class="grid cols-3" style="align-items:start">
      <div class="card span-2">
        <div class="card-head"><h2>Tool catalog</h2><span class="spacer"></span>
          <span class="pill">${tools.summary.count} tools</span></div>
        <p class="faint small">Every capability is a validated tool. Levels: 1 automatic · 2 confirmation · 3 explicit approval.</p>
        <div class="table-wrap" style="max-height:520px;overflow:auto;margin-top:10px"><table class="table">
          <thead><tr><th>Tool</th><th>Category</th><th>Level</th><th>Description</th></tr></thead>
          <tbody>${tools.tools.map((tool) => `
            <tr><td class="mono strong">${esc(tool.name)}</td><td>${esc(tool.category)}</td>
              <td><span class="pill ${tool.level === 1 ? 'ok' : tool.level === 2 ? 'warn' : 'bad'}">${tool.level}</span></td>
              <td class="small">${esc(tool.description)}</td></tr>`).join('')}</tbody>
        </table></div>
      </div>
      <div class="card">
        <div class="card-head"><h2>Backups</h2></div>
        <button class="btn primary" id="backup-now">Back up the database now</button>
        <div class="list" style="margin-top:10px;max-height:300px;overflow:auto">
          ${backups.backups.length ? backups.backups.map((backup) => `
            <div class="list-item"><span class="grow"><span class="title">${esc(backup.name)}</span>
              <div class="meta">${bytes(backup.size_bytes)} · ${esc(relative(new Date(backup.modified * 1000).toISOString()))}</div></span></div>`).join('')
            : '<div class="empty">No backups yet.</div>'}
        </div>
        <div class="mono-wrap" style="margin-top:8px">${esc(backups.directory)}</div>
        <div class="section-title" style="margin-top:14px">Allowed commands</div>
        <div class="mono-wrap">${(tools.catalog || []).map((item) => esc(item.command)).join(' · ')}</div>
        <div class="faint tiny" style="margin-top:8px">Command execution runs without a shell, with a timeout, and only when you enable it in Security Center.</div>
      </div>
    </div>`;
  $('#backup-now').addEventListener('click', async () => {
    try {
      const result = await api('/api/backup', { method: 'POST', body: {} });
      toast('Backup complete', `${result.path.split(/[\\/]/).pop()} · integrity ${result.integrity[0] ? 'OK' : result.integrity[1]}`, 'ok', 6000);
      VIEWS.developer();
    } catch (error) { toast('Backup failed', error.message, 'error'); }
  });
};

/* --------------------------------------------------------------- settings */
VIEWS.settings = async function () {
  const [settings, providers, cursorInfo] = await Promise.all([
    api('/api/settings'),
    api('/api/providers'),
    api('/api/providers/cursor').catch(() => null),
  ]);
  state.settings = settings;
  const config = settings.config;
  const credentials = providers.credentials;
  view().innerHTML = `
    <div class="tabs" id="settings-tabs">
      <button class="tab active" data-tab="ai">AI providers</button>
      <button class="tab" data-tab="general">General</button>
      <button class="tab" data-tab="voice">Voice</button>
      <button class="tab" data-tab="privacy">Privacy &amp; data</button>
      <button class="tab" data-tab="about">About</button>
    </div>
    <div id="settings-body"></div>`;

  const panels = {
    ai: `
      <div class="card">
        <div class="card-head"><h2>Cursor CLI</h2><span class="spacer"></span>
          <span class="pill ${cursorInfo?.available ? 'ok' : cursorInfo?.binary ? 'warn' : 'bad'}">${cursorInfo?.available ? 'ready' : cursorInfo?.binary ? 'needs sign-in' : 'not installed'}</span></div>
        <p class="faint small">RAY's default reasoning engine. The official Cursor CLI is called in read-only
          print mode (<span class="mono">--mode ask</span>); replies never edit files or run commands.</p>
        <div class="form-row" style="margin-top:10px">
          <label class="field"><span>CLI binary (blank = auto-detect)</span>
            <input type="text" id="cursor-binary" value="${esc(config.ai.providers.cursor?.binary || '')}" placeholder="cursor-agent" /></label>
          <label class="field"><span>Working folder (blank = RAY home)</span>
            <input type="text" id="cursor-workspace" value="${esc(config.ai.providers.cursor?.workspace || '')}" placeholder="optional" /></label>
          <label class="field"><span>Model (blank = account default)</span>
            <input type="text" id="cursor-model" value="${esc(config.ai.providers.cursor?.model || '')}" placeholder="auto" /></label>
        </div>
        <div class="row wrap" style="margin-top:10px">
          <button class="btn primary" id="save-cursor">Save</button>
          <button class="btn" data-act="credential-test" data-name="cursor">Test CLI</button>
          ${cursorInfo?.version ? `<span class="faint tiny">${esc(cursorInfo.version)}</span>` : ''}
        </div>
        <div class="mono-wrap" style="margin-top:10px">${cursorInfo?.binary ? esc(cursorInfo.binary) : 'not found on PATH'}${cursorInfo?.detail ? ` · ${esc(cursorInfo.detail)}` : ''}</div>
        <div class="section-title" style="margin-top:14px">Install &amp; sign in</div>
        <div class="mono-wrap">${esc(CURSOR_INSTALL.unix)}<br/>${esc(CURSOR_INSTALL.windows)}<br/>${esc(CURSOR_INSTALL.login)}</div>
        <div class="faint tiny" style="margin-top:6px">Docs:
          <a href="https://cursor.com/cli" target="_blank" rel="noreferrer noopener">cursor.com/cli</a></div>
      </div>
      <div class="card">
        <div class="card-head"><h2>Credentials</h2><span class="spacer"></span>
        <span class="pill">stored encrypted on this machine</span></div>
        <p class="faint small">Keys are encrypted with a local key file and never sent to the interface after saving.
          Cursor needs no key at all — use <span class="mono">cursor-agent login</span>, or paste a Cursor API key here if you prefer.
          Everything else can also come from environment variables.</p>
        <div class="col" style="margin-top:12px">
          ${credentials.map((credential) => `
            <div class="card tight">
              <div class="credential-row">
                <div>
                  <strong style="text-transform:capitalize">${esc(credential.name)}</strong>${credential.name === 'cursor' ? ' <span class="faint tiny">(optional API key)</span>' : ''}
                  <div class="faint tiny">
                    ${credential.configured
                      ? `configured (${esc(credential.source)})${credential.last_test_detail ? ` · ${esc(credential.last_test_detail)}` : ''}`
                      : 'not configured'}
                  </div>
                </div>
                <input type="password" id="cred-${credential.name}" placeholder="${credential.name === 'cursor' ? 'optional — login works too' : 'paste API key'}" />
                <div class="row">
                  <button class="btn sm" data-act="credential-save" data-name="${credential.name}">Save &amp; test</button>
                  <button class="btn sm ghost" data-act="credential-test" data-name="${credential.name}">Test</button>
                  ${credential.configured ? `<button class="btn sm ghost danger" data-act="credential-delete" data-name="${credential.name}">Remove</button>` : ''}
                </div>
              </div>
            </div>`).join('')}
        </div>
      </div>
      <div class="card">
        <div class="card-head"><h2>Models &amp; routing</h2></div>
        <div class="form-row">
          ${['cursor', 'gemini', 'grok', 'fish'].map((name) => `
            <label class="field"><span>${name === 'cursor' ? 'cursor model (blank = auto)' : `${name} model`}</span>
              <input type="text" id="model-${name}" value="${esc(config.ai.providers[name]?.model || '')}" />
            </label>`).join('')}
          <label class="field"><span>Fish Audio voice id</span>
            <input type="text" id="model-fish-voice" value="${esc(config.ai.providers.fish?.reference_id || '')}" placeholder="reference_id" /></label>
        </div>
        <div class="row" style="margin-top:10px">
          <button class="btn primary" id="save-models">Save models</button>
          <button class="btn" id="test-all">Test all providers</button>
        </div>
        <div class="card" style="margin-top:16px">
          <div class="card-head"><h2>Where to get keys</h2></div>
          <div class="list">
            ${PROVIDERS.map((provider) => `
              <div class="list-item">
                <span class="grow"><span class="title">${esc(provider.label)}</span>
                  <div class="meta">${esc(provider.role)}</div></span>
                <a class="btn sm ghost" href="${provider.keyUrl}" target="_blank" rel="noreferrer noopener">Get key</a>
              </div>`).join('')}
          </div>
          <div class="faint tiny" style="margin-top:8px">Keys are encrypted with a key file in RAY's data folder.
            You can also set ${esc(PROVIDERS.map((provider) => provider.env).join(', '))} before launch.</div>
        </div>
        <div class="section-title" style="margin-top:16px">Routing</div>
        <table class="table">
          <thead><tr><th>Task</th><th>Preference</th></tr></thead>
          <tbody>${Object.entries(providers.providers.routes).map(([task, chain]) => `
            <tr><td class="strong">${esc(task)}</td><td>${chain.map(esc).join(' → ')}</td></tr>`).join('')}</tbody>
        </table>
        <div class="faint tiny" style="margin-top:8px">RAY monitors daily call budgets and falls back when a provider fails. Nothing is sent to a provider that is not needed.</div>
      </div>`,
    general: `
      <div class="card">
        <div class="card-head"><h2>Identity</h2></div>
        <div class="form-row">
          <label class="field"><span>Your name</span><input type="text" id="set-name" value="${esc(config.app.user_name || '')}" /></label>
          <label class="field"><span>Interface port</span><input type="number" id="set-port" value="${config.ui.port}" /></label>
          <label class="field"><span>Interface host</span><input type="text" id="set-host" value="${esc(config.ui.host)}" /></label>
          <label class="field"><span>Downloads folder</span><input type="text" id="set-downloads" value="${esc(config.paths.downloads || '')}" placeholder="default" /></label>
          <label class="field"><span>Music folder</span><input type="text" id="set-music" value="${esc(config.paths.music || '')}" placeholder="default" /></label>
        </div>
        <div class="row" style="margin-top:12px"><button class="btn primary" id="save-general">Save</button>
          <span class="faint tiny">Host changes apply after restarting RAY.</span></div>
      </div>
      <div class="card">
        <div class="card-head"><h2>Automation engine</h2></div>
        <div class="form-row">
          <label class="switch"><input type="checkbox" id="set-auto" ${config.automation.enabled ? 'checked' : ''}><span class="track"></span>Run automations</label>
          <label class="field"><span>Tick seconds</span><input type="number" id="set-tick" value="${config.automation.tick_seconds}" min="5" max="300" /></label>
          <label class="switch"><input type="checkbox" id="set-dry" ${config.automation.dry_run ? 'checked' : ''}><span class="track"></span>Dry run (log only)</label>
        </div>
        <div class="row" style="margin-top:12px"><button class="btn primary" id="save-automation">Save</button></div>
      </div>`,
    voice: `
      <div class="card">
        <div class="card-head"><h2>Voice</h2></div>
        <div class="col">
          <label class="switch"><input type="checkbox" id="set-voice" ${config.voice.enabled ? 'checked' : ''}><span class="track"></span>Enable voice output</label>
          <label class="field"><span>Voice id</span><input type="text" id="set-voice-id" value="${esc(config.voice.voice_id || '')}" /></label>
          <label class="field"><span>Speed</span><input type="number" step="0.05" min="0.5" max="2" id="set-voice-speed" value="${config.voice.speed}" /></label>
          <label class="field"><span>Volume</span><input type="number" step="0.05" min="0" max="1" id="set-voice-volume" value="${config.voice.volume}" /></label>
          <label class="switch"><input type="checkbox" id="set-voice-auto" ${config.voice.auto_speak ? 'checked' : ''}><span class="track"></span>Speak replies automatically</label>
          <label class="switch"><input type="checkbox" id="set-voice-wake" ${config.voice.wake_word_enabled ? 'checked' : ''}><span class="track"></span>Wake word while the Voice page is open</label>
          <button class="btn primary" id="save-voice">Save</button>
        </div>
      </div>`,
    privacy: `
      <div class="card">
        <div class="card-head"><h2>Privacy</h2></div>
        <div class="col">
          <label class="switch"><input type="checkbox" id="set-cloud-docs" ${config.privacy.send_documents_to_cloud ? 'checked' : ''}><span class="track"></span>Allow sending document text to the AI provider for summarisation</label>
          <label class="switch"><input type="checkbox" id="set-log-prompts" ${config.privacy.log_prompts ? 'checked' : ''}><span class="track"></span>Log prompts in the local log file</label>
          <label class="switch"><input type="checkbox" id="set-memory" ${config.memory.enabled ? 'checked' : ''}><span class="track"></span>Remember things I ask you to remember</label>
          <label class="field"><span>Memory retention (days, 0 = keep forever)</span>
            <input type="number" id="set-retention" value="${config.memory.retention_days}" min="0" /></label>
          <button class="btn primary" id="save-privacy">Save</button>
        </div>
      </div>
      <div class="card">
        <div class="card-head"><h2>Data</h2></div>
        <div class="row wrap">
          <button class="btn" id="export-memory">Export memories</button>
          <button class="btn" id="backup-db">Back up database</button>
          <button class="btn ghost" id="show-logs">View logs</button>
        </div>
        <div class="mono-wrap" style="margin-top:10px">Home: ${esc(settings.paths.home)}<br/>Database: ${esc(settings.paths.database)}<br/>Logs: ${esc(settings.paths.logs)}</div>
        <div id="log-box" style="margin-top:10px"></div>
      </div>`,
    about: `
      <div class="card">
        <div class="card-head"><h2>RAY ${esc(settings.version)}</h2></div>
        <p class="muted small">Real-time Autonomous Yourself - a local-first personal AI operating system.</p>
        <table class="table" style="margin-top:12px">
          <tr><td>Engine</td><td class="strong">Python ${esc(state.status?.platform?.python || '')}</td></tr>
          <tr><td>Machine</td><td class="strong">${esc(state.status?.platform?.platform || '')} ${esc(state.status?.platform?.release || '')}</td></tr>
          <tr><td>Tools</td><td class="strong">${state.status?.tools?.count || 0}</td></tr>
          <tr><td>Database</td><td class="strong">${bytes(state.status?.database?.size_bytes)} · integrity ${state.status?.database?.integrity ? 'ok' : 'check'}</td></tr>
          <tr><td>Providers</td><td class="strong">${Object.entries(state.status?.providers || {}).map(([name, health]) => `${name}:${health.available ? 'ok' : 'off'}`).join(' · ')}</td></tr>
        </table>
        <div class="banner info" style="margin-top:12px">
          RAY reports only what it can verify. Features that need an API key stay unavailable rather than pretending to work.
        </div>
        <div class="row" style="margin-top:14px">
          <button class="btn" id="open-backups">Open backup folder</button>
          <button class="btn danger" id="shutdown-app">Stop RAY</button>
        </div>
      </div>`,
  };

  const bindPanel = (tab) => {
    const body = $('#settings-body');
    body.innerHTML = panels[tab];
    $$('#settings-tabs .tab').forEach((node) => node.classList.toggle('active', node.dataset.tab === tab));

    const saveValues = async (values) => {
      try {
        await api('/api/settings', { method: 'POST', body: { values } });
        toast('Saved', '', 'ok', 1600);
        state.settings = await api('/api/settings');
      } catch (error) { toast('Could not save', error.message, 'error'); }
    };

    if (tab === 'ai') {
      const cursorSave = $('#save-cursor');
      if (cursorSave) {
        cursorSave.addEventListener('click', async () => {
          try {
            await api('/api/providers/cursor', {
              method: 'POST',
              body: {
                binary: $('#cursor-binary').value.trim(),
                workspace: $('#cursor-workspace').value.trim(),
                model: $('#cursor-model').value.trim(),
              },
            });
            toast('Cursor settings saved', '', 'ok');
            await refreshStatus();
            await VIEWS.settings();
          } catch (error) { toast('Could not save', error.message, 'error'); }
        });
      }
      $('#save-models').addEventListener('click', async () => {
        for (const name of ['cursor', 'gemini', 'grok', 'fish']) {
          const value = $(`#model-${name}`).value.trim();
          if (value) await api('/api/providers/model', { method: 'POST', body: { name, model: value } }).catch(() => {});
        }
        const voice = $('#model-fish-voice').value.trim();
        await api('/api/providers/model', { method: 'POST', body: { name: 'fish', reference_id: voice } }).catch(() => {});
        await api('/api/settings', { method: 'POST', body: { values: { 'voice.voice_id': voice } } });
        toast('Models saved', '', 'ok');
      });
      $('#test-all').addEventListener('click', async () => {
        for (const name of ['cursor', 'gemini', 'fish']) {
          try {
            const result = await api('/api/providers/test', { method: 'POST', body: { name } });
            toast(`${name}: ${result.available ? 'connected' : 'not available'}`, result.detail, result.available ? 'ok' : 'warn', 6000);
          } catch (error) { toast(`${name} test failed`, error.message, 'error'); }
        }
      });
    }
    if (tab === 'general') {
      $('#save-general').addEventListener('click', () => saveValues({
        'app.user_name': $('#set-name').value,
        'ui.port': Number($('#set-port').value),
        'ui.host': $('#set-host').value.trim(),
        'paths.downloads': $('#set-downloads').value.trim(),
        'paths.music': $('#set-music').value.trim(),
      }));
      $('#save-automation').addEventListener('click', () => saveValues({
        'automation.enabled': $('#set-auto').checked,
        'automation.tick_seconds': Number($('#set-tick').value),
        'automation.dry_run': $('#set-dry').checked,
      }));
    }
    if (tab === 'voice') {
      $('#save-voice').addEventListener('click', () => saveValues({
        'voice.enabled': $('#set-voice').checked,
        'voice.voice_id': $('#set-voice-id').value.trim(),
        'voice.speed': Number($('#set-voice-speed').value),
        'voice.volume': Number($('#set-voice-volume').value),
        'voice.auto_speak': $('#set-voice-auto').checked,
        'voice.wake_word_enabled': $('#set-voice-wake').checked,
      }));
    }
    if (tab === 'privacy') {
      $('#save-privacy').addEventListener('click', () => saveValues({
        'privacy.send_documents_to_cloud': $('#set-cloud-docs').checked,
        'privacy.log_prompts': $('#set-log-prompts').checked,
        'memory.enabled': $('#set-memory').checked,
        'memory.retention_days': Number($('#set-retention').value),
      }));
      $('#export-memory').addEventListener('click', async () => {
        try {
          const result = await api('/api/memory/export', { method: 'POST', body: {} });
          toast('Exported', result.path, 'ok');
        } catch (error) { toast('Export failed', error.message, 'error'); }
      });
      $('#backup-db').addEventListener('click', async () => {
        try {
          const result = await api('/api/backup', { method: 'POST', body: {} });
          toast('Backup created', result.path, 'ok');
        } catch (error) { toast('Backup failed', error.message, 'error'); }
      });
      $('#show-logs').addEventListener('click', async () => {
        try {
          const logs = await api('/api/logs?lines=80');
          $('#log-box').innerHTML = `<div class="approval-args">${esc(logs.lines.join('\n'))}</div>`;
        } catch (error) { toast('Logs', error.message, 'error'); }
      });
    }
    if (tab === 'about') {
      $('#open-backups').addEventListener('click', async () => {
        const backups = await api('/api/backups');
        await api('/api/files/open', { method: 'POST', body: { path: backups.directory } }).catch((error) => toast('Could not open', error.message, 'error'));
      });
      $('#shutdown-app').addEventListener('click', async () => {
        const ok = await confirmAction('Stop RAY', 'The interface will stop responding. Restart with "python main.py".', 'Stop RAY');
        if (!ok) return;
        await api('/api/shutdown', { method: 'POST', body: {} }).catch(() => {});
        toast('RAY is stopping', 'You can close this window.', 'warn', 8000);
      });
    }
  };

  $('#settings-tabs').addEventListener('click', (event) => {
    const tab = event.target.closest('.tab');
    if (tab) bindPanel(tab.dataset.tab);
  });
  bindPanel('ai');
};

/* --------------------------------------------------------------- actions */
const ACTIONS = {
  'close-modal': () => closeModal(),
  go: (dataset) => { closeModal(); go(dataset.section); },
  palette: () => openPalette(),
  'key-help': () => openKeyHelp(),
  'cursor-help': () => openCursorHelp(),
  'home-chat-tab': (dataset) => {
    state.homeChat = state.homeChat || { messages: [] };
    state.homeChat.agent = dataset.agent || 'assistant';
    go('home');
  },
  'security-scan': async () => {
    const box = $('#scan-results');
    if (box) box.innerHTML = '<div class="scan-check"><span class="dot" style="background:var(--cyan)"></span><div class="grow">Running checks…</div></div>';
    try {
      const result = await api('/api/security/scan', { method: 'POST', body: {} });
      const indicator = $('#scan-indicator');
      const tone = !result.ok ? 'bad' : result.warnings ? 'warn' : 'ok';
      if (indicator) {
        indicator.className = `pill ${tone}`;
        indicator.textContent = !result.ok
          ? `${result.total - result.passed} issue(s)`
          : result.warnings ? `${result.warnings} warning(s)` : 'all clear';
      }
      if (box) {
        box.innerHTML = result.checks.map((check) => `
          <div class="scan-check ${check.ok ? (check.warn ? 'warn' : '') : 'bad'}">
            <span class="dot"></span>
            <div class="grow"><div>${esc(check.name)}</div><div class="meta">${esc(check.detail)}</div></div>
          </div>`).join('');
      }
      toast(
        !result.ok ? 'Security check found issues' : result.warnings ? 'Check finished with warnings' : 'Security check passed',
        `${result.passed}/${result.total} checks passed${result.warnings ? `, ${result.warnings} need review` : ''}`,
        tone === 'ok' ? 'ok' : 'warn',
        6000,
      );
    } catch (error) {
      toast('Scan failed', error.message, 'error');
      if (box) box.innerHTML = `<div class="scan-check bad"><span class="dot"></span><div class="grow">${esc(error.message)}</div></div>`;
    }
  },
  'browser-open': (dataset) => api('/api/browser/open', { method: 'POST', body: { url: dataset.url } })
    .then(() => toast('Opened in your browser', dataset.url, 'ok'))
    .catch((error) => toast('Could not open', error.message, 'error')),

  'calendar-day': (dataset) => {
    const [year, month] = String(dataset.date).split('-').map(Number);
    state.calendarMonth = { year, month };
    go('calendar');
  },
  quick: (dataset) => runCommand(dataset.cmd),
  'open-file': (dataset) => api('/api/files/open', { method: 'POST', body: { path: dataset.path } })
    .then(() => toast('Opened', dataset.path.split(/[\\/]/).pop(), 'ok'))
    .catch((error) => toast('Could not open', error.message, 'error')),
  'reveal-file': (dataset) => api('/api/files/reveal', { method: 'POST', body: { path: dataset.path } })
    .catch((error) => toast('Could not reveal', error.message, 'error')),
  'browse-files': (dataset) => {
    go('files');
    setTimeout(async () => {
      const next = await api(`/api/files?path=${encodeURIComponent(dataset.path)}`);
      const input = $('#path-input');
      if (input) input.value = next.path;
      const list = $('#file-list');
      if (list) list.innerHTML = fileList(next);
    }, 200);
  },
  'launch-app': (dataset) => api('/api/apps/launch', { method: 'POST', body: { target: dataset.target } })
    .then((result) => toast('Launched', result.launched, 'ok'))
    .catch(async (error) => {
      if (error.status === 403) {
        const ok = await confirmAction('Approval needed', error.message, 'Approve');
        if (ok) toast('Approve it in the bell menu', 'Level 3 actions need the approval dialog.', 'warn');
      } else toast('Could not launch', error.message, 'error');
    }),
  'close-app': (dataset) => api('/api/apps/close', { method: 'POST', body: { name: dataset.name } })
    .then(() => toast('Closed', dataset.name, 'ok'))
    .catch((error) => toast('Could not close', error.message, 'error')),
  'focus-app': (dataset) => toast('Window switching', 'Use Alt+Tab; RAY avoids blind window activation.', 'info', 5000),
  screenshot: () => api('/api/system/screenshot', { method: 'POST', body: {} })
    .then((result) => toast('Screenshot saved', result.path, 'ok'))
    .catch((error) => toast('Screenshot failed', error.message, 'error', 7000)),
  volume: (dataset) => api('/api/system/volume', { method: 'POST', body: { direction: dataset.direction } })
    .catch((error) => toast('Volume', error.message, 'warn')),
  panel: (dataset) => api('/api/system/panel', { method: 'POST', body: { panel: dataset.panel } })
    .then(() => toast('Opened', dataset.panel, 'ok'))
    .catch((error) => toast('Could not open', error.message, 'warn')),
  power: async (dataset) => {
    const action = dataset.action;
    const serious = action === 'restart' || action === 'shutdown';
    if (serious) {
      const ok = await confirmAction(`Really ${action}?`, 'Save your work first. RAY will ask for explicit approval.', `Yes, ${action}`);
      if (!ok) return;
    }
    try {
      const result = await api('/api/system/power', { method: 'POST', body: { action } });
      toast(`${action} requested`, JSON.stringify(result), 'ok');
      loadApprovals();
    } catch (error) {
      toast('Power action', error.message, error.status === 403 ? 'warn' : 'error', 7000);
    }
  },
  'complete-task': (dataset) => api(`/api/tasks/${dataset.id}/complete`, { method: 'POST', body: {} })
    .then(() => { toast('Task completed', '', 'ok'); go(state.section); })
    .catch((error) => toast('Could not complete', error.message, 'error')),
  'delete-task': async (dataset) => {
    if (!await confirmAction('Delete task?', 'This removes the task permanently.', 'Delete')) return;
    await api(`/api/tasks/${dataset.id}`, { method: 'DELETE' }).then(() => go(state.section))
      .catch((error) => toast('Could not delete', error.message, 'error'));
  },
  'delete-note': async (dataset) => {
    if (!await confirmAction('Delete note?', 'This note will be removed.', 'Delete')) return;
    await api(`/api/notes/${dataset.id}`, { method: 'DELETE' }).then(() => VIEWS.notes())
      .catch((error) => toast('Could not delete', error.message, 'error'));
  },
  'delete-reminder': (dataset) => api(`/api/reminders/${dataset.id}`, { method: 'DELETE' })
    .then(() => VIEWS.reminders()).catch((error) => toast('Could not delete', error.message, 'error')),
  snooze: (dataset) => api(`/api/reminders/${dataset.id}/snooze`, { method: 'POST', body: { minutes: 10 } })
    .then(() => toast('Snoozed', '10 minutes', 'ok')).catch((error) => toast('Could not snooze', error.message, 'error')),
  'delete-event': (dataset) => api(`/api/calendar/${dataset.id}`, { method: 'DELETE' })
    .then(() => VIEWS.calendar()).catch((error) => toast('Could not delete', error.message, 'error')),
  'calendar-month': (dataset) => {
    state.calendarMonth = { year: Number(dataset.year), month: Number(dataset.month) };
    go('calendar');
  },
  'workflow-run': (dataset) => api(`/api/automation/${dataset.id}/run`, { method: 'POST', body: {} })
    .then((result) => {
      toast(`Workflow ${result.status}`, result.workflow, result.ok ? 'ok' : 'warn', 6000);
      VIEWS.automation();
    }).catch((error) => toast('Workflow failed', error.message, 'error')),
  'workflow-toggle': (dataset) => api(`/api/automation/${dataset.id}/toggle`, {
    method: 'POST', body: { enabled: dataset.enabled === '1' },
  }).then(() => VIEWS.automation()).catch((error) => toast('Could not toggle', error.message, 'error')),
  'workflow-delete': async (dataset) => {
    if (!await confirmAction('Delete workflow?', 'Its history will be removed too.', 'Delete')) return;
    await api(`/api/automation/${dataset.id}`, { method: 'DELETE' }).then(() => VIEWS.automation())
      .catch((error) => toast('Could not delete', error.message, 'error'));
  },
  'play-media': (dataset) => api('/api/media/play', { method: 'POST', body: { query: dataset.query } })
    .then((result) => toast('Playing', result.title || result.playlist || dataset.query, 'ok'))
    .catch((error) => toast('Could not play', error.message, 'error', 7000)),
  'play-media-input': (dataset) => api('/api/media/play', {
    method: 'POST', body: { query: ($(`#${dataset.input}`)?.value || '').trim() },
  }).then((result) => toast('Playing', result.title || dataset.query, 'ok'))
    .catch((error) => toast('Could not play', error.message, 'error')),
  'open-url': (dataset) => api('/api/browser/open', { method: 'POST', body: { url: dataset.url } })
    .then(() => toast('Opened in browser', dataset.url, 'ok')).catch((error) => toast('Could not open', error.message, 'error')),
  'read-page': (dataset) => readPage(dataset.url),
  'use-voice': async (dataset) => {
    await api('/api/settings', { method: 'POST', body: { values: { 'voice.voice_id': dataset.id } } });
    const input = $('#voice-id');
    if (input) input.value = dataset.id;
    toast('Voice selected', dataset.id, 'ok');
  },
  'credential-save': async (dataset) => {
    const input = $(`#cred-${dataset.name}`);
    const value = input?.value.trim();
    if (!value) { toast('Paste a key first', '', 'warn'); return; }
    try {
      const result = await api('/api/credentials', { method: 'POST', body: { name: dataset.name, value } });
      input.value = '';
      toast(`${dataset.name} saved`, result.test.detail, result.test.available ? 'ok' : 'warn', 7000);
      VIEWS.settings();
      refreshStatus();
    } catch (error) { toast('Could not save', error.message, 'error', 7000); }
  },
  'credential-test': async (dataset) => {
    try {
      const result = await api('/api/providers/test', { method: 'POST', body: { name: dataset.name } });
      const ok = result.ok !== false;
      toast(`${dataset.name} test`, result.detail || (ok ? `reachable${result.latency_ms ? ` in ${result.latency_ms} ms` : ''}` : 'failed'), ok ? 'ok' : 'warn', 6000);
    } catch (error) {
      toast(`${dataset.name} test failed`, error.message, 'error', 6000);
    }
    await refreshStatus();
    if (state.section === 'home' || state.section === 'settings') go(state.section);
  },
  'credential-delete': async (dataset) => {
    if (!await confirmAction(`Remove ${dataset.name} key?`, 'Without a key this provider stays disabled.', 'Remove')) return;
    await api('/api/credentials', { method: 'DELETE', body: { name: dataset.name } })
      .then(() => { toast('Removed', dataset.name, 'ok'); VIEWS.settings(); refreshStatus(); })
      .catch((error) => toast('Could not remove', error.message, 'error'));
  },
  approval: (dataset) => api('/api/approvals', {
    method: 'POST', body: { approval_id: Number(dataset.id), approved: dataset.approved === '1' },
  }).then(() => { toast('Decision recorded', '', 'ok'); loadApprovals(); VIEWS.security(); })
    .catch((error) => toast('Could not record', error.message, 'error')),
  'index-remove': async (dataset) => {
    if (!await confirmAction('Stop watching this folder?', 'Its indexed entries will be removed.', 'Remove')) return;
    await api('/api/knowledge/roots', { method: 'POST', body: { action: 'remove', path: dataset.path } })
      .then(() => VIEWS.knowledge()).catch((error) => toast('Could not remove', error.message, 'error'));
  },
  'agent-run': (dataset) => {
    openModal(`<h3>Run ${esc(dataset.agent)} agent</h3>
      <p class="muted small">Describe the task. The agent uses only its own tools.</p>
      <textarea id="agent-task" placeholder="e.g. summarise the newest PDF in Downloads"></textarea>
      <div class="modal-actions"><button class="btn ghost" data-act="close-modal">Cancel</button>
        <button class="btn primary" id="agent-go">Run</button></div>`);
    $('#agent-go').addEventListener('click', async () => {
      const text = $('#agent-task').value.trim();
      closeModal();
      const result = await api('/api/command', { method: 'POST', body: { text, agent: dataset.agent } });
      renderCommandResult(result);
    });
  },
};

document.addEventListener('click', (event) => {
  const element = event.target.closest('[data-act]');
  if (!element) return;
  const handler = ACTIONS[element.dataset.act];
  if (!handler) return;
  event.preventDefault();
  Promise.resolve(handler(element.dataset, element)).catch((error) => toast('Action failed', error.message, 'error'));
});

function openApprovalDialog() {
  const pending = state.approvals[0];
  if (!pending) {
    openModal(`<h3>No approvals waiting</h3><p class="muted small">RAY is not waiting on you.</p>
      <div class="modal-actions"><button class="btn" data-act="close-modal">Close</button></div>`);
    return;
  }
  openModal(`
    <h3>Approval needed</h3>
    <p class="muted small">${esc(pending.reason || 'This action changes something on your computer.')}</p>
    <div class="row" style="margin:10px 0"><span class="pill warn">level ${pending.level}</span>
      <span class="mono">${esc(pending.tool)}</span></div>
    <div class="approval-args">${esc(JSON.stringify(pending.args, null, 2))}</div>
    <div class="modal-actions">
      <button class="btn danger" id="ap-deny">Deny</button>
      <button class="btn primary" id="ap-approve">Approve</button>
    </div>`);
  const decide = async (approved) => {
    await api('/api/approvals', { method: 'POST', body: { approval_id: pending.id, approved } })
      .catch((error) => toast('Could not record', error.message, 'error'));
    closeModal();
    loadApprovals();
    if (state.section === 'security') VIEWS.security();
  };
  $('#ap-approve').addEventListener('click', () => decide(true));
  $('#ap-deny').addEventListener('click', () => decide(false));
}

/* -------------------------------------------------------------- bootstrap */
async function refreshStatus() {
  try {
    state.status = await api('/api/status');
    const providers = state.status.providers || {};
    const online = Object.values(providers).some((provider) => provider.available);
    const configured = Object.values(providers).some((provider) => provider.configured);
    $('#dot-ai').className = `dot ${online ? 'ok' : configured ? 'warn' : 'bad'}`;
    $('#label-ai').textContent = online ? 'AI: connected' : configured ? 'AI: unreachable' : 'AI: add a key';
    const index = state.status.index || {};
    $('#dot-index').className = `dot ${index.files ? 'ok' : 'warn'}`;
    $('#label-index').textContent = `Index: ${num(index.files)} files`;
    state.voiceEnabled = state.status.voice_enabled;
  } catch (error) {
    $('#dot-ai').className = 'dot bad';
    $('#label-ai').textContent = 'Engine unreachable';
  }
}

async function boot() {
  renderNav();
  document.addEventListener('click', (event) => {
    const navItem = event.target.closest('[data-section]');
    if (navItem) go(navItem.dataset.section);
  });
  $('#search-trigger').addEventListener('click', openPalette);
  $('#palette-overlay').addEventListener('click', (event) => { if (event.target.id === 'palette-overlay') closePalette(); });
  $('#modal-overlay').addEventListener('click', (event) => { if (event.target.id === 'modal-overlay') closeModal(); });
  $('#palette-input').addEventListener('input', (event) => renderPalette(event.target.value));
  $('#palette-input').addEventListener('keydown', (event) => {
    if (event.key === 'Enter') {
      const text = event.target.value.trim();
      if (text) runCommand(text);
    }
  });
  $('#palette-body').addEventListener('click', (event) => {
    const suggestion = event.target.closest('[data-suggest]');
    if (suggestion) runCommand(suggestion.dataset.suggest);
  });
  $('#btn-approvals').addEventListener('click', openApprovalDialog);
  $('#btn-screenshot').addEventListener('click', () => ACTIONS.screenshot());
  $('#btn-lock').addEventListener('click', async () => {
    const policy = state.status?.security || {};
    if (policy.emergency_stop) {
      await api('/api/security/emergency', { method: 'POST', body: { active: false } });
      toast('Emergency stop released', '', 'ok');
    } else {
      const ok = await confirmAction('Emergency stop', 'RAY stops all actions that change your computer until you release it.', 'Engage');
      if (!ok) return;
      await api('/api/security/emergency', { method: 'POST', body: { active: true } });
      toast('Emergency stop engaged', 'Only read-only tools will work.', 'warn', 6000);
    }
    refreshStatus();
    loadApprovals();
  });
  $('#btn-settings').addEventListener('click', () => go('settings'));
  const voiceOrb = $('#btn-voice-orb');
  if (voiceOrb) voiceOrb.addEventListener('click', () => go('voice'));
  startClock();

  document.addEventListener('keydown', (event) => {
    if (event.key === 'Escape') { closePalette(); closeModal(); }
    if ((event.ctrlKey || event.metaKey) && event.code === 'Space') { event.preventDefault(); openPalette(); }
    if ((event.ctrlKey || event.metaKey) && event.key.toLowerCase() === 'k') { event.preventDefault(); openPalette(); }
  });

  const section = (location.hash || '').replace('#/', '') || 'home';
  await refreshStatus().catch(() => {});
  await loadApprovals();
  go(VIEWS[section] ? section : 'home');
  pollEvents();
  setInterval(refreshStatus, 20000);
  setInterval(loadApprovals, 6000);
  window.addEventListener('hashchange', () => {
    const target = (location.hash || '').replace('#/', '') || 'home';
    if (target !== state.section && VIEWS[target]) go(target);
  });
}

window.closeModal = closeModal;
window.openPalette = openPalette;
document.addEventListener('DOMContentLoaded', boot);
