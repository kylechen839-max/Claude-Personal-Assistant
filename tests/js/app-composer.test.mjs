// node --test tests/js/app-composer.test.mjs  (Node 18+; no packages needed)
//
// Runs the phone page's kwil/web/app.js in node:vm (the fake DOM of
// app-think.test.mjs) and checks the one-row composer: [+] [field] [mic, or
// Stop while Kwil answers] [Voice conversation, or Send when there is
// something to send]. Send and Voice swap as the field fills and empties
// (picked files and an edit count too); Enter sends, Shift+Enter and an IME
// that is still composing do not; Stop takes the mic's place while busy and
// gives it back for a yes/no question; a long press on Send still thinks
// harder, with the "Think harder is on" chip shown until it is sent; a
// hidden control hands its focus to the field; the iPhone keyboard's
// visualViewport sets --app-h; and the page's markup is in that order.

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

function load({ files = 0, viewport = null } = {}) {
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
    // attachments.js's tray: `files` picked with "+"
    KwilAttach: { tray: { count: () => files, whenReady: async () => {}, summary: () => '[file: a.pdf]',
                          appendTo: (form) => form.append('attachment', new Blob(['x']), 'a.pdf'),
                          clear: () => { files = 0; } } },
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
  if (viewport) {  // iPhone Safari's visualViewport and <html>
    const props = {}, classes = new Set();
    document.documentElement = { style: { setProperty: (k, v) => { props[k] = v; } },
      classList: { toggle: (c, on) => (on ? classes.add(c) : classes.delete(c)) } };
    win.innerHeight = 844;
    win.scrolled = [];
    win.scrollTo = (x, y) => win.scrolled.push([x, y]);
    win.visualViewport = viewport;
    win.rootProps = props; win.rootClasses = classes;
  }
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
  const setFiles = (n) => { files = n; win.KwilApp.syncComposer(); };
  // a key in the text field: {key, shiftKey, isComposing, keyCode}; true if it was taken
  function key(init) {
    let taken = false;
    for (const fn of $('text').listeners.keydown || []) {
      fn({ keyCode: 13, shiftKey: false, isComposing: false, ...init, preventDefault() { taken = true; } });
    }
    return taken;
  }
  async function type(text) {  // what an input event follows
    $('text').value = text;
    await $('text').fire('input');
  }
  const shows = () => ['send', 'voice-open', 'mic', 'stop'].filter((id) => !$(id).hidden);
  return { win, setFiles, key, type, shows, rendered, fireTimers, $, document, fetches, streams, failing, refusing, state, wake, turns, submit, finish, flush, queued, log };
}


test('Send and Voice swap places as the field fills and empties', async () => {
  const p = load();
  await p.flush();
  assert.deepEqual(p.shows(), ['voice-open', 'mic']);  // empty: talk instead
  await p.type('h');
  assert.deepEqual(p.shows(), ['send', 'mic']);
  await p.type('   ');  // only spaces: nothing to send
  assert.deepEqual(p.shows(), ['voice-open', 'mic']);
  await p.type('hello there');
  assert.deepEqual(p.shows(), ['send', 'mic']);
  await p.$('text-form').fire('submit');
  await p.flush();
  assert.equal(p.$('text').value, '');
  assert.equal(p.$('send').hidden, true);  // sent: the field is empty again
  assert.equal(p.$('voice-open').hidden, false);
});

test('Enter sends; Shift+Enter and a composing IME do not', async () => {
  const p = load();
  await p.flush();
  p.$('text').value = '你好';
  assert.equal(p.key({ key: 'Enter', isComposing: true }), false);  // picking pinyin
  assert.equal(p.key({ key: 'Enter', keyCode: 229 }), false);  // Safari's IME Enter
  assert.equal(p.key({ key: 'Enter', shiftKey: true }), false);  // a new line
  assert.equal(p.key({ key: 'a', keyCode: 65 }), false);
  await p.flush();
  assert.equal(p.turns().length, 0);
  assert.equal(p.key({ key: 'Enter' }), true);
  await p.flush();
  assert.deepEqual(p.turns().map((t) => t.body), [{ text: '你好' }]);
  assert.equal(p.$('text').value, '');
  // Enter in an empty field sends nothing
  await p.finish(p.streams[0], { type: 'reply', text: 'Hi.' });
  assert.equal(p.key({ key: 'Enter' }), true);
  await p.flush();
  assert.equal(p.turns().length, 1);
});

test('busy shows Stop instead of the mic; a yes/no question brings the mic back', async () => {
  const p = load();
  await p.flush();
  await p.submit('delete my dentist appointment');
  assert.deepEqual(p.shows(), ['voice-open', 'stop']);
  await p.type('and lunch');  // typing while it answers: Send queues it
  assert.deepEqual(p.shows(), ['send', 'stop']);
  await p.type('');
  p.streams[0].push({ type: 'confirm', question: 'Delete Dentist?', id: 'c1' });
  await p.flush();
  assert.deepEqual(p.shows(), ['voice-open', 'mic']);  // "hold the mic and say yes"
  assert.equal(p.$('mic').disabled, false);
  await p.finish(p.streams[0]);
  assert.deepEqual(p.shows(), ['voice-open', 'mic']);
  assert.equal(p.$('stop').hidden, true);
});

test('files alone show Send, never for a yes/no answer', async () => {
  const p = load();
  await p.flush();
  p.setFiles(1);
  assert.deepEqual(p.shows(), ['send', 'mic']);
  await p.$('text-form').fire('submit');
  await p.flush();
  const sent = p.fetches.filter((f) => f.url === '/api/turn');
  assert.equal(sent.length, 1);
  assert.equal(sent[0].opts.body.get('attachment').name, 'a.pdf');  // the file alone
  p.streams[0].push({ type: 'confirm', question: 'Delete Dentist?', id: 'c1' });
  await p.flush();
  p.setFiles(2);
  assert.equal(p.$('send').hidden, true);  // files never go with an answer
  p.setFiles(0);
  assert.equal(p.$('send').hidden, true);
});

test('editing the last message shows Send', async () => {
  const p = load();
  await p.flush();
  await p.submit('wake me at 7');
  p.streams[0].push({ type: 'reply', text: 'Done.' }, { type: 'done', redo: true });
  p.streams[0].end();
  await p.flush();
  const me = p.$('log').children.find((c) => c.className === 'msg me');
  const pencil = me.children.find((c) => c.className === 'msg-edit icon-btn');
  await pencil.fire('click');
  assert.equal(p.$('text').value, 'wake me at 7');
  assert.equal(p.$('send').hidden, false);
  await p.$('edit-cancel').fire('click');
  assert.equal(p.$('text').value, '');
  assert.equal(p.$('send').hidden, true);
});

test('a long press on Send still thinks harder; the chip shows until it is sent', async () => {
  const p = load();
  await p.flush();
  const send = p.$('send'), chip = p.$('think-chip');  // hidden in the markup
  await p.type('plan my week');
  await send.fire('pointerdown');
  p.fireTimers();  // held past the long press
  assert.equal(chip.hidden, false);
  assert.equal(p.$('think').attrs['aria-pressed'], 'true');
  await send.fire('pointerup');
  await send.fire('click');
  await p.flush();
  assert.deepEqual(p.turns().map((t) => t.body), [{ text: 'plan my week', think: true }]);
  assert.equal(chip.hidden, true);
  // the sheet's Think harder row turns it on; tapping the chip turns it off
  await p.$('think').fire('click');
  assert.equal(chip.hidden, false);
  chip.focus();
  await chip.fire('click');
  assert.equal(chip.hidden, true);
  assert.equal(p.$('think').attrs['aria-pressed'], 'false');
  assert.equal(p.document.activeElement, p.$('text'));  // not left on a hidden chip
  await p.finish(p.streams[0], { type: 'reply', text: 'Here goes.' });
  await p.submit('thanks');
  assert.deepEqual(p.turns()[1].body, { text: 'thanks' });
});

test('a control that hides while focused hands focus to the field', async () => {
  const p = load();
  await p.flush();
  await p.type('hi');
  p.$('send').focus();
  await p.$('text-form').fire('submit');
  await p.flush();
  assert.equal(p.$('send').hidden, true);
  assert.equal(p.document.activeElement, p.$('text'));
  p.$('voice-open').focus();
  await p.type('more');
  assert.equal(p.document.activeElement, p.$('text'));
});

test('the iPhone keyboard: --app-h follows the visible height, kb-open, no page scroll', async () => {
  const listeners = {};
  const vv = { height: 844, offsetTop: 0, addEventListener: (t, fn) => { (listeners[t] ??= []).push(fn); } };
  const p = load({ viewport: vv });
  await p.flush();
  assert.equal(p.win.rootProps['--app-h'], '844px');
  assert.equal(p.win.rootClasses.has('kb-open'), false);
  vv.height = 500; vv.offsetTop = 120;  // the keyboard opened and Safari scrolled the page
  for (const fn of listeners.resize) fn();
  assert.equal(p.win.rootProps['--app-h'], '500px');
  assert.equal(p.win.rootClasses.has('kb-open'), true);
  assert.deepEqual(p.win.scrolled.at(-1), [0, 0]);
  vv.height = 800; vv.offsetTop = 0;  // closed (a toolbar's worth less: not a keyboard)
  for (const fn of listeners.resize) fn();
  assert.equal(p.win.rootClasses.has('kb-open'), false);
});

test('the page: one row in order, every button but Send is type=button, Think and Study in the sheet', () => {
  const footer = HTML.slice(HTML.indexOf('<footer>'), HTML.indexOf('</footer>'));
  const form = footer.slice(footer.indexOf('<form id="text-form"'), footer.indexOf('</form>'));
  const ids = [...form.matchAll(/<(?:button|textarea|input)[^>]* id="([^"]+)"/g)].map((m) => m[1]);
  assert.deepEqual(ids, ['attach', 'text', 'stop', 'mic', 'voice-open', 'send']);
  for (const m of form.matchAll(/<button [^>]*>/g)) {
    if (m[0].includes('id="send"')) assert.match(m[0], /type="submit"/);
    else assert.match(m[0], /type="button"/, m[0]);
  }
  assert.match(form, /<textarea id="text" rows="1" enterkeyhint="send" aria-label="Message" placeholder="Type a message">/);
  assert.match(form, /<button id="send" type="submit"[^>]* hidden>/);  // empty at first: Voice shows
  assert.match(form, /<button id="voice-open" class="voice-open-btn" type="button" aria-label="Voice conversation"/);
  // the hint and meter only while recording
  assert.match(footer, /<div id="mic-meter" class="mic-meter" hidden/);
  assert.match(footer, /<p class="hint" id="mic-hint" role="status" hidden>/);
  // the modes line above the row: project, study, think chip
  const modes = footer.slice(footer.indexOf('<div id="composer-modes"'), footer.indexOf('<form id="text-form"'));
  for (const id of ['project-pill', 'project-pill-text', 'project-leave', 'study-pill', 'study-pill-text', 'study-stop']) {
    assert.ok(modes.includes(`id="${id}"`), id);
  }
  assert.match(modes, /<button id="think-chip" [^>]*type="button"[^>]* hidden>/);
  assert.ok(footer.indexOf('id="attach-chips"') < footer.indexOf('id="composer-modes"'));
  // the header keeps none of them
  const header = HTML.slice(HTML.indexOf('<main id="app"'), HTML.indexOf('</header>', HTML.indexOf('<main id="app"')));
  for (const id of ['project-pill', 'study-pill', 'study-toggle', 'think']) assert.ok(!header.includes(`id="${id}"`), id);
  // the "+" sheet: pickers, then Think harder and Study mode (they close it), then Cancel
  const sheet = HTML.slice(HTML.indexOf('<dialog id="attach-sheet"'), HTML.indexOf('</dialog>', HTML.indexOf('<dialog id="attach-sheet"')));
  const order = ['data-pick="camera"', 'data-pick="library"', 'data-pick="file"', 'id="think"', 'id="study-toggle"', 'class="cancel"']
    .map((s) => sheet.indexOf(s));
  assert.ok(order.every((i, n) => i > 0 && (n === 0 || i > order[n - 1])), String(order));
  assert.match(sheet, /<button id="think" class="think-btn" type="button" aria-pressed="false" aria-label="Think harder"[^>]* data-close>/);
  assert.match(sheet, /<button id="study-toggle" [^>]*type="button"[^>]* data-close hidden>/);
});
