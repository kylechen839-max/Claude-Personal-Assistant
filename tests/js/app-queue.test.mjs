// node --test tests/js/app-queue.test.mjs  (Node 18+; no packages needed)
//
// Runs the phone page's kwil/web/app.js in node:vm with a small fake DOM
// and fetch(), with replies the test streams by hand, and checks typing
// while Kwil is answering: the text box stays usable, Send queues the
// message as a "Queued" bubble (at most three, each with a ×), the queue
// goes in order one turn at a time once the reply is done, Stop asks the
// server to stop (POST /api/stop) and the queue then goes, and a pending
// yes/no question takes the typed answer instead of queueing it.

import test from 'node:test';
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import vm from 'node:vm';

const SCRIPT = readFileSync(new URL('../../kwil/web/app.js', import.meta.url), 'utf8');

class El {
  constructor(id = '', doc = null) {
    this.id = id; this.doc = doc; this.hidden = false; this.disabled = false; this.checked = false;
    this.value = ''; this.placeholder = ''; this.className = ''; this.type = '';
    this.dataset = {}; this.children = []; this.listeners = {}; this.attrs = {}; this.parent = null;
    this.ownText = ''; this.scrollTop = 0; this.scrollHeight = 0;
    this.classList = { add() {}, remove() {}, toggle() {}, contains: () => false };
  }
  get textContent() { return this.ownText + this.children.map((c) => c.textContent).join(' '); }
  set textContent(v) { this.ownText = String(v); for (const c of this.children) c.parent = null; this.children = []; }
  get lastElementChild() { return this.children.at(-1) ?? null; }
  appendChild(c) { c.parent = this; this.children.push(c); return c; }
  remove() {
    if (!this.parent) return;
    this.parent.children = this.parent.children.filter((c) => c !== this);
    this.parent = null;
  }
  setAttribute(k, v) { this.attrs[k] = String(v); }
  getAttribute(k) { return this.attrs[k] ?? null; }
  focus() { if (this.doc) this.doc.activeElement = this; }
  addEventListener(type, fn) { (this.listeners[type] ??= []).push(fn); }
  async fire(type) { for (const fn of this.listeners[type] || []) await fn({ preventDefault() {} }); }
}

const json = (status, body) => ({ ok: status < 400, status, json: async () => body });

// A turn's NDJSON stream that the test feeds event by event.
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

function load() {
  const byId = {};
  const document = {
    activeElement: null,
    getElementById: (id) => (byId[id] ??= new El(id, document)),
    createElement: () => new El('', document),
    listeners: {},
    addEventListener(type, fn) { (this.listeners[type] ??= []).push(fn); },
    visibilityState: 'visible',
  };
  const $ = document.getElementById;
  $('login').hidden = true;  // signed in
  const fetches = [];
  const streams = [];  // one per /api/turn or /api/confirm, in order
  const failing = [];  // errors the next turns' fetch() throws
  const refusing = [];  // [status, body] the next turns' fetch() answers
  const state = { busy: false, confirm: null };  // what /api/state says
  const win = {
    document,
    Audio: class { play() { return Promise.resolve(); } pause() {} },
    localStorage: { getItem: () => null, setItem() {} },
    navigator: {},
    location: { hash: '', pathname: '/', origin: 'https://pc.example' },
    history: { replaceState() {} },
    FormData, Blob, TextDecoder, setTimeout, clearTimeout, console,
    fetch: async (url, opts = {}) => {
      fetches.push({ url, opts });
      if (url === '/healthz') return json(200, {});  // no failover
      if (url === '/api/state') return json(200, { ...state });
      if (url === '/api/stop') return json(200, { stopped: true });
      if (failing.length) throw failing.shift();
      if (refusing.length) return json(...refusing.shift());
      const s = liveStream();
      streams.push(s);
      return s.res;
    },
  };
  win.window = win;
  vm.createContext(win);
  vm.runInContext(SCRIPT, win);
  const flush = async () => { for (let i = 0; i < 8; i++) await new Promise((r) => setImmediate(r)); };
  const turns = () => fetches.filter((f) => f.url === '/api/turn' || f.url === '/api/confirm')
    .map((f) => ({ url: f.url, body: JSON.parse(f.opts.body) }));
  async function submit(text) {
    $('text').value = text;
    await $('text-form').fire('submit');
    await flush();
  }
  async function finish(stream, ...events) {  // the reply, then "done"
    stream.push(...events, { type: 'done' });
    stream.end();
    await flush();
  }
  const queued = () => $('queue').children.map((c) => c.children[0].textContent);
  const log = (kind) => $('log').children.filter((c) => c.className === `msg ${kind}`).map((c) => c.textContent);
  async function wake() {  // the app comes back to the front: refreshState()
    for (const fn of document.listeners.visibilitychange || []) await fn();
    await flush();
  }
  return { $, document, fetches, streams, failing, refusing, state, wake, turns, submit, finish, flush, queued, log };
}

test('typing stays enabled mid-turn; Send queues and the reply then sends it', async () => {
  const p = load();
  await p.flush();
  await p.submit('first');
  assert.deepEqual(p.turns(), [{ url: '/api/turn', body: { text: 'first' } }]);
  p.streams[0].push({ type: 'delta', text: 'Work' });
  await p.flush();
  // still answering: the box is usable, Stop shows, the mic keeps today's rule
  assert.equal(p.$('text').disabled, false);
  assert.equal(p.$('stop').hidden, false);
  assert.equal(p.$('mic').disabled, true);
  p.$('text').value = 'half typed';
  p.streams[0].push({ type: 'delta', text: 'ing…' });
  await p.flush();
  assert.equal(p.$('text').value, 'half typed');  // never cleared mid-turn
  await p.submit('second');
  assert.equal(p.turns().length, 1);  // not sent yet
  assert.deepEqual(p.queued(), ['second']);
  assert.equal(p.$('queue').hidden, false);
  const bubble = p.$('queue').children[0];
  assert.equal(bubble.className, 'msg me queued');
  assert.equal(bubble.children[1].textContent, 'Queued');
  assert.equal(bubble.children[2].getAttribute('aria-label'), 'Remove queued message');
  assert.equal(p.$('text').value, '');

  await p.finish(p.streams[0], { type: 'reply', text: 'Working on it.' });
  assert.deepEqual(p.turns().map((t) => t.body), [{ text: 'first' }, { text: 'second' }]);
  assert.deepEqual(p.queued(), []);
  assert.equal(p.$('queue').hidden, true);
  assert.deepEqual(p.log('me'), ['first', 'second']);  // after the reply, in order
  assert.deepEqual(p.log('claude'), ['Working on it.']);
  await p.finish(p.streams[1], { type: 'reply', text: 'Done.' });
  assert.equal(p.$('stop').hidden, true);
  assert.equal(p.$('mic').disabled, false);
});

test('the queue goes in order, one turn at a time', async () => {
  const p = load();
  await p.flush();
  await p.submit('one');
  await p.submit('two');
  await p.submit('three');
  assert.deepEqual(p.queued(), ['two', 'three']);
  await p.finish(p.streams[0], { type: 'reply', text: 'A' });
  assert.deepEqual(p.turns().map((t) => t.body.text), ['one', 'two']);  // not three yet
  assert.deepEqual(p.queued(), ['three']);
  await p.finish(p.streams[1], { type: 'reply', text: 'B' });
  assert.deepEqual(p.turns().map((t) => t.body.text), ['one', 'two', 'three']);
  await p.finish(p.streams[2], { type: 'reply', text: 'C' });
  assert.equal(p.turns().length, 3);
});

test('× removes a queued message before it is sent', async () => {
  const p = load();
  await p.flush();
  await p.submit('one');
  await p.submit('drop me');
  await p.submit('keep me');
  await p.$('queue').children[0].children[2].fire('click');
  assert.deepEqual(p.queued(), ['keep me']);
  assert.equal(p.document.activeElement, p.$('text'));  // focus back in the box
  await p.finish(p.streams[0], { type: 'reply', text: 'A' });
  await p.finish(p.streams[1], { type: 'reply', text: 'B' });
  assert.deepEqual(p.turns().map((t) => t.body.text), ['one', 'keep me']);
});

test('the queue holds at most three; a fourth stays in the box', async () => {
  const p = load();
  await p.flush();
  await p.submit('running');
  for (const t of ['a', 'b', 'c']) await p.submit(t);
  await p.submit('d');
  assert.deepEqual(p.queued(), ['a', 'b', 'c']);
  assert.equal(p.$('text').value, 'd');  // kept, not lost
  assert.deepEqual(p.log('note'), ['You can queue up to 3 messages.']);
});

test('Stop asks the server to stop; the stopped reply is kept and the queue goes', async () => {
  const p = load();
  await p.flush();
  await p.submit('write a long story');
  p.streams[0].push({ type: 'delta', text: 'Once upon' });
  await p.flush();
  await p.submit('actually, a poem');
  await p.$('stop').fire('click');
  const stop = p.fetches.find((f) => f.url === '/api/stop');
  assert.equal(stop.opts.method, 'POST');
  assert.equal(p.$('stop').disabled, true);  // one press
  assert.equal(p.turns().length, 1);  // the server ends the turn first
  await p.finish(p.streams[0], { type: 'reply', text: 'Once upon', stopped: true });
  assert.deepEqual(p.log('claude'), ['Once upon']);
  assert.deepEqual(p.log('note'), ['Stopped.']);
  assert.deepEqual(p.turns().map((t) => t.body.text), ['write a long story', 'actually, a poem']);
  assert.equal(p.$('stop').disabled, false);  // the new turn can be stopped too
  // stopped before any words: no empty reply bubble is left behind
  p.streams[1].push({ type: 'delta', text: '' });
  await p.$('stop').fire('click');
  await p.finish(p.streams[1], { type: 'reply', text: '', stopped: true });
  assert.deepEqual(p.log('claude'), ['Once upon']);
});

test('a pending yes/no question takes the typed answer; the queue waits for it', async () => {
  const p = load();
  await p.flush();
  await p.submit('delete the dentist');
  await p.submit('then what is next?');  // queued before the question came
  p.streams[0].push({ type: 'tool', name: 'delete_event' },
                    { type: 'confirm', question: 'Delete Dentist?', id: 'c1' });
  p.streams[0].end();
  await p.flush();
  assert.equal(p.$('confirm').hidden, false);
  assert.equal(p.$('stop').hidden, true);  // a question is answered, not stopped
  assert.equal(p.turns().length, 1);  // the queue waits for the answer
  await p.submit('yes');
  assert.deepEqual(p.turns()[1], { url: '/api/confirm', body: { answer: 'yes' } });
  assert.deepEqual(p.queued(), ['then what is next?']);
  // answered: text typed now is a new message, not a second answer
  await p.submit('and lunch?');
  assert.equal(p.turns().length, 2);
  assert.deepEqual(p.queued(), ['then what is next?', 'and lunch?']);
  await p.finish(p.streams[1], { type: 'reply', text: 'Deleted.' });
  assert.deepEqual(p.turns()[2], { url: '/api/turn', body: { text: 'then what is next?' } });
});

test('a failed answer brings the question back, and text answers it again', async () => {
  const p = load();
  await p.flush();
  await p.submit('delete the dentist');
  p.streams[0].push({ type: 'confirm', question: 'Delete Dentist?', id: 'c1' });
  p.streams[0].end();
  await p.flush();
  p.failing.push(new Error('offline'));
  await p.submit('no');
  assert.deepEqual(p.log('note'), ['offline']);
  assert.equal(p.$('confirm').hidden, false);
  assert.equal(p.$('confirm-question').textContent, 'Delete Dentist?');
  await p.submit('no');
  assert.deepEqual(p.turns().slice(1), [{ url: '/api/confirm', body: { answer: 'no' } },
                                        { url: '/api/confirm', body: { answer: 'no' } }]);
});

test('the page has a polite queue area and a Stop button reachable by keyboard', () => {
  const html = readFileSync(new URL('../../kwil/web/index.html', import.meta.url), 'utf8');
  assert.match(html, /<section id="queue" class="queue" aria-live="polite" hidden><\/section>/);
  // a real <button> in the composer (focusable, Enter/Space), labelled, hidden until a turn runs
  assert.match(html, /<button id="stop" class="stop-btn" type="button" aria-label="Stop" hidden>/);
});

test('Stop after a tool round keeps the streamed words once (judge round 1)', async () => {
  const p = load();
  await p.flush();
  await p.submit('check my calendar and plan');
  p.streams[0].push({ type: 'delta', text: 'Let me look.' }, { type: 'tool', name: 'list_events' },
                    { type: 'delta', text: 'You have' });
  await p.flush();
  await p.$('stop').fire('click');
  // _Cancelled carries the first round's text, already streamed
  await p.finish(p.streams[0], { type: 'reply', text: 'Let me look.', stopped: true });
  assert.deepEqual(p.log('claude'), ['Let me look.', 'You have']);
  assert.deepEqual(p.log('note'), ['Stopped.']);
  // stopped right after a tool, before any new words: nothing added
  await p.submit('again');
  p.streams[1].push({ type: 'delta', text: 'One sec.' }, { type: 'tool', name: 'list_events' });
  await p.flush();
  await p.$('stop').fire('click');
  await p.finish(p.streams[1], { type: 'reply', text: 'One sec.', stopped: true });
  assert.deepEqual(p.log('claude'), ['Let me look.', 'You have', 'One sec.']);
});

test('an answer to an expired question (409) does not bring it back; the queue goes', async () => {
  const p = load();
  await p.flush();
  await p.submit('delete the dentist');
  await p.submit('what is next?');  // queued
  p.streams[0].push({ type: 'confirm', question: 'Delete Dentist?', id: 'c1' });
  p.streams[0].end();
  await p.flush();
  p.refusing.push([409, { error: 'nothing to confirm' }]);
  await p.submit('yes');
  assert.equal(p.$('confirm').hidden, true);  // the dead question stays gone
  assert.deepEqual(p.log('note'), ['nothing to confirm']);
  assert.deepEqual(p.turns().slice(1), [{ url: '/api/confirm', body: { answer: 'yes' } },
                                        { url: '/api/turn', body: { text: 'what is next?' } }]);
});

test('a refused spoken answer (429) keeps the question open', async () => {
  const p = load();
  await p.flush();
  await p.submit('delete the dentist');
  p.streams[0].push({ type: 'confirm', question: 'Delete Dentist?', id: 'c1' });
  p.streams[0].end();
  await p.flush();
  p.refusing.push([429, { error: 'out of speech time today' }]);
  await p.submit('yes');
  assert.equal(p.$('confirm').hidden, false);
  assert.equal(p.$('confirm-question').textContent, 'Delete Dentist?');
});

test('coming back to the app after the question expired sends the queue', async () => {
  const p = load();
  await p.flush();
  await p.submit('delete the dentist');
  await p.submit('what is next?');  // queued
  p.streams[0].push({ type: 'confirm', question: 'Delete Dentist?', id: 'c1' });
  p.streams[0].end();
  await p.flush();
  assert.equal(p.turns().length, 1);
  await p.wake();  // the server timed the question out: no confirm, not busy
  assert.equal(p.$('confirm').hidden, true);
  assert.deepEqual(p.turns()[1], { url: '/api/turn', body: { text: 'what is next?' } });
});
