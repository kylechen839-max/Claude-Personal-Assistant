// node --test tests/js/student.test.mjs  (Node 18+; no packages needed)
//
// Runs the phone page's assistant/web/student.js in node:vm with a minimal
// fake DOM: the card stays hidden when the routes are off or signed out;
// the owner is told to use the environment (no link form); an invited
// person's link is POSTed once and cleared from the page; captured titles
// are shown as text only; conflicts get both buttons; deleting asks first
// and POSTs (the failover preflight allows only GET and POST).

import test from 'node:test';
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import vm from 'node:vm';

const SCRIPT = readFileSync(new URL('../../assistant/web/student.js', import.meta.url), 'utf8');

class El {
  constructor(tag) {
    this.tagName = tag; this.children = []; this.hidden = false; this.textContent = '';
    this.listeners = {}; this.attrs = {}; this.value = ''; this.disabled = false;
    this.className = ''; this.open = false;
  }
  set innerHTML(v) { this.usedHTML = true; }
  append(...kids) { for (const k of kids) { k.parentNode = this; this.children.push(k); } }
  appendChild(k) { this.append(k); return k; }
  insertBefore(k, ref) {
    k.parentNode = this;
    const i = this.children.indexOf(ref);
    this.children.splice(i < 0 ? this.children.length : i, 0, k);
  }
  replaceChildren(...kids) { this.children = []; this.append(...kids); }
  setAttribute(k, v) { this.attrs[k] = v; }
  addEventListener(type, fn) { (this.listeners[type] ??= []).push(fn); }
  async fire(type) { for (const fn of this.listeners[type] || []) await fn({ preventDefault() {} }); }
  all() { return [this, ...this.children.flatMap((c) => c.all())]; }
  find(pred) { return this.all().find(pred); }
  buttons(text) { return this.all().filter((e) => e.tagName === 'button' && e.textContent === text); }
}

const ITEMS = [
  { id: 1, title: '<img src=x onerror=alert(1)>Problem Set 5', course: 'MATH 241',
    kind: 'assignment', due: '2026-11-04T04:59:00Z', status: 'draft', source: 'ics' },
  { id: 2, title: 'Midterm', course: 'PHYS 211', kind: 'exam', due: '2026-11-10',
    status: 'conflict', source: 'ics' },
  { id: 3, title: 'Midterm', course: 'PHYS 211', kind: 'exam', due: '2026-11-12',
    status: 'conflict', source: 'gmail' },
];

function load({ items = { status: 200, body: { items: [], canvas: { connected: false,
                managed: 'app', can_store: true, gmail: false } } },
                routes = {}, confirm = () => true } = {}) {
  const menu = new El('details');
  const apiKey = new El('section'); apiKey.id = 'api-key';
  menu.append(apiKey);
  const fetches = [];
  const asked = [];
  const win = {
    confirm: (q) => { asked.push(q); return confirm(q); },
    fetch: async (url, opts = {}) => {
      fetches.push({ url, opts });
      const r = url === '/api/student/items' ? items : routes[url] || { status: 200, body: {} };
      return { ok: r.status < 300, status: r.status, json: async () => r.body };
    },
    document: {
      readyState: 'complete',
      getElementById: (id) => (id === 'api-key' ? apiKey : null),
      querySelector: (sel) => (sel === 'details.menu' ? menu : null),
      createElement: (tag) => new El(tag),
      addEventListener() {},
    },
    Date, Number, String, Object, Array, JSON, isNaN,
  };
  win.window = win;
  vm.createContext(win);
  vm.runInContext(SCRIPT, win);
  const card = menu.children.find((c) => c.id === 'student');
  return { win, menu, card, fetches, asked };
}

const tick = () => new Promise((r) => setTimeout(r, 0));

test('hidden when the routes are off or nobody is signed in', async () => {
  for (const status of [404, 401]) {
    const { card, fetches } = load({ items: { status, body: { error: 'x' } } });
    await tick();
    assert.equal(card.hidden, true);
    assert.equal(fetches[0].opts.credentials, 'same-origin');
  }
});

test('the card sits before the API key section', async () => {
  const { menu, card } = load();
  await tick();
  assert.equal(menu.children[0], card);
  assert.equal(card.hidden, false);
});

test('the owner is told to use the environment, with no link form', async () => {
  const { card } = load({ items: { status: 200, body: { items: [], canvas: {
    connected: false, managed: 'env', can_store: true } } } });
  await tick();
  assert.match(card.find((e) => e.attrs.role === 'status').textContent, /CANVAS_ICS_URL/);
  const form = card.find((e) => e.attrs['aria-label'] === 'Canvas calendar feed link').parentNode;
  assert.equal(form.hidden, true);
});

test('an invited person connects a feed link once, and it is cleared', async () => {
  const { card, fetches } = load({ routes: {
    '/api/student/sync': { status: 200, body: { sources: { ics: { added: 3 } }, errors: {} } } } });
  await tick();
  const feed = card.find((e) => e.attrs['aria-label'] === 'Canvas calendar feed link');
  feed.value = ' https://canvas.example.edu/feeds/calendars/user_SECRET.ics ';
  await card.buttons('Connect Canvas')[0].fire('click');
  const posts = fetches.filter((f) => f.opts.method === 'POST');
  assert.deepEqual(posts.map((f) => f.url), ['/api/student/canvas', '/api/student/sync']);
  assert.deepEqual(JSON.parse(posts[0].opts.body),
                   { ics_url: 'https://canvas.example.edu/feeds/calendars/user_SECRET.ics' });
  assert.equal(feed.value, '');
  assert.ok(card.all().some((e) => e.textContent === 'Found 3 new.'));
  assert.ok(!card.all().some((e) => String(e.textContent).includes('SECRET')));
});

test('captured items are text only, with conflict and delete buttons', async () => {
  const { card, fetches, asked } = load({ items: { status: 200, body: { items: ITEMS,
    canvas: { connected: true, school: 'canvas.example.edu', managed: 'app' } } } });
  await tick();
  assert.ok(!card.all().some((e) => e.usedHTML));
  assert.ok(card.all().some((e) => e.textContent === '<img src=x onerror=alert(1)>Problem Set 5 · MATH 241'));
  assert.equal(card.buttons('Looks right').length, 1);
  assert.equal(card.buttons('This date is right').length, 2);
  assert.equal(card.buttons('Not this one').length, 2);
  await card.buttons('Not this one')[1].fire('click');
  const resolve = fetches.find((f) => f.url === '/api/student/items/3/resolve');
  assert.deepEqual(JSON.parse(resolve.opts.body), { choice: 'dismiss' });
  await card.buttons('Delete')[0].fire('click');
  assert.deepEqual(asked, ['Delete this deadline?']);
  const del = fetches.find((f) => f.url === '/api/student/items/1/delete');
  assert.equal(del.opts.method, 'POST');
});

test('declining the delete question sends nothing', async () => {
  const { card, fetches } = load({ confirm: () => false, items: { status: 200, body: {
    items: ITEMS, canvas: { connected: true, managed: 'app' } } } });
  await tick();
  await card.buttons('Delete')[0].fire('click');
  assert.ok(!fetches.some((f) => f.url.endsWith('/delete')));
});

test('a throttled sync says why', async () => {
  const { card } = load({ items: { status: 200, body: { items: [], canvas: {
    connected: true, managed: 'app' } } }, routes: {
    '/api/student/sync': { status: 429, body: { error: 'synced recently; try again in 14 minute(s)' } } } });
  await tick();
  await card.buttons('Sync now')[0].fire('click');
  assert.ok(card.all().some((e) => e.textContent === 'synced recently; try again in 14 minute(s)'));
});

const I18N = readFileSync(new URL('../../assistant/web/i18n.js', import.meta.url), 'utf8');

test('every fixed string it shows or asks has Chinese in i18n.js', async () => {
  const ctx = {};
  vm.createContext(ctx);
  vm.runInContext(I18N, ctx);
  const { translate } = ctx.HeyClaudeI18n;
  const seen = new Set();
  const scenarios = [
    load(),
    load({ items: { status: 200, body: { items: [], canvas: { connected: false, managed: 'env' } } } }),
    load({ items: { status: 200, body: { items: [], canvas: {
      connected: false, managed: 'app', can_store: false } } } }),
    load({ items: { status: 200, body: { items: ITEMS, canvas: { connected: true, gmail: true,
      school: 'canvas.example.edu', managed: 'app' } } },
           routes: { '/api/student/sync': { status: 200, body: { sources: {}, errors: { ics: 'x' } } } } }),
    load({ items: { status: 200, body: { items: ITEMS, canvas: { connected: true, managed: 'app' } } } }),
  ];
  await tick();
  const busy = scenarios[3];
  await busy.card.buttons('Sync now')[0].fire('click');
  await busy.card.buttons('Disconnect Canvas')[0].fire('click');
  await busy.card.buttons('Delete')[0].fire('click');
  for (const { card, asked } of scenarios) {
    for (const e of card.all()) {
      for (const s of [e.textContent, e.placeholder, e.attrs['aria-label']]) if (s) seen.add(s);
    }
    for (const q of asked) seen.add(q);
  }
  // the strings written in student.js itself (not titles, dates or hosts)
  const fixed = [...seen].filter((s) => SCRIPT.includes(JSON.stringify(s)))
    .filter((s) => !s.startsWith('https://') && !['Canvas', ' · '].includes(s));
  assert.ok(fixed.length > 25, String(fixed.length));
  for (const s of fixed) assert.notEqual(translate(s), s, s);
  assert.equal(translate('Found 3 new.'), '找到 3 条新内容。');
  assert.equal(translate('School deadlines'), '学校截止日期');
});
