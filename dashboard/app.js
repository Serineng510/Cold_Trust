/* ColdTrust operations dashboard. Vanilla JS, no build step; served by the FastAPI backend at "/".
   Data: GET /api/state, /api/audit, /api/readings, /api/config, /api/epcis(+verify); live updates over /ws. */
'use strict';

/* ============================== helpers ============================== */
const $ = s => document.querySelector(s), $$ = s => [...document.querySelectorAll(s)];
const esc = s => String(s ?? '').replace(/[&<>"']/g, c => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]));
const fmt = n => Number(n).toLocaleString('en-US');
const fmtTime = ts => ts ? new Date(ts * 1000).toLocaleTimeString([], { hour12: false }) : '-';
const fmtDateTime = ts => ts ? new Date(ts * 1000).toLocaleString([], { hour12: false }) : '-';
const LABEL = { NORMAL: 'Normal', UNDER_REVIEW: 'Under review', QUARANTINED: 'Quarantined - do not accept' };
const SHORT = { NORMAL: 'Normal', UNDER_REVIEW: 'Review', QUARANTINED: 'Quarantined' };
const TONE = { NORMAL: 'ok', UNDER_REVIEW: 'warn', QUARANTINED: 'crit' };
const PRIO = { Critical: 'crit', High: 'warn', Normal: 'info', Low: 'mut' };
const SEV_TONE = { info: 'info', warning: 'warn', critical: 'crit', emergency: 'crit' };
const ORDER = ['NORMAL', 'UNDER_REVIEW', 'QUARANTINED'];
const HUMAN_KINDS = new Set(['ALERT_ACKNOWLEDGED', 'REPORT_APPROVED', 'REASSIGN_REQUESTED', 'VEHICLE_ACTION', 'SUPERVISOR_OVERRIDE', 'DEMO_MODE', 'APPROVAL_PROPOSED', 'APPROVAL_DECIDED', 'REPORT_GENERATED']);
const TRUCKS = ['TX-2210-HX (idle, 61% full)', 'TX-7745-HX (loading, 30% full)'];
const pill = (tone, text, dot = true) => `<span class="pill t-${tone}">${dot ? '<i></i>' : ''}${esc(text)}</span>`;
const store = {
  get(k, d) { try { const v = localStorage.getItem('ct-' + k); return v === null ? d : JSON.parse(v); } catch { return d; } },
  set(k, v) { try { localStorage.setItem('ct-' + k, JSON.stringify(v)); } catch { /* private mode */ } },
};

function polar(cx, cy, r, deg) { const a = (deg - 90) * Math.PI / 180; return [cx + r * Math.cos(a), cy + r * Math.sin(a)]; }
function arc(cx, cy, r, a0, a1) {
  const [x0, y0] = polar(cx, cy, r, a0), [x1, y1] = polar(cx, cy, r, a1);
  return `M${x0.toFixed(1)} ${y0.toFixed(1)}A${r} ${r} 0 ${a1 - a0 > 180 ? 1 : 0} 1 ${x1.toFixed(1)} ${y1.toFixed(1)}`;
}
/* 240-degree gauge, -120..+120 */
function gauge({ v, min, max, unit, label, color, band, mark, size = 150, sub, digits = 0 }) {
  const known = v !== null && v !== undefined && !Number.isNaN(+v);
  const A0 = -120, A1 = 120, f = x => A0 + (A1 - A0) * Math.max(0, Math.min(1, (x - min) / (max - min)));
  const cx = 75, cy = 78, r = 56;
  let s = `<svg viewBox="0 0 150 128" width="100%" style="max-width:${size}px" role="img" aria-label="${esc(label)} ${known ? (+v).toFixed(digits) : 'no data'} ${esc(unit)}">`;
  s += `<path d="${arc(cx, cy, r, A0, A1)}" stroke="var(--line2)" stroke-width="10" fill="none" stroke-linecap="round"/>`;
  if (band) s += `<path d="${arc(cx, cy, r, f(band[0]), f(band[1]))}" stroke="color-mix(in srgb,var(--ok) 55%,transparent)" stroke-width="10" fill="none"/>`;
  if (known) s += `<path d="${arc(cx, cy, r, A0, Math.max(A0 + 1, f(+v)))}" stroke="${color}" stroke-width="10" fill="none" stroke-linecap="round"/>`;
  if (mark != null) { const [x0, y0] = polar(cx, cy, r - 9, f(mark)), [x1, y1] = polar(cx, cy, r + 9, f(mark)); s += `<line x1="${x0}" y1="${y0}" x2="${x1}" y2="${y1}" stroke="var(--fg)" stroke-width="2"/>`; }
  s += `<text x="${cx}" y="${cy + 4}" text-anchor="middle" font-size="26" font-weight="600" fill="var(--fg)" font-family="var(--ui)">${known ? (+v).toFixed(digits) : '–'}</text>`;
  s += `<text x="${cx}" y="${cy + 21}" text-anchor="middle" font-size="11" fill="var(--mut)" font-family="var(--ui)">${esc(unit)}</text>`;
  s += `<text x="${cx}" y="122" text-anchor="middle" font-size="11" fill="var(--mut)" font-family="var(--ui)">${esc(label)}${sub ? ' · ' + esc(sub) : ''}</text></svg>`;
  return s;
}
function lineChart(points, { w = 520, h = 160, band = null, labels = null, emptyText = 'No readings yet' } = {}) {
  if (!points.length) return `<div class="empty">${esc(emptyText)}</div>`;
  const vals = points.map(p => p.v), lo = Math.floor(Math.min(...vals, band ? band[0] : Infinity) - 1), hi = Math.ceil(Math.max(...vals, band ? band[1] : -Infinity) + 1);
  const t0 = points[0].t, t1 = points[points.length - 1].t || t0 + 1;
  const pl = 34, pr = 10, pt = 10, pb = 22;
  const X = t => pl + (w - pl - pr) * (t1 === t0 ? 1 : (t - t0) / (t1 - t0)), Y = v => pt + (h - pt - pb) * (1 - (v - lo) / (hi - lo || 1));
  let s = `<svg viewBox="0 0 ${w} ${h}" width="100%" role="img" aria-label="Temperature trend">`;
  if (band) s += `<rect x="${pl}" y="${Y(band[1])}" width="${w - pl - pr}" height="${Math.max(0, Y(band[0]) - Y(band[1]))}" fill="color-mix(in srgb,var(--ok) 11%,transparent)"/>`;
  const step = Math.max(1, Math.ceil((hi - lo) / 4));
  for (let g = lo; g <= hi; g += step) s += `<line x1="${pl}" x2="${w - pr}" y1="${Y(g)}" y2="${Y(g)}" stroke="var(--line)" stroke-dasharray="3 4"/><text x="${pl - 6}" y="${Y(g) + 4}" text-anchor="end" font-size="10.5" fill="var(--dim)">${g}</text>`;
  const pts = points.map(p => `${X(p.t).toFixed(1)},${Y(p.v).toFixed(1)}`).join(' ');
  s += `<polygon points="${pl},${h - pb} ${pts} ${X(t1).toFixed(1)},${h - pb}" fill="color-mix(in srgb,var(--info) 14%,transparent)"/><polyline points="${pts}" fill="none" stroke="var(--info)" stroke-width="2" stroke-linejoin="round"/>`;
  const last = points[points.length - 1];
  s += `<circle cx="${X(last.t)}" cy="${Y(last.v)}" r="4.5" fill="var(--info)" stroke="var(--panel)" stroke-width="2"/>`;
  const labs = labels || [fmtTime(t0), fmtTime(t1)];
  labs.forEach((t, i, a) => { s += `<text x="${pl + (w - pl - pr) * i / Math.max(1, a.length - 1)}" y="${h - 5}" text-anchor="${i === 0 ? 'start' : i === a.length - 1 ? 'end' : 'middle'}" font-size="10.5" fill="var(--dim)">${esc(t)}</text>`; });
  return s + '</svg>';
}

/* ============================== state ============================== */
const S = {
  tab: store.get('tab', 'overview'), sel: null, filter: 'all', module: store.get('module', 'delivery'),
  voice: store.get('voice', true), menu: null, mapRoutes: true, mapFences: true, mapAlerts: true, alogOpen: true,
  reassign: false, ovStatus: 'UNDER_REVIEW', incSel: null, reviewed: {}, q: '', me: store.get('me', 'Lisa Nguyen'),
  mrange: +store.get('mrange', 24), termStick: true, termTop: 0, pendingRender: false, spoken: new Set(), toasted: new Set(),
};
const D = { state: null, audit: [], readings: [], readingsLong: [], maint: null, maintAI: null, maintBusy: false, config: null, connected: false, loaded: false, feed: { live: false } };
/* One <img> for the live MJPEG stream, moved between renders so the stream never restarts. */
const camImg = new Image();
camImg.alt = 'Live cargo-hold camera with person detection overlay';
let camStarted = false;
async function pollFeed() {
  if (locked()) return;
  try {
    const f = await api('/api/camera/status');
    const changed = f.live !== D.feed.live || f.persons !== D.feed.persons || (f.labels || []).join() !== (D.feed.labels || []).join();
    D.feed = f;
    if (f.live && !camStarted) { camStarted = true; camImg.src = '/api/camera/stream'; }
    if (changed) render();
  } catch { /* backend down: the connection banner covers it */ }
}
setInterval(pollFeed, 2000);

/* ============================== data ============================== */
async function api(path, opts = {}) {
  const res = await fetch(path, { headers: { 'Content-Type': 'application/json' }, ...opts });
  let body = null;
  try { body = await res.json(); } catch { /* empty */ }
  if (res.status === 401 && !path.startsWith('/api/login') && !path.startsWith('/api/me')) {
    showLogin(AUTH.role ? 'Your session ended. Please sign in again.' : '');
    throw new Error('Please sign in');
  }
  if (res.status === 403 && body && body.detail === 'admin only') throw new Error('Admin only: ask an admin to do this.');
  if (!res.ok) {
    const msg = body && body.detail ? (Array.isArray(body.detail) ? body.detail.map(d => d.msg).join('; ') : body.detail) : `HTTP ${res.status}`;
    throw new Error(msg);
  }
  return body;
}
const post = (path, data) => api(path, { method: 'POST', body: JSON.stringify(data) });

/* ============================== login and roles ============================== */
const AUTH = { auth: false, role: 'admin', name: null, id: null };      // open access until /api/me says a login is required
const isAdmin = () => !AUTH.auth || AUTH.role === 'admin';
const locked = () => document.body.classList.contains('locked');
function showLogin(msg) {
  document.body.classList.add('locked');
  $('#login').hidden = false;
  if (msg !== undefined) $('#loginErr').textContent = msg;
  const n = $('#loginId');
  if (n && !n.value) n.value = store.get('loginId', '');
  setTimeout(() => ($('#loginId').value ? $('#loginCode') : $('#loginId')).focus(), 0);
}
function hideLogin() { document.body.classList.remove('locked'); $('#login').hidden = true; $('#loginCode').value = ''; }
async function checkSession() {
  try { const me = await api('/api/me'); if (me.auth && !me.role) showLogin('Your session ended. Please sign in again.'); } catch { /* backend down */ }
}
let started = false;
async function startApp() {
  try { Object.assign(AUTH, await api('/api/me')); } catch { /* backend down: loadAll shows the message */ }
  if (AUTH.auth && !AUTH.role) { showLogin(); return; }
  if (AUTH.auth && AUTH.name) S.me = AUTH.name;
  hideLogin();
  renderChrome();
  loadAll();
  if (!started) { started = true; connect(); }
}
async function doLogin() {
  const company_id = $('#loginId').value.trim().toUpperCase(), code = $('#loginCode').value;
  const btn = $('#loginBtn');
  if (company_id.length < 3 || !code) { $('#loginErr').textContent = 'Enter your company ID and the password.'; return; }
  btn.disabled = true; $('#loginErr').textContent = '';
  try {
    const r = await post('/api/login', { company_id, code });
    store.set('loginId', r.id);
    Object.assign(AUTH, { auth: true, role: r.role, name: r.name, id: r.id });
    D.state = null; D.audit = [];
    startApp();
  } catch (err) {
    $('#loginErr').textContent = err.message === 'wrong company ID or password' ? 'Wrong company ID or password.' : err.message;
    $('#loginCode').value = ''; $('#loginCode').focus();
  } finally { btn.disabled = false; }
}
async function doLogout() { try { await post('/api/logout', {}); } catch { /* already signed out */ } location.reload(); }

/* Everything an admin does is switched off for a driver. The server refuses these too; this only makes it clear why. */
const ADMIN_ONLY = ['[data-act="ovr-ok"]', '[data-act="ovs"]', '[data-act="reassign"]', '[data-act="reassign-ok"]',
  '[data-act="vaction"][data-v="maintenance"]', '[data-act="vaction"][data-v="offline"]', '[data-act="speed"]',
  '[data-act="maint-ai"]', '#ovr', '#rtruck'].join(',');
function renderRole() {
  const chip = $('#roleChip'), banner = $('#roleBanner');
  chip.hidden = false;
  if (!AUTH.auth) {
    chip.className = 'chip rolechip open'; chip.textContent = 'Open access: no login';
    chip.title = 'No passwords are set, so anyone who can reach this page can use every control.';
  } else {
    chip.className = 'chip rolechip ' + AUTH.role; chip.textContent = `${AUTH.name} · ${AUTH.role === 'admin' ? 'Admin' : 'Driver'}`;
    chip.title = `${AUTH.id}: ` + (AUTH.role === 'admin' ? 'admin, full control' : 'driver, view, activate the route, acknowledge alerts');
  }
  $('#meRole').textContent = !AUTH.auth ? 'Open access' : AUTH.role === 'admin' ? 'Admin' : 'Driver';
  banner.hidden = isAdmin();
}
function applyRoleLocks() {
  const off = !isAdmin();
  $$(ADMIN_ONLY).forEach(el => { el.disabled = off; el.classList.toggle('rolelock', off); if (off) el.title = 'Admin only'; else if (el.title === 'Admin only') el.removeAttribute('title'); });
  if (off) ['#pop-mock', '#pop-mocklight'].forEach(id => { $(id).innerHTML = '<div class="note" style="padding:6px 4px">Mock controls are admin only.</div>'; });
}

async function loadAll() {
  if (locked()) return;
  try {
    const [state, audit, readings, config] = await Promise.all([api('/api/state'), api('/api/audit?limit=400'), api('/api/readings?minutes=120'), api('/api/config')]);
    D.state = state; D.audit = audit; D.readings = readings; D.config = config; D.loaded = true;
    if (state.camera_feed) { D.feed = state.camera_feed; if (D.feed.live && !camStarted) { camStarted = true; camImg.src = '/api/camera/stream'; } }
    audit.forEach(e => { S.spoken.add(e.id); S.toasted.add(e.id); });  // don't replay history on load
    if (!S.sel && state.packages.length) S.sel = state.packages[0].id;
    render();
    if (S.tab === 'maintenance') { loadLong(); loadMaint(false); }
  } catch (err) {
    $('#view').innerHTML = `<div class="empty"><b>Cannot reach the ColdTrust backend</b>${esc(err.message)}. Start it with <span class="mono">python -m backend</span>; this page retries every 10 s.</div>`;
  }
}
const debounce = (fn, ms) => { let t; return (...a) => { clearTimeout(t); t = setTimeout(() => fn(...a), ms); }; };
const refreshState = debounce(async () => { try { D.state = await api('/api/state'); render(); } catch { /* next poll */ } }, 250);
const refreshReadings = debounce(async () => {
  try { D.readings = await api('/api/readings?minutes=120'); if (S.tab === 'maintenance') { await loadLong(false); await loadMaint(false); } render(); } catch { /* next poll */ }
}, 800);
async function loadMaint(rerender = true) {
  try { D.maint = await api('/api/maintenance?hours=' + S.mrange); } catch { /* keep the last report */ }
  if (rerender) render();
}
async function summariseMaint() {
  D.maintBusy = true; render();
  try { D.maintAI = await post('/api/maintenance/summary', { hours: S.mrange }); }
  catch (err) { D.maintAI = { source: 'error', note: 'That did not work: ' + err.message }; }
  D.maintBusy = false; render();
}
async function loadLong(rerender = true) { try { D.readingsLong = await api('/api/readings?minutes=360'); if (rerender) render(); } catch { /* ignore */ } }

function upsert(entry) {
  const i = D.audit.findIndex(e => e.id === entry.id);
  if (i >= 0) D.audit[i] = { ...D.audit[i], ...entry }; else { D.audit.push(entry); D.audit.sort((a, b) => a.id - b.id); }
  if (D.audit.length > 600) D.audit.splice(0, D.audit.length - 600);
}

let ws, wsRetry = 1000;
function connect() {
  try { ws = new WebSocket(`${location.protocol === 'https:' ? 'wss' : 'ws'}://${location.host}/ws`); } catch { return setTimeout(connect, wsRetry); }
  ws.onopen = () => { D.connected = true; wsRetry = 1000; renderChrome(); loadAll(); };
  ws.onclose = () => { D.connected = false; D.wsDropped = true; renderChrome(); if (AUTH.auth) checkSession(); setTimeout(connect, wsRetry); wsRetry = Math.min(wsRetry * 2, 15000); };
  ws.onerror = () => ws.close();
  ws.onmessage = ev => {
    let m; try { m = JSON.parse(ev.data); } catch { return; }
    if (m.type === 'audit' || m.type === 'audit_update') {
      upsert(m.entry);
      if (m.type === 'audit') announce(m.entry);
      if (m.type === 'audit' && m.entry.kind === 'REPORT_GENERATED' && isAdmin() && !S.toasted.has(m.entry.id)) {
        S.toasted.add(m.entry.id);
        toast('ok', 'Incident report ready', `${m.entry.params.report}${m.entry.params.automatic ? ' was prepared automatically' : ''}. Use Download report in the alert bar or Evidence → Incident reports.`);
      }
      refreshState(); refreshReadings();
      render();
    } else if (m.type === 'vehicle' && D.state) { D.state.vehicle = m.vehicle; render(); }
    else if (m.type === 'approvals' && D.state) { D.state.approvals = m.items; render(); }
    else if (m.type === 'demo' && D.state) { D.state.demo = { ...D.state.demo, ...m.demo }; renderChrome(); }
  };
}
setInterval(() => { if (ws && ws.readyState === 1) ws.send('ping'); }, 20000);
setInterval(() => { if (!D.connected) loadAll(); }, 10000);

/* ============================== derived ============================== */
const packages = () => (D.state ? D.state.packages : []);
const fleet = () => (D.state ? D.state.fleet : {});
const vehicle = () => (D.state ? D.state.vehicle || {} : {});
const holdLimits = () => fleet().hold_limits_c || [2, 8];
const lightThr = () => (D.config ? D.config.light_bright_pct : 30);
const counts = () => { const c = { NORMAL: 0, UNDER_REVIEW: 0, QUARANTINED: 0 }; packages().forEach(p => c[p.status]++); return c; };
const worst = () => packages().reduce((a, p) => ORDER.indexOf(p.status) > ORDER.indexOf(a) ? p.status : a, 'NORMAL');
const telemetry = () => D.audit.filter(e => (e.ping_type === 'ROUTINE' || e.ping_type === 'EVENT') && e.params && e.params.temp_c !== undefined);
const lastTelemetry = () => { const t = telemetry(); return t[t.length - 1] || null; };
const refNow = () => Math.max(D.audit.length ? D.audit[D.audit.length - 1].ts : 0, D.readings.length ? D.readings[D.readings.length - 1].t : 0) || Date.now() / 1000;
function climate() {
  const r = D.readings[D.readings.length - 1], t = lastTelemetry();
  return { temp: r ? r.temp_c : t ? t.params.temp_c : null, light: r ? r.light_pct : t ? t.params.light_pct : null, hum: r ? r.hum : t ? t.params.hum : null };
}
/* An alert stays active while its problem is still the current one; emergencies stay until acknowledged. */
function activeAlerts() {
  const out = [], seen = new Set(), current = lastTelemetry() ? lastTelemetry().scenario : null;
  const after = (e, kind) => D.audit.some(x => x.id > e.id && x.kind === kind);
  for (const e of [...D.audit].reverse()) {
    if (!e.notified || e.severity === 'info' || HUMAN_KINDS.has(e.kind) || e.acked_by) continue;
    if (seen.has(e.scenario)) continue;
    seen.add(e.scenario);
    let live;
    if (e.kind === 'NODE_SILENT') live = !after(e, 'NODE_RESTORED');
    else if (e.kind === 'GPS_STALE') live = !after(e, 'GPS_RESTORED');
    else if (e.kind.startsWith('REJECTED_')) live = refNow() - e.ts < 600;
    else if (e.kind === 'SUSPICIOUS_PERSON') live = D.feed.persons > 0 || refNow() - e.ts < 120;
    else if (e.severity === 'emergency') live = true;
    else live = e.scenario === current;
    if (live) out.push(e);
  }
  return out;
}
const incidents = () => D.audit.filter(e => e.notified && e.severity !== 'info' && !HUMAN_KINDS.has(e.kind)).reverse().slice(0, 30);

/* ============================== map ============================== */
function projector(W, H, P) {
  const st = D.state, pts = [...st.route, ...st.geofences.map(f => [f.lat, f.lon])];
  const lats = pts.map(p => p[0]), lons = pts.map(p => p[1]);
  const minLat = Math.min(...lats) - 0.3, maxLat = Math.max(...lats) + 0.3, minLon = Math.min(...lons) - 0.3, maxLon = Math.max(...lons) + 0.3;
  const kx = Math.cos((minLat + maxLat) / 2 * Math.PI / 180);
  const s = Math.min((W - 2 * P) / ((maxLon - minLon) * kx), (H - 2 * P) / (maxLat - minLat));
  const ox = (W - (maxLon - minLon) * kx * s) / 2, oy = (H - (maxLat - minLat) * s) / 2;
  return { X: lon => ox + (lon - minLon) * kx * s, Y: lat => oy + (maxLat - lat) * s, s, minLat, maxLat, minLon, maxLon };
}
/* Nearest point on the route polyline: fraction travelled and the travelled part. */
function routeProgress(pr, lat, lon) {
  const r = D.state.route.map(([a, b]) => [pr.X(b), pr.Y(a)]), px = pr.X(lon), py = pr.Y(lat);
  let best = { d: Infinity, i: 0, f: 0 }, total = 0;
  const lens = r.slice(1).map((p, i) => Math.hypot(p[0] - r[i][0], p[1] - r[i][1]));
  lens.forEach(l => { total += l; });
  r.slice(1).forEach((b, i) => {
    const a = r[i], dx = b[0] - a[0], dy = b[1] - a[1], L2 = dx * dx + dy * dy || 1;
    const f = Math.max(0, Math.min(1, ((px - a[0]) * dx + (py - a[1]) * dy) / L2));
    const d = Math.hypot(a[0] + dx * f - px, a[1] + dy * f - py);
    if (d < best.d) best = { d, i, f };
  });
  const done = lens.slice(0, best.i).reduce((x, y) => x + y, 0) + lens[best.i] * best.f;
  const travelled = [...r.slice(0, best.i + 1), [r[best.i][0] + (r[best.i + 1][0] - r[best.i][0]) * best.f, r[best.i][1] + (r[best.i + 1][1] - r[best.i][1]) * best.f]];
  return { frac: total ? done / total : 0, travelled };
}
const FENCE_TONE = { depot: 'ok', bay: 'ok', backup: 'info', risk: 'warn' };
const FENCE_LABEL = { depot: 'Depot', bay: 'Authorised stop', backup: 'Backup cold store', risk: 'High-risk zone' };
function mapSVG() {
  const W = 800, H = 380, pr = projector(W, H, 30), st = D.state, v = vehicle();
  let s = `<svg viewBox="0 0 ${W} ${H}" role="img" aria-label="Route map"><rect width="${W}" height="${H}" fill="var(--map)"/>`;
  for (let la = Math.ceil(pr.minLat); la <= pr.maxLat; la++) s += `<line x1="0" x2="${W}" y1="${pr.Y(la)}" y2="${pr.Y(la)}" stroke="var(--road)"/><text x="6" y="${pr.Y(la) - 4}" font-size="10" fill="var(--dim)">${Math.abs(la)}°${la < 0 ? "S" : "N"}</text>`;
  for (let lo = Math.ceil(pr.minLon); lo <= pr.maxLon; lo++) s += `<line y1="0" y2="${H}" x1="${pr.X(lo)}" x2="${pr.X(lo)}" stroke="var(--road)"/><text x="${pr.X(lo) + 4}" y="${H - 6}" font-size="10" fill="var(--dim)">${Math.abs(lo)}°${lo < 0 ? "W" : "E"}</text>`;
  if (S.mapFences) st.geofences.forEach(f => {
    const col = `var(--${FENCE_TONE[f.kind] || 'info'})`, r = Math.max(7, f.radius_m / 111000 * pr.s);
    const ly = f.kind === 'risk' ? pr.Y(f.lat) - r - 6 : pr.Y(f.lat) + r + 13;  // risk zones label above, so nearby stops stay readable
    s += `<circle cx="${pr.X(f.lon)}" cy="${pr.Y(f.lat)}" r="${r}" fill="color-mix(in srgb,${col} 12%,transparent)" stroke="${col}" stroke-dasharray="5 5"/><text x="${pr.X(f.lon)}" y="${ly}" text-anchor="middle" font-size="11" fill="var(--mut)">${esc(f.name)}</text>`;
  });
  const route = st.route.map(([a, b]) => `${pr.X(b).toFixed(1)},${pr.Y(a).toFixed(1)}`).join(' ');
  s += `<polyline points="${route}" fill="none" stroke="var(--line2)" stroke-width="5" stroke-linecap="round" stroke-linejoin="round"/>`;
  const hasPos = v.lat !== undefined && v.lat !== null;
  if (hasPos) {
    const prog = routeProgress(pr, v.lat, v.lon);
    if (S.mapRoutes) s += `<polyline points="${prog.travelled.map(p => p.map(n => n.toFixed(1)).join(',')).join(' ')}" fill="none" stroke="var(--accent)" stroke-width="3.5" stroke-linecap="round" stroke-linejoin="round"/>`;
    const alert = S.mapAlerts && activeAlerts().length, col = alert ? 'var(--crit)' : 'var(--accent)', x = pr.X(v.lon), y = pr.Y(v.lat);
    s += `<circle class="pulse" cx="${x}" cy="${y}" r="16" fill="${col}"/><circle cx="${x}" cy="${y}" r="9" fill="${col}" stroke="var(--panel)" stroke-width="3"/>`;
    if (alert) { const a = activeAlerts()[0]; s += `<g transform="translate(${Math.min(x + 14, W - 150)},${Math.max(y - 32, 6)})"><rect width="140" height="22" rx="11" fill="var(--crit)"/><text x="70" y="15" text-anchor="middle" font-size="11" font-weight="700" fill="#fff">${esc(a.kind.replace(/_/g, ' ').slice(0, 20))}</text></g>`; }
  }
  s += `<g transform="translate(${W - 42},${H - 46})"><circle r="26" fill="var(--panel)" stroke="var(--line2)"/><text y="-11" text-anchor="middle" font-size="10" fill="var(--mut)">N</text><path d="M0 -6 L5 9 L0 5 L-5 9Z" fill="var(--accent)"/></g>`;
  return s + '</svg>';
}
/* Fraction of the route travelled, measured on the lat/lon polyline itself (works with or without the tile map). */
function routeProgressLL(lat, lon) {
  const r = D.state.route, kx = Math.cos(lat * Math.PI / 180), P = ([a, b]) => [b * kx, a];
  const pts = r.map(P), q = P([lat, lon]);
  const lens = pts.slice(1).map((p, i) => Math.hypot(p[0] - pts[i][0], p[1] - pts[i][1]));
  const total = lens.reduce((x, y) => x + y, 0);
  let best = { d: Infinity, i: 0, f: 0 };
  pts.slice(1).forEach((b, i) => {
    const a = pts[i], dx = b[0] - a[0], dy = b[1] - a[1], L2 = dx * dx + dy * dy || 1;
    const f = Math.max(0, Math.min(1, ((q[0] - a[0]) * dx + (q[1] - a[1]) * dy) / L2));
    const dd = Math.hypot(a[0] + dx * f - q[0], a[1] + dy * f - q[1]);
    if (dd < best.d) best = { d: dd, i, f };
  });
  const done = lens.slice(0, best.i).reduce((x, y) => x + y, 0) + lens[best.i] * best.f;
  const a = r[best.i], b = r[best.i + 1];
  const travelled = [...r.slice(0, best.i + 1), [a[0] + (b[0] - a[0]) * best.f, a[1] + (b[1] - a[1]) * best.f]];
  return { frac: total ? done / total : 0, travelled };
}

/* Tile map: Leaflet (BSD-2, vendored) + OpenStreetMap tiles; the route is the OSRM road geometry from the backend.
   One persistent map node is moved into each freshly rendered view, like the camera image. */
const HAS_MAP = typeof window.L !== 'undefined';
const cssVar = n => getComputedStyle(document.documentElement).getPropertyValue(n).trim() || '#888';
const mapHost = document.createElement('div');
mapHost.className = 'mapslot';
let lmap = null, lmapLayers = null, lmapFit = '';
function mapBlock() {
  return `<div class="map">${HAS_MAP ? '<div id="mapslot"></div>' : mapSVG()}${mapTools()}</div>`;
}
function mountMap() {
  const slot = $('#mapslot');
  if (!slot || !HAS_MAP || !D.state) return;
  slot.appendChild(mapHost);
  if (!lmap) {
    lmap = L.map(mapHost, { zoomControl: true, attributionControl: true, scrollWheelZoom: false });
    L.tileLayer('https://tile.openstreetmap.org/{z}/{x}/{y}.png', {
      maxZoom: 18, attribution: '&copy; <a href="https://www.openstreetmap.org/copyright" target="_blank" rel="noopener">OpenStreetMap</a> contributors' }).addTo(lmap);
    lmapLayers = L.layerGroup().addTo(lmap);
  }
  const st = D.state, v = vehicle();
  lmapLayers.clearLayers();
  const line = st.route.map(p => [p[0], p[1]]);
  const key = line.length + ':' + line[0] + ':' + line[line.length - 1];
  L.polyline(line, { color: cssVar('--line2'), weight: 8, opacity: .9, lineCap: 'round' }).addTo(lmapLayers);
  L.polyline(line, { color: cssVar('--info'), weight: 3, opacity: .85, dashArray: '2 7', lineCap: 'round' }).addTo(lmapLayers);
  if (S.mapFences) st.geofences.forEach(f => {
    const col = cssVar('--' + (FENCE_TONE[f.kind] || 'info'));
    L.circle([f.lat, f.lon], { radius: f.radius_m, color: col, weight: 2, dashArray: '5 5', fillColor: col, fillOpacity: .14 })
      .bindTooltip(`${f.name} · ${FENCE_LABEL[f.kind] || f.kind}`, { permanent: f.kind !== 'risk', direction: f.kind === 'risk' ? 'top' : 'bottom', className: 'fence-tip' }).addTo(lmapLayers);
  });
  if (v.lat !== undefined && v.lat !== null) {
    if (S.mapRoutes) L.polyline(routeProgressLL(v.lat, v.lon).travelled, { color: cssVar('--accent'), weight: 5, lineCap: 'round' }).addTo(lmapLayers);
    const al = S.mapAlerts ? activeAlerts() : [], alert = al.length;
    const label = alert ? `<span class="pin-label">${esc(al[0].kind.replace(/_/g, ' ').slice(0, 22))}</span>` : '';
    L.marker([v.lat, v.lon], { keyboard: false, zIndexOffset: 1000, icon: L.divIcon({ className: 'truck-pin' + (alert ? ' alert' : ''),
      html: `<i class="pulse-ring"></i><i class="dot"></i>${label}`, iconSize: [20, 20], iconAnchor: [10, 10] }) }).addTo(lmapLayers);
  }
  lmap.invalidateSize(false);
  const sz = lmap.getSize();   // fit only once the container really has a size; fitting a 0x0 map gives the whole-world view
  if (lmapFit !== key && sz.x > 0 && sz.y > 0) { lmapFit = key; lmap.fitBounds(L.latLngBounds(line).pad(0.08), { animate: false }); }
}
function overlay() {
  const v = vehicle(), f = fleet(), al = activeAlerts();
  let prog = null;
  if (v.lat !== undefined && v.lat !== null) prog = routeProgressLL(v.lat, v.lon).frac;
  const status = v.route_status || 'Idle', tone = /offline/i.test(status) ? 'crit' : /maint/i.test(status) ? 'warn' : /route/i.test(status) ? 'ok' : 'info';
  const remain = prog === null ? '-' : `${(f.route.distance_km * (1 - prog)).toFixed(0)} km left`;
  return `<div class="mapov"><div><div style="display:flex;justify-content:space-between;gap:8px;align-items:flex-start"><div><h4>${esc(f.id)}</h4><div class="mut" style="font-size:12.5px">${esc(v.zone || (v.authorised_stop ? 'Authorised stop' : 'Between stops'))}</div></div>${pill(tone, status)}</div>
   <div class="gauges">${gauge({ v: v.speed_kmh, min: 0, max: 140, unit: 'km/h', label: 'Speed', color: 'var(--accent)', size: 120 })}${gauge({ v: v.fuel_l, min: 0, max: 600, unit: 'L', label: 'Fuel', color: 'var(--info)', size: 120 })}</div></div>
   <div><div class="row"><span><b>${esc(f.driver_name)}</b><br><span class="mut mono" style="font-size:12px">${esc(f.driver_id)}</span></span><span class="mut" style="font-size:12px;text-align:right">${esc(f.route.from)} → ${esc(f.route.to)}<br>${prog === null ? 'No position yet' : `${Math.round(prog * 100)}% · ${remain}`}</span></div>
   ${prog === null ? '' : `<div class="bar" style="margin-top:10px"><i style="width:${Math.round(prog * 100)}%;background:var(--accent)"></i></div>`}</div>
   <div><button class="coll" data-act="alog"><span>Alerts and notifications (${al.length})</span><span>${S.alogOpen ? 'Hide' : 'Show'}</span></button>
   ${S.alogOpen ? `<div class="alog">${al.length ? al.map(a => `<div>${pill(SEV_TONE[a.severity], a.severity, false)}<span><b>${esc(a.kind.replace(/_/g, ' '))}</b> <span class="mut mono">${fmtTime(a.ts)}</span><br><span class="mut">${esc(a.reasoning.slice(0, 120))}</span></span></div>`).join('') : '<div class="mut">No active alerts.</div>'}</div>` : ''}</div></div>`;
}
function mapTools() {
  return `<div class="maptools">${[['mapRoutes', 'Show route'], ['mapFences', 'Geofences'], ['mapAlerts', 'Show alerts']].map(([k, t]) => `<button class="tog ${S[k] ? 'on' : ''}" data-act="mt" data-v="${k}" aria-pressed="${S[k]}">${t} <span class="sw"></span></button>`).join('')}</div>`;
}

/* ============================== shared cards ============================== */
function climateCard() {
  const c = climate(), [lo, hi] = holdLimits(), thr = lightThr();
  const out = c.temp !== null && (c.temp < lo || c.temp > hi), bright = c.light !== null && c.light >= thr;
  const tcol = out ? 'var(--crit)' : 'var(--ok)';
  const t = lastTelemetry();
  return `<div class="card"><div class="h"><h3>Cargo climate</h3>${isMocked('temp') ? '<span class="chip simchip">Temperature mocked</span>' : ''}${isMocked('light') ? '<span class="chip simchip">Light mocked</span>' : ''}<span class="grow"></span>${c.temp === null ? pill('mut', 'Waiting for sensor', false) : pill(out ? 'crit' : bright ? 'warn' : 'ok', out ? 'Outside limits' : bright ? 'Hold is lit' : 'In range')}</div>
   <div class="b"><div class="gauges" style="margin-top:0">${gauge({ v: c.temp, min: 0, max: 15, unit: '°C', label: 'Temperature', color: tcol, band: [lo, hi], sub: `limits ${lo}–${hi}` })}
   ${gauge({ v: c.light, min: 0, max: 100, unit: '% light', label: 'Ambient light', color: bright ? 'var(--crit)' : 'var(--ok)', mark: thr, sub: `alarm ≥${thr}` })}</div>
   <div class="row" style="margin-top:8px;font-size:12.5px"><span class="mut">Humidity <b class="tn" style="color:var(--fg)">${c.hum === null ? '-' : Math.round(c.hum) + '%'}</b></span><span class="mut">Trend <b class="tn" style="color:var(--fg)">${t && t.params.slope_c_per_min != null ? (t.params.slope_c_per_min > 0 ? '+' : '') + t.params.slope_c_per_min + ' °C/min' : '-'}</b></span><span class="mut">Last ping <b class="tn" style="color:var(--fg)">${t ? fmtTime(t.ts) : '-'}</b></span></div>
   <div class="note" style="margin-top:8px">DHT11 sampled every 5 s · logged every 30 s · routine ping every 15 min · event ping instant</div></div></div>`;
}
const APPR_DONE = { approved: ['ok', 'Approved'], rejected: ['mut', 'Rejected'], expired: ['mut', 'Expired'] };
function approvalsCard() {
  const items = ((D.state && D.state.approvals) || []).slice().reverse();
  const waiting = items.filter(i => i.status === 'pending'), recent = items.filter(i => i.status !== 'pending').slice(0, 3);
  const admin = isAdmin();
  const wRow = i => `<div class="row" style="align-items:flex-start"><span><b>${esc(i.label)}</b><div class="note">${esc(i.reason)}</div><div class="note">Proposed by ${esc(i.source)} for ${esc(i.kind.replace(/_/g, ' ').toLowerCase())} · closes ${fmtTime(i.expires)}</div></span>
    ${admin ? `<span style="display:flex;gap:6px"><button class="btn sm pri" data-act="appr-ok" data-v="${i.id}">Approve</button><button class="btn sm" data-act="appr-no" data-v="${i.id}">Reject</button></span>` : pill('warn', 'Waiting for admin', false)}</div>`;
  const dRow = i => `<div class="row"><span>${esc(i.label)}<div class="note">${esc(APPR_DONE[i.status][1])}${i.decided_by && i.decided_by !== 'system' ? ' by ' + esc(i.decided_by) : ''} · ${fmtTime(i.decided_at)}</div></span>${pill(APPR_DONE[i.status][0], APPR_DONE[i.status][1], false)}</div>`;
  return `<div class="card"><div class="h"><h3>Approvals</h3><span class="grow"></span>${waiting.length ? pill('warn', `${waiting.length} waiting`) : pill('mut', 'Nothing waiting', false)}</div>
   <div class="b list">${waiting.map(wRow).join('') || '<div class="note">The agent proposes actions here after an alert. Nothing happens until an admin approves it.</div>'}${recent.length ? `<div class="note" style="margin-top:6px">Recently decided</div>${recent.map(dRow).join('')}` : ''}</div></div>`;
}
const SEAL_PILL = { CLEAN: 'ok', SUSPICIOUS: 'warn', NOT_TRUSTED: 'crit' };
const CHECK_MARK = { pass: ['ok', 'passed'], warn: ['warn', 'suspicious'], fail: ['crit', 'failed'], skip: ['mut', 'not run'] };
function trustCard() {
  const e = [...D.audit].reverse().find(x => x.seal), s = e && e.seal;
  const rows = s ? s.checks.map(c => `<div class="row"><span>${esc(c.name)}<div class="note">${esc(c.detail)}</div></span>${pill(CHECK_MARK[c.status][0], CHECK_MARK[c.status][1], c.status !== 'skip')}</div>`).join('') : '';
  return `<div class="card"><div class="h"><h3>Data trust · Seal agent</h3><span class="grow"></span>${s ? pill(SEAL_PILL[s.result], s.result === 'CLEAN' ? 'No problems found' : s.result === 'SUSPICIOUS' ? 'Suspicious' : 'Not trusted') : pill('mut', 'Waiting for data', false)}</div>
   <div class="b list">${s ? `<div class="note">${s.ran} of ${s.total} checks ran on the batch from ${fmtTime(e.ts)}${s.simulated && s.simulated.length ? ` · simulated ${esc(s.simulated.join(' + '))}` : ''}</div>${rows}` : '<div class="empty"><b>No readings yet</b>The checks run on every batch the sensor node sends.</div>'}</div></div>`;
}
function lastEvidence(around = null, windowS = 900) {
  const ev = D.audit.filter(e => e.kind === 'EVIDENCE_CAPTURED' && e.params && e.params.file);
  return around === null ? ev.slice(-1) : ev.filter(e => Math.abs(e.ts - around) <= windowS);
}
function frameHTML(e) {
  return `<div><div class="cam"><img src="/api/evidence/${encodeURIComponent(e.params.file)}" alt="Cargo camera frame at ${fmtTime(e.ts)}" loading="lazy"></div><div class="note mono" style="margin-top:4px">${fmtTime(e.ts)} · sha ${esc(e.params.sha256.slice(0, 12))}…${e.params.person_present ? ' · person' : ''}</div></div>`;
}
function cameraCard() {
  const cam = D.state.camera || {}, ev = lastEvidence()[0], f = D.feed || {};
  const alert = activeAlerts().find(a => a.kind === 'SUSPICIOUS_PERSON' || a.kind === 'THEFT_SUSPECTED');
  let body, badge;
  if (camStarted) {
    badge = alert ? pill('crit', alert.kind === 'THEFT_SUSPECTED' ? 'Theft suspected' : 'Suspicious person')
      : f.live ? (f.persons ? pill('warn', `${f.persons} person${f.persons > 1 ? 's' : ''} in view`) : pill('ok', 'Live')) : pill('mut', 'Feed paused', false);
    body = `<div class="cam livewrap"><div id="camslot"></div><span class="livetag ${f.live ? '' : 'off'}">${f.live ? 'LIVE' : 'OFFLINE'}</span></div>
      <div class="row" style="margin-top:10px;font-size:12.5px"><span class="mut">People in view</span><b class="tn">${f.live ? f.persons : '-'}</b><span class="mut">Cues</span><span>${f.live && f.labels && f.labels.length ? esc(f.labels.join(', ')) : 'none'}</span></div>`;
  } else {
    badge = cam.person_present ? pill('crit', 'Person in hold') : ev ? pill('info', 'Latest evidence') : pill('mut', 'Not connected', false);
    body = ev ? frameHTML(ev) : `<div class="empty"><b>Camera not connected</b>Start the camera agent: <span class="mono">python -m camera.camera_agent</span>. Live video with person detection appears here.</div>`;
  }
  return `<div class="card"><div class="h"><h3>Cargo camera</h3><span class="grow"></span>${badge}</div>
   <div class="b">${body}<div class="note" style="margin-top:8px">Detects people and pose cues only; it never identifies anyone.</div></div></div>`;
}
function deliveryCard() {
  const st = D.state, sent = D.audit.filter(e => e.notified), last = sent[sent.length - 1];
  const row = (n, on, t, off = 'Off') => `<div class="row"><span>${n}</span>${on ? pill('ok', t) : pill('mut', off, false)}</div>`;
  return `<div class="card"><div class="h"><h3>Alert delivery</h3><span class="grow"></span>${last ? `<span class="chip">last alert <b class="tn">${fmtTime(last.ts)}</b></span>` : ''}</div>
   <div class="b list">${row('Telegram · driver + admin', st.telegram, 'Connected', 'Not configured')}${row('Alarm sound + voice · this console', S.voice, 'On', 'Off (tap the speaker icon)')}${row(st.llm ? `AI agents · ${esc(st.llm_provider)} (${esc(st.llm_model || '')})` : 'AI agents', st.llm, (st.llm_usage ? `${st.llm_usage.used}/${st.llm_usage.daily_cap} today` + (st.llm_usage.paused ? ' · paused' : '') + ' · ' : 'On · ') + (st.vision ? 'camera frames sent' : 'text only'), 'Rules only')}${row('Live link to backend', D.connected, 'Connected', 'Reconnecting')}
   <div class="note">Messages go out on a new problem, an escalation, a status change, or every 10 min while unresolved.</div></div></div>`;
}

/* ============================== terminal ============================== */
function paramsText(e) {
  const p = e.params || {}, kv = [];
  if (p.temp_c !== undefined) kv.push(`temp=${p.temp_c}C`);
  if (p.hum !== undefined) kv.push(`hum=${Math.round(p.hum)}%`);
  if (p.light_pct !== undefined) kv.push(`light=${p.light_pct}%`);
  if (p.slope_c_per_min !== undefined && p.slope_c_per_min !== null) kv.push(`slope=${p.slope_c_per_min}C/min`);
  if (p.seq !== undefined) kv.push(`seq=${p.seq}`);
  if (p.n_samples !== undefined) kv.push(`n=${p.n_samples}`);
  if (p.speed_kmh !== undefined && p.speed_kmh !== null) kv.push(`speed=${p.speed_kmh}km/h`);
  if (p.zone) kv.push(`zone=${p.zone}`);
  if (p.person_in_frame) kv.push('person_in_frame=true');
  if (p.persons !== undefined && p.persons !== null) kv.push(`persons=${p.persons}`);
  if (p.behaviours && p.behaviours.length) kv.push(`cues=${p.behaviours.join('+')}`);
  if (p.lit) kv.push('hold_lit=true');
  if (p.mocked && p.mocked.length) p.mocked.forEach(x => kv.push(`MOCK_${x.toUpperCase()}`)); else if (p.simulated) kv.push('MOCK_TEMP');
  if (p.mock !== undefined) kv.push(`mock=${p.mock}`);
  ['by', 'package', 'truck', 'action', 'supervisor', 'alert', 'incident', 'file'].forEach(k => { if (p[k] !== undefined) kv.push(`${k}=${p[k]}`); });
  if (p.sha256) kv.push(`sha256=${p.sha256.slice(0, 12)}…`);
  return kv.join(' ');
}
function lineHTML(e) {
  const src = e.explanation_source === 'llm' ? `<span class="src llm">${esc(e.explained_by || 'AI')}</span>` : e.explanation_source === 'human' ? '<span class="src">human</span>' : '<span class="src">rules</span>';
  const quiet = e.scenario === 'NORMAL';
  const acts = [...(e.actions || [])];
  if (e.notified) acts.push(`driver + admin notified (${e.notify_reason || 'alert'})`);
  if (e.acked_by) acts.push(`acknowledged by ${e.acked_by}`);
  return `<div class="ln sev-${esc(e.severity)}"><span class="ts">${fmtTime(e.ts)}</span><span class="pt ${esc(e.ping_type)}">${esc(e.ping_type)}</span><span class="kind">${esc(e.kind)}</span> <span class="kv">${esc(paramsText(e))}</span>
    ${!quiet && e.findings && e.findings.length ? `<div class="sub"><i>findings</i>${esc(e.findings.map(f => `${f.code} (${f.severity})`).join(', '))}</div>` : ''}
    <div class="sub"><i>reasoning</i>${esc(e.reasoning)}${src}</div>
    ${e.consulted ? `<div class="sub"><i>consulted</i>${esc(e.consulted)}</div>` : ''}
    ${e.kind === 'REPORT_GENERATED' && e.params && e.params.file && isAdmin() ? `<div class="sub"><i>file</i><a href="/api/reports/${encodeURIComponent(e.params.file)}">Download ${esc(e.params.file)}</a></div>` : ''}
    ${e.ai_rejected ? `<div class="sub"><i>ai check</i>${esc(e.ai_rejected)}</div>` : ''}
    ${e.product_impact ? `<div class="sub"><i>impact</i>${esc(e.product_impact)}</div>` : ''}
    ${acts.length ? `<div class="sub act"><i>action</i>${esc(acts.join(' | '))}</div>` : ''}
    ${!quiet && e.recommended_actions && e.recommended_actions.length ? `<div class="sub rec"><i>next</i>${esc(e.recommended_actions.join('; '))}</div>` : ''}</div>`;
}
function termCard() {
  const lines = D.audit.slice(-150).map(lineHTML).join('');
  return `<div class="card"><div class="h"><h3>Agent reasoning &amp; audit log</h3><span class="grow"></span><span class="chip">Rules decide status · <b>${D.state.llm ? esc(D.state.llm_provider) + ' agents explain' : 'rules explain'}</b></span></div>
   <div class="b"><div class="term" id="term" tabindex="0" aria-label="Agent audit log">${lines || '<div class="ln mut">Waiting for the first device ping…</div>'}<span class="cur"></span></div></div></div>`;
}

/* ============================== views ============================== */
function vOverview() {
  return `<div class="g g2"><div class="g">
     <div class="card"><div class="h"><h3>Operations map</h3><span class="grow"></span><span class="note">Live position, planned route and geofences</span></div>
       <div class="b"><div class="mapgrid">${overlay()}${mapBlock()}</div></div></div>
     ${termCard()}</div>
   <div class="g start">${approvalsCard()}${speedCard()}${climateCard()}${trustCard()}${cameraCard()}${deliveryCard()}</div></div>`;
}
const matches = p => !S.q || [p.id, p.description, p.client, p.destination].some(x => String(x).toLowerCase().includes(S.q));
function slot(p) {
  return `<button class="slot s-${p.status} ${S.sel === p.id ? 'sel' : ''}" data-act="pick" data-v="${esc(p.id)}" aria-label="${esc(p.id)} ${esc(LABEL[p.status])}"><span class="id">${esc(p.id)}</span><span class="mut" style="font-size:11.5px;line-height:1.2">${esc(p.description)}</span>
   <span style="display:flex;gap:5px;flex-wrap:wrap">${pill(TONE[p.status], SHORT[p.status], false)}</span></button>`;
}
function vCargo() {
  const f = fleet(), c = counts(), pk = packages(), tw = pk.reduce((a, p) => a + p.weight_kg, 0), tv = pk.reduce((a, p) => a + p.volume_m3, 0);
  const wp = tw / f.payload_capacity_kg * 100, vp = tv / f.volume_capacity_m3 * 100;
  const shown = pk.filter(p => (S.filter === 'all' || p.status === S.filter) && matches(p)).sort((a, b) => a.loading_order - b.loading_order);
  const series = D.readings.slice(-720).map(r => ({ t: r.t, v: r.temp_c }));
  const capRow = (label, val, pct, tone) => `<div><div class="row" style="background:none;border:0;padding:0"><span class="mut">${label}</span><b class="tn">${val}</b></div><div class="bar" style="margin-top:6px"><i style="width:${Math.min(100, pct)}%;background:var(--${tone})"></i></div></div>`;
  return `<div class="g gcargo"><div class="g start">
    <div class="card"><div class="h"><h3>Cargo layout</h3>${pill(c.QUARANTINED ? 'crit' : c.UNDER_REVIEW ? 'warn' : 'ok', c.QUARANTINED ? `${c.QUARANTINED} quarantined` : c.UNDER_REVIEW ? `${c.UNDER_REVIEW} under review` : 'All normal')}<span class="grow"></span>${vp >= 95 ? '<span class="chip">Fully loaded</span>' : ''}</div>
     <div class="b"><div class="g g3" style="gap:16px">
        ${capRow('Weight', `${fmt(tw)} / ${fmt(f.payload_capacity_kg)} kg`, wp, 'info')}
        ${capRow('Volume', `${tv.toFixed(1)} / ${f.volume_capacity_m3} m³`, vp, vp >= 95 ? 'warn' : 'info')}
        ${capRow('Cleared to deliver', `${c.NORMAL} / ${pk.length}`, pk.length ? c.NORMAL / pk.length * 100 : 0, c.QUARANTINED ? 'crit' : c.UNDER_REVIEW ? 'warn' : 'ok')}</div>
       <div class="truck" style="margin-top:16px"><div class="cab">CAB</div><div class="hold" aria-label="Cargo hold, front at left, rear door at right">${pk.slice().sort((a, b) => a.loading_order - b.loading_order).map(slot).join('')}</div></div>
       <div class="legend"><span><i style="background:var(--ok)"></i>Normal</span><span><i style="background:var(--warn)"></i>Under review</span><span><i style="background:var(--crit)"></i>Quarantined</span><span class="dim">Loading order 1 (front) to ${pk.length} (rear door)</span></div></div></div>
    <div class="g g3" style="gap:14px"><div style="grid-column:span 2" class="g">${climateCard()}</div><div class="card"><div class="h"><h3>Trend</h3><span class="grow"></span><span class="note">last 2 h</span></div><div class="b">${lineChart(series, { w: 300, h: 180, band: holdLimits() })}</div></div></div>
    ${cameraCard()}</div>
   <div class="card start"><div class="h"><h3>Packages</h3><span class="grow"></span>
       <span class="chip">Items <b class="tn">${pk.length}</b></span><span class="chip">Weight <b class="tn">${fmt(tw)} kg</b></span><span class="chip">Payload <b class="tn">${fmt(f.payload_capacity_kg)} kg</b></span></div>
     <div class="b"><div class="filters" style="margin-bottom:10px">${[['all', 'All'], ['NORMAL', 'Normal'], ['UNDER_REVIEW', 'Review'], ['QUARANTINED', 'Quarantined']].map(([k, t]) => `<button class="${S.filter === k ? 'on' : ''}" data-act="filter" data-v="${k}" aria-pressed="${S.filter === k}">${t}${k === 'all' ? '' : ' ' + c[k]}</button>`).join('')}${S.q ? `<span class="chip">matching “${esc(S.q)}” <button data-act="clearq" aria-label="Clear search">✕</button></span>` : ''}</div>
     <div class="plist">${shown.length ? shown.map(pkCard).join('') : '<div class="empty">No packages match.</div>'}</div></div></div></div>`;
}
function pkCard(p) {
  const open = S.sel === p.id;
  const head = `<div class="r"><span><b class="mono">${esc(p.id)}</b> <span class="mut">${esc(p.description)} ×${p.quantity}</span></span>${pill(PRIO[p.priority] || 'mut', p.priority, false)}</div>
    <div class="r"><span class="chips">${p.tags.map(t => `<span class="tag">${esc(t)}</span>`).join('')}${p.reassign_to ? `<span class="tag">→ ${esc(p.reassign_to)}</span>` : ''}</span>${pill(TONE[p.status], SHORT[p.status])}</div>
    <div class="r mut" style="font-size:12.5px">${S.module === 'delivery' ? `<span>${esc(p.recipient || p.client)} · ${esc(p.destination)}</span>` : `<span class="tn">${fmt(p.weight_kg)} kg · ${p.volume_m3} m³ · load #${p.loading_order}</span>`}<span class="tn">risk ${p.risk_score}</span></div>`;
  return `<div class="pk ${open ? 'sel' : ''}"><button data-act="pick" data-v="${esc(p.id)}" style="text-align:left;display:grid;gap:7px;width:100%" aria-expanded="${open}">${head}</button>${open ? pkDetail(p) : ''}</div>`;
}
function pkDetail(p) {
  const r = p.risk_score, out = p.excursion_min || 0;
  const warn = p.status === 'QUARANTINED'
    ? `<div class="critbox"><b>Do not accept.</b> The custody token was revoked when the agent quarantined this package. Only a supervisor release re-issues it.</div>`
    : p.status === 'UNDER_REVIEW' ? `<div class="warnbox"><b>Under review.</b> Check the package and its temperature record before handing it over.</div>`
    : p.tags.includes('Fragile') ? `<div class="warnbox"><b>Fragile.</b> Handle with care. Shock-absorbing packaging required.</div>`
    : `<div class="okbox">${esc(p.instructions || `Keep within ${p.temp_min_c}–${p.temp_max_c} °C.`)}</div>`;
  return `<dl class="kvs"><div><dt>Client</dt><dd>${esc(p.client)}</dd></div><div><dt>Loading order</dt><dd>#${p.loading_order}</dd></div>
     <div><dt>Destination</dt><dd>${esc(p.destination)}</dd></div><div><dt>Temperature limits</dt><dd class="tn">${p.temp_min_c}–${p.temp_max_c} °C</dd></div>
     <div><dt>Weight / volume</dt><dd class="tn">${fmt(p.weight_kg)} kg · ${p.volume_m3} m³</dd></div><div><dt>Excursion budget used</dt><dd class="tn">${out.toFixed(1)} of ${p.excursion_budget_min} min</dd></div></dl>
     <div><div class="row" style="background:none;border:0;padding:0"><span class="mut">Risk score</span><b class="tn">${r} / 100</b></div><div class="bar" style="margin-top:5px"><i style="width:${r}%;background:${r >= 70 ? 'var(--crit)' : r >= 30 ? 'var(--warn)' : 'var(--ok)'}"></i></div></div>
     <div class="row"><span><span class="mut">Custody token</span> <span class="mono">${esc(p.custody_token.slice(0, 4))}…${esc(p.custody_token.slice(-2))}</span></span>${pill(p.token_valid ? 'ok' : 'crit', p.token_valid ? 'Valid' : 'Revoked')}<button class="btn sm" data-act="scan" data-v="${esc(p.custody_token)}">Simulate receiving scan</button></div>
     ${warn}
     <div style="display:flex;gap:8px;flex-wrap:wrap"><button class="btn sm" data-act="tab" data-v="route">View Route</button><button class="btn sm" data-act="reassign" aria-expanded="${S.reassign}">Reassign to Another Truck</button></div>
     ${S.reassign ? `<div class="ovr"><div class="mut" style="font-size:12px">Move ${esc(p.id)} to</div><select id="rtruck" class="field" aria-label="Target truck">${TRUCKS.map(t => `<option>${esc(t)}</option>`).join('')}</select><input id="rreason" class="field" type="text" placeholder="Reason, e.g. this truck's cooling failed" aria-label="Reassignment reason"><button class="btn sm pri" style="justify-self:start" data-act="reassign-ok" data-v="${esc(p.id)}">Request transfer</button></div>` : ''}
     <div class="ovr"><div class="mut" style="font-size:12px">Supervisor override (logged with your name and reason)</div>
       <div class="seg" role="group" aria-label="New status" style="justify-self:start">${ORDER.map(s => `<button class="${S.ovStatus === s ? 'on' : ''}" data-act="ovs" data-v="${s}" aria-pressed="${S.ovStatus === s}">${s === 'NORMAL' ? 'Release' : s === 'UNDER_REVIEW' ? 'Hold' : 'Quarantine'}</button>`).join('')}</div>
       <input type="text" id="ovr" class="field" placeholder="Reason, e.g. seals intact, inspected on site" aria-label="Override reason"><button class="btn sm pri" style="justify-self:start" data-act="ovr-ok" data-v="${esc(p.id)}">Apply override</button></div>`;
}
function recoveries(rows, thr) {
  const out = [];
  for (let i = 1; i < rows.length; i++) {
    if (rows[i].light_pct >= thr && rows[i - 1].light_pct < thr) {           // door opened
      const start = rows[i].t, before = rows.filter(r => r.t >= start - 300 && r.t < start).map(r => r.temp_c);
      const base = before.length ? before.sort((a, b) => a - b)[Math.floor(before.length / 2)] : rows[i - 1].temp_c;
      let j = i; while (j < rows.length && rows[j].light_pct >= thr) j++;   // door closed at rows[j]
      if (j >= rows.length) break;
      const end = rows[j].t, back = rows.slice(j).find(r => r.temp_c <= base + 0.5);
      out.push({ at: start, minutes: back ? (back.t - end) / 60 : null });
      i = j;
    }
  }
  return out;
}
const MAINT_RANGES = [[6, '6 h'], [24, '24 h'], [168, '7 days']];
const COOLING_TONE = { 'no sign of weakening': 'ok', 'possible weakening': 'warn', 'not enough data': 'info' };
const URGENCY_TONE = { none: 'ok', monitor: 'info', 'service soon': 'warn' };
/* After an AI review that finds the cooling unit may be failing: the temperature event(s) it covers can now be reported. */
function reportOffers(ai) {
  const offers = ai.report_offers || [];
  if (!offers.length) return '';
  const live = (D.state && D.state.approvals) || [];
  const row = o => {
    const r = reportFor(o.audit_id), waiting = live.some(i => i.id === o.id && i.status === 'pending');
    const action = r ? (isAdmin() ? downloadBtn(r) : pill('ok', 'Report prepared', false))
      : waiting ? (isAdmin() ? `<button class="btn sm pri" data-act="appr-ok" data-v="${o.id}">Approve incident report</button>` : pill('warn', 'Waiting for admin', false))
      : pill('mut', 'Closed', false);
    return `<div class="row"><span>${esc(o.kind.replace(/_/g, ' ').toLowerCase())} · alert #${o.audit_id}<div class="note">Offered because of this review. Nothing is prepared until an admin approves.</div></span>${action}</div>`;
  };
  return `<div style="display:grid;gap:8px;padding:10px 12px;border:1px solid var(--ok);border-radius:10px"><b>This review supports an incident report</b>${offers.map(row).join('')}</div>`;
}
function maintReview() {
  const m = D.maint, ai = D.maintAI, u = D.state && D.state.llm_usage;
  const seg = `<div class="seg" role="group" aria-label="Review period">${MAINT_RANGES.map(([h, t]) => `<button data-act="mrange" data-v="${h}" class="${S.mrange === h ? 'on' : ''}" aria-pressed="${S.mrange === h}">${t}</button>`).join('')}</div>`;
  const head = `<div class="h"><h3>Maintenance review</h3><span class="grow"></span>${seg}</div>`;
  if (!m) return `<div class="card" style="margin-bottom:14px">${head}<div class="b mut">Loading the review…</div></div>`;
  const ov = m.overall;
  if (!ov) return `<div class="card" style="margin-bottom:14px">${head}<div class="b mut">No sensor readings in this period yet.</div></div>`;
  const num = (v, unit = '') => v === null || v === undefined ? '-' : v + unit;
  const kpis = `<div class="g g4"><div class="card kpi"><div class="l">Time in range</div><div class="v tn">${num(ov.time_in_range_pct, '%')}</div><div>${pill(ov.time_in_range_pct >= 90 ? 'ok' : 'warn', ov.time_in_range_pct >= 90 ? 'Within 90% target' : 'Below 90% target', false)}</div></div>
    <div class="card kpi"><div class="l">Peak temperature</div><div class="v tn">${num(ov.peak_c, ' °C')}</div><div class="note">${m.samples} readings</div></div>
    <div class="card kpi"><div class="l">Door openings</div><div class="v tn">${ov.door_openings}</div><div class="note">${ov.not_recovered ? ov.not_recovered + ' never recovered' : 'all recovered'}</div></div>
    <div class="card kpi"><div class="l">Average recovery</div><div class="v tn">${ov.avg_recovery_min === null ? '-' : ov.avg_recovery_min + ' min'}</div><div>${pill(ov.recovery_trend === 'slower' ? 'warn' : ov.recovery_trend === 'unknown' ? 'mut' : 'ok', ov.recovery_trend === 'unknown' ? 'Trend: not enough openings' : 'Trend: ' + ov.recovery_trend, false)}</div></div></div>`;
  const days = m.per_day.length > 1 ? `<div class="list" style="margin-top:12px">${m.per_day.map(d => `<div class="row"><span class="mono">${esc(d.date)}</span><span class="mut tn">in range ${num(d.time_in_range_pct, '%')} · peak ${num(d.peak_c, ' °C')} · ${d.door_openings} opening${d.door_openings === 1 ? '' : 's'} · recovery ${d.avg_recovery_min === null ? '-' : d.avg_recovery_min + ' min'}</span></div>`).join('')}</div>` : '';
  const ind = `<div class="list" style="margin-top:12px">${m.indicators.map(i => `<div class="row"><span><b>${esc(i.title)}</b><br><span class="mut">${esc(i.detail)}</span></span>${pill(i.tone === 'ok' ? 'ok' : i.tone === 'warn' ? 'warn' : 'info', i.tone === 'ok' ? 'Clear' : i.tone === 'warn' ? 'Watch' : 'Info')}</div>`).join('')}</div>`;
  let aiBox = '';
  if (ai) {
    const sm = ai.summary;
    aiBox = ai.source === 'error' ? `<div class="note" style="margin-top:10px">${esc(ai.note)}</div>` : `<div class="note" style="margin-top:12px;display:grid;gap:8px;padding:12px;border:1px solid var(--line2);border-radius:12px;background:var(--panel2);color:var(--fg)">
      <div style="display:flex;gap:8px;flex-wrap:wrap;align-items:center"><span class="chip">${esc(ai.source === 'ai' ? ai.agent : 'Rule-based summary')}</span>${pill(COOLING_TONE[sm.cooling_unit] || 'info', 'Cooling unit: ' + sm.cooling_unit, false)}${pill(URGENCY_TONE[sm.urgency] || 'info', 'Urgency: ' + sm.urgency, false)}${ai.cached ? '<span class="mut" style="font-size:12px">same numbers as the last press, no new AI call</span>' : ''}</div>
      <div>${esc(sm.assessment)}</div>
      ${sm.evidence.length ? `<div class="mut" style="font-size:12.5px">Evidence: ${sm.evidence.map(esc).join(' · ')}</div>` : ''}
      <ul style="margin:0;padding-left:18px">${sm.recommended_actions.map(a => `<li>${esc(a)}</li>`).join('')}</ul>
      ${ai.note ? `<div class="mut" style="font-size:12.5px">${esc(ai.note)}</div>` : ''}
      ${reportOffers(ai)}
      <div class="mut" style="font-size:12px">An early-warning indicator from the sensor history, not a prediction.</div></div>`;
  }
  const paused = u && u.paused ? ` · AI paused: ${esc(u.paused)}` : '';
  const left = !D.state.llm ? 'AI agents are off, so you get the rule-based summary.' : u ? `Uses 1 of the ${u.daily_cap - u.used} free AI calls left today${paused}.` : '';
  const btn = `<div style="display:flex;gap:10px;align-items:center;flex-wrap:wrap;margin-top:12px"><button class="btn pri" data-act="maint-ai" ${D.maintBusy ? 'disabled' : ''}>${D.maintBusy ? 'Thinking…' : 'Summarise with AI'}</button><span class="note">Press it when you want a written review (for example once a week). ${left}</span></div>`;
  return `<div class="card" style="margin-bottom:14px">${head}<div class="b">${kpis}${ind}${days}${btn}${aiBox}</div></div>`;
}
function vMaintenance() {
  const rows = D.readingsLong.length ? D.readingsLong : D.readings, [lo, hi] = holdLimits();
  if (!rows.length) return maintReview() + `<div class="empty"><b>No sensor history yet</b>Indicators appear after the first device pings.</div>`;
  const inRange = rows.filter(r => r.temp_c >= lo && r.temp_c <= hi).length / rows.length * 100;
  const peak = Math.max(...rows.map(r => r.temp_c));
  const t = lastTelemetry(), slope = t ? t.params.slope_c_per_min : null, cur = rows[rows.length - 1].temp_c;
  const proj = slope && slope > 0 && cur < hi ? (hi - cur) / slope : null;
  const rec = recoveries(rows, lightThr()), recDone = rec.filter(r => r.minutes !== null);
  const hours = ((rows[rows.length - 1].t - rows[0].t) / 3600);
  const rising = recDone.length >= 3 && recDone[recDone.length - 1].minutes > recDone[0].minutes * 1.3;
  const series = rows.map(r => ({ t: r.t, v: r.temp_c }));
  const mx = Math.max(1, ...recDone.map(r => r.minutes));
  const bars = rec.length ? `<svg viewBox="0 0 320 190" width="100%" role="img" aria-label="Minutes to return to range after each door opening">${rec.slice(-6).map((r, i) => { const v = r.minutes, h = v === null ? 130 : v / mx * 130, x = 22 + i * 50; return `<rect x="${x}" y="${160 - h}" width="32" height="${h}" rx="6" fill="${v === null ? 'var(--crit)' : v > 6 ? 'var(--warn)' : 'var(--info)'}" opacity="${v === null ? .5 : 1}"/><text x="${x + 16}" y="${152 - h}" text-anchor="middle" font-size="11" fill="var(--fg)">${v === null ? 'open' : v.toFixed(1)}</text><text x="${x + 16}" y="178" text-anchor="middle" font-size="10.5" fill="var(--dim)">${fmtTime(r.at).slice(0, 5)}</text>`; }).join('')}<line x1="14" x2="310" y1="160" y2="160" stroke="var(--line2)"/><text x="14" y="14" font-size="10.5" fill="var(--dim)">minutes to return to range</text></svg>`
    : `<div class="empty">No door openings in this window.</div>`;
  const ind = [];
  ind.push(inRange < 90 ? ['warn', `Time in range ${inRange.toFixed(0)}% (target 90%)`, 'Investigate the excursions in the chart'] : ['ok', `Time in range ${inRange.toFixed(0)}%`, 'Within target']);
  if (rising) ind.push(['warn', 'Recovery after door openings is getting slower', 'Service the refrigeration unit before the next long haul']);
  if (slope && slope > 0.2) ind.push(['warn', `Hold warming at +${slope} °C/min`, proj ? `About ${Math.round(proj)} min to ${hi} °C if nothing changes` : 'Already above the limit']);
  if (ind.length === 1 && !rising) ind.push(['ok', 'No sign of weakening cooling', 'Keep monitoring']);
  return maintReview() + `<div class="g g4"><div class="card kpi"><div class="l">Time in range (${hours.toFixed(1)} h)</div><div class="v tn">${inRange.toFixed(1)}%</div><div>${pill(inRange >= 90 ? 'ok' : 'warn', inRange >= 90 ? 'Within 90% target' : 'Below 90% target', false)}</div></div>
    <div class="card kpi"><div class="l">Peak temperature</div><div class="v tn">${peak.toFixed(1)} °C</div><div>${pill(peak > hi ? 'crit' : 'ok', peak > hi ? `above ${hi} °C limit` : 'within limit', false)}</div></div>
    <div class="card kpi"><div class="l">Current trend</div><div class="v tn">${slope == null ? '-' : (slope > 0 ? '+' : '') + slope + ' °C/min'}</div><div>${pill(slope > 0.2 ? 'warn' : 'ok', slope > 0.2 ? 'Warming' : 'Stable', false)}</div></div>
    <div class="card kpi"><div class="l">Time to ${hi} °C at this trend</div><div class="v tn">${proj === null ? '-' : '≈ ' + Math.round(proj) + ' min'}</div><div class="note">Linear estimate, not a prediction</div></div></div>
   <div class="g g2" style="margin-top:14px"><div class="card"><div class="h"><h3>Hold temperature</h3><span class="grow"></span><span class="chip">Green band = hold limits ${lo}–${hi} °C</span></div><div class="b">${lineChart(series, { w: 760, h: 240, band: [lo, hi] })}</div></div>
     <div class="card"><div class="h"><h3>Recovery after each door opening</h3></div><div class="b">${bars}<div class="note">A rising recovery time is the early sign of a weakening refrigeration unit.</div></div></div></div>
   <div class="card" style="margin-top:14px"><div class="h"><h3>Early-warning indicators</h3><span class="grow"></span><span class="note">Computed from sensor history; not a trained model</span></div><div class="b list">
     ${ind.map(([tone, a, b]) => `<div class="row"><span><b>${esc(a)}</b><br><span class="mut">${esc(b)}</span></span>${pill(tone, tone === 'ok' ? 'Clear' : 'Watch')}</div>`).join('')}</div></div>`;
}
function vRoute() {
  const v = vehicle(), st = D.state;
  const vf = v.lat !== undefined && v.lat !== null ? routeProgressLL(v.lat, v.lon).frac : null;
  const fences = st.geofences.map(f => ({ ...f, frac: routeProgressLL(f.lat, f.lon).frac })).sort((a, b) => a.frac - b.frac);
  return `<div class="g g2"><div class="card"><div class="h"><h3>Route and geofences</h3><span class="grow"></span><span class="chip">Progress <b class="tn">${vf === null ? '-' : Math.round(vf * 100) + '%'}</b></span></div>
     <div class="b"><div class="mapgrid">${overlay()}${mapBlock()}</div></div></div>
   <div class="g start"><div class="card"><div class="h"><h3>Stops and zones</h3></div><div class="b"><div class="tl">
     ${fences.map(f => { const state = vf === null ? 'upcoming' : f.frac < vf - 0.02 ? 'passed' : f.frac <= vf + 0.02 ? 'here' : 'upcoming'; const cls = f.kind === 'risk' ? 'warn' : state === 'passed' ? 'ok' : state === 'here' ? 'crit' : ''; return `<div class="${cls}"><b>${esc(f.name)}</b> ${pill(FENCE_TONE[f.kind] || 'info', FENCE_LABEL[f.kind] || f.kind, false)}<br><span class="mut">${state === 'passed' ? 'Passed' : state === 'here' ? 'Vehicle is here' : 'Ahead'} · ${f.authorised ? 'door opening allowed' : 'door opening never expected'}</span></div>`; }).join('')}</div></div></div>
     <div class="card"><div class="h"><h3>Why geofences matter</h3></div><div class="b mut">The same light spike is routine at an authorised dock and an emergency on the highway. The agent checks the zone before it decides, and names the backup cold store when cooling fails.</div></div></div></div>`;
}
const DETECTED = { SUSPICIOUS_PERSON: 'Suspicious person', THEFT_SUSPECTED: 'Theft suspected', DOOR_AJAR: 'Door open',
  AUTHORISED_HANDLING: 'Handling at authorised stop', SPOOF_SUSPECTED: 'Door open, data suspect', LIGHT: 'Door open' };
const DETECTED_TONE = { SUSPICIOUS_PERSON: 'crit', THEFT_SUSPECTED: 'crit', DOOR_AJAR: 'warn', SPOOF_SUSPECTED: 'warn' };
function placeText(p) {
  const zone = p.zone || 'Between stops';
  return p.lat !== undefined && p.lat !== null ? `${zone} · ${(+p.lat).toFixed(4)}, ${(+p.lon).toFixed(4)}` : zone;
}
/* Incident reports (admin). Suspicious person / theft: a draft is prepared automatically and waits here to be downloaded.
   Temperature events: only after a maintenance review shows the cooling unit may be failing (the server decides). */
const reportFor = id => [...D.audit].reverse().find(e => e.kind === 'REPORT_GENERATED' && e.params && e.params.incident === id);
const downloadBtn = (r, label = 'Download report') => `<a class="btn sm pri" href="/api/reports/${encodeURIComponent(r.params.file)}" download>${label}</a>`;
function reportsCard() {
  if (!isAdmin()) return '';
  const gates = (D.state && D.state.report_gates) || {};
  const row = e => {
    const r = reportFor(e.id), gate = gates[e.id];
    const head = `<span><b>${esc(e.kind.replace(/_/g, ' '))}</b><div class="note">${fmtDateTime(e.ts)} · alert #${e.id}</div>`;
    if (r) return `<div class="row">${head}<div class="note">${r.params.automatic ? 'Prepared automatically' : 'Prepared by ' + esc(r.params.by)} · ${esc(r.params.report)} · ${r.params.pictures} picture${r.params.pictures === 1 ? '' : 's'} · SHA-256 ${esc((r.params.sha256 || '').slice(0, 12))}…</div></span>
      <span style="display:flex;gap:6px;flex-wrap:wrap">${downloadBtn(r)}<button class="btn sm" data-act="make-report" data-v="${e.id}" title="Makes a new version with you as the reporter">New version in my name</button></span></div>`;
    if (gate && !gate.ok) return `<div class="row">${head}<div class="note">No report yet: ${esc(gate.reason)}</div></span><button class="btn sm" data-act="tab" data-v="maintenance">Open Maintenance</button></div>`;
    return `<div class="row">${head}</span><button class="btn sm pri" data-act="make-report" data-v="${e.id}">Prepare incident report</button></div>`;
  };
  const rows = incidents().slice(0, 8).map(row).join('');
  return `<div class="card" style="margin-bottom:14px"><div class="h"><h3>Incident reports</h3><span class="grow"></span><span class="chip">Word file from your template</span></div>
   <div class="b list">${rows || '<div class="note">Reports appear here once an alert has been raised.</div>'}
   <div class="note">Suspicious person and theft alerts get a draft report automatically. A temperature event only gets one when a maintenance review (Maintenance tab → Summarise with AI) finds the cooling unit may be failing. Every report is an AI-assisted draft: review and sign it before it is sent.</div></div></div>`;
}
/* Evidence: one row per picture the camera saved: time, location, what was detected. */
function vEvidence() {
  const shots = D.audit.filter(e => e.kind === 'EVIDENCE_CAPTURED' && e.params && e.params.file).reverse();
  if (!shots.length) return reportsCard() + `<div class="empty"><b>No pictures yet</b>When the door opens or a person is detected, the camera saves a picture here with the time and location.</div>`;
  return reportsCard() + `<div class="card"><div class="h"><h3>Evidence</h3><span class="grow"></span><span class="chip">${shots.length} picture${shots.length > 1 ? 's' : ''}</span></div>
   <div class="b"><div class="evlist">${shots.map(e => {
     const p = e.params, what = p.detected || e.trigger || 'Camera';
     const url = `/api/evidence/${encodeURIComponent(p.file)}`;
     return `<div class="evrow"><a class="evpic" href="${url}" target="_blank" rel="noopener" title="Open full size"><img src="${url}" alt="${esc(DETECTED[what] || what)} at ${fmtDateTime(e.ts)}" loading="lazy"></a>
       <dl class="evmeta"><div><dt>Time</dt><dd class="tn">${fmtDateTime(e.ts)}</dd></div>
       <div><dt>Location</dt><dd>${esc(placeText(p))}</dd></div>
       <div><dt>Detected</dt><dd>${pill(DETECTED_TONE[what] || 'info', DETECTED[what] || what.replace(/_/g, ' ').toLowerCase())}</dd></div></dl></div>`;
   }).join('')}</div>
   <div class="note" style="margin-top:10px">Pictures are stored with a SHA-256 fingerprint so later changes would show. Open one for full size.</div></div></div>`;
}

/* Speed: the node has no GPS, so the demo sets speed here; with a GPS feed this shows the real value. */
function speedCard() {
  const v = vehicle(), sp = v.speed_kmh ?? 0, onRoute = v.route_status === 'En route';
  const risk = onRoute && sp < 2 && !v.authorised_stop;
  return `<div class="card"><div class="h"><h3>Vehicle speed</h3><span class="grow"></span>${pill(onRoute ? (risk ? 'warn' : 'ok') : 'mut', onRoute ? (risk ? 'Stopped on route' : 'On route') : (v.route_status || 'Route not active'), !!onRoute)}</div>
   <div class="b"><div style="display:grid;grid-template-columns:150px 1fr;gap:14px;align-items:center">${gauge({ v: sp, min: 0, max: 140, unit: 'km/h', label: 'Speed', color: risk ? 'var(--warn)' : 'var(--accent)' })}
     <div class="list"><div class="mut" style="font-size:12.5px">Set speed (demo, no GPS on the node). Any speed above 0 activates the route.</div>
       <div style="display:flex;gap:6px;flex-wrap:wrap">${[0, 50, 90].map(x => `<button class="btn sm ${Math.round(sp) === x ? 'pri' : ''}" data-act="speed" data-v="${x}">${x === 0 ? 'Stop' : x + ' km/h'}</button>`).join('')}</div>
       ${onRoute ? '' : '<button class="btn sm" data-act="vaction" data-v="activate">Activate Route</button>'}</div></div>
   <div class="note" style="margin-top:8px">Rule: if the hold lights up while the truck is stopped on an active route (outside a depot or dock), the agent treats it as possible theft.</div></div></div>`;
}

/* ============================== chrome ============================== */
const TABS = [['overview', 'Overview'], ['cargo', 'Cargo'], ['maintenance', 'Maintenance'], ['route', 'Route'], ['evidence', 'Evidence']];
/* Hold chip popover: "Mock temperature" checkbox; ticked shows a slider, unticked uses the sensor. */
/* Says exactly which data the dashboard is showing, including the short wait while the node catches up. */
/* Mock controls for two sensors. Each sensor has a top-bar chip that opens a popover: a "Mock ..." checkbox and,
   when ticked, a slider. Unticked means the node uses its real sensor. */
const MOCKS = {
  temp: { menu: 'mock', pre: 'mock', on: 'mock', val: 'temp_c', sim: 'simulated_temp', min: 0, max: 40, unit: ' °C',
          title: 'Hold temperature', label: 'Mock temperature', noun: 'temperature', hint: '2–8 °C safe',
          live: () => climate().temp, text: v => `${v} °C` },
  light: { menu: 'mocklight', pre: 'light', on: 'light_mock', val: 'light_pct', sim: 'simulated_light', min: 0, max: 100, unit: ' %',
           title: 'Cargo light', label: 'Mock light', noun: 'light', hint: '30 % = door-open alarm',
           live: () => climate().light, text: v => `${Math.round(v)} %` },
};
/* Per sensor: real | live (mock on and the node is sending it) | waiting-on (ticked, node has not caught up) | waiting-off (unticked, still showing old mock). */
function mockStates() {
  const d = (D.state && D.state.demo) || {};
  const out = {};
  for (const [k, m] of Object.entries(MOCKS)) {
    const on = !!d[m.on], sim = !!d[m.sim];
    out[k] = on ? (sim ? 'live' : 'waiting-on') : (sim ? 'waiting-off' : 'real');
  }
  return out;
}
const isMocked = k => ['live', 'waiting-off'].includes(mockStates()[k]);   // the value on screen is a mock value
function renderDemoBanner() {
  const st = mockStates(), el = $('#demoBanner');
  const live = Object.keys(MOCKS).filter(k => st[k] === 'live').map(k => MOCKS[k].noun);
  const parts = [];
  if (live.length) parts.push(`<b>MOCK DATA.</b> The ${live.join(' and ')} ${live.length > 1 ? 'are' : 'is'} set from the dashboard (top bar), not measured by the sensor. Door and camera are real.`);
  for (const k of Object.keys(MOCKS)) {
    if (st[k] === 'waiting-on') parts.push(`<b>Mock ${MOCKS[k].noun} is on.</b> Waiting for the node to send it. (If this stays, upload the latest firmware.ino.)`);
    if (st[k] === 'waiting-off') parts.push(`<b>Mock ${MOCKS[k].noun} is off.</b> Waiting for a real ${MOCKS[k].noun} reading; the value shown is still the last mock one.`);
  }
  const msg = parts.join(' · ');
  el.hidden = !msg;
  if (msg && el.dataset.msg !== msg) { el.innerHTML = msg; el.dataset.msg = msg; }
}
function lastMessageAge() {
  const t = lastTelemetry(); if (!t) return null;
  const a = Date.now() / 1000 - t.ts;
  return a >= 0 && a < 3600 ? Math.round(a) : null;
}
function ageText() {
  const a = lastMessageAge(), t = lastTelemetry();
  if (a === null || !t) return 'No message from the node yet';
  const mocked = (t.params.mocked && t.params.mocked.length) ? t.params.mocked.join(' + ') + ' mock' : (t.params.simulated ? 'temp mock' : 'real sensors');
  return `Last message from the node ${a < 90 ? a + ' s' : Math.round(a / 60) + ' min'} ago (${mocked})`;
}
setInterval(() => { if (!D.state) return; for (const m of Object.values(MOCKS)) { const el = $(`#${m.pre}Age`); if (el) el.textContent = ageText(); } }, 1000);
const mockTimers = {};
function sendMock(kind, body) {
  return post('/api/demo', { by: S.me, ...body }).then(d => { if (D.state) D.state.demo = { ...D.state.demo, ...d }; renderChrome(); })
    .catch(err => { toast('crit', MOCKS[kind].label, err.message); refreshState(); });
}
const mockBody = (kind, on, value, log = true) => kind === 'temp'
  ? { mock: on, temp_c: value, log } : { light_mock: on, light_pct: value, log };
function renderMockPop(kind) {
  const m = MOCKS[kind], p = m.pre, pop = $('#pop-' + m.menu), d = D.state && D.state.demo;
  if (!d) return;
  const sl = $(`#${p}Slider`), cur = m.live();
  const on = !!d[m.on], val = d[m.val];
  if (sl && S.menu === m.menu) {                       // open: refresh values in place so a drag is never interrupted
    $(`#${p}Live`).textContent = cur === null ? '-' : m.text(cur);
    $(`#${p}Age`).textContent = ageText();
    if (document.activeElement !== sl) { sl.value = val; $(`#${p}Val`).textContent = m.text(val); }
    const box = $(`#${p}Box`); if (document.activeElement !== box) { box.checked = on; $(`#${p}SliderWrap`).hidden = !on; }
    return;
  }
  pop.innerHTML = `<div style="display:grid;gap:12px">
    <div style="display:flex;justify-content:space-between;align-items:baseline"><b>${m.title}</b><span class="mut" style="font-size:12.5px">now <b class="tn" id="${p}Live" style="color:var(--fg)">${cur === null ? '-' : m.text(cur)}</b></span></div>
    <label style="display:flex;gap:9px;align-items:center;${d.enabled ? '' : 'opacity:.55'}"><input type="checkbox" id="${p}Box" ${on ? 'checked' : ''} ${d.enabled ? '' : 'disabled'}> ${m.label}</label>
    <div id="${p}SliderWrap" ${on ? '' : 'hidden'}><div style="display:flex;justify-content:space-between;font-size:12.5px;margin-bottom:6px"><span class="mut">Mock value</span><b class="tn" id="${p}Val">${m.text(val)}</b></div>
      <input type="range" id="${p}Slider" min="${m.min}" max="${m.max}" step="1" value="${val}" style="width:100%" aria-label="${m.label}">
      <div class="mut tn" style="display:flex;justify-content:space-between;font-size:11px"><span>${m.min}${m.unit}</span><span>${m.hint}</span><span>${m.max}${m.unit}</span></div></div>
    <div class="note" id="${p}Age" style="margin-top:-4px">${ageText()}</div>
    <div class="note">${d.enabled ? (on ? `The node reports this value instead of its ${m.noun} sensor. Readings are labelled as mock.` : `Unticked: the ${m.noun} comes from the sensor.`) : 'Mock data is off on this server. Start the backend with <span class="mono">--demo</span> to allow it.'}</div></div>`;
}
function renderChrome() {
  renderRole();
  $('#connBanner').hidden = D.connected || !D.wsDropped;
  renderDemoBanner();
  $$('.rail button[data-act=tab]').forEach(b => b.classList.toggle('on', b.dataset.v === S.tab));
  $$('.seg [data-act=module]').forEach(b => { b.classList.toggle('on', b.dataset.v === S.module); b.setAttribute('aria-pressed', b.dataset.v === S.module); });
  const vb = $('#voiceBtn'); vb.classList.toggle('on', S.voice); vb.setAttribute('aria-pressed', S.voice); vb.title = `Alarm sound (${S.voice ? 'on' : 'off'})`;
  $('#meName').textContent = S.me; $('#meAv').textContent = S.me.split(/\s+/).map(w => w[0]).join('').slice(0, 2).toUpperCase();
  if (!D.state) return;
  const c = counts(), cl = climate(), w = worst(), f = fleet(), v = vehicle(), al = activeAlerts();
  $('#holdBtn').innerHTML = `Hold <b class="tn">${cl.temp === null ? '-' : cl.temp + ' °C'}</b>${isMocked('temp') ? ' <span class="simtag">mock</span>' : ''} <span aria-hidden="true">▾</span>`;
  $('#holdBtn').setAttribute('aria-expanded', S.menu === 'mock');
  $('#lightBtn').innerHTML = `Light <b class="tn">${cl.light === null ? '-' : Math.round(cl.light) + '%'}</b>${isMocked('light') ? ' <span class="simtag">mock</span>' : ''} <span aria-hidden="true">▾</span>`;
  $('#lightBtn').setAttribute('aria-expanded', S.menu === 'mocklight');
  renderMockPop('temp'); renderMockPop('light');
  $('#kpis').innerHTML = `<span class="chip">Cleared <b class="tn">${c.NORMAL}/${packages().length}</b></span><span class="chip"><span class="dot ${D.connected ? 'on' : D.wsDropped ? 'off' : ''}"></span><b>${D.connected ? 'Live' : D.wsDropped ? 'Reconnecting' : 'Connecting'}</b></span>`;
  $('#crumbV').textContent = f.id; $('#vId').textContent = f.id;
  $('#vstat').innerHTML = pill(TONE[w], LABEL[w]);
  $('#vSub').textContent = `${f.model} · ${f.route.from} to ${f.route.to} · ${v.route_status || 'Idle'}`;
  $('#ticker').innerHTML = al.length ? al.map(a => `<span class="a">${pill(SEV_TONE[a.severity], a.severity.toUpperCase(), false)}<b>${esc(a.kind.replace(/_/g, ' '))}</b><span class="mut">${esc(a.reasoning.slice(0, 90))}</span><button class="btn sm" data-act="ack" data-v="${a.id}">Acknowledge</button>${isAdmin() && reportFor(a.id) ? downloadBtn(reportFor(a.id)) : ''}</span>`).join('')
    : `<span class="mut">No active alerts. Hold ${cl.temp === null ? '-' : cl.temp + ' °C'}, light ${cl.light === null ? '-' : Math.round(cl.light) + '%'}.</span>`;
  const b = $('#bellN'); b.hidden = !al.length; b.textContent = al.length;
  $('#tabs').innerHTML = TABS.map(([k, t]) => `<button role="tab" aria-selected="${S.tab === k}" class="${S.tab === k ? 'on' : ''}" data-act="tab" data-v="${k}">${t}${k === 'cargo' && c.QUARANTINED ? `<span class="c">${c.QUARANTINED}</span>` : k === 'evidence' && al.length ? `<span class="c">${al.length}</span>` : ''}</button>`).join('');
  const notified = D.audit.filter(e => e.notified).slice(-8).reverse();
  $('#pop-bell').innerHTML = `<div class="mut" style="padding:6px 10px;font-size:12px">Recent alerts</div>` + (notified.length ? notified.map(e => `<button class="it" data-act="inc-open" data-v="${e.id}"><span><b style="font-weight:600">${esc(e.kind.replace(/_/g, ' '))}</b><br><span class="mut" style="font-size:12px">${esc(e.reasoning.slice(0, 90))}</span></span><span class="mono dim">${fmtTime(e.ts)}</span></button>`).join('') : '<div class="it mut">Nothing yet.</div>');
  $('#pop-profile').innerHTML = `${AUTH.auth ? `<div class="it" style="display:grid;gap:2px"><span class="mut" style="font-size:12px">Signed in (recorded in the audit log)</span><b>${esc(AUTH.name)}</b><span class="mut">${esc(AUTH.id)} · ${AUTH.role === 'admin' ? 'Admin: full control' : 'Driver: view, activate the route, acknowledge'}</span></div><button class="it" data-act="logout"><span>Sign out</span><span class="mut">ends this session</span></button>` : `<div class="it" style="display:grid;gap:6px"><label for="meInput" class="mut" style="font-size:12px">Your name (recorded on acknowledgements and overrides)</label><input id="meInput" class="field" value="${esc(S.me)}" maxlength="60"></div>`}
    <button class="it" data-act="voice"><span>Alarm sound + voice</span><span class="mut">${S.voice ? 'On' : 'Off'}</span></button>
    <button class="it" data-act="theme"><span>Switch theme</span><span class="mut">Dark / light</span></button>`;
  ['bell', 'profile', 'mock', 'mocklight'].forEach(k => { $('#pop-' + k).hidden = S.menu !== k; });
  applyRoleLocks();
}
function renderView() {
  if (!D.state) return;
  const a = document.activeElement;
  if (a && $('#view').contains(a) && /^(INPUT|SELECT|TEXTAREA)$/.test(a.tagName) && a.type !== 'checkbox') { S.pendingRender = true; return; }
  const term = $('#term');
  if (term) { S.termStick = term.scrollTop + term.clientHeight >= term.scrollHeight - 30; S.termTop = term.scrollTop; }
  const v = { overview: vOverview, cargo: vCargo, maintenance: vMaintenance, route: vRoute, evidence: vEvidence }[S.tab] || vOverview;
  $('#view').innerHTML = v();
  const slot = $('#camslot');
  if (slot) slot.appendChild(camImg);
  try { mountMap(); } catch (e) { console.error('map', e); }
  applyRoleLocks();
  const t = $('#term');
  if (t) t.scrollTop = S.termStick ? t.scrollHeight : S.termTop;
}
function render() { renderChrome(); renderView(); }

/* ============================== alerts: toast + voice ============================== */
function toast(sev, title, text) {
  const el = document.createElement('div');
  el.className = 't ' + sev; el.innerHTML = `<b>${esc(title)}</b><div class="mut">${esc(text)}</div>`;
  $('#toast').appendChild(el); setTimeout(() => el.remove(), 7000);
}
function speak(text) {
  if (!S.voice || !text) return;
  try { speechSynthesis.cancel(); speechSynthesis.speak(new SpeechSynthesisUtterance(text)); } catch { /* no speech */ }
}
/* Browsers only allow sound after the first click or tap on the page. */
let audioCtx = null;
function unlockAudio() {
  try {
    audioCtx = audioCtx || new (window.AudioContext || window.webkitAudioContext)();
    if (audioCtx.state !== 'running') audioCtx.resume();
  } catch { /* no Web Audio */ }
  const b = $('#soundBanner'); if (b && audioCtx && audioCtx.state === 'running') b.hidden = true;
}
document.addEventListener('pointerdown', unlockAudio);
document.addEventListener('keydown', unlockAudio);
function siren(seconds) {
  if (!audioCtx || audioCtx.state !== 'running') return 0;
  const o = audioCtx.createOscillator(), g = audioCtx.createGain(), t = audioCtx.currentTime;
  o.type = 'square'; g.gain.value = 0.05;
  o.connect(g); g.connect(audioCtx.destination);
  for (let i = 0; i < seconds * 2; i++) { o.frequency.setValueAtTime(880, t + i * 0.5); o.frequency.setValueAtTime(660, t + i * 0.5 + 0.25); }
  o.start(t); o.stop(t + seconds);
  return seconds * 1000;
}
function alarm(e) {
  if (!S.voice) return;
  if (!audioCtx || audioCtx.state !== 'running') { $('#soundBanner').hidden = false; return; }
  const ms = siren(e.severity === 'emergency' ? 3 : 1.5);
  setTimeout(() => speak(e.voice || `${e.kind.replace(/_/g, ' ').toLowerCase()} detected`), ms);
}
const repeating = new Set();
function repeatUntilAcknowledged(e, left = 5) {   // theft keeps sounding every 20 s until someone acknowledges it
  if (repeating.has(e.id)) return;
  repeating.add(e.id);
  const tick = n => setTimeout(() => {
    const cur = D.audit.find(x => x.id === e.id);
    if (!cur || cur.acked_by || n <= 0) { repeating.delete(e.id); return; }
    alarm(cur); tick(n - 1);
  }, 20000);
  tick(left);
}
function announce(e) {
  if (!e.notified || S.toasted.has(e.id)) return;
  S.toasted.add(e.id);
  toast(e.severity === 'info' ? 'info' : e.severity === 'warning' ? 'warning' : 'crit', e.kind.replace(/_/g, ' '), e.reasoning.slice(0, 160));
  if (!S.spoken.has(e.id)) {
    S.spoken.add(e.id);
    alarm(e);
    if (e.severity === 'emergency') repeatUntilAcknowledged(e);
  }
}

/* ============================== actions ============================== */
async function act(fn, okTitle, okText) {
  try { await fn(); if (okTitle) toast('ok', okTitle, okText || ''); refreshState(); }
  catch (err) { toast('crit', 'That did not work', err.message); }
}
document.addEventListener('click', e => {
  const el = e.target.closest('[data-act]');
  if (!e.target.closest('.relw') && S.menu) { S.menu = null; renderChrome(); }
  if (!el) return;
  const a = el.dataset.act, v = el.dataset.v;
  if (a === 'tab') { S.tab = v; S.menu = null; store.set('tab', v); if (v === 'maintenance') { loadLong(); loadMaint(); } render(); window.scrollTo({ top: 0 }); }
  else if (a === 'pick') {
    S.sel = S.sel === v && S.tab === 'cargo' && !el.classList.contains('slot') ? null : v; S.reassign = false; S.tab = 'cargo'; store.set('tab', 'cargo'); $('#qres').hidden = true; render();
    const card = $('.pk.sel'); if (card) card.scrollIntoView({ block: 'nearest', behavior: matchMedia('(prefers-reduced-motion: reduce)').matches ? 'auto' : 'smooth' });
  }
  else if (a === 'filter') { S.filter = v; renderView(); }
  else if (a === 'logout') doLogout();
  else if (a === 'mrange') { S.mrange = +v; store.set('mrange', S.mrange); D.maint = null; D.maintAI = null; render(); loadMaint(); }
  else if (a === 'maint-ai') { if (!D.maintBusy) summariseMaint(); }
  else if (a === 'clearq') { S.q = ''; $('#q').value = ''; renderView(); }
  else if (a === 'module') { S.module = v; store.set('module', v); render(); }
  else if (a === 'mt') { S[v] = !S[v]; renderView(); }
  else if (a === 'alog') { S.alogOpen = !S.alogOpen; renderView(); }
  else if (a === 'voice') { S.voice = !S.voice; store.set('voice', S.voice); if (S.voice) { unlockAudio(); speak('Alarm sound on.'); } else { try { speechSynthesis.cancel(); } catch { /* none */ } } render(); }
  else if (a === 'menu') { S.menu = S.menu === v ? null : v; renderChrome(); }
  else if (a === 'theme') { const r = document.documentElement, dark = matchMedia('(prefers-color-scheme: dark)').matches; const curDark = r.dataset.theme ? r.dataset.theme === 'dark' : dark; r.dataset.theme = curDark ? 'light' : 'dark'; store.set('theme', r.dataset.theme); S.menu = null; render(); }
  else if (a === 'make-report') act(() => post(`/api/incidents/${v}/report`, { by: S.me }), null);   // the live message announces it
  else if (a === 'appr-ok' || a === 'appr-no') {
    const item = ((D.state && D.state.approvals) || []).find(i => String(i.id) === v);
    if (!item) return;
    const yes = a === 'appr-ok';
    if (yes && item.action !== 'PREPARE_REPORT' && !confirm(`${item.label}?\n\n${item.reason}\n\nThis is logged under your name.`)) return;
    act(() => post(`/api/approvals/${v}/decide`, { approve: yes, by: S.me }), yes ? 'Approved' : 'Rejected', `${item.label} - recorded in the audit log.`);
  }
  else if (a === 'ack') act(() => post(`/api/audit/${v}/ack`, { by: S.me }), 'Acknowledged', 'Recorded in the audit log.');
  else if (a === 'inc-open') { S.tab = 'evidence'; S.menu = null; render(); }
  else if (a === 'speed') act(() => post('/api/vehicle', { speed_kmh: +v }), null);
  else if (a === 'vaction') act(() => post('/api/vehicle/action', { action: v, by: S.me }), 'Vehicle updated', { maintenance: 'Maintenance requested.', offline: 'Vehicle marked offline.', activate: 'Route active.' }[v]);
  else if (a === 'scan') act(async () => { const r = await api(`/api/custody/${encodeURIComponent(v)}`); toast(r.valid ? 'ok' : 'crit', r.valid ? 'Scan accepted' : 'Scan rejected', r.valid ? `${r.package} can be handed over.` : `${r.package || ''} ${r.reason}`); });
  else if (a === 'reassign') { S.reassign = !S.reassign; renderView(); }
  else if (a === 'reassign-ok') {
    const reason = ($('#rreason').value || '').trim(), truck = $('#rtruck').value.split(' ')[0];
    if (reason.length < 3) { toast('warning', 'Reason required', 'Say why the package is moving (3+ characters).'); $('#rreason').focus(); return; }
    S.reassign = false; act(() => post(`/api/package/${encodeURIComponent(v)}/reassign`, { truck, reason, by: S.me }), 'Transfer requested', `${v} → ${truck}`);
  } else if (a === 'ovs') { S.ovStatus = v; renderView(); }
  else if (a === 'ovr-ok') {
    const reason = ($('#ovr').value || '').trim();
    if (reason.length < 3) { toast('warning', 'Reason required', 'Enter at least 3 characters so the override can be audited.'); $('#ovr').focus(); return; }
    act(() => post(`/api/package/${encodeURIComponent(v)}/override`, { status: S.ovStatus, reason, supervisor: S.me }), 'Override logged', `${v} is now ${LABEL[S.ovStatus]}`);
  }
});
const kindOf = id => (id === 'mockSlider' || id === 'mockBox') ? 'temp' : (id === 'lightSlider' || id === 'lightBox') ? 'light' : null;
document.addEventListener('input', e => {
  const kind = kindOf(e.target.id);
  if (!kind || !e.target.id.endsWith('Slider')) return;
  const m = MOCKS[kind];
  $(`#${m.pre}Val`).textContent = m.text(+e.target.value);
  clearTimeout(mockTimers[kind]);                       // follow the slider while dragging, without flooding the log
  mockTimers[kind] = setTimeout(() => sendMock(kind, mockBody(kind, true, +e.target.value, false)), 250);
});
document.addEventListener('change', e => {
  const kind = kindOf(e.target.id);
  if (kind && e.target.id.endsWith('Box')) {
    const m = MOCKS[kind], sl = $(`#${m.pre}Slider`), on = e.target.checked;
    $(`#${m.pre}SliderWrap`).hidden = !on;
    sendMock(kind, mockBody(kind, on, sl ? +sl.value : undefined));
  }
  if (kind && e.target.id.endsWith('Slider')) { clearTimeout(mockTimers[kind]); sendMock(kind, mockBody(kind, true, +e.target.value)); }
  if (e.target.id === 'meInput') { const n = e.target.value.trim(); if (n) { S.me = n.slice(0, 60); store.set('me', S.me); renderChrome(); } }
});
document.addEventListener('focusout', () => { if (S.pendingRender) setTimeout(() => { const a = document.activeElement; if (!(a && $('#view').contains(a) && /^(INPUT|SELECT|TEXTAREA)$/.test(a.tagName))) { S.pendingRender = false; renderView(); } }, 0); });
const q = $('#q'), qres = $('#qres');
q.addEventListener('input', () => {
  S.q = q.value.trim().toLowerCase();
  if (S.tab === 'cargo') renderView();
  if (!S.q) { qres.hidden = true; return; }
  const r = packages().filter(matches).slice(0, 6);
  qres.innerHTML = r.length ? r.map(p => `<button class="it" data-act="pick" data-v="${esc(p.id)}"><span><b class="mono">${esc(p.id)}</b> ${esc(p.description)}<br><span class="mut" style="font-size:12px">${esc(p.client)} · ${esc(p.destination)}</span></span>${pill(TONE[p.status], SHORT[p.status], false)}</button>`).join('') : '<div class="it mut">No match.</div>';
  qres.hidden = false;
});
document.addEventListener('keydown', e => {
  if ((e.ctrlKey || e.metaKey) && e.key.toLowerCase() === 'k') { e.preventDefault(); q.focus(); }
  if (e.key === 'Escape') { qres.hidden = true; S.menu = null; renderChrome(); }
});
document.addEventListener('click', e => { if (!e.target.closest('.search')) qres.hidden = true; });

/* ============================== boot ============================== */
(() => { const t = store.get('theme', null); if (t) document.documentElement.dataset.theme = t; })();
$('#loginForm').addEventListener('submit', e => { e.preventDefault(); doLogin(); });
renderChrome();
startApp();
