// node --test tests/js/connect.test.mjs  (Node 18+; no packages needed)
//
// Runs the phone page's kwil/web/connect.js (the owner's own sign-ins,
// kwil/owner_connect.py, as rows in the menu's Connections) in node:vm with
// a minimal fake DOM and a recording stand-in for connections.js: shown
// only to the owner; each service's row gets its state, status line and
// action word; one this server can't offer has no row; a service set in
// the server's environment has no buttons; Connect POSTs once (Home
// Assistant with the typed address) and goes only to that service's own
// sign-in page in this tab; Disconnect asks first; names and details are
// text, never HTML; and i18n.js has the Chinese for every fixed string it
// shows.

import test from 'node:test';
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import vm from 'node:vm';

const SCRIPT = readFileSync(new URL('../../kwil/web/connect.js', import.meta.url), 'utf8');
const I18N = readFileSync(new URL('../../kwil/web/i18n.js', import.meta.url), 'utf8');
const NOTION_URL = 'https://api.notion.com/v1/oauth/authorize?client_id=c&state=s';
const HA_URL = 'https://home.example.com/auth/authorize?client_id=x&state=s';

class El {
  constructor(tag, id = '', hidden = false) {
    this.tagName = tag.toUpperCase(); this.id = id; this.hidden = hidden;
    this.textContent = ''; this.disabled = false; this.value = ''; this.className = '';
    this.listeners = {}; this.open = false; this.childNodes = []; this.attrs = {};
    this.dataset = {};
  }
  set innerHTML(v) { this.usedHTML = true; }
  setAttribute(k, v) { this.attrs[k] = String(v); }
  appendChild(c) { this.childNodes.push(c); return c; }
  replaceChildren(...cs) { this.childNodes = cs; }
  addEventListener(type, fn) { (this.listeners[type] ??= []).push(fn); }
  async fire(type) { for (const fn of this.listeners[type] || []) await fn({ preventDefault() {} }); }
  all() { return [this, ...this.childNodes.flatMap((c) => c.all())]; }
  text() { return this.all().map((e) => e.textContent).filter(Boolean); }
}

const notion = (over = {}) => ({ id: 'notion', name: 'Notion', managed: 'none', problem: null,
                                 needs_url: false, detail: null, ...over });
const ha = (over = {}) => ({ id: 'home_assistant', name: 'Home Assistant', managed: 'none',
                             problem: null, needs_url: true, detail: null, ...over });

function load({ services = [notion(), ha()], statusCode = 200, start = {}, disconnect = {},
                confirm = () => true } = {}) {
  const menu = new El('details', 'menu');
  const fetches = [];
  const asked = [];
  // connections.js, recorded: the last set() per row and each row's details
  const rows = { state: {}, bodies: {} };
  let current = services;
  let code = statusCode;
  const win = {
    location: { href: '/' },
    confirm: (q) => { asked.push(q); return confirm(q); },
    fetch: async (url, opts = {}) => {
      fetches.push({ url, opts, body: opts.body ? JSON.parse(opts.body) : undefined });
      let r = { status: 404, body: {} };
      if (url === '/api/connect/status') r = { status: code, body: { services: current } };
      const m = url.match(/^\/api\/connect\/([a-z_]+)\/(start|disconnect)$/);
      if (m && m[2] === 'start') {
        r = start[m[1]] || { status: 200, body: { url: m[1] === 'notion' ? NOTION_URL : HA_URL } };
      }
      if (m && m[2] === 'disconnect') {
        r = disconnect[m[1]] || { status: 200, body: { ok: true, managed: 'none' } };
        if (r.status === 200) current = current.map((s) => (s.id === m[1] ? { ...s, managed: 'none', detail: null } : s));
      }
      return { ok: r.status < 300, status: r.status, json: async () => r.body };
    },
    KwilConnections: {
      set: (id, info) => { rows.state[id] = JSON.parse(JSON.stringify(info)); return {}; },
      body: (id, name) => (rows.bodies[id] ??= Object.assign(new El('div'), { rowName: name })),
    },
    document: {
      querySelector: (sel) => (sel === 'details.menu' ? menu : null),
      createElement: (tag) => new El(tag),
    },
  };
  win.window = win;
  vm.createContext(win);
  vm.runInContext(SCRIPT, win);
  return { win, rows, menu, fetches, asked, api: win.KwilConnect,
           setServices: (s) => { current = s; }, setCode: (c) => { code = c; } };
}

const tick = () => new Promise((r) => setTimeout(r, 0));
// a service's box and message line in its row's details
const rowOf = (rows, id) => rows.bodies[id].childNodes[0];
const noteOf = (rows, id) => rows.bodies[id].childNodes[1];
const buttons = (row) => row.all().filter((e) => e.tagName === 'BUTTON');
const inputOf = (row) => row.all().find((e) => e.tagName === 'INPUT');

test('a family member never sees or asks for it', async () => {
  const { api, rows, fetches, menu } = load();
  api.onState({ owner: false });
  await tick();
  menu.open = true;
  await menu.fire('toggle');
  assert.deepEqual(rows.state, {});
  assert.deepEqual(rows.bodies, {});
  assert.deepEqual(fetches.map((f) => f.url), []);
});

test('the owner sees a row per service, with Connect', async () => {
  const { api, rows, fetches } = load();
  api.onState({ owner: true });
  await tick();
  assert.equal(fetches[0].opts.credentials, 'same-origin');
  assert.deepEqual(rows.state.notion, { name: 'Notion', state: 'available', status: 'Not connected', action: 'Connect' });
  assert.equal(rows.state.home_assistant.name, 'Home Assistant');
  assert.equal(rows.bodies.home_assistant.rowName, 'Home Assistant');
  const n = rowOf(rows, 'notion');
  assert.equal(n.dataset.service, 'notion');
  assert.deepEqual(buttons(n).map((b) => b.textContent), ['Connect']);
  const h = rowOf(rows, 'home_assistant');
  assert.equal(inputOf(h).type, 'url');
  assert.equal(inputOf(h).attrs['aria-label'], "Home Assistant's public https address");
  assert.ok(h.text().includes("Kwil reaches it over the internet: use Nabu Casa's address or a tunnel."));
  assert.equal(noteOf(rows, 'notion').hidden, true);
  assert.equal(noteOf(rows, 'notion').attrs.role, 'status');
});

test('a 404 (not the owner, or off) or no services: no rows; shown rows go off', async () => {
  for (const opts of [{ statusCode: 404 }, { services: [] }]) {
    const { api, rows } = load(opts);
    api.onState({ owner: true });
    await tick();
    assert.deepEqual(rows.state, {});
  }
  const r = load();
  r.api.onState({ owner: true });
  await tick();
  r.setCode(404);
  r.menu.open = true;
  await r.menu.fire('toggle');  // a refresh: now off on the server
  await tick();
  assert.equal(r.rows.state.notion.state, 'off');
  assert.equal(r.rows.state.home_assistant.state, 'off');
});

test('a service the server stops listing goes off', async () => {
  const r = load();
  r.api.onState({ owner: true });
  await tick();
  r.setServices([notion()]);
  r.menu.open = true;
  await r.menu.fire('toggle');
  await tick();
  assert.equal(r.rows.state.home_assistant.state, 'off');
  assert.equal(r.rows.state.notion.state, 'available');
});

test('set in the environment: connected, said so, and no buttons', async () => {
  const { api, rows } = load({ services: [notion({ managed: 'env' }), ha({ managed: 'env' })] });
  api.onState({ owner: true });
  await tick();
  for (const id of ['notion', 'home_assistant']) {
    assert.equal(rows.state[id].state, 'connected');
    assert.equal(rows.state[id].status, 'Set on the server');
    const row = rowOf(rows, id);
    assert.ok(row.text().includes('Set on the server.'));
    assert.deepEqual(buttons(row), []);
    assert.equal(inputOf(row), undefined);
  }
});

test('connected: the detail as text only, and Disconnect', async () => {
  const evil = '<img src=x onerror=alert(1)>';
  const { api, rows } = load({ services: [notion({ managed: 'app', detail: evil })] });
  api.onState({ owner: true });
  await tick();
  assert.deepEqual(rows.state.notion, { name: 'Notion', state: 'connected', status: 'Connected',
                                        action: 'Manage', detail: evil });
  const row = rowOf(rows, 'notion');
  assert.ok(row.text().includes(evil));
  assert.ok(row.all().every((e) => !e.usedHTML));
  assert.deepEqual(buttons(row).map((b) => b.textContent), ['Disconnect']);
});

test('a sign-in that stopped working asks for attention, with Connect again', async () => {
  const { api, rows } = load({ services: [notion({ managed: 'app', problem: 'reconnect' }),
                                          ha({ managed: 'app', problem: 'reconnect' })] });
  api.onState({ owner: true });
  await tick();
  for (const id of ['notion', 'home_assistant']) {
    assert.equal(rows.state[id].state, 'attention');
    assert.equal(rows.state[id].status, 'Sign-in expired');
    assert.equal(rows.state[id].action, 'Reconnect');
    assert.ok(rowOf(rows, id).text().includes('The sign-in stopped working. Connect again.'));
  }
  assert.deepEqual(buttons(rowOf(rows, 'notion')).map((b) => b.textContent), ['Connect', 'Disconnect']);
  assert.deepEqual(buttons(rowOf(rows, 'home_assistant')).map((b) => b.textContent), ['Disconnect']);
});

test('not set up: no row at all; no USER_DATA_KEY: why, and no Connect', async () => {
  const { api, rows } = load({ services: [notion({ problem: 'setup' }), ha({ problem: 'key' })] });
  api.onState({ owner: true });
  await tick();
  assert.deepEqual(rows.state.notion, { name: 'Notion', state: 'off' });
  assert.equal(rows.bodies.notion, undefined);  // nothing to fill
  assert.equal(rows.state.home_assistant.state, 'available');
  assert.equal(rows.state.home_assistant.status, 'Not available');
  assert.ok(rowOf(rows, 'home_assistant').text()
    .includes("This server can't store sign-ins yet (USER_DATA_KEY)."));
  assert.deepEqual(buttons(rowOf(rows, 'home_assistant')), []);
});

test("Notion's Connect POSTs once and opens Notion's own page in this tab", async () => {
  const { api, rows, fetches, win } = load();
  api.onState({ owner: true });
  await tick();
  await buttons(rowOf(rows, 'notion'))[0].fire('click');
  const posts = fetches.filter((f) => f.url === '/api/connect/notion/start');
  assert.equal(posts.length, 1);
  assert.equal(posts[0].opts.method, 'POST');
  assert.deepEqual(posts[0].body, {});
  assert.equal(win.location.href, NOTION_URL);
});

test("Home Assistant's Connect sends the typed address", async () => {
  const { api, rows, fetches, win } = load();
  api.onState({ owner: true });
  await tick();
  const row = rowOf(rows, 'home_assistant');
  await buttons(row)[0].fire('click');  // nothing typed: asks for it
  assert.equal(fetches.filter((f) => f.url.endsWith('/start')).length, 0);
  assert.equal(noteOf(rows, 'home_assistant').textContent, "Type your Home Assistant's public https address.");
  assert.equal(noteOf(rows, 'home_assistant').hidden, false);
  inputOf(row).value = '  home.example.com ';
  await buttons(row)[0].fire('click');
  const post = fetches.find((f) => f.url === '/api/connect/home_assistant/start');
  assert.deepEqual(post.body, { url: 'home.example.com' });
  assert.equal(win.location.href, HA_URL);
});

test('a refused address says to check it', async () => {
  const { api, rows, win } = load({ start: { home_assistant: { status: 400, body: { error: 'x' } } } });
  api.onState({ owner: true });
  await tick();
  const row = rowOf(rows, 'home_assistant');
  inputOf(row).value = 'https://192.168.1.10';
  await buttons(row)[0].fire('click');
  assert.equal(win.location.href, '/');
  assert.equal(buttons(row)[0].disabled, false);
  assert.equal(noteOf(rows, 'home_assistant').textContent,
    "Check the address: it must be your Home Assistant's public https address.");
});

test('any other sign-in address is refused', async () => {
  for (const [id, url] of [['notion', 'https://evil.example/x'],
                           ['notion', 'https://api.notion.com.evil.example/v1/oauth/authorize?x'],
                           ['notion', 'javascript:alert(1)'],
                           ['home_assistant', 'http://home.example.com/auth/authorize?x'],
                           ['home_assistant', 'https://user@evil.example/auth/authorize?x'],
                           ['home_assistant', 'javascript:alert(1)//https://x/auth/authorize?']]) {
    const { api, rows, win } = load({ start: { [id]: { status: 200, body: { url } } } });
    api.onState({ owner: true });
    await tick();
    const row = rowOf(rows, id);
    if (inputOf(row)) inputOf(row).value = 'home.example.com';
    await buttons(row)[0].fire('click');
    assert.equal(win.location.href, '/', url);
    assert.equal(noteOf(rows, id).textContent, "Couldn't start the sign-in.");
  }
});

test('Disconnect asks first; no means nothing is sent', async () => {
  const services = [notion({ managed: 'app', detail: 'W' }), ha({ managed: 'app', detail: 'home.example.com' })];
  let r = load({ services, confirm: () => false });
  r.api.onState({ owner: true });
  await tick();
  await buttons(rowOf(r.rows, 'home_assistant'))[0].fire('click');
  assert.deepEqual(r.asked, ["Disconnect Home Assistant? I won't control your home any more."]);
  assert.equal(r.fetches.filter((f) => f.url.endsWith('/disconnect')).length, 0);
  r = load({ services });
  r.api.onState({ owner: true });
  await tick();
  await buttons(rowOf(r.rows, 'notion'))[0].fire('click');
  const posts = r.fetches.filter((f) => f.url === '/api/connect/notion/disconnect');
  assert.equal(posts.length, 1);
  assert.equal(posts[0].opts.method, 'POST');
  assert.equal(noteOf(r.rows, 'notion').textContent, 'Disconnected.');
  assert.deepEqual(buttons(rowOf(r.rows, 'notion')).map((b) => b.textContent), ['Connect']);
  assert.equal(r.rows.state.notion.state, 'available');  // back under Available
  assert.equal(r.rows.bodies.notion.childNodes.length, 2);  // the same box, refilled
});

test('a failed disconnect says so', async () => {
  const { api, rows } = load({ services: [notion({ managed: 'app' })],
                               disconnect: { notion: { status: 500, body: {} } } });
  api.onState({ owner: true });
  await tick();
  await buttons(rowOf(rows, 'notion'))[0].fire('click');
  assert.equal(noteOf(rows, 'notion').textContent, "Couldn't disconnect. Try again.");
});

test('without connections.js on the page it asks nothing', async () => {
  const r = load();
  delete r.win.KwilConnections;
  vm.runInContext(SCRIPT, r.win);  // a fresh copy, set up without it
  r.win.KwilConnect.onState({ owner: true });
  await tick();
  assert.deepEqual(r.fetches, []);
});

test('i18n.js has the Chinese for every fixed string', () => {
  const ctx = {};
  vm.createContext(ctx);
  vm.runInContext(I18N, ctx);
  const { ZH } = ctx.KwilI18n;
  const strings = [
    'Set on the server.', 'Not set up on this server.',
    "This server can't store sign-ins yet (USER_DATA_KEY).",
    'The sign-in stopped working. Connect again.', 'Connect', 'Disconnect',
    "Home Assistant's public https address",
    "Type your Home Assistant's public https address.", 'Opening the sign-in…',
    "Couldn't start the sign-in.",
    "Check the address: it must be your Home Assistant's public https address.",
    "Disconnect Notion? I won't see your assignments any more.",
    "Disconnect Home Assistant? I won't control your home any more.",
    'Disconnected.', "Couldn't disconnect. Try again.",
    "Kwil reaches it over the internet: use Nabu Casa's address or a tunnel.",
    'Set on the server', 'Sign-in expired', 'Reconnect', 'Connected', 'Manage',
    'Not connected', 'Not available',
  ];
  for (const s of strings) {
    assert.ok(ZH[s], `no Chinese for: ${s}`);
    assert.ok(SCRIPT.includes(`"${s}"`), `not in connect.js: ${s}`);
  }
  const page = readFileSync(new URL('../../kwil/web/index.html', import.meta.url), 'utf8');
  assert.ok(page.includes('>Connections<') && ZH.Connections);
});
