// node --test tests/js/app-redo.test.mjs  (Node 18+; no packages needed)
//
// Try again, edit and branch in the phone's chat (kwil/web/app.js and
// history.js, in node:vm with the small fake DOM and fetch() of
// app-resume.test.mjs). Checks: ↻ under the last reply and the pencil on
// Kyle's last message appear only when the server's "done" says "redo",
// are disabled while busy or asked, post to /api/regenerate and /api/edit
// (with the pane's conversation id), replace the old reply when the server
// says "redo" (and bring it back when it failed), and "Branch from here"
// binds the pane to the new conversation.

import test from 'node:test';
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import vm from 'node:vm';

const SCRIPT = readFileSync(new URL('../../kwil/web/app.js', import.meta.url), 'utf8');
const HISTORY = readFileSync(new URL('../../kwil/web/history.js', import.meta.url), 'utf8');
const CID = 'AbCdEfGhIjKlMnOp';
const BRANCH = 'BrAnChEdChAt0123';

class El {
  constructor(id = '', doc = null) {
    this.id = id; this.doc = doc; this.hidden = false; this.disabled = false; this.checked = false;
    this.value = ''; this.placeholder = ''; this.className = ''; this.type = ''; this.returnValue = '';
    this.dataset = {}; this.children = []; this.listeners = {}; this.attrs = {}; this.parent = null;
    this.ownText = ''; this.scrollTop = 0; this.scrollHeight = 0; this.innerHTML = '';
    this.classList = { add() {}, remove() {}, toggle() {}, contains: () => false };
  }
  get textContent() { return this.ownText + this.children.map((c) => c.textContent).join(''); }
  set textContent(v) { this.ownText = String(v); for (const c of this.children) c.parent = null; this.children = []; }
  get lastElementChild() { return this.children.at(-1) ?? null; }
  appendChild(c) { c.remove(); c.parent = this; this.children.push(c); return c; }
  append(...cs) { for (const c of cs) this.appendChild(c); }
  prepend(c) { c.parent = this; this.children.unshift(c); }
  after(c) {  // insert `c` right after this element
    c.remove();
    const kids = this.parent.children;
    kids.splice(kids.indexOf(this) + 1, 0, c);
    c.parent = this.parent;
  }
  remove() {
    if (!this.parent) return;
    this.parent.children = this.parent.children.filter((c) => c !== this);
    this.parent = null;
  }
  setAttribute(k, v) { this.attrs[k] = String(v); }
  getAttribute(k) { return this.attrs[k] ?? null; }
  focus() { if (this.doc) this.doc.activeElement = this; }
  showModal() { this.open = true; }
  querySelector() { return new El('', this.doc); }
  querySelectorAll() { return []; }
  addEventListener(type, fn) { (this.listeners[type] ??= []).push(fn); }
  async fire(type) { for (const fn of this.listeners[type] || []) await fn({ preventDefault() {} }); }
}

const json = (status, body) => ({ ok: status < 400, status, json: async () => body });

function liveStream() {
  const enc = new TextEncoder();
  const chunks = [];
  let wake = null;
  let ended = false;
  const poke = () => { if (wake) { const w = wake; wake = null; w(); } };
  return {
    res: { ok: true, status: 200, body: { getReader: () => ({ read: async () => {
      while (!chunks.length && !ended) await new Promise((r) => { wake = r; });
      return chunks.length ? { value: chunks.shift(), done: false } : { done: true };
    } }) } },
    push(...events) { for (const ev of events) chunks.push(enc.encode(JSON.stringify(ev) + '\n')); poke(); },
    end() { ended = true; poke(); },
  };
}

const stored = (id, title, extra = {}) => ({
  id, title, source: 'wake', started_at: 1, updated_at: 1, message_count: 4, pinned: false,
  archived: false, summary: '', active: false, redo: true,
  messages: [{ role: 'user', kind: 'chat', text: 'plan the lab', tools: [] },
             { role: 'assistant', kind: 'chat', text: 'step one', tools: [] },
             { role: 'user', kind: 'chat', text: 'and then?', tools: [] },
             { role: 'assistant', kind: 'chat', text: 'step two', tools: [] }],
  ...extra,
});

function load({ history = false } = {}) {
  const byId = {};
  const document = {
    activeElement: null,
    getElementById: (id) => (byId[id] ??= new El(id, document)),
    createElement: () => new El('', document),
    listeners: {},
    addEventListener(type, fn) { (this.listeners[type] ??= []).push(fn); },
    querySelector: () => null,
    body: new El('body'),
    visibilityState: 'visible',
  };
  const $ = document.getElementById;
  $('login').hidden = true;  // signed in
  $('edit-bar').hidden = true;  // as index.html has it
  const fetches = [];
  const streams = [];
  const refusing = [];  // [status, body] the next posts' fetch() answers
  const state = { busy: false, confirm: null };
  const conversations = { [CID]: stored(CID, 'Lab plan') };
  const branched = stored(BRANCH, 'Lab plan (branch)', { message_count: 2, source: 'web' });
  branched.messages = branched.messages.slice(0, 2);
  const win = {
    document,
    Audio: class { play() { return Promise.resolve(); } pause() {} },
    localStorage: { getItem: () => null, setItem() {} },
    navigator: {},
    location: { hash: '', pathname: '/', origin: 'https://pc.example' },
    history: { replaceState() {} },
    FormData, Blob, TextDecoder, setTimeout, clearTimeout, console,
    AbortController, URLSearchParams,
    fetch: async (url, opts = {}) => {
      fetches.push({ url, opts });
      if (url === '/healthz') return json(200, {});
      if (url === '/api/state') return json(200, { ...state });
      if (url.startsWith('/api/conversations?')) return json(200, { conversations: [], next_offset: null });
      if (url.endsWith('/branch')) return refusing.length ? json(...refusing.shift()) : json(200, { ok: true, ...branched });
      const m = url.match(/^\/api\/conversations\/([^/?]+)$/);
      if (m) return json(200, conversations[decodeURIComponent(m[1])]);
      if (refusing.length) return json(...refusing.shift());
      const s = liveStream();
      const sent = opts.body ? JSON.parse(opts.body).conversation_id : null;
      s.conversation = sent || 'FreshChat0123456';
      streams.push(s);
      return s.res;
    },
  };
  win.window = win;
  vm.createContext(win);
  vm.runInContext(SCRIPT, win);
  if (history) vm.runInContext(HISTORY, win);
  const flush = async () => { for (let i = 0; i < 8; i++) await new Promise((r) => setImmediate(r)); };
  const posts = () => fetches.filter((f) => ['/api/turn', '/api/regenerate', '/api/edit', '/api/confirm'].includes(f.url))
    .map((f) => ({ url: f.url, body: JSON.parse(f.opts.body) }));
  async function submit(text) {
    $('text').value = text;
    await $('text-form').fire('submit');
    await flush();
  }
  async function finish(stream, ...events) {
    stream.push(...events);
    stream.end();
    await flush();
  }
  const done = (stream, redo = true) => ({ type: 'done', conversation: stream.conversation, redo });
  const shownLog = (kind) => $('log').children.filter((c) => c.className === `msg ${kind}` && !c.hidden)
    .map((c) => c.textContent);
  const find = (cls) => {  // a button with this class anywhere in #log
    for (const c of $('log').children) {
      if (c.className.includes(cls)) return c;
      for (const k of c.children) if (k.className.includes(cls)) return k;
    }
    return null;
  };
  const redoBtn = () => find('redo-btn');
  const editBtn = () => find('msg-edit');
  return { $, win, document, fetches, streams, refusing, state, posts, submit, finish, flush,
           done, shownLog, find, redoBtn, editBtn, conversations };
}

async function firstTurn(p, redo = true) {
  await p.submit('fruit?');
  await p.finish(p.streams[0], { type: 'reply', text: 'Bananas.' }, p.done(p.streams[0], redo));
}

test('↻ and the pencil appear on the last turn only when "done" says redo', async () => {
  const p = load();
  await p.flush();
  await firstTurn(p);
  const again = p.redoBtn();
  const edit = p.editBtn();
  assert.ok(again && edit);
  assert.equal(again.getAttribute('aria-label'), 'Try again');
  assert.equal(edit.getAttribute('aria-label'), 'Edit message');
  assert.equal(again.disabled, false);
  // ↻ sits right under the reply; the pencil in Kyle's bubble
  const kids = p.$('log').children;
  assert.equal(kids[kids.indexOf(again.parent) - 1].textContent, 'Bananas.');
  assert.equal(edit.parent.className, 'msg me');
  // the next message moves them to the new last turn
  await p.submit('and apples?');
  assert.equal(p.redoBtn(), null);
  assert.equal(p.editBtn(), null);
  await p.finish(p.streams[1], { type: 'reply', text: 'Apples.' }, p.done(p.streams[1], false));
  assert.equal(p.redoBtn(), null);  // the server said no (a tool changed something)
  assert.equal(p.editBtn(), null);
  // an older server's "done" has no "redo": nothing offered
  await p.submit('pears?');
  await p.finish(p.streams[2], { type: 'reply', text: 'Pears.' },
                 { type: 'done', conversation: p.streams[2].conversation });
  assert.equal(p.redoBtn(), null);
});

test('try again posts /api/regenerate, replaces the old reply, and offers it again', async () => {
  const p = load();
  await p.flush();
  await firstTurn(p);
  await p.redoBtn().fire('click');
  await p.flush();
  assert.deepEqual(p.posts().at(-1), { url: '/api/regenerate', body: {} });
  assert.equal(p.redoBtn(), null);  // gone while it runs
  assert.equal(p.$('stop').hidden, false);
  p.streams[1].push({ type: 'redo', edit: false }, { type: 'delta', text: 'Apples ' });
  await p.flush();
  assert.deepEqual(p.shownLog('claude'), ['Apples ']);  // the old one hidden at once
  await p.finish(p.streams[1], { type: 'reply', text: 'Apples instead.' }, p.done(p.streams[1]));
  assert.deepEqual(p.shownLog('claude'), ['Apples instead.']);
  assert.equal(p.$('log').children.filter((c) => c.textContent === 'Bananas.').length, 0);
  assert.deepEqual(p.shownLog('me'), ['fruit?']);
  assert.ok(p.redoBtn() && p.editBtn());
  await p.redoBtn().fire('click');  // and again
  await p.flush();
  assert.equal(p.posts().filter((x) => x.url === '/api/regenerate').length, 2);
});

test('edit: the pencil fills the text box; sending posts /api/edit and rewrites the bubble', async () => {
  const p = load();
  await p.flush();
  await firstTurn(p);
  await p.editBtn().fire('click');
  assert.equal(p.$('text').value, 'fruit?');
  assert.equal(p.$('edit-bar').hidden, false);
  await p.submit('vegetables?');
  assert.deepEqual(p.posts().at(-1), { url: '/api/edit', body: { text: 'vegetables?' } });
  assert.equal(p.$('edit-bar').hidden, true);
  assert.deepEqual(p.shownLog('me'), ['fruit?']);  // until the server rewound it
  await p.finish(p.streams[1], { type: 'redo', edit: true }, { type: 'reply', text: 'Carrots.' },
                 p.done(p.streams[1]));
  assert.deepEqual(p.shownLog('me'), ['vegetables?']);
  assert.deepEqual(p.shownLog('claude'), ['Carrots.']);
  // cancelling an edit leaves everything as it was
  await p.editBtn().fire('click');
  await p.$('edit-cancel').fire('click');
  assert.equal(p.$('edit-bar').hidden, true);
  assert.equal(p.$('text').value, '');
  await p.submit('a new message');
  assert.deepEqual(p.posts().at(-1), { url: '/api/turn', body: { text: 'a new message' } });
});

test('a bound pane sends its conversation id with try again and edit', async () => {
  const p = load();
  await p.flush();
  p.win.KwilApp.bindPane(CID);
  await p.submit('fruit?');
  await p.finish(p.streams[0], { type: 'reply', text: 'Bananas.' }, p.done(p.streams[0]));
  await p.redoBtn().fire('click');
  await p.finish(p.streams[1], { type: 'redo', edit: false }, { type: 'reply', text: 'Apples.' },
                 p.done(p.streams[1]));
  await p.editBtn().fire('click');
  await p.submit('kiwis?');
  assert.deepEqual(p.posts().slice(1), [
    { url: '/api/regenerate', body: { conversation_id: CID } },
    { url: '/api/edit', body: { text: 'kiwis?', conversation_id: CID } }]);
});

test('disabled while busy or asked; nothing is sent', async () => {
  const p = load();
  await p.flush();
  await firstTurn(p);
  // the page comes back with a turn running (e.g. the voice view's)
  p.state.busy = true;
  for (const fn of p.document.listeners.visibilitychange) fn();
  await p.flush();
  assert.equal(p.redoBtn().disabled, true);
  assert.equal(p.editBtn().disabled, true);
  await p.redoBtn().fire('click');
  await p.editBtn().fire('click');
  assert.equal(p.$('edit-bar').hidden, true);
  assert.equal(p.$('text').value, '');
  await p.finish(p.streams[1], p.done(p.streams[1]));
  assert.equal(p.redoBtn().disabled, false);
  // a yes/no question open: disabled too
  p.state.busy = false;
  p.state.confirm = 'Delete Dentist?';
  for (const fn of p.document.listeners.visibilitychange) fn();
  await p.flush();
  assert.equal(p.redoBtn().disabled, true);
  assert.equal(p.editBtn().disabled, true);
  await p.redoBtn().fire('click');
  await p.flush();
  assert.deepEqual(p.posts().map((x) => x.url), ['/api/turn']);
});

test('an edit typed while a reply runs is not sent as an edit', async () => {
  const p = load();
  await p.flush();
  await firstTurn(p);
  await p.editBtn().fire('click');
  p.state.busy = true;
  for (const fn of p.document.listeners.visibilitychange) fn();
  await p.flush();
  await p.submit('vegetables?');
  assert.deepEqual(p.posts().map((x) => x.url), ['/api/turn']);
  assert.ok(p.shownLog('note').includes('Wait for the reply to finish first.'));
});

test('a refusal (409) keeps the old reply and says why', async () => {
  const p = load();
  await p.flush();
  await firstTurn(p);
  p.refusing.push([409, { error: "That reply already changed something, so I won't redo it. Ask again instead." }]);
  await p.redoBtn().fire('click');
  await p.flush();
  assert.deepEqual(p.shownLog('claude'), ['Bananas.']);
  assert.match(p.shownLog('note').at(-1), /already changed something/);
  assert.equal(p.$('stop').hidden, true);  // not busy any more
});

test('a failed request after the rewind brings the old reply back', async () => {
  const p = load();
  await p.flush();
  await firstTurn(p);
  await p.editBtn().fire('click');
  await p.submit('vegetables?');
  await p.finish(p.streams[1], { type: 'redo', edit: true },
                 { type: 'error', message: 'Sorry, something went wrong.', restored: true },
                 p.done(p.streams[1]));
  assert.deepEqual(p.shownLog('me'), ['fruit?']);
  assert.deepEqual(p.shownLog('claude'), ['Bananas.']);
  assert.ok(p.redoBtn());  // it may be tried again
});

test('Stop before the new reply says anything: the old reply comes back', async () => {
  const p = load();
  await p.flush();
  await firstTurn(p);
  await p.redoBtn().fire('click');
  await p.flush();
  p.streams[1].push({ type: 'redo', edit: false });
  await p.flush();
  assert.deepEqual(p.shownLog('claude'), []);
  await p.$('stop').fire('click');
  await p.flush();
  assert.ok(p.fetches.some((f) => f.url === '/api/stop'));
  await p.finish(p.streams[1],
                 { type: 'error', message: 'Stopped. The earlier reply stays.', restored: true },
                 p.done(p.streams[1]));
  assert.deepEqual(p.shownLog('claude'), ['Bananas.']);
  assert.deepEqual(p.shownLog('me'), ['fruit?']);
  assert.equal(p.shownLog('note').at(-1), 'Stopped. The earlier reply stays.');
  assert.ok(p.redoBtn());
});

test('a new pane drops the tools and the edit', async () => {
  const p = load();
  await p.flush();
  await firstTurn(p);
  await p.editBtn().fire('click');
  p.win.KwilApp.bindPane(CID);
  assert.equal(p.redoBtn(), null);
  assert.equal(p.$('edit-bar').hidden, true);
  await p.submit('hello there');
  assert.deepEqual(p.posts().at(-1), { url: '/api/turn',
                                       body: { text: 'hello there', conversation_id: CID } });
});

test('history.js: Continue offers try again on the stored last turn', async () => {
  const p = load({ history: true });
  await p.flush();
  await p.win.openTranscript(CID);
  await p.$('continue').fire('click');
  await p.flush();
  assert.ok(p.redoBtn() && p.editBtn());
  assert.equal(p.editBtn().parent.textContent, 'and then?');
  await p.redoBtn().fire('click');
  await p.flush();
  assert.deepEqual(p.posts().at(-1), { url: '/api/regenerate', body: { conversation_id: CID } });
  // a stored last turn that changed something: nothing offered
  const q = load({ history: true });
  q.conversations[CID].redo = false;
  await q.flush();
  await q.win.openTranscript(CID);
  await q.$('continue').fire('click');
  await q.flush();
  assert.equal(q.redoBtn(), null);
  assert.equal(q.editBtn(), null);
});

test('history.js: Branch from here posts the index and binds the pane to the new conversation', async () => {
  const p = load({ history: true });
  await p.flush();
  await p.win.openTranscript(CID);
  const msgs = p.$('transcript-log').children;
  assert.equal(msgs.length, 4);
  const more = msgs[1].children.find((c) => c.className.includes('msg-more'));
  assert.equal(more.getAttribute('aria-label'), 'Message actions');
  await more.fire('click');
  assert.equal(p.$('msg-sheet').open, true);
  assert.equal(p.$('msg-branch').disabled, false);
  p.$('msg-sheet').returnValue = 'branch';
  await p.$('msg-sheet').fire('close');
  await p.flush();
  const post = p.fetches.find((f) => f.url.endsWith('/branch'));
  assert.equal(post.url, `/api/conversations/${CID}/branch`);
  assert.equal(post.opts.method, 'POST');
  assert.deepEqual(JSON.parse(post.opts.body), { message_index: 1 });
  assert.equal(p.win.KwilApp.pane().cid, BRANCH);
  assert.deepEqual(p.shownLog('me'), ['plan the lab']);
  assert.deepEqual(p.shownLog('claude'), ['step one']);
  assert.equal(p.shownLog('note').at(-1), 'Branched into “Lab plan (branch)”. Go ahead.');
  assert.equal(p.$('app').hidden, false);
  await p.submit('another way?');
  assert.deepEqual(p.posts().at(-1), { url: '/api/turn',
                                       body: { text: 'another way?', conversation_id: BRANCH } });
});

test('history.js: no branching while a reply runs, and a refusal is shown', async () => {
  const p = load({ history: true });
  await p.flush();
  await p.win.openTranscript(CID);
  await p.submit('busy now');  // a turn is running
  p.win.openMessageSheet(0, 'plan the lab');
  assert.equal(p.$('msg-branch').disabled, true);
  p.$('msg-sheet').returnValue = 'branch';
  await p.$('msg-sheet').fire('close');
  await p.flush();
  assert.equal(p.fetches.filter((f) => f.url.endsWith('/branch')).length, 0);
  await p.finish(p.streams[0], { type: 'reply', text: 'Done.' }, p.done(p.streams[0]));
  p.refusing.push([409, { error: 'turn incognito off to branch a conversation' }]);
  p.win.openMessageSheet(0, 'plan the lab');
  p.$('msg-sheet').returnValue = 'branch';
  await p.$('msg-sheet').fire('close');
  await p.flush();
  assert.equal(p.$('continue-hint').textContent, 'turn incognito off to branch a conversation');
  assert.equal(p.win.KwilApp.pane().cid, null);  // not bound
});
