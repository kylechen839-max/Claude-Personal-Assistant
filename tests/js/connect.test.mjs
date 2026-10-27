// node --test tests/js/connect.test.mjs  (Node 18+; no packages needed)
//
// Runs the phone page's kwil/web/connect.js ("Connected services", the
// owner's own sign-ins, kwil/owner_connect.py) in node:vm with a minimal
// fake DOM: shown only to the owner; a service set in the server's
// environment has no buttons; Connect POSTs once (Home Assistant with the
// typed address) and goes only to that service's own sign-in page in this
// tab; Disconnect asks first; names and details are text, never HTML; and
// i18n.js has the Chinese for every fixed string it shows.

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
  const els = {
    connectors: new El('section', 'connectors', true),
    'connector-list': new El('div', 'connector-list'),
    'connectors-status': new El('p', 'connectors-status', true),
  };
  const menu = new El('details', 'menu');
  const fetches = [];
  const asked = [];
  let current = services;
  const win = {
    location: { href: '/' },
    confirm: (q) => { asked.push(q); return confirm(q); },
    fetch: async (url, opts = {}) => {
      fetches.push({ url, opts, body: opts.body ? JSON.parse(opts.body) : undefined });
      let r = { status: 404, body: {} };
      if (url === '/api/connect/status') r = { status: statusCode, body: { services: current } };
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
    document: {
      getElementById: (id) => els[id] || null,
      querySelector: (sel) => (sel === 'details.menu' ? menu : null),
      createElement: (tag) => new El(tag),
    },
  };
  win.window = win;
  vm.createContext(win);
  vm.runInContext(SCRIPT, win);
  return { win, els, menu, fetches, asked, api: win.KwilConnect };
}

const tick = () => new Promise((r) => setTimeout(r, 0));
const rowOf = (els, id) => els['connector-list'].childNodes.find((r) => r.dataset.service === id);
const buttons = (row) => row.all().filter((e) => e.tagName === 'BUTTON');
const inputOf = (row) => row.all().find((e) => e.tagName === 'INPUT');

test('a family member never sees or asks for it', async () => {
  const { api, els, fetches, menu } = load();
  api.onState({ owner: false });
  await tick();
  menu.open = true;
  await menu.fire('toggle');
  assert.equal(els.connectors.hidden, true);
  assert.deepEqual(fetches.map((f) => f.url), []);
});

test('the owner sees a row per service, with Connect', async () => {
  const { api, els, fetches } = load();
  api.onState({ owner: true });
  await tick();
  assert.equal(els.connectors.hidden, false);
  assert.equal(fetches[0].opts.credentials, 'same-origin');
  const n = rowOf(els, 'notion');
  assert.deepEqual(n.text().slice(0, 2), ['Notion', 'Not connected.']);
  assert.deepEqual(buttons(n).map((b) => b.textContent), ['Connect']);
  const h = rowOf(els, 'home_assistant');
  assert.equal(inputOf(h).type, 'url');
  assert.equal(inputOf(h).attrs['aria-label'], "Home Assistant's public https address");
  assert.ok(h.text().includes("Kwil reaches it over the internet: use Nabu Casa's address or a tunnel."));
});

test('a 404 (not the owner, or off) or no services: hidden', async () => {
  for (const opts of [{ statusCode: 404 }, { services: [] }]) {
    const { api, els } = load(opts);
    api.onState({ owner: true });
    await tick();
    assert.equal(els.connectors.hidden, true);
  }
});

test('set in the environment: said so, and no buttons', async () => {
  const { api, els } = load({ services: [notion({ managed: 'env' }), ha({ managed: 'env' })] });
  api.onState({ owner: true });
  await tick();
  for (const id of ['notion', 'home_assistant']) {
    const row = rowOf(els, id);
    assert.ok(row.text().includes('Set on the server.'));
    assert.deepEqual(buttons(row), []);
    assert.equal(inputOf(row), undefined);
  }
});

test('connected: the detail as text only, and Disconnect', async () => {
  const evil = '<img src=x onerror=alert(1)>';
  const { api, els } = load({ services: [notion({ managed: 'app', detail: evil })] });
  api.onState({ owner: true });
  await tick();
  const row = rowOf(els, 'notion');
  assert.ok(row.text().includes('Connected.') && row.text().includes(evil));
  assert.ok(row.all().every((e) => !e.usedHTML));
  assert.deepEqual(buttons(row).map((b) => b.textContent), ['Disconnect']);
});

test('not set up, or no USER_DATA_KEY: why, and no Connect', async () => {
  const { api, els } = load({ services: [notion({ problem: 'setup' }), ha({ problem: 'key' })] });
  api.onState({ owner: true });
  await tick();
  assert.ok(rowOf(els, 'notion').text().includes('Not set up on this server.'));
  assert.ok(rowOf(els, 'home_assistant').text()
    .includes("This server can't store sign-ins yet (USER_DATA_KEY)."));
  assert.deepEqual(buttons(rowOf(els, 'notion')), []);
  assert.deepEqual(buttons(rowOf(els, 'home_assistant')), []);
});

test("Notion's Connect POSTs once and opens Notion's own page in this tab", async () => {
  const { api, els, fetches, win } = load();
  api.onState({ owner: true });
  await tick();
  await buttons(rowOf(els, 'notion'))[0].fire('click');
  const posts = fetches.filter((f) => f.url === '/api/connect/notion/start');
  assert.equal(posts.length, 1);
  assert.equal(posts[0].opts.method, 'POST');
  assert.deepEqual(posts[0].body, {});
  assert.equal(win.location.href, NOTION_URL);
});

test("Home Assistant's Connect sends the typed address", async () => {
  const { api, els, fetches, win } = load();
  api.onState({ owner: true });
  await tick();
  const row = rowOf(els, 'home_assistant');
  await buttons(row)[0].fire('click');  // nothing typed: asks for it
  assert.equal(fetches.filter((f) => f.url.endsWith('/start')).length, 0);
  assert.equal(els['connectors-status'].textContent, "Type your Home Assistant's public https address.");
  inputOf(row).value = '  home.example.com ';
  await buttons(row)[0].fire('click');
  const post = fetches.find((f) => f.url === '/api/connect/home_assistant/start');
  assert.deepEqual(post.body, { url: 'home.example.com' });
  assert.equal(win.location.href, HA_URL);
});

test('a refused address says to check it', async () => {
  const { api, els, win } = load({ start: { home_assistant: { status: 400, body: { error: 'x' } } } });
  api.onState({ owner: true });
  await tick();
  const row = rowOf(els, 'home_assistant');
  inputOf(row).value = 'https://192.168.1.10';
  await buttons(row)[0].fire('click');
  assert.equal(win.location.href, '/');
  assert.equal(buttons(row)[0].disabled, false);
  assert.equal(els['connectors-status'].textContent,
    "Check the address: it must be your Home Assistant's public https address.");
});

test('any other sign-in address is refused', async () => {
  for (const [id, url] of [['notion', 'https://evil.example/x'],
                           ['notion', 'https://api.notion.com.evil.example/v1/oauth/authorize?x'],
                           ['notion', 'javascript:alert(1)'],
                           ['home_assistant', 'http://home.example.com/auth/authorize?x'],
                           ['home_assistant', 'https://user@evil.example/auth/authorize?x'],
                           ['home_assistant', 'javascript:alert(1)//https://x/auth/authorize?']]) {
    const { api, els, win } = load({ start: { [id]: { status: 200, body: { url } } } });
    api.onState({ owner: true });
    await tick();
    const row = rowOf(els, id);
    if (inputOf(row)) inputOf(row).value = 'home.example.com';
    await buttons(row)[0].fire('click');
    assert.equal(win.location.href, '/', url);
    assert.equal(els['connectors-status'].textContent, "Couldn't start the sign-in.");
  }
});

test('Disconnect asks first; no means nothing is sent', async () => {
  const services = [notion({ managed: 'app', detail: 'W' }), ha({ managed: 'app', detail: 'home.example.com' })];
  let r = load({ services, confirm: () => false });
  r.api.onState({ owner: true });
  await tick();
  await buttons(rowOf(r.els, 'home_assistant'))[0].fire('click');
  assert.deepEqual(r.asked, ["Disconnect Home Assistant? I won't control your home any more."]);
  assert.equal(r.fetches.filter((f) => f.url.endsWith('/disconnect')).length, 0);
  r = load({ services });
  r.api.onState({ owner: true });
  await tick();
  await buttons(rowOf(r.els, 'notion'))[0].fire('click');
  const posts = r.fetches.filter((f) => f.url === '/api/connect/notion/disconnect');
  assert.equal(posts.length, 1);
  assert.equal(posts[0].opts.method, 'POST');
  assert.equal(r.els['connectors-status'].textContent, 'Disconnected.');
  assert.deepEqual(buttons(rowOf(r.els, 'notion')).map((b) => b.textContent), ['Connect']);
});

test('a failed disconnect says so', async () => {
  const { api, els } = load({ services: [notion({ managed: 'app' })],
                              disconnect: { notion: { status: 500, body: {} } } });
  api.onState({ owner: true });
  await tick();
  await buttons(rowOf(els, 'notion'))[0].fire('click');
  assert.equal(els['connectors-status'].textContent, "Couldn't disconnect. Try again.");
});

test('i18n.js has the Chinese for every fixed string', () => {
  const ctx = {};
  vm.createContext(ctx);
  vm.runInContext(I18N, ctx);
  const { ZH } = ctx.KwilI18n;
  const strings = [
    'Set on the server.', 'Connected.', 'Not connected.', 'Not set up on this server.',
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
  ];
  for (const s of strings) {
    assert.ok(ZH[s], `no Chinese for: ${s}`);
    assert.ok(SCRIPT.includes(s), `not in connect.js: ${s}`);
  }
  const page = readFileSync(new URL('../../kwil/web/index.html', import.meta.url), 'utf8');
  assert.ok(page.includes('>Connected services<') && ZH['Connected services']);
});
