// node --test tests/js/spotify.test.mjs  (Node 18+; no packages needed)
//
// Runs the phone page's kwil/web/spotify.js in node:vm with a minimal
// fake window: "Connect Spotify" shows only for the owner and only when the
// server says it is set up; a family member's page never even asks; a tap
// POSTs once and goes only to Spotify's own consent page; and i18n.js has
// the Chinese for every string it shows.

import test from 'node:test';
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import vm from 'node:vm';

const SCRIPT = readFileSync(new URL('../../kwil/web/spotify.js', import.meta.url), 'utf8');
const I18N = readFileSync(new URL('../../kwil/web/i18n.js', import.meta.url), 'utf8');

class El {
  constructor(id, hidden = false) {
    this.id = id; this.hidden = hidden; this.textContent = ''; this.disabled = false;
    this.listeners = {}; this.open = false;
  }
  addEventListener(type, fn) { (this.listeners[type] ??= []).push(fn); }
  async fire(type) { for (const fn of this.listeners[type] || []) await fn({ preventDefault() {} }); }
}

function load({ status = { status: 200, body: { enabled: true, connected: false } },
                connect = { status: 200, body: { url: 'https://accounts.spotify.com/authorize?x=1' } } } = {}) {
  const els = { 'spotify-connect': new El('spotify-connect', true),
                'spotify-status': new El('spotify-status', true) };
  const menu = new El('menu');
  const fetches = [];
  const win = {
    location: { href: '/' },
    fetch: async (url, opts = {}) => {
      fetches.push({ url, opts });
      const r = url === '/api/spotify/status' ? status : url === '/api/spotify/connect' ? connect
        : { status: 404, body: {} };
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
  return { win, els, menu, fetches, api: win.KwilSpotify };
}

const tick = () => new Promise((r) => setTimeout(r, 0));

test('a family member never sees or asks for Spotify', async () => {
  const { api, els, fetches, menu } = load();
  api.onState({ owner: false });
  await tick();
  menu.open = true;
  await menu.fire('toggle');
  assert.equal(els['spotify-connect'].hidden, true);
  assert.deepEqual(fetches.map((f) => f.url), []);
});

test('the owner sees it when the server has it set up', async () => {
  const { api, els, fetches } = load();
  api.onState({ owner: true });
  await tick();
  assert.equal(els['spotify-connect'].hidden, false);
  assert.equal(els['spotify-connect'].textContent, 'Connect Spotify');
  assert.equal(els['spotify-status'].hidden, true);
  assert.equal(fetches[0].opts.credentials, 'same-origin');
});

test('connected: "Reconnect Spotify" and a note', async () => {
  const { api, els } = load({ status: { status: 200, body: { enabled: true, connected: true } } });
  api.onState({ owner: true });
  await tick();
  assert.equal(els['spotify-connect'].textContent, 'Reconnect Spotify');
  assert.equal(els['spotify-status'].hidden, false);
  assert.equal(els['spotify-status'].textContent, 'Spotify connected.');
});

test('not set up (or 404): stays hidden', async () => {
  for (const status of [{ status: 200, body: { enabled: false, connected: false } },
                        { status: 404, body: {} }]) {
    const { api, els } = load({ status });
    api.onState({ owner: true });
    await tick();
    assert.equal(els['spotify-connect'].hidden, true);
  }
});

test('a tap POSTs once and opens Spotify\'s consent page', async () => {
  const { api, els, fetches, win } = load();
  api.onState({ owner: true });
  await tick();
  await els['spotify-connect'].fire('click');
  const posts = fetches.filter((f) => f.url === '/api/spotify/connect');
  assert.equal(posts.length, 1);
  assert.equal(posts[0].opts.method, 'POST');
  assert.equal(win.location.href, 'https://accounts.spotify.com/authorize?x=1');
});

test('any other address is refused', async () => {
  const { api, els, win } = load({ connect: { status: 200, body: { url: 'https://evil.example/x' } } });
  api.onState({ owner: true });
  await tick();
  await els['spotify-connect'].fire('click');
  assert.equal(win.location.href, '/');
  assert.equal(els['spotify-connect'].disabled, false);
  assert.equal(els['spotify-status'].textContent, "Couldn't start the Spotify sign-in.");
});

test('every string it shows has Chinese in i18n.js', () => {
  const ctx = {};
  vm.createContext(ctx);
  vm.runInContext(I18N, ctx);
  const { ZH } = ctx.KwilI18n;
  for (const s of ['Connect Spotify', 'Reconnect Spotify', 'Spotify connected.',
                   'Opening Spotify…', "Couldn't start the Spotify sign-in."]) {
    assert.ok(ZH[s], s);
    assert.match(SCRIPT, new RegExp(s.replace(/[.*+?^${}()|[\]\\]/g, '\\$&')));
  }
  assert.equal(ZH['Connect Spotify'], '连接 Spotify');
});

test("the Connections row: the owner's status; off for anyone else or when not set up", async () => {
  const cases = [
    [true, { status: 200, body: { enabled: true, connected: false } },
     { state: 'available', status: 'Not connected', action: 'Connect' }],
    [true, { status: 200, body: { enabled: true, connected: true } },
     { state: 'connected', status: 'Connected', action: 'Manage' }],
    [true, { status: 200, body: { enabled: false, connected: false } }, { state: 'off' }],
    [true, { status: 404, body: {} }, { state: 'off' }],
    [false, undefined, { state: 'off' }]];
  for (const [owner, status, want] of cases) {
    const r = load(status ? { status } : {});
    const marks = [];
    r.win.KwilConnections = { set: (id, info) => { marks.push([id, JSON.parse(JSON.stringify(info))]); } };
    r.api.onState({ owner });
    await tick();
    assert.deepEqual(marks.at(-1), ['spotify', want]);
  }
});
