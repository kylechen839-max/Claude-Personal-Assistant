// node --test tests/js/app-think.test.mjs  (Node 18+; no packages needed)
//
// Runs the phone page's kwil/web/app.js in node:vm (the fake DOM of
// app-queue.test.mjs) and checks "think harder" (thinkhard.py): the toggle
// left of Send and a long press on Send ask for it on the next message
// only ({"think": true} on /api/turn), never sticky, a queued message keeps
// its own, a yes/no answer never carries it; and a reply's citations go to
// sources.js (KwilSources.render) with the reply's own words.

import test from 'node:test';
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import vm from 'node:vm';

const SCRIPT = readFileSync(new URL('../../kwil/web/app.js', import.meta.url), 'utf8');
const HTML = readFileSync(new URL('../../kwil/web/index.html', import.meta.url), 'utf8');

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
  const timers = [];
  const rendered = [];
  const win = {
    document,
    Audio: class { play() { return Promise.resolve(); } pause() {} },
    localStorage: { getItem: () => null, setItem() {} },
    navigator: {},
    location: { hash: '', pathname: '/', origin: 'https://pc.example' },
    history: { replaceState() {} },
    FormData, Blob, TextDecoder, console,
    // the long press's timer fires when the test says (timers.fire())
    setTimeout: (fn, ms) => { if (ms === 500) { timers.push(fn); return timers.length; } return setTimeout(fn, ms); },
    clearTimeout: (id) => { if (typeof id === 'number' && id <= timers.length) timers[id - 1] = null; else clearTimeout(id); },
    KwilSources: { render: (...args) => rendered.push(args), speakable: (t) => t },
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
  const fireTimers = () => { for (const fn of timers.splice(0)) if (fn) fn(); };
  return { rendered, fireTimers, $, document, fetches, streams, failing, refusing, state, wake, turns, submit, finish, flush, queued, log };
}


test('the toggle asks for one think turn; the next message is ordinary', async () => {
  const p = load();
  await p.flush();
  const think = p.$('think');
  await think.fire('click');
  assert.equal(think.attrs['aria-pressed'], 'true');
  await p.submit('why is the sky blue?');
  assert.deepEqual(p.turns()[0].body, { text: 'why is the sky blue?', think: true });
  assert.equal(think.attrs['aria-pressed'], 'false');  // off again at once
  assert.equal(p.$('status').textContent, 'Thinking harder…');
  await p.finish(p.streams[0], { type: 'reply', text: 'Rayleigh scattering.', think: true });
  await p.submit('and sunsets?');
  assert.deepEqual(p.turns()[1].body, { text: 'and sunsets?' });
  // pressed twice: off
  await think.fire('click');
  await think.fire('click');
  await p.finish(p.streams[1], { type: 'reply', text: 'Longer path.' });
  await p.submit('ok');
  assert.deepEqual(p.turns()[2].body, { text: 'ok' });
});

test('a long press on Send thinks harder about that message and sends it', async () => {
  const p = load();
  await p.flush();
  const send = p.$('send');
  p.$('text').value = 'plan my week';
  await send.fire('pointerdown');
  p.fireTimers();  // held past the long press
  assert.equal(p.$('think').attrs['aria-pressed'], 'true');
  await send.fire('pointerup');
  await send.fire('click');  // the click after a long press sends nothing more
  await p.flush();
  assert.deepEqual(p.turns().map((t) => t.body), [{ text: 'plan my week', think: true }]);
  await p.finish(p.streams[0], { type: 'reply', text: 'Here goes.' });
  // a short tap is an ordinary Send (the form's submit)
  p.$('text').value = 'thanks';
  await send.fire('pointerdown');
  await send.fire('pointerup');
  await p.$('text-form').fire('submit');
  await p.flush();
  assert.deepEqual(p.turns()[1].body, { text: 'thanks' });
});

test('a queued message keeps its own flag; an answer never carries one', async () => {
  const p = load();
  await p.flush();
  await p.submit('first');
  await p.$('think').fire('click');
  await p.submit('second, thought through');  // queued while busy
  assert.equal(p.$('think').attrs['aria-pressed'], 'false');
  await p.finish(p.streams[0], { type: 'reply', text: 'One.' });
  assert.deepEqual(p.turns()[1].body, { text: 'second, thought through', think: true });
  p.streams[1].push({ type: 'confirm', question: 'Delete Dentist?', id: 'c1' });
  await p.flush();
  await p.$('think').fire('click');
  await p.submit('yes');
  assert.deepEqual(p.turns()[2], { url: '/api/confirm', body: { answer: 'yes' } });
  assert.equal(p.$('think').attrs['aria-pressed'], 'false');
});

test("a reply's citations go to sources.js with its words", async () => {
  const p = load();
  await p.flush();
  await p.submit('search the web for the exam date');
  const cites = { sources: [{ kind: 'web', url: 'https://a.example/', title: 'A' }],
                  marks: [[8, [1]]] };
  await p.finish(p.streams[0], { type: 'delta', text: 'Friday.' },
                 { type: 'reply', text: 'Friday!!', citations: cites });
  assert.equal(p.rendered.length, 1);
  const [, bubble, text, given, links] = p.rendered[0];
  assert.equal(bubble.className, 'msg claude');
  assert.equal(text, 'Friday!!');
  assert.equal(JSON.stringify(given), JSON.stringify(cites));  // (another realm's objects)
  assert.equal(typeof links.openConversation, 'function');
  await p.submit('thanks');
  await p.finish(p.streams[1], { type: 'reply', text: 'Sure.' });
  assert.equal(p.rendered.length, 1);  // none without citations
});

test('the page has the toggle, a 44px Send to hold, and loads sources.js before app.js', () => {
  assert.match(HTML, /<button id="think" class="think-btn" type="button" aria-pressed="false" aria-label="Think harder"/);
  assert.match(HTML, /<button id="send" type="submit"/);
  assert.ok(HTML.indexOf('/sources.js') > 0 && HTML.indexOf('/sources.js') < HTML.indexOf('/app.js'));
});
