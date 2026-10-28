// node --test tests/js/notion.test.mjs  (Node 18+; no packages needed)
//
// Runs the phone page's kwil/web/notion.js in node:vm with a minimal
// fake window: "Connect Notion" shows only for an invited person (never the
// owner, whose page never even asks) and only when the server says it is
// set up; their workspace and database names are shown as text only; a tap
// POSTs once and goes only to Notion's own consent page; disconnect asks
// first; and i18n.js has the Chinese for every string it shows.

import test from 'node:test';
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import vm from 'node:vm';

const SCRIPT = readFileSync(new URL('../../kwil/web/notion.js', import.meta.url), 'utf8');
const I18N = readFileSync(new URL('../../kwil/web/i18n.js', import.meta.url), 'utf8');
const NOTION_URL = 'https://api.notion.com/v1/oauth/authorize?client_id=c&state=s';

class El {
  constructor(id, hidden = false) {
    this.id = id; this.hidden = hidden; this.textContent = ''; this.disabled = false;
    this.listeners = {}; this.open = false;
  }
  set innerHTML(v) { this.usedHTML = true; }  // (a throw would be swallowed)
  addEventListener(type, fn) { (this.listeners[type] ??= []).push(fn); }
  async fire(type) { for (const fn of this.listeners[type] || []) await fn({ preventDefault() {} }); }
}

function load({ status = { status: 200, body: { enabled: true, connected: false, workspace: null, database: null } },
                connect = { status: 200, body: { url: NOTION_URL } },
                disconnect = { status: 200, body: { ok: true, connected: false } },
                confirm = () => true } = {}) {
  const ids = ['notion-connect', 'notion-disconnect', 'notion-status', 'notion-detail'];
  const els = Object.fromEntries(ids.map((id) => [id, new El(id, true)]));
  const menu = new El('menu');
  const fetches = [];
  const asked = [];
  const win = {
    location: { href: '/' },
    confirm: (q) => { asked.push(q); return confirm(q); },
    fetch: async (url, opts = {}) => {
      fetches.push({ url, opts });
      const r = url === '/api/notion/status' ? status : url === '/api/notion/connect' ? connect
        : url === '/api/notion/disconnect' ? disconnect : { status: 404, body: {} };
      return { ok: r.status < 300, status: r.status, json: async () => r.body };
    },
    document: {
      getElementById: (id) => els[id] || null,
      querySelector: (sel) => (sel === 'details.menu' ? menu : null),
    },
  };
  win.window = win;
  vm.createContext(win);
  vm.runInContext(SCRIPT, win);
  return { win, els, menu, fetches, asked, api: win.KwilNotion };
}

const tick = () => new Promise((r) => setTimeout(r, 0));

test('the owner never sees or asks for Notion', async () => {
  const { api, els, fetches, menu } = load();
  api.onState({ owner: true });
  await tick();
  menu.open = true;
  await menu.fire('toggle');
  assert.equal(els['notion-connect'].hidden, true);
  assert.deepEqual(fetches.map((f) => f.url), []);
});

test('an invited person sees it when the server has it set up', async () => {
  const { api, els, fetches } = load();
  api.onState({ owner: false });
  await tick();
  assert.equal(els['notion-connect'].hidden, false);
  assert.equal(els['notion-connect'].textContent, 'Connect Notion');
  assert.equal(els['notion-disconnect'].hidden, true);
  assert.equal(els['notion-status'].hidden, true);
  assert.equal(fetches[0].opts.credentials, 'same-origin');
});

test('connected: workspace and database as text, and Disconnect', async () => {
  const evil = '<img src=x onerror=alert(1)>';
  const { api, els } = load({ status: { status: 200, body: {
    enabled: true, connected: true, workspace: evil, database: 'Fall Assignments' } } });
  api.onState({ owner: false });
  await tick();
  assert.equal(els['notion-connect'].textContent, 'Reconnect Notion');
  assert.equal(els['notion-status'].textContent, 'Notion connected.');
  assert.equal(els['notion-detail'].hidden, false);
  assert.equal(els['notion-detail'].textContent, `${evil} · Fall Assignments`);
  assert.ok(Object.values(els).every((el) => !el.usedHTML));
  assert.equal(els['notion-disconnect'].hidden, false);
});

test('connected without one Assignments database: says how to fix it', async () => {
  const { api, els } = load({ status: { status: 200, body: {
    enabled: true, connected: true, workspace: 'Tess', database: null } } });
  api.onState({ owner: false });
  await tick();
  assert.match(els['notion-status'].textContent, /share only your Assignments database/);
  assert.equal(els['notion-status'].hidden, false);
});

test('not set up (or 404): stays hidden', async () => {
  for (const status of [{ status: 200, body: { enabled: false, connected: false } },
                        { status: 404, body: {} }]) {
    const { api, els } = load({ status });
    api.onState({ owner: false });
    await tick();
    assert.equal(els['notion-connect'].hidden, true);
  }
});

test("a tap POSTs once and opens Notion's consent page", async () => {
  const { api, els, fetches, win } = load();
  api.onState({ owner: false });
  await tick();
  await els['notion-connect'].fire('click');
  const posts = fetches.filter((f) => f.url === '/api/notion/connect');
  assert.equal(posts.length, 1);
  assert.equal(posts[0].opts.method, 'POST');
  assert.equal(win.location.href, NOTION_URL);
});

test('any other address is refused', async () => {
  for (const url of ['https://evil.example/x', 'https://api.notion.com.evil.example/v1/oauth/authorize?x',
                     'javascript:alert(1)']) {
    const { api, els, win } = load({ connect: { status: 200, body: { url } } });
    api.onState({ owner: false });
    await tick();
    await els['notion-connect'].fire('click');
    assert.equal(win.location.href, '/');
    assert.equal(els['notion-connect'].disabled, false);
    assert.equal(els['notion-status'].textContent, "Couldn't start the Notion sign-in.");
  }
});

test('disconnect asks first; no means nothing is sent', async () => {
  const connected = { status: 200, body: { enabled: true, connected: true, workspace: 'W', database: 'D' } };
  let r = load({ status: connected, confirm: () => false });
  r.api.onState({ owner: false });
  await tick();
  await r.els['notion-disconnect'].fire('click');
  assert.equal(r.asked.length, 1);
  assert.equal(r.fetches.filter((f) => f.url === '/api/notion/disconnect').length, 0);
  r = load({ status: connected });
  r.api.onState({ owner: false });
  await tick();
  await r.els['notion-disconnect'].fire('click');
  const posts = r.fetches.filter((f) => f.url === '/api/notion/disconnect');
  assert.equal(posts.length, 1);
  assert.equal(posts[0].opts.method, 'POST');
  assert.equal(r.els['notion-status'].textContent, 'Notion disconnected.');
});

test('every string it shows has Chinese in i18n.js', () => {
  const ctx = {};
  vm.createContext(ctx);
  vm.runInContext(I18N, ctx);
  const { ZH } = ctx.KwilI18n;
  const strings = ['Connect Notion', 'Reconnect Notion', 'Disconnect Notion', 'Notion connected.',
                   'Opening Notion…', "Couldn't start the Notion sign-in.", 'Notion disconnected.',
                   "Disconnect Notion? I won't see your assignments any more.",
                   "Couldn't disconnect. Try again."];
  for (const s of strings) {
    assert.ok(ZH[s], s);
    assert.ok(SCRIPT.includes(s), s);
  }
  const long = 'Notion is connected, but I couldn\'t find exactly one Assignments database. ' +
    'Tap "Reconnect Notion" and share only your Assignments database.';
  assert.ok(ZH[long]);
  assert.equal(ZH['Connect Notion'], '连接 Notion');
});

// connections.js, recorded: every set() of the Notion row
function recordRows(win) {
  const marks = [];
  win.KwilConnections = { set: (id, info) => { marks.push([id, JSON.parse(JSON.stringify(info))]); return {}; } };
  return marks;
}

test("the Connections row: an invited person's status, never the owner's", async () => {
  const body = (over) => ({ status: 200, body: { enabled: true, connected: false, workspace: null,
                                                  database: null, ...over } });
  for (const [status, want] of [
    [body({}), { state: 'available', status: 'Not connected', action: 'Connect' }],
    [body({ connected: true, workspace: 'Tess <b>', database: 'D' }),
     { state: 'connected', status: 'Connected', action: 'Manage', detail: 'Tess <b>' }],
    [body({ connected: true, workspace: 'Tess', database: null }),
     { state: 'attention', status: 'Needs attention', action: 'Reconnect' }],
    [body({ enabled: false }), { state: 'off' }],
    [{ status: 404, body: {} }, { state: 'off' }]]) {
    const r = load({ status });
    const marks = recordRows(r.win);
    r.api.onState({ owner: false });
    await tick();
    assert.deepEqual(marks.at(-1), ['notion', want]);
  }
  // the owner's Notion row is connect.js's: notion.js never touches it
  const r = load();
  const marks = recordRows(r.win);
  r.api.onState({ owner: true });
  await tick();
  r.menu.open = true;
  await r.menu.fire('toggle');
  assert.deepEqual(marks, []);
});
