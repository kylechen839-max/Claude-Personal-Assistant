// node --test tests/js/app-send.test.mjs  (Node 18+; no packages needed)
//
// Runs the phone page's assistant/web/app.js in node:vm with a small fake
// DOM, fetch() and a fake attachments tray (attachments.js), and checks the
// text box's submit path: picked files go with a new message as /api/turn
// "attachment" parts, the chips are cleared only once the server took the
// turn (a refused upload keeps them for a retry), and a yes/no answer to a
// confirmation never carries files (typed or tapped).

import test from 'node:test';
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import vm from 'node:vm';

const SCRIPT = readFileSync(new URL('../../assistant/web/app.js', import.meta.url), 'utf8');

class El {
  constructor(id = '') {
    this.id = id; this.hidden = false; this.disabled = false; this.checked = false;
    this.value = ''; this.placeholder = ''; this.textContent = ''; this.className = '';
    this.dataset = {}; this.children = []; this.listeners = {};
    this.scrollTop = 0; this.scrollHeight = 0;
    this.classList = { add() {}, remove() {}, toggle() {}, contains: () => false };
  }
  get lastElementChild() { return this.children.at(-1) ?? null; }
  appendChild(c) { this.children.push(c); return c; }
  addEventListener(type, fn) { (this.listeners[type] ??= []).push(fn); }
  async fire(type) { for (const fn of this.listeners[type] || []) await fn({ preventDefault() {} }); }
}

const json = (status, body) => ({ ok: status < 400, status, json: async () => body });

function ndjson(events) {
  const bytes = new TextEncoder().encode(events.map((e) => JSON.stringify(e) + '\n').join(''));
  let sent = false;
  return { ok: true, status: 200,
           body: { getReader: () => ({ read: async () => (sent ? { done: true }
             : (sent = true, { value: bytes, done: false })) }) } };
}

function load() {
  const byId = {};
  const $ = (id) => (byId[id] ??= new El(id));
  const fetches = [];
  const replies = [];  // what /api/turn and /api/confirm answer, in order
  const tray = {
    files: [],
    cleared: 0,
    count() { return this.files.length; },
    whenReady: async () => {},
    summary() { return this.files.map((f) => `[file: ${f}]`).join(' '); },
    appendTo(form) { for (const f of this.files) form.append('attachment', new Blob(['x']), f); },
    clear() { this.cleared += 1; this.files = []; },
  };
  const win = {
    document: {
      getElementById: $,
      createElement: () => new El(),
      addEventListener() {},
      visibilityState: 'visible',
    },
    Audio: class { play() { return Promise.resolve(); } pause() {} },
    localStorage: { getItem: () => null, setItem() {} },
    navigator: {},
    location: { hash: '', pathname: '/', origin: 'https://pc.example' },
    history: { replaceState() {} },
    HeyClaudeAttach: { tray },
    FormData, Blob, TextDecoder, setTimeout, clearTimeout, console,
    fetch: async (url, opts = {}) => {
      fetches.push({ url, opts });
      if (url === '/healthz') return json(200, {});  // no failover
      if (url === '/api/state') return json(200, { busy: false, confirm: null });
      return replies.shift();
    },
  };
  win.window = win;
  vm.createContext(win);
  vm.runInContext(SCRIPT, win);
  const flush = () => new Promise((r) => setImmediate(r));
  const turns = () => fetches.filter((f) => f.url === '/api/turn' || f.url === '/api/confirm');
  async function submit(text) {
    $('text').value = text;
    await $('text-form').fire('submit');
    for (let i = 0; i < 5; i++) await flush();  // send() isn't awaited by the handler
  }
  return { $, tray, fetches, replies, turns, submit, flush, notes: () =>
    $('log').children.filter((c) => c.className === 'msg note').map((c) => c.textContent) };
}

test('files go with a message; the chips clear only when the server took it', async () => {
  const p = load();
  await p.flush();
  p.tray.files = ['plan.pdf', 'notes.txt'];
  p.replies.push(json(413, { error: 'too large' }));
  await p.submit('read these');
  const [refused] = p.turns();
  assert.equal(refused.url, '/api/turn');
  assert.equal(refused.opts.body.get('text'), 'read these');
  assert.deepEqual(refused.opts.body.getAll('attachment').map((f) => f.name), ['plan.pdf', 'notes.txt']);
  assert.equal(p.tray.cleared, 0);  // refused: the files are still there to retry
  assert.deepEqual(p.tray.files, ['plan.pdf', 'notes.txt']);
  assert.deepEqual(p.notes(), ['too large']);

  p.replies.push(ndjson([{ type: 'reply', text: 'Two files.' }, { type: 'done' }]));
  await p.submit('read these');
  assert.equal(p.turns().length, 2);
  assert.equal(p.turns()[1].opts.body.getAll('attachment').length, 2);
  assert.equal(p.tray.cleared, 1);
  assert.equal(p.$('text').value, '');
});

test('a yes/no answer never carries the picked files', async () => {
  const p = load();
  await p.flush();
  p.replies.push(ndjson([{ type: 'confirm', question: 'Delete Dentist?', id: 'c1' }]));
  await p.submit('delete the dentist');
  assert.equal(p.$('confirm').hidden, false);
  // files picked while the question is open stay for the next message
  p.tray.files = ['photo.jpg'];
  p.replies.push(ndjson([{ type: 'reply', text: 'OK, I won\'t delete it.' }, { type: 'done' }]));
  await p.submit('no');
  const answer = p.turns().at(-1);
  assert.equal(answer.url, '/api/confirm');
  assert.equal(answer.opts.headers['Content-Type'], 'application/json');
  assert.deepEqual(JSON.parse(answer.opts.body), { answer: 'no' });
  assert.equal(p.tray.cleared, 0);
  assert.deepEqual(p.tray.files, ['photo.jpg']);

  // the Yes button, too
  p.replies.push(ndjson([{ type: 'confirm', question: 'Add Lunch?', id: 'c2' }]));
  await p.submit('add lunch');
  assert.equal(p.turns().at(-1).opts.body.getAll('attachment').length, 1);  // a new message: they go
  p.tray.files = ['photo.jpg'];  // picked again while asked
  p.replies.push(ndjson([{ type: 'reply', text: 'Added.' }, { type: 'done' }]));
  await p.$('yes').fire('click');
  for (let i = 0; i < 5; i++) await p.flush();
  const yes = p.turns().at(-1);
  assert.equal(yes.url, '/api/confirm');
  assert.deepEqual(JSON.parse(yes.opts.body), { answer: 'yes' });
  assert.deepEqual(p.tray.files, ['photo.jpg']);
});
