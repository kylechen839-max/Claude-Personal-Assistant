// node --test tests/js/agent-dashboard.test.mjs  (Node 18+; no packages needed)
//
// Runs the dev-team dashboard page's own <script> (tools/agent-dashboard/
// index.html) in node:vm with a minimal fake document, a fake
// window.claude.use("db") and fake timers, then checks what it decides and
// renders: silent/running/needs at the time limits, safe links, row text
// that stays text, and each db error code -> stop, back off or retry.
// The Grid view and the chat panel run against a fake window.claude.use("mcp")
// (watchTool/callTool/invalidate) built on real list_sessions and
// list_events shapes with made-up values.

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

function fakeDb() {
  const calls = { subs: 0, unsubs: 0, query: null };
  let handlers = null;
  const query = {
    onSnapshot(next, error) {
      calls.subs += 1;
      if (calls.throwOnSubscribe) throw calls.throwOnSubscribe;
      handlers = { next, error };
      return () => { calls.unsubs += 1; };
    },
  };
  const db = {
    collection: (name) => ({
      orderBy: (field, dir) => ({
        limit: (n) => { calls.query = [name, field, dir, n]; return query; },
      }),
    }),
  };
  return { db, calls, handlers: () => handlers };
}

// The mcp namespace: watchTool keeps each handler and returns a counted,
// idempotent unsubscribe; callTool and invalidate record their arguments
// and answer from a queue (default: a pending promise).
function fakeMcp() {
  const calls = { watch: [], call: [], invalidate: [], listTools: 0 };
  const replies = [];
  const mcp = {
    watchTool(server, tool, input, handler, options) {
      const w = { server, tool, input, handler, options, unsubs: 0 };
      calls.watch.push(w);
      return () => { w.unsubs += 1; };
    },
    callTool(server, tool, input, options) {
      const c = { server, tool, input, options };
      calls.call.push(c);
      const next = replies.shift();
      return next ? next(c) : new Promise((resolve, reject) => { c.resolve = resolve; c.reject = reject; });
    },
    invalidate(...args) { calls.invalidate.push(args); return calls.invalidateRejects ? Promise.reject({ code: 'bad_request' }) : Promise.resolve(); },
    listTools: async () => { calls.listTools += 1; return { servers: [] }; },
  };
  const live = (tool) => calls.watch.filter((w) => w.tool === tool && w.unsubs === 0);
  return { mcp, calls, replies, live };
}

const PAGE_IDS = {
  summary: 'p', conn: 'p', runs: 'ul', empty: 'p',
  main: 'main', 'runs-view': 'section', 'grid-view': 'section', 'view-runs': 'button', 'view-grid': 'button',
  'grid-summary': 'p', 'grid-conn': 'p', grid: 'ul', 'grid-hidden': 'p', 'grid-action': 'span', 'grid-refresh': 'button',
  chat: 'aside', 'chat-title': 'h2', 'chat-open': 'a', 'chat-close': 'button', 'chat-conn': 'p', 'chat-log': 'ol',
  'chat-input': 'textarea', 'chat-count': 'span', 'chat-action': 'span', 'chat-send': 'button', 'chat-status': 'p',
};

async function loadPage({ db = fakeDb(), withClaude = true, mcp = fakeMcp(), storage = undefined, hash = undefined } = {}) {
  const ids = {};
  for (const [id, tag] of Object.entries(PAGE_IDS)) ids[id] = new FakeEl(tag);
  ids['grid-view'].hidden = true;
  ids.chat.hidden = true;
  const timers = [];
  const intervals = [];
  const document = {
    hidden: false,
    getElementById: (id) => ids[id] ?? null,
    createElement: (tag) => new FakeEl(tag),
    addEventListener: () => {},
    // Markup sinks a regression might reach for. They "parse" tags into
    // child elements, so a test sees markup become elements, not a crash.
    createRange: () => ({ createContextualFragment: parseTags }),
  };
  const sandbox = {
    document,
    console,
    setTimeout: (fn, ms) => { timers.push({ fn, ms, cleared: false }); return timers.length; },
    clearTimeout: (id) => { if (id && timers[id - 1]) timers[id - 1].cleared = true; },
    setInterval: (fn, ms) => { intervals.push({ fn, ms }); return intervals.length; },
    DOMParser: class { parseFromString(html) { return { body: parseTags(html) }; } },
  };
  const uses = [];
  if (withClaude) {
    sandbox.claude = {
      use: async (cap) => { uses.push(cap); return cap === 'db' ? db.db : cap === 'mcp' ? (mcp && mcp.mcp) : null; },
    };
  }
  if (storage !== undefined) sandbox.localStorage = storage;
  if (hash !== undefined) sandbox.location = { hash };
  sandbox.window = sandbox;
  const ctx = vm.createContext(sandbox);
  vm.runInContext(`Date.now = () => ${NOW};`, ctx);
  vm.runInContext(SCRIPT, ctx, { filename: 'index.html' });
  await new Promise((r) => setImmediate(r));
  const pending = () => timers.filter((t) => !t.cleared);
  return { api: sandbox.AgentDashboard, ids, db, mcp, uses, timers, pending, intervals };
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

// ==== Grid view (list_sessions through the viewer's connector) ====

const X24 = 'x'.repeat(24);
const sid = (n) => `session_${X24.slice(String(n).length)}${n}`;
const micro = (t) => new Date(t).toISOString().replace(/\.(\d{3})Z$/, '.$1123Z');  // 6 fractional digits

// One session in the real list_sessions shape, made-up values.
function session(n, { bucket = 'SESSION_STATUS_BUCKET_WORKING', status = 'SESSION_STATUS_RUNNING', ageMin = 1,
  title = `Session ${n}`, needs = '', category = 'completed', kind = 'anthropic_cloud', id = sid(n),
  detail = `Detail ${n}` } = {}) {
  return {
    id, title, session_status: status,
    created_at: micro(NOW - (ageMin + 60) * MIN), updated_at: micro(NOW - ageMin * MIN),
    environment_id: 'env_madeup', origin: 'web', connection_status: 'connected', unread: false,
    post_turn_summary: { status_category: category, status_detail: detail, recent_action: `Action ${n}` },
    task_summary: `Task ${n}`,
    external_metadata: {
      post_turn_summary: { needs_action: needs, recent_action: `Action ${n}`, status_category: category, status_detail: detail },
      task_summary: `Task ${n}`,
    },
    environment_kind: kind, status_bucket: bucket,
  };
}
const wrap = (list) => ({ ccr: { data: list, first_id: list[0]?.id ?? null, last_id: list.at(-1)?.id ?? null } });
const data = (list, extra = {}) => ({ type: 'data', result: { content: [], payload: wrap(list), ...extra } });

async function gridPage(opts = {}) {
  const page = await loadPage(opts);
  page.ids['view-grid'].click();
  await settle();
  return page;
}
const tiles = (page) => page.ids.grid.children;
const titlesOf = (page) => tiles(page).map((li) => li.find((e) => e.tagName === 'H2')[0].textContent);
const sessionsWatch = (page) => page.mcp.calls.watch.filter((w) => w.tool === 'list_sessions');

test('Runs is the default: no mcp lookup, no watch, no call, no timers', async () => {
  const page = await loadPage();
  assert.equal(page.ids.main.dataset.view, 'runs');
  assert.equal(page.ids['grid-view'].hidden, true);
  assert.equal(page.ids['runs-view'].hidden, false);
  assert.equal(page.ids['view-runs'].getAttribute('aria-pressed'), 'true');
  assert.equal(page.ids['view-grid'].getAttribute('aria-pressed'), 'false');
  assert.deepEqual(page.uses, ['db']);
  assert.equal(page.mcp.calls.watch.length, 0);
  assert.equal(page.mcp.calls.call.length, 0);
  assert.equal(page.pending().length, 0);
  // Runs still renders exactly as before.
  page.db.handlers().next(snap([{ feature: 'a', phase: 'building', updated_at: iso(NOW - MIN) }]));
  assert.equal(page.ids.summary.textContent, 'Nothing needs you · 1 running');
});

test('without window.claude the grid says it needs claude.ai and starts nothing', async () => {
  const page = await gridPage({ withClaude: false });
  assert.equal(page.ids['grid-summary'].textContent, 'The grid needs this page open on claude.ai');
  assert.equal(page.mcp.calls.watch.length, 0);
  assert.equal(page.intervals.length, 0);
  assert.equal(page.pending().length, 0);
});

test('Grid starts one watch with the brief arguments; Runs unsubscribes it', async () => {
  const page = await gridPage();
  assert.equal(page.ids.main.dataset.view, 'grid');
  assert.equal(page.ids['grid-view'].hidden, false);
  assert.equal(page.ids['runs-view'].hidden, true);
  assert.equal(page.ids['view-grid'].getAttribute('aria-pressed'), 'true');
  const [w] = page.mcp.calls.watch;
  assert.equal(page.mcp.calls.watch.length, 1);
  assert.deepEqual([w.server, w.tool, { ...w.input }], ['Claude Code Remote', 'list_sessions', { limit: 50 }]);
  assert.equal(w.options.refetchInterval, 30000);
  assert.deepEqual({ ...w.options.cache }, { staleTime: 15000, gcTime: 300000 });
  page.ids['view-grid'].click();
  await settle();
  assert.equal(page.mcp.calls.watch.length, 1, 'a second Grid click does not stack watches');
  page.ids['view-runs'].click();
  assert.equal(w.unsubs, 1);
  page.ids['view-grid'].click();
  await settle();
  assert.equal(page.mcp.calls.watch.length, 2, 'coming back watches again');
  assert.equal(page.pending().length, 0);
  assert.equal(page.intervals.length, 1, 'only the Runs re-render interval');
});

test('every bucket maps to a state; lower-case and unknown forms work', async () => {
  const { api } = await loadPage();
  const c = (o) => api.classifySession(session(1, o), NOW);
  const cases = [
    [{ bucket: 'SESSION_STATUS_BUCKET_WORKING' }, 'working', 'working'],
    [{ bucket: 'session_status_bucket_working' }, 'working', 'working'],
    [{ bucket: 'SESSION_STATUS_BUCKET_BLOCKED' }, 'needs', 'needs you'],
    [{ bucket: 'SESSION_STATUS_BUCKET_REVIEW_READY', status: 'SESSION_STATUS_IDLE' }, 'idle', 'review ready'],
    [{ bucket: 'review_ready', status: 'SESSION_STATUS_IDLE' }, 'idle', 'review ready'],
    [{ bucket: 'SESSION_STATUS_BUCKET_COMPLETED', status: 'SESSION_STATUS_IDLE' }, 'done', 'done'],
    [{ bucket: 'SESSION_STATUS_BUCKET_FAILED', status: 'SESSION_STATUS_IDLE' }, 'failed', 'failed'],
    [{ bucket: 'SESSION_STATUS_BUCKET_SOMETHING_NEW' }, 'working', 'working'],  // unknown + RUNNING
    [{ bucket: 'SESSION_STATUS_BUCKET_SOMETHING_NEW', status: 'SESSION_STATUS_IDLE' }, 'idle', 'idle'],
    [{ bucket: 'SESSION_STATUS_BUCKET_COMPLETED', status: 'SESSION_STATUS_ARCHIVED' }, 'archived', 'archived'],
    [{ bucket: 'SESSION_STATUS_BUCKET_COMPLETED', needs: 'Approve the push' }, 'needs', 'needs you'],
    [{ bucket: 'SESSION_STATUS_BUCKET_REVIEW_READY', category: 'need_input' }, 'needs', 'needs you'],
  ];
  for (const [o, state, pill] of cases) {
    const r = c(o);
    assert.deepEqual([r.state, r.pill], [state, pill], JSON.stringify(o));
  }
  assert.equal(c({ bucket: 'SESSION_STATUS_BUCKET_SOMETHING_NEW', status: 'SESSION_STATUS_IDLE' }).note, 'unknown status');
  // needs_action only under external_metadata (the real shape) still counts.
  const s = session(1, { bucket: 'SESSION_STATUS_BUCKET_COMPLETED' });
  s.external_metadata.post_turn_summary.needs_action = 'Answer the question';
  assert.equal(api.classifySession(s, NOW).state, 'needs');
  // Odd values never throw.
  for (const odd of [null, undefined, 'x', 42, [], { status_bucket: 7 }]) {
    assert.equal(typeof api.classifySession(odd, NOW).state, 'string');
  }
  // Microsecond timestamps parse.
  assert.equal(api.isoTime('2026-10-19T11:59:00.123456Z'), NOW - MIN + 123);
});

test('normalizeSessions accepts the ccr wrapper, data, a bare array and JSON text', async () => {
  const { api } = await loadPage();
  const list = [session(1), session(2)];
  for (const p of [wrap(list), { data: list }, list, JSON.stringify(wrap(list))]) {
    assert.deepEqual([...api.normalizeSessions(p).map((s) => s.id)], [sid(1), sid(2)]);
  }
  for (const bad of [null, undefined, 42, '{not json', { ccr: {} }, { data: 'x' }]) {
    assert.equal(api.normalizeSessions(bad), null, String(bad));
  }
  assert.deepEqual([...api.normalizeSessions([null, 'x', [], session(3)]).map((s) => s.id)], [sid(3)]);
});

test('tiles sort needs, failed, working, idle, done; archived and old hidden with counts', async () => {
  const page = await gridPage();
  const [w] = page.mcp.calls.watch;
  w.handler(data([
    session(1, { bucket: 'SESSION_STATUS_BUCKET_COMPLETED', status: 'SESSION_STATUS_IDLE', title: 'done' }),
    session(2, { bucket: 'SESSION_STATUS_BUCKET_WORKING', title: 'working-old', ageMin: 30 }),
    session(3, { bucket: 'SESSION_STATUS_BUCKET_WORKING', title: 'working-new', ageMin: 2 }),
    session(4, { bucket: 'SESSION_STATUS_BUCKET_FAILED', status: 'SESSION_STATUS_IDLE', title: 'failed' }),
    session(5, { bucket: 'SESSION_STATUS_BUCKET_BLOCKED', title: 'blocked' }),
    session(6, { bucket: 'SESSION_STATUS_BUCKET_REVIEW_READY', status: 'SESSION_STATUS_IDLE', title: 'review' }),
    session(7, { status: 'SESSION_STATUS_ARCHIVED', bucket: 'SESSION_STATUS_BUCKET_COMPLETED', title: 'archived' }),
    session(8, { bucket: 'SESSION_STATUS_BUCKET_COMPLETED', status: 'SESSION_STATUS_IDLE', ageMin: 8 * 24 * 60, title: 'old' }),
    session(9, { bucket: 'SESSION_STATUS_BUCKET_WORKING', ageMin: 8 * 24 * 60, title: 'old-working' }),
  ]));
  assert.deepEqual(titlesOf(page), ['blocked', 'failed', 'working-new', 'working-old', 'old-working', 'review', 'done']);
  assert.deepEqual(tiles(page).map((li) => li.dataset.state), ['needs', 'silent', 'running', 'running', 'running', 'done', 'done']);
  assert.equal(page.ids['grid-summary'].textContent, '1 needs you · 1 failed · 3 working · 1 idle · 1 done');
  assert.equal(page.ids['grid-hidden'].textContent, '1 archived hidden · 1 older hidden');
  assert.equal(page.ids['grid-hidden'].hidden, false);
  assert.equal(page.ids['grid-conn'].textContent, 'live · updated just now');
});

test('the status line falls back detail -> recent action -> task summary; local label', async () => {
  const page = await gridPage();
  const a = session(1, { kind: 'bridge' });
  const b = session(2, { detail: '' });
  b.external_metadata.post_turn_summary.status_detail = '';
  const c = session(3, { detail: '' });
  c.external_metadata.post_turn_summary.status_detail = '';
  c.post_turn_summary.recent_action = '';
  c.external_metadata.post_turn_summary.recent_action = '';
  c.post_turn_summary.status_detail = 'y'.repeat(500);
  page.mcp.calls.watch[0].handler(data([a, b, session(4, { detail: '' })]));
  const statusOf = (li) => li.find((e) => e.className === 'status')[0]?.textContent;
  const byTitle = Object.fromEntries(tiles(page).map((li) => [li.find((e) => e.tagName === 'H2')[0].textContent, li]));
  assert.equal(statusOf(byTitle['Session 1']), 'Detail 1');
  assert.equal(statusOf(byTitle['Session 2']), 'Action 2');
  assert.equal(byTitle['Session 1'].find((e) => e.className === 'local').length, 1);
  assert.equal(byTitle['Session 2'].find((e) => e.className === 'local').length, 0);
  page.mcp.calls.watch[0].handler(data([c]));
  assert.ok(statusOf(tiles(page)[0]).length <= 160, 'clamped');
});

test('session text stays text; a bad id gets no link and no Chat button', async () => {
  const page = await gridPage();
  const markup = '<img src=x onerror=alert(1)><b>hi</b>';
  page.mcp.calls.watch[0].handler(data([
    session(1, { title: markup, detail: '<script>x</script>', needs: '<a href="https://evil.example">x</a>' }),
    session(2, { id: 'session_x/../../evil' }),
    session(3, { id: `https://evil.example/?session_${X24}` }),
  ]));
  const [first, ...bad] = tiles(page);
  assert.equal(first.find((e) => e.tagName === 'H2')[0].textContent, markup);
  assert.equal(first.find((e) => ['IMG', 'B', 'SCRIPT'].includes(e.tagName)).length, 0);
  const [a] = first.find((e) => e.tagName === 'A');
  assert.deepEqual([a.textContent, a.href, a.target, a.rel],
    ['Open in claude.ai', `https://claude.ai/code/${sid(1)}`, '_blank', 'noopener noreferrer']);
  for (const li of bad) {
    assert.equal(li.find((e) => e.tagName === 'A').length, 0);
    assert.equal(li.find((e) => e.tagName === 'BUTTON').length, 0);
  }
});

test('updated time comes from cache.storedAt when present', async () => {
  const page = await gridPage();
  page.mcp.calls.watch[0].handler(data([session(1)], { cache: { storedAt: NOW - 5 * MIN, revalidating: true } }));
  assert.equal(page.ids['grid-conn'].textContent, 'live · updated 5 min ago');
});

// ---- grid error codes ----

const RETRACT = {
  needs_reauth: 'Reconnect Claude Code Remote in claude.ai Settings → Connectors',
  server_not_connected: 'Add Claude Code Remote in claude.ai Settings → Connectors',
  selection_required: 'Add Claude Code Remote in claude.ai Settings → Connectors',
  server_not_found: 'Add Claude Code Remote in claude.ai Settings → Connectors',
  not_in_manifest: 'Claude Code Remote is not allowed for this view.',
  blocked_by_policy: 'Claude Code Remote is not allowed for this view.',
  consent_required: 'Claude Code Remote is not allowed for this view.',
};
for (const [code, copy] of Object.entries(RETRACT)) {
  test(`grid ${code} retracts tiles, keeps the watch, never shows the message`, async () => {
    const page = await gridPage();
    const [w] = page.mcp.calls.watch;
    w.handler(data([session(1)]));
    w.handler({ type: 'error', error: { code, message: 'SECRET-DETAIL' } });
    assert.equal(tiles(page).length, 0);
    assert.equal(page.ids['grid-summary'].textContent, copy);
    assert.ok(!JSON.stringify(page.ids['grid-view']).includes('SECRET'));
    assert.equal(w.unsubs, 0);
    assert.equal(page.mcp.calls.call.length, 0, 'no re-ask');
    assert.equal(page.pending().length, 0);
    w.handler(data([session(2)]));
    assert.equal(tiles(page).length, 1, 'a later delivery recovers');
  });
}

for (const [code, summary] of [['not_granted', 'The grid needs this page open on claude.ai'],
  ['capability_disabled', 'The grid needs this page open on claude.ai'],
  ['capability_removed', 'The grid needs this page open on claude.ai'],
  ['bad_request', "Can't load sessions"], ['transform_error', "Can't load sessions"],
  ['user_changed', '']]) {
  test(`grid ${code} unsubscribes for good and starts nothing new`, async () => {
    const page = await gridPage();
    const [w] = page.mcp.calls.watch;
    w.handler(data([session(1)]));
    w.handler({ type: 'error', error: { code } });
    assert.equal(w.unsubs, 1);
    assert.equal(tiles(page).length, 0);
    assert.equal(page.ids['grid-summary'].textContent, summary);
    w.handler(data([session(2)]));
    assert.equal(tiles(page).length, 0, 'nothing renders after the end');
    page.ids['view-runs'].click();
    page.ids['view-grid'].click();
    await settle();
    assert.equal(page.mcp.calls.watch.length, 1, 'no new watch');
    assert.equal(page.pending().length, 0);
  });
}

for (const code of ['server_unavailable', 'rate_limited', 'upstream_error', 'tool_error', 'brand_new_code']) {
  test(`grid ${code} keeps the last tiles with a stale note and no timer`, async () => {
    const page = await gridPage();
    const [w] = page.mcp.calls.watch;
    w.handler(data([session(1)]));
    w.handler({ type: 'error', error: { code, message: 'SECRET-DETAIL', retryable: true } });
    assert.equal(tiles(page).length, 1);
    const shown = code === 'brand_new_code' ? 'upstream_error' : code;
    assert.match(page.ids['grid-conn'].textContent, new RegExp(`^Stale \\(${shown}\\)`));
    assert.ok(!page.ids['grid-conn'].textContent.includes('SECRET'));
    assert.equal(page.ids['grid-conn'].dataset.level, 'bad');
    assert.equal(w.unsubs, 0);
    assert.equal(page.pending().length, 0);
    assert.equal(page.mcp.calls.call.length, 0, 'no manual retry');
    w.handler(data([session(1), session(2)]));
    assert.equal(page.ids['grid-conn'].textContent, 'live · updated just now');
  });
}

test('grid cancelled is ignored', async () => {
  const page = await gridPage();
  const [w] = page.mcp.calls.watch;
  w.handler(data([session(1)]));
  w.handler({ type: 'error', error: { code: 'cancelled' } });
  assert.equal(tiles(page).length, 1);
  assert.equal(page.ids['grid-conn'].textContent, 'live · updated just now');
});

test('grid approval_required: retract, then one callTool per button click', async () => {
  const page = await gridPage();
  const [w] = page.mcp.calls.watch;
  w.handler(data([session(1)]));
  w.handler({ type: 'error', error: { code: 'approval_required' } });
  assert.equal(tiles(page).length, 0);
  const [button] = page.ids['grid-action'].children;
  assert.equal(button.tagName, 'BUTTON');
  assert.equal(page.mcp.calls.call.length, 0, 'nothing is called until the click');
  button.click();
  button.click();  // a double click while pending is one call
  await settle();
  assert.equal(page.mcp.calls.call.length, 1);
  const [c] = page.mcp.calls.call;
  assert.deepEqual([c.server, c.tool, { ...c.input }], ['Claude Code Remote', 'list_sessions', { limit: 50 }]);
  c.resolve({ content: [], payload: wrap([session(5)]) });
  await settle();
  assert.equal(tiles(page).length, 1);
  assert.equal(w.unsubs, 0, 'the watch keeps running');
});

test('Refresh invalidates the list once; a rejection is harmless', async () => {
  const page = await gridPage();
  page.mcp.calls.invalidateRejects = true;
  page.ids['grid-refresh'].click();
  await settle();
  assert.deepEqual(page.mcp.calls.invalidate.map((a) => [a[0], a[1]]), [['Claude Code Remote', 'list_sessions']]);
});

// ---- view memory ----

function memStorage(initial = {}) {
  const store = { ...initial };
  return { store, getItem: (k) => (k in store ? store[k] : null), setItem: (k, v) => { store[k] = String(v); } };
}

test('the chosen view persists and is restored', async () => {
  const storage = memStorage();
  const page = await loadPage({ storage });
  assert.equal(Object.keys(storage.store).length, 0, 'loading writes nothing');
  page.ids['view-grid'].click();
  assert.equal(storage.store['agent-dashboard-view'], 'grid');
  const again = await loadPage({ storage });
  await settle();
  assert.equal(again.ids.main.dataset.view, 'grid');
  assert.equal(again.mcp.calls.watch.length, 1);
  const hashed = await loadPage({ storage, hash: '#runs' });
  assert.equal(hashed.ids.main.dataset.view, 'runs');
  const hashGrid = await loadPage({ hash: '#grid' });
  assert.equal(hashGrid.ids.main.dataset.view, 'grid');
});

test('a throwing localStorage is tolerated', async () => {
  const boom = () => { throw new Error('SecurityError'); };
  const page = await loadPage({ storage: { getItem: boom, setItem: boom } });
  assert.equal(page.ids.main.dataset.view, 'runs');
  page.ids['view-grid'].click();
  await settle();
  assert.equal(page.ids.main.dataset.view, 'grid');
  assert.equal(page.mcp.calls.watch.length, 1);
});

// ==== chat panel (list_events + send_message) ====

// list_events in the real shape: {created_at, user|assistant: {uuid, internal_anthropic_catchall: {...}}}.
let uuidN = 0;
const uuid = () => `00000000-0000-4000-8000-${String(++uuidN).padStart(12, '0')}`;
function userEvent(content, extra = {}) {
  return { created_at: micro(NOW - MIN), user: { uuid: uuid(), internal_anthropic_catchall: {
    message: { role: 'user', content }, parent_tool_use_id: null, session_id: 'madeup', timestamp: micro(NOW - MIN), ...extra } } };
}
function assistantEvent(blocks, extra = {}) {
  return { created_at: micro(NOW - MIN), assistant: { uuid: uuid(), internal_anthropic_catchall: {
    message: { id: 'msg_madeup', role: 'assistant', type: 'message', content: blocks }, parent_tool_use_id: null,
    request_id: 'req_madeup', session_id: 'madeup', timestamp: micro(NOW - MIN), ...extra } } };
}
const eventsData = (list) => ({ type: 'data', result: { content: [], payload: { ccr: { data: list, has_more: false } } } });

test('parseEvents keeps user and agent text only', async () => {
  const { api } = await loadPage();
  const markup = '<img src=x onerror=alert(1)>';
  const list = [
    userEvent('Please fix the build'),
    userEvent([{ type: 'text', text: 'With a block' }, { type: 'image', source: {} }]),
    userEvent([{ type: 'tool_result', tool_use_id: 'toolu_madeup', content: 'tool output' }]),
    userEvent([{ type: 'tool_result', tool_use_id: 'toolu_madeup', content: 'x' }, { type: 'text', text: 'hook note' }]),
    userEvent('A synthetic nudge', { isSynthetic: true }),
    userEvent('Meta', { isMeta: true }),
    assistantEvent([{ type: 'thinking', thinking: 'hmm' }]),
    assistantEvent([{ type: 'tool_use', id: 'toolu_madeup', name: 'Bash', input: { command: 'ls' } }]),
    assistantEvent([{ type: 'text', text: `Done. ${markup}` }, { type: 'tool_use', id: 't', name: 'x', input: {} }]),
    assistantEvent([{ type: 'text', text: 'Subagent says hi' }], { parent_tool_use_id: 'toolu_madeup' }),
    { created_at: micro(NOW), result: { uuid: uuid(), internal_anthropic_catchall: { result: 'ok', subtype: 'success' } } },
    null, 'x', [],
  ];
  for (const p of [{ ccr: { data: list } }, { data: list }, list, JSON.stringify({ ccr: { data: list } })]) {
    assert.deepEqual([...api.parseEvents(p)].map((m) => [m.who, m.text]), [
      ['you', 'Please fix the build'], ['you', 'With a block'], ['agent', `Done. ${markup}`],
    ]);
  }
  assert.equal(api.parseEvents('{nope'), null);
  assert.equal(api.parseEvents({}), null);
});

test('background task notifications and replayed turns are not shown as you', async () => {
  const { api } = await loadPage();
  // Real shape: the notification arrives as a user turn whose catchall has
  // origin.kind "task-notification" and none of the synthetic/meta flags.
  const note = userEvent('<task-notification>\n<task-id>made-up</task-id>\n<status>completed</status>\n</task-notification>',
    { origin: { kind: 'task-notification' } });
  const list = [
    userEvent('Typed by me'),
    note,
    userEvent('<!-- a replayed turn -->', { origin: { kind: 'task-notification' }, isReplay: true }),
    userEvent('Replayed text', { isReplay: true }),
    userEvent('Other machine origin', { origin: { kind: 'peer' } }),
    userEvent('Also typed by me', { origin: { kind: 'human' } }),
    assistantEvent([{ type: 'text', text: 'On it' }]),
  ];
  assert.deepEqual([...api.parseEvents(list)].map((m) => [m.who, m.text]), [
    ['you', 'Typed by me'], ['you', 'Also typed by me'], ['agent', 'On it'],
  ]);
});

test('the same event delivered twice shows once; equal text with its own uuid shows twice', async () => {
  const { api } = await loadPage();
  const a = userEvent('Same words');
  const b = userEvent('Same words');
  assert.deepEqual([...api.parseEvents([a, a, b])].map((m) => m.text), ['Same words', 'Same words']);
  assert.equal(api.parseEvents([a, JSON.parse(JSON.stringify(a))]).length, 1);
});

async function chatPage(opts = {}) {
  const page = await gridPage(opts);
  page.mcp.calls.watch[0].handler(data([session(1, { title: 'Fix the build' })]));
  const chat = tiles(page)[0].find((e) => e.tagName === 'BUTTON' && e.textContent === 'Chat')[0];
  chat.click();
  await settle();
  return page;
}
const eventsWatch = (page) => page.mcp.calls.watch.filter((w) => w.tool === 'list_events');
const bubbles = (page) => page.ids['chat-log'].children;

test('Chat opens the panel: one list_events call and one watch with the right arguments', async () => {
  const page = await chatPage();
  assert.equal(page.ids.chat.hidden, false);
  assert.equal(page.ids['chat-title'].textContent, 'Fix the build');
  assert.equal(page.ids['chat-open'].href, `https://claude.ai/code/${sid(1)}`);
  const args = { session_id: sid(1), kinds: ['user', 'assistant'], limit: 40 };
  const calls = page.mcp.calls.call;
  assert.equal(calls.length, 1);
  assert.deepEqual([calls[0].server, calls[0].tool, JSON.parse(JSON.stringify(calls[0].input))],
    ['Claude Code Remote', 'list_events', args]);
  const [w] = eventsWatch(page);
  assert.equal(eventsWatch(page).length, 1);
  assert.deepEqual(JSON.parse(JSON.stringify(w.input)), args);
  assert.equal(w.options.refetchInterval, 30000);
  calls[0].resolve({ content: [], payload: { ccr: { data: [userEvent('<b>hi</b>'), assistantEvent([{ type: 'text', text: 'Hello' }])] } } });
  await settle();
  assert.deepEqual(bubbles(page).map((li) => [li.dataset.who, li.textContent]), [['you', '<b>hi</b>'], ['agent', 'Hello']]);
  assert.equal(page.ids['chat-log'].find((e) => e.tagName === 'B').length, 0);
  // Closing unsubscribes; a late delivery renders nothing.
  page.ids['chat-close'].click();
  assert.equal(page.ids.chat.hidden, true);
  assert.equal(w.unsubs, 1);
  w.handler(eventsData([userEvent('late')]));
  assert.equal(bubbles(page).length, 0);
  assert.equal(page.pending().length, 0);
});

test('a long message is clamped with Show more', async () => {
  const page = await chatPage();
  eventsWatch(page)[0].handler(eventsData([assistantEvent([{ type: 'text', text: 'z'.repeat(2000) }])]));
  const [li] = bubbles(page);
  assert.ok(li.textContent.length < 700);
  const [more] = li.find((e) => e.tagName === 'BUTTON');
  more.click();
  assert.ok(li.textContent.includes('z'.repeat(2000)));
});

test('a Runs card with a session link gets a Chat button; nothing is called until it is clicked', async () => {
  const page = await loadPage();
  page.db.handlers().next(snap([{ feature: 'voice', phase: 'building', updated_at: iso(NOW - MIN),
    session_url: `https://claude.ai/code/${sid(7)}` }, { feature: 'nolink', phase: 'building', updated_at: iso(NOW - MIN) }]));
  const [withLink, without] = page.ids.runs.children;
  assert.equal(without.find((e) => e.tagName === 'BUTTON').length, 0);
  assert.deepEqual(page.uses, ['db']);
  assert.equal(page.mcp.calls.call.length + page.mcp.calls.watch.length, 0);
  withLink.find((e) => e.tagName === 'BUTTON')[0].click();
  await settle();
  assert.deepEqual(page.uses, ['db', 'mcp']);
  assert.equal(page.mcp.calls.call[0].input.session_id, sid(7));
  assert.equal(sessionsWatch(page).length, 0, 'the grid watch still never started');
});

function typeAndSend(page, textValue) {
  page.ids['chat-input'].value = textValue;
  page.ids['chat-send'].click();
}
const sends = (page) => page.mcp.calls.call.filter((c) => c.tool === 'send_message');

test('Send makes exactly one send_message per click, disabled while pending, then clears', async () => {
  const page = await chatPage();
  typeAndSend(page, '  Please rebase  ');
  assert.equal(page.ids['chat-send'].disabled, true);
  page.ids['chat-send'].click();  // while pending: ignored
  assert.equal(sends(page).length, 1);
  const [s] = sends(page);
  assert.deepEqual([s.server, JSON.parse(JSON.stringify(s.input))],
    ['Claude Code Remote', { session_id: sid(1), message: 'Please rebase' }]);
  assert.equal(s.options.cache, false);
  s.resolve({ content: [] });
  await settle();
  assert.equal(page.ids['chat-send'].disabled, false);
  assert.equal(page.ids['chat-input'].value, '');
  assert.equal(page.ids['chat-status'].textContent, 'Sent — the agent will reply in its next turn');
  assert.deepEqual(page.mcp.calls.invalidate.map((a) => a[1]), ['list_events']);
  assert.equal(sends(page).length, 1, 'never resent');
  page.ids['chat-send'].click();  // empty box
  assert.equal(sends(page).length, 1);
});

test('a send still reports after the panel is closed and reopened on the same session', async () => {
  const page = await chatPage();
  typeAndSend(page, 'still here');
  page.ids['chat-close'].click();
  tiles(page)[0].find((e) => e.tagName === 'BUTTON' && e.textContent === 'Chat')[0].click();
  await settle();
  assert.equal(page.ids['chat-send'].disabled, true, 'still pending');
  sends(page)[0].resolve({ content: [] });
  await settle();
  assert.equal(page.ids['chat-status'].textContent, 'Sent — the agent will reply in its next turn');
  assert.equal(page.ids['chat-send'].disabled, false);
});

test('Enter alone never sends; Ctrl/Cmd+Enter does', async () => {
  const page = await chatPage();
  const input = page.ids['chat-input'];
  input.value = 'hello';
  const plain = input.dispatch('keydown', { key: 'Enter', ctrlKey: false, metaKey: false });
  assert.equal(plain.defaultPrevented, undefined);
  assert.equal(sends(page).length, 0);
  input.dispatch('keydown', { key: 'Enter', metaKey: true });
  assert.equal(sends(page).length, 1);
});

test('a held or composing Ctrl/Cmd+Enter never sends again after a fast failure', async () => {
  const page = await chatPage();
  const input = page.ids['chat-input'];
  input.value = 'hello';
  input.dispatch('keydown', { key: 'Enter', ctrlKey: true });
  sends(page)[0].reject({ code: 'tool_error' });
  await settle();
  assert.equal(page.ids['chat-send'].disabled, false);
  const held = input.dispatch('keydown', { key: 'Enter', ctrlKey: true, repeat: true });
  assert.equal(held.defaultPrevented, true, 'a held key adds no line either');
  input.dispatch('keydown', { key: 'Enter', metaKey: true, isComposing: true });
  assert.equal(sends(page).length, 1);
  input.dispatch('keydown', { key: 'Enter', ctrlKey: true });  // a fresh press does
  assert.equal(sends(page).length, 2);
});

test('too long or empty messages are not sent', async () => {
  const page = await chatPage();
  typeAndSend(page, 'a'.repeat(4001));
  typeAndSend(page, '   ');
  assert.equal(sends(page).length, 0);
  assert.equal(page.ids['chat-status'].dataset.level, 'bad');
});

for (const [code, expect] of [['tool_error', /tool_error/], ['server_unavailable', /may or may not have arrived/],
  ['needs_reauth', /Reconnect Claude Code Remote/], ['not_in_manifest', /not allowed/]]) {
  test(`send ${code} keeps the draft and does not retry`, async () => {
    const page = await chatPage();
    typeAndSend(page, 'keep me');
    sends(page)[0].reject({ code, message: 'SECRET-DETAIL' });
    await settle();
    assert.equal(page.ids['chat-input'].value, 'keep me');
    assert.match(page.ids['chat-status'].textContent, expect);
    assert.ok(!page.ids['chat-status'].textContent.includes('SECRET'));
    assert.equal(page.ids['chat-send'].disabled, false);
    assert.equal(sends(page).length, 1);
    assert.equal(page.pending().length, 0);
  });
}

test('send approval_required offers one retry per click', async () => {
  const page = await chatPage();
  typeAndSend(page, 'please');
  sends(page)[0].reject({ code: 'approval_required' });
  await settle();
  assert.equal(sends(page).length, 1);
  const [btn] = page.ids['chat-action'].children;
  btn.click();
  btn.click();
  await settle();
  assert.equal(sends(page).length, 2);
  assert.equal(sends(page)[1].input.message, 'please');
});

test('chat watch errors: retract, stale, approval button, and user_changed ends everything', async () => {
  const page = await chatPage();
  const [w] = eventsWatch(page);
  w.handler(eventsData([userEvent('one')]));
  w.handler({ type: 'error', error: { code: 'server_unavailable' } });
  assert.equal(bubbles(page).length, 1);
  assert.match(page.ids['chat-conn'].textContent, /^Stale \(server_unavailable\)/);
  w.handler({ type: 'error', error: { code: 'needs_reauth' } });
  assert.equal(bubbles(page).length, 0);
  w.handler({ type: 'error', error: { code: 'approval_required' } });
  const before = page.mcp.calls.call.length;
  page.ids['chat-action'].children[0].click();
  await settle();
  assert.equal(page.mcp.calls.call.length, before + 1);
  assert.equal(page.mcp.calls.call.at(-1).tool, 'list_events');
  w.handler({ type: 'error', error: { code: 'user_changed' } });
  assert.equal(page.ids.chat.hidden, true);
  assert.equal(w.unsubs, 1);
  assert.equal(sessionsWatch(page)[0].unsubs, 1);
  assert.equal(tiles(page).length, 0);
});

test('when use("mcp") resolves null the chat says so and sends nothing', async () => {
  const page = await loadPage({ mcp: null });
  page.db.handlers().next(snap([{ feature: 'f', phase: 'building', updated_at: iso(NOW - MIN),
    session_url: `https://claude.ai/code/${sid(2)}` }]));
  page.ids.runs.children[0].find((e) => e.tagName === 'BUTTON')[0].click();
  await settle();
  assert.equal(page.ids.chat.hidden, false);
  assert.equal(page.ids['chat-conn'].textContent, 'Chat needs this page open on claude.ai');
  assert.equal(page.ids['chat-send'].disabled, true);
  typeAndSend(page, 'hi');
  assert.equal(page.pending().length, 0);
});

// ---- per-session drafts and send outcomes ----
async function twoChatPage() {
  const page = await gridPage();
  page.mcp.calls.watch[0].handler(data([session(1, { title: 'Alpha' }), session(2, { title: 'Beta', ageMin: 2 })]));
  await openTile(page, 'Alpha');
  return page;
}
async function openTile(page, title) {
  const tile = tiles(page).find((li) => li.find((e) => e.tagName === 'H2')[0].textContent === title);
  tile.find((e) => e.tagName === 'BUTTON' && e.textContent === 'Chat')[0].click();
  await settle();
}
const status = (page) => page.ids['chat-status'].textContent;

test('after a failed send, Close or Escape and reopening keeps the draft and the warning', async () => {
  const page = await chatPage();
  typeAndSend(page, 'keep me');
  sends(page)[0].reject({ code: 'server_unavailable' });
  await settle();
  for (const close of [() => page.ids['chat-close'].click(), () => page.ids.chat.dispatch('keydown', { key: 'Escape' })]) {
    close();
    assert.equal(page.ids.chat.hidden, true);
    await openTile(page, 'Fix the build');
    assert.equal(page.ids['chat-input'].value, 'keep me');
    assert.match(status(page), /^It may or may not have arrived \(server_unavailable\)/);
    assert.equal(page.ids['chat-status'].dataset.level, 'bad');
  }
  assert.equal(sends(page).length, 1, 'never resent');
});

test('opening another session while a send is pending: drafts stay apart, the outcome is reported', async () => {
  const page = await twoChatPage();
  typeAndSend(page, 'alpha text');
  await openTile(page, 'Beta');
  assert.equal(page.ids['chat-input'].value, '', 'Beta starts with its own empty draft');
  assert.equal(status(page), '');
  page.ids['chat-input'].value = 'beta draft';
  sends(page)[0].reject({ code: 'upstream_error', message: 'SECRET-DETAIL' });
  await settle();
  assert.match(status(page), /^Message to “Alpha”: It may or may not have arrived \(upstream_error\)/);
  assert.ok(!status(page).includes('SECRET'));
  assert.equal(page.ids['chat-input'].value, 'beta draft');
  await openTile(page, 'Alpha');
  assert.equal(page.ids['chat-input'].value, 'alpha text');
  assert.match(status(page), /^It may or may not have arrived \(upstream_error\)/);
  await openTile(page, 'Beta');
  assert.equal(page.ids['chat-input'].value, 'beta draft');
  assert.equal(sends(page).length, 1);
});

test('a send that succeeds while another session is open clears only its own draft', async () => {
  const page = await twoChatPage();
  typeAndSend(page, 'ok');
  await openTile(page, 'Beta');
  page.ids['chat-input'].value = 'ok';  // same words, different session
  sends(page)[0].resolve({ content: [] });
  await settle();
  assert.equal(page.ids['chat-input'].value, 'ok', "Beta's draft is not cleared");
  assert.equal(status(page), 'Message to “Alpha”: Sent — the agent will reply in its next turn');
  await openTile(page, 'Alpha');
  assert.equal(page.ids['chat-input'].value, '', "Alpha's sent draft is gone");
  assert.equal(status(page), 'Sent — the agent will reply in its next turn');
  await openTile(page, 'Beta');
  assert.equal(page.ids['chat-input'].value, 'ok');
});

test('a draft edited while its send is pending is kept on success', async () => {
  const page = await twoChatPage();
  typeAndSend(page, 'one');
  page.ids['chat-input'].value = 'one more';
  sends(page)[0].resolve({ content: [] });
  await settle();
  assert.equal(page.ids['chat-input'].value, 'one more');
  assert.equal(status(page), 'Sent — the agent will reply in its next turn');
  // The same holds when the edit was made, then another session opened.
  typeAndSend(page, 'two');
  page.ids['chat-input'].value = 'two, edited';
  await openTile(page, 'Beta');
  sends(page)[1].resolve({ content: [] });
  await settle();
  await openTile(page, 'Alpha');
  assert.equal(page.ids['chat-input'].value, 'two, edited');
});

test('a send that settles after another account signed in calls and shows nothing', async () => {
  const page = await chatPage();
  typeAndSend(page, 'late');
  sessionsWatch(page)[0].handler({ type: 'error', error: { code: 'user_changed' } });
  assert.equal(page.ids.chat.hidden, true);
  assert.equal(page.ids['chat-input'].value, '');
  sends(page)[0].resolve({ content: [] });
  await settle();
  assert.equal(page.mcp.calls.invalidate.length, 0);
  assert.equal(status(page), '');
});

test('drafts are kept in memory for at most 50 sessions', async () => {
  const page = await loadPage();
  const rows = Array.from({ length: 52 }, (_, i) => ({ _docId: `r${i}`, feature: `f${i}`, phase: 'building',
    updated_at: iso(NOW - MIN), session_url: `https://claude.ai/code/${sid(i + 1)}` }));
  page.db.handlers().next(snap(rows));
  const open = async (i) => {
    const card = page.ids.runs.children.find((li) => li.find((e) => e.tagName === 'A' && e.href === `https://claude.ai/code/${sid(i + 1)}`).length);
    card.find((e) => e.tagName === 'BUTTON')[0].click();
    await settle();
  };
  for (let i = 0; i < 52; i += 1) { await open(i); page.ids['chat-input'].value = `draft ${i}`; }
  page.ids['chat-close'].click();
  await open(0);
  assert.equal(page.ids['chat-input'].value, '', 'the oldest drafts were dropped');
  await open(51);
  assert.equal(page.ids['chat-input'].value, 'draft 51');
  await open(2);
  assert.equal(page.ids['chat-input'].value, 'draft 2');
});
