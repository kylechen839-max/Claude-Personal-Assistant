// node --test tests/js/tabbar.test.mjs  (Node 18+; no packages needed)
//
// The phone's tab bar (Chat · Today · History · Settings, below 48rem).
// Runs kwil/web/app.js in node:vm (the fake DOM of app-composer.test.mjs)
// and checks syncTabs: the bar shows on the root screens only (the chat,
// with its Settings page, Today and History), exactly one tab is
// aria-current="page" (Settings while the menu is open), Today and History
// follow their header buttons after /api/state; and the taps: each tab works
// through the header's own control, Chat never focuses the field (no
// keyboard), the tab already shown does nothing. Then the markup, the CSS
// that hides it (keyboard, voice mode, wide screens) and the zh label.

import test from 'node:test';
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import vm from 'node:vm';

const SCRIPT = readFileSync(new URL('../../kwil/web/app.js', import.meta.url), 'utf8');
const HTML = readFileSync(new URL('../../kwil/web/index.html', import.meta.url), 'utf8');
const CSS = readFileSync(new URL('../../kwil/web/app.css', import.meta.url), 'utf8');
const FEATURES_CSS = readFileSync(new URL('../../kwil/web/static/features.css', import.meta.url), 'utf8');
const I18N = readFileSync(new URL('../../kwil/web/i18n.js', import.meta.url), 'utf8');

const TABS = ['tab-chat', 'tab-today', 'tab-history', 'tab-settings'];
const ROOTS = { app: 'tab-chat', 'today-view': 'tab-today', 'history-view': 'tab-history' };
const BACK_REACHED = ['transcript-view', 'memory-view', 'projects-view', 'project-view',
  'reminders-view', 'brief-view'];

class El {
  constructor(id = '', doc = null) {
    this.id = id; this.doc = doc; this.hidden = false; this.disabled = false; this.checked = false;
    this.value = ''; this.placeholder = ''; this.className = ''; this.type = ''; this.open = false;
    this.dataset = {}; this.children = []; this.listeners = {}; this.attrs = {}; this.parent = null;
    this.ownText = ''; this.scrollTop = 0; this.scrollHeight = 0; this.clicks = 0;
    this.classList = { add() {}, remove() {}, toggle() {}, contains: () => false };
  }
  get textContent() { return this.ownText + this.children.map((c) => c.textContent).join(' '); }
  set textContent(v) { this.ownText = String(v); for (const c of this.children) c.parent = null; this.children = []; }
  get lastElementChild() { return this.children.at(-1) ?? null; }
  appendChild(c) { c.parent = this; this.children.push(c); return c; }
  remove() {
    if (!this.parent) return;
    this.parent.children = this.parent.children.filter((c) => c !== this);
    this.parent = null;
  }
  setAttribute(k, v) { this.attrs[k] = String(v); }
  getAttribute(k) { return this.attrs[k] ?? null; }
  removeAttribute(k) { delete this.attrs[k]; }
  focus() { if (this.doc) this.doc.activeElement = this; }
  addEventListener(type, fn) { (this.listeners[type] ??= []).push(fn); }
  click() { this.clicks++; for (const fn of this.listeners.click || []) fn({ preventDefault() {} }); }
  fire(type) { for (const fn of this.listeners[type] || []) fn({ preventDefault() {} }); }
}

const json = (status, body) => ({ ok: status < 400, status, json: async () => body });

function load() {
  const byId = {};
  const document = {
    activeElement: null,
    getElementById: (id) => (byId[id] ??= new El(id, document)),
    createElement: () => new El('', document),
    querySelector: (sel) => (sel === 'details.menu' ? menu : null),
    listeners: {},
    addEventListener(type, fn) { (this.listeners[type] ??= []).push(fn); },
    visibilityState: 'visible',
  };
  const menu = new El('menu', document);  // <details class="menu">, closed
  // a <details>: setting .open fires "toggle" (asynchronously in a browser)
  let open = false;
  Object.defineProperty(menu, 'open', {
    get: () => open,
    set: (v) => { const was = open; open = Boolean(v); if (was !== open) menu.fire('toggle'); },
  });
  const $ = document.getElementById;
  for (const id of ['login', ...Object.keys(ROOTS).filter((v) => v !== 'app'), ...BACK_REACHED]) $(id).hidden = true;
  $('tabbar').hidden = true;  // as the markup has it
  $('open-today').hidden = true;  // until /api/state says so
  $('open-history').hidden = true;
  const state = { busy: false, confirm: null, history: false, today: false };
  const win = {
    document,
    Audio: class { play() { return Promise.resolve(); } pause() {} },
    localStorage: { getItem: () => null, setItem() {} },
    navigator: {},
    location: { hash: '', pathname: '/', origin: 'https://pc.example' },
    history: { replaceState() {} },
    FormData, Blob, TextDecoder, console, setTimeout, clearTimeout,
    // history.js and today.js: their header buttons follow /api/state
    onHistoryState: (s) => { $('open-history').hidden = !s.history; },
    KwilToday: { configure() {}, onState: (s) => { $('open-today').hidden = !s.today; } },
    fetch: async (url) => {
      if (url === '/healthz') return json(200, {});  // no failover
      if (url === '/api/state') return json(200, { ...state });
      return json(404, {});
    },
  };
  win.window = win;
  vm.createContext(win);
  vm.runInContext(SCRIPT, win);
  // what history.js and today.js do on their header buttons and Back
  $('open-history').addEventListener('click', () => win.show('history-view'));
  $('open-today').addEventListener('click', () => { menu.open = false; win.show('today-view'); });
  $('today-back').addEventListener('click', () => win.show('app'));
  const flush = async () => { for (let i = 0; i < 8; i++) await new Promise((r) => setImmediate(r)); };
  async function wake() {  // the app comes back to the front: refreshState()
    for (const fn of document.listeners.visibilitychange || []) await fn();
    await flush();
  }
  const current = () => TABS.filter((id) => $(id).getAttribute('aria-current') === 'page');
  const shown = () => ['app', ...Object.keys(ROOTS).filter((v) => v !== 'app'), ...BACK_REACHED, 'login']
    .filter((id) => !$(id).hidden);
  return { win, $, document, menu, state, flush, wake, current, shown };
}

test('each root screen shows the bar with its own tab current, and only that one', async () => {
  const p = load();
  await p.flush();
  for (const [view, tab] of Object.entries(ROOTS)) {
    p.win.show(view);
    assert.equal(p.$('tabbar').hidden, false, view);
    assert.deepEqual(p.current(), [tab], view);
  }
});

test('the bar hides on screens reached with Back and on the sign-in', async () => {
  const p = load();
  await p.flush();
  for (const view of [...BACK_REACHED, 'login']) {
    p.win.show('app');
    assert.equal(p.$('tabbar').hidden, false);
    p.win.show(view);
    assert.equal(p.$('tabbar').hidden, true, view);
  }
});

test('Settings is the current tab while the menu is open', async () => {
  const p = load();
  await p.flush();
  p.win.show('app');
  p.menu.open = true;  // the gear, or a Today row that opens Settings
  assert.deepEqual(p.current(), ['tab-settings']);
  p.menu.open = false;
  assert.deepEqual(p.current(), ['tab-chat']);
});

test('the Settings tab opens the menu over the chat, from any root screen', async () => {
  const p = load();
  await p.flush();
  p.win.show('history-view');
  p.$('tab-settings').click();
  assert.deepEqual(p.shown(), ['app']);
  assert.equal(p.menu.open, true);
  assert.deepEqual(p.current(), ['tab-settings']);
  p.win.show('today-view');
  p.$('tab-settings').click();
  assert.equal(p.$('today-back').clicks, 1);  // Today's own Back (it clears /#today)
  assert.deepEqual(p.shown(), ['app']);
  assert.equal(p.menu.open, true);
});

test('the Chat tab closes the menu and never focuses the message field', async () => {
  const p = load();
  await p.flush();
  p.win.show('app');
  p.menu.open = true;
  p.$('tab-chat').click();
  assert.equal(p.menu.open, false);
  assert.deepEqual(p.shown(), ['app']);
  assert.deepEqual(p.current(), ['tab-chat']);
  assert.notEqual(p.document.activeElement, p.$('text'));
  p.win.show('history-view');
  p.$('tab-chat').click();
  assert.deepEqual(p.shown(), ['app']);
  assert.notEqual(p.document.activeElement, p.$('text'));
});

test('Today and History tabs click their header buttons; the current tab does nothing', async () => {
  const p = load();
  await p.flush();
  p.win.show('app');
  p.menu.open = true;
  p.$('tab-history').click();
  assert.equal(p.$('open-history').clicks, 1);
  assert.equal(p.menu.open, false);
  assert.deepEqual(p.shown(), ['history-view']);
  p.$('tab-history').click();  // already there: no reload
  assert.equal(p.$('open-history').clicks, 1);
  p.$('tab-today').click();
  assert.equal(p.$('open-today').clicks, 1);
  assert.deepEqual(p.shown(), ['today-view']);
  assert.deepEqual(p.current(), ['tab-today']);
  p.$('tab-today').click();
  assert.equal(p.$('open-today').clicks, 1);
  p.win.show('app');
  p.$('tab-chat').click();
  assert.equal(p.menu.open, false);
  p.menu.open = true;
  p.$('tab-settings').click();  // already open: stays open
  assert.equal(p.menu.open, true);
});

test('Today and History tabs show only when their header buttons do (after /api/state)', async () => {
  const p = load();
  await p.flush();
  p.win.show('app');
  assert.equal(p.$('tab-today').hidden, true);
  assert.equal(p.$('tab-history').hidden, true);
  p.state.today = true;
  p.state.history = true;
  await p.wake();
  assert.equal(p.$('open-today').hidden, false);
  assert.equal(p.$('tab-today').hidden, false);
  assert.equal(p.$('tab-history').hidden, false);
  p.state.history = false;
  await p.wake();
  assert.equal(p.$('tab-history').hidden, true);
  assert.equal(p.$('tab-today').hidden, false);
});

test('markup: a nav after the last screen, four buttons in order, hidden until app.js, no tab roles', () => {
  const m = HTML.match(/<nav id="tabbar"([^>]*)>([\s\S]*?)<\/nav>/);
  assert.ok(m, 'no #tabbar');
  assert.match(m[1], /class="tabbar"/);
  assert.match(m[1], /aria-label="Main navigation"/);
  assert.match(m[1], /\shidden\b/);
  const ids = [...m[2].matchAll(/<button id="([^"]+)" class="tabbar-item" type="button"/g)].map((x) => x[1]);
  assert.deepEqual(ids, TABS);
  for (const label of ['Chat', 'Today', 'History', 'Settings']) {
    assert.match(m[2], new RegExp(`<span class="tabbar-label">${label}</span>`));
  }
  assert.equal((m[2].match(/<svg viewBox="0 0 24 24" aria-hidden="true">/g) || []).length, 4);
  assert.doesNotMatch(m[0], /role="tab|class="tabs/);
  // body level, after the last <main> (brief-view), before the dialogs
  assert.ok(HTML.lastIndexOf('</main>') < HTML.indexOf('<nav id="tabbar"'));
  assert.ok(HTML.indexOf('<nav id="tabbar"') < HTML.indexOf('<dialog id="sheet"'));
  // the header's own controls stay (the tabs click them; desktop uses them)
  for (const id of ['open-today', 'open-history', 'settings-done', 'settings-title', 'today-back', 'history-back']) {
    assert.match(HTML, new RegExp(`id="${id}"`));
  }
  assert.match(HTML, /<details class="menu">\s*<summary aria-label="Settings">/);
});

// the rules inside `@media (max-width: 47.99rem) { ... }` of a stylesheet
function phoneRules(css) {
  const out = [];
  let at = 0;
  while ((at = css.indexOf('@media (max-width: 47.99rem)', at)) !== -1) {
    let i = css.indexOf('{', at) + 1, depth = 1;
    const start = i;
    while (depth) { if (css[i] === '{') depth++; else if (css[i] === '}') depth--; i++; }
    out.push(css.slice(start, i - 1));
    at = i;
  }
  return out.join('\n').replace(/\s+/g, ' ');
}

test('CSS: phone only; hidden with the keyboard, in voice mode and when [hidden]; room left for it', () => {
  assert.match(CSS, /--tabbar-h: 49px/);
  // outside the phone media query it never shows
  assert.match(CSS.replace(/\s+/g, ' '), /\.tabbar \{ display: none; \} @media \(max-width: 47\.99rem\)/);
  const phone = phoneRules(CSS);
  assert.match(phone, /\.tabbar\[hidden\], html\.kb-open \.tabbar, body\.voice-open \.tabbar \{ display: none; \}/);
  assert.match(phone, /\.app > header > :is\(#open-today, #open-history\), \.app > header \.menu > summary \{ display: none; \}/);
  assert.match(phone, /#today-back, #history-back \{ visibility: hidden; \}/);
  assert.match(phone, /\.tabbar \{ position: fixed;[^}]*bottom: 0; z-index: 31;[^}]*env\(safe-area-inset-bottom\)[^}]*border-top: 1px solid var\(--line\)/);
  assert.match(phone, /\.tabbar-item\[aria-current=page\] \{ color: var\(--accent-ink\); \}/);
  assert.match(phone, /\.tabbar-item:focus-visible \{ box-shadow: var\(--ring\); \}/);
  assert.match(phone, /html:has\(#tabbar:not\(\[hidden\]\)\):not\(\.kb-open\) \.app \{ padding-bottom: calc\(var\(--tabbar-h\) \+ env\(safe-area-inset-bottom\)\); \}/);
  assert.match(phone, /html:has\(#tabbar:not\(\[hidden\]\)\):not\(\.kb-open\) \.app > footer \{ padding-bottom: var\(--s2\); \}/);
  assert.match(phone, /:not\(\.kb-open\) :is\(\.history, \.menu\[open\] \.menu-body\) \{ padding-bottom: calc\(var\(--tabbar-h\)/);
  // tokens only: no colours of its own
  const tabbarCss = CSS.slice(CSS.indexOf("the phone's tab bar (#tabbar"));
  assert.doesNotMatch(tabbarCss, /#[0-9a-f]{3,8}\b(?![-\w])/i);
  assert.match(phoneRules(FEATURES_CSS), /:not\(\.kb-open\) \.panel \{ padding-bottom: 1\.25rem; \}/);
});

test('zh: the bar and its labels are translated', () => {
  const win = {};
  vm.createContext(win);
  vm.runInContext(I18N, win);
  const tr = win.KwilI18n.translate;
  assert.equal(tr('Main navigation'), '主导航');
  for (const s of ['Chat', 'Today', 'History', 'Settings']) assert.notEqual(tr(s), s, s);
});
