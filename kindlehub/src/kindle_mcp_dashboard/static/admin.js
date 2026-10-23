'use strict';

/* KindleHub 管理后台
 * 交互设计参考 zihengjackchen/kindle-dashboard 的 preview.html + editor.js（MIT，见 NOTICE），
 * 按其 widget 模型改写为适配本项目的 region 模型，并加入拖拽定位。
 */

const CANVAS_W = 1000;

let meta = null;
let state = null;
let selectedId = null;
let previewUrl = null;
let previewTimer = null;
let displayScale = 1;
let drag = null;

const $ = (id) => document.getElementById(id);
const canvasEl = $('canvas');
const overlayEl = $('overlay');
const previewEl = $('preview');

const clamp = (v, lo, hi) => Math.min(hi, Math.max(lo, v));
const canvasH = () => CANVAS_W * state.device.height / state.device.width;

function currentScreen() {
  return state.screens.find((s) => s.id === state.active_screen) || state.screens[0];
}

function selectedRegion() {
  return currentScreen().regions.find((r) => r.id === selectedId) || null;
}

function setStatus(msg, kind) {
  const el = $('status');
  el.textContent = msg || '';
  el.className = 'status' + (kind ? ' ' + kind : '');
  if (msg) {
    clearTimeout(setStatus._t);
    setStatus._t = setTimeout(() => { el.textContent = ''; el.className = 'status'; }, 4000);
  }
}

/* ---------- 画布尺寸 ---------- */

function layoutCanvas() {
  const stage = document.querySelector('.stage');
  const availW = Math.max(120, stage.clientWidth - 32);
  const availH = Math.max(120, stage.clientHeight - 48);
  displayScale = Math.min(availW / CANVAS_W, availH / canvasH());
  canvasEl.style.width = Math.round(CANVAS_W * displayScale) + 'px';
  canvasEl.style.height = Math.round(canvasH() * displayScale) + 'px';
  renderOverlay();
}

/* ---------- 渲染 ---------- */

function renderScreenSelect() {
  const sel = $('screenSelect');
  sel.innerHTML = '';
  for (const s of state.screens) {
    const opt = document.createElement('option');
    opt.value = s.id;
    opt.textContent = s.name || s.id;
    if (s.id === state.active_screen) opt.selected = true;
    sel.appendChild(opt);
  }
  $('screenName').value = currentScreen().name || '';
}

function renderList() {
  const ul = $('regionList');
  ul.innerHTML = '';
  for (const r of currentScreen().regions) {
    const li = document.createElement('li');
    if (r.id === selectedId) li.className = 'active';
    li.dataset.id = r.id;

    const name = document.createElement('span');
    name.className = 'name';
    name.textContent = r.label || r.id;

    const tag = document.createElement('span');
    tag.className = 'tag';
    tag.textContent = r.kind;

    const del = document.createElement('button');
    del.className = 'del';
    del.textContent = '×';
    del.title = '删除模块';
    del.addEventListener('click', (e) => { e.stopPropagation(); removeRegion(r.id); });

    li.append(name, tag, del);
    li.addEventListener('click', () => { selectedId = r.id; renderList(); renderOverlay(); renderProps(); });
    ul.appendChild(li);
  }
}

function renderOverlay() {
  overlayEl.innerHTML = '';
  if (!state) return;
  const s = displayScale;
  for (const r of currentScreen().regions) {
    const box = document.createElement('div');
    box.className = 'region-box' + (r.id === selectedId ? ' selected' : '');
    box.dataset.id = r.id;
    box.style.left = (r.x * s) + 'px';
    box.style.top = (r.y * s) + 'px';
    box.style.width = (r.w * s) + 'px';
    box.style.height = (r.h * s) + 'px';

    const tag = document.createElement('span');
    tag.className = 'tag';
    tag.textContent = r.label || r.id;
    box.appendChild(tag);

    const handle = document.createElement('span');
    handle.className = 'handle';
    box.appendChild(handle);

    box.addEventListener('mousedown', (e) => {
      if (e.target === handle) return;
      e.preventDefault();
      selectedId = r.id;
      renderList(); renderProps();
      overlayEl.querySelectorAll('.region-box').forEach((b) => b.classList.toggle('selected', b.dataset.id === r.id));
      drag = { region: r, box, mode: 'move', startX: e.clientX, startY: e.clientY, orig: { x: r.x, y: r.y, w: r.w, h: r.h } };
    });

    handle.addEventListener('mousedown', (e) => {
      e.preventDefault();
      e.stopPropagation();
      selectedId = r.id;
      renderList(); renderProps();
      overlayEl.querySelectorAll('.region-box').forEach((b) => b.classList.toggle('selected', b.dataset.id === r.id));
      drag = { region: r, box, mode: 'resize', startX: e.clientX, startY: e.clientY, orig: { x: r.x, y: r.y, w: r.w, h: r.h } };
    });

    overlayEl.appendChild(box);
  }
}

/* ---------- 属性面板 ---------- */

function field(label, input) {
  const wrap = document.createElement('label');
  const span = document.createElement('span');
  span.textContent = label;
  wrap.append(span, input);
  return wrap;
}

function textInput(value, onInput, type) {
  const el = document.createElement('input');
  el.type = type || 'text';
  if (type === 'number') { el.min = '0'; el.step = '1'; }
  el.value = value === undefined || value === null ? '' : value;
  el.addEventListener('input', () => onInput(el.value));
  return el;
}

function selectInput(options, value, onChange) {
  const el = document.createElement('select');
  for (const [val, text] of options) {
    const o = document.createElement('option');
    o.value = val;
    o.textContent = text;
    if (val === value) o.selected = true;
    el.appendChild(o);
  }
  el.addEventListener('change', () => onChange(el.value));
  return el;
}

function checkbox(text, value, onChange) {
  const wrap = document.createElement('label');
  wrap.className = 'check';
  const el = document.createElement('input');
  el.type = 'checkbox';
  el.checked = !!value;
  el.addEventListener('change', () => onChange(el.checked));
  const span = document.createElement('span');
  span.textContent = text;
  wrap.append(el, span);
  return wrap;
}

function itemsToText(items) {
  return (items || []).map((it) => {
    if (typeof it === 'string') return it;
    return (it.text || '') + (it.status ? '|' + it.status : '');
  }).join('\n');
}

function textToItems(text) {
  return text.split('\n').map((l) => l.trim()).filter(Boolean).map((line) => {
    const idx = line.lastIndexOf('|');
    if (idx > 0) {
      const t = line.slice(0, idx).trim();
      const s = line.slice(idx + 1).trim();
      if (s) return { text: t, status: s };
    }
    return line;
  });
}

function renderProps() {
  const body = $('propBody');
  body.innerHTML = '';
  const r = selectedRegion();
  if (!r) {
    body.className = 'muted';
    body.textContent = '未选中模块';
    return;
  }
  body.className = '';

  body.appendChild(field('标识 id', textInput(r.id, (v) => {
    const old = r.id;
    if (!v || currentScreen().regions.some((x) => x.id === v && x !== r)) { setStatus('id 不能为空或重复', 'err'); return; }
    r.id = v; selectedId = v;
    renderList(); renderOverlay();
    return old;
  })));

  body.appendChild(field('类型 kind', selectInput(
    meta.kinds.map((k) => [k, k]), r.kind,
    (v) => { r.kind = v; renderList(); renderProps(); refreshPreview(); })));

  body.appendChild(field('标题 label', textInput(r.label, (v) => {
    r.label = v; renderList(); renderOverlay(); refreshPreview();
  })));

  if (r.kind === 'text' || r.kind === 'metric' || r.kind === 'status') {
    const ta = document.createElement('textarea');
    ta.value = r.value || '';
    ta.addEventListener('input', () => { r.value = ta.value; refreshPreview(); });
    body.appendChild(field('内容 value（可多行）', ta));

    const opts = [['', '（不绑定）']].concat(meta.sources.map((s) => [s.id, s.label + ' — ' + s.id]));
    body.appendChild(field('数据源 source', selectInput(opts, r.source || '', (v) => {
      if (v) r.source = v; else delete r.source;
      refreshPreview();
    })));
  }

  if (r.kind === 'progress') {
    const opts = [['', '（不绑定）']].concat(meta.sources.map((s) => [s.id, s.label + ' — ' + s.id]));
    body.appendChild(field('数据源 source', selectInput(opts, r.source || '', (v) => {
      if (v) r.source = v; else delete r.source;
      refreshPreview();
    })));
  }

  if (r.kind === 'list') {
    const ta = document.createElement('textarea');
    ta.style.minHeight = '90px';
    ta.value = itemsToText(r.items);
    ta.addEventListener('input', () => { r.items = textToItems(ta.value); refreshPreview(); });
    body.appendChild(field('列表项（每行一项，可写 文本|状态）', ta));
    const hint = document.createElement('p');
    hint.className = 'hintline';
    hint.textContent = '状态可用：ok / running / healthy 表示在线，其余显示空心圆点。';
    body.appendChild(hint);
  }

  if (r.kind === 'image') {
    body.appendChild(field('图片路径（Mac 本地绝对路径）', textInput(r.image || '', (v) => {
      r.image = v; refreshPreview();
    })));
    body.appendChild(checkbox('裁掉白边', r.trim, (v) => { r.trim = v; refreshPreview(); }));
  }

  body.appendChild(field('字号 size（像素）', textInput(r.size, (v) => {
    if (v === '') delete r.size; else r.size = Number(v);
    refreshPreview();
  }, 'number')));

  body.appendChild(field('图标 icon', selectInput(
    [['', '无'], ['weather', '天气'], ['cpu', 'CPU'], ['memory', '内存'], ['disk', '磁盘'], ['agent', 'Agent']],
    r.icon || '', (v) => { if (v) r.icon = v; else delete r.icon; refreshPreview(); })));

  body.appendChild(field('对齐 align', selectInput(
    [['left', '左'], ['center', '中'], ['right', '右']], r.align || 'left',
    (v) => { r.align = v; refreshPreview(); })));

  const row = document.createElement('div');
  row.className = 'row';
  row.appendChild(field('x', textInput(r.x, (v) => { r.x = Number(v) || 0; renderOverlay(); refreshPreview(); }, 'number')));
  row.appendChild(field('y', textInput(r.y, (v) => { r.y = Number(v) || 0; renderOverlay(); refreshPreview(); }, 'number')));
  body.appendChild(row);

  const row2 = document.createElement('div');
  row2.className = 'row';
  row2.appendChild(field('宽 w', textInput(r.w, (v) => { r.w = Number(v) || 1; renderOverlay(); refreshPreview(); }, 'number')));
  row2.appendChild(field('高 h', textInput(r.h, (v) => { r.h = Number(v) || 1; renderOverlay(); refreshPreview(); }, 'number')));
  body.appendChild(row2);

  body.appendChild(checkbox('显示边框', r.border !== false, (v) => { r.border = v; refreshPreview(); }));
  body.appendChild(checkbox('反色显示', r.invert, (v) => { r.invert = v; refreshPreview(); }));
}

/* ---------- 拖拽 ---------- */

document.addEventListener('mousemove', (e) => {
  if (!drag) return;
  const dx = (e.clientX - drag.startX) / displayScale;
  const dy = (e.clientY - drag.startY) / displayScale;
  const r = drag.region;
  const H = canvasH();
  if (drag.mode === 'move') {
    r.x = Math.round(clamp(drag.orig.x + dx, 0, CANVAS_W - r.w));
    r.y = Math.round(clamp(drag.orig.y + dy, 0, H - r.h));
  } else {
    r.w = Math.round(clamp(drag.orig.w + dx, 40, CANVAS_W - r.x));
    r.h = Math.round(clamp(drag.orig.h + dy, 30, H - r.y));
  }
  drag.box.style.left = (r.x * displayScale) + 'px';
  drag.box.style.top = (r.y * displayScale) + 'px';
  drag.box.style.width = (r.w * displayScale) + 'px';
  drag.box.style.height = (r.h * displayScale) + 'px';
});

document.addEventListener('mouseup', () => {
  if (!drag) return;
  drag = null;
  renderProps();
  refreshPreview();
});

/* ---------- 预览 ---------- */

function refreshPreview() {
  clearTimeout(previewTimer);
  previewTimer = setTimeout(doPreview, 260);
}

async function doPreview() {
  try {
    const res = await fetch('/api/preview', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(state),
    });
    if (!res.ok) {
      const err = await res.json().catch(() => ({ error: '预览失败' }));
      setStatus(err.error || '预览失败', 'err');
      return;
    }
    const blob = await res.blob();
    if (previewUrl) URL.revokeObjectURL(previewUrl);
    previewUrl = URL.createObjectURL(blob);
    previewEl.src = previewUrl;
  } catch (e) {
    setStatus('预览请求失败：' + e.message, 'err');
  }
}

/* ---------- 增删 ---------- */

function uniqueId(base) {
  const ids = new Set(currentScreen().regions.map((r) => r.id));
  if (!ids.has(base)) return base;
  let i = 2;
  while (ids.has(base + i)) i++;
  return base + i;
}

function addRegion() {
  const H = canvasH();
  const region = {
    id: uniqueId('region'),
    kind: 'text',
    x: 20, y: Math.round(H / 2), w: 960, h: 120,
    label: '新模块',
    value: '编辑内容',
    size: 24,
  };
  currentScreen().regions.push(region);
  selectedId = region.id;
  renderList(); renderOverlay(); renderProps(); refreshPreview();
}

function removeRegion(id) {
  const screen = currentScreen();
  screen.regions = screen.regions.filter((r) => r.id !== id);
  if (selectedId === id) selectedId = null;
  renderList(); renderOverlay(); renderProps(); renderOptional(); refreshPreview();
}

/* ---------- 页面 ---------- */

function switchScreen(id) {
  state.active_screen = id;
  selectedId = null;
  renderScreenSelect(); renderList(); renderOverlay(); renderProps(); refreshPreview();
}

function addScreen() {
  let n = state.screens.length + 1;
  while (state.screens.some((s) => s.id === 'screen' + n)) n++;
  const id = 'screen' + n;
  state.screens.push({ id, name: '新页面', regions: [] });
  switchScreen(id);
}

function delScreen() {
  if (state.screens.length <= 1) { setStatus('至少保留一个页面', 'err'); return; }
  const id = state.active_screen;
  state.screens = state.screens.filter((s) => s.id !== id);
  switchScreen(state.screens[0].id);
}

/* ---------- 保存 / 导入导出 ---------- */

async function save() {
  try {
    const res = await fetch('/api/state', {
      method: 'PUT',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(state),
    });
    const data = await res.json().catch(() => ({}));
    if (!res.ok) { setStatus(data.error || '保存失败', 'err'); return; }
    setStatus('已保存', 'ok');
  } catch (e) {
    setStatus('保存失败：' + e.message, 'err');
  }
}

async function reload() {
  const res = await fetch('/api/state');
  state = await res.json();
  selectedId = null;
  syncRefreshField();
  renderScreenSelect(); renderList(); renderOverlay(); renderProps(); refreshPreview();
  setStatus('已重新载入', 'ok');
}

function exportJson() {
  const blob = new Blob([JSON.stringify(state, null, 2)], { type: 'application/json' });
  const a = document.createElement('a');
  a.href = URL.createObjectURL(blob);
  a.download = 'kindlehub-layout.json';
  a.click();
  setTimeout(() => URL.revokeObjectURL(a.href), 1000);
}

function importJson(file) {
  const reader = new FileReader();
  reader.onload = () => {
    try {
      const data = JSON.parse(reader.result);
      state = data;
      selectedId = null;
      syncRefreshField();
      renderScreenSelect(); renderList(); renderOverlay(); renderProps(); refreshPreview();
      setStatus('已导入，记得点保存', 'ok');
    } catch (e) {
      setStatus('JSON 解析失败', 'err');
    }
  };
  reader.readAsText(file);
}

function renderHealth() {
  const h = (meta && meta.health) || {};
  const mark = (s) => (s === 'ok' ? '●' : s === 'stale' ? '◐' : '○');
  const parts = [];
  if (h.weather) parts.push('天气 ' + mark(h.weather.state));
  if (h.workbuddy) parts.push('看板 ' + mark(h.workbuddy.state));

  const seen = meta && meta.kindle_seen;
  if (seen) {
    if (seen.seconds_ago === null) {
      parts.push('Kindle ○');
    } else if (seen.seconds_ago < 180) {
      parts.push('Kindle ●');
    } else {
      parts.push('Kindle ◐' + Math.round(seen.seconds_ago / 60) + '分前');
    }
  }
  $('health').textContent = parts.join('   ');
  $('health').title = seen && seen.ip ? 'Kindle IP: ' + seen.ip : 'Kindle 尚未连接';
}

async function refreshHealth() {
  try {
    const fresh = await fetch('/api/meta').then((r) => r.json());
    meta.health = fresh.health;
    meta.lan_ip = fresh.lan_ip;
    meta.kindle_seen = fresh.kindle_seen;
    renderHealth();
  } catch (e) { /* 后台轮询失败不打扰用户 */ }
}

const OPTIONAL_LABELS = {
  'weather.current': '天气（Open-Meteo，免费无需 Key）',
  'workbuddy.models': '模型消耗 · 精简',
  'workbuddy.usage': '模型消耗 · 总量',
  'workbuddy.models_detail': '模型消耗 · 详细',
};

const OPTIONAL_HINTS = {
  'weather.current': '添加后在顶部填入城市名，支持中文。',
  'workbuddy.models': '需要自建网关，并在 secrets 里设置密码。',
  'workbuddy.usage': '需要自建网关，并在 secrets 里设置密码。',
  'workbuddy.models_detail': '需要自建网关，并在 secrets 里设置密码。',
};

function renderOptional() {
  const box = $('optionalBox');
  const optional = (meta && meta.optional_regions) || {};
  const existing = new Set((currentScreen().regions || []).map((r) => r.source));
  const keys = Object.keys(optional).filter((k) => !existing.has(k));
  box.innerHTML = '';
  const h = document.createElement('h3');
  h.textContent = '可选模块';
  box.appendChild(h);
  if (!keys.length) {
    const p = document.createElement('p');
    p.textContent = '全部可选模块已添加。';
    box.appendChild(p);
    return;
  }
  const p = document.createElement('p');
  p.textContent = '这些模块依赖外部服务，按需添加：';
  box.appendChild(p);
  keys.forEach((key) => {
    const btn = document.createElement('button');
    btn.textContent = '+ ' + (OPTIONAL_LABELS[key] || key);
    btn.title = OPTIONAL_HINTS[key] || '';
    btn.addEventListener('click', () => {
      const region = JSON.parse(JSON.stringify(optional[key]));
      // 避免 id 冲突
      const ids = new Set(currentScreen().regions.map((r) => r.id));
      if (ids.has(region.id)) region.id = region.id + '-' + Date.now();
      currentScreen().regions.push(region);
      renderList(); renderOverlay(); renderProps(); renderOptional(); refreshPreview();
      setStatus('已添加「' + (OPTIONAL_LABELS[key] || key) + '」，记得保存', 'ok');
    });
    box.appendChild(btn);
  });
}

function syncRefreshField() {
  $('refresh').value = state.refresh_seconds || 60;
  state.settings = state.settings || {};
  state.settings.weather = state.settings.weather || {};
  $('city').value = state.settings.weather.city || '';
  const where = meta && meta.lan_ip ? ' · http://' + meta.lan_ip + ':' + meta.port + '/kindle' : '';
  $('deviceInfo').textContent =
    state.device.model + ' · ' + state.device.width + '×' + state.device.height +
    ' · ' + state.device.grayscale + ' 级灰' + where;
}

/* ---------- 启动 ---------- */

async function boot() {
  try {
    const [m, s] = await Promise.all([
      fetch('/api/meta').then((r) => r.json()),
      fetch('/api/state').then((r) => r.json()),
    ]);
    meta = m;
    state = s;
  } catch (e) {
    document.body.innerHTML = '<p style="padding:20px">无法连接后端：' + e.message + '</p>';
    return;
  }

  syncRefreshField();
  renderScreenSelect();
  renderList();
  renderProps();
  layoutCanvas();
  doPreview();

  const hasSecret = meta.secrets_set && meta.secrets_set.workbuddy_password;
  $('wbPassword').placeholder = hasSecret ? '已设置（留空不改）' : '未设置';

  renderOptional();

  renderHealth();
  setInterval(refreshHealth, 30000);

  // 深链接：/admin?select=<region_id>
  const want = new URLSearchParams(location.search).get('select');
  if (want && currentScreen().regions.some((r) => r.id === want)) {
    selectedId = want;
    renderList(); renderOverlay(); renderProps();
  }

  $('refresh').addEventListener('input', (e) => {
    state.refresh_seconds = Number(e.target.value) || 60;
  });
  $('city').addEventListener('input', (e) => {
    state.settings.weather.city = e.target.value.trim();
    refreshPreview();
  });
  $('wbPassword').addEventListener('change', async (e) => {
    const value = e.target.value.trim();
    if (!value) return;
    try {
      const res = await fetch('/api/secrets', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ key: 'workbuddy_password', value }),
      });
      if (!res.ok) { setStatus('密码保存失败', 'err'); return; }
      e.target.value = '';
      e.target.placeholder = '已设置（留空不改）';
      setStatus('密码已保存', 'ok');
      refreshPreview();
    } catch (err) {
      setStatus('密码保存失败：' + err.message, 'err');
    }
  });
  $('save').addEventListener('click', save);
  $('reload').addEventListener('click', reload);
  $('export').addEventListener('click', exportJson);
  $('import').addEventListener('change', (e) => { if (e.target.files[0]) importJson(e.target.files[0]); });
  $('addRegion').addEventListener('click', addRegion);
  $('addScreen').addEventListener('click', addScreen);
  $('delScreen').addEventListener('click', delScreen);
  $('screenSelect').addEventListener('change', (e) => switchScreen(e.target.value));
  $('screenName').addEventListener('input', (e) => {
    currentScreen().name = e.target.value;
    renderScreenSelect();
  });

  window.addEventListener('resize', layoutCanvas);
}

boot();