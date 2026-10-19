// node --test tests/js/agent-dashboard.test.mjs  (Node 18+; no packages needed)
//
// Runs the dev-team dashboard page's own <script> (tools/agent-dashboard/
// index.html) in node:vm with a minimal fake document, a fake
// window.claude.use("db") (one query per collection: runs and agents), a
// fake use("comments") (composer_only: openComposer), fake timers and a
// fake localStorage, then checks what it decides and renders: silent/
// running/needs at the time limits, safe links, row text that stays text,
// each db error code -> stop, back off or retry (for both queries), the
// coordinator panels and agent tiles of the Grid, Message coordinator, and
// the per-viewer tile layout (drag, resize, keyboard, storage).

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

// The composer_only comments namespace: openComposer records each target and
// answers from a queue of functions (default: {opened: true}).
function fakeComments() {
  const calls = { open: [] };
  const replies = [];
  const ns = {
    openComposer(target) {
      calls.open.push(target);
      const next = replies.shift();
      return next ? next(target) : Promise.resolve({ opened: true });
    },
  };
  return { ns, calls, replies };
}

const PAGE_IDS = {
  summary: 'p', conn: 'p', runs: 'ul', empty: 'p',
  main: 'main', 'runs-view': 'section', 'grid-view': 'section', 'view-runs': 'button', 'view-grid': 'button',
  'grid-summary': 'p', 'grid-conn': 'p', coords: 'div', 'grid-hidden': 'p', 'grid-reset': 'button', 'grid-live': 'p',
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
  hash = undefined, range = 'ok', narrow = undefined } = {}) {
  const ids = {};
  for (const [id, tag] of Object.entries(PAGE_IDS)) ids[id] = new FakeEl(tag);
  ids['grid-view'].hidden = true;
  const timers = [];
  const intervals = [];
  const ranges = [];
  const document = {
    hidden: false,
    getElementById: (id) => ids[id] ?? null,
    createElement: (tag) => new FakeEl(tag),
    addEventListener: () => {},
    // Markup sinks a regression might reach for. They "parse" tags into
    // child elements, so a test sees markup become elements, not a crash.
    // selectNodeContents records the node; range 'throw' / 'select-throws'
    // make createRange or selectNodeContents fail.
    createRange: () => {
      if (range === 'throw') throw new Error('no ranges here');
      const r = {
        createContextualFragment: parseTags,
        selectNodeContents(node) {
          if (range === 'select-throws') throw new Error('bad node');
          r.node = node;
        },
      };
      ranges.push(r);
      return r;
    },
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
  // bubble there); win() delivers one to every listener the page added.
  const winListeners = {};
  sandbox.addEventListener = (type, fn) => { (winListeners[type] ??= []).push(fn); };
  sandbox.removeEventListener = (type, fn) => { winListeners[type] = (winListeners[type] ?? []).filter((f) => f !== fn); };
  const win = (type, extra = {}) => {
    for (const fn of [...(winListeners[type] ?? [])]) fn({ type, preventDefault() {}, ...extra });
  };
  const uses = [];
  if (withClaude) {
    sandbox.claude = {
      use: async (cap) => {
        uses.push(cap);
        return cap === 'db' ? db.db : cap === 'comments' ? (comments && comments.ns) : null;
      },
    };
  }
  if (storage !== undefined) sandbox.localStorage = storage;
  if (hash !== undefined) sandbox.location = { hash };
  if (narrow !== undefined) sandbox.matchMedia = () => ({ matches: narrow, addEventListener: () => {} });
  sandbox.window = sandbox;
  const ctx = vm.createContext(sandbox);
  vm.runInContext(`Date.now = () => ${NOW};`, ctx);
  vm.runInContext(SCRIPT, ctx, { filename: 'index.html' });
  await new Promise((r) => setImmediate(r));
  const pending = () => timers.filter((t) => !t.cleared);
  return { api: sandbox.AgentDashboard, ids, db, comments, uses, timers, pending, intervals, ranges, win, winListeners };
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

// ==== Grid: coordinator panels (runs rows) and agent tiles (agents rows) ====

const SESSION = 'https://claude.ai/code/session_01KA1RGMx3zUcsTDp3cT5KUt';
const PR = 'https://github.com/kylechen839-max/Claude-Personal-Assistant/pull/';
const RUN = '20261019-183000-voice-speed';

async function gridPage(opts = {}) {
  const page = await loadPage(opts);
  page.ids['view-grid'].click();
  await settle();
  return page;
}
const panels = (page) => page.ids.coords.find((e) => e.className === 'tile coord');
const agentTiles = (root) => root.find((e) => e.className === 'tile agent');
const boards = (page) => page.ids.coords.children;
const h2Of = (tile) => tile.find((e) => e.tagName === 'H2')[0].textContent;
const byClass = (root, cls) => root.find((e) => e.className === cls);
const button = (root, label) => root.find((e) => e.tagName === 'BUTTON' && e.textContent === label)[0];
const visibleLinks = (root) => root.find((e) => e.tagName === 'A' && !e.hidden);
const runSnap = (page, list) => page.db.handlers('runs').next(snap(list));
const agentSnap = (page, list) => page.db.handlers('agents').next(snap(list));
const agentsQueries = (page) => page.db.calls.subsBy.agents ?? 0;

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
  assert.equal(page.comments.calls.open.length, 0);
  assert.equal(page.pending().length, 0);
  // Runs renders as before; a card keeps its Session and PR links only.
  runSnap(page, [runRow({ pr_url: `${PR}7` })]);
  assert.equal(page.ids.summary.textContent, 'Nothing needs you · 1 running');
  const [card] = page.ids.runs.children;
  assert.deepEqual(card.find((e) => e.tagName === 'A').map((a) => a.textContent), ['Session', 'PR #7']);
  assert.equal(card.find((e) => e.tagName === 'BUTTON').length, 0, 'no Chat button');
});

test('Grid starts one agents query and one comments lookup; Runs pauses it, Grid resumes once', async () => {
  const page = await gridPage();
  assert.equal(page.ids.main.dataset.view, 'grid');
  assert.equal(page.ids['grid-view'].hidden, false);
  assert.deepEqual(page.db.calls.queries.agents, ['agents', 'started_at', 'desc', 200]);
  assert.equal(agentsQueries(page), 1);
  assert.equal(page.uses.filter((u) => u === 'comments').length, 1);
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
  assert.equal(page.uses.filter((u) => u === 'comments').length, 1, 'the comments lookup is memoized');
  assert.equal(page.db.calls.subs, 1);
  assert.equal(page.pending().length, 0);
  assert.equal(page.intervals.length, 1, 'only the one re-render interval');
  assert.equal(page.comments.calls.open.length, 0, 'nothing opens the composer by itself');
});

test('the coordinator panel: feature, phase, round, elapsed, newest 5 status lines, links', async () => {
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
  assert.equal(panels(page).length, 1, 'rows sharing a session make one panel');
  assert.equal(panel.id, `coord-${RUN}`);
  assert.equal(h2Of(panel), 'Voice speed');
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
  assert.deepEqual(visibleLinks(panel).map((a) => [a.textContent, a.href, a.rel]).sort(), [
    ['Open session', SESSION, 'noopener noreferrer'],
    ['PR #12', `${PR}12`, 'noopener noreferrer'],
  ]);
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
});

test('grouping: pieces and the plain row share a panel; agents join it; unmatched are counted', async () => {
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
  assert.equal(panels(page).length, 1, 'the done plain row without a live piece is not its own panel');
  const [panel] = panels(page);
  assert.equal(panel.id, `coord-${RUN}`);
  assert.equal(h2Of(panel), 'Voice speed');
  const whens = byClass(panel, 'when').map((e) => e.textContent);
  assert.ok(!whens.some((w) => w.startsWith('Voice speed:')), 'the plain done row hands over to its pieces');
  const [board] = boards(page);
  const tiles = agentTiles(board);
  // Running first (newest start first), then done.
  assert.deepEqual(tiles.map(h2Of), ['builder · settings', 'judge · ui', 'researcher', 'builder · ui']);
  assert.deepEqual(tiles.map((t) => t.id), [`agent-${RUN}-settings.builder.r1`, `agent-${RUN}-ui.judge.r1`,
    `agent-${RUN}.researcher.r0`, `agent-${RUN}-ui.builder.r1`]);
  // Counted: only the live agent whose run has no row at all ('other-run'
  // has a row, just no shown panel).
  assert.equal(page.ids['grid-hidden'].textContent, '1 agent hidden');
  assert.equal(page.ids['grid-hidden'].hidden, false);
  assert.equal(page.ids['grid-summary'].textContent, '1 active run · 2 agents running');
  const api = page.api;
  const g = api.groupCoordinators([{ _id: 'a', phase: 'building', updated_at: iso(NOW) }], [{ run_id: 'b' }], NOW);
  assert.deepEqual([g.panels.length, g.hidden], [1, 1]);
  // Panel ids keep only safe characters.
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
  assert.equal(byClass(panels(page)[0], 'kicker')[0].textContent, 'Last run');
  assert.equal(page.ids['grid-summary'].textContent, 'Nothing running · last run below');
  runSnap(page, []);
  assert.equal(panels(page).length, 0);
  assert.equal(page.ids['grid-summary'].textContent, 'No runs yet');
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

test('agent tiles render text only, with the retired and unknown rules', async () => {
  const page = await gridPage();
  runSnap(page, [runRow()]);
  agentSnap(page, [
    agent('a1', { role: '<script>x</script>', state: 'weird', outcome: '<b>built</b>, tests green' }),
    agent('a2', { state: 'done', finished_at: iso(NOW - 8 * DAY), updated_at: iso(NOW - 8 * DAY), started_at: iso(NOW - 9 * DAY) }),
    agent('a3', { state: 'done', started_at: iso(NOW - 40 * MIN), finished_at: iso(NOW - 10 * MIN), outcome: 'built, tests green' }),
  ]);
  const tiles = agentTiles(page.ids.coords);
  assert.deepEqual(tiles.map((t) => t.id), ['agent-a3', 'agent-a1'], 'the retired tile is gone; unknown sorts last');
  const [a3, a1] = tiles;
  assert.equal(h2Of(a1), '<script>x</script>');
  assert.equal(byClass(a1, 'status')[0].textContent, '<b>built</b>, tests green');
  assert.equal(a1.find((e) => ['SCRIPT', 'B'].includes(e.tagName)).length, 0);
  assert.equal(byClass(a1, 'label')[0].textContent, 'weird');
  assert.match(byClass(a3, 'when')[0].textContent, /^round 1 · started .+ · 30 min$/);
  assert.equal(page.ids['grid-hidden'].hidden, true, 'retired tiles are not counted as hidden');
});

test('each state gets its badge class, on agent tiles and on the coordinator panel', async () => {
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

// ---- Message coordinator ----

test('Message coordinator: one openComposer per click, a Range over the run-id line', async () => {
  const page = await gridPage();
  runSnap(page, [runRow()]);
  const [panel] = panels(page);
  const msg = button(panel, 'Message coordinator');
  assert.equal(msg.hidden, false);
  assert.equal(byClass(panel, 'hint')[0].textContent, 'Press Send to Claude so the coordinator gets it.');
  assert.equal(page.comments.calls.open.length, 0, 'nothing at load');
  page.intervals[0].fn();
  assert.equal(page.comments.calls.open.length, 0, 'nothing on the timer');
  msg.click();
  await settle();
  assert.equal(page.comments.calls.open.length, 1);
  const [target] = page.comments.calls.open;
  assert.deepEqual(Object.keys(target), ['range']);
  assert.equal(target.range.node, byClass(panel, 'runid')[0]);
  assert.equal(target.range.node.textContent, `run ${RUN}`);
  assert.equal(page.pending().length, 0);
  // The panel is updated in place: the same element after another snapshot.
  runSnap(page, [runRow({ phase: 'judging' })]);
  assert.equal(panels(page)[0], panel);
  assert.equal(byClass(panel, 'runid')[0], target.range.node);
});

for (const range of ['throw', 'select-throws']) {
  test(`Message coordinator falls back to {element: panel} when the Range ${range}`, async () => {
    const page = await gridPage({ range });
    runSnap(page, [runRow()]);
    const [panel] = panels(page);
    button(panel, 'Message coordinator').click();
    await settle();
    assert.equal(page.comments.calls.open.length, 1);
    assert.deepEqual(Object.keys(page.comments.calls.open[0]), ['element']);
    assert.equal(page.comments.calls.open[0].element, panel);
  });
}

test('opened:false shows the hint and never calls again by itself', async () => {
  const page = await gridPage();
  runSnap(page, [runRow()]);
  page.comments.replies.push(() => Promise.resolve({ opened: false }));
  const [panel] = panels(page);
  button(panel, 'Message coordinator').click();
  await settle();
  assert.equal(byClass(panel, 'hint')[0].textContent, 'Click the page, then try again.');
  for (const t of page.pending()) t.fn();
  page.intervals[0].fn();
  await settle();
  assert.equal(page.comments.calls.open.length, 1);
  assert.equal(button(panel, 'Message coordinator').hidden, false);
});

test('while one composer open is pending, another panel\'s button makes no second call', async () => {
  const page = await gridPage();
  runSnap(page, [runRow(), runRow({ _docId: 'second', feature: 'Second', session_url: undefined })]);
  let finish;
  page.comments.replies.push(() => new Promise((resolve) => { finish = resolve; }));
  const [first, second] = panels(page);
  button(first, 'Message coordinator').click();
  await settle();
  button(second, 'Message coordinator').click();
  button(first, 'Message coordinator').click();
  await settle();
  assert.equal(page.comments.calls.open.length, 1);
  finish({ opened: true });
  await settle();
  button(second, 'Message coordinator').click();
  await settle();
  assert.equal(page.comments.calls.open.length, 2, 'a new click after it settles works');
});

test('rate_limited says to slow down; no retry', async () => {
  const page = await gridPage();
  runSnap(page, [runRow()]);
  page.comments.replies.push(() => Promise.reject({ code: 'rate_limited', message: 'SECRET' }));
  const [panel] = panels(page);
  button(panel, 'Message coordinator').click();
  await settle();
  assert.equal(byClass(panel, 'hint')[0].textContent, 'Too fast, try again in a moment.');
  assert.equal(page.comments.calls.open.length, 1);
  assert.equal(page.pending().length, 0);
  assert.ok(!page.ids.coords.textContent.includes('SECRET'));
});

for (const code of ['unavailable', 'not_granted', 'capability_disabled', 'capability_removed']) {
  test(`${code} hides every Message button for the visit; Open session stays`, async () => {
    const page = await gridPage();
    runSnap(page, [runRow(), runRow({ _docId: 'second', feature: 'Second', session_url: undefined })]);
    page.comments.replies.push(() => Promise.reject({ code }));
    const msg = button(panels(page)[0], 'Message coordinator');
    msg.click();
    await settle();
    for (const p of panels(page)) assert.equal(button(p, 'Message coordinator').hidden, true);
    assert.deepEqual(visibleLinks(panels(page).find((p) => p.id === `coord-${RUN}`)).map((a) => a.textContent), ['Open session']);
    msg.click();
    await settle();
    assert.equal(page.comments.calls.open.length, 1, 'a hidden button never calls');
    runSnap(page, [runRow()]);
    assert.equal(button(panels(page)[0], 'Message coordinator').hidden, true, 'still hidden after a re-render');
  });
}

test('use("comments") resolving null: no Message button, Open session is there', async () => {
  const page = await gridPage({ comments: null });
  runSnap(page, [runRow()]);
  const [panel] = panels(page);
  assert.equal(button(panel, 'Message coordinator').hidden, true);
  assert.equal(byClass(panel, 'hint')[0].hidden, true);
  assert.deepEqual(visibleLinks(panel).map((a) => [a.textContent, a.href]), [['Open session', SESSION]]);
});

// ---- agents query errors: the same table as runs ----

const STOP_AGENTS = ['revoked', 'invalid_argument', 'transform_error', 'not_granted', 'capability_disabled', 'capability_removed'];
for (const code of STOP_AGENTS) {
  test(`agents error ${code} clears the tiles for good and leaves Runs alone`, async () => {
    const page = await gridPage();
    runSnap(page, [runRow()]);
    agentSnap(page, [agent('a1')]);
    assert.equal(agentTiles(page.ids.coords).length, 1);
    page.db.handlers('agents').error({ code, message: 'SECRET' });
    assert.equal(agentTiles(page.ids.coords).length, 0);
    assert.equal(panels(page).length, 1, 'the coordinator panel stays');
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

// ---- tile layout: drag, resize, keyboard, storage ----

const LAYOUT = 'agentDashboard.layout.v1';

// A coordinator plus three running agents (newest first: a1, a2, a3), each
// tile laid out 100 px apart so a pointer position names one tile.
async function layoutPage(opts = {}) {
  const page = await gridPage(opts);
  runSnap(page, [runRow()]);
  agentSnap(page, [agent('a1', { started_at: iso(NOW - MIN) }), agent('a2', { started_at: iso(NOW - 2 * MIN) }),
    agent('a3', { started_at: iso(NOW - 3 * MIN) })]);
  placeTiles(page);
  return page;
}
function placeTiles(page) {
  boards(page)[0].children.forEach((t, i) => {
    t.rect = { left: i * 100, right: i * 100 + 90, top: 0, bottom: 90, width: 90, height: 90 };
  });
}
const order = (page) => boards(page)[0].children.map((t) => t.id);
const gripOf = (t) => byClass(t, 'grip')[0];
const resizeOf = (t) => byClass(t, 'resize')[0];
const tile = (page, id) => boards(page)[0].children.find((t) => t.id === id);
const saved = (storage) => JSON.parse(storage.store[LAYOUT]);

test('dragging a tile by its handle reorders the grid and saves only then', async () => {
  const storage = memStorage();
  const page = await layoutPage({ storage });
  const C = `coord-${RUN}`;
  assert.deepEqual(order(page), [C, 'agent-a1', 'agent-a2', 'agent-a3']);
  assert.equal(storage.store[LAYOUT], undefined, 'loading writes no layout');
  const g = gripOf(tile(page, 'agent-a3'));
  g.dispatch('pointerdown', { button: 0, pointerId: 1, clientX: 345, clientY: 40 });
  page.win('pointermove', { pointerId: 1, clientX: 145, clientY: 40 });
  assert.equal(tile(page, 'agent-a1').dataset.drop, '');
  assert.equal(storage.store[LAYOUT], undefined, 'nothing saved mid-drag');
  page.win('pointerup', { pointerId: 1, clientX: 145, clientY: 40 });
  assert.deepEqual(order(page), [C, 'agent-a3', 'agent-a1', 'agent-a2']);
  assert.equal(tile(page, 'agent-a1').dataset.drop, undefined);
  const tiles = saved(storage).tiles;
  assert.deepEqual([tiles[C].o, tiles.a3.o, tiles.a1.o, tiles.a2.o], [0, 1, 2, 3]);
  assert.equal(saved(storage).v, 1);
  // A reload keeps the order; a cancelled drag changes nothing.
  const again = await layoutPage({ storage });
  assert.deepEqual(order(again), [C, 'agent-a3', 'agent-a1', 'agent-a2']);
  const before = storage.store[LAYOUT];
  const g2 = gripOf(tile(again, 'agent-a2'));
  g2.dispatch('pointerdown', { button: 0, pointerId: 2, clientX: 345, clientY: 40 });
  again.win('pointercancel', { pointerId: 2, clientX: 45, clientY: 40 });
  assert.deepEqual(order(again), [C, 'agent-a3', 'agent-a1', 'agent-a2']);
  assert.equal(storage.store[LAYOUT], before);
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
  assert.deepEqual({ ...saved(storage).tiles[`coord-${RUN}`] }, { c: 1, r: 1 });
});

test('keyboard: Enter picks a tile up, arrows move it; Shift+arrows resize', async () => {
  const storage = memStorage();
  const page = await layoutPage({ storage });
  const C = `coord-${RUN}`;
  const g = gripOf(tile(page, 'agent-a1'));
  g.dispatch('keydown', { key: 'ArrowRight' });
  assert.deepEqual(order(page), [C, 'agent-a1', 'agent-a2', 'agent-a3'], 'arrows do nothing before Enter');
  g.dispatch('keydown', { key: 'Enter' });
  assert.equal(g.getAttribute('aria-pressed'), 'true');
  g.dispatch('keydown', { key: 'ArrowRight' });
  g.dispatch('keydown', { key: 'ArrowDown' });
  assert.deepEqual(order(page), [C, 'agent-a2', 'agent-a3', 'agent-a1']);
  g.dispatch('keydown', { key: 'ArrowRight' });
  assert.deepEqual(order(page), [C, 'agent-a2', 'agent-a3', 'agent-a1'], 'stops at the end');
  g.dispatch('keydown', { key: 'ArrowLeft' });
  assert.deepEqual(order(page), [C, 'agent-a2', 'agent-a1', 'agent-a3']);
  assert.ok(g.focused >= 1, 'focus follows the moved tile');
  assert.match(page.ids['grid-live'].textContent, /^Moved to place 3 of 4\.$/);
  g.dispatch('keydown', { key: 'Enter' });
  assert.equal(g.getAttribute('aria-pressed'), 'false');
  g.dispatch('keydown', { key: 'ArrowLeft' });
  assert.deepEqual(order(page), [C, 'agent-a2', 'agent-a1', 'agent-a3'], 'dropped: arrows do nothing');
  // Escape drops it too.
  g.dispatch('keydown', { key: 'Enter' });
  assert.equal(g.getAttribute('aria-pressed'), 'true');
  g.dispatch('keydown', { key: 'Escape' });
  assert.equal(g.getAttribute('aria-pressed'), 'false');
  g.dispatch('keydown', { key: 'ArrowRight' });
  assert.deepEqual(order(page), [C, 'agent-a2', 'agent-a1', 'agent-a3'], 'after Escape: arrows do nothing');
  assert.equal(saved(storage).tiles.a1.o, 2);

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
  const C = `coord-${RUN}`;
  for (const bad of ['not json', '[]', '{"v":2,"tiles":{"a3":{"o":0},"a1":{"o":5,"c":3}}}', '{"tiles":{"a1":{"c":3}}}',
    '{"v":1,"tiles":[1]}', 'null',
    JSON.stringify({ v: 1, tiles: { a3: { o: 0 } }, pad: 'x'.repeat(70000) })]) {
    const page = await layoutPage({ storage: memStorage({ [LAYOUT]: bad }) });
    assert.deepEqual(order(page), [C, 'agent-a1', 'agent-a2', 'agent-a3'], bad.slice(0, 30));
    assert.deepEqual([panels(page)[0].dataset.cols, panels(page)[0].dataset.rows], ['2', '2']);
    assert.equal(tile(page, 'agent-a1').dataset.cols, '1', bad.slice(0, 30));
  }
  // Out-of-range sizes are clamped; junk entries are dropped.
  const odd = { v: 1, tiles: { a1: { c: 9, r: -4, o: 1e9 }, a2: 'x', a3: { o: 0 }, x: { o: 'soon' } } };
  const page = await layoutPage({ storage: memStorage({ [LAYOUT]: JSON.stringify(odd) }) });
  assert.deepEqual([tile(page, 'agent-a1').dataset.cols, tile(page, 'agent-a1').dataset.rows], ['3', '1']);
  assert.deepEqual(order(page), [C, 'agent-a1', 'agent-a2', 'agent-a3']);
  // 250 entries: at most 200 are read, and the next save keeps only ids
  // that still exist.
  const many = {};
  for (let i = 0; i < 250; i += 1) many[`gone-${i}`] = { o: i };
  many.a3 = { o: 0 };
  const storage = memStorage({ [LAYOUT]: JSON.stringify({ v: 1, tiles: many }) });
  const big = await layoutPage({ storage });
  assert.deepEqual(order(big), [C, 'agent-a1', 'agent-a2', 'agent-a3'], 'entry 251 (a3) was not read');
  const r = resizeOf(tile(big, 'agent-a2'));
  r.dispatch('keydown', { key: 'ArrowRight', shiftKey: true });
  assert.deepEqual(Object.keys(saved(storage).tiles), ['a2']);
});

test('a throwing localStorage still renders, and moves still work in memory', async () => {
  const boom = () => { throw new Error('SecurityError'); };
  const page = await layoutPage({ storage: { getItem: boom, setItem: boom, removeItem: boom } });
  const C = `coord-${RUN}`;
  assert.deepEqual(order(page), [C, 'agent-a1', 'agent-a2', 'agent-a3']);
  const g = gripOf(tile(page, 'agent-a2'));
  g.dispatch('keydown', { key: 'Enter' });
  g.dispatch('keydown', { key: 'ArrowLeft' });
  assert.deepEqual(order(page), [C, 'agent-a2', 'agent-a1', 'agent-a3']);
  page.ids['grid-reset'].click();
  assert.deepEqual(order(page), [C, 'agent-a1', 'agent-a2', 'agent-a3']);
});

test('narrow mode (phone or coarse pointer): no handles, no drag, no keyboard moves', async () => {
  const storage = memStorage();
  const page = await layoutPage({ storage, narrow: true });
  const C = `coord-${RUN}`;
  const a3 = tile(page, 'agent-a3');
  assert.equal(gripOf(a3).hidden, true);
  assert.equal(resizeOf(a3).hidden, true);
  assert.equal(page.ids['grid-reset'].hidden, true);
  const g = gripOf(a3);
  g.dispatch('pointerdown', { button: 0, pointerId: 1, clientX: 345, clientY: 40 });
  page.win('pointerup', { pointerId: 1, clientX: 145, clientY: 40 });
  g.dispatch('keydown', { key: 'Enter' });
  g.dispatch('keydown', { key: 'ArrowLeft' });
  resizeOf(a3).dispatch('keydown', { key: 'ArrowRight', shiftKey: true });
  assert.deepEqual(order(page), [C, 'agent-a1', 'agent-a2', 'agent-a3']);
  assert.equal(a3.dataset.cols, '1');
  assert.equal(storage.store[LAYOUT], undefined);
  // A wide screen with a fine pointer keeps the handles.
  const wide = await layoutPage({ narrow: false });
  assert.equal(gripOf(tile(wide, 'agent-a3')).hidden, false);
});

test('Reset layout clears the saved layout and restores the default order and sizes', async () => {
  const storage = memStorage();
  const page = await layoutPage({ storage });
  const C = `coord-${RUN}`;
  const g = gripOf(tile(page, 'agent-a3'));
  g.dispatch('pointerdown', { button: 0, pointerId: 1, clientX: 345, clientY: 40 });
  page.win('pointerup', { pointerId: 1, clientX: 45, clientY: 40 });
  resizeOf(tile(page, 'agent-a1')).dispatch('keydown', { key: 'ArrowRight', shiftKey: true });
  assert.notDeepEqual(order(page), [C, 'agent-a1', 'agent-a2', 'agent-a3']);
  assert.ok(storage.store[LAYOUT]);
  page.ids['grid-reset'].click();
  assert.equal(storage.store[LAYOUT], undefined);
  assert.deepEqual(order(page), [C, 'agent-a1', 'agent-a2', 'agent-a3']);
  assert.equal(tile(page, 'agent-a1').dataset.cols, '1');
  assert.equal(page.ids['grid-live'].textContent, 'Layout reset.');
});

// ---- view memory ----

test('the chosen view persists and is restored; only the view and layout keys are stored', async () => {
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

test('a saved layout never holds more than 200 tiles, even with more on screen', async () => {
  const storage = memStorage();
  const page = await gridPage({ storage });
  runSnap(page, [runRow()]);
  const many = [];
  for (let i = 0; i < 210; i += 1) many.push(agent(`m${String(i).padStart(3, '0')}`, { started_at: iso(NOW - (i + 1) * 1000) }));
  agentSnap(page, many);
  assert.equal(boards(page)[0].children.length, 211);
  const g = gripOf(tile(page, 'agent-m209'));
  g.dispatch('keydown', { key: 'Enter' });
  g.dispatch('keydown', { key: 'ArrowLeft' });
  const kept = Object.keys(saved(storage).tiles);
  assert.equal(kept.length, 200);
  assert.equal(kept[0], `coord-${RUN}`, 'tiles on screen are kept first, in order');
});

// ---- a live snapshot during a drag or resize (judge round 1, finding 1) ----

test('an agent arriving mid-drag neither re-parents tiles nor leaves the drag stuck', async () => {
  const storage = memStorage();
  const page = await layoutPage({ storage });
  const C = `coord-${RUN}`;
  const before = [...boards(page)[0].children];
  const g = gripOf(tile(page, 'agent-a3'));
  g.dispatch('pointerdown', { button: 0, pointerId: 7, clientX: 345, clientY: 40 });
  assert.equal(page.winListeners.pointerup.length, 1, 'the end of the drag is heard on the window');
  // The master writes a new running agent: it sorts first by default.
  agentSnap(page, [agent('a0', { started_at: iso(NOW - 10e3) }), agent('a1', { started_at: iso(NOW - MIN) }),
    agent('a2', { started_at: iso(NOW - 2 * MIN) }), agent('a3', { started_at: iso(NOW - 3 * MIN) })]);
  assert.deepEqual(boards(page)[0].children, before, 'no tile is detached while the pointer is captured');
  page.win('pointermove', { pointerId: 7, clientX: 145, clientY: 40 });
  page.win('pointerup', { pointerId: 7, clientX: 145, clientY: 40 });
  assert.equal(boards(page)[0].find((e) => 'dragging' in e.dataset).length, 0);
  assert.equal(page.winListeners.pointerup.length, 0, 'window listeners are removed');
  assert.deepEqual(order(page), [C, 'agent-a0', 'agent-a3', 'agent-a1', 'agent-a2'], 'the drop lands and the new tile shows');
  assert.equal(saved(storage).tiles.a3.o, 2);
  // The next drag and resize still work.
  placeTiles(page);
  const g2 = gripOf(tile(page, 'agent-a2'));
  g2.dispatch('pointerdown', { button: 0, pointerId: 8, clientX: 445, clientY: 40 });
  page.win('pointerup', { pointerId: 8, clientX: 145, clientY: 40 });
  assert.deepEqual(order(page), [C, 'agent-a2', 'agent-a0', 'agent-a3', 'agent-a1']);
  const r = resizeOf(tile(page, 'agent-a1'));
  r.dispatch('pointerdown', { button: 0, pointerId: 9, clientX: 490, clientY: 90 });
  page.win('pointerup', { pointerId: 9, clientX: 690, clientY: 90 });
  assert.equal(tile(page, 'agent-a1').dataset.cols, '3');
  // A cancelled drag still lets a tile that arrived meanwhile show.
  gripOf(tile(page, 'agent-a0')).dispatch('pointerdown', { button: 0, pointerId: 10, clientX: 245, clientY: 40 });
  agentSnap(page, [agent('a9', { started_at: iso(NOW - 5e3) }), agent('a0', { started_at: iso(NOW - 10e3) }),
    agent('a1', { started_at: iso(NOW - MIN) }), agent('a2', { started_at: iso(NOW - 2 * MIN) }),
    agent('a3', { started_at: iso(NOW - 3 * MIN) })]);
  assert.equal(order(page).length, 5, 'held back during the drag');
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
  agentSnap(page, [agent('a0', { started_at: iso(NOW - 10e3) }), agent('a1', { started_at: iso(NOW - MIN) }),
    agent('a2', { started_at: iso(NOW - 2 * MIN) }), agent('a3', { started_at: iso(NOW - 3 * MIN) })]);
  assert.deepEqual([a1.dataset.cols, a1.dataset.rows], ['3', '2'], 'the re-render keeps the preview');
  page.win('pointerup', { pointerId: 3, clientX: 390, clientY: 190 });
  assert.deepEqual({ ...saved(storage).tiles.a1 }, { c: 3, r: 2 });
  assert.equal(order(page).length, 5, 'the new tile shows once the resize ends');
  assert.equal(page.winListeners.pointerup.length, 0);
});

test('a tile that disappears mid-drag or mid-resize cancels it; nothing is stuck', async () => {
  const storage = memStorage();
  const page = await layoutPage({ storage });
  const C = `coord-${RUN}`;
  gripOf(tile(page, 'agent-a3')).dispatch('pointerdown', { button: 0, pointerId: 1, clientX: 345, clientY: 40 });
  agentSnap(page, [agent('a1', { started_at: iso(NOW - MIN) }), agent('a2', { started_at: iso(NOW - 2 * MIN) })]);
  assert.deepEqual(order(page), [C, 'agent-a1', 'agent-a2'], 'the cancelled drag lets the grid update at once');
  assert.equal(page.winListeners.pointerup.length, 0);
  page.win('pointerup', { pointerId: 1, clientX: 145, clientY: 40 });
  resizeOf(tile(page, 'agent-a2')).dispatch('pointerdown', { button: 0, pointerId: 2, clientX: 290, clientY: 90 });
  agentSnap(page, [agent('a1', { started_at: iso(NOW - MIN) })]);
  assert.deepEqual(order(page), [C, 'agent-a1']);
  assert.equal(storage.store[LAYOUT], undefined, 'nothing was saved');
  placeTiles(page);
  gripOf(tile(page, 'agent-a1')).dispatch('pointerdown', { button: 0, pointerId: 3, clientX: 145, clientY: 40 });
  page.win('pointerup', { pointerId: 3, clientX: 45, clientY: 40 });
  assert.deepEqual(order(page), ['agent-a1', C], 'a new drag works');
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

test('"N agents hidden" counts only live agents whose run has no row', async () => {
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
  assert.equal(page.ids['grid-hidden'].textContent, '2 agents hidden');
});
