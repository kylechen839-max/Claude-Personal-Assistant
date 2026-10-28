// node --test tests/js/connections.test.mjs  (Node 18+; no packages needed)
//
// Runs the phone page's kwil/web/connections.js (the Settings menu's
// "Connections" list) in node:vm with a small fake DOM built from
// index.html's own rows: rows are ordered connected (needing attention
// first) then still to connect, in a fixed order; "off" rows, empty lists
// and an empty section are hidden; the right side shows "Connect" or a
// status dot and word; the subtitle is someone's detail (text only) or the
// row's own line; a tap opens that row's sheet (a modal dialog, one at a
// time, aria-expanded), and Close, Esc, a tap outside it or closing the
// menu shuts it; nothing moves while a sheet is open (that would take the
// dialog out of the top layer), only out-of-place rows move, focus is kept; a row the page doesn't have is
// built like index.html's with a letter icon; and i18n.js has the Chinese
// for every heading, subtitle, status and action word.

import test from 'node:test';
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import vm from 'node:vm';

const read = (name) => readFileSync(new URL(`../../kwil/web/${name}`, import.meta.url), 'utf8');
const SCRIPT = read('connections.js');
const PAGE = read('index.html');

class El {
  constructor(doc, tag) {
    this.doc = doc; this.tagName = tag.toUpperCase(); this.children = []; this.parentNode = null;
    this.attrs = {}; this.dataset = {}; this.hidden = false; this.listeners = {};
    this.className = ''; this._text = ''; this.open = false;
    if (tag === 'dialog') {
      this.showModal = () => { if (this.hidden) throw new Error('showModal on a hidden dialog'); this.open = true; this.modal = true; };
      this.close = () => { this.open = false; this.fire('close'); };
      this.getBoundingClientRect = () => ({ left: 0, right: 390, top: 400, bottom: 844 });
    }
  }
  get id() { return this.attrs.id || ''; }
  set id(v) { this.attrs.id = String(v); }
  get childNodes() { return this.children; }
  get textContent() { return this._text + this.children.map((c) => c.textContent).join(''); }
  set textContent(v) { this.children = []; this._text = String(v); }
  set innerHTML(v) { this.usedHTML = true; }
  setAttribute(k, v) { this.attrs[k] = String(v); }
  getAttribute(k) { return k in this.attrs ? this.attrs[k] : null; }
  // as a browser does when a node is moved: it loses focus, and an open
  // modal dialog inside it leaves the top layer (still open, not modal)
  detach(c) {
    if (!c.parentNode) return;
    c.parentNode.children = c.parentNode.children.filter((x) => x !== c);
    if (this.doc.activeElement && c.contains(this.doc.activeElement)) this.doc.activeElement = null;
    for (const d of c.all()) if (d.tagName === 'DIALOG' && d.open) d.modal = false;
    this.doc.moves = (this.doc.moves || 0) + 1;
  }
  appendChild(c) {
    this.detach(c);
    c.parentNode = this; this.children.push(c); return c;
  }
  insertBefore(c, ref) {
    if (ref === null || ref === undefined) return this.appendChild(c);
    this.detach(c);
    c.parentNode = this; this.children.splice(this.children.indexOf(ref), 0, c); return c;
  }
  get firstElementChild() { return this.children[0] || null; }
  get nextElementSibling() {
    const sibs = this.parentNode ? this.parentNode.children : [];
    return sibs[sibs.indexOf(this) + 1] || null;
  }
  append(...cs) { cs.forEach((c) => this.appendChild(c)); }
  contains(x) { return x === this || this.children.some((c) => c.contains(x)); }
  all() { return [this, ...this.children.flatMap((c) => c.all())]; }
  matches(sel) { return sel.startsWith('.') && this.className.split(/\s+/).includes(sel.slice(1)); }
  querySelector(sel) { return this.all().slice(1).find((e) => e.matches(sel)) || null; }
  closest(sel) { let e = this; while (e && !e.matches(sel)) e = e.parentNode; return e || null; }
  addEventListener(type, fn) { (this.listeners[type] ??= []).push(fn); }
  fire(type, target = this, extra = {}) { for (const fn of this.listeners[type] || []) fn({ target, ...extra }); }
  focus() { this.doc.activeElement = this; }
}

// The Connections section, its rows parsed from index.html (so a markup
// change shows up here, and the page's rows match what build() makes).
function load() {
  const htmlClasses = new Set();
  const doc = { activeElement: null, roots: [], documentElement: { classList: {
    toggle: (c, on) => { if (on) htmlClasses.add(c); else htmlClasses.delete(c); },
    contains: (c) => htmlClasses.has(c) } } };
  doc.createElement = (tag) => new El(doc, tag);
  doc.getElementById = (id) => doc.roots.flatMap((r) => r.all()).find((e) => e.id === id) || null;
  const menu = new El(doc, 'details');
  doc.querySelector = (sel) => (sel === 'details.menu' ? menu : null);
  const section = new El(doc, 'section'); section.id = 'connections'; section.hidden = true;
  doc.roots.push(section);
  for (const key of ['connected', 'available']) {
    const label = new El(doc, 'h3'); label.id = `conn-${key}-label`;
    const list = new El(doc, 'ul'); list.id = `conn-${key}`; list.hidden = true;
    section.append(label, list);
  }
  const available = doc.getElementById('conn-available');
  const span = (cls, text = '') => { const s = new El(doc, 'span'); s.className = cls; s.textContent = text; return s; };
  for (const m of PAGE.matchAll(/<li class="conn-row" id="conn-([a-z_]+)" data-conn="\1" hidden>[\s\S]*?<\/li>/g)) {
    const [html, id] = m;
    for (const want of [
      `<button class="row conn-head" id="conn-${id}-head" type="button" aria-haspopup="dialog" aria-expanded="false" aria-controls="conn-${id}-body">`,
      `<span class="conn-icon" aria-hidden="true"><svg viewBox="0 0 24 24"><use href="#icon-${id}"/></svg></span>`,
      '<span class="conn-state state off"></span>', '<span class="conn-action value connect" hidden></span>',
      `<dialog class="conn-body conn-sheet" id="conn-${id}-body" aria-labelledby="conn-${id}-title" hidden>`,
      `<h2 id="conn-${id}-title">`, '<button class="icon-btn conn-close" type="button" aria-label="Close"></button>']) {
      assert.ok(html.includes(want), `${id}: ${want}`);
    }
    const li = new El(doc, 'li'); li.className = 'conn-row'; li.id = `conn-${id}`; li.dataset.conn = id; li.hidden = true;
    const head = new El(doc, 'button'); head.className = 'row conn-head'; head.id = `conn-${id}-head`;
    head.setAttribute('aria-expanded', 'false'); head.setAttribute('aria-controls', `conn-${id}-body`);
    const text = span('conn-text');
    text.append(span('conn-name', html.match(/<span class="conn-name">([^<]+)</)[1]),
                span('conn-sub', html.match(/<span class="conn-sub">([^<]*)</)[1]));
    const action = span('conn-action value connect'); action.hidden = true;
    head.append(span('conn-icon'), text, span('conn-state state off'), action);
    const body = new El(doc, 'dialog'); body.className = 'conn-body conn-sheet'; body.id = `conn-${id}-body`; body.hidden = true;
    const close = new El(doc, 'button'); close.className = 'icon-btn conn-close';
    body.append(close);
    li.append(head, body);
    available.appendChild(li);
  }
  const win = { document: doc };
  win.window = win;
  vm.createContext(win);
  vm.runInContext(SCRIPT, win);
  const api = win.KwilConnections;
  return { win, doc, menu, section, api, ctl: api.create(win, doc) };
}

const ids = (list) => list.children.filter((li) => !li.hidden).map((li) => li.dataset.conn);
const head = (doc, id) => doc.getElementById(`conn-${id}-head`);
const body = (doc, id) => doc.getElementById(`conn-${id}-body`);
const part = (doc, id, cls) => doc.getElementById(`conn-${id}`).querySelector(`.${cls}`);
const isOpen = (doc, id) => head(doc, id).getAttribute('aria-expanded') === 'true' &&
  !body(doc, id).hidden && body(doc, id).open;

test('the page has a row with an icon for each known service, in the shared markup', () => {
  const { doc, api } = load();
  for (const id of ['google', 'notion', 'canvas', 'home_assistant', 'spotify', 'anthropic']) {
    assert.ok(doc.getElementById(`conn-${id}`), id);
    assert.ok(api.ORDER.includes(id), id);
    assert.ok(PAGE.includes(`<symbol id="icon-${id}" viewBox="0 0 24 24"><path d="`), id);
  }
  // the old one-off sections are gone; their ids moved into the sheets
  assert.ok(!PAGE.includes('id="connector-list"'));
  for (const [row, inner] of [['google', 'google-connect'], ['google', 'google-disconnect'],
    ['notion', 'notion-connect'], ['canvas', 'student'], ['spotify', 'spotify-connect'],
    ['anthropic', 'api-key']]) {
    const li = PAGE.slice(PAGE.indexOf(`id="conn-${row}"`));
    assert.ok(li.slice(0, li.indexOf('</li>')).includes(`id="${inner}"`), `${inner} in ${row}`);
  }
});

test('group(): attention first, then connected, then available; off nowhere', () => {
  const { api } = load();
  const g = api.group([
    { id: 'spotify', state: 'available' }, { id: 'google', state: 'connected' },
    { id: 'zzz', state: 'connected' }, { id: 'canvas', state: 'attention' },
    { id: 'notion', state: 'off' }, { id: 'home_assistant', state: 'available' },
    { id: 'anthropic', state: 'connected' }, null, { state: 'connected' }]);
  assert.deepEqual([...g.connected], ['canvas', 'google', 'anthropic', 'zzz']);
  assert.deepEqual([...g.available], ['home_assistant', 'spotify']);
  assert.deepEqual(JSON.parse(JSON.stringify(api.group(undefined))), { connected: [], available: [] });
});

test('set(): rows move between the lists; empty lists and the section hide', () => {
  const { doc, ctl, section } = load();
  const connected = doc.getElementById('conn-connected');
  const available = doc.getElementById('conn-available');
  assert.equal(section.hidden, true);
  ctl.set('spotify', { state: 'available', status: 'Not connected', action: 'Connect' });
  ctl.set('google', { state: 'available', status: 'Not connected', action: 'Connect' });
  assert.equal(section.hidden, false);
  assert.deepEqual(ids(available), ['google', 'spotify']);
  assert.equal(connected.hidden, true);
  assert.equal(doc.getElementById('conn-connected-label').hidden, true);
  assert.equal(available.hidden, false);
  ctl.set('google', { state: 'connected', status: 'Connected', action: 'Manage' });
  ctl.set('notion', { state: 'attention', status: 'Sign-in expired', action: 'Reconnect' });
  assert.deepEqual(ids(connected), ['notion', 'google']);
  assert.deepEqual(ids(available), ['spotify']);
  assert.equal(connected.hidden, false);
  assert.equal(doc.getElementById('conn-connected-label').hidden, false);
  assert.equal(doc.getElementById('conn-google').dataset.state, 'connected');
  assert.equal(doc.getElementById('conn-notion').dataset.alert, '1');
  for (const id of ['google', 'notion', 'spotify']) ctl.set(id, { state: 'off' });
  assert.equal(section.hidden, true);
  assert.equal(doc.getElementById('conn-spotify').hidden, true);
  // an unknown state is "off", not shown
  ctl.set('canvas', { state: 'bogus', status: 'x' });
  assert.equal(doc.getElementById('conn-canvas').hidden, true);
  assert.equal(section.hidden, true);
});

test('the right side: "Connect" to connect, else a status dot and word', () => {
  const { doc, ctl } = load();
  const shows = (id) => {
    const st = part(doc, id, 'conn-state');
    const act = part(doc, id, 'conn-action');
    return act.hidden ? [st.hidden ? '' : st.textContent, st.className] : [act.textContent, 'action'];
  };
  ctl.set('google', { state: 'available', status: 'Not connected', action: 'Connect' });
  assert.deepEqual(shows('google'), ['Connect', 'action']);
  ctl.set('google', { state: 'connected', status: 'Connected', action: 'Manage' });
  assert.deepEqual(shows('google'), ['Connected', 'conn-state state on']);
  ctl.set('notion', { state: 'attention', status: 'Sign-in expired', action: 'Reconnect' });
  assert.deepEqual(shows('notion'), ['Sign-in expired', 'conn-state state warn']);
  ctl.set('anthropic', { state: 'available', status: 'Key needed', action: 'Add key', alert: true });
  assert.deepEqual(shows('anthropic'), ['Key needed', 'conn-state state warn']);
  assert.deepEqual(ids(doc.getElementById('conn-available')), ['anthropic']);  // flagged, not moved
  ctl.set('spotify', { state: 'available', status: 'Not available' });
  assert.deepEqual(shows('spotify'), ['Not available', 'conn-state state off']);
});

test('the subtitle: their detail as text (cut at 100), a fixed line, or the row\'s own', () => {
  const { doc, ctl } = load();
  const sub = () => part(doc, 'notion', 'conn-sub').textContent;
  const own = sub();
  assert.ok(own);
  const evil = '<img src=x onerror=alert(1)>' + 'x'.repeat(200);
  ctl.set('notion', { state: 'connected', status: 'Connected', detail: evil });
  assert.equal(sub(), evil.slice(0, 100));
  assert.ok(doc.getElementById('conn-notion').all().every((e) => !e.usedHTML));
  ctl.set('notion', { state: 'available', status: 'Not connected', action: 'Connect', detail: 42 });
  assert.equal(sub(), own);  // back to the page's own line
  ctl.set('google', { state: 'available', action: 'Connect', sub: 'Calendar' });
  assert.equal(part(doc, 'google', 'conn-sub').textContent, 'Calendar');
});

test('a tap opens the row\'s sheet, one at a time; Close and a tap outside shut it', () => {
  const { doc, ctl, section } = load();
  for (const id of ['google', 'spotify']) ctl.set(id, { state: 'available', status: 'Not connected', action: 'Connect' });
  section.fire('click', part(doc, 'google', 'conn-name'));  // a tap on the name inside the row
  assert.ok(isOpen(doc, 'google'));
  assert.equal(body(doc, 'google').modal, true);
  section.fire('click', head(doc, 'spotify'));
  assert.ok(!isOpen(doc, 'google'));
  assert.equal(body(doc, 'google').hidden, true);
  assert.ok(isOpen(doc, 'spotify'));
  // a tap inside the sheet (its own buttons, its padding) leaves it open
  section.fire('click', body(doc, 'spotify'), { clientX: 100, clientY: 600 });
  assert.ok(isOpen(doc, 'spotify'));
  // the backdrop: the dialog itself, outside its box
  section.fire('click', body(doc, 'spotify'), { clientX: 100, clientY: 100 });
  assert.ok(!isOpen(doc, 'spotify'));
  assert.equal(doc.activeElement, head(doc, 'spotify'));  // focus back on its row
  section.fire('click', head(doc, 'spotify'));
  section.fire('click', body(doc, 'spotify').querySelector('.conn-close'));
  assert.ok(!isOpen(doc, 'spotify'));
});

test('Esc (the dialog closing itself) keeps the row in step', () => {
  const { doc, ctl } = load();
  ctl.set('google', { state: 'available', status: 'Not connected', action: 'Connect' });
  ctl.toggle('google', true);
  assert.ok(isOpen(doc, 'google'));
  body(doc, 'google').close();  // what Esc does
  assert.equal(head(doc, 'google').getAttribute('aria-expanded'), 'false');
  assert.equal(body(doc, 'google').hidden, true);
});

test('a row going off closes; closing the menu closes any sheet', () => {
  const { doc, ctl, menu } = load();
  ctl.set('google', { state: 'connected', status: 'Connected', action: 'Manage' });
  ctl.toggle('google', true);
  ctl.set('google', { state: 'off' });
  assert.ok(!isOpen(doc, 'google'));
  ctl.toggle('google', true);  // a hidden row never opens
  assert.equal(body(doc, 'google').hidden, true);
  ctl.set('spotify', { state: 'available', status: 'Not connected', action: 'Connect' });
  ctl.toggle('spotify', true);
  menu.open = true; menu.fire('toggle');
  assert.ok(isOpen(doc, 'spotify'));
  menu.open = false; menu.fire('toggle');
  assert.ok(!isOpen(doc, 'spotify'));
});

test('a connect or disconnect inside an open sheet keeps it modal; the row moves once it closes', () => {
  const { doc, ctl } = load();
  ctl.set('google', { state: 'available', status: 'Not connected', action: 'Connect' });
  ctl.set('notion', { state: 'connected', status: 'Connected', action: 'Manage' });
  ctl.toggle('notion', true);
  const disconnect = doc.createElement('button');
  body(doc, 'notion').appendChild(disconnect);
  disconnect.focus();
  doc.moves = 0;
  // the in-sheet Disconnect worked: notion's row now belongs under the rest
  ctl.set('notion', { state: 'available', status: 'Not connected', action: 'Connect' });
  assert.equal(doc.moves, 0);  // nothing moved while the sheet is open
  assert.ok(isOpen(doc, 'notion'));
  assert.equal(body(doc, 'notion').modal, true);  // still in the top layer
  assert.equal(doc.activeElement, disconnect);
  assert.equal(part(doc, 'notion', 'conn-action').textContent, 'Connect');  // the row itself is current
  assert.ok(doc.documentElement.classList.contains('conn-sheet-open'));  // the page behind can't scroll
  // closed (Close, Esc or a tap outside): now it regroups, focus back on its row
  body(doc, 'notion').close();
  assert.ok(!doc.documentElement.classList.contains('conn-sheet-open'));
  assert.deepEqual(ids(doc.getElementById('conn-available')), ['google', 'notion']);
  assert.equal(doc.getElementById('conn-connected').hidden, true);
  assert.equal(doc.activeElement, head(doc, 'notion'));
});

test("another row's status arriving while a sheet is open never moves anything", () => {
  const { doc, ctl, section } = load();
  ctl.set('canvas', { state: 'available', status: 'Not connected', action: 'Connect' });
  ctl.toggle('canvas', true);
  doc.moves = 0;
  ctl.set('google', { state: 'connected', status: 'Connected', action: 'Manage' });
  ctl.set('spotify', { state: 'available', status: 'Not connected', action: 'Connect' });
  ctl.set('google', { state: 'attention', status: 'Sign-in expired', action: 'Reconnect' });
  assert.equal(doc.moves, 0);
  assert.equal(body(doc, 'canvas').modal, true);
  assert.equal(section.hidden, false);
  assert.equal(part(doc, 'google', 'conn-state').textContent, 'Sign-in expired');
  ctl.toggle('canvas', false);
  assert.deepEqual(ids(doc.getElementById('conn-connected')), ['google']);
  assert.deepEqual(ids(doc.getElementById('conn-available')), ['canvas', 'spotify']);
});

test('only rows out of place move, and a moved row keeps the focus', () => {
  const { doc, ctl } = load();
  for (const id of ['google', 'notion', 'canvas', 'spotify']) {
    ctl.set(id, { state: 'available', status: 'Not connected', action: 'Connect' });
  }
  head(doc, 'canvas').focus();
  doc.moves = 0;
  ctl.set('canvas', { state: 'connected', status: 'Connected', action: 'Manage' });
  assert.equal(doc.moves, 1);  // just canvas
  assert.deepEqual(ids(doc.getElementById('conn-connected')), ['canvas']);
  assert.deepEqual(ids(doc.getElementById('conn-available')), ['google', 'notion', 'spotify']);
  assert.equal(doc.activeElement, head(doc, 'canvas'));
  doc.moves = 0;
  ctl.set('canvas', { state: 'connected', status: 'Connected', action: 'Manage', detail: 'W' });
  ctl.set('google', { state: 'available', status: 'Not connected', action: 'Connect', sub: 'Calendar' });
  assert.equal(doc.moves, 0);  // nothing changed order
  doc.moves = 0;
  ctl.set('google', { state: 'connected', status: 'Connected', action: 'Manage' });
  assert.equal(doc.moves, 1);
  assert.deepEqual(ids(doc.getElementById('conn-connected')), ['google', 'canvas']);
});

test("a service the page doesn't have gets a row built like index.html's, text only", () => {
  const { doc, ctl, section } = load();
  assert.equal(ctl.body('teams'), null);  // no name: not made up
  assert.equal(ctl.body('Bad Id', 'X'), null);
  assert.equal(ctl.set('../x', { state: 'connected', name: 'X' }), null);
  const box = ctl.body('teams', '<b>Teams</b>');
  const li = doc.getElementById('conn-teams');
  assert.equal(box, doc.getElementById('conn-teams-body'));
  assert.equal(box.tagName, 'DIALOG');
  assert.equal(li.hidden, true);  // until it has a state
  assert.equal(box.hidden, true);
  assert.equal(box.getAttribute('aria-labelledby'), 'conn-teams-title');
  assert.equal(doc.getElementById('conn-teams-title').textContent, '<b>Teams</b>');
  const h = head(doc, 'teams');
  assert.equal(h.type, 'button');
  assert.equal(h.className, 'row conn-head');
  assert.equal(h.getAttribute('aria-haspopup'), 'dialog');
  assert.equal(h.getAttribute('aria-controls'), 'conn-teams-body');
  assert.equal(h.getAttribute('aria-expanded'), 'false');
  assert.equal(li.querySelector('.conn-name').textContent, '<b>Teams</b>');
  assert.equal(li.querySelector('.conn-icon').textContent, '<');  // a letter tile
  assert.equal(li.querySelector('.conn-icon').getAttribute('aria-hidden'), 'true');
  assert.equal(box.querySelector('.conn-close').getAttribute('aria-label'), 'Close');
  assert.ok(li.all().every((e) => !e.usedHTML));
  ctl.set('teams', { state: 'available', status: 'Not connected', action: 'Connect' });
  assert.deepEqual(ids(doc.getElementById('conn-available')), ['teams']);
  section.fire('click', h);
  assert.ok(isOpen(doc, 'teams'));
  box.close();
  assert.ok(!isOpen(doc, 'teams'));
});

test('the shared helpers do nothing on a page without the section', () => {
  const win = { document: { getElementById: () => null, querySelector: () => null } };
  win.window = win;
  vm.createContext(win);
  vm.runInContext(SCRIPT, win);
  assert.equal(win.KwilConnections.set('google', { state: 'connected' }), null);
  assert.equal(win.KwilConnections.body('google', 'Google'), null);
});

test('i18n.js has the Chinese for the headings, subtitles, explanations, statuses and actions', () => {
  const ctx = {};
  vm.createContext(ctx);
  vm.runInContext(read('i18n.js'), ctx);
  const { ZH } = ctx.KwilI18n;
  const said = new Set(['Connections', 'Connected', 'Available', 'Close', 'Calendar']);
  for (const file of ['i18n.js', 'notion.js', 'connect.js', 'student.js', 'spotify.js', 'family.js']) {
    const src = read(file);
    const found = [...src.matchAll(/\b(?:status|action|sub): "([^"]+)"/g)].map((m) => m[1]);
    assert.ok(found.length >= 2, file);
    found.forEach((s) => said.add(s));
  }
  const section = PAGE.slice(PAGE.indexOf('<section id="connections"'), PAGE.indexOf('<section id="api-key"'));
  for (const m of section.matchAll(/<(?:span class="conn-sub"|p class="conn-about"|h2 id="conn-title" class="group-head")>([^<]+)</g)) said.add(m[1]);
  assert.ok(said.size >= 28, [...said].join('|'));
  for (const s of said) {
    assert.ok(ZH[s], `no Chinese for: ${s}`);
    assert.match(ZH[s], /[一-鿿]/, s);
  }
});
