// node --test tests/js/agent-dashboard.test.mjs  (Node 18+; no packages needed)
//
// Runs the dev-team dashboard page's own <script> (tools/agent-dashboard/
// index.html) in node:vm with a minimal fake document, a fake
// window.claude.use("db") (one query per collection: runs, agents and each
// chat mirror), a fake use("user") and use("comments") (canSendToClaude,
// anchorFor, sendToClaude), fake timers and a fake localStorage, then checks
// what it decides and renders: silent/running/needs at the time limits,
// safe links, row text that stays text, each db error code -> stop, back
// off or retry, the Grid's flat window workspace (auto-tiling, spans,
// maximize, hide and restore, filter, run colours, drag, resize,
// keyboard, storage; desktop only), the coordinator chat (send, errors, the mirrored
// transcript) and the markdown renderer.

import test from 'node:test';
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import vm from 'node:vm';

const PAGE = readFileSync(new URL('../../tools/agent-dashboard/index.html', import.meta.url), 'utf8');
const SCRIPT = (() => {
  const m = PAGE.match(/<script>([\s\S]*?)<\/script>/);
  assert.ok(m, 'page has an inline <script>');
  return m[1];
})();

const MIN = 60e3;
const DAY = 24 * 60 * MIN;
const NOW = Date.parse('2026-10-19T12:00:00Z');
const iso = (t) => new Date(t).toISOString().replace(/\.\d{3}Z$/, 'Z');

// ---- a minimal DOM: text and child elements only, no markup parsing ----
class FakeEl {
  constructor(tag) {
    this.tagName = tag.toUpperCase();
    this.children = [];
    this.ownText = '';
    this.dataset = {};
    this.className = '';
    this.hidden = false;
    this.attrs = {};
    this.listeners = {};
    this.value = '';
    this.disabled = false;
    this.focused = 0;
    this.rect = null;
    this.scrollTop = 0;
    this.scrollHeight = 0;
    this.clientHeight = 0;
  }
  setAttribute(name, value) { this.attrs[name] = String(value); }
  getAttribute(name) { return Object.hasOwn(this.attrs, name) ? this.attrs[name] : null; }
  addEventListener(type, fn) { (this.listeners[type] ??= []).push(fn); }
  dispatch(type, extra = {}) {
    const ev = { type, target: this, preventDefault() { ev.defaultPrevented = true; }, ...extra };
    for (const fn of this.listeners[type] ?? []) fn(ev);
    return ev;
  }
  click() { this.dispatch('click'); }
  focus() { this.focused += 1; }
  getBoundingClientRect() { return this.rect ?? { left: 0, top: 0, right: 0, bottom: 0, width: 0, height: 0 }; }
  set textContent(v) { this.ownText = String(v); this.children = []; }
  get textContent() { return this.ownText + this.children.map((c) => c.textContent).join(''); }
  append(...nodes) {
    for (const n of nodes) {
      assert.ok(n instanceof FakeEl, 'only elements are appended');
      this.children.push(n);
    }
  }
  replaceChildren(...nodes) { this.ownText = ''; this.children = []; this.append(...nodes); }
  find(pred) {
    const out = [];
    const walk = (e) => { for (const c of e.children) { if (pred(c)) out.push(c); walk(c); } };
    walk(this);
    return out;
  }
}

function parseTags(html) {
  const frag = new FakeEl('#fragment');
  for (const m of String(html).matchAll(/<([a-z][a-z0-9]*)/gi)) frag.children.push(new FakeEl(m[1]));
  return frag;
}

// One query and one handler pair per collection. calls.query, calls.subs,
// calls.unsubs and calls.throwOnSubscribe are the runs query's (as before);
// calls.queries[name], calls.subsBy[name] and calls.unsubsBy[name] cover both.
function fakeDb() {
  const calls = { subs: 0, unsubs: 0, query: null, queries: {}, subsBy: {}, unsubsBy: {}, throwBy: {} };
  const handlers = {};
  const makeQuery = (name) => ({
    onSnapshot(next, error) {
      calls.subsBy[name] = (calls.subsBy[name] ?? 0) + 1;
      if (name === 'runs') calls.subs += 1;
      const boom = name === 'runs' ? calls.throwOnSubscribe : calls.throwBy[name];
      if (boom) throw boom;
      handlers[name] = { next, error };
      return () => {
        calls.unsubsBy[name] = (calls.unsubsBy[name] ?? 0) + 1;
        if (name === 'runs') calls.unsubs += 1;
      };
    },
  });
  const db = {
    doc: (name) => { calls.queries[name] = [name, 'doc']; return makeQuery(name); },
    collection: (name) => ({
      orderBy: (field, dir) => ({
        limit: (n) => {
          calls.queries[name] = [name, field, dir, n];
          if (name === 'runs') calls.query = [name, field, dir, n];
          return makeQuery(name);
        },
      }),
    }),
  };
  return { db, calls, handlers: (name = 'runs') => handlers[name] ?? null };
}

// The full comments namespace. canSendToClaude answers ns.state.can (or the
// next function queued in canReplies); anchorFor records the element;
// sendToClaude records each target and answers from sendReplies (default: a
// new thread t<N>).
function fakeComments({ can = 'available' } = {}) {
  const calls = { can: 0, anchor: [], send: [] };
  const canReplies = [];
  const replies = [];
  const state = { can };
  const ns = {
    canSendToClaude() {
      calls.can += 1;
      const next = canReplies.shift();
      return next ? next() : Promise.resolve(state.can);
    },
    anchorFor(el) {
      calls.anchor.push(el);
      return Promise.resolve({ path: 'article > h2', x: 3, y: 4 });
    },
    sendToClaude(target) {
      calls.send.push(target);
      const next = replies.shift();
      return next ? next(target) : Promise.resolve({ threadId: `t${calls.send.length}`, commentId: `c${calls.send.length}` });
    },
  };
  return { ns, calls, replies, canReplies, state };
}

// The downloads namespace: save() records each request and answers from
// replies (default: saved).
function fakeDownloads() {
  const calls = { save: [] };
  const replies = [];
  const ns = {
    save(req) {
      calls.save.push(req);
      const next = replies.shift();
      return next ? next(req) : Promise.resolve({ status: 'saved' });
    },
  };
  return { ns, calls, replies };
}

const PAGE_IDS = {
  summary: 'p', conn: 'p', runs: 'ul', empty: 'p',
  main: 'main', 'view-nav': 'nav', 'runs-view': 'section', 'grid-view': 'section', 'view-runs': 'button', 'view-grid': 'button',
  'grid-head': 'header', 'grid-summary': 'p', 'grid-toolbar': 'div', 'grid-filter-active': 'button', 'grid-filter-all': 'button',
  'grid-count': 'p', 'grid-conn': 'p', coords: 'div', 'grid-hidden': 'p', 'grid-reset': 'button', 'grid-live': 'p',
  'grid-foot': 'footer', 'grid-tray': 'details', 'grid-tray-sum': 'summary', 'grid-tray-list': 'div',
  'grid-showall': 'button', getapp: 'div', 'getapp-btn': 'button', 'getapp-menu': 'div', 'getapp-hint': 'p',
  'getapp-dash-win': 'button', 'getapp-dash-mac': 'button', 'getapp-hub-win': 'button', 'getapp-hub-mac': 'button',
};

function memStorage(initial = {}) {
  const store = { ...initial };
  return {
    store,
    getItem: (k) => (k in store ? store[k] : null),
    setItem: (k, v) => { store[k] = String(v); },
    removeItem: (k) => { delete store[k]; },
  };
}

async function loadPage({ db = fakeDb(), withClaude = true, comments = fakeComments(), storage = undefined,
  hash = undefined, user = 'u_test', userNs = true, downloads = fakeDownloads() } = {}) {
  const ids = {};
  for (const [id, tag] of Object.entries(PAGE_IDS)) ids[id] = new FakeEl(tag);
  ids['grid-view'].hidden = true;
  ids['grid-hidden'].hidden = true;
  ids['grid-tray'].hidden = true;
  for (const id of ['getapp', 'getapp-menu', 'getapp-hint']) ids[id].hidden = true;
  ids['grid-filter-active'].setAttribute('aria-pressed', 'true');
  ids['grid-filter-all'].setAttribute('aria-pressed', 'false');
  const timers = [];
  const intervals = [];
  // The document hears keydown (Esc) and visibilitychange; doc() delivers
  // one to every listener the page added and returns the event.
  const docListeners = {};
  const document = {
    hidden: false,
    getElementById: (id) => ids[id] ?? null,
    createElement: (tag) => new FakeEl(tag),
    addEventListener: (type, fn) => { (docListeners[type] ??= []).push(fn); },
    // A markup sink a regression might reach for: it "parses" tags into
    // child elements, so a test sees markup become elements, not a crash.
    createRange: () => ({ createContextualFragment: parseTags, selectNodeContents() {} }),
  };
  const doc = (type, extra = {}) => {
    const ev = { type, defaultPrevented: false, preventDefault() { ev.defaultPrevented = true; }, ...extra };
    for (const fn of [...(docListeners[type] ?? [])]) fn(ev);
    return ev;
  };
  const sandbox = {
    document,
    console,
    setTimeout: (fn, ms) => { timers.push({ fn, ms, cleared: false }); return timers.length; },
    clearTimeout: (id) => { if (id && timers[id - 1]) timers[id - 1].cleared = true; },
    setInterval: (fn, ms) => { intervals.push({ fn, ms }); return intervals.length; },
    DOMParser: class { parseFromString(html) { return { body: parseTags(html) }; } },
  };
  // The window hears pointermove/up/cancel (a captured pointer's events
  // bubble there) and resize; win() delivers one to every listener the page added.
  const winListeners = {};
  sandbox.addEventListener = (type, fn) => { (winListeners[type] ??= []).push(fn); };
  sandbox.removeEventListener = (type, fn) => { winListeners[type] = (winListeners[type] ?? []).filter((f) => f !== fn); };
  const win = (type, extra = {}) => {
    for (const fn of [...(winListeners[type] ?? [])]) fn({ type, preventDefault() {}, ...extra });
  };
  const uses = [];
  const userCalls = { id: 0 };
  if (withClaude) {
    sandbox.claude = {
      use: async (cap) => {
        uses.push(cap);
        if (cap === 'db') return db.db;
        if (cap === 'comments') return comments && comments.ns;
        if (cap === 'user') return userNs ? { id: async () => { userCalls.id += 1; return user; } } : null;
        if (cap === 'downloads') return downloads && downloads.ns;
        return null;
      },
    };
  }
  if (storage !== undefined) sandbox.localStorage = storage;
  if (hash !== undefined) sandbox.location = { hash };
  // A phone-sized media query: the page is desktop-only and must not ask.
  sandbox.matchMedia = () => { throw new Error('the dashboard is desktop-only; no media queries'); };
  sandbox.window = sandbox;
  const ctx = vm.createContext(sandbox);
  const setNow = (t) => vm.runInContext(`Date.now = () => ${t};`, ctx);
  setNow(NOW);
  vm.runInContext(SCRIPT, ctx, { filename: 'index.html' });
  await new Promise((r) => setImmediate(r));
  const pending = () => timers.filter((t) => !t.cleared);
  return { api: sandbox.AgentDashboard, ids, db, comments, uses, userCalls, timers, pending, intervals, win, winListeners,
    doc, docListeners, setNow, document };
}

const settle = async () => { for (let i = 0; i < 5; i += 1) await new Promise((r) => setImmediate(r)); };

const snap = (rows) => ({
  metadata: { fromCache: false },
  docs: rows.map((r, i) => ({ id: r._docId ?? `run-${i}`, data: r._data ?? (() => r) })),
});

// ---- classify ----

test('each live phase turns silent just past its limit, not before', async () => {
  const { api } = await loadPage();
  assert.deepEqual({ ...api.SILENT_AFTER_MIN }, { started: 25, researching: 45, building: 90, judging: 45 });
  for (const [phase, limit] of Object.entries(api.SILENT_AFTER_MIN)) {
    const under = api.classify({ phase, updated_at: iso(NOW - (limit - 1) * MIN) }, NOW);
    assert.equal(under.state, 'running', `${phase} at limit - 1`);
    assert.equal(under.pill, 'running');
    const over = api.classify({ phase, updated_at: iso(NOW - (limit + 1) * MIN) }, NOW);
    assert.equal(over.state, 'silent', `${phase} at limit + 1`);
    assert.match(over.note, new RegExp(`limit ${limit} min`));
  }
});

test('missing, unreadable or far-future updated_at is silent', async () => {
  const { api } = await loadPage();
  for (const updated_at of [undefined, null, '', 'yesterday', 42, iso(NOW + 11 * MIN)]) {
    const c = api.classify({ phase: 'building', updated_at }, NOW);
    assert.equal(c.state, 'silent', String(updated_at));
    assert.equal(c.note, 'last update unreadable');
  }
  // Small clock skew is fine.
  assert.equal(api.classify({ phase: 'building', updated_at: iso(NOW + 5 * MIN) }, NOW).state, 'running');
});

test('dead, unknown, done and parked phases', async () => {
  const { api } = await loadPage();
  const fresh = iso(NOW - MIN);
  const dead = api.classify({ phase: 'dead', updated_at: fresh }, NOW);
  assert.deepEqual([dead.state, dead.pill], ['silent', 'dead (watchdog)']);
  const unknown = api.classify({ phase: 'deploying', updated_at: fresh }, NOW);
  assert.deepEqual([unknown.state, unknown.note], ['silent', 'unknown phase']);
  assert.equal(api.classify({ phase: { toString: () => 'done' }, updated_at: fresh }, NOW).state, 'silent');
  assert.equal(api.classify({ phase: 'DONE', updated_at: fresh }, NOW).state, 'done');
  for (const phase of ['pr_ready', 'blocked']) {
    const c = api.classify({ phase, updated_at: fresh }, NOW);
    assert.deepEqual([c.state, c.pill], ['needs', 'needs you'], phase);
  }
});

test('PR ready, blocked and dead rows count as done after 7 days', async () => {
  const { api } = await loadPage();
  assert.equal(api.PARKED_DONE_MS, 7 * DAY);
  for (const phase of ['pr_ready', 'blocked', 'dead']) {
    const young = api.classify({ phase, updated_at: iso(NOW - 7 * DAY + MIN) }, NOW);
    assert.notEqual(young.state, 'done', phase);
    const old = api.classify({ phase, updated_at: iso(NOW - 7 * DAY - MIN) }, NOW);
    assert.equal(old.state, 'done', phase);
  }
  // A live phase never ages into done; an unreadable time stays visible.
  assert.equal(api.classify({ phase: 'building', updated_at: iso(NOW - 8 * DAY) }, NOW).state, 'silent');
  assert.equal(api.classify({ phase: 'pr_ready' }, NOW).state, 'needs');
});

test('an approval prompt seen after the last write shows needs you (approval)', async () => {
  const { api } = await loadPage();
  const updated_at = iso(NOW - 30 * MIN);
  const row = (watch) => api.classify({ phase: 'building', updated_at, watch }, NOW);
  const blocked = row({ bucket: 'BLOCKED', checked_at: iso(NOW - 5 * MIN) });
  assert.deepEqual([blocked.state, blocked.pill], ['needs', 'needs you (approval)']);
  // The master wrote after the check: the prompt was answered.
  assert.equal(row({ bucket: 'BLOCKED', checked_at: iso(NOW - 40 * MIN) }).state, 'running');
  assert.equal(row({ bucket: 'WORKING', checked_at: iso(NOW - 5 * MIN) }).state, 'running');
  assert.equal(row({ bucket: 'BLOCKED' }).state, 'running');
  assert.equal(row('BLOCKED').state, 'running');
  // Only live phases: a terminal row keeps its own state.
  const done = api.classify({ phase: 'done', updated_at, watch: { bucket: 'BLOCKED', checked_at: iso(NOW) } }, NOW);
  assert.equal(done.state, 'done');
});

// ---- links ----

test('safeLink accepts only the two prefixes with a strict tail', async () => {
  const { api } = await loadPage();
  const S = api.SESSION_PREFIX;
  const P = api.PR_PREFIX;
  const sessionTail = /^[A-Za-z0-9_-]{1,120}$/;
  const prTail = /^\d{1,7}$/;
  assert.equal(api.safeLink(`${S}01KA1RGMx3zUcsTDp3cT5KUt`, S, sessionTail), `${S}01KA1RGMx3zUcsTDp3cT5KUt`);
  assert.equal(api.safeLink(`${P}42`, P, prTail), `${P}42`);
  for (const bad of [
    `https://evil.example/?${S}x`,              // prefix mid-string
    `https://evil.example/${S}x`,
    ` ${S}x`,
    'https://claude.ai/code/other_x',            // wrong prefix
    `javascript:alert(1)//${S}x`,
    'javascript:alert(1)',
    `${S}x/../../evil`,                           // bad tail
    `${S}x"onmouseover=alert(1)`,
    `${S}`,
    `${S}x?next=https://evil.example`,
    null, undefined, 42, [`${S}x`],
  ]) {
    assert.equal(api.safeLink(bad, S, sessionTail), null, String(bad));
  }
  for (const bad of [`${P}42abc`, `${P}42/files`, `${P}`, `${P}12345678`, `https://evil.example/${P}1`]) {
    assert.equal(api.safeLink(bad, P, prTail), null, bad);
  }
});

// ---- rendering through the fake db ----

test('the page subscribes once to the newest 100 runs', async () => {
  const page = await loadPage();
  assert.deepEqual(page.db.calls.query, ['runs', 'started_at', 'desc', 100]);
  assert.equal(page.db.calls.subs, 1);
  assert.equal(page.pending().length, 0);
});

test('row text is never parsed as markup and bad links get no anchor', async () => {
  const page = await loadPage();
  const title = '<img src=x onerror=alert(1)><b>hi</b>';
  page.db.handlers().next(snap([{
    feature: title, phase: 'building', round: 2, updated_at: iso(NOW - MIN),
    branch: '<script>x</script>', blocker: '<a href="https://evil.example">x</a>',
    session_url: `https://evil.example/?${'https://claude.ai/code/session_'}x`,
    pr_url: 'javascript:alert(1)',
  }]));
  const [li] = page.ids.runs.children;
  const [h2] = li.find((e) => e.tagName === 'H2');
  assert.equal(h2.textContent, title);
  assert.equal(h2.children.length, 0);
  assert.equal(li.find((e) => e.tagName === 'A').length, 0);
  assert.equal(li.find((e) => ['IMG', 'B', 'SCRIPT'].includes(e.tagName)).length, 0);
  assert.equal(li.dataset.state, 'running');
});

test('good links render with noopener and a new tab', async () => {
  const page = await loadPage();
  page.db.handlers().next(snap([{
    feature: 'x', phase: 'pr_ready', updated_at: iso(NOW - MIN),
    session_url: 'https://claude.ai/code/session_abc',
    pr_url: 'https://github.com/kylechen839-max/Claude-Personal-Assistant/pull/7',
  }]));
  const links = page.ids.runs.children[0].find((e) => e.tagName === 'A');
  assert.deepEqual(links.map((a) => [a.textContent, a.href, a.target, a.rel]), [
    ['Session', 'https://claude.ai/code/session_abc', '_blank', 'noopener noreferrer'],
    ['PR #7', 'https://github.com/kylechen839-max/Claude-Personal-Assistant/pull/7', '_blank', 'noopener noreferrer'],
  ]);
});

test('odd row bodies are copied, never trusted or mutated', async () => {
  const page = await loadPage();
  const frozen = Object.freeze({ feature: 'frozen', phase: 'building', updated_at: iso(NOW - MIN) });
  page.db.handlers().next(snap([
    { _data: () => frozen },
    { _data: () => null },
    { _data: () => 'just a string' },
    { _data: () => ['an', 'array'] },
    { _data: () => { throw new Error('bad doc'); } },
    { _data: () => undefined },
  ]));
  assert.equal(page.ids.runs.children.length, 6);
  assert.equal(Object.hasOwn(frozen, '_id'), false);
  const titles = page.ids.runs.children.map((li) => li.find((e) => e.tagName === 'H2')[0].textContent);
  assert.ok(titles.includes('frozen'));
  assert.ok(titles.includes('run-1'), 'a row with no body falls back to its document id');
});

test('summary counts; old parked rows are done and hidden after 14 days', async () => {
  const page = await loadPage();
  page.db.handlers().next(snap([
    { feature: 'a', phase: 'pr_ready', updated_at: iso(NOW - DAY) },
    { feature: 'b', phase: 'building', updated_at: iso(NOW - MIN) },
    { feature: 'c', phase: 'judging', updated_at: iso(NOW - 3 * 60 * MIN) },
    { feature: 'd', phase: 'blocked', updated_at: iso(NOW - 8 * DAY) },
    { feature: 'e', phase: 'dead', updated_at: iso(NOW - 15 * DAY) },
    { feature: 'f', phase: 'done', updated_at: iso(NOW - 15 * DAY) },
  ]));
  assert.equal(page.ids.summary.textContent, '1 needs you · 1 running · 1 silent · 1 done');
  const shown = page.ids.runs.children.map((li) => li.find((e) => e.tagName === 'H2')[0].textContent);
  assert.deepEqual(shown.slice().sort(), ['a', 'b', 'c', 'd']);
  assert.equal(shown[shown.length - 1], 'd', 'done rows sort last');

  page.db.handlers().next(snap([{ feature: 'old', phase: 'pr_ready', updated_at: iso(NOW - 9 * DAY) }]));
  assert.equal(page.ids.summary.textContent, 'Nothing needs you · 1 done');
});

// ---- errors ----

const STOP = ['revoked', 'invalid_argument', 'transform_error', 'not_granted', 'capability_disabled',
  'capability_removed'];

for (const code of STOP) {
  test(`error ${code} stops for good: no retry timer, no resubscribe`, async () => {
    const page = await loadPage();
    page.db.handlers().next(snap([{ feature: 'x', phase: 'building', updated_at: iso(NOW) }]));
    page.db.handlers().error({ code });
    assert.equal(page.pending().length, 0);
    assert.equal(page.db.calls.subs, 1);
    assert.equal(page.ids.runs.children.length, 0, 'rows are cleared');
    assert.notEqual(page.ids.summary.textContent, '');
    assert.equal(page.ids.conn.dataset.level, 'bad');
  });
}

test('resource_exhausted backs off with doubling, reset by a good snapshot', async () => {
  const page = await loadPage();
  const fire = () => { const t = page.pending().at(-1); t.cleared = true; t.fn(); };
  const expect = [60e3, 120e3, 240e3, 480e3, 900e3, 900e3];
  for (const [i, base] of expect.entries()) {
    page.db.handlers().error({ code: 'resource_exhausted' });
    const timers = page.pending();
    assert.equal(timers.length, 1, `one timer after error ${i}`);
    assert.ok(timers[0].ms >= base * 0.75 && timers[0].ms <= base * 1.25, `${timers[0].ms} near ${base}`);
    assert.equal(page.db.calls.subs, i + 1, 'no resubscribe before the timer');
    fire();
    assert.equal(page.db.calls.subs, i + 2);
  }
  page.db.handlers().next(snap([]));
  page.db.handlers().error({ code: 'resource_exhausted' });
  assert.ok(page.pending()[0].ms <= 75e3, 'backoff resets after a good snapshot');
});

for (const err of [{ code: 'unavailable' }, { code: 'something_new' }, {}, null, new Error('boom')]) {
  test(`error ${JSON.stringify(err)} retries once after 5-30 s`, async () => {
    const page = await loadPage();
    page.db.handlers().error(err);
    const timers = page.pending();
    assert.equal(timers.length, 1);
    assert.ok(timers[0].ms >= 5e3 && timers[0].ms <= 30e3, String(timers[0].ms));
    assert.equal(page.db.calls.subs, 1);
    timers[0].fn();
    assert.equal(page.db.calls.subs, 2);
  });
}

test('a subscribe that throws schedules one retry instead of looping', async () => {
  const db = fakeDb();
  db.calls.throwOnSubscribe = Object.assign(new Error('down'), { code: 'unavailable' });
  const page = await loadPage({ db });
  assert.equal(db.calls.subs, 1);
  assert.equal(page.pending().length, 1);
});

test('without window.claude the page says to open it on claude.ai', async () => {
  const page = await loadPage({ withClaude: false });
  assert.equal(page.ids.summary.textContent, 'Open this page on claude.ai while signed in to see runs.');
  assert.equal(page.db.calls.subs, 0);
  assert.equal(page.pending().length, 0);
  assert.equal(page.intervals.length, 0);
});

// ==== Grid: a flat workspace of coordinator windows (runs rows) and agent windows (agents rows) ====

const SESSION = 'https://claude.ai/code/session_01KA1RGMx3zUcsTDp3cT5KUt';
const TAIL = '01KA1RGMx3zUcsTDp3cT5KUt';
const CHAT = `data/users/u_test/chat-${TAIL}/msgs`;
const PR = 'https://github.com/kylechen839-max/Claude-Personal-Assistant/pull/';
const RUN = '20261019-183000-voice-speed';
const NO_LISTENER = "Coordinator isn't listening (no watching session); use Open session";
const SEND_OFF = 'Sending from this page is off here. Use Open session.';

async function gridPage(opts = {}) {
  const page = await loadPage(opts);
  page.ids['view-grid'].click();
  await settle();
  return page;
}
const panels = (page) => page.ids.coords.find((e) => e.className === 'tile coord');
const agentTiles = (root) => root.find((e) => e.className === 'tile agent');
const windows = (page) => page.ids.coords.children;
const h2Of = (tile) => tile.find((e) => e.tagName === 'H2')[0].textContent;
const byClass = (root, cls) => root.find((e) => e.className === cls);
const button = (root, label) => root.find((e) => e.tagName === 'BUTTON' && e.textContent === label)[0];
const visibleLinks = (root) => root.find((e) => e.tagName === 'A' && !e.hidden);
const runSnap = (page, list) => page.db.handlers('runs').next(snap(list));
const agentSnap = (page, list) => page.db.handlers('agents').next(snap(list));
const agentsQueries = (page) => page.db.calls.subsBy.agents ?? 0;
const used = (page, cap) => page.uses.filter((u) => u === cap).length;

const runRow = (o = {}) => ({ _docId: RUN, feature: 'Voice speed', phase: 'building', round: 1,
  started_at: iso(NOW - 80 * MIN), updated_at: iso(NOW - 5 * MIN), session_url: SESSION, ...o });
const agent = (id, o = {}) => ({ _docId: id, run_id: RUN, role: 'builder', piece: '', round: 1, state: 'running',
  started_at: iso(NOW - 10 * MIN), updated_at: iso(NOW - 10 * MIN), ...o });

test('Runs is the default: only db is looked up, only the runs query, no Chat button', async () => {
  const page = await loadPage();
  assert.equal(page.ids.main.dataset.view, 'runs');
  assert.equal(page.ids['grid-view'].hidden, true);
  assert.equal(page.ids['runs-view'].hidden, false);
  assert.equal(page.ids['view-runs'].getAttribute('aria-pressed'), 'true');
  assert.deepEqual([...page.uses], ['db']);
  assert.deepEqual(Object.keys(page.db.calls.queries), ['runs']);
  assert.equal(agentsQueries(page), 0);
  assert.equal(page.comments.calls.can, 0);
  assert.equal(page.comments.calls.send.length, 0);
  assert.equal(page.pending().length, 0);
  // Runs renders as before; a card keeps its Session and PR links only.
  runSnap(page, [runRow({ pr_url: `${PR}7` })]);
  assert.equal(page.ids.summary.textContent, 'Nothing needs you · 1 running');
  const [card] = page.ids.runs.children;
  assert.deepEqual(card.find((e) => e.tagName === 'A').map((a) => a.textContent), ['Session', 'PR #7']);
  assert.equal(card.find((e) => e.tagName === 'BUTTON').length, 0, 'no Chat button');
  assert.deepEqual(Object.keys(page.db.calls.queries), ['runs'], 'no chat query on Runs');
});

test('Grid starts one agents query and one user and comments lookup; Runs pauses it, Grid resumes once', async () => {
  const page = await gridPage();
  assert.equal(page.ids.main.dataset.view, 'grid');
  assert.equal(page.ids['grid-view'].hidden, false);
  assert.deepEqual(page.db.calls.queries.agents, ['agents', 'started_at', 'desc', 200]);
  assert.equal(agentsQueries(page), 1);
  assert.equal(used(page, 'comments'), 1);
  assert.equal(used(page, 'user'), 1);
  assert.equal(page.userCalls.id, 1);
  assert.equal(page.comments.calls.can, 1, 'canSendToClaude is checked on the Grid show');
  page.ids['view-grid'].click();
  await settle();
  assert.equal(agentsQueries(page), 1, 'a second Grid click does not stack subscriptions');
  runSnap(page, [runRow()]);
  const paused = page.db.handlers('agents');
  page.ids['view-runs'].click();
  assert.equal(page.db.calls.unsubsBy.agents, 1);
  assert.equal(page.db.calls.unsubs, 0, 'runs keeps its subscription');
  paused.next(snap([agent('late')]));
  assert.equal(agentTiles(page.ids.coords).length, 0, 'a late snapshot from the paused listener is dropped');
  page.ids['view-grid'].click();
  await settle();
  assert.equal(agentsQueries(page), 2, 'coming back subscribes once more');
  assert.equal(used(page, 'comments'), 1, 'the comments lookup is memoized');
  assert.equal(used(page, 'user'), 1, 'the user id is looked up on the first Grid show only');
  assert.equal(page.comments.calls.can, 3, 're-checked on each Grid show');
  assert.equal(page.db.calls.subs, 1);
  assert.equal(page.pending().length, 0);
  assert.equal(page.intervals.length, 1, 'only the one re-render interval');
  assert.equal(page.comments.calls.send.length, 0, 'nothing sends by itself');
});

test('the coordinator window: feature, phase, round, elapsed, newest 5 status lines, links in the title bar', async () => {
  const page = await gridPage();
  const at = (m) => iso(NOW - m * MIN);
  runSnap(page, [
    runRow({ phase: 'judging', round: 2, pr_url: `${PR}12`, status_lines: [
      { at: at(70), text: 'Started' }, { at: at(60), text: 'Researching' }, { at: at(20), text: 'Judging round 2' },
      { at: 'not a time', text: 'bad time' }, { at: at(19), text: '' }, null, 'just a string', [1, 2],
    ] }),
    runRow({ _docId: `${RUN}-settings`, feature: 'Settings piece', phase: 'building', round: 1,
      started_at: iso(NOW - 50 * MIN), status_lines: [{ at: at(50), text: 'Building round 1' }, { at: at(40), text: 'y'.repeat(300) },
        { at: at(30), text: '<img src=x onerror=alert(1)>' }] }),
  ]);
  const [panel] = panels(page);
  assert.equal(panels(page).length, 1, 'rows sharing a session make one window');
  assert.equal(panel.id, `coord-${RUN}`);
  assert.equal(panel.dataset.role, 'coordinator');
  assert.equal(h2Of(panel), 'Voice speed');
  assert.equal(byClass(panel, 'kicker')[0].textContent, 'Coordinator');
  assert.equal(byClass(panel, 'runid')[0].textContent, `run ${RUN}`);
  const whens = byClass(panel, 'when').map((e) => e.textContent);
  assert.ok(whens.includes('Voice speed: judging · round 2 · updated 5 min ago'), whens.join(' | '));
  assert.ok(whens.includes('Settings piece: building · round 1 · updated 5 min ago'), whens.join(' | '));
  assert.ok(whens.includes('elapsed 1 h 20 min'), 'elapsed runs from the earliest start: ' + whens.join(' | '));
  const [ul] = byClass(panel, 'lines');
  const lines = ul.children.map((li) => li.children[1].textContent);
  // Merged across both rows by time, oldest first, the newest 5; bad items dropped.
  assert.deepEqual(lines, ['Researching', 'Building round 1', 'y'.repeat(99) + '…',
    '<img src=x onerror=alert(1)>', 'Judging round 2']);
  assert.ok(lines.every((l) => l.length <= 100));
  assert.equal(panel.find((e) => e.tagName === 'IMG').length, 0, 'markup stays text');
  const head = byClass(panel, 'tile-head')[0];
  assert.deepEqual(visibleLinks(head).map((a) => [a.textContent, a.href, a.rel]).sort(), [
    ['Open session', SESSION, 'noopener noreferrer'],
    ['PR #12', `${PR}12`, 'noopener noreferrer'],
  ]);
  assert.equal(byClass(head, 'round')[0].textContent, 'round 2', 'the most urgent row\'s round');
  assert.equal(byClass(head, 'chip').length, 1);
  assert.equal(button(head, '⤢').getAttribute('aria-label'), 'Maximize Voice speed');
});

test('a bad session or PR link gets no anchor; title markup stays text', async () => {
  const page = await gridPage();
  const markup = '<img src=x onerror=alert(1)><b>hi</b>';
  runSnap(page, [runRow({ feature: markup, session_url: `https://evil.example/?${SESSION}`, pr_url: 'javascript:alert(1)' })]);
  const [panel] = panels(page);
  assert.equal(h2Of(panel), markup);
  assert.equal(panel.find((e) => ['IMG', 'B'].includes(e.tagName)).length, 0);
  assert.deepEqual(visibleLinks(panel), [], 'no Open session, no PR link');
  for (const a of panel.find((e) => e.tagName === 'A')) assert.ok(a.href.startsWith('https://claude.ai/code/session_'));
  assert.equal(byClass(panel, 'chat')[0].hidden, true, 'no session: no chat');
});

test('grouping: pieces and the plain row share a window; agents follow it; unmatched are counted', async () => {
  const page = await gridPage();
  runSnap(page, [
    runRow({ phase: 'done', updated_at: iso(NOW - 70 * MIN) }),
    runRow({ _docId: `${RUN}-settings`, feature: 'Settings', phase: 'building' }),
    runRow({ _docId: `${RUN}-ui`, feature: 'UI', phase: 'judging' }),
    runRow({ _docId: 'other-run', feature: 'Other', session_url: undefined, phase: 'done', updated_at: iso(NOW - DAY) }),
  ]);
  agentSnap(page, [
    agent(`${RUN}.researcher.r0`, { role: 'researcher', round: 0, state: 'done', finished_at: iso(NOW - 70 * MIN) }),
    agent(`${RUN}-settings.builder.r1`, { run_id: `${RUN}-settings`, piece: 'settings' }),
    agent(`${RUN}-ui.builder.r1`, { run_id: `${RUN}-ui`, piece: 'ui', state: 'done', finished_at: iso(NOW - 20 * MIN) }),
    agent(`${RUN}-ui.judge.r1`, { run_id: `${RUN}-ui`, role: 'judge', piece: 'ui' }),
    agent('elsewhere.builder.r1', { run_id: 'elsewhere' }),
    agent('other-run.builder.r1', { run_id: 'other-run' }),
  ]);
  assert.equal(panels(page).length, 1, 'the done plain row without a live piece is not its own window');
  const [panel] = panels(page);
  assert.equal(panel.id, `coord-${RUN}`);
  assert.equal(h2Of(panel), 'Voice speed');
  const whens = byClass(panel, 'when').map((e) => e.textContent);
  assert.ok(!whens.some((w) => w.startsWith('Voice speed:')), 'the plain done row hands over to its pieces');
  const tiles = agentTiles(page.ids.coords);
  // Running first (newest start first), then done.
  assert.deepEqual(tiles.map(h2Of), ['builder · settings', 'judge · ui', 'researcher', 'builder · ui']);
  assert.deepEqual(tiles.map((t) => t.id), [`agent-${RUN}-settings.builder.r1`, `agent-${RUN}-ui.judge.r1`,
    `agent-${RUN}.researcher.r0`, `agent-${RUN}-ui.builder.r1`]);
  assert.deepEqual(tiles.map((t) => t.dataset.role), ['builder', 'judge', 'researcher', 'builder']);
  assert.deepEqual(tiles.map((t) => byClass(t, 'kicker')[0].textContent), ['builder', 'judge', 'researcher', 'builder']);
  // Agents link to their run's master session.
  assert.deepEqual(visibleLinks(tiles[0]).map((a) => a.href), [SESSION]);
  // Counted: only the live agent whose run has no row at all ('other-run'
  // has a row, just no window).
  assert.equal(page.ids['grid-hidden'].textContent, '1 agent without a run');
  assert.equal(page.ids['grid-hidden'].hidden, false);
  assert.equal(page.ids['grid-summary'].textContent, '1 active run · 2 agents running');
  assert.equal(page.ids['grid-count'].textContent, '5 windows');
  const api = page.api;
  const g = api.groupCoordinators([{ _id: 'a', phase: 'building', updated_at: iso(NOW) }], [{ run_id: 'b' }], NOW);
  assert.deepEqual([g.panels.length, g.hidden], [1, 1]);
  // Window ids keep only safe characters.
  const odd = api.groupCoordinators([{ _id: 'r+1@x~y', phase: 'building', updated_at: iso(NOW) }], [], NOW);
  assert.equal(odd.panels[0].id, 'coord-r_1_x_y');

  // No active run: the newest group shows as "Last run".
  runSnap(page, [
    runRow({ phase: 'done', updated_at: iso(NOW - 2 * DAY) }),
    runRow({ _docId: 'older', feature: 'Older', session_url: undefined, phase: 'done',
      started_at: iso(NOW - 5 * DAY), updated_at: iso(NOW - 4 * DAY) }),
  ]);
  assert.equal(panels(page).length, 1);
  assert.equal(h2Of(panels(page)[0]), 'Voice speed');
  assert.equal(byClass(panels(page)[0], 'kicker')[0].textContent, 'Coordinator · Last run');
  assert.equal(page.ids['grid-summary'].textContent, 'Nothing running · last run below');
  runSnap(page, []);
  assert.equal(panels(page).length, 0);
  assert.equal(page.ids['grid-summary'].textContent, 'No runs yet');
  assert.equal(page.ids['grid-count'].textContent, '0 windows');
});

test('classifyAgent: limits per role, stopped under a finished run, duration, clamps, unknowns, retire', async () => {
  const { api } = await loadPage();
  assert.deepEqual({ ...api.AGENT_PHASE }, { researcher: 'researching', builder: 'building', judge: 'judging' });
  assert.equal(api.OUTCOME_MAX, 80);
  assert.equal(api.STATUS_LINES_MAX, 5);
  const live = { phase: 'building', updated_at: iso(NOW - MIN) };
  const run = (role, mins) => api.classifyAgent({ role, state: 'running', started_at: iso(NOW - mins * MIN),
    updated_at: iso(NOW - mins * MIN) }, live, NOW);
  assert.deepEqual([run('builder', 89).state, run('builder', 89).badge], ['running', 'running']);
  assert.deepEqual([run('builder', 91).state, run('builder', 91).badge], ['silent', 'silent']);
  assert.match(run('builder', 91).note, /limit 90 min/);
  for (const role of ['researcher', 'judge']) {
    assert.equal(run(role, 44).state, 'running', role);
    assert.equal(run(role, 46).state, 'silent', role);
  }
  assert.equal(run('builder', 30).durationMs, 30 * MIN, 'running: now - started');
  // A start time from the future (beyond 10 min of clock skew) is unreadable.
  const future = run('builder', -11);
  assert.deepEqual([future.state, future.badge, future.note, future.durationMs], ['silent', 'silent', 'start time unreadable', null]);
  assert.equal(run('builder', -5).state, 'running', 'small skew is fine');
  for (const phase of ['done', 'dead', 'pr_ready', 'blocked']) {
    for (const state of ['running', 'queued']) {
      const c = api.classifyAgent({ role: 'builder', state, started_at: iso(NOW - MIN) },
        { phase, updated_at: iso(NOW - MIN) }, NOW);
      assert.deepEqual([c.state, c.badge], ['stopped', 'silent'], `${state} under ${phase}`);
      assert.match(c.label, /^stopped \(run /);
    }
  }
  assert.equal(api.classifyAgent({ role: 'builder', state: 'running', started_at: iso(NOW - MIN) },
    { phase: 'done' }, NOW).label, 'stopped (run done)');
  const done = api.classifyAgent({ role: 'judge', state: 'done', started_at: iso(NOW - 50 * MIN),
    finished_at: iso(NOW - 20 * MIN), outcome: 'z'.repeat(200) }, live, NOW);
  assert.deepEqual([done.state, done.badge, done.durationMs], ['done', 'done', 30 * MIN]);
  assert.equal(done.outcome.length, 80);
  assert.equal(api.classifyAgent({ role: 'judge', state: 'failed', started_at: iso(NOW - MIN) }, live, NOW).badge, 'failed');
  assert.equal(api.classifyAgent({ role: 'judge', state: 'queued' }, live, NOW).badge, 'queued');
  const odd = api.classifyAgent({ role: '<b>Reviewer</b>', state: 'Pondering', started_at: iso(NOW - MIN) }, live, NOW);
  assert.deepEqual([odd.role, odd.state, odd.label, odd.badge], ['<b>reviewer</b>', 'unknown', 'pondering', 'queued']);
  assert.equal(api.classifyAgent({ role: 'constructor', state: 'running', started_at: iso(NOW - 9 * 60 * MIN) }, live, NOW).state,
    'running', 'an unknown role has no limit');
  for (const bad of [null, 'x', 42, []]) assert.equal(typeof api.classifyAgent(bad, null, NOW).state, 'string');
  // Retired: updated_at (or finished_at) older than 7 days.
  const old = (o) => api.classifyAgent({ role: 'builder', state: 'done', started_at: iso(NOW - 9 * DAY), ...o }, live, NOW).retired;
  assert.equal(old({ updated_at: iso(NOW - 7 * DAY - MIN) }), true);
  assert.equal(old({ updated_at: iso(NOW - 7 * DAY + MIN) }), false);
  assert.equal(old({ finished_at: iso(NOW - 8 * DAY) }), true);
  assert.equal(old({ updated_at: iso(NOW - 8 * DAY), finished_at: iso(NOW - DAY) }), false);
});

test('agent windows render text only, with the retired and unknown rules', async () => {
  const page = await gridPage();
  runSnap(page, [runRow()]);
  agentSnap(page, [
    agent('a1', { role: '<script>x</script>', state: 'weird', outcome: '<b>built</b>, tests green' }),
    agent('a2', { state: 'done', finished_at: iso(NOW - 8 * DAY), updated_at: iso(NOW - 8 * DAY), started_at: iso(NOW - 9 * DAY) }),
    agent('a3', { state: 'done', started_at: iso(NOW - 40 * MIN), finished_at: iso(NOW - 10 * MIN), outcome: 'built, tests green' }),
  ]);
  const tiles = agentTiles(page.ids.coords);
  assert.deepEqual(tiles.map((t) => t.id), ['agent-a3', 'agent-a1'], 'the retired window is gone; unknown sorts last');
  const [a3, a1] = tiles;
  assert.equal(h2Of(a1), '<script>x</script>');
  assert.equal(byClass(a1, 'status')[0].textContent, '<b>built</b>, tests green');
  assert.equal(a1.find((e) => ['SCRIPT', 'B'].includes(e.tagName)).length, 0);
  assert.equal(byClass(a1, 'label')[0].textContent, 'weird');
  assert.match(byClass(a3, 'when')[0].textContent, /^round 1 · started .+ · 30 min$/);
  assert.equal(page.ids['grid-hidden'].hidden, true, 'retired windows are not counted as hidden');
});

test('each state gets its badge class, on agent windows and on the coordinator window', async () => {
  const page = await gridPage();
  runSnap(page, [runRow()]);
  agentSnap(page, [
    agent('run', { state: 'running' }),
    agent('que', { state: 'queued' }),
    agent('don', { state: 'done', finished_at: iso(NOW - MIN) }),
    agent('fai', { state: 'failed', finished_at: iso(NOW - MIN) }),
    agent('sil', { state: 'running', started_at: iso(NOW - 120 * MIN) }),
  ]);
  const badgeOf = (t) => t.find((e) => e.className.startsWith('badge '))[0];
  const want = { 'agent-run': 'running', 'agent-que': 'queued', 'agent-don': 'done', 'agent-fai': 'failed', 'agent-sil': 'silent' };
  for (const t of agentTiles(page.ids.coords)) {
    assert.equal(badgeOf(t).className, `badge badge-${want[t.id]}`, t.id);
    assert.equal(t.dataset.badge, want[t.id]);
    assert.equal(byClass(badgeOf(t), 'dot').length, 1);
  }
  const coordBadge = (o) => {
    runSnap(page, [runRow(o)]);
    const b = badgeOf(panels(page)[0]);
    return [b.className, byClass(b, 'label')[0].textContent];
  };
  assert.deepEqual(coordBadge({ phase: 'building' }), ['badge badge-running', 'running']);
  assert.deepEqual(coordBadge({ phase: 'pr_ready' }), ['badge badge-done', 'PR ready']);
  assert.deepEqual(coordBadge({ phase: 'blocked' }), ['badge badge-failed', 'blocked']);
  assert.deepEqual(coordBadge({ phase: 'dead' }), ['badge badge-failed', 'dead (watchdog)']);
  assert.deepEqual(coordBadge({ phase: 'building', updated_at: iso(NOW - 100 * MIN) }), ['badge badge-silent', 'silent']);
  // Several rows: the badge comes from the most urgent one (silent, then
  // needs you, then running, then done).
  const mixed = (...rows) => {
    runSnap(page, rows.map((o, i) => runRow({ _docId: i ? `${RUN}-p${i}` : RUN, ...o })));
    return badgeOf(panels(page)[0]).className;
  };
  const silentRow = { phase: 'building', updated_at: iso(NOW - 100 * MIN) };
  assert.equal(mixed({ phase: 'building' }, silentRow, { phase: 'done' }), 'badge badge-silent');
  assert.equal(mixed({ phase: 'done' }, { phase: 'building' }, { phase: 'pr_ready' }), 'badge badge-done',
    'needs you (PR ready) beats running');
  assert.equal(mixed({ phase: 'done' }, { phase: 'judging' }, { phase: 'done' }), 'badge badge-running', 'running beats done');
  // A running agent under a finished run is "stopped", in the silent style.
  runSnap(page, [runRow({ phase: 'done' })]);
  assert.equal(badgeOf(agentTiles(page.ids.coords).find((t) => t.id === 'agent-run')).className, 'badge badge-silent');
  assert.match(page.ids.coords.textContent, /stopped \(run done\)/);
});

// ---- agents query errors: the same table as runs ----

const STOP_AGENTS = ['revoked', 'invalid_argument', 'transform_error', 'not_granted', 'capability_disabled', 'capability_removed'];
for (const code of STOP_AGENTS) {
  test(`agents error ${code} clears the agent windows for good and leaves Runs alone`, async () => {
    const page = await gridPage();
    runSnap(page, [runRow()]);
    agentSnap(page, [agent('a1')]);
    assert.equal(agentTiles(page.ids.coords).length, 1);
    page.db.handlers('agents').error({ code, message: 'SECRET' });
    assert.equal(agentTiles(page.ids.coords).length, 0);
    assert.equal(panels(page).length, 1, 'the coordinator window stays');
    assert.equal(page.ids['grid-conn'].dataset.level, 'bad');
    assert.ok(!page.ids['grid-conn'].textContent.includes('SECRET'));
    assert.equal(page.pending().length, 0);
    assert.equal(page.db.calls.subs, 1);
    assert.equal(page.ids.runs.children.length, 1, 'Runs keeps its rows');
    assert.equal(page.ids.conn.textContent, 'live');
    page.ids['view-runs'].click();
    page.ids['view-grid'].click();
    await settle();
    assert.equal(agentsQueries(page), 1, 'never resubscribed');
  });
}

for (const err of [{ code: 'unavailable' }, { code: 'brand_new' }, null]) {
  test(`agents error ${JSON.stringify(err)} retries once after 5-30 s`, async () => {
    const page = await gridPage();
    page.db.handlers('agents').error(err);
    const timers = page.pending();
    assert.equal(timers.length, 1);
    assert.ok(timers[0].ms >= 5e3 && timers[0].ms <= 30e3, String(timers[0].ms));
    assert.match(page.ids['grid-conn'].textContent, /^Connection lost · retrying in \d+ s$/);
    assert.equal(agentsQueries(page), 1);
    timers[0].fn();
    assert.equal(agentsQueries(page), 2);
    assert.equal(page.db.calls.subs, 1, 'runs untouched');
  });
}

test('agents resource_exhausted backs off with doubling; Runs view cancels a pending retry', async () => {
  const page = await gridPage();
  const fire = () => { const t = page.pending().at(-1); t.cleared = true; t.fn(); };
  for (const [i, base] of [60e3, 120e3, 240e3].entries()) {
    page.db.handlers('agents').error({ code: 'resource_exhausted' });
    const [t] = page.pending();
    assert.ok(t.ms >= base * 0.75 && t.ms <= base * 1.25, `${t.ms} near ${base}`);
    assert.equal(agentsQueries(page), i + 1);
    fire();
  }
  page.db.handlers('agents').error({ code: 'resource_exhausted' });
  assert.equal(page.pending().length, 1);
  page.ids['view-runs'].click();
  assert.equal(page.pending().length, 0, 'leaving Grid clears the retry timer');
  const old = page.db.handlers('agents');
  old.error({ code: 'unavailable' });
  assert.equal(page.pending().length, 0, 'a late error from a paused listener is ignored');
});

// ---- the workspace: flat windows, auto-tiling, spans, run colours ----

test('tileCols: about square, at most 6, clamped by width', async () => {
  const { api } = await loadPage();
  assert.deepEqual([1, 2, 3, 4, 5, 6, 7, 8, 9, 10].map((n) => api.tileCols(n, 0)), [1, 2, 2, 2, 3, 3, 3, 3, 3, 4]);
  assert.equal(api.tileCols(10, 700), 2, '(700 + 10) / (260 + 10) = 2 columns fit');
  assert.equal(api.tileCols(10, 1400), 4);
  assert.equal(api.tileCols(50, 0), 6, 'never more than 6');
  assert.equal(api.tileCols(50, 3000), 6);
  assert.equal(api.tileCols(10, 100), 1, 'a very narrow frame still gets one column');
  assert.equal(api.tileCols(10, 300), 1, 'the width cap: (300 + 10) / (260 + 10) = 1');
  assert.equal(api.tileCols(10, NaN), 4, 'an unreadable width means no width cap');
  assert.equal(api.tileCols(0, 0), 1);
  assert.equal(api.tileCols.length, 2, 'no phone argument');
});

test('the workspace is #coords itself: flat windows in order, columns set, re-tiled on resize', async () => {
  const page = await gridPage();
  const B = 'https://claude.ai/code/session_second';
  runSnap(page, [
    runRow(),
    runRow({ _docId: 'second-run', feature: 'Second', session_url: B, started_at: iso(NOW - 200 * MIN), updated_at: iso(NOW - 30 * MIN) }),
  ]);
  agentSnap(page, [
    agent('a-done', { state: 'done', finished_at: iso(NOW - MIN) }),
    agent('a-run', {}),
    agent('b-run', { run_id: 'second-run' }),
  ]);
  const kids = windows(page);
  // Newest run first; each coordinator followed by its agents in rank order.
  assert.deepEqual(kids.map((t) => t.id), [`coord-${RUN}`, 'agent-a-run', 'agent-a-done', 'coord-second-run', 'agent-b-run']);
  assert.ok(kids.every((t) => t.tagName === 'ARTICLE' && ['tile coord', 'tile agent'].includes(t.className)));
  assert.equal(page.ids.coords.find((e) => /\bboard\b/.test(e.className)).length, 0, 'no boards');
  assert.equal(page.ids.coords.dataset.cols, '3', '5 windows: 3 columns');
  // A resize re-tiles: a 700 px frame fits 2 columns.
  page.ids.coords.rect = { left: 0, top: 0, right: 700, bottom: 600, width: 700, height: 600 };
  page.win('resize');
  assert.equal(page.ids.coords.dataset.cols, '2');
  page.ids.coords.rect = { left: 0, top: 0, right: 2000, bottom: 600, width: 2000, height: 600 };
  page.win('resize');
  assert.equal(page.ids.coords.dataset.cols, '3');
});

test('the coordinator starts 2x2 only with 3+ columns; spans are clamped to the columns, saved sizes kept', async () => {
  const storage = memStorage();
  const page = await gridPage({ storage });
  runSnap(page, [runRow()]);
  agentSnap(page, [agent('a1')]);
  const coord = panels(page)[0];
  assert.equal(page.ids.coords.dataset.cols, '2');
  assert.deepEqual([coord.dataset.cols, coord.dataset.rows], ['1', '1'], '2 windows: 2 columns, the coordinator 1x1');
  agentSnap(page, [agent('a1'), agent('a2'), agent('a3'), agent('a4')]);
  assert.equal(page.ids.coords.dataset.cols, '3');
  assert.deepEqual([coord.dataset.cols, coord.dataset.rows], ['2', '2'], '5 windows: 3 columns, the coordinator 2x2');
  // A window saved 3 wide shows 2 wide in a 2-column workspace; the saved size stays.
  const a1 = agentTiles(page.ids.coords).find((t) => t.id === 'agent-a1');
  const r = byClass(a1, 'resize')[0];
  r.dispatch('keydown', { key: 'ArrowRight', shiftKey: true });
  r.dispatch('keydown', { key: 'ArrowRight', shiftKey: true });
  assert.equal(a1.dataset.cols, '3');
  agentSnap(page, [agent('a1')]);
  assert.equal(page.ids.coords.dataset.cols, '2');
  assert.equal(a1.dataset.cols, '2', 'clamped for display');
  r.dispatch('keydown', { key: 'ArrowDown', shiftKey: true });
  assert.deepEqual({ ...JSON.parse(storage.store[LAYOUT]).tiles.a1 }, { c: 3, r: 2 }, 'the saved width is kept');
});

test('run colour: one hue per run, shared by its agents and pieces, deterministic, 0-7', async () => {
  const page = await gridPage();
  const { api } = page;
  const ids = ['a', 'b', RUN, '20261019-110000-new', 'x'.repeat(200), ''];
  for (const id of ids) {
    const h = api.runHue(id);
    assert.ok(Number.isInteger(h) && h >= 0 && h <= 7, `${id}: ${h}`);
    assert.equal(api.runHue(id), h, 'deterministic');
  }
  // FNV-1a 32-bit of "a" is 0xe40c292c.
  assert.equal(api.runHue('a'), 0xe40c292c % 8);
  const seen = new Set();
  for (let i = 0; i < 64; i += 1) seen.add(api.runHue(`run-${i}`));
  assert.equal(seen.size, 8, 'all 8 hues are used');
  let other = 'other-0';
  for (let i = 1; api.runHue(other) === api.runHue(RUN); i += 1) other = `other-${i}`;
  runSnap(page, [runRow(), runRow({ _docId: `${RUN}-ui`, feature: 'UI' }),
    runRow({ _docId: other, feature: 'Other', session_url: 'https://claude.ai/code/session_other' })]);
  agentSnap(page, [agent('p1', { run_id: `${RUN}-ui`, piece: 'ui' }), agent('o1', { run_id: other })]);
  const hue = (id) => windows(page).find((t) => t.id === id).dataset.hue;
  assert.equal(hue(`coord-${RUN}`), String(api.runHue(RUN)));
  assert.equal(hue('agent-p1'), hue(`coord-${RUN}`), 'a piece agent shares its run family\'s colour');
  assert.equal(hue('agent-o1'), hue(`coord-${other}`));
  assert.notEqual(hue(`coord-${other}`), hue(`coord-${RUN}`));
});

// ---- maximize ----

test('maximize: one window fills the workspace; handles hidden; drag a no-op; Esc and Restore go back', async () => {
  const storage = memStorage();
  const page = await layoutPage({ storage });
  const a2 = tile(page, 'agent-a2');
  const max = byClass(a2, 'max')[0];
  const restore = byClass(a2, 'restore')[0];
  assert.equal(max.getAttribute('aria-label'), 'Maximize builder');
  assert.equal(max.getAttribute('aria-pressed'), 'false');
  assert.equal(restore.hidden, true);
  // A grab in progress is dropped by maximize.
  const g1 = gripOf(tile(page, 'agent-a1'));
  g1.dispatch('keydown', { key: 'Enter' });
  assert.equal(g1.getAttribute('aria-pressed'), 'true');
  max.click();
  assert.equal(g1.getAttribute('aria-pressed'), 'false');
  assert.equal(max.getAttribute('aria-pressed'), 'true');
  assert.ok('max' in a2.dataset);
  assert.ok('max' in page.ids.coords.dataset);
  assert.equal(windows(page).filter((t) => 'max' in t.dataset).length, 1);
  assert.equal(restore.hidden, false);
  assert.equal(restore.focused, 1, 'focus moves to Restore');
  for (const t of windows(page)) {
    assert.equal(gripOf(t).hidden, true);
    assert.equal(resizeOf(t).hidden, true);
  }
  // Drag, keyboard moves and resizes do nothing while maximized.
  const before = order(page);
  gripOf(a2).dispatch('pointerdown', { button: 0, pointerId: 1, clientX: 245, clientY: 40 });
  page.win('pointerup', { pointerId: 1, clientX: 45, clientY: 40 });
  gripOf(a2).dispatch('keydown', { key: 'Enter' });
  gripOf(a2).dispatch('keydown', { key: 'ArrowLeft' });
  resizeOf(a2).dispatch('pointerdown', { button: 0, pointerId: 2, clientX: 290, clientY: 90 });
  resizeOf(a2).dispatch('keydown', { key: 'ArrowRight', shiftKey: true });
  assert.deepEqual(order(page), before);
  assert.equal(storage.store[LAYOUT], undefined, 'nothing saved');
  assert.equal(page.winListeners.pointerup?.length ?? 0, 0);
  // Esc another handler already used is left alone; a plain Esc restores.
  page.doc('keydown', { key: 'Escape', defaultPrevented: true });
  assert.ok('max' in a2.dataset);
  const ev = page.doc('keydown', { key: 'Escape' });
  assert.equal(ev.defaultPrevented, true);
  assert.equal('max' in a2.dataset, false);
  assert.equal('max' in page.ids.coords.dataset, false);
  assert.equal(max.getAttribute('aria-pressed'), 'false');
  assert.equal(max.focused, 1, 'focus returns to the maximize button');
  assert.equal(gripOf(a2).hidden, false);
  // Restore does the same.
  max.click();
  restore.click();
  assert.equal('max' in a2.dataset, false);
  assert.equal(max.focused, 2);
  // The grip's Escape (dropping a grab) is marked handled, so it never restores too.
  gripOf(a2).dispatch('keydown', { key: 'Enter' });
  const drop = gripOf(a2).dispatch('keydown', { key: 'Escape' });
  assert.equal(drop.defaultPrevented, true);
  assert.equal(gripOf(a2).getAttribute('aria-pressed'), 'false');
});

test('a maximized window that disappears restores the workspace; Runs drops it too', async () => {
  const page = await layoutPage();
  byClass(tile(page, 'agent-a3'), 'max')[0].click();
  assert.ok('max' in page.ids.coords.dataset);
  agentSnap(page, [agent('a1', { started_at: iso(NOW - MIN) }), agent('a2', { started_at: iso(NOW - 2 * MIN) })]);
  assert.equal('max' in page.ids.coords.dataset, false);
  assert.equal(windows(page).filter((t) => 'max' in t.dataset).length, 0);
  assert.equal(page.ids['grid-live'].textContent, 'Restored: that window closed.');
  assert.equal(gripOf(tile(page, 'agent-a1')).hidden, false);
  // Maximizing again toggles off from its own button.
  const m = byClass(panels(page)[0], 'max')[0];
  m.click();
  assert.equal(m.getAttribute('aria-pressed'), 'true');
  m.click();
  assert.equal(m.getAttribute('aria-pressed'), 'false');
});

// ---- the filter ----

test('filter: Active only by default; All recent shows every recent session; stored only on click', async () => {
  const storage = memStorage();
  const page = await gridPage({ storage });
  const B = 'https://claude.ai/code/session_bbb';
  const C = 'https://claude.ai/code/session_ccc';
  runSnap(page, [
    runRow(),
    runRow({ _docId: 'done-run', feature: 'Done run', session_url: B, phase: 'done', started_at: iso(NOW - 3 * DAY), updated_at: iso(NOW - 2 * DAY) }),
    runRow({ _docId: 'ancient', feature: 'Ancient', session_url: C, phase: 'done', started_at: iso(NOW - 30 * DAY), updated_at: iso(NOW - 20 * DAY) }),
  ]);
  agentSnap(page, [agent('a1'), agent('d1', { run_id: 'done-run', state: 'done', finished_at: iso(NOW - 2 * DAY), updated_at: iso(NOW - 2 * DAY) }),
    agent('d-old', { run_id: 'done-run', state: 'done', finished_at: iso(NOW - 9 * DAY), updated_at: iso(NOW - 9 * DAY) })]);
  const active = page.ids['grid-filter-active'];
  const all = page.ids['grid-filter-all'];
  assert.equal(active.getAttribute('aria-pressed'), 'true');
  assert.equal(all.getAttribute('aria-pressed'), 'false');
  assert.deepEqual(panels(page).map(h2Of), ['Voice speed']);
  assert.equal(page.ids['grid-count'].textContent, '2 windows');
  assert.equal(storage.store['agentDashboard.filter'], undefined, 'nothing stored on load');
  all.click();
  assert.equal(storage.store['agentDashboard.filter'], 'all');
  assert.equal(all.getAttribute('aria-pressed'), 'true');
  assert.equal(active.getAttribute('aria-pressed'), 'false');
  assert.deepEqual(panels(page).map(h2Of), ['Voice speed', 'Done run'], 'the 20-day-old session stays hidden');
  assert.deepEqual(agentTiles(page.ids.coords).map((t) => t.id), ['agent-a1', 'agent-d1'], 'retired agents stay hidden');
  assert.equal(page.ids['grid-count'].textContent, '4 windows');
  assert.equal(byClass(panels(page)[1], 'kicker')[0].textContent, 'Coordinator');
  // Only active runs: nothing running still shows the last run.
  runSnap(page, [runRow({ phase: 'done', updated_at: iso(NOW - DAY) }),
    runRow({ _docId: 'done-run', feature: 'Done run', session_url: B, phase: 'done', started_at: iso(NOW - 3 * DAY), updated_at: iso(NOW - 2 * DAY) })]);
  assert.equal(page.ids['grid-summary'].textContent, 'Nothing running · 1 silent', 'a1 stopped under a done run');
  assert.equal(panels(page).length, 2);
  // A reload keeps the choice; Active only goes back.
  const again = await gridPage({ storage });
  assert.equal(again.ids['grid-filter-all'].getAttribute('aria-pressed'), 'true');
  again.ids['grid-filter-active'].click();
  assert.equal(storage.store['agentDashboard.filter'], 'active');
  assert.equal(again.api.FILTER_KEY, 'agentDashboard.filter');
});

// ---- desktop only: no phone layout ----

test('desktop only: no compact or bare chrome, the header, toolbar and footer stay while maximized', async () => {
  const page = await layoutPage();
  const { main } = page.ids;
  assert.equal('compact' in main.dataset, false);
  assert.equal(page.ids['grid-head'].hidden, false);
  assert.equal(page.ids['grid-foot'].hidden, false);
  assert.equal(page.ids['grid-reset'].hidden, false);
  assert.equal(page.ids['grid-count'].textContent, '5 windows');
  const coord = panels(page)[0];
  byClass(coord, 'max')[0].click();
  assert.equal('bare' in main.dataset, false);
  assert.equal(page.ids['view-nav'].hidden, false, 'the toolbar stays while maximized');
  assert.equal(page.ids['grid-toolbar'].hidden, false);
  const restore = byClass(coord, 'restore')[0];
  assert.equal(restore.hidden, false);
  restore.click();
  assert.equal(restore.hidden, true);
  assert.doesNotMatch(PAGE, /data-compact|data-bare|safe-area-inset|matchMedia|max-width:600px|pointer:coarse/);
});

// ---- window layout: drag, resize, keyboard, storage ----

const LAYOUT = 'agentDashboard.layout.v1';

// A coordinator plus four running agents (newest first: a1..a4), each
// window laid out 100 px apart so a pointer position names one window.
// Five windows: three columns, so the coordinator starts 2x2.
async function layoutPage(opts = {}) {
  const page = await gridPage(opts);
  runSnap(page, [runRow()]);
  agentSnap(page, [1, 2, 3, 4].map((i) => agent(`a${i}`, { started_at: iso(NOW - i * MIN) })));
  placeTiles(page);
  return page;
}
function placeTiles(page) {
  windows(page).forEach((t, i) => {
    t.rect = { left: i * 100, right: i * 100 + 90, top: 0, bottom: 90, width: 90, height: 90 };
  });
}
const order = (page) => windows(page).map((t) => t.id);
const gripOf = (t) => byClass(t, 'grip')[0];
const resizeOf = (t) => byClass(t, 'resize')[0];
const tile = (page, id) => windows(page).find((t) => t.id === id);
const saved = (storage) => JSON.parse(storage.store[LAYOUT]);
const C0 = `coord-${RUN}`;
const DEFAULT = [C0, 'agent-a1', 'agent-a2', 'agent-a3', 'agent-a4'];

test('dragging a window by its handle reorders the workspace and saves only then', async () => {
  const storage = memStorage();
  const page = await layoutPage({ storage });
  assert.deepEqual(order(page), DEFAULT);
  assert.equal(storage.store[LAYOUT], undefined, 'loading writes no layout');
  const g = gripOf(tile(page, 'agent-a3'));
  g.dispatch('pointerdown', { button: 0, pointerId: 1, clientX: 345, clientY: 40 });
  page.win('pointermove', { pointerId: 1, clientX: 145, clientY: 40 });
  assert.equal(tile(page, 'agent-a1').dataset.drop, '');
  assert.equal(storage.store[LAYOUT], undefined, 'nothing saved mid-drag');
  page.win('pointerup', { pointerId: 1, clientX: 145, clientY: 40 });
  assert.deepEqual(order(page), [C0, 'agent-a3', 'agent-a1', 'agent-a2', 'agent-a4']);
  assert.equal(tile(page, 'agent-a1').dataset.drop, undefined);
  const tiles = saved(storage).tiles;
  assert.deepEqual([tiles[C0].o, tiles.a3.o, tiles.a1.o, tiles.a2.o, tiles.a4.o], [0, 1, 2, 3, 4]);
  assert.equal(saved(storage).v, 2);
  // A reload keeps the order; a cancelled drag changes nothing.
  const again = await layoutPage({ storage });
  assert.deepEqual(order(again), [C0, 'agent-a3', 'agent-a1', 'agent-a2', 'agent-a4']);
  const before = storage.store[LAYOUT];
  const g2 = gripOf(tile(again, 'agent-a2'));
  g2.dispatch('pointerdown', { button: 0, pointerId: 2, clientX: 345, clientY: 40 });
  again.win('pointercancel', { pointerId: 2, clientX: 45, clientY: 40 });
  assert.deepEqual(order(again), [C0, 'agent-a3', 'agent-a1', 'agent-a2', 'agent-a4']);
  assert.equal(storage.store[LAYOUT], before);
  // Across mixed spans: the 2x2 coordinator moves like any window.
  placeTiles(again);
  gripOf(tile(again, C0)).dispatch('pointerdown', { button: 0, pointerId: 3, clientX: 45, clientY: 40 });
  again.win('pointerup', { pointerId: 3, clientX: 445, clientY: 40 });
  assert.deepEqual(order(again), ['agent-a3', 'agent-a1', 'agent-a2', 'agent-a4', C0]);
});

test('resizing snaps to 1-3 columns and 1-2 rows; defaults are 2x2 and 1x1', async () => {
  const storage = memStorage();
  const page = await layoutPage({ storage });
  const coord = panels(page)[0];
  assert.deepEqual([coord.dataset.cols, coord.dataset.rows], ['2', '2']);
  const a1 = tile(page, 'agent-a1');
  assert.deepEqual([a1.dataset.cols, a1.dataset.rows], ['1', '1']);
  const r = resizeOf(a1);
  r.dispatch('pointerdown', { button: 0, pointerId: 1, clientX: 190, clientY: 90 });
  page.win('pointermove', { pointerId: 1, clientX: 1990, clientY: 990 });
  assert.deepEqual([a1.dataset.cols, a1.dataset.rows], ['3', '2'], 'preview clamps');
  assert.equal(storage.store[LAYOUT], undefined, 'nothing saved mid-resize');
  page.win('pointerup', { pointerId: 1, clientX: 1990, clientY: 990 });
  assert.deepEqual([a1.dataset.cols, a1.dataset.rows], ['3', '2']);
  assert.deepEqual({ ...saved(storage).tiles.a1 }, { c: 3, r: 2 });
  // Shrinking far past the edge stops at 1x1.
  const rc = resizeOf(coord);
  rc.dispatch('pointerdown', { button: 0, pointerId: 2, clientX: 90, clientY: 90 });
  page.win('pointerup', { pointerId: 2, clientX: -5000, clientY: -5000 });
  assert.deepEqual([coord.dataset.cols, coord.dataset.rows], ['1', '1']);
  assert.deepEqual({ ...saved(storage).tiles[C0] }, { c: 1, r: 1 });
});

test('keyboard: Enter picks a window up, arrows move it; Shift+arrows resize', async () => {
  const storage = memStorage();
  const page = await layoutPage({ storage });
  const g = gripOf(tile(page, 'agent-a1'));
  g.dispatch('keydown', { key: 'ArrowRight' });
  assert.deepEqual(order(page), DEFAULT, 'arrows do nothing before Enter');
  g.dispatch('keydown', { key: 'Enter' });
  assert.equal(g.getAttribute('aria-pressed'), 'true');
  g.dispatch('keydown', { key: 'ArrowRight' });
  g.dispatch('keydown', { key: 'ArrowDown' });
  g.dispatch('keydown', { key: 'ArrowRight' });
  assert.deepEqual(order(page), [C0, 'agent-a2', 'agent-a3', 'agent-a4', 'agent-a1']);
  g.dispatch('keydown', { key: 'ArrowRight' });
  assert.deepEqual(order(page), [C0, 'agent-a2', 'agent-a3', 'agent-a4', 'agent-a1'], 'stops at the end');
  g.dispatch('keydown', { key: 'ArrowLeft' });
  assert.deepEqual(order(page), [C0, 'agent-a2', 'agent-a3', 'agent-a1', 'agent-a4']);
  assert.ok(g.focused >= 1, 'focus follows the moved window');
  assert.match(page.ids['grid-live'].textContent, /^Moved to place 4 of 5\.$/);
  g.dispatch('keydown', { key: 'Enter' });
  assert.equal(g.getAttribute('aria-pressed'), 'false');
  g.dispatch('keydown', { key: 'ArrowLeft' });
  assert.deepEqual(order(page), [C0, 'agent-a2', 'agent-a3', 'agent-a1', 'agent-a4'], 'dropped: arrows do nothing');
  // Escape drops it too.
  g.dispatch('keydown', { key: 'Enter' });
  assert.equal(g.getAttribute('aria-pressed'), 'true');
  g.dispatch('keydown', { key: 'Escape' });
  assert.equal(g.getAttribute('aria-pressed'), 'false');
  g.dispatch('keydown', { key: 'ArrowRight' });
  assert.deepEqual(order(page), [C0, 'agent-a2', 'agent-a3', 'agent-a1', 'agent-a4'], 'after Escape: arrows do nothing');
  assert.equal(saved(storage).tiles.a1.o, 3);

  const a2 = tile(page, 'agent-a2');
  const r = resizeOf(a2);
  r.dispatch('keydown', { key: 'ArrowRight' });
  assert.equal(a2.dataset.cols, '1', 'plain arrows on the corner do not resize');
  r.dispatch('keydown', { key: 'ArrowRight', shiftKey: true });
  r.dispatch('keydown', { key: 'ArrowDown', shiftKey: true });
  assert.deepEqual([a2.dataset.cols, a2.dataset.rows], ['2', '2']);
  for (let i = 0; i < 5; i += 1) r.dispatch('keydown', { key: 'ArrowRight', shiftKey: true });
  r.dispatch('keydown', { key: 'ArrowDown', shiftKey: true });
  assert.deepEqual([a2.dataset.cols, a2.dataset.rows], ['3', '2']);
  for (let i = 0; i < 5; i += 1) r.dispatch('keydown', { key: 'ArrowLeft', shiftKey: true });
  r.dispatch('keydown', { key: 'ArrowUp', shiftKey: true });
  assert.deepEqual([a2.dataset.cols, a2.dataset.rows], ['1', '1']);
  assert.deepEqual({ ...saved(storage).tiles.a2 }, { o: 1, c: 1, r: 1 });
  // Shift+arrows on the move handle resize too.
  gripOf(a2).dispatch('keydown', { key: 'ArrowRight', shiftKey: true });
  assert.equal(a2.dataset.cols, '2');
});

test('a corrupt, oversized or out-of-range saved layout is ignored or pruned', async () => {
  for (const bad of ['not json', '[]', '{"v":3,"tiles":{"a3":{"o":0},"a1":{"o":5,"c":3}}}', '{"tiles":{"a1":{"c":3}}}',
    '{"v":2,"tiles":[1]}', 'null',
    JSON.stringify({ v: 2, tiles: { a3: { o: 0 } }, pad: 'x'.repeat(70000) })]) {
    const page = await layoutPage({ storage: memStorage({ [LAYOUT]: bad }) });
    assert.deepEqual(order(page), DEFAULT, bad.slice(0, 30));
    assert.deepEqual([panels(page)[0].dataset.cols, panels(page)[0].dataset.rows], ['2', '2']);
    assert.equal(tile(page, 'agent-a1').dataset.cols, '1', bad.slice(0, 30));
  }
  // Out-of-range sizes are clamped; junk entries are dropped.
  const odd = { v: 2, tiles: { a1: { c: 9, r: -4, o: 1e9 }, a2: 'x', a3: { o: 0 }, x: { o: 'soon' } } };
  const page = await layoutPage({ storage: memStorage({ [LAYOUT]: JSON.stringify(odd) }) });
  assert.deepEqual([tile(page, 'agent-a1').dataset.cols, tile(page, 'agent-a1').dataset.rows], ['3', '1']);
  assert.deepEqual(order(page), DEFAULT);
  // 250 entries: at most 200 are read, and the next save keeps only ids
  // that still exist.
  const many = {};
  for (let i = 0; i < 250; i += 1) many[`gone-${i}`] = { o: i };
  many.a4 = { o: 0 };
  many.a1 = { o: 1 };
  const storage = memStorage({ [LAYOUT]: JSON.stringify({ v: 2, tiles: many }) });
  const big = await layoutPage({ storage });
  assert.deepEqual(order(big), DEFAULT, 'entries 251 and 252 (a4, a1) were not read');
  const r = resizeOf(tile(big, 'agent-a2'));
  r.dispatch('keydown', { key: 'ArrowRight', shiftKey: true });
  assert.deepEqual(Object.keys(saved(storage).tiles), ['a2']);
});

test('a v1 layout (per-board orders) keeps its sizes but not its orders, and is rewritten as v2', async () => {
  const v1 = { v: 1, tiles: { a4: { o: 0, c: 2 }, a3: { o: 1 }, a1: { c: 3, r: 2, o: 2 } } };
  const storage = memStorage({ [LAYOUT]: JSON.stringify(v1) });
  const page = await layoutPage({ storage });
  assert.deepEqual(order(page), DEFAULT, 'old board orders are dropped');
  assert.deepEqual([tile(page, 'agent-a1').dataset.cols, tile(page, 'agent-a1').dataset.rows], ['3', '2']);
  assert.equal(tile(page, 'agent-a4').dataset.cols, '2');
  assert.equal(storage.store[LAYOUT], JSON.stringify(v1), 'loading writes nothing');
  resizeOf(tile(page, 'agent-a2')).dispatch('keydown', { key: 'ArrowDown', shiftKey: true });
  const next = saved(storage);
  assert.equal(next.v, 2);
  assert.deepEqual({ ...next.tiles.a1 }, { c: 3, r: 2 });
  assert.deepEqual({ ...next.tiles.a4 }, { c: 2 });
  assert.equal(next.tiles.a3, undefined, 'an entry with only an old order is gone');
});

test('a throwing localStorage still renders, and moves still work in memory', async () => {
  const boom = () => { throw new Error('SecurityError'); };
  const page = await layoutPage({ storage: { getItem: boom, setItem: boom, removeItem: boom } });
  assert.deepEqual(order(page), DEFAULT);
  const g = gripOf(tile(page, 'agent-a2'));
  g.dispatch('keydown', { key: 'Enter' });
  g.dispatch('keydown', { key: 'ArrowLeft' });
  assert.deepEqual(order(page), [C0, 'agent-a2', 'agent-a1', 'agent-a3', 'agent-a4']);
  page.ids['grid-filter-all'].click();
  assert.equal(page.ids['grid-filter-all'].getAttribute('aria-pressed'), 'true', 'the filter works in memory');
  page.ids['grid-reset'].click();
  assert.deepEqual(order(page), DEFAULT);
});

test('Reset layout clears the saved layout and restores the default order and sizes', async () => {
  const storage = memStorage();
  const page = await layoutPage({ storage });
  const g = gripOf(tile(page, 'agent-a3'));
  g.dispatch('pointerdown', { button: 0, pointerId: 1, clientX: 345, clientY: 40 });
  page.win('pointerup', { pointerId: 1, clientX: 45, clientY: 40 });
  resizeOf(tile(page, 'agent-a1')).dispatch('keydown', { key: 'ArrowRight', shiftKey: true });
  assert.notDeepEqual(order(page), DEFAULT);
  assert.ok(storage.store[LAYOUT]);
  page.ids['grid-reset'].click();
  assert.equal(storage.store[LAYOUT], undefined);
  assert.deepEqual(order(page), DEFAULT);
  assert.equal(tile(page, 'agent-a1').dataset.cols, '1');
  assert.equal(page.ids['grid-live'].textContent, 'Layout reset.');
});

// ---- view memory ----

test('the chosen view persists and is restored; only the view, layout and filter keys are stored', async () => {
  const storage = memStorage();
  const page = await loadPage({ storage });
  assert.equal(Object.keys(storage.store).length, 0, 'loading writes nothing');
  page.ids['view-grid'].click();
  assert.equal(storage.store['agent-dashboard-view'], 'grid');
  const again = await loadPage({ storage });
  await settle();
  assert.equal(again.ids.main.dataset.view, 'grid');
  assert.equal(again.db.calls.subsBy.agents, 1, 'a page restored to Grid subscribes to agents once');
  assert.deepEqual(again.db.calls.queries.agents, ['agents', 'started_at', 'desc', 200]);
  assert.deepEqual(Object.keys(storage.store), ['agent-dashboard-view']);
  const hashed = await loadPage({ storage, hash: '#runs' });
  assert.equal(hashed.ids.main.dataset.view, 'runs');
  assert.equal(hashed.db.calls.subsBy.agents, undefined);
  const hashGrid = await loadPage({ hash: '#grid' });
  await settle();
  assert.equal(hashGrid.ids.main.dataset.view, 'grid');
  assert.equal(hashGrid.db.calls.subsBy.agents, 1);
});

test('a throwing localStorage is tolerated', async () => {
  const boom = () => { throw new Error('SecurityError'); };
  const page = await loadPage({ storage: { getItem: boom, setItem: boom, removeItem: boom } });
  assert.equal(page.ids.main.dataset.view, 'runs');
  page.ids['view-grid'].click();
  await settle();
  assert.equal(page.ids.main.dataset.view, 'grid');
  assert.equal(page.db.calls.subsBy.agents, 1);
});

test('without window.claude the grid says to open the page on claude.ai and starts nothing', async () => {
  const page = await gridPage({ withClaude: false });
  assert.equal(page.ids['grid-summary'].textContent, 'Open this page on claude.ai while signed in to see runs.');
  assert.equal(page.db.calls.subsBy.agents, undefined);
  assert.equal(page.intervals.length, 0);
  assert.equal(page.pending().length, 0);
  assert.equal(page.ids.coords.children.length, 0);
});

test('a saved layout never holds more than 200 windows, even with more on screen', async () => {
  const storage = memStorage();
  const page = await gridPage({ storage });
  runSnap(page, [runRow()]);
  const many = [];
  for (let i = 0; i < 210; i += 1) many.push(agent(`m${String(i).padStart(3, '0')}`, { started_at: iso(NOW - (i + 1) * 1000) }));
  agentSnap(page, many);
  assert.equal(windows(page).length, 211);
  assert.equal(page.ids.coords.dataset.cols, '6');
  const g = gripOf(tile(page, 'agent-m209'));
  g.dispatch('keydown', { key: 'Enter' });
  g.dispatch('keydown', { key: 'ArrowLeft' });
  const kept = Object.keys(saved(storage).tiles);
  assert.equal(kept.length, 200);
  assert.equal(kept[0], C0, 'windows on screen are kept first, in order');
});

// ---- a live snapshot during a drag or resize (judge round 1, finding 1) ----

test('an agent arriving mid-drag neither re-parents windows nor leaves the drag stuck', async () => {
  const storage = memStorage();
  const page = await layoutPage({ storage });
  const before = [...windows(page)];
  const g = gripOf(tile(page, 'agent-a3'));
  g.dispatch('pointerdown', { button: 0, pointerId: 7, clientX: 345, clientY: 40 });
  assert.equal(page.winListeners.pointerup.length, 1, 'the end of the drag is heard on the window');
  // The master writes a new running agent: it sorts first by default.
  const four = [1, 2, 3, 4].map((i) => agent(`a${i}`, { started_at: iso(NOW - i * MIN) }));
  agentSnap(page, [agent('a0', { started_at: iso(NOW - 10e3) }), ...four]);
  assert.deepEqual(windows(page), before, 'no window is detached while the pointer is captured');
  page.win('pointermove', { pointerId: 7, clientX: 145, clientY: 40 });
  page.win('pointerup', { pointerId: 7, clientX: 145, clientY: 40 });
  assert.equal(page.ids.coords.find((e) => 'dragging' in e.dataset).length, 0);
  assert.equal(page.winListeners.pointerup.length, 0, 'window listeners are removed');
  assert.deepEqual(order(page), [C0, 'agent-a0', 'agent-a3', 'agent-a1', 'agent-a2', 'agent-a4'], 'the drop lands and the new window shows');
  assert.equal(saved(storage).tiles.a3.o, 2);
  // The next drag and resize still work.
  placeTiles(page);
  const g2 = gripOf(tile(page, 'agent-a2'));
  g2.dispatch('pointerdown', { button: 0, pointerId: 8, clientX: 445, clientY: 40 });
  page.win('pointerup', { pointerId: 8, clientX: 145, clientY: 40 });
  assert.deepEqual(order(page), [C0, 'agent-a2', 'agent-a0', 'agent-a3', 'agent-a1', 'agent-a4']);
  const r = resizeOf(tile(page, 'agent-a1'));
  r.dispatch('pointerdown', { button: 0, pointerId: 9, clientX: 490, clientY: 90 });
  page.win('pointerup', { pointerId: 9, clientX: 690, clientY: 90 });
  assert.equal(tile(page, 'agent-a1').dataset.cols, '3');
  // A cancelled drag still lets a window that arrived meanwhile show.
  gripOf(tile(page, 'agent-a0')).dispatch('pointerdown', { button: 0, pointerId: 10, clientX: 245, clientY: 40 });
  agentSnap(page, [agent('a9', { started_at: iso(NOW - 5e3) }), agent('a0', { started_at: iso(NOW - 10e3) }), ...four]);
  assert.equal(order(page).length, 6, 'held back during the drag');
  page.win('pointercancel', { pointerId: 10 });
  assert.ok(order(page).includes('agent-a9'), 'shown once the drag ends');
});

test('a snapshot mid-resize keeps the preview; the release still saves', async () => {
  const storage = memStorage();
  const page = await layoutPage({ storage });
  const a1 = tile(page, 'agent-a1');
  resizeOf(a1).dispatch('pointerdown', { button: 0, pointerId: 3, clientX: 190, clientY: 90 });
  page.win('pointermove', { pointerId: 3, clientX: 390, clientY: 190 });
  assert.deepEqual([a1.dataset.cols, a1.dataset.rows], ['3', '2']);
  agentSnap(page, [agent('a0', { started_at: iso(NOW - 10e3) }),
    ...[1, 2, 3, 4].map((i) => agent(`a${i}`, { started_at: iso(NOW - i * MIN) }))]);
  assert.deepEqual([a1.dataset.cols, a1.dataset.rows], ['3', '2'], 'the re-render keeps the preview');
  page.win('pointerup', { pointerId: 3, clientX: 390, clientY: 190 });
  assert.deepEqual({ ...saved(storage).tiles.a1 }, { c: 3, r: 2 });
  assert.equal(order(page).length, 6, 'the new window shows once the resize ends');
  assert.equal(page.winListeners.pointerup.length, 0);
});

test('a window that disappears mid-drag or mid-resize cancels it; nothing is stuck', async () => {
  const storage = memStorage();
  const page = await layoutPage({ storage });
  gripOf(tile(page, 'agent-a3')).dispatch('pointerdown', { button: 0, pointerId: 1, clientX: 345, clientY: 40 });
  agentSnap(page, [agent('a1', { started_at: iso(NOW - MIN) }), agent('a2', { started_at: iso(NOW - 2 * MIN) })]);
  assert.deepEqual(order(page), [C0, 'agent-a1', 'agent-a2'], 'the cancelled drag lets the workspace update at once');
  assert.equal(page.winListeners.pointerup.length, 0);
  page.win('pointerup', { pointerId: 1, clientX: 145, clientY: 40 });
  resizeOf(tile(page, 'agent-a2')).dispatch('pointerdown', { button: 0, pointerId: 2, clientX: 290, clientY: 90 });
  agentSnap(page, [agent('a1', { started_at: iso(NOW - MIN) })]);
  assert.deepEqual(order(page), [C0, 'agent-a1']);
  assert.equal(storage.store[LAYOUT], undefined, 'nothing was saved');
  placeTiles(page);
  gripOf(tile(page, 'agent-a1')).dispatch('pointerdown', { button: 0, pointerId: 3, clientX: 145, clientY: 40 });
  page.win('pointerup', { pointerId: 3, clientX: 45, clientY: 40 });
  assert.deepEqual(order(page), ['agent-a1', C0], 'a new drag works');
});

// ---- several runs in one master session (finding 2) and the hidden count (finding 3) ----

test('a second run in the same master session shows only its own time, lines and rows', async () => {
  const page = await gridPage();
  const at = (m) => iso(NOW - m * MIN);
  runSnap(page, [
    runRow({ _docId: '20261019-110000-new', feature: 'New', phase: 'building', started_at: at(10), updated_at: at(1),
      status_lines: [{ at: at(10), text: 'Started' }, { at: at(5), text: 'Building round 1' }] }),
    runRow({ _docId: '20261018-090000-old', feature: 'Old', phase: 'done', started_at: at(27 * 60), updated_at: at(20 * 60),
      pr_url: `${PR}9`, status_lines: ['Started', 'Researching', 'Building round 1', 'Judging round 1', 'PR ready']
        .map((text, i) => ({ at: at((27 - i) * 60), text })) }),
  ]);
  agentSnap(page, [
    agent('20261019-110000-new.builder.r1', { run_id: '20261019-110000-new' }),
    agent('20261018-090000-old.judge.r1', { run_id: '20261018-090000-old', role: 'judge', state: 'done',
      started_at: at(21 * 60), finished_at: at(20 * 60), updated_at: at(20 * 60) }),
  ]);
  assert.equal(panels(page).length, 1);
  const [panel] = panels(page);
  assert.equal(h2Of(panel), 'New');
  assert.equal(panel.id, 'coord-20261019-110000-new');
  const whens = byClass(panel, 'when').map((e) => e.textContent);
  assert.ok(whens.includes('elapsed 10 min'), whens.join(' | '));
  assert.ok(!whens.some((w) => w.startsWith('Old')), 'the old run\'s row is left out');
  assert.deepEqual(byClass(panel, 'lines')[0].children.map((li) => li.children[1].textContent), ['Started', 'Building round 1']);
  assert.deepEqual(visibleLinks(panel).map((a) => a.textContent), ['Open session'], 'no PR link from the old run');
  assert.deepEqual(agentTiles(page.ids.coords).map((t) => t.id), ['agent-20261019-110000-new.builder.r1']);
  assert.equal(page.ids['grid-hidden'].hidden, true, 'the old run\'s agent is not counted as hidden');
  // Two live runs in one session: the newest leads, the other's rows still show.
  runSnap(page, [
    runRow({ _docId: '20261019-110000-new', feature: 'New', phase: 'building', started_at: at(10), updated_at: at(1) }),
    runRow({ _docId: '20261018-090000-old', feature: 'Old', phase: 'pr_ready', started_at: at(27 * 60), updated_at: at(60) }),
  ]);
  const [p2] = panels(page);
  assert.equal(h2Of(p2), 'New');
  assert.ok(byClass(p2, 'when').some((e) => e.textContent.startsWith('Old: PR ready')));
  assert.ok(byClass(p2, 'when').some((e) => e.textContent === 'elapsed 10 min'));
  assert.equal(agentTiles(page.ids.coords).length, 2, 'agents of both live runs show');
  // The old run marked done just now (its PR merged) does not take over.
  runSnap(page, [
    runRow({ _docId: '20261019-110000-new', feature: 'New', phase: 'building', started_at: at(10), updated_at: at(5) }),
    runRow({ _docId: '20261018-090000-old', feature: 'Old', phase: 'done', started_at: at(27 * 60), updated_at: at(1) }),
  ]);
  assert.equal(h2Of(panels(page)[0]), 'New');
  assert.ok(byClass(panels(page)[0], 'when').some((e) => e.textContent === 'elapsed 10 min'));
});

test('"N agents without a run" counts only live agents whose run has no row', async () => {
  const page = await gridPage();
  runSnap(page, [runRow(), runRow({ _docId: 'old-run', feature: 'Old', session_url: undefined, phase: 'done',
    started_at: iso(NOW - 30 * DAY), updated_at: iso(NOW - 29 * DAY) })]);
  const list = [];
  for (let i = 0; i < 40; i += 1) {
    list.push(agent(`old-run.builder.r${i}`, { run_id: 'old-run', state: 'done', started_at: iso(NOW - 30 * DAY),
      finished_at: iso(NOW - 29 * DAY), updated_at: iso(NOW - 29 * DAY) }));
  }
  list.push(agent('fresh-under-old.judge.r1', { run_id: 'old-run', role: 'judge', state: 'done', finished_at: iso(NOW - DAY) }));
  list.push(agent('ghost-old.builder.r1', { run_id: 'ghost', state: 'done', started_at: iso(NOW - 9 * DAY),
    finished_at: iso(NOW - 8 * DAY), updated_at: iso(NOW - 8 * DAY) }));
  agentSnap(page, list);
  assert.equal(page.ids['grid-hidden'].hidden, true, 'old runs and a retired orphan are not counted');
  agentSnap(page, [...list, agent('ghost.builder.r1', { run_id: 'ghost' }), agent('noid.builder.r1', { run_id: 7 })]);
  assert.equal(page.ids['grid-hidden'].textContent, '2 agents without a run');
});

// ==== the coordinator chat ====

async function chatPage(opts = {}) {
  const page = await gridPage(opts);
  runSnap(page, [runRow()]);
  await settle();
  return page;
}
const chatOf = (page, i = 0) => {
  const panel = panels(page)[i];
  return {
    panel,
    input: panel.find((e) => e.tagName === 'TEXTAREA')[0],
    send: panel.find((e) => e.tagName === 'BUTTON' && e.getAttribute('aria-label') === 'Send')[0],
    form: byClass(panel, 'composer')[0],
    hint: byClass(panel, 'chat')[0].children.at(-1),
    note: byClass(panel, 'chat')[0].children[0],
    log: byClass(panel, 'log')[0],
    pill: byClass(panel, 'newpill')[0],
    title: panel.find((e) => e.tagName === 'H2')[0],
  };
};
const bubbles = (page, i = 0) => byClass(chatOf(page, i).log, 'msg');
const bubbleText = (b) => b.children[1].textContent;
const chatSnap = (page, docs, path = CHAT) => page.db.handlers(path).next(snap(docs));
const mirror = (id, o = {}) => ({ _docId: id, from: 'coordinator', kind: 'update', text: `text ${id}`,
  at: iso(NOW - MIN), run_id: RUN, ...o });
const type = (c, value) => { c.input.value = value; };
const enter = (c, extra = {}) => c.input.dispatch('keydown', { key: 'Enter', ...extra });
const sentText = (page, i = -1) => page.comments.calls.send.at(i).text;

test('chat: the box shows only when canSendToClaude says available; other answers hide it', async () => {
  const cases = [
    ['available', false, ''],
    ['no_session', true, NO_LISTENER],
    ['writers_only', true, ''],
    ['off', true, ''],
    ['something_new', true, ''],
  ];
  for (const [can, hidden, hint] of cases) {
    const comments = fakeComments({ can });
    const page = await chatPage({ comments });
    const c = chatOf(page);
    assert.equal(c.form.hidden, hidden, can);
    assert.equal(c.hint.textContent, hint, can);
    assert.equal(c.hint.hidden, !hint, can);
    assert.equal(page.comments.calls.send.length, 0);
  }
  const rejecting = fakeComments();
  rejecting.canReplies.push(() => Promise.reject({ code: 'capability_removed', message: 'SECRET' }));
  const page = await chatPage({ comments: rejecting });
  assert.equal(chatOf(page).form.hidden, true, 'a rejected check hides it');
  assert.ok(!page.ids.coords.textContent.includes('SECRET'));
  // Without comments at all there is no box, and the transcript still loads.
  const none = await chatPage({ comments: null });
  assert.equal(chatOf(none).form.hidden, true);
  assert.deepEqual(none.db.calls.queries[CHAT], [CHAT, 'at', 'desc', 200]);
});

test('chat: canSendToClaude is re-checked on Grid show, maximize, visibility and at most once a minute', async () => {
  const comments = fakeComments({ can: 'no_session' });
  const page = await chatPage({ comments });
  const c = chatOf(page);
  assert.equal(comments.calls.can, 1);
  assert.equal(c.form.hidden, true);
  comments.state.can = 'available';
  page.intervals[0].fn();
  await settle();
  assert.equal(comments.calls.can, 1, 'the minute tick checks at most once a minute');
  page.setNow(NOW + 61e3);
  page.intervals[0].fn();
  await settle();
  assert.equal(comments.calls.can, 2);
  assert.equal(c.form.hidden, false, 'a session started watching');
  page.doc('visibilitychange');
  await settle();
  assert.equal(comments.calls.can, 3);
  byClass(c.panel, 'max')[0].click();
  await settle();
  assert.equal(comments.calls.can, 4);
  page.ids['view-runs'].click();
  page.doc('visibilitychange');
  page.setNow(NOW + 200e3);
  page.intervals[0].fn();
  await settle();
  assert.equal(comments.calls.can, 4, 'nothing is checked on Runs');
  page.ids['view-grid'].click();
  await settle();
  assert.equal(comments.calls.can, 5);
  assert.equal(comments.calls.send.length, 0, 'checks never send');
});

test('chat: Enter sends, Shift+Enter and IME composition do not; whitespace never sends', async () => {
  const page = await chatPage();
  const c = chatOf(page);
  type(c, 'hello');
  const shift = enter(c, { shiftKey: true });
  assert.equal(shift.defaultPrevented, undefined, 'Shift+Enter keeps its newline');
  enter(c, { isComposing: true });
  c.input.dispatch('keydown', { key: 'a' });
  await settle();
  assert.equal(page.comments.calls.send.length, 0);
  const ev = enter(c);
  assert.equal(ev.defaultPrevented, true);
  await settle();
  assert.equal(page.comments.calls.send.length, 1);
  assert.equal(c.input.value, '', 'the draft clears once sent');
  for (const blank of ['', '   ', '\n\t\n', '\u0007\u0001']) {
    type(c, blank);
    c.send.click();
    enter(c);
  }
  await settle();
  assert.equal(page.comments.calls.send.length, 1, 'whitespace-only text never sends');
});

test('chat: the first send anchors on the window title; later sends reply in that thread, per session', async () => {
  const page = await gridPage();
  const B = 'https://claude.ai/code/session_second';
  runSnap(page, [runRow(), runRow({ _docId: 'second-run', feature: 'Second', session_url: B, started_at: iso(NOW - 200 * MIN) })]);
  await settle();
  const c = chatOf(page, 0);
  type(c, 'first');
  c.send.click();
  await settle();
  const [t1] = page.comments.calls.send;
  assert.deepEqual(Object.keys(t1), ['anchor', 'text']);
  assert.deepEqual({ ...t1.anchor }, { path: 'article > h2', x: 3, y: 4 });
  assert.equal(page.comments.calls.anchor[0], c.title, 'anchored on the title, read at the gesture');
  type(c, 'second');
  c.send.click();
  await settle();
  assert.deepEqual(Object.keys(page.comments.calls.send[1]), ['threadId', 'text']);
  assert.equal(page.comments.calls.send[1].threadId, 't1');
  assert.equal(page.comments.calls.anchor.length, 1);
  const d = chatOf(page, 1);
  type(d, 'other run');
  d.send.click();
  await settle();
  assert.deepEqual(Object.keys(page.comments.calls.send[2]), ['anchor', 'text'], 'another session starts its own thread');
  assert.equal(page.comments.calls.anchor[1], d.title);
  assert.match(sentText(page), /^\[run second-run #[a-z0-9]{6}\] other run$/);
});

test('chat: the text gets a run and nonce prefix, loses control characters and fits 4000 UTF-8 bytes', async () => {
  const page = await chatPage();
  const c = chatOf(page);
  type(c, 'line one\r\nline\ttwo\u0007\u001b[31m\u0085 end');
  c.send.click();
  await settle();
  assert.match(sentText(page), new RegExp(`^\\[run ${RUN} #[a-z0-9]{6}\\] line one\\nline\\ttwo\\[31m end$`));
  // 3000 two-byte characters (6000 bytes) are cut to fit, never mid-character.
  type(c, 'é'.repeat(3000));
  c.send.click();
  await settle();
  const long = sentText(page);
  assert.ok(page.api.utf8Len(long) <= 4000 && page.api.utf8Len(long) >= 3998, String(page.api.utf8Len(long)));
  assert.ok(long.endsWith('é'));
  assert.equal(chatOf(page).hint.textContent, 'Sent, cut to fit 4 KB.');
  // Emoji (4 bytes) are never split into a lone surrogate.
  type(c, '😀'.repeat(1200));
  c.send.click();
  await settle();
  const emoji = sentText(page);
  assert.ok(page.api.utf8Len(emoji) <= 4000);
  assert.ok(!/[\ud800-\udbff]$/.test(emoji));
  // A lone surrogate becomes U+FFFD.
  type(c, 'a\ud800b');
  c.send.click();
  await settle();
  assert.ok(sentText(page).endsWith('a�b'));
});

test('utf8Len counts bytes by hand', async () => {
  const { api } = await loadPage();
  assert.equal(api.utf8Len(''), 0);
  assert.equal(api.utf8Len('abc'), 3);
  assert.equal(api.utf8Len('é'), 2);
  assert.equal(api.utf8Len('€'), 3);
  assert.equal(api.utf8Len('😀'), 4);
  assert.equal(api.utf8Len('\ud800'), 3, 'a lone surrogate is sent as U+FFFD');
  assert.equal(api.utf8Len('a😀é€'), 10);
});

test('chat: one send in flight at a time; nothing sends at load, on a timer or by itself', async () => {
  const page = await gridPage();
  const B = 'https://claude.ai/code/session_second';
  runSnap(page, [runRow(), runRow({ _docId: 'second-run', feature: 'Second', session_url: B })]);
  await settle();
  assert.equal(page.comments.calls.send.length, 0, 'nothing at load');
  page.setNow(NOW + 120e3);
  page.intervals[0].fn();
  for (const t of page.pending()) t.fn();
  await settle();
  assert.equal(page.comments.calls.send.length, 0, 'nothing on the timer');
  let finish;
  page.comments.replies.push(() => new Promise((resolve) => { finish = resolve; }));
  const [a, b] = [chatOf(page, 0), chatOf(page, 1)];
  type(a, 'one');
  a.send.click();
  await settle();
  assert.equal(a.send.disabled, true);
  type(b, 'two');
  b.send.click();
  enter(b);
  a.send.click();
  await settle();
  assert.equal(page.comments.calls.send.length, 1);
  finish({ threadId: 'tx', commentId: 'cx' });
  await settle();
  assert.equal(a.send.disabled, false);
  b.send.click();
  await settle();
  assert.equal(page.comments.calls.send.length, 2, 'a new press after it settles works');
  assert.equal(page.pending().length, 0, 'no retry timer');
});

test('chat: Enter clears the box at once, before claude.ai confirms; a failed send puts the draft back', async () => {
  const page = await chatPage();
  const c = chatOf(page);
  let finish;
  page.comments.replies.push(() => new Promise((resolve) => { finish = resolve; }));
  type(c, 'merge it');
  enter(c);
  await settle();
  assert.equal(page.comments.calls.send.length, 1);
  assert.equal(c.input.value, '', 'the box is empty while the send is still in flight');
  assert.equal(bubbles(page).at(-1).find((e) => e.className === 'state')[0].textContent, 'sending');
  finish({ threadId: 'tx', commentId: 'cx' });
  await settle();
  assert.equal(c.input.value, '');
  let fail;
  page.comments.replies.push(() => new Promise((_, reject) => { fail = reject; }));
  type(c, 'second');
  enter(c);
  await settle();
  assert.equal(c.input.value, '');
  fail({ code: 'rate_limited' });
  await settle();
  assert.equal(c.input.value, 'second', 'the draft comes back on failure');
});

const ERRORS = [
  // code, hint, draft kept, box hidden after
  ['consent_required', 'Not sent. Allow commenting when claude.ai asks, then press Send.', true, false],
  ['forbidden', SEND_OFF, true, true],
  ['invalid', 'Not sent: claude.ai rejected the message.', true, false],
  ['transform_error', 'Not sent: claude.ai rejected the message.', true, false],
  ['rate_limited', 'Not sent: too many messages. Wait a minute, then press Send.', true, false],
  ['upstream_error', 'May have been sent; check before resending.', true, false],
  ['brand_new_code', 'May have been sent; check before resending.', true, false],
  ['not_granted', SEND_OFF, true, true],
  ['capability_disabled', SEND_OFF, true, true],
  ['capability_removed', SEND_OFF, true, true],
];
for (const [code, hint, kept, hidden] of ERRORS) {
  test(`chat error ${code}: "${hint.slice(0, 30)}…", never retried, never shows the error message`, async () => {
    const page = await chatPage();
    const c = chatOf(page);
    page.comments.replies.push(() => Promise.reject({ code, message: 'SECRET detail' }));
    type(c, 'are you there?');
    c.send.click();
    await settle();
    assert.equal(c.hint.textContent, hint);
    assert.equal(c.hint.hidden, false);
    assert.equal(c.input.value === 'are you there?', kept);
    assert.equal(c.form.hidden, hidden);
    assert.ok(!page.ids.coords.textContent.includes('SECRET'));
    assert.equal(page.comments.calls.send.length, 1);
    assert.equal(page.pending().length, 0);
    const state = bubbles(page).at(-1).find((e) => e.className === 'state')[0].textContent;
    assert.equal(state, code === 'upstream_error' || code === 'brand_new_code' ? 'may have been sent' : 'not sent');
    c.send.click();
    await settle();
    assert.equal(page.comments.calls.send.length, hidden ? 1 : 2, hidden ? 'a hidden box never sends' : 'a new press sends again');
    runSnap(page, [runRow({ phase: 'judging' })]);
    assert.equal(chatOf(page).form.hidden, hidden, 'the same after a re-render');
  });
}

test('chat error unavailable: a hint the first time, the box hidden the second time', async () => {
  const page = await chatPage();
  const c = chatOf(page);
  page.comments.replies.push(() => Promise.reject({ code: 'unavailable' }), () => Promise.reject({ code: 'unavailable' }));
  type(c, 'hi');
  c.send.click();
  await settle();
  assert.equal(c.hint.textContent, 'Not sent: comments are unavailable right now. Try again in a moment.');
  assert.equal(c.form.hidden, false);
  c.send.click();
  await settle();
  assert.equal(c.form.hidden, true);
  assert.equal(c.hint.textContent, SEND_OFF);
  assert.equal(page.comments.calls.send.length, 2);
  page.setNow(NOW + 120e3);
  page.intervals[0].fn();
  await settle();
  assert.equal(page.comments.calls.can, 1, 'no more checks once it is off for the visit');
});

test('chat error not_found drops the thread: the next send anchors again', async () => {
  const page = await chatPage();
  const c = chatOf(page);
  type(c, 'one');
  c.send.click();
  await settle();
  page.comments.replies.push(() => Promise.reject({ code: 'not_found' }));
  type(c, 'two');
  c.send.click();
  await settle();
  assert.equal(page.comments.calls.send[1].threadId, 't1');
  assert.equal(c.hint.textContent, 'Not sent: that thread is gone. Press Send to start a new one.');
  assert.equal(c.input.value, 'two');
  c.send.click();
  await settle();
  assert.deepEqual(Object.keys(page.comments.calls.send[2]), ['anchor', 'text']);
});

test('chat error claude_unavailable: nothing was posted, and the box is re-checked', async () => {
  const page = await chatPage();
  const c = chatOf(page);
  page.comments.replies.push(() => Promise.reject({ code: 'claude_unavailable' }));
  page.comments.state.can = 'no_session';
  type(c, 'hi');
  c.send.click();
  await settle();
  assert.equal(page.comments.calls.can, 2, 're-checked right away');
  assert.equal(c.form.hidden, true);
  assert.equal(c.hint.textContent, NO_LISTENER);
  assert.equal(c.input.value, 'hi', 'the draft is kept');
});

test('chat: the mirror is read from Kyle\'s private path, newest 200 by time, and shown oldest first', async () => {
  const page = await chatPage();
  assert.deepEqual(page.db.calls.queries[CHAT], [CHAT, 'at', 'desc', 200]);
  assert.equal(page.api.CHAT_LIMIT, 200);
  assert.equal(page.api.CHAT_MAX, 12000);
  chatSnap(page, [
    mirror('m3', { text: 'third', at: iso(NOW - MIN), kind: 'report' }),
    mirror('m2', { from: 'kyle', text: 'second', at: iso(NOW - 2 * MIN) }),
    mirror('m1', { text: 'first', at: iso(NOW - 3 * MIN), kind: 'nonsense', from: 'someone' }),
    mirror('m0', { text: 'x'.repeat(20000), at: 'not a time' }),
  ]);
  const list = bubbles(page);
  assert.deepEqual(list.map((b) => b.dataset.from), ['coordinator', 'coordinator', 'kyle', 'coordinator']);
  assert.equal(bubbleText(list[0]).length, 12000, 'clamped to 12000 characters');
  assert.deepEqual(list.slice(1).map(bubbleText), ['first', 'second', 'third']);
  assert.deepEqual(list.map((b) => b.dataset.kind), ['update', 'update', 'update', 'report'], 'unknown kinds fall back');
  assert.equal(byClass(list[0], 'at')[0].textContent, '?');
  assert.match(byClass(list[3], 'at')[0].textContent, /\d/, 'each bubble shows its time');
  assert.equal(byClass(list[3], 'who')[0].textContent, 'Coordinator · report');
  // One bubble per doc id, reused while it doesn't change.
  const first = list[1];
  chatSnap(page, [mirror('m4', { text: 'fourth', at: iso(NOW) }), mirror('m3', { text: 'third', at: iso(NOW - MIN), kind: 'report' }),
    mirror('m2', { from: 'kyle', text: 'second', at: iso(NOW - 2 * MIN) }), mirror('m1', { text: 'first', at: iso(NOW - 3 * MIN) })]);
  assert.equal(bubbles(page)[0], first);
  assert.deepEqual(bubbles(page).map(bubbleText), ['first', 'second', 'third', 'fourth']);
});

test('chat: no user id means no transcript, with copy saying so; the box still works', async () => {
  for (const opts of [{ user: null }, { userNs: false }, { user: '../evil' }, { user: 'a/b' }, { user: '.' }, { user: 42 }]) {
    const page = await chatPage(opts);
    const user = JSON.stringify(opts);
    const c = chatOf(page);
    assert.deepEqual(Object.keys(page.db.calls.queries).filter((q) => q.startsWith('data/')), [], String(user));
    assert.equal(c.note.textContent, 'No transcript here: claude.ai gave this page no user id. Sending still works.');
    assert.equal(c.form.hidden, false);
    type(c, 'hi');
    c.send.click();
    await settle();
    assert.equal(page.comments.calls.send.length, 1);
  }
});

test('chat: Kyle\'s message shows as sending, then sent, and is replaced by its mirrored copy', async () => {
  const page = await chatPage();
  chatSnap(page, [mirror('m1', { text: 'hello Kyle' })]);
  let finish;
  page.comments.replies.push(() => new Promise((resolve) => { finish = resolve; }));
  const c = chatOf(page);
  type(c, 'please **stop**');
  c.send.click();
  await settle();
  let mine = bubbles(page).at(-1);
  assert.equal(mine.dataset.from, 'kyle');
  assert.equal(bubbleText(mine), 'please **stop**', 'Kyle\'s words stay plain text');
  assert.equal(byClass(mine, 'state')[0].textContent, 'sending');
  finish({ threadId: 't9', commentId: 'c9' });
  await settle();
  mine = bubbles(page).at(-1);
  assert.equal(byClass(mine, 'state')[0].textContent, 'sent');
  const nonce = /#([a-z0-9]{6})\]/.exec(sentText(page))[1];
  chatSnap(page, [mirror('k1', { from: 'kyle', kind: 'reply', text: 'please **stop**', ref: nonce, at: iso(NOW) }),
    mirror('m1', { text: 'hello Kyle' })]);
  const list = bubbles(page);
  assert.equal(list.length, 2, 'deduped by the nonce');
  assert.equal(byClass(list[1], 'state').length, 0, 'the mirrored copy has no local state');
  // A mirrored Kyle message with another ref leaves the local one alone.
  type(c, 'again');
  c.send.click();
  await settle();
  chatSnap(page, [mirror('k2', { from: 'kyle', text: 'again', ref: 'zzzzzz', at: iso(NOW) }),
    mirror('k1', { from: 'kyle', kind: 'reply', text: 'please **stop**', ref: nonce, at: iso(NOW) }), mirror('m1', { text: 'hello Kyle' })]);
  assert.equal(bubbles(page).length, 4);
});

test('chat: the newest open question is highlighted until Kyle replies', async () => {
  const page = await chatPage();
  const c = chatOf(page);
  chatSnap(page, [
    mirror('q2', { kind: 'question', text: 'Merge now?', at: iso(NOW - MIN) }),
    mirror('u1', { text: 'Tests green', at: iso(NOW - 2 * MIN) }),
    mirror('q1', { kind: 'question', text: 'Old question', at: iso(NOW - 5 * MIN) }),
  ]);
  const list = bubbles(page);
  assert.deepEqual(list.map((b) => b.dataset.kind), ['asked', 'update', 'question']);
  assert.ok('question' in c.panel.dataset);
  const chip = byClass(c.panel, 'qchip')[0];
  assert.equal(chip.hidden, false);
  assert.equal(chip.textContent, 'question');
  // Kyle answers from the page: cleared at once.
  type(c, 'yes');
  c.send.click();
  await settle();
  assert.equal('question' in c.panel.dataset, false);
  assert.equal(chip.hidden, true);
  assert.equal(bubbles(page).find((b) => b.dataset.kind === 'question'), undefined);
  // A new question after that is open again; a mirrored Kyle reply clears it.
  chatSnap(page, [mirror('q3', { kind: 'question', text: 'And release?', at: iso(NOW + MIN) })]);
  assert.ok('question' in c.panel.dataset);
  chatSnap(page, [mirror('k3', { from: 'kyle', text: 'no', at: iso(NOW + 2 * MIN) }),
    mirror('q3', { kind: 'question', text: 'And release?', at: iso(NOW + MIN) })]);
  assert.equal('question' in c.panel.dataset, false);
});

test('chat: the transcript follows new messages near the bottom, else shows a New messages pill', async () => {
  const page = await chatPage();
  const c = chatOf(page);
  chatSnap(page, [mirror('m1')]);
  c.log.scrollHeight = 1000;
  c.log.clientHeight = 300;
  c.log.scrollTop = 680;  // 20 px from the bottom
  chatSnap(page, [mirror('m2', { at: iso(NOW) }), mirror('m1')]);
  assert.equal(c.log.scrollTop, 1000, 'pinned to the bottom');
  assert.equal(c.pill.hidden, true);
  c.log.scrollHeight = 1200;
  c.log.scrollTop = 100;  // Kyle scrolled up to read
  chatSnap(page, [mirror('m3', { at: iso(NOW + 1) }), mirror('m2', { at: iso(NOW) }), mirror('m1')]);
  assert.equal(c.log.scrollTop, 100, 'left where Kyle is reading');
  assert.equal(c.pill.hidden, false);
  assert.equal(c.pill.textContent, 'New messages ↓');
  c.pill.click();
  assert.equal(c.log.scrollTop, 1200);
  assert.equal(c.pill.hidden, true);
  // Scrolling back down by hand hides it too.
  c.log.scrollTop = 0;
  chatSnap(page, [mirror('m4', { at: iso(NOW + 2) }), mirror('m3', { at: iso(NOW + 1) }), mirror('m2', { at: iso(NOW) }), mirror('m1')]);
  assert.equal(c.pill.hidden, false);
  c.log.scrollTop = 880;
  c.log.dispatch('scroll');
  assert.equal(c.pill.hidden, true);
  // Kyle's own send always scrolls down.
  c.log.scrollTop = 0;
  type(c, 'hi');
  c.send.click();
  await settle();
  assert.equal(c.log.scrollTop, 1200);
});

test('chat: subscriptions only for coordinator windows on Grid, newest 8 (a maximized one first), paused on Runs', async () => {
  const page = await gridPage();
  const runs = [];
  for (let i = 0; i < 10; i += 1) {
    runs.push(runRow({ _docId: `run-${i}`, feature: `Run ${i}`, session_url: `https://claude.ai/code/session_s${i}`,
      started_at: iso(NOW - (i + 1) * MIN), updated_at: iso(NOW - (i + 1) * MIN) }));
  }
  runs.push(runRow({ _docId: 'no-session', session_url: undefined }));
  runSnap(page, runs);
  await settle();
  const chatQueries = () => Object.keys(page.db.calls.queries).filter((q) => q.startsWith('data/users/') && q.endsWith('/msgs'));
  const live = () => chatQueries().filter((q) => (page.db.calls.subsBy[q] ?? 0) > (page.db.calls.unsubsBy[q] ?? 0));
  assert.deepEqual(live().sort(), [0, 1, 2, 3, 4, 5, 6, 7].map((i) => `data/users/u_test/chat-s${i}/msgs`).sort());
  const ninth = panels(page).find((p) => p.id === 'coord-run-9');
  assert.match(byClass(ninth, 'chat')[0].children[0].textContent, /^Conversation not loaded: only the newest 8 runs/);
  byClass(ninth, 'max')[0].click();
  assert.equal(live().length, 8);
  assert.ok(live().includes('data/users/u_test/chat-s9/msgs'), 'the maximized window loads its conversation');
  assert.ok(!live().includes('data/users/u_test/chat-s7/msgs'));
  page.ids['view-runs'].click();
  assert.deepEqual(live(), [], 'all paused on Runs');
  const late = page.db.handlers('data/users/u_test/chat-s0/msgs');
  late.next(snap([mirror('late')]));
  page.ids['view-grid'].click();
  await settle();
  assert.equal(live().length, 8);
  assert.equal(page.db.calls.subs, 1, 'runs untouched');
});

test('chat: a mirror query error retries like the others and shows in the window', async () => {
  const page = await chatPage();
  page.db.handlers(CHAT).error({ code: 'unavailable', message: 'SECRET' });
  const c = chatOf(page);
  assert.match(c.note.textContent, /^Connection lost · retrying in \d+ s$/);
  assert.equal(c.note.dataset.level, 'bad');
  page.pending()[0].fn();
  assert.equal(page.db.calls.subsBy[CHAT], 2);
  page.db.handlers(CHAT).error({ code: 'revoked' });
  assert.match(chatOf(page).note.textContent, /^No access/);
  assert.ok(!page.ids.coords.textContent.includes('SECRET'));
});

// ---- markdown ----

const md = (page, src) => page.api.renderMarkdown(src);
const tags = (root) => root.find(() => true).map((e) => e.tagName);
const count = (root, tag) => root.find((e) => e.tagName === tag).length;

test('markdown: headings, fences, quotes, lists, tables, inline code, bold, em and links', async () => {
  const { api } = await loadPage();
  const root = api.renderMarkdown([
    '# Title', '## Sub', '### Third', '#### Fourth ##', '',
    'Plain **bold** and *em* and _em2_ and `code` and [PR](https://github.com/x/y/pull/1).', 'next line',
    '', '```js', 'const a = "<b>";', '**not bold**', '```',
    '> quoted **text**', '> more',
    '- one', '- two', '  - nested', '    1. deep', '- three',
    '', '3. three', '4. four',
    '', '| a | b |', '|---|:-:|', '| 1 | **2** |', '| 3 |',
    '', '---', 'after',
  ].join('\n'));
  assert.equal(root.className, 'md');
  const top = root.children.map((e) => e.tagName);
  assert.deepEqual(top, ['H3', 'H4', 'H5', 'H6', 'P', 'PRE', 'BLOCKQUOTE', 'UL', 'OL', 'DIV', 'HR', 'P']);
  assert.equal(root.children[3].textContent, 'Fourth');
  const p = root.children[4];
  assert.equal(p.find((e) => e.tagName === 'STRONG')[0].textContent, 'bold');
  assert.deepEqual(p.find((e) => e.tagName === 'EM').map((e) => e.textContent), ['em', 'em2']);
  assert.equal(p.find((e) => e.tagName === 'CODE')[0].textContent, 'code');
  const [a] = p.find((e) => e.tagName === 'A');
  assert.deepEqual([a.textContent, a.href, a.target, a.rel], ['PR', 'https://github.com/x/y/pull/1', '_blank', 'noopener noreferrer']);
  assert.equal(count(p, 'BR'), 1, 'a line break inside a paragraph');
  const pre = root.children[5];
  assert.equal(pre.textContent, 'const a = "<b>";\n**not bold**');
  assert.equal(count(pre, 'STRONG'), 0);
  assert.equal(root.children[6].find((e) => e.tagName === 'STRONG')[0].textContent, 'text');
  const ul = root.children[7];
  assert.deepEqual(ul.children.map((li) => li.children[0].textContent), ['one', 'two', 'three']);
  const nested = ul.children[1].find((e) => e.tagName === 'UL')[0];
  assert.equal(nested.children[0].children[0].textContent, 'nested');
  assert.equal(nested.find((e) => e.tagName === 'OL')[0].textContent, 'deep');
  assert.equal(root.children[8].getAttribute('start'), '3');
  const table = root.children[9].children[0];
  assert.equal(table.tagName, 'TABLE');
  assert.deepEqual(table.find((e) => e.tagName === 'TH').map((e) => e.textContent), ['a', 'b']);
  assert.deepEqual(table.find((e) => e.tagName === 'TR').slice(1).map((tr) => tr.children.map((td) => td.textContent)),
    [['1', '2'], ['3', '']]);
  assert.equal(root.children[11].textContent, 'after');
});

test('markdown: hostile input stays text and stays fast', async () => {
  const { api } = await loadPage();
  const evil = api.renderMarkdown('<script>alert(1)</script> <img src=x onerror=alert(1)>');
  assert.deepEqual([...new Set(tags(evil))], ['P', 'SPAN']);
  assert.equal(evil.textContent, '<script>alert(1)</script> <img src=x onerror=alert(1)>');
  for (const url of ['javascript:alert(1)', 'data:text/html,<b>x</b>', '//evil.example/x', 'JaVaScRiPt:alert(1)',
    'https://ok.example/a b', ' javascript:alert(1)', 'vbscript:x', 'ftp://x']) {
    const root = api.renderMarkdown(`[click](${url})`);
    assert.equal(count(root, 'A'), 0, url);
    assert.ok(root.textContent.includes('click'), url);
  }
  assert.equal(count(api.renderMarkdown('[ok](HTTPS://example.com/x)'), 'A'), 1, 'the scheme check ignores case');
  // 100k characters are clamped to 12000.
  assert.ok(api.renderMarkdown('x'.repeat(100000)).textContent.length <= 12000);
  // A 500-deep list (as much of it as fits 12000 characters) nests at most 6 lists.
  const deep = Array.from({ length: 500 }, (_, i) => `${' '.repeat(i)}- item ${i}`).join('\n');
  assert.equal(count(api.renderMarkdown(deep), 'UL') <= 6, true);
  const fits = Array.from({ length: 140 }, (_, i) => `${' '.repeat(i)}- item ${i}`).join('\n');
  const tree = api.renderMarkdown(fits);
  let depth = 0;
  let node = tree.children[0];
  while (node) { depth += 1; node = node.children.at(-1)?.children.find((e) => e.tagName === 'UL'); }
  assert.equal(depth, 6);
  assert.ok(tree.textContent.includes('item 139'), 'deeper items are flattened, not lost');
  // Quotes nest at most 4 deep.
  const quotes = api.renderMarkdown('>'.repeat(5000) + ' x');
  assert.ok(count(quotes, 'BLOCKQUOTE') <= 4);
  // Pathological inline input stays linear.
  for (const s of ['['.repeat(12000), '[a]('.repeat(3000), '*'.repeat(12000), '_'.repeat(12000), '`'.repeat(12000),
    '**a'.repeat(4000), '``a'.repeat(4000), '|'.repeat(12000), '# '.repeat(6000), ' '.repeat(11000) + 'x',
    '- '.repeat(6000), '1. '.repeat(4000), ('| a '.repeat(30) + '|\n|' + '---|'.repeat(30) + '\n').repeat(100)]) {
    const t0 = performance.now();
    api.renderMarkdown(s);
    const ms = performance.now() - t0;
    assert.ok(ms < 200, `${JSON.stringify(s.slice(0, 12))}: ${ms.toFixed(1)} ms`);
  }
  const wide = api.renderMarkdown(`|${'c|'.repeat(40)}\n|${'-|'.repeat(40)}\n|${'v|'.repeat(40)}`);
  assert.equal(wide.find((e) => e.tagName === 'TH').length, 20, 'at most 20 columns');
});

test('markdown: about 2000 elements per message; the rest is plain text', async () => {
  const { api } = await loadPage();
  const src = Array.from({ length: 3000 }, (_, i) => `- **b${i}** *e*`).join('\n');
  const root = api.renderMarkdown(src);
  const n = root.find(() => true).length;
  assert.ok(n <= 2010, String(n));
  const rest = root.find((e) => e.className === 'md-rest');
  assert.equal(rest.length, 1);
  assert.match(rest[0].textContent, /^- \*\*b\d+\*\* \*e\*\n/, 'the rest is kept as plain text, markup and all');
  assert.ok(root.textContent.endsWith('…'), 'up to the 12000-character clamp');
  // Paragraph lines and list continuation lines stay inside the budget too.
  for (const s of ['a\n'.repeat(6000), '*a*\n'.repeat(3000), '- x\n' + '  a\n'.repeat(3000)]) {
    const r = api.renderMarkdown(s);
    const count = r.find(() => true).length;
    assert.ok(count <= 2010, `${JSON.stringify(s.slice(0, 8))}: ${count} elements`);
  }
});

// ---- the working indicator (the master's status on the chat index doc) ----

const STATUS = `data/users/u_test/chat-${TAIL}`;
const statusSnap = (page, body) => page.db.handlers(STATUS).next({ id: `chat-${TAIL}`, exists: body !== null,
  data: () => (body === null ? undefined : body), metadata: { fromCache: false } });
const work = (page) => byClass(chatOf(page).panel, 'work')[0];
const pill = (page) => {
  const pr = byClass(chatOf(page).panel, 'presence')[0];
  return [pr.dataset.state, byClass(pr, 'ptext')[0].textContent, pr.hidden];
};

test('working: the status row shows the step text and elapsed time, refreshed on the interval', async () => {
  const page = await chatPage();
  assert.deepEqual(page.db.calls.queries[STATUS], [STATUS, 'doc']);
  assert.equal(work(page).hidden, true, 'no status yet');
  statusSnap(page, { last_at: iso(NOW), status: { state: 'working', text: 'Checking CI on PR #4', at: iso(NOW - 3 * MIN) } });
  const w = work(page);
  assert.equal(w.hidden, false);
  assert.equal(byClass(w, 'wlabel')[0].textContent, 'Working…');
  assert.equal(byClass(w, 'wtext')[0].hidden, true, 'the step shows once, in the presence pill');
  assert.equal(pill(page)[1], 'In task: Checking CI on PR #4 · 3m');
  assert.equal(byClass(w, 'wtime')[0].textContent, '3m');
  assert.equal(byClass(w, 'spin')[0].textContent, '✻');
  assert.equal(byClass(w, 'spin')[0].getAttribute('aria-hidden'), 'true');
  page.setNow(NOW + 2 * MIN);
  page.intervals[0].fn();
  assert.equal(byClass(w, 'wtime')[0].textContent, '5m');
  // Stale (over 15 min), idle, missing or junk: nothing.
  page.setNow(NOW + 13 * MIN);
  page.intervals[0].fn();
  assert.equal(w.hidden, true, 'stale');
  page.setNow(NOW);
  for (const body of [{ status: { state: 'idle', at: iso(NOW) } }, {}, null, { status: 'working' },
    { status: { state: 'working', text: 'x' } }, { status: { state: 'working', at: iso(NOW + 60 * MIN) } }]) {
    statusSnap(page, body);
    assert.equal(w.hidden, true, JSON.stringify(body));
  }
});

test('working: hostile status text stays text, clamped to 80', async () => {
  const page = await chatPage();
  const evil = '<img src=x onerror=alert(1)>' + 'y'.repeat(200) + '\n**bold**';
  statusSnap(page, { status: { state: 'working', text: evil, at: iso(NOW) } });
  const t = byClass(byClass(chatOf(page).panel, 'presence')[0], 'ptext')[0];
  assert.equal(t.children.length, 0);
  assert.equal(t.textContent, `In task: ${('<img src=x onerror=alert(1)>' + 'y'.repeat(200)).slice(0, 79)}… · just now`);
  assert.equal(chatOf(page).panel.find((e) => ['IMG', 'STRONG'].includes(e.tagName)).length, 0);
});

const dline = (page) => byClass(chatOf(page).log, 'dline')[0];
const stage = (page) => {
  const d = dline(page);
  if (!d) return null;
  return [d.dataset.stage, byClass(d, 'dtext')[0].textContent, byClass(d, 'spin')[0].hidden ? '' : 'spin'];
};

test('delivery: Sending…, Delivered, Seen, working, then cleared by the reply; the line sits under Kyle\'s bubble', async () => {
  const page = await chatPage();
  let finish;
  page.comments.replies.push(() => new Promise((resolve) => { finish = resolve; }));
  const c = chatOf(page);
  type(c, 'status?');
  c.send.click();
  await settle();
  assert.deepEqual(stage(page), ['sending', 'Sending…', 'spin']);
  const kids = c.log.children;
  assert.equal(kids[kids.indexOf(dline(page)) - 1].dataset.from, 'kyle', 'right under Kyle\'s bubble');
  finish({ threadId: 't1', commentId: 'c1' });
  await settle();
  assert.deepEqual(stage(page), ['delivered', 'Delivered', '']);
  assert.equal(work(page).hidden, true, 'no separate waiting row');
  const nonce = /#([a-z0-9]{6})\]/.exec(sentText(page))[1];
  // Seen: the master's pickup write names this message.
  statusSnap(page, { status: { state: 'idle', seen_ref: nonce, at: iso(NOW + 1000) } });
  assert.deepEqual(stage(page), ['seen', 'Seen by coordinator', '']);
  assert.match(byClass(dline(page), 'dtime')[0].textContent, /\d/);
  // Working, with the step and the elapsed time.
  statusSnap(page, { status: { state: 'working', text: 'Reading your message', seen_ref: nonce, at: iso(NOW + 1000) } });
  assert.deepEqual(stage(page), ['working', 'Coordinator · working: Reading your message', 'spin']);
  page.setNow(NOW + 4 * MIN);
  page.intervals[0].fn();
  assert.equal(byClass(dline(page), 'dtime')[0].textContent, '3m');
  assert.equal(work(page).hidden, true, 'the line carries it; no duplicate row');
  // The mirrored copy of Kyle's message keeps the line under it; the reply clears it.
  chatSnap(page, [mirror('k1', { from: 'kyle', text: 'status?', ref: nonce, at: iso(NOW) })]);
  assert.equal(stage(page)[0], 'working');
  chatSnap(page, [mirror('r1', { text: 'All green.', at: iso(NOW + 2 * MIN) }),
    mirror('k1', { from: 'kyle', text: 'status?', ref: nonce, at: iso(NOW) })]);
  assert.equal(stage(page), null);
});

test('delivery: a mirrored copy alone counts as seen; 2 minutes with no pickup turns amber with Open session', async () => {
  const page = await chatPage();
  const c = chatOf(page);
  type(c, 'hello?');
  c.send.click();
  await settle();
  page.setNow(NOW + 119e3);
  page.intervals[0].fn();
  assert.equal(stage(page)[0], 'delivered');
  page.setNow(NOW + 121e3);
  page.intervals[0].fn();
  assert.deepEqual(stage(page), ['late',
    "Coordinator hasn't picked this up yet. It may be busy; it checks for missed messages after each reply.", '']);
  const open = dline(page).find((e) => e.tagName === 'A')[0];
  assert.deepEqual([open.hidden, open.textContent, open.href], [false, 'Open session', SESSION]);
  // A status for something else (no seen_ref) doesn't count as seen.
  statusSnap(page, { status: { state: 'idle', at: iso(NOW + 121e3) } });
  assert.equal(stage(page)[0], 'late');
  const nonce = /#([a-z0-9]{6})\]/.exec(sentText(page))[1];
  chatSnap(page, [mirror('k1', { from: 'kyle', text: 'hello?', ref: nonce, at: iso(NOW + 130e3) })]);
  assert.equal(stage(page)[0], 'seen');
  assert.equal(dline(page).find((e) => e.tagName === 'A')[0].hidden, true);
});

test('delivery: a failed send shows "Not sent: <reason>" in red under the bubble', async () => {
  for (const [code, want] of [['rate_limited', 'Not sent: too many messages. Wait a minute, then press Send.'],
    ['forbidden', 'Not sent: Sending from this page is off here. Use Open session.'],
    ['upstream_error', 'May have been sent; check before resending.']]) {
    const page = await chatPage();
    page.comments.replies.push(() => Promise.reject({ code, message: 'SECRET' }));
    type(chatOf(page), 'x');
    chatOf(page).send.click();
    await settle();
    assert.deepEqual(stage(page), ['failed', want, ''], code);
    assert.ok(!page.ids.coords.textContent.includes('SECRET'));
  }
});

test('delivery: hostile status text in the line stays text', async () => {
  const page = await chatPage();
  const c = chatOf(page);
  type(c, 'go');
  c.send.click();
  await settle();
  const nonce = /#([a-z0-9]{6})\]/.exec(sentText(page))[1];
  statusSnap(page, { status: { state: 'working', seen_ref: nonce, text: '<script>alert(1)</script>' + 'z'.repeat(200), at: iso(NOW + 1000) } });
  const t = byClass(dline(page), 'dtext')[0];
  assert.equal(t.children.length, 0);
  assert.ok(t.textContent.startsWith('Coordinator · working: <script>alert(1)</script>'));
  assert.equal(t.textContent.length, 'Coordinator · working: '.length + 80);
  assert.equal(page.ids.coords.find((e) => e.tagName === 'SCRIPT').length, 0);
});

test('working: the status subscription is paused on Runs and resumes on Grid', async () => {
  const page = await chatPage();
  assert.equal(page.db.calls.subsBy[STATUS], 1);
  page.ids['view-runs'].click();
  assert.equal(page.db.calls.unsubsBy[STATUS], 1);
  page.ids['view-grid'].click();
  await settle();
  assert.equal(page.db.calls.subsBy[STATUS], 2);
});

test('bubbles show the date too when a message is not from today', async () => {
  const page = await chatPage();
  chatSnap(page, [mirror('today', { at: iso(NOW - MIN) }), mirror('old', { at: iso(NOW - 3 * DAY) })]);
  const [old, today] = bubbles(page).map((b) => byClass(b, 'at')[0].textContent);
  assert.ok(old.length > today.length, `${old} | ${today}`);
  assert.doesNotMatch(today, /[A-Za-z]{3} \d/);
  assert.match(old, /[A-Za-z]{3} \d/, 'a month and day in front');
});

// ---- round 2: judge findings ----

test('delivery: a working status for another task (no seen_ref) never shows as working: queued while in task, amber only when idle', async () => {
  const page = await chatPage();
  const c = chatOf(page);
  type(c, 'hi');
  c.send.click();
  await settle();
  statusSnap(page, { status: { state: 'working', text: 'Old task: judge round 2', at: iso(NOW + 30e3) } });
  assert.deepEqual(stage(page), ['queued', 'Queued · coordinator is mid-task; it reads new messages when this step ends', 'spin']);
  page.setNow(NOW + 125e3);
  statusSnap(page, { status: { state: 'working', text: 'Old task: pushing', at: iso(NOW + 120e3) } });
  assert.equal(stage(page)[0], 'queued', 'still queued past 2 minutes while in task: no amber');
  statusSnap(page, { status: { state: 'idle', at: iso(NOW + 121e3) } });
  assert.equal(stage(page)[0], 'late', 'amber once idle without a pickup');
  // Seen is latched: a later step write without seen_ref keeps it working.
  const nonce = /#([a-z0-9]{6})\]/.exec(sentText(page))[1];
  statusSnap(page, { status: { state: 'working', text: 'Reading your message', seen_ref: nonce, at: iso(NOW + 124e3) } });
  assert.equal(stage(page)[0], 'working');
  statusSnap(page, { status: { state: 'working', text: 'Running tests', at: iso(NOW + 125e3) } });
  assert.deepEqual(stage(page).slice(0, 2), ['working', 'Coordinator · working: Running tests']);
  statusSnap(page, { status: { state: 'idle', at: iso(NOW + 126e3) } });
  assert.equal(stage(page)[0], 'seen');
});

test('docsOf: a document snapshot with exists() as a method is read', async () => {
  const page = await chatPage();
  page.db.handlers(STATUS).next({ id: `chat-${TAIL}`, exists: () => true, metadata: { fromCache: false },
    data: () => ({ status: { state: 'working', text: 'Method form', at: iso(NOW) } }) });
  assert.equal(pill(page)[1], 'In task: Method form · just now');
  page.db.handlers(STATUS).next({ id: `chat-${TAIL}`, exists: () => false, data: () => undefined, metadata: {} });
  assert.equal(work(page).hidden, true);
});

test('delivery: the reply clears the line even when Kyle\'s clock runs ahead of the master\'s', async () => {
  const page = await chatPage();
  chatSnap(page, [mirror('before', { text: 'earlier', at: iso(NOW - 20 * MIN) })]);
  page.setNow(NOW + 10 * MIN);  // Kyle's clock is 10 minutes fast
  const c = chatOf(page);
  type(c, 'go');
  c.send.click();
  await settle();
  const nonce = /#([a-z0-9]{6})\]/.exec(sentText(page))[1];
  const earlier = mirror('before', { text: 'earlier', at: iso(NOW - 20 * MIN) });
  // An older coordinator message, or one before Seen, doesn't clear it.
  statusSnap(page, { status: { state: 'working', seen_ref: nonce, text: 'Reading', at: iso(NOW) } });
  chatSnap(page, [mirror('k1', { from: 'kyle', text: 'go', ref: nonce, at: iso(NOW) }), earlier]);
  assert.equal(stage(page)[0], 'working');
  chatSnap(page, [mirror('r1', { text: 'Done.', at: iso(NOW + MIN) }), mirror('k1', { from: 'kyle', text: 'go', ref: nonce, at: iso(NOW) }), earlier]);
  assert.equal(stage(page), null, 'cleared by the master-clock reply after the copy');
  // An activity row is not a reply.
  const again = await chatPage();
  type(chatOf(again), 'x');
  chatOf(again).send.click();
  await settle();
  const n2 = /#([a-z0-9]{6})\]/.exec(sentText(again))[1];
  chatSnap(again, [mirror('a1', { kind: 'activity', text: 'Ran tests', at: iso(NOW + MIN) }),
    mirror('k1', { from: 'kyle', text: 'x', ref: n2, at: iso(NOW) })]);
  assert.equal(stage(again)[0], 'seen');
});

// ---- activity rows ----

test('activity: one compact text-only row each; consecutive ones collapse into an expandable "N steps" row', async () => {
  const page = await chatPage();
  chatSnap(page, [
    mirror('a4', { kind: 'activity', text: 'Started the judge', at: iso(NOW - MIN) }),
    mirror('u1', { text: 'Building **round 2**', at: iso(NOW - 2 * MIN) }),
    mirror('a3', { kind: 'activity', text: 'Pushed claude/x', at: iso(NOW - 3 * MIN) }),
    mirror('a2', { kind: 'activity', text: '<img src=x onerror=alert(1)> **not bold** ' + 'w'.repeat(300), at: iso(NOW - 4 * MIN) }),
    mirror('a1', { kind: 'activity', text: 'Ran tests · 151 passed', at: iso(NOW - 5 * MIN) }),
  ]);
  const kids = chatOf(page).log.children;
  assert.deepEqual(kids.map((e) => e.className), ['acts', 'msg', 'act']);
  const [group, , single] = kids;
  const toggle = byClass(group, 'acts-toggle')[0];
  const list = byClass(group, 'acts-list')[0];
  assert.deepEqual([toggle.textContent, toggle.getAttribute('aria-expanded'), list.hidden], ['3 steps ▸', 'false', true]);
  toggle.click();
  assert.deepEqual([toggle.textContent, toggle.getAttribute('aria-expanded'), list.hidden], ['3 steps ▾', 'true', false]);
  const rows = byClass(list, 'act');
  assert.deepEqual(rows.map((r) => byClass(r, 'icon')[0].textContent), ['✓', '•', '✓']);
  const hostile = byClass(rows[1], 'atext')[0];
  assert.equal(hostile.children.length, 0, 'text only, no markdown');
  assert.equal(hostile.textContent.length, 200);
  assert.ok(hostile.textContent.includes('**not bold**'));
  assert.equal(page.ids.coords.find((e) => e.tagName === 'IMG').length, 0);
  assert.deepEqual([byClass(single, 'icon')[0].textContent, byClass(single, 'atext')[0].textContent], ['•', 'Started the judge']);
  // Expanded stays expanded across a re-render.
  runSnap(page, [runRow({ phase: 'judging' })]);
  assert.equal(byClass(chatOf(page).log.children[0], 'acts-list')[0].hidden, false);
});

test('chat: the composer has an arrow Send button and the reply placeholder; fences get a text language label', async () => {
  const page = await chatPage();
  const c = chatOf(page);
  assert.deepEqual([c.send.textContent, c.send.getAttribute('aria-label')], ['↑', 'Send']);
  assert.equal(c.input.placeholder, 'Reply to the coordinator…');
  const md = page.api.renderMarkdown('```python\nx = 1\n```\n```"><script>\ny\n```');
  const [a, b] = md.children;
  assert.deepEqual([a.dataset.lang, a.textContent], ['python', 'x = 1']);
  assert.equal(b.dataset.lang, undefined, 'an odd info string gets no label');
});

// ---- presence: in task or idle, at any time ----

test('presence: the pill shows in task, maybe, idle or unknown, with no message sent', async () => {
  const page = await chatPage();
  assert.equal(page.comments.calls.send.length, 0);
  assert.deepEqual(pill(page), ['unknown', 'Status unknown', false]);
  const at = (m) => iso(NOW - m * MIN);
  const cases = [
    [{ state: 'working', text: 'Judging round 2', at: at(4) }, ['task', 'In task: Judging round 2 · 4m']],
    [{ state: 'working', text: 'x', at: at(30) }, ['task', 'In task: x · 30m']],
    [{ state: 'working', text: 'x', at: at(31) }, ['maybe', 'In task (no update for 31m)']],
    [{ state: 'working', text: 'x', at: at(45) }, ['maybe', 'In task (no update for 45m)']],
    [{ state: 'working', text: 'x', at: at(120) }, ['maybe', 'In task (no update for 2h 0m)']],
    [{ state: 'working', text: 'x', at: at(121) }, ['idle', 'Idle · last active 2h 1m ago']],
    [{ state: 'idle', at: at(12) }, ['idle', 'Idle · last active 12m ago']],
    [{ state: 'idle', at: iso(NOW) }, ['idle', 'Idle · last active just now']],
    [{ state: 'working' }, ['idle', 'Idle']],
    [{ state: 'pondering', at: at(1) }, ['unknown', 'Status unknown']],
  ];
  for (const [status, want] of cases) {
    statusSnap(page, { status });
    assert.deepEqual(pill(page).slice(0, 2), want, JSON.stringify(status));
  }
  statusSnap(page, null);
  assert.deepEqual(pill(page).slice(0, 2), ['unknown', 'Status unknown']);
  // Elapsed times refresh on the interval.
  statusSnap(page, { status: { state: 'working', text: 'Tests', at: at(4) } });
  page.setNow(NOW + 27 * MIN);
  page.intervals[0].fn();
  assert.deepEqual(pill(page).slice(0, 2), ['maybe', 'In task (no update for 31m)']);
  // No user id: unknown.
  const none = await chatPage({ user: null });
  assert.deepEqual(pill(none).slice(0, 2), ['unknown', 'Status unknown']);
});

// ---- hidden windows: layout h: 1, per viewer, never in the db ----

const hideOf = (t) => byClass(t, 'hide')[0];
const trayItems = (page) => page.ids['grid-tray-list'].children;
const dbWrites = (page) => Object.keys(page.db.calls.queries).filter((q) => /set|update|delete|add/i.test(q));

test('hide: the window leaves the workspace and the count, the tray says Hidden (1), and it stays hidden after a reload', async () => {
  const storage = memStorage();
  const page = await layoutPage({ storage });
  const a2 = tile(page, 'agent-a2');
  const hide = hideOf(a2);
  assert.equal(hide.textContent, '–');
  assert.equal(hide.getAttribute('aria-label'), 'Hide builder');
  hide.click();
  assert.deepEqual(order(page), [C0, 'agent-a1', 'agent-a3', 'agent-a4']);
  assert.equal(page.ids['grid-count'].textContent, '4 windows · 1 hidden');
  assert.equal(page.ids['grid-tray'].hidden, false);
  assert.equal(page.ids['grid-tray-sum'].textContent, 'Hidden (1)');
  assert.equal(trayItems(page).length, 1);
  assert.match(trayItems(page)[0].textContent, /builder/);
  assert.equal(trayItems(page)[0].textContent.endsWith('running'), true, 'a running marker');
  assert.deepEqual({ ...saved(storage).tiles.a2 }, { h: 1 });
  assert.equal(saved(storage).v, 2);
  assert.match(page.ids['grid-live'].textContent, /^Hid builder/);
  const again = await layoutPage({ storage });
  assert.deepEqual(order(again), [C0, 'agent-a1', 'agent-a3', 'agent-a4']);
  assert.equal(again.ids['grid-tray-sum'].textContent, 'Hidden (1)');
  assert.deepEqual([...again.api.hiddenIds()], ['a2']);
  assert.deepEqual(dbWrites(page), []);
  for (const k of Object.keys(storage.store)) {
    assert.ok(['agent-dashboard-view', LAYOUT, 'agentDashboard.filter'].includes(k), `only the view, layout and filter keys: ${k}`);
  }
});

test('restore: back in its saved place and size, h removed, the tray goes away', async () => {
  const storage = memStorage();
  const page = await layoutPage({ storage });
  // Move a3 first and make it wide, then hide and restore it.
  const g = gripOf(tile(page, 'agent-a3'));
  g.dispatch('keydown', { key: 'Enter' });
  g.dispatch('keydown', { key: 'ArrowLeft' });
  g.dispatch('keydown', { key: 'Enter' });
  resizeOf(tile(page, 'agent-a3')).dispatch('keydown', { key: 'ArrowRight', shiftKey: true });
  const before = order(page);
  assert.deepEqual(before, [C0, 'agent-a1', 'agent-a3', 'agent-a2', 'agent-a4']);
  hideOf(tile(page, 'agent-a3')).click();
  assert.equal(tile(page, 'agent-a3'), undefined);
  trayItems(page)[0].click();
  assert.deepEqual(order(page), before);
  assert.equal(tile(page, 'agent-a3').dataset.cols, '2');
  assert.deepEqual({ ...saved(storage).tiles.a3 }, { o: 2, c: 2, r: 1 });
  assert.equal(page.ids['grid-tray'].hidden, true);
  assert.equal(page.ids['grid-count'].textContent, '5 windows');
  assert.equal(page.ids['grid-live'].textContent, 'Restored builder.');
  assert.ok(byClass(tile(page, 'agent-a3'), 'max')[0].focused > 0, 'focus goes to the window');
});

test('Show all clears every h; Reset layout clears them too and says so', async () => {
  const storage = memStorage();
  const page = await layoutPage({ storage });
  hideOf(tile(page, 'agent-a1')).click();
  hideOf(tile(page, 'agent-a4')).click();
  assert.equal(page.ids['grid-tray-sum'].textContent, 'Hidden (2)');
  page.ids['grid-showall'].click();
  assert.deepEqual(order(page), DEFAULT);
  assert.deepEqual({ ...saved(storage).tiles }, {});
  assert.equal(page.ids['grid-live'].textContent, 'Showing 2 hidden windows again.');
  hideOf(tile(page, 'agent-a1')).click();
  page.ids['grid-reset'].click();
  assert.deepEqual(order(page), DEFAULT);
  assert.equal(storage.store[LAYOUT], undefined);
  assert.equal(page.ids['grid-live'].textContent, 'Layout reset; hidden windows are shown again.');
});

test('a hidden coordinator keeps its draft and its chat; the tray shows question', async () => {
  const storage = memStorage();
  const page = await chatPage({ storage });
  const c = chatOf(page);
  chatSnap(page, [mirror('u1', { text: 'Tests green' })]);
  type(c, 'half-typed reply');
  hideOf(c.panel).click();
  assert.equal(panels(page).length, 0);
  // A new mirrored question arrives while hidden: the chat stays subscribed.
  chatSnap(page, [mirror('q1', { kind: 'question', text: 'Merge now?', at: iso(NOW) }), mirror('u1', { text: 'Tests green' })]);
  assert.equal(trayItems(page)[0].textContent.endsWith('question'), true);
  trayItems(page)[0].click();
  const back = chatOf(page);
  assert.equal(back.panel, c.panel, 'the same window, not a rebuilt one');
  assert.equal(back.input.value, 'half-typed reply');
  assert.deepEqual(bubbles(page).map(bubbleText), ['Tests green', 'Merge now?']);
  assert.ok(back.input.focused > 0, 'focus goes to the chat box');
  assert.equal(page.comments.calls.send.length, 0);
});

test('hiding the maximized window un-maximizes; hiding mid-drag cancels the drag', async () => {
  const storage = memStorage();
  const page = await layoutPage({ storage });
  const a1 = tile(page, 'agent-a1');
  byClass(a1, 'max')[0].click();
  assert.ok('max' in page.ids.coords.dataset);
  hideOf(a1).click();
  assert.equal('max' in page.ids.coords.dataset, false);
  assert.equal(tile(page, 'agent-a2').dataset.max, undefined);
  const a3 = tile(page, 'agent-a3');
  gripOf(a3).dispatch('pointerdown', { button: 0, pointerId: 7, clientX: 345, clientY: 40 });
  assert.ok('dragging' in a3.dataset);
  hideOf(a3).click();
  assert.equal('dragging' in a3.dataset, false);
  assert.equal((page.winListeners.pointermove ?? []).length, 0, 'the drag listeners are gone');
  page.win('pointerup', { pointerId: 7, clientX: 45, clientY: 40 });
  assert.deepEqual(order(page), [C0, 'agent-a2', 'agent-a4']);
});

test('hidden flags: 250 entries load capped at 200; a gone id is pruned on save; junk h is ignored', async () => {
  const tiles = {};
  for (let i = 0; i < 250; i += 1) tiles[`x${i}`] = { h: 1 };
  tiles.a1 = { h: 1 };
  const big = await layoutPage({ storage: memStorage({ [LAYOUT]: JSON.stringify({ v: 2, tiles }) }) });
  assert.deepEqual(order(big), DEFAULT, 'a1 is past the 200 cap, so it is not hidden');
  const storage = memStorage({ [LAYOUT]: JSON.stringify({ v: 2, tiles: {
    gone: { h: 1 }, a1: { h: true }, a2: { h: '1' }, a3: { h: 2 }, a4: { h: 1 } } }) });
  const page = await layoutPage({ storage });
  assert.deepEqual(order(page), [C0, 'agent-a1', 'agent-a2', 'agent-a3']);
  hideOf(tile(page, 'agent-a1')).click();
  assert.deepEqual({ ...saved(storage).tiles }, { a1: { h: 1 }, a4: { h: 1 } });
  assert.deepEqual(dbWrites(page), []);
});

// ---- "Get desktop app": the launchers, saved through downloads ----

const DESKTOP = (name) => readFileSync(new URL(`../../tools/agent-dashboard/desktop/${name}`, import.meta.url), 'latin1');

// The one stored file in a zip: [name, text, unix mode or 0].
function unzipOne(bytes) {
  const b = Buffer.from(bytes);
  assert.equal(b.readUInt32LE(0), 0x04034b50, 'a local file header');
  assert.equal(b.readUInt16LE(8), 0, 'stored, not compressed');
  const size = b.readUInt32LE(18);
  const nameLen = b.readUInt16LE(26);
  const name = b.subarray(30, 30 + nameLen).toString('latin1');
  const text = b.subarray(30 + nameLen, 30 + nameLen + size).toString('latin1');
  const end = b.length - 22;
  assert.equal(b.readUInt32LE(end), 0x06054b50, 'an end record');
  assert.equal(b.readUInt16LE(end + 10), 1, 'exactly one file');
  const cd = b.readUInt32LE(end + 16);
  assert.equal(b.readUInt32LE(cd), 0x02014b50);
  const crc = b.readUInt32LE(14);
  assert.equal(b.readUInt32LE(cd + 16), crc);
  const mode = b.readUInt32LE(cd + 38) >>> 16;
  // The CRC is right (zlib's crc32, when this Node has it).
  return [name, text, mode, crc];
}

test('get desktop app: nothing is looked up or saved at load; a choice saves exactly one launcher', async () => {
  const downloads = fakeDownloads();
  const page = await loadPage({ downloads });
  assert.equal(page.ids.getapp.hidden, false, 'the button shows inside claude.ai');
  assert.equal(page.uses.includes('downloads'), false, 'no lookup at load');
  assert.equal(downloads.calls.save.length, 0);
  page.ids['getapp-btn'].click();
  assert.equal(page.ids['getapp-menu'].hidden, false);
  assert.equal(page.ids['getapp-btn'].getAttribute('aria-expanded'), 'true');
  page.ids['getapp-dash-win'].click();
  await settle();
  assert.equal(downloads.calls.save.length, 1);
  const req = downloads.calls.save[0];
  assert.equal(req.filename, 'open-dashboard-windows.zip');
  const [name, text, mode] = unzipOne(req.data);
  assert.equal(name, 'open-dashboard.cmd');
  assert.equal(text, DESKTOP('open-dashboard.cmd'), 'the same text as desktop/, CRLF');
  assert.ok(text.includes('\r\n') && !text.replace(/\r\n/g, '').includes('\n'));
  assert.equal(mode, 0);
  assert.equal(page.ids['getapp-menu'].hidden, true);
  assert.match(page.ids['getapp-hint'].textContent, /^Saved\. Unzip it and double-click open-dashboard\.cmd to open the agent dashboard in its own window\.$/);
  // The Mac launcher: LF and executable; the hint says right-click, Open.
  page.ids['getapp-btn'].click();
  page.ids['getapp-dash-mac'].click();
  await settle();
  const mac = downloads.calls.save[1];
  assert.equal(mac.filename, 'open-dashboard-mac.zip');
  const [mname, mtext, mmode] = unzipOne(mac.data);
  assert.equal(mname, 'open-dashboard.command');
  assert.equal(mtext, DESKTOP('open-dashboard.command'));
  assert.ok(!mtext.includes('\r'));
  assert.equal(mmode, 0o100755);
  assert.match(page.ids['getapp-hint'].textContent, /right-click it and choose Open/);
  // The PC hub, from the same chooser and the same generator.
  page.ids['getapp-hub-win'].click();
  page.ids['getapp-hub-win'].click();  // a second click while the first save is open does nothing
  await settle();
  page.ids['getapp-hub-mac'].click();
  await settle();
  assert.deepEqual(downloads.calls.save.map((r) => r.filename).slice(2), ['open-hub-windows.zip', 'open-hub-mac.zip']);
  for (const r of downloads.calls.save.slice(2)) {
    const [n, t] = unzipOne(r.data);
    assert.equal(t, DESKTOP(n));
    assert.match(t, /http:\/\/127\.0\.0\.1:8765\/devteam/);
  }
  assert.equal(page.uses.filter((u) => u === 'downloads').length, 1, 'looked up once');
});

test('get desktop app: CRC-32 of the stored launcher is correct', async () => {
  const downloads = fakeDownloads();
  const page = await loadPage({ downloads });
  page.ids['getapp-dash-mac'].click();
  await settle();
  const [, text, , crc] = unzipOne(downloads.calls.save[0].data);
  const zlib = await import('node:zlib');
  if (typeof zlib.crc32 === 'function') assert.equal(crc, zlib.crc32(Buffer.from(text, 'latin1')));
  else {
    let c = 0xffffffff;
    for (const b of Buffer.from(text, 'latin1')) { c ^= b; for (let k = 0; k < 8; k += 1) c = c & 1 ? 0xedb88320 ^ (c >>> 1) : c >>> 1; }
    assert.equal(crc, (c ^ 0xffffffff) >>> 0);
  }
});

test('get desktop app: no downloads capability hides the button; outside claude.ai there is none', async () => {
  const page = await loadPage({ downloads: null });
  page.ids['getapp-btn'].click();
  page.ids['getapp-dash-win'].click();
  await settle();
  assert.equal(page.ids.getapp.hidden, true);
  const outside = await loadPage({ withClaude: false });
  assert.equal(outside.ids.getapp.hidden, true);
});

test('get desktop app: a declined save is quiet; other rejections get a calm line; unavailable hides it', async () => {
  const downloads = fakeDownloads();
  const page = await loadPage({ downloads });
  const reject = (code) => () => Promise.reject(Object.assign(new Error(code), { code }));
  downloads.replies.push(reject('declined'));
  page.ids['getapp-dash-win'].click();
  await settle();
  assert.equal(page.ids['getapp-hint'].hidden, true, 'declining is not an error');
  downloads.replies.push(reject('rate_limited'));
  page.ids['getapp-dash-win'].click();
  await settle();
  assert.equal(page.ids['getapp-hint'].textContent, 'Another save is still open. Try again in a moment.');
  downloads.replies.push(reject('something_new'));
  page.ids['getapp-dash-win'].click();
  await settle();
  assert.equal(page.ids['getapp-hint'].textContent, "Couldn't save it. Try again.");
  assert.equal(page.ids.getapp.hidden, false);
  downloads.replies.push(reject('unavailable'));
  page.ids['getapp-dash-win'].click();
  await settle();
  assert.equal(page.ids.getapp.hidden, true);
  assert.equal(downloads.calls.save.length, 4, 'never retried by itself');
});
