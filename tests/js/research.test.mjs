// node --test tests/js/research.test.mjs  (Node 18+; no packages needed)
//
// Runs the phone page's kwil/web/research.js in node:vm with a minimal fake
// DOM: the research card shows only with {"research": true}, says
// "Researching… (3/10 searches)" while a job runs and asks the server again
// on a timer (and stops once nothing runs), Cancel asks first and POSTs the
// cancel, a finished report opens its History transcript and offers Save to
// Notion only when the server says it is connected, a dismissed card stays
// gone, a notification's /#research=<id> opens the report (a bad id never
// does), the question goes in as text, and every fixed string has Chinese.

import test from 'node:test';
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import vm from 'node:vm';

const SCRIPT = readFileSync(new URL('../../kwil/web/research.js', import.meta.url), 'utf8');
const I18N = readFileSync(new URL('../../kwil/web/i18n.js', import.meta.url), 'utf8');
const HTML = readFileSync(new URL('../../kwil/web/index.html', import.meta.url), 'utf8');
const APP = readFileSync(new URL('../../kwil/web/app.js', import.meta.url), 'utf8');
const SW = readFileSync(new URL('../../kwil/web/sw.js', import.meta.url), 'utf8');
const CID = 'AbCdEfGhIjKlMnOpQr12';
const ICON = '<svg viewBox="0 0 24 24" aria-hidden="true"><path d="M6 6l12 12M18 6L6 18"/></svg>';

class El {
  constructor(tag, id = '') {
    this.tagName = tag.toUpperCase(); this.id = id; this.hidden = false; this.disabled = false;
    this._text = ''; this.children = []; this.listeners = {}; this.attrs = {}; this.className = '';
  }
  get textContent() { return this._text + this.children.map((c) => c.textContent).join(''); }
  set textContent(v) { this._text = String(v); this.children = []; }
  set innerHTML(v) { assert.equal(v, ICON, 'only the static icon goes in as HTML'); this._html = v; }
  appendChild(c) { this.children.push(c); return c; }
  setAttribute(k, v) { this.attrs[k] = String(v); }
  getAttribute(k) { return k in this.attrs ? this.attrs[k] : null; }
  addEventListener(type, fn) { (this.listeners[type] ??= []).push(fn); }
  async fire(type) {
    for (const fn of this.listeners[type] || []) await fn({ currentTarget: this, preventDefault() {} });
  }
  all() { return [this, ...this.children.flatMap((c) => c.all())]; }
  find(cls) { return this.all().find((e) => String(e.className).split(' ').includes(cls)) || null; }
}

function job(over = {}) {
  return { id: 'res-0123456789abcdef', question: 'Best physics internships', depth: 'standard',
           status: 'researching', searches: 3, max_searches: 10, fetches: 1, max_fetches: 10,
           conversation_id: null, notion: false, notion_url: '', partial: '', error: '', ...over };
}

function load({ jobs = [job()], confirm = true, hash = '' } = {}) {
  const box = new El('section', 'research-cards');
  box.hidden = true;
  const fetches = [], timers = [], opened = [], stored = {}, replaced = [];
  const listeners = {}, swListeners = {};
  const win = {
    fetch: async (url, opts = {}) => {
      fetches.push({ url, method: opts.method || 'GET' });
      if (url.endsWith('/notion')) return { ok: true, status: 200, json: async () => ({ ok: true, status: 'saved', url: 'https://notion.so/p1' }) };
      if (url.endsWith('/cancel')) return { ok: true, status: 200, json: async () => ({ ok: true }) };
      return { ok: true, status: 200, json: async () => ({ jobs: win.jobs }) };
    },
    jobs,
    setTimeout: (fn, ms) => { timers.push({ fn, ms }); return timers.length; },
    clearTimeout: (id) => { if (timers[id - 1]) timers[id - 1].cleared = true; },
    confirm: () => confirm,
    localStorage: { getItem: (k) => stored[k] ?? null, setItem: (k, v) => { stored[k] = v; } },
    location: { hash },
    history: { replaceState: (...a) => replaced.push(a) },
    addEventListener: (type, fn) => { listeners[type] = fn; },
    navigator: { serviceWorker: { addEventListener: (type, fn) => { swListeners[type] = fn; } } },
    document: { getElementById: (id) => (id === 'research-cards' ? box : null), createElement: (t) => new El(t) },
    JSON, String, Boolean, Error, Number, Array, URL,
  };
  win.window = win;
  vm.createContext(win);
  vm.runInContext(SCRIPT, win);
  win.KwilResearch.configure({ openConversation: (id) => opened.push(id) });
  return { win, box, fetches, timers, opened, stored, replaced, listeners, swListeners, api: win.KwilResearch };
}

const tick = () => new Promise((r) => setImmediate(r));
async function state(t, s) { t.api.onState(s, t.win, t.win.document); await tick(); await tick(); }

test('the status line for each state', () => {
  const t = load();
  assert.equal(t.api.statusText(job()), 'Researching… (3/10 searches)');
  assert.equal(t.api.statusText(job({ status: 'queued' })), t.api.TEXT.queued);
  assert.equal(t.api.statusText(job({ status: 'writing' })), t.api.TEXT.writing);
  assert.equal(t.api.statusText(job({ status: 'partial' })), t.api.TEXT.partial);
  assert.equal(t.api.statusText(job({ status: 'failed', error: 'restarted' })), t.api.TEXT.restarted);
  assert.equal(t.api.statusText(job({ status: 'failed', error: 'no_key' })), t.api.TEXT.noKey);
  assert.equal(t.api.statusText(job({ status: 'failed' })), t.api.TEXT.failed);
  assert.equal(t.api.conversationOf(`/#research=${CID}`), CID);
  for (const bad of ['/#research=', '/#research=../x', '/#research=a"b', '/#brief', null, `/#research=${'a'.repeat(40)}`]) {
    assert.equal(t.api.conversationOf(bad), null, String(bad));
  }
});

test('the card shows only when research is on, and polls while a job runs', async () => {
  const t = load();
  await state(t, { research: false });
  assert.equal(t.box.hidden, true);
  assert.equal(t.fetches.length, 0);
  await state(t, { research: true });
  assert.deepEqual(t.fetches.map((f) => f.url), ['/api/research']);
  assert.equal(t.box.hidden, false);
  const card = t.box.children[0];
  assert.equal(card.getAttribute('data-status'), 'researching');
  assert.equal(card.find('research-state').textContent, 'Researching… (3/10 searches)');
  assert.ok(card.find('research-cancel'));
  assert.equal(card.find('research-dismiss'), null);  // a running job can't be hidden
  const live = t.timers.filter((x) => !x.cleared);
  assert.equal(live.length, 1);
  assert.equal(live[0].ms, 4000);
  // it finishes: the next poll shows it ready, and the polling stops
  t.win.jobs = [job({ status: 'done', searches: 9, conversation_id: CID })];
  await live[0].fn();
  await tick();
  assert.equal(t.box.children[0].find('research-state').textContent, 'Research ready');
  assert.equal(t.timers.length, 1);  // no new one: nothing runs now
});

test('the question is text, never HTML', async () => {
  const t = load({ jobs: [job({ question: '<img src=x onerror=alert(1)>' })] });
  await state(t, { research: true });
  assert.equal(t.box.children[0].find('research-question').textContent, '<img src=x onerror=alert(1)>');
});

test('cancel asks first, then posts', async () => {
  const no = load({ confirm: false });
  await state(no, { research: true });
  await no.box.children[0].find('research-cancel').fire('click');
  assert.deepEqual(no.fetches.map((f) => f.method), ['GET']);
  const t = load();
  await state(t, { research: true });
  t.win.jobs = [job({ status: 'cancelled' })];
  await t.box.children[0].find('research-cancel').fire('click');
  await tick();
  assert.deepEqual(t.fetches.map((f) => [f.method, f.url]), [
    ['GET', '/api/research'], ['POST', '/api/research/res-0123456789abcdef/cancel'],
    ['GET', '/api/research']]);
  assert.equal(t.box.children[0].find('research-state').textContent, 'Research cancelled');
});

test('a ready report opens its transcript; Save to Notion only when connected', async () => {
  const t = load({ jobs: [job({ status: 'done', conversation_id: CID, notion: false })] });
  await state(t, { research: true });
  const card = t.box.children[0];
  await card.find('research-open').fire('click');
  assert.deepEqual(t.opened, [CID]);
  assert.equal(card.find('research-notion'), null);  // not connected: no button

  const n = load({ jobs: [job({ status: 'partial', conversation_id: CID, notion: true })] });
  await state(n, { research: true });
  assert.equal(n.box.children[0].find('research-state').textContent, 'Research ready (partial)');
  await n.box.children[0].find('research-notion').fire('click');
  assert.deepEqual(n.fetches.at(-1), { url: '/api/research/res-0123456789abcdef/notion', method: 'POST' });
  const link = n.box.children[0].find('research-notion');
  assert.equal(link.tagName, 'A');
  assert.equal(link.href, 'https://notion.so/p1');
  assert.equal(link.rel, 'noopener noreferrer');
  assert.equal(link.textContent, 'Saved to Notion');

  const bad = load({ jobs: [job({ status: 'done', conversation_id: CID, notion_url: 'javascript:alert(1)' })] });
  await state(bad, { research: true });
  assert.equal(bad.box.children[0].find('research-notion'), null);
});

test('a dismissed card stays gone', async () => {
  const t = load({ jobs: [job({ status: 'failed', error: 'restarted' })] });
  await state(t, { research: true });
  await t.box.children[0].find('research-dismiss').fire('click');
  assert.equal(t.box.hidden, true);
  assert.deepEqual(JSON.parse(t.stored['kwil-research-dismissed']), ['res-0123456789abcdef']);
  // a fresh page reads them back (a new vm, the same storage)
  const t2 = load({ jobs: [job({ status: 'failed' })] });
  t2.win.localStorage.getItem = () => t.stored['kwil-research-dismissed'];
  t2.api.onState({ research: true }, t2.win, t2.win.document);
  await tick(); await tick();
  assert.equal(t2.box.hidden, true);
});

test('a notification opens the report; a bad id never does', async () => {
  const t = load({ hash: `#research=${CID}`, jobs: [] });
  await state(t, { research: true });
  assert.deepEqual(t.opened, [CID]);
  assert.equal(t.replaced.length, 1);  // the hash is cleared
  await state(t, { research: true });
  assert.deepEqual(t.opened, [CID]);  // only once
  t.swListeners.message({ data: { type: 'open', url: `/#research=${CID}` } });
  t.swListeners.message({ data: { type: 'open', url: '/#research=bad"id' } });
  t.swListeners.message({ data: { type: 'open', url: '/#brief' } });
  assert.deepEqual(t.opened, [CID, CID]);
  t.win.location.hash = `#research=${CID}`;
  t.listeners.hashchange();
  assert.deepEqual(t.opened, [CID, CID, CID]);
  const bad = load({ hash: '#research=../../x' });
  await state(bad, { research: true });
  assert.deepEqual(bad.opened, []);
});

test('every fixed string has Chinese, and the page, app.js and sw.js wire it up', () => {
  const ctx = { console };
  vm.createContext(ctx);
  vm.runInContext(I18N, ctx);
  const tr = ctx.KwilI18n.translate;
  const t = load();
  for (const s of Object.values(t.api.TEXT)) {
    const zh = tr(s);
    assert.notEqual(zh, s, s);
    assert.match(zh, /[一-鿿]/, s);
  }
  assert.equal(tr('Researching… (3/10 searches)'), '正在研究…（已搜索 3/10 次）');
  assert.ok(HTML.includes('id="research-cards"'));
  assert.ok(HTML.indexOf('/research.js') > 0 && HTML.indexOf('/research.js') < HTML.indexOf('/app.js'));
  assert.match(APP, /KwilResearch\.onState\(state\)/);
  assert.match(APP, /KwilResearch\.onDone\(ev\)/);
  assert.match(APP, /KwilResearch\.configure\(\{ request \}\)/);
  assert.ok(SW.includes('"/research.js"') && SW.includes('kwil-shell-v29'));
});
