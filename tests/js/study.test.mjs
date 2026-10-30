// node --test tests/js/study.test.mjs  (Node 18+; no packages needed)
//
// Runs the phone page's kwil/web/study.js in node:vm with a minimal fake
// DOM: the chat header's study button shows only with {"study_mode": true};
// tapping it POSTs /api/study {"on": true} and the "Studying" pill takes its
// place; a turn's "done" event redraws it ("Studying PHYS 361" when Kwil
// started it, gone when the event has no "study"); the pill's × POSTs
// {"on": false}. The course goes in as text, never HTML, and every fixed
// string has Chinese in i18n.js. app.js wires it up.

import test from 'node:test';
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import vm from 'node:vm';

const SCRIPT = readFileSync(new URL('../../kwil/web/study.js', import.meta.url), 'utf8');
const I18N = readFileSync(new URL('../../kwil/web/i18n.js', import.meta.url), 'utf8');
const HTML = readFileSync(new URL('../../kwil/web/index.html', import.meta.url), 'utf8');
const APP = readFileSync(new URL('../../kwil/web/app.js', import.meta.url), 'utf8');

class El {
  constructor(tag, id, hidden = false) {
    this.tagName = tag.toUpperCase(); this.id = id; this.hidden = hidden;
    this._text = ''; this.children = []; this.listeners = {}; this.attrs = {};
  }
  get textContent() { return this._text + this.children.map((c) => c.textContent).join(''); }
  set textContent(v) { this._text = String(v); this.children = []; }
  set innerHTML(v) { throw new Error('never innerHTML'); }
  setAttribute(k, v) { this.attrs[k] = String(v); }
  getAttribute(k) { return k in this.attrs ? this.attrs[k] : null; }
  addEventListener(type, fn) { (this.listeners[type] ??= []).push(fn); }
  async fire(type) { for (const fn of this.listeners[type] || []) await fn({ preventDefault() {} }); }
}

const IDS = ['study-toggle', 'study-pill', 'study-pill-text', 'study-stop'];

function load({ status = 200, reply } = {}) {
  const els = Object.fromEntries(IDS.map((id) => [id, new El(id === 'study-pill-text' ? 'span' : 'button', id, id !== 'study-pill-text')]));
  const fetches = [];
  const notes = [];
  const win = {
    fetch: async (url, opts = {}) => {
      fetches.push({ url, opts, body: opts.body ? JSON.parse(opts.body) : null });
      const on = fetches.at(-1).body && fetches.at(-1).body.on;
      const body = reply ?? (status < 300 ? { study: on ? { course: '', topic: '' } : null }
        : { error: 'not found' });
      return { ok: status < 300, status, json: async () => body };
    },
    document: { getElementById: (id) => els[id] ?? null },
    JSON, String, Boolean, Error,
  };
  win.window = win;
  vm.createContext(win);
  vm.runInContext(SCRIPT, win);
  win.KwilStudy.configure({ note: (t) => notes.push(t) });
  return { win, els, fetches, notes, api: win.KwilStudy };
}

const state = (t, s) => t.api.onState(s, t.win, t.win.document);

test('the button shows only with study mode, and the pill only while on', () => {
  const t = load();
  state(t, { study_mode: false, study: null });
  assert.equal(t.els['study-toggle'].hidden, true);
  assert.equal(t.els['study-pill'].hidden, true);
  state(t, { study_mode: true, study: null });
  assert.equal(t.els['study-toggle'].hidden, false);
  assert.equal(t.els['study-toggle'].getAttribute('aria-pressed'), 'false');
  assert.equal(t.els['study-pill'].hidden, true);
  state(t, { study_mode: true, study: { course: 'PHYS 361', topic: '' } });
  assert.equal(t.els['study-toggle'].hidden, true);
  assert.equal(t.els['study-pill'].hidden, false);
  assert.equal(t.els['study-pill-text'].textContent, 'Studying PHYS 361');
  state(t, { study_mode: false, study: { course: 'PHYS 361', topic: '' } });  // turned off in config
  assert.equal(t.els['study-pill'].hidden, true);
});

test('the toggle turns it on, the × turns it off', async () => {
  const t = load();
  state(t, { study_mode: true, study: null });
  await t.els['study-toggle'].fire('click');
  assert.deepEqual(t.fetches.map((f) => [f.url, f.opts.method, f.body]),
    [['/api/study', 'POST', { on: true }]]);
  assert.equal(t.els['study-pill'].hidden, false);
  assert.equal(t.els['study-pill-text'].textContent, 'Studying');
  assert.equal(t.els['study-toggle'].getAttribute('aria-pressed'), 'true');
  assert.deepEqual(t.notes, [t.api.TEXT.on]);
  await t.els['study-stop'].fire('click');
  assert.deepEqual(t.fetches.at(-1).body, { on: false });
  assert.equal(t.els['study-pill'].hidden, true);
  assert.equal(t.els['study-toggle'].hidden, false);
  assert.deepEqual(t.notes, [t.api.TEXT.on, t.api.TEXT.off]);
});

test('a turn\'s done event redraws the pill; the course is text, never HTML', () => {
  const t = load();
  state(t, { study_mode: true, study: null });
  t.api.onDone({ type: 'done', conversation: 'c1', study: { course: '<img src=x onerror=alert(1)>', topic: '' } },
    t.win, t.win.document);
  assert.equal(t.els['study-pill'].hidden, false);
  assert.equal(t.els['study-pill-text'].textContent, 'Studying <img src=x onerror=alert(1)>');
  t.api.onDone({ type: 'done', conversation: 'c1', study: { course: '', topic: 'the midterm' } },
    t.win, t.win.document);
  assert.equal(t.els['study-pill-text'].textContent, 'Studying the midterm');
  t.api.onDone({ type: 'done', conversation: 'c1' }, t.win, t.win.document);  // "stop studying"
  assert.equal(t.els['study-pill'].hidden, true);
});

test('a refused toggle says why and leaves the header as it was', async () => {
  const t = load({ status: 404 });
  state(t, { study_mode: true, study: null });
  await t.els['study-toggle'].fire('click');
  assert.equal(t.els['study-pill'].hidden, true);
  assert.deepEqual(t.notes, ['not found']);
});

test('every fixed string has Chinese, and the page and app.js wire it up', () => {
  const ctx = { console };
  vm.createContext(ctx);
  vm.runInContext(I18N, ctx);
  const tr = ctx.KwilI18n.translate;
  const t = load();
  for (const s of [...Object.values(t.api.TEXT), t.api.label({ course: 'PHYS 361' })]) {
    const zh = tr(s);
    assert.notEqual(zh, s, s);
    assert.match(zh, /[一-鿿]/, s);
  }
  assert.equal(tr('Studying PHYS 361'), '正在学习 PHYS 361');
  for (const id of IDS) assert.ok(HTML.includes(`id="${id}"`), id);
  assert.ok(HTML.includes('aria-label="Study mode"') && HTML.includes('aria-label="Stop studying"'));
  assert.ok(HTML.indexOf('/study.js') < HTML.indexOf('/app.js'));
  assert.match(APP, /KwilStudy\.onState\(state\)/);
  assert.match(APP, /KwilStudy\.onDone\(ev\)/);
});
