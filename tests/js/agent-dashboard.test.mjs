// node --test tests/js/agent-dashboard.test.mjs  (Node 18+; no packages needed)
//
// Runs the dev-team dashboard page's own <script> (tools/agent-dashboard/
// index.html) in node:vm with a minimal fake document, a fake
// window.claude.use("db") and fake timers, then checks what it decides and
// renders: silent/running/needs at the time limits, safe links, row text
// that stays text, and each db error code -> stop, back off or retry.

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
  }
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

async function loadPage({ db = fakeDb(), withClaude = true } = {}) {
  const ids = {};
  for (const id of ['summary', 'conn', 'runs', 'empty']) ids[id] = new FakeEl(id === 'runs' ? 'ul' : 'p');
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
  if (withClaude) sandbox.claude = { use: async (cap) => (cap === 'db' ? db.db : null) };
  sandbox.window = sandbox;
  const ctx = vm.createContext(sandbox);
  vm.runInContext(`Date.now = () => ${NOW};`, ctx);
  vm.runInContext(SCRIPT, ctx, { filename: 'index.html' });
  await new Promise((r) => setImmediate(r));
  const pending = () => timers.filter((t) => !t.cleared);
  return { api: sandbox.AgentDashboard, ids, db, timers, pending, intervals };
}

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
