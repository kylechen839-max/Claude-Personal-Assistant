// node --test tests/js/student.test.mjs  (Node 18+; no packages needed)
//
// Runs the phone page's kwil/web/student.js in node:vm with a minimal
// fake DOM: the card stays hidden when the routes are off or signed out;
// the owner is told to use the environment (no link form) only while it
// sets the link, else gets the form; an invited person's link is POSTed
// once and cleared from the page; the guided way (find the school, open
// its Canvas calendar from the tap, Paste the link) only fills the field;
// captured titles are shown as text only; conflicts get both buttons;
// deleting asks first and POSTs (the failover preflight allows only GET
// and POST).

import test from 'node:test';
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import vm from 'node:vm';

const SCRIPT = readFileSync(new URL('../../kwil/web/student.js', import.meta.url), 'utf8');

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
                routes = {}, confirm = () => true, clipboard } = {}) {
  const menu = new El('details');
  const apiKey = new El('section'); apiKey.id = 'api-key';
  menu.append(apiKey);
  const fetches = [];
  const asked = [];
  const opened = [];
  const win = {
    navigator: clipboard ? { clipboard } : {},
    open: (...args) => { opened.push(args); return null; },
    confirm: (q) => { asked.push(q); return confirm(q); },
    fetch: async (url, opts = {}) => {
      fetches.push({ url, opts });
      const path = url.split('?')[0];
      const r = url === '/api/student/items' ? items
        : routes[url] || routes[path] || { status: 200, body: {} };
      return { ok: r.status < 300, status: r.status, json: async () => r.body };
    },
    document: {
      readyState: 'complete',
      getElementById: (id) => (id === 'api-key' ? apiKey : null),
      querySelector: (sel) => (sel === 'details.menu' ? menu : null),
      createElement: (tag) => new El(tag),
      addEventListener() {},
    },
    Date, Number, String, Object, Array, JSON, isNaN, URL, encodeURIComponent,
  };
  win.window = win;
  vm.createContext(win);
  vm.runInContext(SCRIPT, win);
  const card = menu.children.find((c) => c.id === 'student');
  return { win, menu, card, fetches, asked, opened };
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

test('the owner gets the link form once the environment no longer sets it', async () => {
  const { card } = load({ items: { status: 200, body: { items: [], canvas: {
    connected: false, managed: 'app', can_store: true } } } });
  await tick();
  assert.doesNotMatch(card.find((e) => e.attrs.role === 'status').textContent, /CANVAS_ICS_URL/);
  const form = card.find((e) => e.attrs['aria-label'] === 'Canvas calendar feed link').parentNode;
  assert.equal(form.hidden, false);
  assert.equal(card.buttons('Disconnect Canvas')[0].hidden, true);
});

const SCHOOLS = { status: 200, body: { schools: [
  { name: 'Example University', base_url: 'https://canvas.example.edu' },
  { name: 'Evil LAN', base_url: 'https://10.0.0.1' },
  { name: 'Loopback', base_url: 'https://localhost' },
  { name: 'Plain http', base_url: 'http://canvas.other.edu' },
  { name: 'With password', base_url: 'https://me:pw@canvas.other.edu' },
  { name: 'Odd port', base_url: 'https://canvas.other.edu:8443' },
  { name: 'Intranet', base_url: 'https://canvas.school.local' },
  { name: 'Not a URL', base_url: 'javascript:alert(1)' },
  { name: 'Second U', base_url: 'https://second.instructure.com' },
] } };

test('guided: find the school, open its Canvas calendar from the tap', async () => {
  const { card, fetches, opened } = load({ routes: { '/api/student/schools': SCHOOLS } });
  await tick();
  const find = card.find((e) => e.attrs['aria-label'] === 'Find your school');
  const openBtn = card.buttons('Open my Canvas calendar')[0];
  assert.equal(openBtn.parentNode.hidden, true);
  find.value = 'Ex';
  await card.buttons('Find my school')[0].fire('click');
  assert.ok(card.all().some((e) => e.textContent === "Type at least 3 letters of your school's name."));
  find.value = ' Example U ';
  await card.buttons('Find my school')[0].fire('click');
  assert.equal(fetches.at(-1).url, '/api/student/schools?q=Example%20U');
  // only the public https Canvas hosts are offered
  assert.equal(card.buttons('Example University').length, 1);
  assert.equal(card.buttons('Second U').length, 1);
  for (const bad of ['Evil LAN', 'Loopback', 'Plain http', 'With password', 'Odd port',
                     'Intranet', 'Not a URL']) {
    assert.equal(card.buttons(bad).length, 0, bad);
  }
  assert.equal(openBtn.parentNode.hidden, true);
  await card.buttons('Example University')[0].fire('click');
  assert.equal(openBtn.parentNode.hidden, false);
  assert.ok(card.all().some((e) => e.textContent === 'canvas.example.edu'));
  assert.ok(card.all().some((e) => e.textContent ===
    'Tap Calendar Feed (bottom right), copy the link, come back.'));
  await openBtn.fire('click');
  assert.deepEqual(opened, [['https://canvas.example.edu/calendar', '_blank', 'noopener']]);
  assert.ok(!fetches.some((f) => f.opts.method === 'POST'));  // nothing sent
});

test('guided: a typed Canvas address is used as it is', async () => {
  for (const [typed, host] of [['https://canvas.school.edu/', 'canvas.school.edu'],
                               ['canvas.school.edu/', 'canvas.school.edu'],
                               ['  canvas.school.edu  ', 'canvas.school.edu'],
                               ['https://school.instructure.com/courses', 'school.instructure.com']]) {
    const { card, fetches, opened } = load();
    await tick();
    card.find((e) => e.attrs['aria-label'] === 'Find your school').value = typed;
    await card.buttons('Find my school')[0].fire('click');
    assert.ok(!fetches.some((f) => f.url.startsWith('/api/student/schools')), typed);
    const openBtn = card.buttons('Open my Canvas calendar')[0];
    assert.equal(openBtn.parentNode.hidden, false, typed);
    await openBtn.fire('click');
    assert.deepEqual(opened, [[`https://${host}/calendar`, '_blank', 'noopener']], typed);
  }
  // unsafe or bare ones still go to the lookup (which knows the Canvas host)
  for (const [typed, q] of [['https://localhost/', 'https%3A%2F%2Flocalhost%2F'],
                            ['http://canvas.school.edu/', 'http%3A%2F%2Fcanvas.school.edu%2F'],
                            ['school.edu', 'school.edu'], ['school.edu/', 'school.edu']]) {
    const { card, fetches } = load();
    await tick();
    card.find((e) => e.attrs['aria-label'] === 'Find your school').value = typed;
    await card.buttons('Find my school')[0].fire('click');
    assert.equal(fetches.at(-1).url, '/api/student/schools?q=' + q, typed);
    assert.equal(card.buttons('Open my Canvas calendar')[0].parentNode.hidden, true, typed);
  }
});

test('env-managed owner: Disconnect only with an older app-stored link', async () => {
  for (const [appLink, hidden] of [[true, false], [false, true]]) {
    const { card } = load({ items: { status: 200, body: { items: [], canvas: {
      connected: true, managed: 'env', can_store: true, app_link: appLink } } } });
    await tick();
    assert.equal(card.buttons('Disconnect Canvas')[0].hidden, hidden);
    const form = card.find((e) => e.attrs['aria-label'] === 'Canvas calendar feed link').parentNode;
    assert.equal(form.hidden, true);
  }
});

test('guided: a lookup with nothing usable says so', async () => {
  const { card, opened } = load({ routes: { '/api/student/schools': { status: 200,
    body: { schools: [{ name: 'Loopback', base_url: 'https://localhost' }] } } } });
  await tick();
  card.find((e) => e.attrs['aria-label'] === 'Find your school').value = 'loopback';
  await card.buttons('Find my school')[0].fire('click');
  assert.ok(card.all().some((e) => e.textContent ===
    "I couldn't find that school. Type its Canvas address, e.g. canvas.school.edu."));
  await card.buttons('Open my Canvas calendar')[0].fire('click');
  assert.deepEqual(opened, []);
});

test('Paste fills the feed field and never connects by itself', async () => {
  const link = 'https://canvas.example.edu/feeds/calendars/user_SECRET.ics';
  let reads = 0;
  const { card, fetches } = load({ clipboard: { readText: async () => { reads++; return ` ${link} `; } } });
  await tick();
  const feed = card.find((e) => e.attrs['aria-label'] === 'Canvas calendar feed link');
  const paste = card.buttons('Paste')[0];
  assert.equal(paste.hidden, false);
  assert.equal(reads, 0);  // only on the tap
  await paste.fire('click');
  assert.equal(reads, 1);
  assert.equal(feed.value, link);
  assert.ok(!fetches.some((f) => f.opts.method === 'POST'));
  await card.buttons('Connect Canvas')[0].fire('click');
  assert.deepEqual(JSON.parse(fetches.find((f) => f.url === '/api/student/canvas').opts.body),
                   { ics_url: link });
});

test('Paste: denied is silent, other text is not filled, no clipboard hides it', async () => {
  const denied = load({ clipboard: { readText: async () => { throw new Error('NotAllowedError'); } } });
  await tick();
  await denied.card.buttons('Paste')[0].fire('click');
  assert.equal(denied.card.find((e) => e.attrs['aria-label'] === 'Canvas calendar feed link').value, '');
  assert.ok(denied.card.all().every((e) => e.hidden || !/NotAllowed|Couldn't/.test(e.textContent)));
  const other = load({ clipboard: { readText: async () => 'my grocery list' } });
  await tick();
  await other.card.buttons('Paste')[0].fire('click');
  assert.equal(other.card.find((e) => e.attrs['aria-label'] === 'Canvas calendar feed link').value, '');
  assert.ok(other.card.all().some((e) => e.textContent === 'Copy the Calendar Feed link in Canvas first.'));
  const none = load();
  await tick();
  assert.equal(none.card.buttons('Paste')[0].hidden, true);
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

const I18N = readFileSync(new URL('../../kwil/web/i18n.js', import.meta.url), 'utf8');

test('every fixed string it shows or asks has Chinese in i18n.js', async () => {
  const ctx = {};
  vm.createContext(ctx);
  vm.runInContext(I18N, ctx);
  const { translate } = ctx.KwilI18n;
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
    load({ routes: { '/api/student/schools': SCHOOLS } }),
    load({ routes: { '/api/student/schools': { status: 200, body: { schools: [] } } } }),
    load({ routes: { '/api/student/schools': { status: 500, body: {} } },
           clipboard: { readText: async () => 'not a link' } }),
  ];
  await tick();
  for (const [i, q] of [[5, 'Example'], [6, 'Nowhere'], [7, 'Broken']]) {
    const { card } = scenarios[i];
    card.find((e) => e.attrs['aria-label'] === 'Find your school').value = 'Ex';
    await card.buttons('Find my school')[0].fire('click');
    card.find((e) => e.attrs['aria-label'] === 'Find your school').value = q;
    await card.buttons('Find my school')[0].fire('click');
  }
  await scenarios[5].card.buttons('Example University')[0].fire('click');
  await scenarios[7].card.buttons('Paste')[0].fire('click');
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
  // the guided way's messages (some are only ever the last one said)
  for (const s of ["Type at least 3 letters of your school's name.",
                   "I couldn't find that school. Type its Canvas address, e.g. canvas.school.edu.",
                   "Couldn't look up schools. Try again.",
                   'Tap Calendar Feed (bottom right), copy the link, come back.']) {
    assert.ok(seen.has(s) || SCRIPT.includes(s.slice(0, 30)), s);
    assert.notEqual(translate(s), s, s);
  }
});
