"""Hold the static page, stylesheet and script of the Owner web console as plain strings served from their own routes.

The page needs no template engine and no external library; all untrusted text is placed with textContent, never as markup.
"""

TOKEN_HEADER_NAME = "X-Console-Token"  # [shared contract]: Header the script uses to present the access code; must equal the server's TOKEN_HEADER.
BATCH_SIZE_LIMIT = 100  # [shared contract]: Largest batch the server accepts; the script splits longer selections into chunks of this size.

INDEX_HTML = """<!doctype html>
<html lang="zh-Hant">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<meta name="color-scheme" content="light dark">
<meta name="referrer" content="no-referrer">
<title>歌曲審核</title>
<link rel="stylesheet" href="/app.css">
</head>
<body>
<header class="top">
<h1>歌曲審核</h1>
<span id="count" class="muted"></span>
<button id="reload" type="button">重新載入</button>
</header>
<main>
<section class="lookup" aria-label="查兌換問題">
<h2>查兌換問題</h2>
<form id="lookup-form" class="group">
<input id="lookup-code" type="text" inputmode="numeric" autocomplete="off" placeholder="輸入使用者回報的編號，例如 7" aria-label="兌換編號">
<button id="lookup-button" type="submit">查詢</button>
</form>
<p id="lookup-result" class="notice" hidden></p>
</section>
<section class="toolbar" aria-label="篩選與批次操作">
<div id="filters" class="group" role="group" aria-label="篩選">
<button type="button" data-filter="all" aria-pressed="true">全部</button>
<button type="button" data-filter="doubt" aria-pressed="false">有疑慮</button>
<button type="button" data-filter="duration" aria-pressed="false">等時長</button>
<button type="button" data-filter="duplicate" aria-pressed="false">等確認是否重複</button>
<button type="button" data-filter="frozen" aria-pressed="false">凍結衝突</button>
</div>
<div class="group" role="group" aria-label="批次操作">
<button id="select-all" type="button">全選</button>
<button id="all-yes" type="button">全部選是</button>
<button id="all-no" type="button">全部選不是</button>
<button id="submit" type="button" class="primary">送出</button>
</div>
</section>
<div id="status" role="status" aria-live="polite" class="notice" hidden></div>
<section id="results-panel" hidden>
<h2>處理結果</h2>
<ul id="results" class="results"></ul>
</section>
<ul id="cases" class="cases"></ul>
</main>
<script src="/app.js" defer></script>
</body>
</html>
"""

APP_CSS = """:root {
  color-scheme: light dark;
  --bg: #f6f6f3;
  --surface: #ffffff;
  --text: #1c1c1a;
  --muted: #62625c;
  --border: #d6d6cf;
  --accent: #1f5fbf;
  --accent-text: #ffffff;
  --good: #1b6b3a;
  --bad: #a3261f;
  --warn-bg: #fff4d6;
}
@media (prefers-color-scheme: dark) {
  :root {
    --bg: #131312;
    --surface: #1d1d1b;
    --text: #ecece6;
    --muted: #a3a39b;
    --border: #3a3a36;
    --accent: #6ea3f2;
    --accent-text: #0c1a30;
    --good: #6fcf97;
    --bad: #f29a94;
    --warn-bg: #3b3216;
  }
}
* { box-sizing: border-box; }
body { margin: 0; padding: 0 16px 48px; background: var(--bg); color: var(--text); font: 16px/1.5 system-ui, "Noto Sans TC", sans-serif; }
.top { display: flex; align-items: center; gap: 12px; flex-wrap: wrap; padding: 16px 0 8px; }
.top h1 { font-size: 1.3rem; margin: 0; }
.muted { color: var(--muted); }
main { max-width: 960px; margin: 0 auto; }
.top { max-width: 960px; margin: 0 auto; }
button { font: inherit; color: inherit; background: var(--surface); border: 1px solid var(--border); border-radius: 8px; padding: 8px 14px; min-height: 40px; cursor: pointer; }
button[aria-pressed="true"] { background: var(--accent); color: var(--accent-text); border-color: var(--accent); }
button.primary { background: var(--accent); color: var(--accent-text); border-color: var(--accent); font-weight: 600; }
button:disabled { opacity: 0.55; cursor: not-allowed; }
.toolbar { position: sticky; top: 0; z-index: 2; display: flex; flex-wrap: wrap; gap: 8px 16px; justify-content: space-between; padding: 8px 0; background: var(--bg); border-bottom: 1px solid var(--border); }
.lookup { margin: 8px 0; padding: 8px 12px; background: var(--surface); border: 1px solid var(--border); border-radius: 12px; }
.lookup h2 { font-size: 1rem; margin: 0 0 8px; }
.lookup input { font: inherit; min-height: 40px; padding: 4px 10px; flex: 1 1 240px; color: inherit; background: var(--bg); border: 1px solid var(--border); border-radius: 8px; }
.group { display: flex; flex-wrap: wrap; gap: 8px; }
.notice { margin: 12px 0; padding: 10px 14px; border-radius: 8px; background: var(--warn-bg); border: 1px solid var(--border); }
.notice.error { border-color: var(--bad); }
.cases, .results { list-style: none; margin: 12px 0; padding: 0; display: grid; gap: 12px; }
.case { display: grid; grid-template-columns: 160px 1fr; gap: 12px; padding: 12px; background: var(--surface); border: 1px solid var(--border); border-radius: 12px; }
.thumb { width: 160px; height: 90px; border-radius: 8px; background: var(--border); object-fit: cover; }
.thumb.empty { display: flex; align-items: center; justify-content: center; color: var(--muted); font-size: 0.85rem; }
.info { min-width: 0; overflow-wrap: anywhere; }
.line { margin: 2px 0; }
.doubt { color: var(--bad); }
.choices { grid-column: 1 / -1; display: flex; flex-wrap: wrap; gap: 8px 20px; align-items: center; border: 0; padding: 0; margin: 0; }
.choices label { display: inline-flex; align-items: center; gap: 6px; min-height: 40px; }
.choices input[type="radio"], .choices input[type="checkbox"] { width: 20px; height: 20px; }
.choices select { font: inherit; max-width: 100%; min-height: 40px; }
.blocked { grid-column: 1 / -1; margin: 0; color: var(--muted); }
.results li { padding: 8px 12px; border-radius: 8px; border: 1px solid var(--border); background: var(--surface); }
.results li.ok { border-color: var(--good); }
.results li.fail { border-color: var(--bad); }
a { color: var(--accent); }
@media (max-width: 600px) {
  .case { grid-template-columns: 1fr; }
  .thumb { width: 100%; height: auto; aspect-ratio: 16 / 9; }
  .toolbar { position: static; }
}
"""

_JS_STATE = r"""'use strict';
const TOKEN_HEADER = 'X-Console-Token';
const BATCH_LIMIT = 100;
const THUMB_PREFIX = 'https://i.ytimg.com/vi/';
const WATCH_PREFIX = 'https://www.youtube.com/watch?v=';
const VIDEO_ID_PATTERN = /^[A-Za-z0-9_-]{11}$/;
const FILTERS = {
  all: () => true,
  doubt: (item) => item.doubt_text !== '',
  duration: (item) => item.needs_duration,
  duplicate: (item) => item.requires_duplicate_pick,
  frozen: (item) => item.frozen_conflict,
};
const state = { token: '', cases: [], known: [], filter: 'all', picks: new Map(), busy: false };

function readTokenOnce() {
  const fragment = new URLSearchParams(location.hash.slice(1));
  state.token = fragment.get('token') || '';
  history.replaceState(null, '', location.pathname);
}

function pickFor(operationId) {
  if (!state.picks.has(operationId)) state.picks.set(operationId, { checked: false, answer: '', duplicate: '' });
  return state.picks.get(operationId);
}

function isSelectable(item) {
  return item.blocked_reason === '' && (item.can_yes || item.can_no);
}

function visibleCases() {
  return state.cases.filter(FILTERS[state.filter]);
}
"""

_JS_NETWORK = r"""
async function callApi(path, body) {
  const options = { cache: 'no-store', headers: { [TOKEN_HEADER]: state.token } };
  if (body !== undefined) {
    options.method = 'POST';
    options.headers['Content-Type'] = 'application/json';
    options.body = JSON.stringify(body);
  }
  let response;
  try {
    response = await fetch(path, options);
  } catch (networkError) {
    throw new Error('連不上審核網頁的伺服器，請確認 SSH 通道還在，再按「重新載入」。');
  }
  let payload = null;
  try { payload = await response.json(); } catch (parseError) { payload = null; }
  if (!response.ok) throw new Error((payload && payload.message) || '伺服器回應失敗，請按「重新載入」。');
  return payload;
}

function showStatus(message, isError) {
  const box = document.getElementById('status');
  box.textContent = message;
  box.classList.toggle('error', Boolean(isError));
  box.hidden = message === '';
}

function setBusy(isBusy) {
  state.busy = isBusy;
  for (const id of ['submit', 'all-yes', 'all-no', 'select-all']) document.getElementById(id).disabled = isBusy;
}
"""

_JS_RENDER = r"""
function el(tag, className, text) {
  const node = document.createElement(tag);
  if (className) node.className = className;
  if (text !== undefined) node.textContent = text;
  return node;
}

function linkNode(href, label) {
  if (typeof href !== 'string' || !href.startsWith(WATCH_PREFIX)) return el('span', 'muted', label + '（連結無法辨識）');
  const anchor = el('a', '', label);
  anchor.href = href;
  anchor.target = '_blank';
  anchor.rel = 'noopener noreferrer';
  return anchor;
}

function thumbNode(item) {
  if (typeof item.video_id !== 'string' || !VIDEO_ID_PATTERN.test(item.video_id)) return el('div', 'thumb empty', '沒有縮圖');
  const image = el('img', 'thumb');
  image.src = THUMB_PREFIX + item.video_id + '/mqdefault.jpg';
  image.alt = '';
  image.loading = 'lazy';
  image.referrerPolicy = 'no-referrer';
  return image;
}

function infoNode(item) {
  const box = el('div', 'info');
  box.append(el('p', 'line', '案件 ' + item.operation_id + '　受理順序 ' + item.sequence + '　投稿者 ' + item.submitter_id));
  const line = el('p', 'line');
  line.append(linkNode(item.link, '開啟影片連結'));
  box.append(line, el('p', 'line', '狀態：' + item.status_text));
  if (item.doubt_text) box.append(el('p', 'line doubt', '疑慮：' + item.doubt_text));
  return box;
}

function radioNode(item, value, label) {
  const wrapper = el('label');
  const input = el('input');
  input.type = 'radio';
  input.name = 'answer-' + item.operation_id;
  input.value = value;
  const pick = pickFor(item.operation_id);
  input.checked = pick.answer === value;
  input.disabled = value === 'yes' ? !item.can_yes : !item.can_no;
  wrapper.append(input, label);
  return { wrapper, input };
}

function duplicatePicker(item) {
  const select = el('select');
  select.setAttribute('aria-label', '重複的是哪一首');
  select.append(new Option('請選擇重複的是哪一首', ''));
  for (const known of state.known) select.append(new Option('受理順序 ' + known.sequence + '（' + known.video_id + '）', String(known.sequence)));
  select.value = pickFor(item.operation_id).duplicate;
  return select;
}
"""

_JS_ROWS = r"""
function choicesNode(item) {
  const pick = pickFor(item.operation_id);
  const box = el('fieldset', 'choices');
  const check = el('input');
  check.type = 'checkbox';
  check.checked = pick.checked;
  check.addEventListener('change', () => { pick.checked = check.checked; });
  const checkLabel = el('label');
  checkLabel.append(check, '選取');
  const yes = radioNode(item, 'yes', item.yes_label);
  const no = radioNode(item, 'no', item.no_label);
  const picker = duplicatePicker(item);
  picker.hidden = !(item.requires_duplicate_pick && pick.answer === 'no');
  const choose = (answer) => { pick.answer = answer; pick.checked = true; check.checked = true; picker.hidden = !(item.requires_duplicate_pick && answer === 'no'); };
  yes.input.addEventListener('change', () => choose('yes'));
  no.input.addEventListener('change', () => choose('no'));
  picker.addEventListener('change', () => { pick.duplicate = picker.value; });
  box.append(checkLabel, yes.wrapper, no.wrapper, picker);
  return box;
}

function caseNode(item) {
  const row = el('li', 'case');
  row.append(thumbNode(item), infoNode(item));
  if (item.blocked_reason) row.append(el('p', 'blocked', item.blocked_reason));
  else row.append(choicesNode(item));
  return row;
}

function renderCases() {
  const list = document.getElementById('cases');
  list.replaceChildren(...visibleCases().map(caseNode));
  document.getElementById('count').textContent = '共 ' + state.cases.length + ' 筆待處理，目前顯示 ' + visibleCases().length + ' 筆';
  for (const button of document.querySelectorAll('[data-filter]')) button.setAttribute('aria-pressed', String(button.dataset.filter === state.filter));
}
"""

_JS_ACTIONS = r"""
async function loadCases() {
  showStatus('讀取中…', false);
  try {
    const payload = await callApi('/api/cases');
    state.cases = payload.cases;
    state.known = payload.known;
    renderCases();
    showStatus(payload.truncated ? '清單太長，只顯示前面一部分；處理完後請再按「重新載入」。' : '', false);
  } catch (loadError) {
    showStatus(loadError.message + '（可以再按一次「重新載入」。）', true);
  }
}

function applyToVisible(answer) {
  for (const item of visibleCases().filter(isSelectable)) {
    const allowed = answer === 'yes' ? item.can_yes : item.can_no;
    const pick = pickFor(item.operation_id);
    if (allowed) { pick.answer = answer; pick.checked = true; }
  }
  renderCases();
}

function toggleSelectAll() {
  const selectable = visibleCases().filter(isSelectable);
  const everyChecked = selectable.length > 0 && selectable.every((item) => pickFor(item.operation_id).checked);
  for (const item of selectable) pickFor(item.operation_id).checked = !everyChecked;
  renderCases();
}

function collectDecisions() {
  const decisions = [];
  const skipped = [];
  for (const item of state.cases.filter(isSelectable)) {
    const pick = pickFor(item.operation_id);
    if (!pick.checked) continue;
    if (!pick.answer) { skipped.push('案件 ' + item.operation_id + '：還沒選「是」或「不是」，這筆沒有送出。'); continue; }
    if (pick.answer === 'no' && item.requires_duplicate_pick && !pick.duplicate) { skipped.push('案件 ' + item.operation_id + '：請先選出重複的是哪一首，這筆沒有送出。'); continue; }
    const decision = { operation_id: item.operation_id, answer: pick.answer };
    if (pick.answer === 'no' && item.requires_duplicate_pick) decision.duplicate_of_sequence = Number(pick.duplicate);
    decisions.push(decision);
  }
  return { decisions, skipped };
}

function addResult(text, isOk) {
  document.getElementById('results-panel').hidden = false;
  document.getElementById('results').append(el('li', isOk ? 'ok' : 'fail', (isOk ? '成功　' : '失敗　') + text));
}

async function sendChunk(chunk) {
  try {
    const payload = await callApi('/api/decide', { decisions: chunk });
    for (const result of payload.results) addResult('案件 ' + result.operation_id + '：' + result.text, result.ok);
  } catch (sendError) {
    for (const decision of chunk) addResult('案件 ' + decision.operation_id + '：' + sendError.message + '（不確定有沒有處理，請重新載入確認。）', false);
  }
}

async function submitSelected() {
  const { decisions, skipped } = collectDecisions();
  document.getElementById('results').replaceChildren();
  document.getElementById('results-panel').hidden = skipped.length === 0;
  for (const text of skipped) addResult(text, false);
  if (decisions.length === 0) { showStatus('沒有可以送出的項目。', false); return; }
  setBusy(true);
  try {
    for (let start = 0; start < decisions.length; start += BATCH_LIMIT) await sendChunk(decisions.slice(start, start + BATCH_LIMIT));
  } finally {
    setBusy(false);
    state.picks.clear();
    await loadCases();
  }
}

function exchangeSummary(exchange) {
  const parts = ['編號 ' + exchange.operation_code, '狀態：' + exchange.state_text, '提出者：' + exchange.requester,
    '建立時間：' + exchange.created_at, '退點：' + exchange.refund_text, '已等待 ' + exchange.waiting_seconds + ' 秒'];
  if (exchange.reason_text) parts.push('卡住原因：' + exchange.reason_text);
  return parts.join('　');
}

async function lookupExchange(event) {
  event.preventDefault();
  const box = document.getElementById('lookup-result');
  const code = document.getElementById('lookup-code').value.trim();
  box.hidden = false;
  box.classList.remove('error');
  try {
    const payload = await callApi('/api/exchange?code=' + encodeURIComponent(code));
    box.textContent = payload.found ? exchangeSummary(payload.exchange) : payload.message;
  } catch (lookupError) {
    box.classList.add('error');
    box.textContent = lookupError.message;
  }
}

function start() {
  readTokenOnce();
  if (!state.token) { showStatus('找不到存取碼。請使用終端機印出的完整網址（含 #token=…）開啟。', true); return; }
  document.getElementById('lookup-form').addEventListener('submit', lookupExchange);
  document.getElementById('reload').addEventListener('click', loadCases);
  document.getElementById('select-all').addEventListener('click', toggleSelectAll);
  document.getElementById('all-yes').addEventListener('click', () => applyToVisible('yes'));
  document.getElementById('all-no').addEventListener('click', () => applyToVisible('no'));
  document.getElementById('submit').addEventListener('click', submitSelected);
  for (const button of document.querySelectorAll('[data-filter]')) button.addEventListener('click', () => { state.filter = button.dataset.filter; renderCases(); });
  loadCases();
}

start();
"""

APP_JS = _JS_STATE + _JS_NETWORK + _JS_RENDER + _JS_ROWS + _JS_ACTIONS
