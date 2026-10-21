// node --test tests/js/family.test.mjs  (Node 18+; no packages needed)
//
// Runs the phone page's assistant/web/family.js in node:vm with a minimal
// fake window: an "#invite=" link is read, cleared from the address bar
// before anything is sent, POSTed once as {invite}, and remembered as a
// family account (so app.js skips the failover router); a failed invite
// shows a message; the owner's "Invite someone" box shows only for the
// owner with family on, and a new link goes to the share sheet or, without
// one, the clipboard.

import test from 'node:test';
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import vm from 'node:vm';

const SCRIPT = readFileSync(new URL('../../assistant/web/family.js', import.meta.url), 'utf8');

class El {
  constructor(id, hidden = false) {
    this.id = id; this.hidden = hidden; this.value = ''; this.textContent = '';
    this.listeners = {}; this.children = []; this.dataset = {}; this.open = false;
  }
  addEventListener(type, fn) { (this.listeners[type] ??= []).push(fn); }
  appendChild(c) { this.children.push(c); return c; }
  append(...cs) { this.children.push(...cs); }
  async fire(type, ev = {}) {
    for (const fn of this.listeners[type] || []) await fn({ preventDefault() {}, ...ev });
  }
}

function load({ hash = '', responses = {}, share, clipboard = true, store = {} } = {}) {
  const ids = ['login-error', 'app', 'family', 'invite-open', 'invite-form', 'invite-name',
    'invite-language', 'invite-tz', 'invite-status', 'invite-link', 'family-list'];
  const els = Object.fromEntries(ids.map((id) => [id, new El(id, ['family', 'invite-form', 'invite-link'].includes(id))]));
  els['invite-language'].value = 'zh';
  const menu = new El('menu');
  const fetches = [];
  const events = [];
  const copied = [];
  const shared = [];
  const local = { ...store };
  const win = {
    location: {
      hash, pathname: '/',
      replace: (url) => events.push(['replace', url]),
    },
    history: { replaceState: (s, t, url) => { events.push(['replaceState', url]); win.location.hash = ''; } },
    localStorage: {
      getItem: (k) => (k in local ? local[k] : null),
      setItem: (k, v) => { local[k] = String(v); },
      removeItem: (k) => { delete local[k]; },
    },
    navigator: {
      ...(share ? { share: async (data) => { shared.push(data); if (share !== true) throw share; } } : {}),
      clipboard: { writeText: async (t) => { if (!clipboard) throw new Error('no'); copied.push(t); } },
    },
    fetch: async (url, opts = {}) => {
      fetches.push({ url, opts, hashAtSend: win.location.hash });
      const r = responses[url] ?? { status: 404, body: {} };
      return { ok: r.status < 300, status: r.status, json: async () => r.body };
    },
    confirm: () => true,
    Intl,
    document: {
      getElementById: (id) => els[id] ?? null,
      querySelector: (sel) => (sel === 'details.menu' ? menu : null),
      createElement: (tag) => new El(tag),
    },
  };
  win.window = win;
  vm.createContext(win);
  vm.runInContext(SCRIPT, win);
  return { win, els, fetches, events, local, copied, shared, api: win.HeyClaudeFamily };
}

const TOKEN = 'hcinv.' + 'Ab-_9'.repeat(8);

test('reads the invite token from the fragment only', () => {
  const { api } = load();
  assert.equal(api.readInvite({ hash: `#invite=${TOKEN}` }), TOKEN);
  assert.equal(api.readInvite({ hash: `#x=1&invite=${TOKEN}` }), TOKEN);
  assert.equal(api.readInvite({ hash: '#token=abc' }), null);
  assert.equal(api.readInvite({ hash: '' }), null);
});

test('an invite is cleared from the address bar, then POSTed once', async () => {
  const t = load({ hash: `#invite=${TOKEN}`,
                   responses: { '/api/invite/accept': { status: 200, body: { ok: true } } } });
  assert.equal(await t.api.acceptInvite(t.win), true);
  assert.deepEqual(t.events[0], ['replaceState', '/']);
  assert.equal(t.fetches.length, 1);
  const f = t.fetches[0];
  assert.equal(f.url, '/api/invite/accept');
  assert.equal(f.hashAtSend, '');  // cleared before the request went out
  assert.equal(f.opts.method, 'POST');
  assert.equal(f.opts.credentials, 'same-origin');
  assert.deepEqual(JSON.parse(f.opts.body), { invite: TOKEN });
  assert.equal(t.local.account, 'family');  // app.js then skips failover
  assert.equal(t.api.isFamilyAccount(t.win), true);
  assert.deepEqual(t.events.at(-1), ['replace', '/']);
});

test('a used or expired invite shows a message and signs nobody in', async () => {
  const t = load({ hash: `#invite=${TOKEN}`,
                   responses: { '/api/invite/accept': { status: 401, body: {} } } });
  assert.equal(await t.api.acceptInvite(t.win), false);
  assert.match(t.els['login-error'].textContent, /expired or was already used/);
  assert.equal(t.local.account, undefined);
  assert.ok(!t.events.some((e) => e[0] === 'replace'));
});

test('no invite: nothing is sent', async () => {
  const t = load({ hash: '#token=abc' });
  assert.equal(await t.api.acceptInvite(t.win), false);
  assert.equal(t.fetches.length, 0);
  assert.equal(t.events.length, 0);
});

test('the invite box shows only for the owner with family on', () => {
  const t = load({ store: { account: 'family' } });
  t.api.onState({ owner: false, family: false }, t.win, t.win.document);
  assert.equal(t.els.family.hidden, true);
  assert.equal(t.els.app.dataset.account, 'family');
  assert.equal(t.local.account, 'family');
  t.api.onState({ owner: true, family: false }, t.win, t.win.document);
  assert.equal(t.els.family.hidden, true);
  assert.equal(t.local.account, undefined);  // the owner signed in here: failover back on
  t.api.onState({ owner: true, family: true }, t.win, t.win.document);
  assert.equal(t.els.family.hidden, false);
  assert.equal(t.els.app.dataset.account, 'owner');
});

test('a new invite is shared, or copied without a share sheet', async () => {
  const url = `https://x.example/#invite=${TOKEN}`;
  const responses = {
    '/api/invites': { status: 200, body: { url, expires_at: 1 } },
    '/api/users': { status: 200, body: { users: [] } },
  };
  for (const [share, expect] of [[true, 'shared'], [undefined, 'copied']]) {
    const t = load({ responses, share });
    t.api.onState({ owner: true, family: true }, t.win, t.win.document);
    t.els['invite-name'].value = ' Mom ';
    await t.els['invite-form'].fire('submit');
    const post = t.fetches.find((f) => f.url === '/api/invites');
    assert.deepEqual(JSON.parse(post.opts.body).name, 'Mom');
    assert.equal(JSON.parse(post.opts.body).language, 'zh');
    assert.equal(t.els['invite-link'].value, url);
    if (expect === 'shared') assert.equal(JSON.stringify(t.shared), JSON.stringify([{ url }]));
    else assert.deepEqual(t.copied, [url]);
  }
});

test('with failover the owner\'s calls go through the router with the bearer token', async () => {
  const { BackendRouter } = await import('../../assistant/web/backend-router.js');
  const cloud = 'https://cloud.example.com';
  const url = `${cloud}/#invite=${TOKEN}`;  // built by the server from its own address
  const sent = [];
  const router = new BackendRouter({
    backends: [{ role: 'cloud', url: cloud }],
    getToken: async () => 'hc1.app.sid.9999999999.1.sig',
    fetch: async (u, opts) => {
      sent.push({ u, opts });
      const body = u.endsWith('/api/invites') ? { url, expires_at: 1 } : { users: [] };
      return { ok: true, status: 200, json: async () => body };
    },
  });
  router.active = 'cloud';
  const t = load({ responses: {} });
  t.api.configure({ request: (path, opts) => router.request(path, opts) });
  t.api.onState({ owner: true, family: true }, t.win, t.win.document);
  t.els['invite-name'].value = 'Mom';
  await t.els['invite-form'].fire('submit');
  assert.equal(t.fetches.length, 0);  // no plain same-origin fetch (it has no token)
  const post = sent.find((s) => s.u === `${cloud}/api/invites`);
  assert.ok(post, JSON.stringify(sent.map((s) => s.u)));
  assert.equal(post.opts.method, 'POST');
  assert.equal(post.opts.headers.Authorization, 'Bearer hc1.app.sid.9999999999.1.sig');
  assert.equal(post.opts.credentials, 'omit');
  assert.ok(sent.some((s) => s.u === `${cloud}/api/users`));
  assert.equal(t.els['invite-link'].value, url);
});

test('remove uses POST .../remove (a cross-origin DELETE fails the failover preflight)', async () => {
  const user = { id: 'u 1', name: 'Mom', status: 'active' };
  const t = load({ responses: {
    '/api/users': { status: 200, body: { users: [user] } },
    '/api/users/u%201/remove': { status: 200, body: { ok: true } },
  } });
  t.api.onState({ owner: true, family: true }, t.win, t.win.document);
  await new Promise((r) => setTimeout(r, 0));
  const li = t.els['family-list'].children[0];
  assert.ok(li, 'the list shows Mom');
  const remove = li.children.find((c) => c && c.className === 'danger');
  await remove.fire('click');
  const sent = t.fetches.find((f) => f.url === '/api/users/u%201/remove');
  assert.ok(sent, JSON.stringify(t.fetches.map((f) => f.url)));
  assert.equal(sent.opts.method, 'POST');
  assert.ok(!t.fetches.some((f) => f.opts.method === 'DELETE'));
});

test('app.js hands its router-aware request() to family.js before anything else', () => {
  const app = readFileSync(new URL('../../assistant/web/app.js', import.meta.url), 'utf8');
  const configured = app.indexOf('family.configure({ request })');
  assert.ok(configured > 0);
  assert.ok(configured < app.indexOf('family.acceptInvite(window)'));
});

test('an invite sends the chosen tier, beta by default; the list marks beta people', async () => {
  const url = `https://x.example/#invite=${TOKEN}`;
  const responses = {
    '/api/invites': { status: 200, body: { url, expires_at: 1 } },
    '/api/users': { status: 200, body: { users: [
      { id: 'u_aaaaaaaa1', name: 'Tester', status: 'active', tier: 'beta' },
      { id: 'u_bbbbbbbb1', name: 'Mom', status: 'invited', tier: 'family' }] } },
  };
  const plain = load({ responses });  // no tier select (an older page): beta
  plain.api.onState({ owner: true, family: true }, plain.win, plain.win.document);
  plain.els['invite-name'].value = 'Tester';
  await plain.els['invite-form'].fire('submit');
  assert.equal(JSON.parse(plain.fetches.find((f) => f.url === '/api/invites').opts.body).tier, 'beta');
  const states = plain.els['family-list'].children.map((li) => li.children[2].textContent);
  assert.deepEqual(states, ['Active · Beta', 'Invited']);

  const t = load({ responses });
  t.els['invite-tier'] = new El('invite-tier');
  t.els['invite-tier'].value = 'family';
  t.api.onState({ owner: true, family: true }, t.win, t.win.document);
  t.els['invite-name'].value = 'Mom';
  await t.els['invite-form'].fire('submit');
  assert.equal(JSON.parse(t.fetches.find((f) => f.url === '/api/invites').opts.body).tier, 'family');
});

// --- a family member's own Anthropic key (Settings) ---
function loadKey({ status = { set: false, last4: null, unreadable: false, required: true,
                              available: true }, put = { status: 200, body: { set: true, last4: 'wxyz' } },
                   confirmAnswer = true } = {}) {
  const ids = ['app', 'api-key', 'api-key-form', 'api-key-input', 'api-key-save', 'api-key-remove',
    'api-key-state', 'api-key-cost'];
  const els = Object.fromEntries(ids.map((id) => [id, new El(id, ['api-key', 'api-key-remove', 'api-key-cost'].includes(id))]));
  const menu = new El('menu');
  const fetches = [];
  const asked = [];
  let current = { ...status };
  const win = {
    location: { hash: '', pathname: '/' },
    history: { replaceState() {} },
    localStorage: { getItem: () => null, setItem() {}, removeItem() {} },
    navigator: {},
    fetch: async (url, opts = {}) => {
      const method = opts.method || 'GET';
      fetches.push({ url, method, body: opts.body });
      let r = { status: 200, body: current };
      if (method === 'PUT') {
        r = put;
        if (put.status < 300) current = { ...current, set: true, last4: put.body.last4, month_usd: 0 };
      } else if (method === 'DELETE') {
        current = { ...current, set: false, last4: null };
        r = { status: 200, body: { ok: true, deleted: true } };
      }
      return { ok: r.status < 300, status: r.status, json: async () => r.body };
    },
    confirm: (q) => { asked.push(q); return confirmAnswer; },
    Intl,
    document: {
      getElementById: (id) => els[id] ?? null,
      querySelector: (sel) => (sel === 'details.menu' ? menu : null),
      createElement: (tag) => new El(tag),
    },
  };
  win.window = win;
  vm.createContext(win);
  vm.runInContext(SCRIPT, win);
  const flush = () => new Promise((r) => setTimeout(r, 0));
  return { win, els, fetches, asked, flush, api: win.HeyClaudeFamily };
}

test('the key settings show only for a family member or beta tester, never the owner', async () => {
  const t = loadKey();
  t.api.onState({ owner: true, family: true }, t.win, t.win.document);
  await t.flush();
  assert.equal(t.els['api-key'].hidden, true);
  assert.equal(t.fetches.filter((f) => f.url === '/api/me/anthropic-key').length, 0);
  t.api.onState({ owner: false, api_key: 'required' }, t.win, t.win.document);
  await t.flush();
  assert.equal(t.els['api-key'].hidden, false);
  assert.equal(t.els['api-key-state'].textContent, 'Add your key to start talking with Claude.');
  assert.equal(t.els['api-key-save'].textContent, 'Save key');
  assert.equal(t.els['api-key-remove'].hidden, true);
  // another /api/state with the same mode doesn't ask again
  t.api.onState({ owner: false, api_key: 'required' }, t.win, t.win.document);
  await t.flush();
  assert.equal(t.fetches.length, 1);
  const fam = loadKey({ status: { set: false, required: false, available: true } });
  fam.api.onState({ owner: false, api_key: 'optional' }, fam.win, fam.win.document);
  await fam.flush();
  assert.equal(fam.els['api-key-state'].textContent, 'Optional: without one, the family\'s key is used.');
});

test('saving a key PUTs it once, clears the box and shows only the last four', async () => {
  const t = loadKey();
  t.api.onState({ owner: false, api_key: 'required' }, t.win, t.win.document);
  await t.flush();
  const key = 'sk-ant-api03-' + 'A'.repeat(40) + 'wxyz';
  t.els['api-key-input'].value = `  ${key} `;
  await t.els['api-key-form'].fire('submit');
  const puts = t.fetches.filter((f) => f.method === 'PUT');
  assert.equal(puts.length, 1);
  assert.equal(JSON.parse(puts[0].body).key, key);
  assert.equal(t.els['api-key-input'].value, '');
  assert.equal(t.els['api-key-state'].textContent, 'Your key ending in wxyz is saved.');
  assert.equal(t.els['api-key-save'].textContent, 'Replace key');
  assert.equal(t.els['api-key-remove'].hidden, false);
  assert.equal(t.els['api-key-cost'].textContent, 'About $0.00 this month on your key.');
  assert.ok(!JSON.stringify(t.els).includes('A'.repeat(40)));  // nowhere on the page
  // an empty box sends nothing
  await t.els['api-key-form'].fire('submit');
  assert.equal(t.fetches.filter((f) => f.method === 'PUT').length, 1);
});

test('a refused key shows the server\'s reason', async () => {
  const t = loadKey({ put: { status: 400, body: { error: "Anthropic didn't accept that key" } } });
  t.api.onState({ owner: false, api_key: 'required' }, t.win, t.win.document);
  await t.flush();
  t.els['api-key-input'].value = 'sk-ant-api03-' + 'x'.repeat(40);
  await t.els['api-key-form'].fire('submit');
  assert.equal(t.els['api-key-state'].textContent, "Anthropic didn't accept that key");
  assert.equal(t.els['api-key-save'].disabled, false);
});

test('remove asks first, then DELETEs; a key that no longer opens asks for it again', async () => {
  const t = loadKey({ status: { set: true, last4: 'wxyz', required: true, available: true } });
  t.api.onState({ owner: false, api_key: 'required' }, t.win, t.win.document);
  await t.flush();
  await t.els['api-key-remove'].fire('click');
  assert.equal(t.asked.length, 1);
  assert.equal(t.asked[0], 'Remove your Anthropic API key from this app?');
  assert.equal(t.fetches.filter((f) => f.method === 'DELETE').length, 1);
  assert.equal(t.els['api-key-remove'].hidden, true);
  const no = loadKey({ status: { set: true, last4: 'wxyz', required: true }, confirmAnswer: false });
  no.api.onState({ owner: false, api_key: 'required' }, no.win, no.win.document);
  await no.flush();
  await no.els['api-key-remove'].fire('click');
  assert.equal(no.fetches.filter((f) => f.method === 'DELETE').length, 0);
  const stale = loadKey({ status: { set: false, unreadable: true, required: true, available: true } });
  stale.api.onState({ owner: false, api_key: 'required' }, stale.win, stale.win.document);
  await stale.flush();
  assert.equal(stale.els['api-key-state'].textContent,
               "Your saved key can't be read any more. Please enter it again.");
  const off = loadKey({ status: { set: false, required: true, available: false } });
  off.api.onState({ owner: false, api_key: 'required' }, off.win, off.win.document);
  await off.flush();
  assert.equal(off.els['api-key-state'].textContent, "This server can't store keys yet.");
});
