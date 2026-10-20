// node --test tests/js/devhub.test.mjs  (Node 18+; no packages needed)
//
// Runs the PC hub's page code (assistant/web/devteam.js) in node:vm with a
// minimal fake document, a fake fetch for /api/devteam/* and /api/login,
// fake timers and a fake localStorage, then checks the delivery stages,
// the presence pill, sending (Enter clears the box at once, a failure puts
// the draft back), text-only markdown, hide/restore/Show all with its
// storage, and that its markdown renderer matches the agent dashboard's.

import test from 'node:test';
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import vm from 'node:vm';

const HUB = readFileSync(new URL('../../assistant/web/devteam.js', import.meta.url), 'utf8');
const HUB_HTML = readFileSync(new URL('../../assistant/web/devteam.html', import.meta.url), 'utf8');
const DASH = readFileSync(new URL('../../tools/agent-dashboard/index.html', import.meta.url), 'utf8')
  .match(/<script>([\s\S]*?)<\/script>/)[1];

const SID = 'session_01DiUkqY2kzbUbDmW1w96rfi';
const SID2 = 'cse_0123456789abcdefABCDEF';
const KEY = 'heyClaudeHub.layout.v1';
const NOW = Date.parse('2026-10-20T15:30:00Z');

class FakeEl {
  constructor(tag) {
    this.tagName = tag.toUpperCase();
    this.children = [];
    this.ownText = '';
    this.dataset = {};
    this.className = '';
    this.hidden = false;
    this.attrs = {};
    this.listeners = {};
    this.value = '';
    this.disabled = false;
    this.focused = 0;
    this.scrollTop = 0;
    this.scrollHeight = 0;
    this.clientHeight = 0;
  }
  setAttribute(n, v) { this.attrs[n] = String(v); }
  getAttribute(n) { return Object.hasOwn(this.attrs, n) ? this.attrs[n] : null; }
  addEventListener(type, fn) { (this.listeners[type] ??= []).push(fn); }
  dispatch(type, extra = {}) {
    const ev = { type, target: this, preventDefault() { ev.defaultPrevented = true; }, ...extra };
    for (const fn of this.listeners[type] ?? []) fn(ev);
    return ev;
  }
  click() { this.dispatch('click'); }
  focus() { this.focused += 1; }
  getBoundingClientRect() { return { width: 1400, height: 800 }; }
  set textContent(v) { this.ownText = String(v); this.children = []; }
  get textContent() { return this.ownText + this.children.map((c) => c.textContent).join(''); }
  append(...nodes) {
    for (const n of nodes) {
      assert.ok(n instanceof FakeEl, 'only elements are appended');
      this.children.push(n);
    }
  }
  replaceChildren(...nodes) { this.ownText = ''; this.children = []; this.append(...nodes); }
  find(pred) {
    const out = [];
    const walk = (e) => { for (const c of e.children) { if (pred(c)) out.push(c); walk(c); } };
    walk(this);
    return out;
  }
}

function memStorage(initial = {}) {
  const store = { ...initial };
  return { store, getItem: (k) => (k in store ? store[k] : null), setItem: (k, v) => { store[k] = String(v); },
    removeItem: (k) => { delete store[k]; } };
}

const settle = async () => { for (let i = 0; i < 8; i += 1) await new Promise((r) => setImmediate(r)); };

function sandboxFor({ storage, fetch }) {
  const ids = {};
  const document = {
    hidden: false,
    getElementById: (id) => (ids[id] ??= new FakeEl('div')),
    createElement: (tag) => new FakeEl(tag),
    addEventListener: () => {},
  };
  const timers = [];
  const sandbox = {
    document, console, fetch,
    setTimeout: (fn, ms) => { timers.push({ fn, ms }); return timers.length; },
    clearTimeout: () => {},
    setInterval: () => 0,
    localStorage: storage,
    confirm: () => true,
    addEventListener: () => {},
  };
  sandbox.window = sandbox;
  return { sandbox, ids, timers };
}

// The server: windows from `state`, sends answered by `sendReply`.
function fakeServer(state) {
  const calls = [];
  const server = { state, calls, sendReply: null, status: 200 };
  server.fetch = async (path, opts = {}) => {
    const body = opts.body ? JSON.parse(opts.body) : undefined;
    calls.push({ path, body });
    let status = 200;
    let data = {};
    if (path === '/api/devteam/state') { status = server.status; data = server.state; }
    else if (path === '/api/devteam/send') [status, data] = server.sendReply ? await server.sendReply(body) : [200, { message: {} }];
    else if (path === '/api/devteam/clear') data = { ok: true };
    else if (path === '/api/login') status = body.token === 'good' ? 200 : 401;
    return { status, json: async () => data };
  };
  return server;
}

const win = (session, o = {}) => ({ session, url: `https://claude.ai/code/${session}`,
  presence: { state: 'idle', text: '', age: 30 }, queued: false, messages: [], ...o });

async function loadHub({ windows = [win(SID), win(SID2)], storage = memStorage(), server = null } = {}) {
  server ??= fakeServer({ windows, poll_seconds: 4, notion_error: '', max_text: 12000 });
  const { sandbox, ids, timers } = sandboxFor({ storage, fetch: server.fetch });
  const ctx = vm.createContext(sandbox);
  vm.runInContext(`Date.now = () => ${NOW};`, ctx);
  vm.runInContext(HUB, ctx, { filename: 'devteam.js' });
  await settle();
  return { api: sandbox.DevHub, ids, server, storage, timers, sandbox };
}

const tiles = (page) => page.ids.wins.children;
const tileOf = (page, sid) => tiles(page).find((t) => t.dataset.session === sid);
const byClass = (root, cls) => root.find((e) => e.className === cls);
const msgs = (t) => byClass(t, 'msg');
const textarea = (t) => t.find((e) => e.tagName === 'TEXTAREA')[0];
const pres = (t) => byClass(t, 'presence')[0];
const dline = (t) => byClass(t, 'dline')[0];

test('the page has no inline script and loads its own code and styles', () => {
  assert.match(HUB_HTML, /<script src="\/devteam\.js"><\/script>/);
  assert.doesNotMatch(HUB_HTML, /<script>|\sstyle="|\son[a-z]+="/);
  assert.match(HUB_HTML, /<link rel="stylesheet" href="\/devteam\.css">/);
  assert.doesNotMatch(HUB + HUB_HTML, /https?:\/\/(?!claude\.ai\/code)[a-z]/i, 'no CDN or other origin');
});

test('stages: sending, not sent, queued, working, blocked; nothing once answered', async () => {
  const { api } = await loadHub();
  assert.equal(api.stageLine({ stage: 'sending' }).text, 'Sending…');
  assert.deepEqual({ ...api.stageLine({ stage: 'not sent', error: 'The coordinator didn\'t get it: archived' }) },
    { stage: 'failed', text: 'Not sent: The coordinator didn\'t get it: archived' });
  assert.equal(api.stageLine({ stage: 'queued' }).stage, 'queued');
  assert.equal(api.stageLine({ stage: 'working', step: 'Reading your message' }).text, 'Coordinator · working: Reading your message');
  assert.equal(api.stageLine({ stage: 'blocked' }).stage, 'failed');
  assert.equal(api.stageLine({ stage: 'answered' }), null);
  assert.equal(api.stageLine({ stage: 'working', step: '<img src=x onerror=alert(1)>' }).text,
    'Coordinator · working: <img src=x onerror=alert(1)>', 'text, never markup');
});

test('the presence pill: in task, in task with no update, idle, unknown; a Queued badge', async () => {
  const { api } = await loadHub();
  assert.deepEqual({ ...api.presenceLine({ state: 'task', text: 'Judging round 2', age: 120 }) },
    { state: 'task', text: 'In task: Judging round 2 · 2m' });
  assert.deepEqual({ ...api.presenceLine({ state: 'maybe', age: 900 }) }, { state: 'maybe', text: 'In task (no update for 15m)' });
  assert.equal(api.presenceLine({ state: 'idle' }).text, 'Idle');
  assert.equal(api.presenceLine(null).text, 'Status unknown');
  const page = await loadHub({ windows: [win(SID, { presence: { state: 'task', text: 'Tests', age: 10 }, queued: true })] });
  const t = tileOf(page, SID);
  assert.equal(pres(t).dataset.state, 'task');
  assert.equal(pres(t).textContent, 'In task: Tests · just now');
  assert.equal(byClass(t, 'qbadge')[0].hidden, false);
});

test('Enter sends and clears the box at once; the stage line sits under Kyle\'s message', async () => {
  const server = fakeServer({ windows: [win(SID)], poll_seconds: 4 });
  let release;
  server.sendReply = (body) => new Promise((r) => { release = () => r([200, { message: { stage: 'queued' } }]); });
  const page = await loadHub({ server });
  const t = tileOf(page, SID);
  const box = textarea(t);
  box.value = 'build the hub';
  box.dispatch('keydown', { key: 'Enter', shiftKey: true });
  assert.equal(box.value, 'build the hub', 'Shift+Enter is a new line');
  box.dispatch('keydown', { key: 'Enter' });
  assert.equal(box.value, '', 'cleared before the server answers');
  assert.equal(msgs(t).at(-1).textContent, 'build the hub');
  assert.equal(dline(t).textContent, '✻Sending…');
  assert.deepEqual(server.calls.find((c) => c.path === '/api/devteam/send').body, { session: SID, text: 'build the hub' });
  // The server now has it, queued.
  server.state = { windows: [win(SID, { queued: true, messages: [{ from: 'kyle', text: 'build the hub', at: 'x', stage: 'queued' }] })] };
  release();
  await settle();
  assert.equal(dline(t).dataset.stage, 'queued');
  server.state = { windows: [win(SID, { messages: [
    { from: 'kyle', text: 'build the hub', at: 'x', stage: 'working', step: 'Reading your message' }] })] };
  await page.api.poll();
  assert.equal(dline(t).textContent, '✻Coordinator · working: Reading your message');
  server.state = { windows: [win(SID, { messages: [
    { from: 'kyle', text: 'build the hub', at: 'x', stage: 'answered' },
    { from: 'coordinator', text: '**Done.** PR is up.', at: 'y', status: 'answered' }] })] };
  await page.api.poll();
  assert.equal(t.find((e) => e.className === 'dline').length, 0, 'no line once answered');
  const reply = msgs(t).at(-1);
  assert.equal(reply.dataset.from, 'coordinator');
  assert.equal(reply.find((e) => e.tagName === 'STRONG')[0].textContent, 'Done.');
});

test('a failed send puts the draft back and says why; 409 means one at a time', async () => {
  const server = fakeServer({ windows: [win(SID)], poll_seconds: 4 });
  server.sendReply = async () => [502, { error: 'The coordinator didn\'t get it: not signed in' }];
  const page = await loadHub({ server });
  const t = tileOf(page, SID);
  textarea(t).value = 'hello';
  textarea(t).dispatch('keydown', { key: 'Enter' });
  await settle();
  assert.equal(textarea(t).value, 'hello');
  const hint = byClass(t, 'hint')[0];
  assert.equal(hint.hidden, false);
  assert.equal(hint.textContent, 'The coordinator didn\'t get it: not signed in');
  server.sendReply = async () => [409, { error: 'one message at a time' }];
  textarea(t).dispatch('keydown', { key: 'Enter' });
  await settle();
  assert.equal(hint.textContent, 'One message at a time: wait for the last one.');
  assert.equal(textarea(t).value, 'hello');
});

test('replies are text-only markdown with http(s) links only', async () => {
  const hostile = '<img src=x onerror=alert(1)> [x](javascript:alert(1)) [ok](https://github.com/x) <script>1</script>';
  const page = await loadHub({ windows: [win(SID, { messages: [{ from: 'coordinator', text: hostile, at: 'y' }] })] });
  const m = msgs(tileOf(page, SID))[0];
  assert.equal(m.find((e) => ['IMG', 'SCRIPT'].includes(e.tagName)).length, 0);
  const links = m.find((e) => e.tagName === 'A');
  assert.deepEqual(links.map((a) => a.href), ['https://github.com/x']);
  assert.equal(links[0].rel, 'noopener noreferrer');
  assert.match(m.textContent, /<img src=x onerror=alert\(1\)>/);
  // Kyle's own message is plain text, never markdown.
  const mine = await loadHub({ windows: [win(SID, { messages: [{ from: 'kyle', text: '**not bold**', at: 'x', stage: 'queued' }] })] });
  assert.equal(msgs(tileOf(mine, SID))[0].find((e) => e.tagName === 'STRONG').length, 0);
});

test('hide: the window leaves, Hidden (1) shows, {h:1} is saved, and it stays hidden after a reload', async () => {
  const storage = memStorage();
  const page = await loadHub({ storage });
  const t = tileOf(page, SID2);
  const hide = byClass(t, 'hide')[0];
  assert.equal(hide.getAttribute('aria-label'), 'Hide Coordinator 01234567');
  hide.click();
  assert.deepEqual(tiles(page).map((x) => x.dataset.session), [SID]);
  assert.equal(page.ids.tray.hidden, false);
  assert.equal(page.ids['tray-sum'].textContent, 'Hidden (1)');
  assert.equal(page.ids.count.textContent, '1 window · 1 hidden');
  assert.deepEqual(JSON.parse(storage.store[KEY]), { v: 1, tiles: { [SID2]: { h: 1 } } });
  const again = await loadHub({ storage });
  assert.deepEqual(tiles(again).map((x) => x.dataset.session), [SID]);
  assert.deepEqual([...again.api.hiddenIds()], [SID2]);
  assert.deepEqual(Object.keys(storage.store), [KEY], 'only the hub key');
});

test('restore and Show all; the hidden window keeps its draft and gets its replies', async () => {
  const storage = memStorage();
  const page = await loadHub({ storage });
  const t = tileOf(page, SID);
  textarea(t).value = 'half typed';
  byClass(t, 'hide')[0].click();
  byClass(tileOf(page, SID2), 'hide')[0].click();
  assert.equal(page.ids['tray-sum'].textContent, 'Hidden (2)');
  // A reply arrives while hidden: polling goes on, the tray marks it.
  page.server.state = { windows: [win(SID, { messages: [{ from: 'coordinator', text: 'Answered.', at: 'y' }] }), win(SID2)], poll_seconds: 4 };
  await page.api.poll();
  const item = page.ids['tray-list'].children[0];
  assert.match(item.textContent, /new reply$/);
  item.click();
  const back = tileOf(page, SID);
  assert.equal(back, t, 'the same window');
  assert.equal(textarea(back).value, 'half typed');
  assert.equal(msgs(back)[0].textContent, 'Answered.');
  assert.ok(textarea(back).focused > 0);
  assert.deepEqual(JSON.parse(storage.store[KEY]).tiles, { [SID2]: { h: 1 } });
  page.ids.showall.click();
  assert.equal(tiles(page).length, 2);
  assert.equal(page.ids.tray.hidden, true);
  assert.deepEqual(JSON.parse(storage.store[KEY]).tiles, {});
});

test('a blocked hidden window is marked blocked in the tray; hiding the maximized one restores the grid', async () => {
  const page = await loadHub({ windows: [win(SID, { messages: [{ from: 'kyle', text: 'x', at: 'a', stage: 'blocked' }] }), win(SID2)] });
  const t = tileOf(page, SID);
  byClass(t, 'max')[0].click();
  assert.ok('max' in page.ids.wins.dataset);
  byClass(t, 'hide')[0].click();
  assert.equal('max' in page.ids.wins.dataset, false);
  assert.match(page.ids['tray-list'].children[0].textContent, /blocked$/);
});

test('hidden flags are capped at 8, pruned to the server\'s windows, and junk is ignored', async () => {
  const many = {};
  for (let i = 0; i < 12; i += 1) many[`session_x${i}`] = { h: 1 };
  many[SID] = { h: 1 };
  const capped = await loadHub({ storage: memStorage({ [KEY]: JSON.stringify({ v: 1, tiles: many }) }) });
  assert.equal(tiles(capped).length, 2, 'SID is past the cap of 8');
  const storage = memStorage({ [KEY]: JSON.stringify({ v: 1, tiles: { gone_1: { h: 1 }, [SID]: { h: true }, [SID2]: { h: 1 } } }) });
  const page = await loadHub({ storage });
  assert.deepEqual(tiles(page).map((x) => x.dataset.session), [SID]);
  byClass(tileOf(page, SID), 'hide')[0].click();
  assert.deepEqual(JSON.parse(storage.store[KEY]).tiles, { [SID]: { h: 1 }, [SID2]: { h: 1 } });
  for (const bad of ['{', '[]', JSON.stringify({ v: 2, tiles: { [SID]: { h: 1 } } }), 'x'.repeat(20000)]) {
    const p = await loadHub({ storage: memStorage({ [KEY]: bad }) });
    assert.equal(tiles(p).length, 2, bad.slice(0, 20));
  }
});

test('signed out: the page asks for the token and signs in through /api/login', async () => {
  const server = fakeServer({ windows: [win(SID)], poll_seconds: 4 });
  server.status = 401;
  const page = await loadHub({ server });
  assert.equal(page.ids.login.hidden, false);
  page.ids.token.value = 'bad';
  page.ids['login-go'].click();
  await settle();
  assert.equal(page.ids['login-hint'].textContent, 'That token didn\'t work.');
  server.status = 200;
  page.ids.token.value = 'good';
  page.ids['login-go'].click();
  await settle();
  assert.equal(page.ids.login.hidden, true);
  assert.equal(page.ids.token.value, '');
  assert.equal(tiles(page).length, 1);
  assert.ok(server.calls.every((c) => !c.path.includes('good')), 'the token never goes in a URL');
});

// ---- parity: the hub's markdown is the dashboard's ----

function serialize(e) {
  const attrs = Object.entries(e.attrs).sort().map(([k, v]) => `${k}=${v}`);
  const extra = [e.className, e.href ?? '', e.target ?? '', e.rel ?? '', JSON.stringify(e.dataset), ...attrs].join('|');
  return `<${e.tagName}${extra}>${e.ownText}${e.children.map(serialize).join('')}</${e.tagName}>`;
}

async function dashboardRenderer() {
  const ids = {};
  const sandbox = {
    document: { hidden: false, getElementById: (id) => (ids[id] ??= new FakeEl('div')),
      createElement: (tag) => new FakeEl(tag), addEventListener: () => {} },
    console, setTimeout: () => 0, clearTimeout: () => {}, setInterval: () => 0, addEventListener: () => {},
    localStorage: memStorage(),
  };
  sandbox.window = sandbox;
  vm.runInContext(DASH, vm.createContext(sandbox), { filename: 'index.html' });
  await settle();
  return sandbox.AgentDashboard.renderMarkdown;
}

const CORPUS = [
  '## Heading\nText with **bold**, *em*, _em_ and `code`.',
  '- one\n  - nested\n    - deeper\n1. first\n2. second\n\n3) third',
  '```python\nprint("hi")\n```\n> quote\n> > nested quote',
  '| a | b |\n|---|:-:|\n| 1 | 2 \\| pipe |\n| only |',
  '[link](https://github.com/x) [bad](javascript:alert(1)) [rel](/x) <b>raw</b>',
  '***\n---\nplain\nlines\n\n\\*escaped\\*',
  '*'.repeat(5000) + '['.repeat(3000) + '`'.repeat(2000),
  Array.from({ length: 2500 }, (_, i) => `- item ${i}`).join('\n'),
  'x'.repeat(13000),
  '',
];

test('renderMarkdown matches the agent dashboard\'s on a shared corpus', async () => {
  const dash = await dashboardRenderer();
  const { api } = await loadHub();
  for (const src of CORPUS) {
    assert.equal(serialize(api.renderMarkdown(src)), serialize(dash(src)), src.slice(0, 40));
  }
});
