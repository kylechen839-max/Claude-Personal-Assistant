// node --test tests/js/desktop.test.mjs  (Node 18+; no packages needed)
//
// The desktop layout (48rem and up). kwil/web/app.js in node:vm (the fake
// DOM of tabbar.test.mjs, plus a matchMedia stub and document.body):
// the rail shows on every screen but the sign-in, one item aria-current,
// Today and History following their header buttons, each item clicking the
// header's own control; at 64rem and up Today opens beside the chat and a
// transcript beside the History list (body.dataset.split), every other
// screen alone, and a resize lays the shown screen out again; narrower, or
// with no matchMedia, nothing changes. history.js marks the open row.
// kwil/web/ui.js's Ctrl+K / Cmd+K switcher (its own fake DOM): the
// shortcut, when it is ignored, the filter, its ARIA, the keys, Escape's
// focus return, and the conversation search (debounce, abort, stale
// answers dropped). Voice mode (voice.js with ui.js): the page behind is
// inert, the orb has focus, Escape ends it and focus comes back. Then the
// markup, the CSS (the rail only from 48rem, the splits only from 64rem,
// tokens only) and the Chinese strings.

import test from 'node:test';
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import vm from 'node:vm';

const read = (path) => readFileSync(new URL(`../../kwil/web/${path}`, import.meta.url), 'utf8');
const APP = read('app.js');
const HISTORY = read('history.js');
const UI = read('ui.js');
const VOICE = read('voice.js');
const HTML = read('index.html');
const CSS = read('app.css');
const VOICE_CSS = read('voice.css');
const I18N = read('i18n.js');

const RAIL = ['rail-chat', 'rail-today', 'rail-history', 'rail-settings'];
const ROOTS = { app: 'tab-chat', 'today-view': 'tab-today', 'history-view': 'tab-history' };
const BACK_REACHED = ['transcript-view', 'memory-view', 'projects-view', 'project-view',
  'reminders-view', 'brief-view'];

// ---------- app.js (tabbar.test.mjs's fake DOM) ----------

class El {
  constructor(id = '', doc = null) {
    this.id = id; this.doc = doc; this.hidden = false; this.disabled = false; this.checked = false;
    this.value = ''; this.placeholder = ''; this.className = ''; this.type = ''; this.open = false;
    this.dataset = {}; this.children = []; this.listeners = {}; this.attrs = {}; this.parent = null;
    this.ownText = ''; this.scrollTop = 0; this.scrollHeight = 0; this.clicks = 0; this.style = {};
    this.classList = { add() {}, remove() {}, toggle() {}, contains: () => false };
  }
  get textContent() { return this.ownText + this.children.map((c) => c.textContent).join(' '); }
  set textContent(v) { this.ownText = String(v); for (const c of this.children) c.parent = null; this.children = []; }
  get lastElementChild() { return this.children.at(-1) ?? null; }
  appendChild(c) { c.parent = this; this.children.push(c); return c; }
  append(...cs) { for (const c of cs) this.appendChild(c); }
  prepend(c) { c.parent = this; this.children.unshift(c); }
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

// A media query's answer for a window `width` px wide (1rem = 16px).
function matches(query, width) {
  const min = query.match(/min-width:\s*([\d.]+)rem/);
  if (min) return width >= parseFloat(min[1]) * 16;
  const max = query.match(/max-width:\s*([\d.]+)rem/);
  if (max) return width <= parseFloat(max[1]) * 16;
  return false;
}

function load({ width = 1280, media = true, body = true, history = false, convs = [] } = {}) {
  const byId = {};
  const document = {
    activeElement: null,
    getElementById: (id) => (byId[id] ??= new El(id, document)),
    createElement: () => new El('', document),
    createTextNode: (t) => { const n = new El('', document); n.ownText = t; return n; },
    querySelector: (sel) => (sel === 'details.menu' ? menu : null),
    listeners: {},
    addEventListener(type, fn) { (this.listeners[type] ??= []).push(fn); },
    visibilityState: 'visible',
  };
  if (body) document.body = { dataset: {}, appendChild() {} };
  const menu = new El('menu', document);  // <details class="menu">, closed
  let open = false;
  Object.defineProperty(menu, 'open', {
    get: () => open,
    set: (v) => { const was = open; open = Boolean(v); if (was !== open) menu.fire('toggle'); },
  });
  const $ = document.getElementById;
  for (const id of ['login', ...Object.keys(ROOTS).filter((v) => v !== 'app'), ...BACK_REACHED]) $(id).hidden = true;
  $('tabbar').hidden = true;
  $('rail').hidden = true;  // as the markup has them
  for (const id of ['open-today', 'open-history', 'rail-today', 'rail-history']) $(id).hidden = true;
  const state = { busy: false, confirm: null, history: false, today: false };
  const size = { width };
  const queries = [];
  const win = {
    document,
    Audio: class { play() { return Promise.resolve(); } pause() {} },
    localStorage: { getItem: () => null, setItem() {} },
    navigator: {},
    location: { hash: '', pathname: '/', origin: 'https://pc.example' },
    history: { replaceState() {} },
    FormData, Blob, TextDecoder, URLSearchParams, AbortController, console, setTimeout, clearTimeout,
    onHistoryState: (s) => { $('open-history').hidden = !s.history; },
    KwilToday: { configure() {}, onState: (s) => { $('open-today').hidden = !s.today; } },
    fetch: async (url) => {
      if (url === '/healthz') return json(200, {});  // no failover
      if (url === '/api/state') return json(200, { ...state });
      if (url.startsWith('/api/conversations?')) return json(200, { conversations: convs, next_offset: null });
      const one = url.match(/^\/api\/conversations\/([^/?]+)$/);
      if (one) {
        return json(200, { id: one[1], title: `Title ${one[1]}`, source: 'web', started_at: 1, message_count: 0,
          messages: [] });
      }
      return json(404, {});
    },
  };
  if (media) {
    win.matchMedia = (query) => {
      const mq = { query, listeners: [], get matches() { return matches(query, size.width); },
        addEventListener(type, fn) { if (type === 'change') this.listeners.push(fn); } };
      queries.push(mq);
      return mq;
    };
  }
  win.window = win;
  vm.createContext(win);
  vm.runInContext(APP, win);
  if (history) vm.runInContext(HISTORY, win);
  else $('open-history').addEventListener('click', () => win.show('history-view'));
  $('open-today').addEventListener('click', () => { menu.open = false; win.show('today-view'); });
  $('today-back').addEventListener('click', () => win.show('app'));
  const flush = async () => { for (let i = 0; i < 8; i++) await new Promise((r) => setImmediate(r)); };
  async function wake() {
    for (const fn of document.listeners.visibilitychange || []) await fn();
    await flush();
  }
  function resize(w) {
    size.width = w;
    for (const mq of queries) for (const fn of mq.listeners) fn({ matches: mq.matches });
  }
  const current = () => RAIL.filter((id) => $(id).getAttribute('aria-current') === 'page');
  const shown = () => ['app', ...Object.keys(ROOTS).filter((v) => v !== 'app'), ...BACK_REACHED, 'login']
    .filter((id) => !$(id).hidden);
  return { win, $, document, menu, state, flush, wake, resize, current, shown, queries };
}

test('rail: shows on every screen but the sign-in, with one item current', async () => {
  const p = load();
  await p.flush();
  const expect = { app: ['rail-chat'], 'today-view': ['rail-today'], 'history-view': ['rail-history'],
    'transcript-view': ['rail-history'], 'memory-view': [], 'projects-view': [], 'project-view': [],
    'reminders-view': [], 'brief-view': [] };
  for (const [view, items] of Object.entries(expect)) {
    p.win.show(view);
    assert.equal(p.$('rail').hidden, false, view);
    assert.deepEqual(p.current(), items, view);
  }
  p.win.show('login');
  assert.equal(p.$('rail').hidden, true);
  p.win.show('app');
  p.menu.open = true;  // Settings is current while its page is open
  assert.deepEqual(p.current(), ['rail-settings']);
  p.menu.open = false;
  assert.deepEqual(p.current(), ['rail-chat']);
});

test('rail: Today and History show only when their header buttons do (after /api/state)', async () => {
  const p = load();
  await p.flush();
  assert.equal(p.$('rail-today').hidden, true);
  assert.equal(p.$('rail-history').hidden, true);
  p.state.today = true;
  p.state.history = true;
  await p.wake();
  assert.equal(p.$('rail-today').hidden, false);
  assert.equal(p.$('rail-history').hidden, false);
  p.state.today = false;
  await p.wake();
  assert.equal(p.$('rail-today').hidden, true);
  assert.equal(p.$('rail-history').hidden, false);
});

test('rail: each item clicks the header control; the current one does nothing', async () => {
  const p = load({ width: 900 });  // one screen at a time
  await p.flush();
  p.win.show('app');
  p.$('rail-history').click();
  assert.equal(p.$('open-history').clicks, 1);
  assert.deepEqual(p.shown(), ['history-view']);
  p.$('rail-history').click();  // already there
  assert.equal(p.$('open-history').clicks, 1);
  p.$('rail-today').click();
  assert.equal(p.$('open-today').clicks, 1);
  assert.deepEqual(p.shown(), ['today-view']);
  p.$('rail-settings').click();  // through Today's own Back, then the menu
  assert.equal(p.$('today-back').clicks, 1);
  assert.deepEqual(p.shown(), ['app']);
  assert.equal(p.menu.open, true);
  assert.deepEqual(p.current(), ['rail-settings']);
  p.$('rail-chat').click();
  assert.equal(p.menu.open, false);
  assert.deepEqual(p.current(), ['rail-chat']);
  assert.notEqual(p.document.activeElement, p.$('text'));  // no keyboard
  p.win.show('transcript-view');  // History is current, and goes back to the list
  p.$('rail-history').click();
  assert.equal(p.$('open-history').clicks, 2);
  assert.deepEqual(p.shown(), ['history-view']);
});

test('split: at 64rem and up Today opens beside the chat', async () => {
  const p = load({ width: 1280 });
  await p.flush();
  p.win.show('today-view');
  assert.equal(p.document.body.dataset.split, 'today-view');
  assert.deepEqual(p.shown(), ['app', 'today-view']);
  assert.deepEqual(p.current(), ['rail-today']);
  p.win.show('app');
  assert.equal(p.document.body.dataset.split, undefined);
  assert.deepEqual(p.shown(), ['app']);
  // the rail's Chat from the split: Today's own Back (it clears /#today)
  p.win.show('today-view');
  p.$('rail-chat').click();
  assert.equal(p.$('today-back').clicks, 1);
  assert.deepEqual(p.shown(), ['app']);
});

test('split: at 64rem and up a transcript opens beside the History list; other screens alone', async () => {
  const p = load({ width: 1440 });
  await p.flush();
  p.win.show('transcript-view');
  assert.equal(p.document.body.dataset.split, 'transcript-view');
  assert.deepEqual(p.shown(), ['history-view', 'transcript-view']);
  for (const view of ['app', 'history-view', 'memory-view', 'projects-view', 'project-view',
    'reminders-view', 'brief-view']) {
    p.win.show(view);
    assert.equal(p.document.body.dataset.split, undefined, view);
    assert.deepEqual(p.shown(), [view], view);
  }
  p.win.show('login');
  assert.deepEqual(p.shown(), ['login']);
});

test('narrow: below 64rem one screen at a time; below 48rem the tab bar as before', async () => {
  for (const width of [1000, 800, 390]) {
    const p = load({ width });
    await p.flush();
    for (const view of ['today-view', 'transcript-view']) {
      p.win.show(view);
      assert.equal(p.document.body.dataset.split, undefined, `${width} ${view}`);
      assert.deepEqual(p.shown(), [view], `${width} ${view}`);
    }
    for (const [view, tab] of Object.entries(ROOTS)) {
      p.win.show(view);
      assert.equal(p.$('tabbar').hidden, false, view);
      assert.equal(p.$(tab).getAttribute('aria-current'), 'page', view);
    }
    p.win.show('memory-view');
    assert.equal(p.$('tabbar').hidden, true);
  }
});

test('a resize across 64rem lays the shown screen out again', async () => {
  const p = load({ width: 1280 });
  await p.flush();
  assert.ok(p.queries.some((q) => q.query === '(min-width: 64rem)' && q.listeners.length));
  p.win.show('today-view');
  assert.deepEqual(p.shown(), ['app', 'today-view']);
  p.resize(900);
  assert.equal(p.document.body.dataset.split, undefined);
  assert.deepEqual(p.shown(), ['today-view']);
  p.resize(1300);
  assert.equal(p.document.body.dataset.split, 'today-view');
  assert.deepEqual(p.shown(), ['app', 'today-view']);
  p.win.show('transcript-view');
  p.resize(700);
  assert.deepEqual(p.shown(), ['transcript-view']);
});

test('no matchMedia and no document.body (old fakes): one screen at a time, nothing throws', async () => {
  const p = load({ media: false, body: false });
  await p.flush();
  p.win.show('today-view');
  assert.deepEqual(p.shown(), ['today-view']);
  p.win.show('transcript-view');
  assert.deepEqual(p.shown(), ['transcript-view']);
});

test('history.js: the open conversation\'s row is aria-current beside its transcript', async () => {
  const now = Math.floor(Date.now() / 1000);
  const convs = [{ id: 'c1', title: 'One', source: 'web', updated_at: now },
    { id: 'c2', title: 'Two', source: 'web', updated_at: now }];
  const p = load({ width: 1280, history: true, convs });
  await p.flush();
  assert.equal(typeof p.win.KwilHistory.openTranscript, 'function');
  p.$('open-history').click();
  await p.flush();
  const rows = () => {
    const out = {};
    const walk = (n) => { if (n.className === 'conv-main') out[n.parent.dataset.id] = n; n.children.forEach(walk); };
    walk(p.$('history-list'));
    return out;
  };
  assert.deepEqual(Object.keys(rows()), ['c1', 'c2']);
  await p.win.KwilHistory.openTranscript('c2');
  await p.flush();
  assert.deepEqual(p.shown(), ['history-view', 'transcript-view']);
  assert.equal(rows().c2.getAttribute('aria-current'), 'true');
  assert.equal(rows().c1.getAttribute('aria-current'), null);
  p.$('transcript-back').click();  // Escape and Back: the list alone, nothing current
  await p.flush();
  assert.deepEqual(p.shown(), ['history-view']);
  assert.deepEqual(Object.values(rows()).map((r) => r.getAttribute('aria-current')), [null, null]);
});

// ---------- ui.js: the switcher and the voice overlay ----------

class Node2 {
  constructor(doc, tag, id = '') {
    this.doc = doc; this.tagName = tag.toUpperCase(); this.id = id; this.hidden = false; this.open = false;
    this.children = []; this.attrs = {}; this.listeners = {}; this.ownText = ''; this.value = ''; this.clicks = 0;
    this.className = ''; this.dataset = {}; this.inert = false; this.parent = null;
    this.classList = { add() {}, remove() {}, toggle() {}, contains: () => false };
  }
  get textContent() { return this.ownText + this.children.map((c) => c.textContent).join(''); }
  set textContent(v) { this.ownText = String(v); this.children = []; }
  appendChild(c) { c.parent = this; this.children.push(c); return c; }
  setAttribute(k, v) { this.attrs[k] = String(v); }
  getAttribute(k) { return this.attrs[k] ?? null; }
  removeAttribute(k) { delete this.attrs[k]; }
  addEventListener(type, fn) { (this.listeners[type] ??= []).push(fn); }
  dispatch(type, ev = {}) {
    const e = { target: this, defaultPrevented: false, stopped: false,
      preventDefault() { this.defaultPrevented = true; }, stopPropagation() { this.stopped = true; }, ...ev };
    for (const fn of this.listeners[type] || []) fn(e);
    return e;
  }
  focus() { this.doc.activeElement = this; }
  click() { this.clicks++; this.dispatch('click'); }
  // laid out unless a test says it is in a hidden screen
  getClientRects() { return { length: this.undrawn ? 0 : 1 }; }
  // like a browser's modal dialog: closing it gives focus back to its opener
  showModal() { this.open = true; this.opener = this.doc.activeElement; }
  close() {
    this.open = false;
    if (this.opener) { this.doc.activeElement = this.opener; this.opener = null; }
    this.dispatch('close');
  }
  pause() {}
}

const SWITCHER_IDS = {
  dialog: ['switcher', 'sheet'],
  input: ['switcher-input'], textarea: ['text'], ul: ['switcher-list'],
  p: ['switcher-status'], kbd: ['rail-search-key'], h1: ['history-title', 'memory-title'], main: ['app', 'login', 'history-view'],
  nav: ['rail', 'tabbar'], section: ['voice'],
  button: ['rail-search', 'rail-chat', 'rail-today', 'rail-history', 'rail-settings', 'memory-open',
    'projects-open', 'reminders-open', 'voice-open', 'voice-orb', 'voice-end', 'voice-mute', 'voice-cc',
    'voice-resume', 'voice-timings', 'voice-camera', 'voice-frame'],
};

function ui({ platform = 'Win32', idle = true, zh = false, voice = false } = {}) {
  const byId = {};
  const listeners = { keydown: [] };
  const doc = {
    activeElement: null,
    body: null,
    documentElement: { getAttribute: () => (zh ? 'zh-CN' : 'en'), hasAttribute: () => false },
    getElementById: (id) => byId[id] ?? null,
    createElement: (tag) => new Node2(doc, tag),
    querySelector: (sel) => {
      if (sel === 'details.menu') return menu;
      if (sel === 'dialog[open]') return Object.values(byId).find((n) => n.tagName === 'DIALOG' && n.open) ?? null;
      return null;
    },
    querySelectorAll: (sel) => {
      if (sel === 'dialog[open]') return Object.values(byId).filter((n) => n.tagName === 'DIALOG' && n.open);
      if (sel === 'body > main, #rail, #tabbar') return ['app', 'login', 'history-view', 'rail', 'tabbar'].map((id) => byId[id]);
      return [];
    },
    addEventListener(type, fn, capture) { (listeners[type] ??= [])[capture ? 'unshift' : 'push'](fn); },
  };
  for (const [tag, ids] of Object.entries(SWITCHER_IDS)) for (const id of ids) byId[id] = new Node2(doc, tag, id);
  for (const id of ['voice-state', 'voice-user', 'voice-reply', 'voice-captions', 'voice-debug', 'voice-meter',
    'voice-camera-preview', 'voice-camera-on', 'status']) byId[id] = new Node2(doc, 'p', id);
  doc.body = new Node2(doc, 'body');
  const menu = new Node2(doc, 'details', 'menu');
  for (const id of ['login', 'voice', 'rail-today', 'reminders-open']) byId[id].hidden = true;
  const timers = [];
  const requests = [];
  const aborted = [];
  const app = {
    idle: () => idle, newChats: [],
    newChat(project) { this.newChats.push(project); },
    request: (url, opts) => new Promise((resolve) => requests.push({ url, opts, resolve })),
  };
  const opened = [];
  const win = {
    document: doc, navigator: { platform }, console, URLSearchParams,
    setTimeout: (fn, ms) => { timers.push({ fn, ms }); return timers.length; },
    clearTimeout: (n) => { if (timers[n - 1]) timers[n - 1].fn = null; },
    AbortController: class { constructor() { this.signal = { aborted: false }; } abort() { this.signal.aborted = true; aborted.push(this.signal); } },
    KwilApp: app,
    KwilHistory: { openTranscript: (id) => opened.push(id) },
  };
  if (zh) {
    const ctx = {};
    vm.createContext(ctx);
    vm.runInContext(I18N, ctx);
    win.KwilI18n = { isActive: () => true, translate: ctx.KwilI18n.translate };
  }
  win.window = win;
  vm.createContext(win);
  vm.runInContext(UI, win);
  const $ = (id) => byId[id];
  const key = (k, mods = {}, target = doc.activeElement) => {
    const e = { key: k, target, defaultPrevented: false, stopped: false, ctrlKey: false, metaKey: false,
      altKey: false, shiftKey: false, ...mods,
      preventDefault() { this.defaultPrevented = true; }, stopPropagation() { this.stopped = true; },
      stopImmediatePropagation() { this.stopped = true; } };
    // the field's own handler first (target), then the document's
    if (target && target.listeners && target.listeners.keydown) for (const fn of target.listeners.keydown) fn(e);
    if (!e.stopped) for (const fn of listeners.keydown) { fn(e); if (e.stopped) break; }
    return e;
  };
  // capture first: the field gets the shortcut only after the document
  const shortcut = (k, mods) => {
    const e = { key: k, ctrlKey: false, metaKey: false, altKey: false, shiftKey: false, ...mods,
      defaultPrevented: false, preventDefault() { this.defaultPrevented = true; } };
    for (const fn of listeners.keydown) fn(e);
    return e;
  };
  const type = (text) => { $('switcher-input').value = text; $('switcher-input').dispatch('input'); };
  const tick = () => { const due = timers.splice(0); for (const t of due) if (t.fn) t.fn(); return due.filter((t) => t.fn); };
  const options = () => $('switcher-list').children.map((li) => li.children[0].textContent);
  const flush = async () => { for (let i = 0; i < 6; i++) await new Promise((r) => setImmediate(r)); };
  return { win, doc, $, menu, key, shortcut, type, tick, timers, options, requests, aborted, app, opened, flush };
}

test('switcher: Ctrl+K opens it (even while typing a message), again closes it and focus returns', () => {
  const t = ui();
  t.$('text').focus();
  const e = t.shortcut('k', { ctrlKey: true });
  assert.equal(e.defaultPrevented, true);
  assert.equal(t.$('switcher').open, true);
  assert.equal(t.doc.activeElement, t.$('switcher-input'));
  const again = t.shortcut('k', { ctrlKey: true });
  assert.equal(again.defaultPrevented, true);
  assert.equal(t.$('switcher').open, false);
  assert.equal(t.doc.activeElement, t.$('text'));
  assert.equal(t.$('rail-search-key').textContent, 'Ctrl K');
  assert.equal(t.$('rail-search').getAttribute('aria-keyshortcuts'), 'Control+K');
  t.$('rail-search').click();  // the rail's Search opens it too
  assert.equal(t.$('switcher').open, true);
});

test('switcher: on a Mac it is Cmd+K, and Ctrl+K is left alone', () => {
  const t = ui({ platform: 'MacIntel' });
  const ctrl = t.shortcut('k', { ctrlKey: true });
  assert.equal(ctrl.defaultPrevented, false);
  assert.equal(t.$('switcher').open, false);
  t.shortcut('k', { metaKey: true });
  assert.equal(t.$('switcher').open, true);
  assert.equal(t.$('rail-search-key').textContent, '⌘K');
  assert.equal(t.$('rail-search').getAttribute('aria-keyshortcuts'), 'Meta+K');
  // and on Windows, Cmd (the Windows key) is not it
  const w = ui();
  w.shortcut('k', { metaKey: true });
  assert.equal(w.$('switcher').open, false);
});

test('switcher: ignored with Alt or Shift, over another dialog, on the sign-in and in voice mode', () => {
  for (const mods of [{ altKey: true }, { shiftKey: true }]) {
    const t = ui();
    const e = t.shortcut('k', { ctrlKey: true, ...mods });
    assert.equal(e.defaultPrevented, false);
    assert.equal(t.$('switcher').open, false);
  }
  for (const setup of [(t) => { t.$('sheet').open = true; }, (t) => { t.$('login').hidden = false; },
    (t) => { t.$('voice').hidden = false; }]) {
    const t = ui();
    setup(t);
    const e = t.shortcut('k', { ctrlKey: true });
    assert.equal(e.defaultPrevented, false);
    assert.equal(t.$('switcher').open, false);
    t.$('rail-search').click();
    assert.equal(t.$('switcher').open, false);
  }
});

test('switcher: the combobox and listbox ARIA, and the first option active', () => {
  const t = ui();
  t.shortcut('k', { ctrlKey: true });
  const input = t.$('switcher-input');
  const list = t.$('switcher-list');
  assert.equal(input.getAttribute('aria-expanded'), 'true');
  assert.equal(input.getAttribute('aria-activedescendant'), 'switcher-opt-0');
  list.children.forEach((li, n) => {
    assert.equal(li.id, `switcher-opt-${n}`);
    assert.equal(li.getAttribute('role'), 'option');
    assert.equal(li.getAttribute('aria-selected'), String(n === 0));
  });
  const m = HTML.match(/<dialog id="switcher"([^>]*)>([\s\S]*?)<\/dialog>/);
  assert.ok(m, 'no #switcher');
  assert.match(m[1], /class="sheet switcher"/);
  assert.match(m[1], /aria-label="Go to"/);
  const field = m[2].match(/<input id="switcher-input"[^>]*>/)[0];
  for (const attr of ['role="combobox"', 'aria-controls="switcher-list"', 'aria-autocomplete="list"',
    'aria-expanded="false"', 'aria-label="Go to a screen or conversation"']) assert.ok(field.includes(attr), attr);
  assert.match(m[2], /<ul id="switcher-list" class="switcher-list" role="listbox" aria-label="Results"><\/ul>/);
  assert.match(m[2], /<p id="switcher-status" class="switcher-status" role="status"><\/p>/);
});

test('switcher: commands only for controls that show, filtered by a case-insensitive substring', async () => {
  const t = ui();
  t.shortcut('k', { ctrlKey: true });
  // rail-today and reminders-open are [hidden]
  assert.deepEqual(t.options(), ['Chat', 'New chat', 'History', 'Settings', 'Memory', 'Projects',
    'Start voice conversation']);
  t.type('HIST');
  assert.deepEqual(t.options(), ['History']);
  t.type('  ch ');
  assert.deepEqual(t.options(), ['Chat', 'New chat']);
  t.type('zzz');
  assert.deepEqual(t.options(), []);
  assert.equal(t.$('switcher-status').textContent, '');  // still asking the server
  t.tick();
  t.requests.at(-1).resolve(json(200, { conversations: [] }));
  await t.flush();
  assert.equal(t.$('switcher-status').textContent, 'No matches');
  assert.equal(t.$('switcher-input').getAttribute('aria-expanded'), 'false');
  // New chat only while no reply is running
  const busy = ui({ idle: false });
  busy.shortcut('k', { ctrlKey: true });
  assert.ok(!busy.options().includes('New chat'));
});

test('switcher: in Chinese it lists and filters the translated labels', async () => {
  const t = ui({ zh: true });
  t.shortcut('k', { ctrlKey: true });
  assert.deepEqual(t.options(), ['聊天', '新对话', '历史记录', '设置', '记忆', '项目', '开始语音对话']);
  t.type('对话');
  assert.deepEqual(t.options(), ['新对话', '开始语音对话']);
  t.type('History');  // the English word is not on the page
  t.tick();
  t.requests.at(-1).resolve(json(200, { conversations: [] }));
  await t.flush();
  assert.deepEqual(t.options(), []);
  assert.equal(t.$('switcher-status').textContent, '没有匹配项');
});

test('switcher: Up and Down wrap, Home and End jump, Enter and a click pick (the control is clicked)', () => {
  const t = ui();
  t.$('text').focus();
  t.shortcut('k', { ctrlKey: true });
  const input = t.$('switcher-input');
  const n = t.options().length;
  const active = () => input.getAttribute('aria-activedescendant');
  assert.equal(t.key('ArrowUp').defaultPrevented, true);
  assert.equal(active(), `switcher-opt-${n - 1}`);
  t.key('ArrowDown');
  assert.equal(active(), 'switcher-opt-0');
  t.key('End');
  assert.equal(active(), `switcher-opt-${n - 1}`);
  t.key('Home');
  t.key('ArrowDown');
  t.key('ArrowDown');
  assert.equal(active(), 'switcher-opt-2');  // History
  assert.equal(t.$('switcher-list').children[2].getAttribute('aria-selected'), 'true');
  t.menu.open = true;  // a command other than Settings closes the Settings page
  t.key('Enter');
  assert.equal(t.$('switcher').open, false);
  assert.equal(t.$('rail-history').clicks, 1);
  assert.equal(t.menu.open, false);
  // a click
  t.shortcut('k', { ctrlKey: true });
  t.$('switcher-list').children[4].click();  // Memory
  assert.equal(t.$('memory-open').clicks, 1);
  assert.equal(t.$('switcher').open, false);
  // New chat starts one in no project; Settings leaves the menu to its item
  t.shortcut('k', { ctrlKey: true });
  t.type('new');
  t.key('Enter');
  assert.deepEqual(t.app.newChats, [null]);
  t.menu.open = true;
  t.shortcut('k', { ctrlKey: true });
  t.type('sett');
  t.key('Enter');
  assert.equal(t.$('rail-settings').clicks, 1);
  assert.equal(t.menu.open, true);
});

test('switcher: Escape closes only it and gives focus back to what had it', () => {
  const t = ui();
  t.$('rail-chat').focus();
  t.shortcut('k', { ctrlKey: true });
  const e = t.key('Escape');
  assert.equal(e.defaultPrevented, true);
  assert.equal(e.stopped, true);  // not the Settings page's or a screen's Escape too
  assert.equal(t.$('switcher').open, false);
  assert.equal(t.doc.activeElement, t.$('rail-chat'));
  // the dialog's own cancel (Escape outside the field) does the same
  t.$('text').focus();
  t.shortcut('k', { ctrlKey: true });
  const cancel = t.$('switcher').dispatch('cancel');
  assert.equal(cancel.defaultPrevented, true);
  assert.equal(t.$('switcher').open, false);
  assert.equal(t.doc.activeElement, t.$('text'));
});

test('switcher: conversations after a 150 ms pause, the previous request aborted, stale answers dropped', async () => {
  const t = ui();
  t.shortcut('k', { ctrlKey: true });
  t.type('p');
  t.type('ph');
  assert.equal(t.requests.length, 0);
  assert.deepEqual(t.timers.filter((x) => x.fn).map((x) => x.ms), [150]);  // one debounce
  t.tick();
  assert.equal(t.requests.length, 1);
  assert.equal(t.requests[0].url, '/api/conversations?q=ph&limit=6');
  assert.equal(t.requests[0].opts.signal.aborted, false);
  t.type('phy');
  t.tick();
  assert.equal(t.requests.length, 2);
  assert.equal(t.requests[0].opts.signal.aborted, true);  // the previous one
  const conv = (id, title) => ({ id, title, source: 'web', updated_at: 1 });
  t.requests[1].resolve(json(200, { conversations: [1, 2, 3, 4, 5, 6, 7, 8].map((i) => conv(`n${i}`, `Physics ${i}`)) }));
  await t.flush();
  t.requests[0].resolve(json(200, { conversations: [conv('old', 'Phone call')] }));  // late
  await t.flush();
  assert.deepEqual(t.options(), ['Physics 1', 'Physics 2', 'Physics 3', 'Physics 4', 'Physics 5', 'Physics 6']);
  const li = t.$('switcher-list').children[0];
  assert.equal(li.children[1].textContent, 'Conversation');
  t.key('Enter');
  assert.deepEqual(t.opened, ['n1']);
  assert.equal(t.$('switcher').open, false);
  // closing drops a search still on its way
  t.shortcut('k', { ctrlKey: true });
  t.type('ab');
  t.tick();
  t.key('Escape');
  assert.equal(t.requests.at(-1).opts.signal.aborted, true);
  t.requests.at(-1).resolve(json(200, { conversations: [conv('z', 'Abacus')] }));
  await t.flush();
  t.shortcut('k', { ctrlKey: true });
  assert.ok(!t.options().includes('Abacus'));
  // clearing the field drops the conversations
  t.type('ph');
  t.tick();
  t.requests.at(-1).resolve(json(200, { conversations: [conv('q', 'Physics')] }));
  await t.flush();
  assert.ok(t.options().includes('Physics'));
  t.type('');
  assert.ok(!t.options().includes('Physics'));
});

// voice.js with ui.js: the mic is never granted here (it waits), so the
// session stays "connecting" until End or Escape.
function voiceLoad() {
  const t = ui();
  let micAsked = 0;
  t.win.VoiceCore = { CameraPolicy: class { setOn() {} } };
  t.win.localStorage = { getItem: () => null, setItem() {} };
  t.win.fetch = () => Promise.reject(new Error('offline'));
  t.win.AudioContext = class {
    resume() {} createBufferSource() { return { connect() {}, start() {} }; }
    createBuffer() { return {}; } get destination() { return {}; }
  };
  t.win.navigator.mediaDevices = { getUserMedia: () => { micAsked++; return new Promise(() => {}); } };
  vm.runInContext(VOICE, t.win);
  return { ...t, micAsked: () => micAsked };
}

test('voice: opening it focuses the orb and makes the page behind inert; Escape ends it, focus comes back', async () => {
  const t = voiceLoad();
  const behind = ['app', 'login', 'history-view', 'rail', 'tabbar'];
  t.$('voice-open').focus();
  t.$('voice-open').click();
  await t.flush();
  assert.equal(t.micAsked(), 1);
  assert.equal(t.$('voice').hidden, false);
  assert.equal(t.doc.activeElement, t.$('voice-orb'));
  assert.deepEqual(behind.map((id) => t.$(id).inert), behind.map(() => true));
  // the switcher stays shut in voice mode
  t.shortcut('k', { ctrlKey: true });
  assert.equal(t.$('switcher').open, false);
  const e = t.key('Escape');
  assert.equal(e.defaultPrevented, true);
  assert.equal(t.$('voice-end').clicks, 1);
  assert.equal(t.$('voice').hidden, true);
  assert.deepEqual(behind.map((id) => t.$(id).inert), behind.map(() => false));
  assert.equal(t.doc.activeElement, t.$('voice-open'));
  // Escape with voice closed does nothing
  t.key('Escape');
  assert.equal(t.$('voice-end').clicks, 1);
});

test('voice: End gives focus to the message field when the Voice button is hidden; Escape over a dialog is the dialog\'s', async () => {
  const t = voiceLoad();
  t.$('voice-open').click();
  await t.flush();
  t.$('sheet').open = true;  // e.g. a sheet still up
  t.key('Escape');
  assert.equal(t.$('voice-end').clicks, 0);
  t.$('sheet').open = false;
  t.$('voice-open').hidden = true;  // text typed: Send shows instead
  t.$('voice-end').click();
  assert.equal(t.$('voice').hidden, true);
  assert.equal(t.doc.activeElement, t.$('text'));
});

// ---------- markup, CSS, Chinese ----------

test('markup: the rail is the body\'s first element, its items in order, hidden until app.js', () => {
  const body = HTML.slice(HTML.indexOf('<body>') + 6).replace(/<!--[\s\S]*?-->/g, '').trimStart();
  assert.ok(body.startsWith('<nav id="rail" class="rail" aria-label="Main navigation" hidden>'), body.slice(0, 80));
  const nav = body.slice(0, body.indexOf('</nav>'));
  const ids = [...nav.matchAll(/<button id="([^"]+)" class="rail-item[^"]*" type="button"/g)].map((x) => x[1]);
  assert.deepEqual(ids, ['rail-search', ...RAIL]);
  for (const label of ['Search', 'Chat', 'Today', 'History', 'Settings']) {
    assert.match(nav, new RegExp(`<span class="rail-label">${label}</span>`));
  }
  assert.match(nav, /<button id="rail-today" [^>]*hidden>/);
  assert.match(nav, /<button id="rail-history" [^>]*hidden>/);
  assert.match(nav, /<button id="rail-search" [^>]*aria-haspopup="dialog" aria-controls="switcher">/);
  assert.match(nav, /<kbd id="rail-search-key" class="rail-key"><\/kbd>/);  // ui.js: Ctrl K or ⌘K
  assert.equal((nav.match(/<svg viewBox="0 0 24 24" aria-hidden="true">/g) || []).length, 5);
  // every id the scripts and tests use stays
  for (const id of ['tabbar', 'tab-chat', 'tab-today', 'tab-history', 'tab-settings', 'open-today', 'open-history',
    'settings-title', 'settings-done', 'history-view', 'transcript-view', 'today-view', 'memory-view',
    'history-back', 'transcript-back', 'today-back', 'text', 'voice-open', 'voice', 'voice-orb', 'voice-end',
    'memory-open', 'projects-open', 'reminders-open']) {
    assert.match(HTML, new RegExp(`id="${id}"`), id);
  }
  assert.match(HTML, /<details class="menu">\s*<summary aria-label="Settings">/);
});

// the rules inside every `@media <query> { ... }` of a stylesheet, and the
// stylesheet without them
function media(css, query) {
  css = css.replace(/\/\*[\s\S]*?\*\//g, '');
  const inside = [];
  let outside = '';
  let at = 0, from = 0;
  while ((at = css.indexOf(`@media ${query}`, from)) !== -1) {
    outside += css.slice(from, at);
    let i = css.indexOf('{', at) + 1, depth = 1;
    const start = i;
    while (depth) { if (css[i] === '{') depth++; else if (css[i] === '}') depth--; i++; }
    inside.push(css.slice(start, i - 1));
    from = i;
  }
  outside += css.slice(from);
  return { inside: inside.join('\n').replace(/\s+/g, ' '), outside: outside.replace(/\s+/g, ' ') };
}

test('CSS: the rail and the grid only from 48rem, and never in voice mode', () => {
  const desk = media(CSS, '(min-width: 48rem)');
  const flat = CSS.replace(/\s+/g, ' ');
  assert.match(flat, /\.rail \{ display: none; \} @media \(min-width: 48rem\)/);
  assert.match(CSS, /--rail-w: 4\.5rem; --chat-w: 46rem;/);
  assert.match(desk.inside, /body:has\(#rail:not\(\[hidden\]\)\) \{ display: grid; grid-template-columns: var\(--rail-w\) minmax\(0, 1fr\);/);
  assert.match(desk.inside, /body:has\(#rail:not\(\[hidden\]\)\) > main \{ grid-row: 1; grid-column: 2;/);
  assert.match(desk.inside, /\.rail \{ grid-row: 1; grid-column: 1; position: relative; z-index: 31; display: flex;/);
  assert.match(desk.inside, /\.rail\[hidden\], body\.voice-open \.rail \{ display: none; \}/);
  assert.match(desk.inside, /\.rail-item \{[^}]*min-height: 56px;[^}]*font-size: var\(--t-cap\);/);
  assert.match(desk.inside, /\.rail-item svg \{ width: 22px; height: 22px;/);
  assert.match(desk.inside, /\.rail-item\[aria-current=page\] \{ color: var\(--accent-ink\);/);
  assert.match(desk.inside, /\.rail-item:focus-visible \{ box-shadow: var\(--ring\); \}/);
  assert.match(desk.inside, /\.rail-settings \{ margin-top: auto; \}/);
  assert.match(desk.inside, /body:has\(#rail:not\(\[hidden\]\)\) \.app > header > :is\(#open-today, #open-history\), body:has\(#rail:not\(\[hidden\]\)\) \.app > header \.menu > summary \{ display: none; \}/);
  assert.match(desk.inside, /body:has\(#rail:not\(\[hidden\]\)\) \.menu\[open\] \.menu-body \{ inset-inline-start: var\(--rail-w\); \}/);
  assert.match(desk.inside, /--gutter: max\(var\(--pad\), calc\(\(100% - var\(--chat-w\)\) \/ 2 \+ var\(--pad\)\)\)/);
  // narrower: no rail, no grid, no rail-hidden header (the 64rem rules are wider still)
  const narrow = media(media(CSS, '(min-width: 48rem)').outside, '(min-width: 64rem)').outside;
  assert.doesNotMatch(narrow, /\.rail(-item)? \{(?! display: none)/);
  assert.doesNotMatch(narrow, /#rail:not/);
});

test('CSS: the two panes only from 64rem; the transcript\'s Back hidden there; voice captions widen', () => {
  const wide = media(CSS, '(min-width: 64rem)');
  assert.doesNotMatch(wide.outside, /data-split/);
  assert.match(wide.inside, /\[data-split=today-view\] \{ grid-template-columns: var\(--rail-w\) minmax\(0, 1fr\) minmax\(20rem, 26rem\); \}/);
  assert.match(wide.inside, /\[data-split=today-view\] > #today-view \{ grid-column: 3;/);
  assert.match(wide.inside, /\[data-split=transcript-view\] \{ grid-template-columns: var\(--rail-w\) minmax\(18rem, 24rem\) minmax\(0, 1fr\); \}/);
  assert.match(wide.inside, /\[data-split=transcript-view\] > #history-view \{ grid-column: 2;/);
  assert.match(wide.inside, /\[data-split=transcript-view\] > #transcript-view \{ grid-column: 3; \}/);
  assert.match(wide.inside, /body\[data-split=transcript-view\] #transcript-back \{ visibility: hidden; \}/);
  assert.match(media(VOICE_CSS, '(min-width: 64rem)').inside, /\.voice-captions, \.voice-debug \{ max-width: 40rem; \}/);
  // the phone's rules are untouched by any of it
  const phone = media(CSS, '(max-width: 47.99rem)').inside;
  assert.doesNotMatch(phone, /rail|data-split|switcher/);
});

test('CSS: the desktop layout and the switcher use tokens only (no colours of their own)', () => {
  const desktop = CSS.slice(CSS.indexOf('/* ---------- the desktop layout'));
  assert.ok(desktop.length > 1000);
  assert.doesNotMatch(desktop, /#[0-9a-f]{3,8}\b(?![-\w])/i);
  assert.doesNotMatch(desktop, /rgba?\(|hsla?\(/);
  assert.match(desktop, /dialog\.switcher \{ width: min\(32rem, calc\(100% - 1rem\)\);/);
  assert.match(desktop, /\.switcher-opt\[aria-selected=true\] \{ background: var\(--accent-soft\);/);
  assert.match(desktop, /\.switcher-opt \{[^}]*min-height: 44px;/);
});

test('zh: the rail, the switcher and its commands are translated', () => {
  const win = {};
  vm.createContext(win);
  vm.runInContext(I18N, win);
  const tr = win.KwilI18n.translate;
  const want = { Search: '搜索', 'Go to': '前往', 'Go to a screen or conversation': '前往页面或对话',
    'No matches': '没有匹配项', 'New chat': '新对话', 'Start voice conversation': '开始语音对话',
    Conversation: '对话' };
  for (const [en, zh] of Object.entries(want)) assert.equal(tr(en), zh, en);
  assert.notEqual(tr('Results'), 'Results');  // History's heading, the listbox's label
  const ctx = { document: undefined };
  vm.createContext(ctx);
  vm.runInContext(UI, ctx);
  for (const c of ctx.KwilUI.COMMANDS) assert.notEqual(tr(c.label), c.label, c.label);
});

// ---------- judge round 1 ----------

test('switcher: keys picking a Chinese (pinyin) candidate are the IME\'s, not the switcher\'s', () => {
  const t = ui();
  t.$('text').focus();
  t.shortcut('k', { ctrlKey: true });
  for (const composing of [{ isComposing: true }, { keyCode: 229 }]) {
    const enter = t.key('Enter', composing);
    assert.equal(enter.defaultPrevented, false);
    assert.equal(t.$('switcher').open, true);
    assert.equal(t.$('rail-chat').clicks, 0);
    const esc = t.key('Escape', composing);
    assert.equal(esc.stopped, false);
    assert.equal(t.$('switcher').open, true);
    t.key('ArrowDown', composing);
    assert.equal(t.$('switcher-input').getAttribute('aria-activedescendant'), 'switcher-opt-0');
  }
  t.key('Enter');  // the candidate chosen: Enter picks again
  assert.equal(t.$('rail-chat').clicks, 1);
});

test('switcher: a pick that hides the focused element moves focus to its own place', () => {
  // History's title had focus; Chat hides the History screen
  const t = ui();
  t.$('rail-chat').addEventListener('click', () => { t.$('history-title').undrawn = true; });
  t.$('history-title').focus();
  t.shortcut('k', { ctrlKey: true });
  t.key('Enter');  // Chat
  assert.equal(t.$('rail-chat').clicks, 1);
  assert.equal(t.doc.activeElement, t.$('text'));
  // History from the hidden chat: the rail's History
  t.$('text').undrawn = true;
  t.shortcut('k', { ctrlKey: true });
  t.type('hist');
  t.key('Enter');
  assert.equal(t.doc.activeElement, t.$('rail-history'));
  // Memory: its screen's title
  t.$('rail-history').focus();
  t.$('rail-history').undrawn = true;
  t.shortcut('k', { ctrlKey: true });
  t.type('mem');
  t.key('Enter');
  assert.equal(t.doc.activeElement, t.$('memory-title'));
  // still drawn (or moved by the command itself): left where it is
  const u = ui();
  u.$('rail-history').addEventListener('click', () => u.$('history-title').focus());
  u.$('text').focus();
  u.shortcut('k', { ctrlKey: true });
  u.type('hist');
  u.key('Enter');
  assert.equal(u.doc.activeElement, u.$('history-title'));
});

test('switcher: a command whose control went away or is disabled does nothing when picked', () => {
  const t = ui();
  t.shortcut('k', { ctrlKey: true });
  t.type('mem');
  assert.deepEqual(t.options(), ['Memory']);
  t.$('memory-open').hidden = true;  // gone since the list was drawn
  t.key('Enter');
  assert.equal(t.$('memory-open').clicks, 0);
  assert.equal(t.$('switcher').open, true);
  assert.deepEqual(t.options(), []);  // the list redrawn
  // New chat while a reply started meanwhile
  let idle = true;
  t.app.idle = () => idle;
  t.type('new');
  assert.deepEqual(t.options(), ['New chat']);
  idle = false;
  t.key('Enter');
  assert.deepEqual(t.app.newChats, []);
  // a disabled control is not listed at all
  t.$('voice-open').disabled = true;
  t.type('voice');
  assert.deepEqual(t.options(), []);
  t.$('voice-open').disabled = false;
  t.type('voice');
  assert.deepEqual(t.options(), ['Start voice conversation']);
});

test('CSS: the switcher\'s status keeps its own styles over dialog.sheet p', () => {
  const flat = CSS.replace(/\s+/g, ' ');
  assert.match(flat, /dialog\.switcher \.switcher-status \{ margin: 0; padding: var\(--s3\) var\(--s4\); text-align: center; color: var\(--muted\);/);
  assert.match(flat, /dialog\.switcher \.switcher-status:empty \{ display: none; \}/);
  assert.doesNotMatch(flat, /(^|\} )\.switcher-status \{/);  // a bare one loses to dialog.sheet p
  assert.match(flat, /dialog\.sheet p \{/);  // (what it has to outrank)
});
