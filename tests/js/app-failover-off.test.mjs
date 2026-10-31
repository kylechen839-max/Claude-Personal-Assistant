// node --test tests/js/app-failover-off.test.mjs  (Node 18+; no packages needed)
//
// Kwil is cloud-only (2026-10-31). Runs the phone page's kwil/web/app.js in
// node:vm with a small fake DOM, fetch() and localStorage, and checks the
// start-up failover check: a cached failover=true from an old Pi/PC setup
// is never trusted when /healthz can't be reached (the router module is
// never imported, the Backends section stays hidden) and the old failover
// keys are cleared; /healthz without a role clears them too; only a server
// that says it has a role still loads the router.

import test from 'node:test';
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import vm from 'node:vm';

const PATH = new URL('../../kwil/web/app.js', import.meta.url);
const SOURCE = readFileSync(PATH, 'utf8');
// node:vm can't hook a dynamic import() without a flag: the page's one
// import of the router goes through a recorder instead
const IMPORT = 'import("/backend-router.js")';
const SCRIPT = SOURCE.replace(IMPORT, '__import("/backend-router.js")');
const KEYS = ['failover', 'backendsCache', 'backendSettings', 'backendOverride'];

class El {
  constructor(id = '') {
    this.id = id; this.hidden = true; this.disabled = false; this.checked = false;
    this.value = ''; this.placeholder = ''; this.textContent = ''; this.className = '';
    this.dataset = {}; this.children = []; this.listeners = {};
    this.scrollTop = 0; this.scrollHeight = 0;
    this.classList = { add() {}, remove() {}, toggle() {}, contains: () => false };
  }
  get lastElementChild() { return this.children.at(-1) ?? null; }
  appendChild(c) { this.children.push(c); return c; }
  addEventListener(type, fn) { (this.listeners[type] ??= []).push(fn); }
  setAttribute(k, v) { (this.attrs ??= {})[k] = String(v); }
}

const json = (status, body) => ({ ok: status < 400, status, json: async () => body });

async function load(healthz) {
  const byId = {};
  const $ = (id) => (byId[id] ??= new El(id));
  const store = new Map([
    ['failover', 'true'],  // what an old failover setup cached
    ['backendsCache', JSON.stringify([{ role: 'pi', url: 'https://pi.tail1234.ts.net' }])],
    ['backendSettings', JSON.stringify({ pc: 'https://pc.tail1234.ts.net' })],
    ['backendOverride', JSON.stringify('pi')],
    ['other', 'kept'],
  ]);
  const imports = [];
  const win = {
    document: {
      getElementById: $,
      createElement: () => new El(),
      addEventListener() {},
      querySelector: () => null,
      body: new El('body'),
      visibilityState: 'visible',
    },
    Audio: class { play() { return Promise.resolve(); } pause() {} },
    localStorage: {
      getItem: (k) => (store.has(k) ? store.get(k) : null),
      setItem: (k, v) => { store.set(k, String(v)); },
      removeItem: (k) => { store.delete(k); },
    },
    navigator: {},
    location: { hash: '', pathname: '/', origin: 'https://heykwil.com' },
    history: { replaceState() {} },
    FormData, Blob, TextDecoder, setTimeout, clearTimeout, console,
    addEventListener() {},
    fetch: async (url) => {
      if (url === '/healthz') return healthz();
      if (url === '/api/state') return json(200, { busy: false, confirm: null });
      return json(404, {});
    },
  };
  win.window = win;
  vm.createContext(win);
  win.__import = async (specifier) => {  // records it, never loads it
    imports.push(specifier);
    throw new Error('not in this test');
  };
  vm.runInContext(SCRIPT, win);
  for (let i = 0; i < 10; i++) await new Promise((r) => setImmediate(r));
  return { store, imports, $ };
}

test('the page imports the router in exactly one place', () => {
  assert.equal(SOURCE.split(IMPORT).length, 2);
  assert.equal(SOURCE.split('import(').length, 2);
});

test('a cached failover flag is not trusted when /healthz is unreachable', async () => {
  const p = await load(async () => { throw new TypeError('Failed to fetch'); });
  assert.deepEqual(p.imports, []);  // the router is never loaded
  for (const key of KEYS) assert.equal(p.store.has(key), false, key);
  assert.equal(p.store.get('other'), 'kept');  // only the failover keys go
  assert.equal(p.$('backends').hidden, true);
  assert.equal(p.$('badge').hidden, true);
});

test('/healthz without a role (the cloud) clears the old failover keys', async () => {
  const p = await load(async () => json(200, { ok: true, version: '1' }));
  assert.deepEqual(p.imports, []);
  for (const key of KEYS) assert.equal(p.store.has(key), false, key);
  assert.equal(p.store.get('other'), 'kept');
});

test('a server that still reports a role loads the router (the harness sees imports)', async () => {
  const p = await load(async () => json(200, { ok: true, version: '1', api: 1, role: 'pc' }));
  assert.deepEqual(p.imports, ['/backend-router.js']);
  assert.equal(p.store.get('failover'), 'true');
});
