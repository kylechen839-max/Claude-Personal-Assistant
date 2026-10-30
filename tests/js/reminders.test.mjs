// node --test tests/js/reminders.test.mjs  (Node 18+; no packages needed)
//
// Runs the phone page's kwil/web/reminders.js in node:vm with a minimal
// fake DOM: "Reminders" shows in the menu only with {"reminders": true};
// opening it loads /api/reminders and lists the active ones (text, next
// time and repeat as the server worded them, "Task" and "Untrusted"
// badges) and the missed ones; Pause/Resume POST .../pause; Delete asks
// first ("no" sends nothing) and POSTs .../delete; a notification's tap
// (/#reminders, or sw.js's message) opens it. Text goes in as text, never
// HTML, and every fixed string has Chinese in i18n.js.

import test from 'node:test';
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import vm from 'node:vm';

const SCRIPT = readFileSync(new URL('../../kwil/web/reminders.js', import.meta.url), 'utf8');
const I18N = readFileSync(new URL('../../kwil/web/i18n.js', import.meta.url), 'utf8');
const HTML = readFileSync(new URL('../../kwil/web/index.html', import.meta.url), 'utf8');

class El {
  constructor(tag, id, hidden = false) {
    this.tagName = tag.toUpperCase(); this.id = id; this.hidden = hidden;
    this.className = ''; this._text = ''; this.children = []; this.listeners = {};
    this.dataset = {}; this.attrs = {}; this.open = false; this.type = '';
  }
  get textContent() { return this._text + this.children.map((c) => c.textContent).join(''); }
  set textContent(v) { this._text = String(v); this.children = []; }
  setAttribute(k, v) { this.attrs[k] = String(v); }
  getAttribute(k) { return k in this.attrs ? this.attrs[k] : null; }
  appendChild(c) { this.children.push(c); return c; }
  addEventListener(type, fn) { (this.listeners[type] ??= []).push(fn); }
  async fire(type, e = {}) { for (const fn of this.listeners[type] || []) await fn({ preventDefault() {}, ...e }); }
  all(cls) {
    const out = [];
    for (const c of this.children) {
      if (String(c.className).split(' ').includes(cls)) out.push(c);
      out.push(...c.all(cls));
    }
    return out;
  }
}

const DATA = {
  limits: { active: 100, text: 300 }, notifications: true,
  reminders: [
    { id: 'rem-aaaaaaaaaaaa', text: '<b>Submit</b> HW7', kind: 'once', state: 'active', paused: false,
      untrusted: false, prompt_task: false, next_run: '2026-10-31T01:00:00Z',
      next_local: 'today at 9:00 PM', repeat: '' },
    { id: 'rem-bbbbbbbbbbbb', text: "Tell me what's due this week", kind: 'recurring', state: 'active',
      paused: true, untrusted: true, prompt_task: true, next_run: '2026-11-02T13:00:00Z',
      next_local: 'Monday at 8:00 AM', repeat: 'every Monday at 8:00 AM' },
    { id: 'rem-cccccccccccc', text: 'Call the dentist', kind: 'once', state: 'missed', paused: false,
      untrusted: false, prompt_task: false, next_run: null, next_local: '', repeat: '' },
  ],
};

const IDS = ['reminders-view', 'reminders-open', 'reminders-back', 'reminders-items',
  'reminders-empty', 'reminders-missed-title', 'reminders-missed', 'reminders-push',
  'reminders-status'];

function load({ answer = true, responses = {}, hash = '' } = {}) {
  const els = Object.fromEntries(IDS.map((id) => [id, new El('div', id,
    ['reminders-view', 'reminders-open', 'reminders-empty', 'reminders-push'].includes(id))]));
  const menu = new El('details', 'menu');
  menu.open = true;
  const fetches = [], asked = [], shown = [], winListeners = {}, swListeners = {};
  const win = {
    fetch: async (url, opts = {}) => {
      fetches.push({ url, opts });
      const r = responses[url] ?? (url === '/api/reminders' ? { status: 200, body: DATA }
        : { status: 200, body: { ok: true } });
      return { ok: r.status < 300, status: r.status, json: async () => r.body };
    },
    confirm: (q) => { asked.push(q); return answer; },
    location: { hash, pathname: '/' },
    history: { replaceState: (a, b, url) => { win.location.hash = ''; } },
    addEventListener: (type, fn) => { (winListeners[type] ??= []).push(fn); },
    navigator: { serviceWorker: { addEventListener: (type, fn) => { (swListeners[type] ??= []).push(fn); } } },
    document: {
      getElementById: (id) => els[id] ?? null,
      querySelector: (sel) => (sel === 'details.menu' ? menu : null),
      createElement: (tag) => new El(tag),
    },
    JSON, encodeURIComponent, Error, String, Boolean, Object,
  };
  win.window = win;
  vm.createContext(win);
  vm.runInContext(SCRIPT, win);
  win.KwilReminders.configure({ show: (v) => shown.push(v) });
  return { win, els, menu, fetches, asked, shown, winListeners, swListeners, api: win.KwilReminders };
}

async function open(t, state = { reminders: true }) {
  await t.api.onState(state, t.win, t.win.document);
  await t.els['reminders-open'].fire('click');
}

const posted = (t, url) => t.fetches.filter((f) => f.url === url && f.opts.method === 'POST');

test('the menu row shows only with reminders on', async () => {
  const t = load();
  await t.api.onState({ reminders: false }, t.win, t.win.document);
  assert.equal(t.els['reminders-open'].hidden, true);
  await t.els['reminders-open'].fire('click');
  assert.deepEqual(t.fetches, []);  // off: opening does nothing
  await t.api.onState({ reminders: true }, t.win, t.win.document);
  assert.equal(t.els['reminders-open'].hidden, false);
});

test('opening lists active and missed reminders with their times, repeats and badges', async () => {
  const t = load();
  await open(t);
  assert.deepEqual(t.fetches.map((f) => f.url), ['/api/reminders']);
  assert.deepEqual(t.shown, ['reminders-view']);
  assert.equal(t.menu.open, false);
  const rows = t.els['reminders-items'].all('rem-item');
  assert.deepEqual(rows.map((r) => r.dataset.id), ['rem-aaaaaaaaaaaa', 'rem-bbbbbbbbbbbb']);
  assert.equal(rows[0].all('rem-when')[0].textContent, 'today at 9:00 PM');
  assert.equal(rows[0].all('mem-text')[0].textContent, '<b>Submit</b> HW7');  // text, never HTML
  assert.equal(rows[0].all('mem-text')[0].children.length, 0);
  assert.equal(rows[0].all('badge').length, 0);
  assert.equal(rows[0].all('rem-repeat').length, 0);
  assert.equal(rows[0].all('rem-pause')[0].textContent, 'Pause');
  assert.equal(rows[1].all('rem-when')[0].textContent, 'Paused');
  assert.equal(rows[1].all('rem-repeat')[0].textContent, 'every Monday at 8:00 AM');
  assert.deepEqual(rows[1].all('badge').map((b) => b.textContent), ['Task', 'Untrusted']);
  assert.equal(rows[1].all('rem-pause')[0].textContent, 'Resume');
  const missed = t.els['reminders-missed'].all('rem-item');
  assert.deepEqual(missed.map((r) => r.dataset.id), ['rem-cccccccccccc']);
  assert.equal(missed[0].all('rem-when')[0].textContent, 'Missed');
  assert.equal(missed[0].all('rem-pause').length, 0);  // only Delete
  assert.equal(t.els['reminders-missed'].hidden, false);
  assert.equal(t.els['reminders-missed-title'].hidden, false);
  assert.equal(t.els['reminders-empty'].hidden, true);
  assert.equal(t.els['reminders-push'].hidden, true);
});

test('an empty list says how to fill it; notifications off says so', async () => {
  const t = load({ responses: { '/api/reminders': { status: 200,
    body: { ...DATA, reminders: [], notifications: false } } } });
  await open(t);
  assert.equal(t.els['reminders-empty'].hidden, false);
  assert.equal(t.els['reminders-empty'].textContent, t.api.TEXT.empty);
  assert.equal(t.els['reminders-missed'].hidden, true);
  assert.equal(t.els['reminders-missed-title'].hidden, true);
  assert.equal(t.els['reminders-push'].hidden, false);
});

test('Pause and Resume POST the new state and reload', async () => {
  const t = load();
  await open(t);
  const rows = t.els['reminders-items'].all('rem-item');
  await rows[0].all('rem-pause')[0].fire('click');
  const [sent] = posted(t, '/api/reminders/rem-aaaaaaaaaaaa/pause');
  assert.deepEqual(JSON.parse(sent.opts.body), { paused: true });
  assert.equal(t.fetches.at(-1).url, '/api/reminders');
  assert.equal(t.els['reminders-status'].textContent, 'Reminder paused.');
  await t.els['reminders-items'].all('rem-item')[1].all('rem-pause')[0].fire('click');
  const [resumed] = posted(t, '/api/reminders/rem-bbbbbbbbbbbb/pause');
  assert.deepEqual(JSON.parse(resumed.opts.body), { paused: false });
  assert.equal(t.els['reminders-status'].textContent, 'Reminder on.');
});

test('Delete asks first; no sends nothing; yes POSTs and reloads', async () => {
  for (const answer of [false, true]) {
    const t = load({ answer });
    await open(t);
    await t.els['reminders-items'].all('rem-item')[0].all('rem-delete')[0].fire('click');
    assert.deepEqual([...t.asked], [t.api.TEXT.ask]);
    const sent = posted(t, '/api/reminders/rem-aaaaaaaaaaaa/delete');
    if (!answer) { assert.equal(sent.length, 0); continue; }
    assert.equal(sent.length, 1);
    assert.equal(t.fetches.at(-1).url, '/api/reminders');
    assert.equal(t.els['reminders-status'].textContent, 'Deleted.');
  }
});

test('an error is shown, not thrown', async () => {
  const t = load({ responses: { '/api/reminders/rem-aaaaaaaaaaaa/delete':
    { status: 404, body: { error: 'not found' } } } });
  await open(t);
  await t.els['reminders-items'].all('rem-item')[0].all('rem-delete')[0].fire('click');
  assert.equal(t.els['reminders-status'].textContent, 'not found');
});

test('a tapped notification opens the list: the link, or sw.js\'s message', async () => {
  const t = load({ hash: '#reminders' });
  await t.api.onState({ reminders: true }, t.win, t.win.document);
  assert.deepEqual(t.fetches.map((f) => f.url), ['/api/reminders']);
  await t.api.onState({ reminders: true }, t.win, t.win.document);  // only the first state
  assert.equal(t.fetches.length, 1);
  await t.swListeners.message[0]({ data: { type: 'open', url: '/#reminders' } });
  assert.equal(t.fetches.length, 2);
  await t.swListeners.message[0]({ data: { type: 'open', url: '/#brief' } });  // brief.js's
  assert.equal(t.fetches.length, 2);
  await t.els['reminders-back'].fire('click');
  assert.equal(t.win.location.hash, '');
  assert.equal(t.shown.at(-1), 'app');
});

test('the page has the view, the menu row and the script', () => {
  for (const id of IDS) assert.match(HTML, new RegExp(`id="${id}"`), id);
  assert.match(HTML, /<script src="\/reminders\.js" defer><\/script>/);
  const sw = readFileSync(new URL('../../kwil/web/sw.js', import.meta.url), 'utf8');
  assert.match(sw, /"\/reminders\.js"/);
  const app = readFileSync(new URL('../../kwil/web/app.js', import.meta.url), 'utf8');
  assert.match(app, /"reminders-view"/);
  assert.match(app, /KwilReminders\.onState\(state\)/);
});

test('every fixed string has Chinese', () => {
  const ctx = { console };
  vm.createContext(ctx);
  vm.runInContext(I18N, ctx);
  const tr = ctx.KwilI18n.translate;
  const t = load();
  const strings = [...Object.values(t.api.TEXT),
    'Reminders', 'Active', 'Missed', 'Turn on notifications in Settings so reminders can arrive.',
    "Say “remind me at 9 to …” or “every Monday at 8, tell me what's due”. Reminders arrive as notifications on this phone.",
    "couldn't reach your reminders; try again"];
  for (const s of strings) {
    const zh = tr(s);
    assert.notEqual(zh, s, s);
    assert.match(zh, /[一-鿿]/, s);
  }
});
