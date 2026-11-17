'use strict';

/* ------------------------------------------------------------------ */
/* helpers                                                             */
/* ------------------------------------------------------------------ */
const $ = (selector, root = document) => root.querySelector(selector);
const $$ = (selector, root = document) => Array.from(root.querySelectorAll(selector));

const STATUS_LABELS = {
  new: '未开始',
  heard: '已听',
  answered: '待检查',
  checked: '已检查',
};

const VERDICT_LABELS = {
  correct: '正确',
  wrong: '错误',
  blank: '未作答',
  uncertain: '待确认',
};

const SPEAKER_SVG = '<svg viewBox="0 0 24 24" width="14" height="14" aria-hidden="true"><path fill="currentColor" d="M4 9v6h4l5 4V5L8 9H4zm11.5 3a4.5 4.5 0 0 1-2.5 4v-8a4.5 4.5 0 0 1 2.5 4z"/></svg>';

const state = {
  version: '',
  meta: null,
  sources: [],
  stats: {},
  settings: {},
  recent: [],
  pending: [],
  ai: { configured: false, model: 'deepseek-chat', base_url: 'https://api.deepseek.com', masked_key: '' },
  wordbooks: [],
  voices: [],
  session: null,
  words: [],
  index: 0,
  heardCount: 0,
  sessionAnswers: {},
  sessionLocked: false,
  mode: 'auto',
  order: 'random',
  interval: 6,
  repeat: 1,
  rate: 1,
  voiceURI: '',
  showWord: false,
  phase: 'idle',
  gapLeft: 0,
  gapTimer: null,
  countdownTimer: null,
  runToken: 0,
  currentSessionId: null,
  view: 'setup',
  busy: false,
  books: {
    book: 'new',
    level: 'all',
    sort: 'frq',
    q: '',
    page: 1,
    pageSize: 50,
    total: 0,
    items: [],
  },
  history: [],
};

let toastTimer = null;
let searchTimer = null;
let answerDraftTimer = null;
let answerPersistChain = Promise.resolve();

function escapeHtml(value) {
  return String(value ?? '').replace(/[&<>"']/g, (ch) => ({
    '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;',
  }[ch]));
}

function fmtNumber(n) {
  return Number(n || 0).toLocaleString('zh-CN');
}

function fmtDate(iso) {
  if (!iso) return '—';
  const d = new Date(iso);
  if (Number.isNaN(d.getTime())) return iso;
  const pad = (n) => String(n).padStart(2, '0');
  return `${d.getFullYear()}-${pad(d.getMonth() + 1)}-${pad(d.getDate())} ${pad(d.getHours())}:${pad(d.getMinutes())}`;
}

function truncate(text, max = 90) {
  const value = String(text ?? '').replace(/\s+/g, ' ').trim();
  return value.length > max ? `${value.slice(0, max)}…` : value;
}

function meaningText(text) {
  return String(text ?? '').replace(/\n/g, '；').replace(/\s+/g, ' ').trim();
}

function randomSeed() {
  return String(Math.floor(100000 + Math.random() * 900000));
}

function toast(message, type = '') {
  const el = $('#toast');
  el.textContent = message;
  el.className = `toast show ${type}`.trim();
  clearTimeout(toastTimer);
  toastTimer = setTimeout(() => {
    el.className = 'toast';
  }, 3200);
}

async function copyText(text) {
  try {
    await navigator.clipboard.writeText(text);
    toast(`已复制：${text}`);
  } catch (err) {
    const area = document.createElement('textarea');
    area.value = text;
    area.style.position = 'fixed';
    area.style.opacity = '0';
    document.body.appendChild(area);
    area.select();
    try { document.execCommand('copy'); toast(`已复制：${text}`); }
    catch (e) { toast('复制失败，请手动复制', 'error'); }
    area.remove();
  }
}

async function api(path, options = {}) {
  const opts = { ...options };
  opts.headers = { ...(options.headers || {}) };
  if (opts.body !== undefined && typeof opts.body !== 'string') {
    opts.headers['Content-Type'] = 'application/json';
    opts.body = JSON.stringify(opts.body);
  }
  const response = await fetch(path, opts);
  const text = await response.text();
  let data = null;
  if (text) {
    try { data = JSON.parse(text); }
    catch (err) { data = { error: text }; }
  }
  if (!response.ok) {
    throw new Error((data && data.error) || `请求失败（${response.status}）`);
  }
  return data;
}

/* ------------------------------------------------------------------ */
/* views                                                               */
/* ------------------------------------------------------------------ */
const SESSION_PHASES = new Set(['countdown', 'speaking', 'gap', 'waiting', 'paused']);

function isSessionBusy() {
  return !!state.session && SESSION_PHASES.has(state.phase);
}

function showView(name) {
  state.view = name;
  $$('.view').forEach((view) => view.classList.toggle('active', view.id === `view-${name}`));
  $$('.nav-btn').forEach((btn) => btn.classList.toggle('active', btn.dataset.nav === name));
  if (name === 'books') loadBooks();
  if (name === 'history') loadHistory();
  if (name === 'settings') renderSettings();
  if (name === 'setup') updateSetup();
  window.scrollTo({ top: 0, behavior: 'smooth' });
}

function navigate(name) {
  if (isSessionBusy() && name !== 'session') {
    if (!window.confirm('正在听写，离开会停止本轮播放。确定离开吗？')) return;
    stopPlayback();
  }
  showView(name);
  if (name === 'setup') loadState();
}

/* ------------------------------------------------------------------ */
/* initial load                                                        */
/* ------------------------------------------------------------------ */
document.addEventListener('DOMContentLoaded', init);

async function init() {
  bindEvents();
  restorePlaybackSettings();
  setSeed(randomSeed());
  loadVoices();
  setTimeout(loadVoices, 900);
  await loadState();
  showView('setup');
}

async function loadState() {
  try {
    const data = await api('/api/state');
    state.version = data.version || state.version;
    state.meta = data.meta || {};
    state.sources = data.sources || [];
    state.stats = data.stats || {};
    state.settings = data.settings || {};
    state.recent = data.recent_sessions || [];
    state.pending = data.pending || [];
    state.ai = data.ai || state.ai;
    state.wordbooks = data.wordbooks || [];
    if (data.settings && data.settings.threshold) state.settings.threshold = data.settings.threshold;
    renderStats();
    renderSources();
    renderRecent();
    renderSettings();
    updateSetup();
  } catch (err) {
    toast(`无法读取本地数据：${err.message}`, 'error');
  }
}

function renderStats() {
  const stats = state.stats || {};
  const total = Number(stats.total || 0);
  const mastered = Number(stats.mastered || 0);
  const threshold = Number(state.settings.threshold || 5);
  const setText = (id, value) => { const el = $(id); if (el) el.textContent = value; };
  setText('#stat-new', fmtNumber(stats.new));
  setText('#stat-wrong', fmtNumber(stats.wrong));
  setText('#stat-mastered', fmtNumber(stats.mastered));
  setText('#hero-new', fmtNumber(stats.new));
  setText('#hero-mastered', fmtNumber(stats.mastered));
  setText('#hero-total', fmtNumber(total));
  setText('#hero-wrong', fmtNumber(stats.wrong));
  setText('#hero-threshold', threshold);
  setText('#rule-threshold', threshold);
  const bar = $('#hero-bar');
  if (bar) bar.style.width = `${total ? Math.min(100, (mastered / total) * 100) : 0}%`;
}

function renderSources() {
  const select = $('#source');
  if (!select) return;
  const saved = loadPlayback().source;
  const current = select.value || saved || state.settings.default_source || 'new_cet4';
  const groups = [
    ['new', '新词本'],
    ['wrong', '错题本'],
    ['all', '全部词库'],
    ['custom', '自定义词本'],
  ];
  select.innerHTML = '';
  for (const [book, label] of groups) {
    const items = state.sources.filter((s) => s.book === book);
    if (!items.length) continue;
    const group = document.createElement('optgroup');
    group.label = label;
    for (const source of items) {
      const option = document.createElement('option');
      option.value = source.id;
      option.textContent = `${source.name}（${fmtNumber(source.count)}）`;
      group.appendChild(option);
    }
    select.appendChild(group);
  }
  const values = $$('option', select).map((o) => o.value);
  select.value = values.includes(current) ? current : (values[0] || '');
  updateSetup();
}

function renderRecent() {
  const list = $('#recent-list');
  if (!list) return;
  if (!state.recent.length) {
    list.innerHTML = '<p class="empty">还没有记录，开始第一轮吧。</p>';
    return;
  }
  list.innerHTML = state.recent.map((item) => {
    const status = item.status || 'new';
    const accuracy = item.accuracy != null ? `${Math.round(item.accuracy * 100)}%` : '—';
    return `
      <div class="recent-item">
        <div class="row">
          <b>${escapeHtml(item.source_name || '—')}</b>
          <span class="tag ${status === 'checked' ? '' : 'warm'}">${STATUS_LABELS[status] || status}</span>
        </div>
        <div class="meta">${fmtDate(item.created_at)} · 种子 ${escapeHtml(item.seed)} · 播放 ${item.heard_count || 0}/${item.count || 0} · 正确率 ${accuracy}</div>
        <div class="row">
          <button class="row-btn" type="button" data-open-session="${escapeHtml(item.id)}">查看</button>
          <button class="row-btn" type="button" data-replay-session="${escapeHtml(item.id)}">重听</button>
        </div>
      </div>`;
  }).join('');
}

function updateSetup() {
  const select = $('#source');
  if (!select) return;
  const sourceId = select.value;
  const source = state.sources.find((s) => s.id === sourceId);
  const desc = $('#source-desc');
  if (desc) desc.textContent = source ? source.desc : '—';
  if (!source) {
    const hint = $('#pool-hint');
    if (hint) hint.textContent = '请选择一个词库来源。';
    const start = $('#start-btn');
    if (start) start.disabled = true;
    return;
  }
  const poolInput = $('#pool');
  const countInput = $('#count');
  const pool = Math.max(0, parseInt(poolInput.value || '0', 10) || 0);
  const count = Math.max(0, parseInt(countInput.value || '0', 10) || 0);
  const sourceCount = Number(source.count || 0);
  const candidate = pool > 0 ? Math.min(pool, sourceCount) : sourceCount;
  const actual = Math.min(count, candidate);
  const poolRange = $('#pool-range');
  if (poolRange) poolRange.textContent = pool > 0 ? `候选池前 ${fmtNumber(pool)}` : '候选池全部';
  const hint = $('#pool-hint');
  if (hint) {
    if (!sourceCount) {
      hint.innerHTML = `<b>${escapeHtml(source.name)}</b> 目前没有可听的词。新词本可能已经清空；可以改用「全部词库」复习，或在已掌握里重置单词。`;
    } else {
      const warn = actual < count
        ? `<br><span class="warn">候选池只有 ${fmtNumber(candidate)} 个词，本轮实际会听 ${fmtNumber(actual)} 个。</span>`
        : '';
      hint.innerHTML = `当前 <b>${escapeHtml(source.name)}</b> 有 <b>${fmtNumber(sourceCount)}</b> 个可用词；` +
        `候选池 <b>${pool > 0 ? `前 ${fmtNumber(pool)} 个` : '全部'}</b>；` +
        `本轮随机抽 <b>${fmtNumber(actual)}</b> 个。${warn}`;
    }
  }
  const start = $('#start-btn');
  if (start) start.disabled = state.busy || sourceCount === 0 || count < 1;
  const intervalField = $('#interval-field');
  if (intervalField) intervalField.classList.toggle('hidden', state.mode !== 'auto');
}

function renderSettings() {
  const threshold = Number(state.settings.threshold || 5);
  const input = $('#threshold-input');
  if (input) input.value = threshold;
  const version = $('#app-version');
  if (version) version.textContent = state.version || '0.1.0';
  setVoiceDiagnostic();

  const aiInfo = state.ai || {};
  const aiStatus = $('#ai-status');
  if (aiStatus) {
    aiStatus.textContent = aiInfo.configured ? '已配置' : '未配置';
    aiStatus.className = aiInfo.configured ? 'tag' : 'tag warm';
  }
  const keyInput = $('#ai-key');
  if (keyInput) {
    keyInput.placeholder = aiInfo.configured
      ? `已配置 ${aiInfo.masked_key || ''}，留空保持不变`
      : 'sk-...';
  }
  const modelInput = $('#ai-model');
  if (modelInput) modelInput.value = aiInfo.model || 'deepseek-chat';
  const baseInput = $('#ai-base');
  if (baseInput) baseInput.value = aiInfo.base_url || 'https://api.deepseek.com';
  const aiHint = $('#ai-hint');
  if (aiHint) {
    aiHint.textContent = aiInfo.configured
      ? '已配置。答题页点「AI 判卷」即可自动判分；Key 只保存在本机。'
      : '配置 API Key 后，答题页可以直接点「AI 判卷」自动判分；不配置也可以把答题纸发给我复核。';
  }
  renderWordbooks();
}

/* ------------------------------------------------------------------ */
/* custom wordbooks                                                    */
/* ------------------------------------------------------------------ */
function renderWordbooks() {
  const count = $('#wordbook-count');
  if (count) count.textContent = `${state.wordbooks.length} 个`;
  const list = $('#wordbook-list');
  if (!list) return;
  if (!state.wordbooks.length) {
    list.innerHTML = '<p class="empty">还没有导入自定义词本。支持 JSON / CSV，也可以先下载模板。</p>';
    return;
  }
  list.innerHTML = state.wordbooks.map((book) => {
    const meta = [
      `${book.count} 个词`,
      `未掌握 ${book.new_count ?? '—'}`,
      `已掌握 ${book.mastered_count ?? 0}`,
    ];
    if (book.description) meta.push(book.description);
    return `
      <div class="wordbook-item">
        <div>
          <b>${escapeHtml(book.name)}</b>
          <div class="meta">${meta.map(escapeHtml).join(' · ')}</div>
        </div>
        <button class="row-btn danger-text" type="button" data-delete-wordbook="${escapeHtml(book.id)}">删除</button>
      </div>`;
  }).join('');
}

async function importWordbook() {
  const fileInput = $('#wordbook-file');
  const file = fileInput?.files?.[0];
  if (!file) {
    toast('先选择一个 JSON 或 CSV 文件', 'error');
    return;
  }
  if (file.size > 2 * 1024 * 1024) {
    toast('文件太大（最多 2 MB）', 'error');
    return;
  }
  const button = $('#wordbook-import');
  const original = button.textContent;
  button.disabled = true;
  button.textContent = '导入中…';
  const hint = $('#wordbook-import-hint');
  if (hint) hint.textContent = '';
  try {
    const content = await file.text();
    const name = $('#wordbook-name').value.trim();
    const body = { filename: file.name, content };
    if (name) body.name = name;
    const data = await api('/api/wordbooks/import', { method: 'POST', body });
    toast(`已导入「${data.book.name}」：${data.book.count} 个词`);
    fileInput.value = '';
    $('#wordbook-name').value = '';
    state.wordbooks = data.wordbooks || state.wordbooks;
    await loadState();
    renderSettings();
    if (hint) hint.textContent = '导入成功。去「听写」页，在「词库来源 → 自定义词本」里选择它。';
  } catch (err) {
    toast(err.message, 'error');
    if (hint) hint.textContent = err.message;
  } finally {
    button.disabled = false;
    button.textContent = original;
  }
}

async function deleteWordbook(bookId) {
  const book = state.wordbooks.find((item) => item.id === bookId);
  if (!window.confirm(`删除自定义词本「${book?.name || bookId}」？\n历史听写记录会保留，但该词本会从来源和浏览中移除。`)) return;
  try {
    const data = await api('/api/wordbooks/delete', { method: 'POST', body: { id: bookId } });
    state.wordbooks = data.wordbooks || [];
    await loadState();
    renderSettings();
    toast('词本已删除');
  } catch (err) {
    toast(err.message, 'error');
  }
}

function downloadWordbookTemplate(fmt) {
  window.location.href = `/api/wordbooks/template?format=${fmt}`;
}

/* ------------------------------------------------------------------ */
/* playback settings                                                   */
/* ------------------------------------------------------------------ */
function loadPlayback() {
  try {
    return JSON.parse(localStorage.getItem('dictation.playback') || '{}') || {};
  } catch (err) {
    return {};
  }
}

function savePlaybackSettings() {
  const payload = {
    mode: state.mode,
    order: state.order,
    interval: state.interval,
    repeat: state.repeat,
    rate: state.rate,
    voiceURI: state.voiceURI,
    showWord: state.showWord,
    source: $('#source') ? $('#source').value : '',
  };
  localStorage.setItem('dictation.playback', JSON.stringify(payload));
}

function restorePlaybackSettings() {
  const saved = loadPlayback();
  setMode(saved.mode === 'manual' ? 'manual' : 'auto');
  setOrder(saved.order === 'frequency' ? 'frequency' : 'random');
  if (saved.interval) {
    state.interval = Math.max(1, Math.min(30, Number(saved.interval) || 6));
    $('#interval').value = String(state.interval);
  }
  if (saved.repeat) {
    state.repeat = Number(saved.repeat) === 2 ? 2 : 1;
    $('#repeat').value = String(state.repeat);
  }
  if (saved.rate) {
    state.rate = Math.max(0.6, Math.min(1.2, Number(saved.rate) || 1));
    $('#rate').value = String(state.rate);
  }
  state.voiceURI = saved.voiceURI || '';
  state.showWord = Boolean(saved.showWord);
  $('#show-word').checked = state.showWord;
  updateIntervalLabel();
  updateRateLabel();
}

function setMode(mode) {
  state.mode = mode === 'manual' ? 'manual' : 'auto';
  $$('#mode-seg button').forEach((btn) => {
    btn.classList.toggle('active', btn.dataset.mode === state.mode);
  });
  const intervalField = $('#interval-field');
  if (intervalField) intervalField.classList.toggle('hidden', state.mode !== 'auto');
}

function setOrder(order) {
  state.order = order === 'frequency' ? 'frequency' : 'random';
  $$('#order-seg button').forEach((btn) => {
    btn.classList.toggle('active', btn.dataset.order === state.order);
  });
  const hint = $('#order-hint');
  if (hint) hint.textContent = state.order === 'frequency' ? '从高频到低频' : '按种子打乱顺序';
}

function updateIntervalLabel() {
  const value = Math.max(1, Math.min(30, Number($('#interval').value) || 6));
  state.interval = value;
  const label = $('#interval-val');
  if (label) label.textContent = value;
}

function updateRateLabel() {
  const value = Math.max(0.6, Math.min(1.2, Number($('#rate').value) || 1));
  state.rate = value;
  const label = $('#rate-val');
  if (label) label.textContent = value.toFixed(2).replace(/0$/, '');
}

function setSeed(value) {
  const input = $('#seed');
  if (input) input.value = String(value || '');
}

function readSetup() {
  const source = $('#source').value;
  const pool = parseInt($('#pool').value || '0', 10);
  const count = parseInt($('#count').value || '0', 10);
  const seedText = ($('#seed').value || '').trim();
  if (!source) {
    toast('请选择词库来源', 'error');
    return null;
  }
  if (!Number.isFinite(pool) || pool < 0 || pool > 100000) {
    toast('候选池需在 0–100000 之间（0 表示全部）', 'error');
    return null;
  }
  if (!Number.isFinite(count) || count < 1 || count > 500) {
    toast('听写数量需在 1–500 之间', 'error');
    return null;
  }
  let seed = 0;
  if (seedText) {
    if (!/^\d+$/.test(seedText)) {
      toast('种子只能是数字，留空则自动生成', 'error');
      return null;
    }
    seed = parseInt(seedText, 10);
    if (seed < 1 || seed > 999999999) {
      toast('种子需在 1–999999999 之间', 'error');
      return null;
    }
  }
  state.interval = Math.max(1, Math.min(30, parseInt($('#interval').value || '6', 10) || 6));
  state.repeat = Number($('#repeat').value) === 2 ? 2 : 1;
  state.rate = Math.max(0.6, Math.min(1.2, Number($('#rate').value) || 1));
  state.showWord = $('#show-word').checked;
  return {
    source,
    pool,
    count,
    seed,
    mode: state.mode,
    interval: state.interval,
    repeat: state.repeat,
    rate: state.rate,
    showWord: state.showWord,
    voiceURI: state.voiceURI,
    order: state.order,
  };
}

async function startNewSession() {
  if (state.busy) return;
  const config = readSetup();
  if (!config) return;
  state.busy = true;
  const button = $('#start-btn');
  const original = button.innerHTML;
  button.disabled = true;
  button.textContent = '准备中…';
  savePlaybackSettings();
  try {
    const data = await api('/api/sessions', { method: 'POST', body: config });
    const session = data.session;
    if (data.warnings && data.warnings.length) toast(data.warnings.join(' '));
    setSeed(randomSeed());
    await loadState();
    startSession(session);
  } catch (err) {
    toast(err.message, 'error');
  } finally {
    state.busy = false;
    button.innerHTML = original;
    button.disabled = false;
    updateSetup();
  }
}

/* ------------------------------------------------------------------ */
/* TTS                                                                 */
/* ------------------------------------------------------------------ */
function setVoiceDiagnostic(message) {
  const el = $('#voice-diagnostic');
  if (!el) return;
  if (message) { el.textContent = message; return; }
  if (!('speechSynthesis' in window)) {
    el.textContent = '这个浏览器不支持语音合成，请使用 Edge 或 Chrome。';
    el.classList.add('warn');
    return;
  }
  if (!state.voices.length) {
    el.textContent = '未检测到英文语音。请使用 Edge，或在 Windows 设置 → 时间和语言 → 语音 里添加英文语音。';
    el.classList.add('warn');
    return;
  }
  const selected = selectedVoice();
  el.textContent = `已检测到 ${state.voices.length} 个英文语音，当前：${selected ? selected.name : '默认'}。`;
  el.classList.remove('warn');
}

function loadVoices() {
  const select = $('#voice');
  if (!select) return;
  if (!('speechSynthesis' in window)) {
    select.innerHTML = '<option value="">不支持语音</option>';
    state.voices = [];
    setVoiceDiagnostic();
    return;
  }
  const all = window.speechSynthesis.getVoices() || [];
  state.voices = all.filter((voice) => String(voice.lang || '').toLowerCase().startsWith('en'));
  const saved = state.voiceURI || loadPlayback().voiceURI || '';
  select.innerHTML = '';
  if (!state.voices.length) {
    select.innerHTML = '<option value="">未检测到英文语音</option>';
    setVoiceDiagnostic();
    return;
  }
  for (const voice of state.voices) {
    const option = document.createElement('option');
    option.value = voice.voiceURI;
    option.textContent = `${voice.name} (${voice.lang})`;
    select.appendChild(option);
  }
  let chosen = state.voices.find((voice) => voice.voiceURI === saved);
  if (!chosen) {
    const lowerLang = (voice) => String(voice.lang || '').toLowerCase();
    const isNatural = (voice) => /natural|online|aria|jenny|guy|zira|william|sonia|ryan/i.test(voice.name);
    const us = state.voices.filter((voice) => lowerLang(voice).startsWith('en-us'));
    const gb = state.voices.filter((voice) => lowerLang(voice).startsWith('en-gb'));
    chosen = us.find(isNatural) || gb.find(isNatural) || state.voices.find(isNatural)
      || us[0] || gb[0] || state.voices[0];
  }
  select.value = chosen.voiceURI;
  select.title = chosen.name;
  state.voiceURI = chosen.voiceURI;
  setVoiceDiagnostic();
}

function selectedVoice() {
  if (!state.voices.length) return null;
  return state.voices.find((voice) => voice.voiceURI === state.voiceURI) || state.voices[0];
}

function speakText(text) {
  return new Promise((resolve) => {
    if (!('speechSynthesis' in window)) {
      resolve({ ok: false, error: 'unsupported' });
      return;
    }
    const synth = window.speechSynthesis;
    const utterance = new SpeechSynthesisUtterance(text);
    const voice = selectedVoice();
    if (voice) utterance.voice = voice;
    utterance.lang = (voice && voice.lang) || 'en-US';
    utterance.rate = state.rate;
    utterance.volume = 1;
    let settled = false;
    let timer = null;
    const finish = (result) => {
      if (settled) return;
      settled = true;
      if (timer) clearTimeout(timer);
      resolve(result);
    };
    timer = setTimeout(
      () => finish({ ok: false, error: 'timeout' }),
      Math.max(8000, String(text).length * 1500),
    );
    utterance.onend = () => finish({ ok: true });
    utterance.onerror = (event) => finish({ ok: false, error: (event && event.error) || 'error' });
    try {
      synth.cancel();
      synth.resume();
      synth.speak(utterance);
    } catch (err) {
      finish({ ok: false, error: String(err) });
    }
  });
}

function testVoice() {
  if (!state.voices.length) {
    toast('没有可用的英文语音，请使用 Edge 或安装英文语音', 'error');
    return;
  }
  speakText('abandon');
}

function speakWord(word) {
  const text = String(word || '').trim();
  if (!text) return;
  if (!state.voices.length) {
    toast('没有可用的英文语音，请使用 Edge 或安装英文语音', 'error');
    return;
  }
  speakText(text);
}

/* ------------------------------------------------------------------ */
/* session player                                                      */
/* ------------------------------------------------------------------ */
function clearPlaybackTimers() {
  if (state.gapTimer) { clearInterval(state.gapTimer); state.gapTimer = null; }
  if (state.countdownTimer) { clearTimeout(state.countdownTimer); state.countdownTimer = null; }
  const speaker = $('#speaker');
  if (speaker) speaker.classList.remove('active');
}

function cancelSpeech() {
  if ('speechSynthesis' in window) {
    try { window.speechSynthesis.cancel(); } catch (err) { /* ignore */ }
  }
}

function startSession(session) {
  state.runToken += 1;
  clearPlaybackTimers();
  cancelSpeech();
  state.session = session;
  state.words = session.words || [];
  state.index = 0;
  state.heardCount = Number(session.heard_count || 0);
  const savedAnswers = session.answers && typeof session.answers === 'object'
    ? session.answers
    : readDraft(session.id);
  state.sessionAnswers = { ...(savedAnswers || {}) };
  state.sessionLocked = Boolean(session.has_check);
  state.mode = session.mode === 'manual' ? 'manual' : 'auto';
  state.interval = Number(session.interval || state.interval || 6);
  state.repeat = Number(session.repeat) === 2 ? 2 : 1;
  state.currentSessionId = session.id;
  localStorage.setItem('dictation.lastSession', session.id);
  $('#session-source').textContent = session.source_name || '—';
  $('#session-seed').textContent = session.seed ?? '—';
  $('#session-total').textContent = String(state.words.length);
  showView('session');
  syncSessionAnswerInput();
  updateSessionUI();
  if (!state.words.length) {
    toast('这一轮没有可播放的词', 'error');
    showView('setup');
    return;
  }
  startCountdown();
  setTimeout(focusSessionAnswer, 200);
}

function startCountdown() {
  state.phase = 'countdown';
  let remaining = 3;
  updateSessionUI();
  const tick = () => {
    remaining -= 1;
    if (remaining > 0) {
      const el = $('#countdown');
      if (el) el.textContent = `准备 ${remaining}…`;
      state.countdownTimer = setTimeout(tick, 1000);
    } else {
      state.countdownTimer = null;
      playCurrent();
    }
  };
  const el = $('#countdown');
  if (el) el.textContent = `准备 ${remaining}…`;
  state.countdownTimer = setTimeout(tick, 1000);
}

async function playCurrent() {
  if (!state.session) return;
  if (state.index >= state.words.length) {
    await finishSession(false);
    return;
  }
  const token = ++state.runToken;
  state.phase = 'speaking';
  const word = state.words[state.index];
  syncSessionAnswerInput();
  if (state.index + 1 > state.heardCount) {
    state.heardCount = state.index + 1;
    persistHeard();
  }
  updateSessionUI();
  const repeats = state.repeat === 2 ? 2 : 1;
  for (let round = 0; round < repeats; round += 1) {
    if (token !== state.runToken) return;
    if (round > 0) {
      await new Promise((resolve) => setTimeout(resolve, 550));
    }
    if (token !== state.runToken) return;
    const result = await speakText(word.word);
    if (token !== state.runToken) return;
    if (!result.ok && !['interrupted', 'canceled'].includes(result.error)) {
      toast(`语音播放失败：${result.error}。点「继续」重试，或换一个英文音色 / 换用 Edge。`, 'error');
      state.phase = 'paused';
      updateSessionUI();
      return;
    }
  }
  if (token !== state.runToken) return;
  if (state.mode === 'auto') {
    startGap(token);
  } else {
    state.phase = 'waiting';
    updateSessionUI();
  }
}

function startGap(token) {
  state.phase = 'gap';
  state.gapLeft = state.interval * 1000;
  updateSessionUI();
  if (state.gapTimer) clearInterval(state.gapTimer);
  state.gapTimer = setInterval(() => {
    if (token !== state.runToken) {
      clearInterval(state.gapTimer);
      state.gapTimer = null;
      return;
    }
    state.gapLeft -= 100;
    if (state.gapLeft <= 0) {
      clearInterval(state.gapTimer);
      state.gapTimer = null;
      commitCurrentAnswer();
      state.index += 1;
      playCurrent();
      persistSessionAnswers();
    } else {
      updateSessionUI();
    }
  }, 100);
}

function updateSessionUI() {
  if (!state.session) return;
  const word = state.words[state.index];
  const indexEl = $('#session-n');
  if (indexEl) indexEl.textContent = String(Math.min(state.index + 1, Math.max(1, state.words.length)));
  const totalEl = $('#session-total');
  if (totalEl) totalEl.textContent = String(state.words.length);
  const bar = $('#session-bar');
  if (bar) {
    const progress = state.words.length ? ((state.index + 1) / state.words.length) * 100 : 0;
    bar.style.width = `${Math.min(100, Math.max(0, progress))}%`;
  }
  const wordEl = $('#session-word');
  if (wordEl) {
    if (state.showWord && word) {
      wordEl.textContent = word.word;
      wordEl.classList.remove('hidden');
    } else {
      wordEl.classList.add('hidden');
    }
  }
  const speaker = $('#speaker');
  if (speaker) speaker.classList.toggle('active', state.phase === 'speaking');
  let countdown = '';
  if (state.phase === 'countdown') countdown = '准备中…';
  else if (state.phase === 'speaking') countdown = '正在读…';
  else if (state.phase === 'gap') countdown = `${Math.ceil(Math.max(0, state.gapLeft) / 1000)} 秒后下一个`;
  else if (state.phase === 'waiting') countdown = '按空格读下一个';
  else if (state.phase === 'paused') countdown = '已暂停，按空格继续';
  else if (state.phase === 'finished') countdown = '本轮结束';
  const countdownEl = $('#countdown');
  if (countdownEl) countdownEl.textContent = countdown;
  const pauseBtn = $('#pause-btn');
  if (pauseBtn) pauseBtn.textContent = state.phase === 'paused' ? '继续' : '暂停';
  const prevBtn = $('#prev-btn');
  if (prevBtn) prevBtn.disabled = state.index <= 0;
  const hintEl = $('#session-hint');
  if (hintEl) {
    hintEl.textContent = '输入框内：Enter 下一个 · 空格 暂停 · ↑/← 上一个 · ↓/→ 下一个 · Esc 退出输入';
  }
  const answerInput = $('#session-answer');
  if (answerInput) {
    answerInput.disabled = state.phase === 'finished' || state.phase === 'idle' || state.sessionLocked;
  }
}

function pauseSession() {
  if (!state.session || !['countdown', 'speaking', 'gap', 'waiting'].includes(state.phase)) return;
  state.runToken += 1;
  clearPlaybackTimers();
  cancelSpeech();
  state.phase = 'paused';
  updateSessionUI();
}

function togglePause() {
  if (!state.session) return;
  if (state.phase === 'paused') {
    playCurrent();
  } else {
    pauseSession();
  }
}

function repeatCurrent() {
  if (!state.session || ['finished', 'idle'].includes(state.phase)) return;
  state.runToken += 1;
  clearPlaybackTimers();
  cancelSpeech();
  playCurrent();
}

function prevWord() {
  if (!state.session || state.index <= 0) return;
  commitCurrentAnswer();
  state.runToken += 1;
  clearPlaybackTimers();
  cancelSpeech();
  state.index -= 1;
  playCurrent();
  persistSessionAnswers();
  setTimeout(focusSessionAnswer, 0);
}

function nextWord() {
  skipToNext();
}

function skipToNext() {
  if (!state.session || ['finished', 'idle'].includes(state.phase)) return;
  if (state.phase === 'countdown') {
    if (state.countdownTimer) {
      clearTimeout(state.countdownTimer);
      state.countdownTimer = null;
    }
    playCurrent();
    setTimeout(focusSessionAnswer, 0);
    return;
  }
  commitCurrentAnswer();
  state.runToken += 1;
  clearPlaybackTimers();
  cancelSpeech();
  state.index += 1;
  playCurrent();
  persistSessionAnswers();
  setTimeout(focusSessionAnswer, 0);
}

async function finishSession(early) {
  if (!state.session) return;
  const id = state.session.id;
  commitCurrentAnswer();
  state.runToken += 1;
  clearPlaybackTimers();
  cancelSpeech();
  state.phase = 'finished';
  if (!early && state.index >= state.words.length) {
    state.heardCount = state.words.length;
  } else {
    state.heardCount = Math.max(
      state.heardCount,
      Math.min(state.words.length, state.index + 1),
    );
  }
  try {
    await api(`/api/sessions/${id}/heard`, {
      method: 'POST',
      body: { heard_count: state.heardCount },
    });
  } catch (err) { /* the per-word updates already carry the count */ }
  await persistSessionAnswers({ silent: true });
  updateSessionUI();
  await openAnswers(id);
}

function persistHeard() {
  if (!state.session) return;
  const id = state.session.id;
  const value = state.heardCount;
  api(`/api/sessions/${id}/heard`, {
    method: 'POST',
    body: { heard_count: value },
  }).catch(() => {});
}

function stopPlayback() {
  if (state.session) {
    commitCurrentAnswer();
    persistSessionAnswers();
  }
  state.runToken += 1;
  clearPlaybackTimers();
  cancelSpeech();
  state.phase = 'idle';
  state.session = null;
  state.words = [];
  state.index = 0;
  state.heardCount = 0;
  state.currentSessionId = null;
}

/* ------------------------------------------------------------------ */
/* answers                                                             */
/* ------------------------------------------------------------------ */
function draftKey(id) {
  return `dictation.answers.${id}`;
}

function readDraft(id) {
  try {
    return JSON.parse(localStorage.getItem(draftKey(id)) || '{}') || {};
  } catch (err) {
    return {};
  }
}

function saveDraft() {
  if (!state.currentSessionId) return;
  clearTimeout(answerDraftTimer);
  answerDraftTimer = setTimeout(() => {
    const values = {};
    $$('#answer-rows input[data-n]').forEach((input) => {
      if (input.value.trim()) values[input.dataset.n] = input.value.trim();
    });
    localStorage.setItem(draftKey(state.currentSessionId), JSON.stringify(values));
  }, 250);
}

function updateAnswerCount() {
  const inputs = $$('#answer-rows input[data-n]');
  const filled = inputs.filter((input) => input.value.trim()).length;
  const count = $('#answers-count');
  if (count) count.textContent = `${filled} / ${inputs.length}`;
}

/* ------------------------------------------------------------------ */
/* answer box on the listening screen                                  */
/* ------------------------------------------------------------------ */
function currentWordN() {
  if (!state.session) return '';
  const word = state.words[state.index];
  return word ? String(word.n) : '';
}

function syncSessionAnswerInput() {
  const input = $('#session-answer');
  if (!input) return;
  const hint = $('#session-answer-hint');
  const n = currentWordN();
  if (!n) {
    input.value = '';
    input.disabled = true;
    input.placeholder = '本轮结束';
    if (hint) hint.textContent = '本轮结束，去填写答题纸吧。';
    return;
  }
  if (state.sessionLocked) {
    input.value = '';
    input.disabled = true;
    input.placeholder = '本轮已检查，不能修改答案';
    if (hint) hint.textContent = '重听不会修改已检查的记录。';
    return;
  }
  input.disabled = false;
  input.value = state.sessionAnswers[n] ?? '';
  input.placeholder = '在这里写中文意思…';
  if (hint) hint.textContent = '输入框内：Enter 下一个 · 空格 暂停 · ↑/← 上一个 · ↓/→ 下一个';
}

function saveSessionDraft() {
  if (!state.session) return;
  const values = {};
  for (const [key, value] of Object.entries(state.sessionAnswers)) {
    if (String(value ?? '').trim()) values[key] = String(value);
  }
  localStorage.setItem(draftKey(state.session.id), JSON.stringify(values));
}

function handleSessionAnswerInput() {
  if (!state.session || state.sessionLocked) return;
  const n = currentWordN();
  if (!n) return;
  const input = $('#session-answer');
  state.sessionAnswers[n] = input.value;
  saveSessionDraft();
}

function commitCurrentAnswer() {
  if (!state.session || state.sessionLocked) return;
  const n = currentWordN();
  if (!n) return;
  const input = $('#session-answer');
  if (input) state.sessionAnswers[n] = input.value;
  saveSessionDraft();
}

function persistSessionAnswers({ silent = true } = {}) {
  if (!state.session || state.sessionLocked) return Promise.resolve();
  const sessionId = state.session.id;
  const answers = {};
  for (const [key, value] of Object.entries(state.sessionAnswers)) {
    answers[key] = String(value ?? '');
  }
  state.session.answers = answers;
  state.session.has_answers = Object.values(answers).some((value) => value.trim());
  answerPersistChain = answerPersistChain.then(async () => {
    try {
      await api(`/api/sessions/${sessionId}/answers`, {
        method: 'POST',
        body: { answers },
      });
    } catch (err) {
      if (!silent) toast(err.message, 'error');
    }
  });
  return answerPersistChain;
}

function focusSessionAnswer() {
  if (!state.session || state.sessionLocked) return;
  if (['finished', 'idle'].includes(state.phase)) return;
  const input = $('#session-answer');
  if (input && !input.disabled) input.focus();
}

async function openAnswers(id) {
  try {
    if (isSessionBusy()) stopPlayback();
    const detail = await api(`/api/sessions/${id}`);
    if (state.session && state.session.id === id) {
      detail.answers = { ...(detail.answers || {}), ...state.sessionAnswers };
    }
    state.session = detail;
    state.currentSessionId = id;
    state.words = detail.words || [];
    renderAnswers(detail);
    showView('answers');
    if (detail.report_available) {
      try {
        const report = await api(`/api/sessions/${id}/report`);
        renderResult(report);
      } catch (err) {
        const panel = $('#result-panel');
        if (panel) {
          panel.classList.remove('hidden');
          panel.innerHTML = `<p class="hint">检查报告读取失败：${escapeHtml(err.message)}</p>`;
        }
      }
    }
  } catch (err) {
    toast(err.message, 'error');
  }
}

function renderAnswers(detail) {
  const form = $('#answers-form');
  const result = $('#result-panel');
  const heard = Number(detail.effective_heard || (detail.words || []).length || 0);
  const total = Number(detail.count || (detail.words || []).length || 0);
  const title = $('#answers-title');
  if (title) title.textContent = `答题纸 · ${detail.source_name || ''}`;
  const subtitle = $('#answers-subtitle');
  if (subtitle) {
    subtitle.textContent = `场次 ${detail.id} · 种子 ${detail.seed} · 本轮播放 ${heard} / ${total} 个`;
  }
  const command = $('#check-command');
  if (command) command.textContent = `检查 ${detail.id}`;
  const status = $('#answers-status');
  if (detail.has_check) {
    if (status) { status.textContent = '已检查'; status.className = 'tag warm'; }
    if (form) form.classList.add('hidden');
    if (result) result.classList.remove('hidden');
  } else {
    if (status) {
      status.textContent = detail.has_answers ? '已保存，待检查' : '待填写';
      status.className = detail.has_answers ? 'tag warm' : 'tag';
    }
    if (form) form.classList.remove('hidden');
    if (result) { result.classList.add('hidden'); result.innerHTML = ''; }
    renderAnswerRows(detail, heard);
  }
}

function renderAnswerRows(detail, heard) {
  const container = $('#answer-rows');
  if (!container) return;
  const saved = detail.answers || {};
  const draft = readDraft(detail.id);
  const words = (detail.words || []).slice(0, heard);
  container.innerHTML = words.map((word) => {
    const n = String(word.n);
    const value = saved[n] ?? draft[n] ?? '';
    return `
      <div class="answer-row" data-n="${n}">
        <div class="answer-num">${String(word.n).padStart(2, '0')}</div>
        <div class="answer-word">${escapeHtml(word.word)}<small>${escapeHtml(word.phonetic || '')}</small></div>
        <input type="text" data-n="${n}" value="${escapeHtml(value)}" placeholder="写中文意思…" autocomplete="off" spellcheck="false">
      </div>`;
  }).join('');
  updateAnswerCount();
}

function collectAnswers() {
  const answers = {};
  $$('#answer-rows input[data-n]').forEach((input) => {
    answers[input.dataset.n] = input.value.trim();
  });
  return answers;
}

async function saveAnswers() {
  if (!state.currentSessionId) return;
  const button = $('#save-answers');
  const original = button.textContent;
  button.disabled = true;
  button.textContent = '保存中…';
  try {
    const data = await api(`/api/sessions/${state.currentSessionId}/answers`, {
      method: 'POST',
      body: { answers: collectAnswers() },
    });
    state.session.answers = data.answers || {};
    const status = $('#answers-status');
    if (status) { status.textContent = '已保存，待检查'; status.className = 'tag warm'; }
    toast(`答题纸已保存：${data.paths.markdown}`);
    loadState();
  } catch (err) {
    toast(err.message, 'error');
  } finally {
    button.disabled = false;
    button.textContent = original;
  }
}

function downloadSheet() {
  if (!state.currentSessionId) return;
  const a = document.createElement('a');
  a.href = `/api/sessions/${state.currentSessionId}/sheet?filled=0`;
  a.download = `dictation-sheet-${state.currentSessionId}.md`;
  document.body.appendChild(a);
  a.click();
  a.remove();
}

/* ------------------------------------------------------------------ */
/* AI grading / batch answers / packet export                          */
/* ------------------------------------------------------------------ */
async function saveAiSettings({ silent = false } = {}) {
  const key = $('#ai-key').value.trim();
  const model = $('#ai-model').value.trim();
  const baseUrl = $('#ai-base').value.trim();
  const body = { model, base_url: baseUrl };
  if (key) body.api_key = key;
  try {
    const data = await api('/api/ai/settings', { method: 'PUT', body });
    state.ai = data.ai || state.ai;
    $('#ai-key').value = '';
    renderSettings();
    if (!silent) toast('AI 设置已保存');
    return true;
  } catch (err) {
    toast(err.message, 'error');
    return false;
  }
}

async function testAiSettings() {
  const button = $('#ai-test');
  const original = button.textContent;
  button.disabled = true;
  button.textContent = '测试中…';
  try {
    const saved = await saveAiSettings({ silent: true });
    if (!saved) return;
    const data = await api('/api/ai/test', { method: 'POST' });
    toast(`DeepSeek 连接正常（${data.model}）：${data.reply || 'OK'}`);
  } catch (err) {
    toast(err.message, 'error');
  } finally {
    button.disabled = false;
    button.textContent = original;
  }
}

async function clearAiSettings() {
  if (!window.confirm('确定清除本机保存的 DeepSeek API Key 吗？')) return;
  try {
    const data = await api('/api/ai/settings', { method: 'PUT', body: { clear: true } });
    state.ai = data.ai || state.ai;
    $('#ai-key').value = '';
    renderSettings();
    toast('API Key 已清除');
  } catch (err) {
    toast(err.message, 'error');
  }
}

async function runAiCheck() {
  if (!state.currentSessionId) return;
  if (!state.ai || !state.ai.configured) {
    toast('先在「设置」里配置 DeepSeek API Key', 'error');
    navigate('settings');
    return;
  }
  const answers = collectAnswers();
  const filled = Object.values(answers).filter((value) => String(value || '').trim()).length;
  if (!filled) {
    toast('先填写答案再判卷', 'error');
    return;
  }
  const button = $('#ai-check');
  const original = button.textContent;
  button.disabled = true;
  button.textContent = 'AI 判卷中…';
  toast('正在让 DeepSeek 判卷，通常需要十几秒到一分钟…');
  try {
    const data = await api(`/api/sessions/${state.currentSessionId}/ai-check`, {
      method: 'POST',
      body: { answers },
    });
    state.session = data.session;
    state.currentSessionId = data.session.id;
    state.words = data.session.words || [];
    renderAnswers(data.session);
    renderResult(data);
    showView('answers');
    await loadState();
    toast(`AI 判卷完成：正确 ${data.check.summary.correct} / ${data.check.summary.total}`);
  } catch (err) {
    toast(err.message, 'error');
  } finally {
    button.disabled = false;
    button.textContent = original;
  }
}

function openBatchDialog() {
  const dialog = $('#batch-dialog');
  const textarea = $('#batch-text');
  if (!dialog || !textarea) return;
  const lines = $$('#answer-rows input[data-n]').map((input) => {
    const value = input.value.trim();
    return value ? `${input.dataset.n}. ${value}` : '';
  }).filter(Boolean);
  textarea.value = lines.join('\n');
  if (typeof dialog.showModal === 'function') dialog.showModal();
  textarea.focus();
}

function applyBatchAnswers() {
  const text = $('#batch-text').value;
  const map = {};
  text.split(/\r?\n/).forEach((line) => {
    const match = line.match(/^\s*(\d+)\s*[.、:：)]?\s*(.+?)\s*$/);
    if (match) map[match[1]] = match[2];
  });
  const keys = Object.keys(map);
  if (!keys.length) {
    toast('没有识别到「题号 + 答案」，例如：1. 放弃', 'error');
    return;
  }
  let applied = 0;
  $$('#answer-rows input[data-n]').forEach((input) => {
    const value = map[input.dataset.n];
    if (value !== undefined) {
      input.value = value;
      input.dispatchEvent(new Event('input', { bubbles: true }));
      applied += 1;
    }
  });
  $('#batch-dialog').close();
  updateAnswerCount();
  saveDraft();
  toast(`已填入 ${applied} 题`);
}

async function exportPacket() {
  if (!state.currentSessionId) return;
  const id = state.currentSessionId;
  try {
    if (!state.session || !state.session.has_check) {
      const answers = collectAnswers();
      await api(`/api/sessions/${id}/answers`, { method: 'POST', body: { answers } });
      if (state.session) {
        state.session.has_answers = true;
        state.session.answers = answers;
      }
      const status = $('#answers-status');
      if (status) { status.textContent = '已保存，待检查'; status.className = 'tag warm'; }
    }
  } catch (err) {
    toast(err.message, 'error');
    return;
  }
  const a = document.createElement('a');
  a.href = `/api/sessions/${id}/sheet?filled=1`;
  a.download = `dictation-packet-${id}.md`;
  document.body.appendChild(a);
  a.click();
  a.remove();
  toast('答题包已导出（含场次 ID 和种子）');
}

function renderResult(report) {
  const check = report.check || {};
  const session = report.session || state.session || {};
  const summary = check.summary || {};
  const items = check.items || [];
  const panel = $('#result-panel');
  if (!panel) return;
  const form = $('#answers-form');
  if (form) form.classList.add('hidden');
  panel.classList.remove('hidden');
  const status = $('#answers-status');
  if (status) { status.textContent = '已检查'; status.className = 'tag warm'; }
  const accuracy = Math.round(Number(summary.accuracy || 0) * 1000) / 10;
  const newly = check.newly_mastered || [];
  const before = check.book_counts_before || {};
  const after = check.book_counts_after || {};
  const rows = items.map((item) => {
    const verdict = item.verdict || 'wrong';
    const expected = verdict === 'correct' ? '—' : escapeHtml(meaningText(item.expected || ''));
    const reason = item.reason
      ? `<div class="hint">AI：${escapeHtml(item.reason)}</div>`
      : '';
    return `
      <tr class="${escapeHtml(verdict)}">
        <td>${item.n}</td>
        <td>
          <div class="result-word">
            <b>${escapeHtml(item.word)}</b>
            <button class="speak-btn" type="button" data-speak-word="${escapeHtml(item.word)}" title="听发音" aria-label="听 ${escapeHtml(item.word)}">${SPEAKER_SVG}</button>
          </div>
          <span class="hint">${escapeHtml(item.phonetic || '')}</span>
        </td>
        <td>${escapeHtml(item.answer || '—')}</td>
        <td><span class="verdict ${escapeHtml(verdict)}">${VERDICT_LABELS[verdict] || verdict}</span></td>
        <td class="result-meaning">${expected}${reason}</td>
      </tr>`;
  }).join('');
  const engineLine = check.engine === 'deepseek'
    ? `DeepSeek · ${escapeHtml(check.model || 'deepseek-chat')}`
    : '本地/助手检查';
  panel.innerHTML = `
    <div class="card-head">
      <div>
        <h2>检查结果</h2>
        <p class="hint">${escapeHtml(session.id)} · ${escapeHtml(session.source_name || '')} · 种子 ${escapeHtml(session.seed)} · ${fmtDate(check.applied_at)}</p>
      </div>
      <span class="tag">已检查</span>
    </div>
    <p class="result-ai-line">判卷引擎：${engineLine}</p>
    <div class="result-summary">
      <div class="result-stat"><b>${summary.correct ?? 0}</b><span>正确</span></div>
      <div class="result-stat"><b>${summary.wrong ?? 0}</b><span>错误</span></div>
      <div class="result-stat"><b>${summary.blank ?? 0}</b><span>未作答</span></div>
      <div class="result-stat"><b>${accuracy}%</b><span>正确率</span></div>
    </div>
    ${newly.length ? `<div class="newly-mastered">本轮新掌握 ${newly.length} 个：${newly.map(escapeHtml).join('、')}</div>` : '<p class="hint">本轮没有新掌握的词。</p>'}
    <p class="book-delta">新词本 ${fmtNumber(before.new)} → ${fmtNumber(after.new)} · 错题本 ${fmtNumber(before.wrong)} → ${fmtNumber(after.wrong)} · 已掌握 ${fmtNumber(before.mastered)} → ${fmtNumber(after.mastered)}</p>
    <div class="table-wrap result-table-wrap">
      <table class="result-table">
        <thead><tr><th>#</th><th>单词</th><th>你的答案</th><th>结果</th><th>参考释义</th></tr></thead>
        <tbody>${rows || '<tr><td colspan="5" class="empty-row">没有题目</td></tr>'}</tbody>
      </table>
    </div>
    <div class="actions">
      <button class="ghost" type="button" data-replay-session="${escapeHtml(session.id)}">重听本轮</button>
      <button class="ghost" type="button" data-retry-wrong="${escapeHtml(session.id)}">重听本轮错题</button>
      <button class="ghost" type="button" data-export-packet="${escapeHtml(session.id)}">导出答题包</button>
      <button class="ghost" type="button" data-copy-check="${escapeHtml(session.id)}">复制检查指令</button>
      <button class="ghost" type="button" data-nav="setup">返回听写</button>
    </div>
  `;
  if (form) form.classList.add('hidden');
}

/* ------------------------------------------------------------------ */
/* books                                                               */
/* ------------------------------------------------------------------ */
async function loadBooks() {
  const params = new URLSearchParams({
    book: state.books.book,
    level: state.books.level,
    sort: state.books.sort,
    q: state.books.q,
    page: String(state.books.page),
    page_size: String(state.books.pageSize),
  });
  try {
    const data = await api(`/api/books?${params.toString()}`);
    state.books = { ...state.books, ...data };
    renderBooks(data);
  } catch (err) {
    toast(err.message, 'error');
  }
}

function renderBooks(data) {
  const body = $('#book-body');
  const summary = $('#book-summary');
  const pager = $('#book-pager');
  if (!body) return;
  $$('#book-tabs button').forEach((btn) => {
    btn.classList.toggle('active', btn.dataset.book === state.books.book);
  });
  const search = $('#book-search');
  if (search && search.value !== state.books.q) search.value = state.books.q;
  const sort = $('#book-sort');
  if (sort) sort.value = state.books.sort;
  const threshold = Number(state.settings.threshold || 5);
  const bookLabels = {
    new: `新词本 = 所有还没连续答对 ${threshold} 次的词（含错题）。`,
    wrong: `错题本 = 新词本里答错过的词；连续答对 ${threshold} 次后同样移出。`,
    mastered: `已掌握 = 连续答对 ${threshold} 次；可以重新学习放回新词本。`,
  };
  if (summary) {
    summary.textContent = `${bookLabels[state.books.book] || ''} 当前显示 ${fmtNumber(data.total)} 个。`;
  }
  if (!data.items || !data.items.length) {
    body.innerHTML = `<tr><td colspan="5" class="empty-row">这个词本还是空的。</td></tr>`;
  } else {
    body.innerHTML = data.items.map((item) => {
      const streak = Number(item.streak || 0);
      const wrong = Number(item.wrong_count || 0);
      const action = state.books.book === 'mastered'
        ? `<button class="row-btn danger-text" type="button" data-reset-word="${escapeHtml(item.word)}">重新学习</button>`
        : `<button class="row-btn" type="button" data-word-detail="${escapeHtml(item.word)}">详情</button>`;
      const speak = `<button class="speak-btn" type="button" data-speak-word="${escapeHtml(item.word)}" title="听发音" aria-label="听 ${escapeHtml(item.word)}">${SPEAKER_SVG}</button>`;
      return `
        <tr>
          <td>
            <span class="book-word" data-word-detail="${escapeHtml(item.word)}">${escapeHtml(item.word)}
              <span class="phonetic">${escapeHtml(item.phonetic || '')}</span>
            </span>
          </td>
          <td class="book-meaning">${escapeHtml(truncate(meaningText(item.translation), 110))}</td>
          <td><span class="streak-badge ${streak ? '' : 'zero'}">${streak}</span></td>
          <td>${wrong ? `<span class="wrong-badge">${wrong}</span>` : '—'}</td>
          <td>${action} ${speak}</td>
        </tr>`;
    }).join('');
  }
  if (pager) {
    const pages = Math.max(1, Math.ceil(Number(data.total || 0) / Number(state.books.pageSize || 50)));
    const page = Number(state.books.page || 1);
    pager.innerHTML = `
      <button class="row-btn" type="button" data-page="${page - 1}" ${page <= 1 ? 'disabled' : ''}>← 上一页</button>
      <span>第 ${page} / ${pages} 页</span>
      <button class="row-btn" type="button" data-page="${page + 1}" ${page >= pages ? 'disabled' : ''}>下一页 →</button>`;
  }
  if (state.books.counts) {
    const stats = state.books.counts;
    const newEl = $('#stat-new');
    if (newEl) newEl.textContent = fmtNumber(stats.new);
    const wrongEl = $('#stat-wrong');
    if (wrongEl) wrongEl.textContent = fmtNumber(stats.wrong);
    const doneEl = $('#stat-mastered');
    if (doneEl) doneEl.textContent = fmtNumber(stats.mastered);
  }
}

function openWordDialog(word) {
  const item = state.books.items.find((entry) => entry.word === word);
  if (!item) return;
  const dialog = $('#word-dialog');
  $('#dialog-word').textContent = item.word;
  const tags = (item.tags || []).map((tag) => tag.toUpperCase()).join(' · ');
  $('#dialog-body').innerHTML = `
    <div class="phonetic">${escapeHtml(item.phonetic || '')} · 词频 ${fmtNumber(item.frq)} · ${escapeHtml(tags)}</div>
    <div class="dialog-meaning">${escapeHtml(item.translation || '')}</div>
    <div class="dialog-meta">
      连续答对 ${item.streak || 0} 次 · 答错 ${item.wrong_count || 0} 次 · 总出现 ${item.total_seen || 0} 次<br>
      最后出现：${item.last_seen ? fmtDate(item.last_seen) : '—'} · 状态：${item.mastered ? '已掌握' : (item.wrong_count ? '错题本' : '新词本')}
    </div>`;
  if (typeof dialog.showModal === 'function') dialog.showModal();
}

async function resetWord(word) {
  if (!word) return;
  if (!window.confirm(`把「${word}」重新放回新词本？\n它会从零开始累计连续答对次数。`)) return;
  try {
    await api('/api/reset', { method: 'POST', body: { scope: 'word', word } });
    toast(`已重置：${word}`);
    await loadState();
    loadBooks();
  } catch (err) {
    toast(err.message, 'error');
  }
}

function startWrongbook() {
  const wrongSource = state.sources.find((source) => source.id === 'wrong_all');
  if (!wrongSource || !wrongSource.count) {
    toast('错题本还是空的，先去听一轮吧。');
    return;
  }
  const select = $('#source');
  if (select) select.value = 'wrong_all';
  $('#pool').value = '0';
  $('#count').value = String(Math.min(20, wrongSource.count));
  setMode('auto');
  updateSetup();
  showView('setup');
  updateSetup();
}

/* ------------------------------------------------------------------ */
/* history                                                             */
/* ------------------------------------------------------------------ */
async function loadHistory() {
  try {
    const data = await api('/api/history?limit=100');
    state.history = data.items || [];
    renderHistory();
  } catch (err) {
    toast(err.message, 'error');
  }
}

function renderHistory() {
  const body = $('#history-body');
  if (!body) return;
  if (!state.history.length) {
    body.innerHTML = '<tr><td colspan="7" class="empty-row">还没有听写记录。</td></tr>';
    return;
  }
  body.innerHTML = state.history.map((item) => {
    const accuracy = item.accuracy != null ? `${Math.round(item.accuracy * 100)}%` : '—';
    const accuracyClass = item.accuracy != null ? (item.accuracy >= 0.8 ? 'good' : 'bad') : '';
    const actions = [];
    if (item.status === 'checked') {
      actions.push(`<button class="row-btn" type="button" data-history-action="open" data-id="${escapeHtml(item.id)}">看结果</button>`);
    } else if (item.status === 'answered') {
      actions.push(`<button class="row-btn" type="button" data-history-action="copy" data-id="${escapeHtml(item.id)}">复制检查指令</button>`);
      actions.push(`<button class="row-btn" type="button" data-history-action="open" data-id="${escapeHtml(item.id)}">填写</button>`);
    } else {
      actions.push(`<button class="row-btn" type="button" data-history-action="open" data-id="${escapeHtml(item.id)}">填写</button>`);
    }
    actions.push(`<button class="row-btn" type="button" data-history-action="replay" data-id="${escapeHtml(item.id)}">重听</button>`);
    return `
      <tr>
        <td>${fmtDate(item.created_at)}</td>
        <td>${escapeHtml(item.source_name || '—')}</td>
        <td>${escapeHtml(item.seed)}</td>
        <td>${item.heard_count || 0} / ${item.count || 0}</td>
        <td><span class="status ${escapeHtml(item.status)}">${STATUS_LABELS[item.status] || item.status}</span></td>
        <td><span class="accuracy ${accuracyClass}">${accuracy}</span></td>
        <td><div class="history-actions">${actions.join('')}</div></td>
      </tr>`;
  }).join('');
}

async function replaySession(id) {
  try {
    if (isSessionBusy()) {
      if (!window.confirm('正在听写，重新开始另一轮会停止当前播放。继续吗？')) return;
      stopPlayback();
    }
    const detail = await api(`/api/sessions/${id}`);
    startSession(detail);
  } catch (err) {
    toast(err.message, 'error');
  }
}

async function retryWrongWords(id) {
  try {
    const data = await api(`/api/sessions/${id}/retry-wrong`, {
      method: 'POST',
      body: { mode: state.mode, interval: state.interval, repeat: state.repeat },
    });
    toast(`本轮错题 ${data.wrong_count} 个，开始重听`);
    startSession(data.session);
  } catch (err) {
    toast(err.message, 'error');
  }
}

/* ------------------------------------------------------------------ */
/* settings                                                            */
/* ------------------------------------------------------------------ */
async function saveThreshold() {
  const input = $('#threshold-input');
  const value = parseInt(input.value || '5', 10);
  if (!Number.isFinite(value) || value < 1 || value > 20) {
    toast('掌握阈值需在 1–20 之间', 'error');
    return;
  }
  try {
    const data = await api('/api/settings', { method: 'PUT', body: { threshold: value } });
    state.settings = data.settings || state.settings;
    toast('掌握阈值已更新');
    await loadState();
  } catch (err) {
    toast(err.message, 'error');
  }
}

async function resetAll() {
  if (!window.confirm('确定清空学习记录吗？\n现有检查记录会归档到 data/archive/，新词本回到全部单词。')) return;
  try {
    await api('/api/reset', { method: 'POST', body: { scope: 'all' } });
    toast('学习记录已清空，新词本回到全部单词');
    await loadState();
  } catch (err) {
    toast(err.message, 'error');
  }
}

/* ------------------------------------------------------------------ */
/* events                                                              */
/* ------------------------------------------------------------------ */
function bindEvents() {
  document.addEventListener('click', (event) => {
    const nav = event.target.closest('[data-nav]');
    if (!nav) return;
    event.preventDefault();
    navigate(nav.dataset.nav);
  });

  $('#reroll').addEventListener('click', () => setSeed(randomSeed()));
  $('#source').addEventListener('change', () => {
    savePlaybackSettings();
    updateSetup();
  });
  $('#pool').addEventListener('input', updateSetup);
  $('#count').addEventListener('input', updateSetup);
  $('#mode-seg').addEventListener('click', (event) => {
    const button = event.target.closest('button[data-mode]');
    if (!button) return;
    setMode(button.dataset.mode);
    savePlaybackSettings();
  });
  $('#order-seg').addEventListener('click', (event) => {
    const button = event.target.closest('button[data-order]');
    if (!button) return;
    setOrder(button.dataset.order);
    savePlaybackSettings();
  });
  $('#interval').addEventListener('input', () => {
    updateIntervalLabel();
    savePlaybackSettings();
  });
  $('#rate').addEventListener('input', () => {
    updateRateLabel();
    savePlaybackSettings();
  });
  $('#repeat').addEventListener('change', () => {
    state.repeat = Number($('#repeat').value) === 2 ? 2 : 1;
    savePlaybackSettings();
  });
  $('#show-word').addEventListener('change', () => {
    state.showWord = $('#show-word').checked;
    savePlaybackSettings();
  });
  $('#voice').addEventListener('change', () => {
    state.voiceURI = $('#voice').value;
    $('#voice').title = ($('#voice').selectedOptions[0] || {}).textContent || '';
    savePlaybackSettings();
    setVoiceDiagnostic();
  });
  $('#test-voice').addEventListener('click', testVoice);
  $('#test-voice-2').addEventListener('click', testVoice);
  $('#start-btn').addEventListener('click', startNewSession);
  $('#refresh-state').addEventListener('click', async () => {
    await loadState();
    toast('已刷新');
  });

  $('#leave-session').addEventListener('click', () => {
    if (isSessionBusy() && !window.confirm('结束本轮并返回？已播放的部分会保留在记录里。')) return;
    stopPlayback();
    showView('setup');
    loadState();
  });
  $('#pause-btn').addEventListener('click', togglePause);
  $('#repeat-btn').addEventListener('click', repeatCurrent);
  $('#prev-btn').addEventListener('click', prevWord);
  $('#next-btn').addEventListener('click', skipToNext);
  $('#finish-btn').addEventListener('click', () => {
    if (!state.session) return;
    if (!window.confirm('结束本轮并填写答题纸？')) return;
    finishSession(true);
  });

  $('#save-answers').addEventListener('click', saveAnswers);
  $('#ai-check').addEventListener('click', runAiCheck);
  $('#batch-answers').addEventListener('click', openBatchDialog);
  $('#export-packet').addEventListener('click', exportPacket);
  $('#download-sheet').addEventListener('click', downloadSheet);
  $('#copy-command').addEventListener('click', () => {
    if (state.currentSessionId) copyText(`检查 ${state.currentSessionId}`);
  });
  $('#replay-session').addEventListener('click', () => {
    if (state.currentSessionId) replaySession(state.currentSessionId);
  });
  $('#answer-rows').addEventListener('input', () => {
    updateAnswerCount();
    saveDraft();
  });
  $('#session-answer').addEventListener('input', handleSessionAnswerInput);
  $('#session-answer').addEventListener('keydown', (event) => {
    if (event.isComposing || event.keyCode === 229) return;
    if (event.key === 'Enter') {
      event.preventDefault();
      skipToNext();
    } else if (event.key === ' ') {
      event.preventDefault();
      togglePause();
    } else if (event.key === 'ArrowUp' || event.key === 'ArrowLeft') {
      event.preventDefault();
      prevWord();
    } else if (event.key === 'ArrowDown' || event.key === 'ArrowRight') {
      event.preventDefault();
      skipToNext();
    } else if (event.key === 'Escape') {
      event.preventDefault();
      $('#session-answer').blur();
    }
  });
  $('#result-panel').addEventListener('click', (event) => {
    const speak = event.target.closest('[data-speak-word]');
    if (speak) { speakWord(speak.dataset.speakWord); return; }
    const replay = event.target.closest('[data-replay-session]');
    if (replay) { replaySession(replay.dataset.replaySession); return; }
    const retry = event.target.closest('[data-retry-wrong]');
    if (retry) { retryWrongWords(retry.dataset.retryWrong); return; }
    const exportBtn = event.target.closest('[data-export-packet]');
    if (exportBtn) { exportPacket(); return; }
    const copyBtn = event.target.closest('[data-copy-check]');
    if (copyBtn) copyText(`检查 ${copyBtn.dataset.copyCheck}`);
  });

  $('#batch-apply').addEventListener('click', applyBatchAnswers);
  $('#batch-close').addEventListener('click', () => $('#batch-dialog').close());
  $('#batch-cancel').addEventListener('click', () => $('#batch-dialog').close());
  $('#batch-dialog').addEventListener('click', (event) => {
    if (event.target === $('#batch-dialog')) $('#batch-dialog').close();
  });

  $('#ai-save').addEventListener('click', async () => {
    const button = $('#ai-save');
    const original = button.textContent;
    button.disabled = true;
    button.textContent = '保存中…';
    try { await saveAiSettings(); }
    finally { button.disabled = false; button.textContent = original; }
  });
  $('#ai-test').addEventListener('click', testAiSettings);
  $('#ai-clear').addEventListener('click', clearAiSettings);

  $('#wordbook-import').addEventListener('click', importWordbook);
  $('#wordbook-template-json').addEventListener('click', () => downloadWordbookTemplate('json'));
  $('#wordbook-template-csv').addEventListener('click', () => downloadWordbookTemplate('csv'));
  $('#wordbook-list').addEventListener('click', (event) => {
    const button = event.target.closest('[data-delete-wordbook]');
    if (button) deleteWordbook(button.dataset.deleteWordbook);
  });

  $('#book-tabs').addEventListener('click', (event) => {
    const button = event.target.closest('button[data-book]');
    if (!button) return;
    state.books.book = button.dataset.book;
    state.books.page = 1;
    loadBooks();
  });
  $('#book-search').addEventListener('input', () => {
    clearTimeout(searchTimer);
    searchTimer = setTimeout(() => {
      state.books.q = $('#book-search').value.trim();
      state.books.page = 1;
      loadBooks();
    }, 300);
  });
  $('#book-sort').addEventListener('change', () => {
    state.books.sort = $('#book-sort').value;
    state.books.page = 1;
    loadBooks();
  });
  $('#book-pager').addEventListener('click', (event) => {
    const button = event.target.closest('[data-page]');
    if (!button || button.disabled) return;
    const page = parseInt(button.dataset.page, 10);
    if (!Number.isFinite(page) || page < 1) return;
    state.books.page = page;
    loadBooks();
  });
  $('#book-body').addEventListener('click', (event) => {
    const speak = event.target.closest('[data-speak-word]');
    if (speak) { speakWord(speak.dataset.speakWord); return; }
    const reset = event.target.closest('[data-reset-word]');
    if (reset) { resetWord(reset.dataset.resetWord); return; }
    const detail = event.target.closest('[data-word-detail]');
    if (detail) openWordDialog(detail.dataset.wordDetail);
  });
  $('#start-wrongbook').addEventListener('click', startWrongbook);
  $('#export-book').addEventListener('click', () => {
    const params = new URLSearchParams({ book: state.books.book, level: state.books.level });
    window.location.href = `/api/export/notebook?${params.toString()}`;
  });

  $('#refresh-history').addEventListener('click', loadHistory);
  $('#history-body').addEventListener('click', (event) => {
    const button = event.target.closest('[data-history-action]');
    if (!button) return;
    const id = button.dataset.id;
    const action = button.dataset.historyAction;
    if (action === 'copy') copyText(`检查 ${id}`);
    else if (action === 'open') openAnswers(id);
    else if (action === 'replay') replaySession(id);
  });
  $('#recent-list').addEventListener('click', (event) => {
    const open = event.target.closest('[data-open-session]');
    if (open) { openAnswers(open.dataset.openSession); return; }
    const replay = event.target.closest('[data-replay-session]');
    if (replay) replaySession(replay.dataset.replaySession);
  });

  $('#save-threshold').addEventListener('click', saveThreshold);
  $('#reset-all').addEventListener('click', resetAll);

  $('#dialog-close').addEventListener('click', () => $('#word-dialog').close());
  $('#word-dialog').addEventListener('click', (event) => {
    if (event.target === $('#word-dialog')) $('#word-dialog').close();
  });

  document.addEventListener('keydown', (event) => {
    const tag = (event.target.tagName || '').toLowerCase();
    if (['input', 'textarea', 'select'].includes(tag) || event.target.isContentEditable) return;
    if (state.view !== 'session' || !state.session) return;
    if (event.code === 'Space') {
      event.preventDefault();
      if (state.mode === 'manual' && state.phase === 'waiting') nextWord();
      else if (state.mode === 'manual' && state.phase === 'paused') playCurrent();
      else togglePause();
    } else if (event.key === 'Enter' && state.mode === 'manual') {
      event.preventDefault();
      nextWord();
    } else if (event.key === 'r' || event.key === 'R') {
      event.preventDefault();
      repeatCurrent();
    } else if (event.key === 'ArrowLeft' || event.key === 'Backspace') {
      event.preventDefault();
      prevWord();
    } else if (event.key === 'ArrowRight' || event.key === 'n' || event.key === 'N') {
      event.preventDefault();
      skipToNext();
    } else if (event.key === 'Escape') {
      event.preventDefault();
      if (!window.confirm('结束本轮并填写答题纸？')) return;
      finishSession(true);
    }
  });

  document.addEventListener('visibilitychange', () => {
    if (document.hidden && isSessionBusy() && state.phase !== 'paused') {
      pauseSession();
      toast('页面切到后台，已自动暂停。');
    }
  });

  window.addEventListener('beforeunload', (event) => {
    if (isSessionBusy()) {
      event.preventDefault();
      event.returnValue = '';
    }
  });

  if ('speechSynthesis' in window) {
    window.speechSynthesis.addEventListener('voiceschanged', loadVoices);
  }
}

/* ------------------------------------------------------------------ */
/* boot                                                                */
/* ------------------------------------------------------------------ */
showView('setup');
