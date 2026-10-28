// node --test tests/js/canvas-helper.test.mjs  (Node 18+; no packages needed)
//
// The Kwil Canvas helper extension (tools/canvas-helper/): its service
// worker logic with a fake `chrome`, a fake Canvas (its own API shapes,
// Link pagination, `while(1);`, rate limits, the file store redirect) and
// a fake Kwil (kwil/canvas_helper.py's answers). No network. Covers
// pairing (the key stored, never sent to Canvas, never to the popup),
// pagination and back-off, the manifest's fields (no grades, submissions
// or file URLs), only wanted files pushed, and origin pinning. The
// pairing content script runs in node:vm with a minimal DOM.

import test from 'node:test';
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import vm from 'node:vm';

import { CanvasReader, nextLink, rateLimited, stripGuard } from
  '../../tools/canvas-helper/lib/canvas.js';
import { KwilClient, KwilError } from '../../tools/canvas-helper/lib/kwil.js';
import { batches, buildCourse, size } from '../../tools/canvas-helper/lib/manifest.js';
import { runSync } from '../../tools/canvas-helper/lib/sync.js';
import { createHelper, isInstructure } from '../../tools/canvas-helper/lib/helper.js';
import { describe } from '../../tools/canvas-helper/lib/status.js';
import { CANVAS, CODE, FILESTORE, KEY, KWIL, PDF_BYTES, VERIFIER, canvasFile, fakeChrome,
         kwilRoutes, popupSender, school, tabSender, world } from './canvas-helper-fakes.mjs';

const PAIRING = { key: KEY, origin: KWIL, pairedAt: '2026-10-27T00:00:00Z',
                  maxFileBytes: 3_934_464, maxFiles: 40 };
const noSleep = async () => {};

function sync(w, extra = {}) {
  const logs = [];
  const slept = [];
  return runSync({ pairing: PAIRING, canvasOrigin: CANVAS, fetch: w.fetch,
                   sleep: async (ms) => { slept.push(ms); }, log: (l) => logs.push(l), ...extra })
    .then((out) => ({ ...out, logs, slept }));
}

const canvasCalls = (w) => w.calls.filter((c) => c.url.startsWith(CANVAS));
const kwilCalls = (w) => w.calls.filter((c) => c.url.startsWith(KWIL));

// --- the whole run --------------------------------------------------------------------

test('a sync pushes the courses as Kwil reads them, then only the wanted PDF', async () => {
  const w = world({ kwil: kwilRoutes() });
  const out = await sync(w);
  assert.equal(out.result, 'ok');
  assert.equal(w.pushed.manifests.length, 1);
  const m = w.pushed.manifests[0];
  assert.deepEqual(m.courses.map((c) => c.id), [111, 222]);   // 333: not open yet
  const math = m.courses[0];
  assert.match(math.syllabus_body, /Grading/);
  assert.deepEqual(math.pages.map((p) => p.body), ['<p>Office hours in Altgeld 141.</p>',
                                                   '<p>Lab in Loomis 276.</p>']);
  assert.deepEqual(math.assignments, [{ id: 9, name: 'Problem Set 5',
    due_at: '2026-11-04T04:59:00Z', updated_at: '2026-10-03T12:00:00Z',
    description: '<p>Show your work.</p>' }]);
  assert.deepEqual(math.modules, [{ id: 5, name: 'Week 1',
                                    items: [{ title: 'Week 1', type: 'Page' }] }]);
  assert.deepEqual(math.files.map((f) => f.id), [501, 502]);
  assert.equal(m.courses[1].files, null);   // a hidden Files tab: not read, Kwil keeps its own
  assert.deepEqual(m.announcements.map((n) => n.id), [31]);
  const blob = JSON.stringify(m);
  for (const never of ['score', 'grade', 'submission', 'enrollments', 'points_possible',
                       'verifier', 'download_frd', '"url":"https', 'Prof. X', 'my answers']) {
    assert.ok(!blob.includes(never), never);
  }
  // only the file Kwil asked for, with the bytes the file store sent
  assert.deepEqual(w.pushed.files.map((f) => [f.course_id, f.file_id]), [['111', '501']]);
  assert.deepEqual([...w.pushed.files[0].bytes], [...PDF_BYTES]);
  assert.equal(canvasCalls(w).filter((c) => c.url.includes('/files/502/')).length, 0);
  assert.equal(out.counts.files, 1);
  assert.equal(out.counts.courses, 2);
});

test('the key goes only to Kwil and the Canvas session only to Canvas', async () => {
  const w = world({ kwil: kwilRoutes() });
  await sync(w);
  assert.ok(canvasCalls(w).length > 5);
  for (const c of canvasCalls(w)) {
    assert.equal(c.opts.method, 'GET');
    assert.equal(c.opts.credentials, 'include');   // the browser's own Canvas session
    assert.ok(!JSON.stringify(c).includes(KEY), 'the key reached Canvas');
    assert.ok(!('Authorization' in (c.opts.headers || {})));
    assert.ok(!c.url.includes('kcs1.'));
  }
  assert.equal(kwilCalls(w).length, 2);
  for (const c of kwilCalls(w)) {
    assert.equal(c.opts.method, 'POST');
    assert.equal(c.opts.credentials, 'omit');      // no cookie of any site rides along
    assert.equal(c.opts.redirect, 'error');        // a redirect can't carry the key away
    assert.equal(c.opts.headers.Authorization, `Bearer ${KEY}`);
    assert.ok(!c.url.includes(KEY) && !c.url.includes('?'));
  }
  assert.deepEqual(w.calls.filter((c) => !c.url.startsWith(CANVAS) && !c.url.startsWith(KWIL)),
                   []);
});

test('only wanted files are pushed: none wanted, none downloaded', async () => {
  const w = world({ kwil: kwilRoutes({ want: [] }) });
  const out = await sync(w);
  assert.equal(out.result, 'ok');
  assert.deepEqual(w.pushed.files, []);
  assert.equal(canvasCalls(w).filter((c) => c.url.includes('/download')).length, 0);
});

test('a wanted id the run did not list is never fetched; files stop at max_files', async () => {
  const data = school();
  data.files[111] = Array.from({ length: 50 }, (_, i) =>
    canvasFile(600 + i, `Notes ${i}.pdf`, 'application/pdf', 'pdf', 10));
  for (let i = 0; i < 50; i++) data.bytes[600 + i] = new Uint8Array(10);
  const want = [{ course_id: 111, file_id: 999 }, { course_id: 222, file_id: 600 },
                ...data.files[111].map((f) => ({ course_id: 111, file_id: f.id }))];
  const w = world({ data, kwil: kwilRoutes({ want }) });
  const out = await sync(w, { pairing: { ...PAIRING, maxFiles: 5 } });
  assert.equal(out.result, 'ok');
  // 999 and (222, 600) weren't in this run's listing: skipped; then 3 of 600..
  assert.deepEqual(w.pushed.files.map((f) => Number(f.file_id)), [600, 601, 602]);
});

test('a file over Kwil\'s limit is cut one byte past it (Kwil notes it "too large")', async () => {
  const w = world({ kwil: kwilRoutes() });
  await sync(w, { pairing: { ...PAIRING, maxFileBytes: 10 } });
  // the manifest's answer says 3_934_464; with a tiny one from Kwil:
  const w2 = world({ kwil: { ...kwilRoutes(), '/api/student/canvas/push/manifest': () => ({
    body: { counts: {}, max_file_bytes: 10, want: [{ course_id: 111, file_id: 501 }] } }) } });
  await sync(w2);
  assert.equal(w2.pushed.files[0].bytes.length, 11);
  assert.equal(w.pushed.files[0].bytes.length, PDF_BYTES.length);
});

test('a file the browser can\'t download skips that file only', async () => {
  const data = school();
  data.unreachable = [501];
  const w = world({ data, kwil: kwilRoutes({ want: [{ course_id: 111, file_id: 501 },
                                                    { course_id: 111, file_id: 502 }] }) });
  const out = await sync(w);
  assert.equal(out.result, 'ok');
  assert.deepEqual(w.pushed.files.map((f) => f.file_id), ['502']);
});

test('a file link on another host is never fetched', async () => {
  const data = school();
  data.files[111][0].url = `https://evil.example/files/501/download?verifier=${VERIFIER}`;
  const w = world({ data, kwil: kwilRoutes() });
  await sync(w);
  assert.deepEqual(w.pushed.files, []);
  assert.ok(!w.calls.some((c) => c.url.includes('evil.example')));
});

// --- pagination and back-off -------------------------------------------------------------

test('listings follow Link rel=next on the same origin, at most 10 pages', async () => {
  const data = school();
  data.assignments[111] = Array.from({ length: 15 }, (_, i) =>
    ({ id: 100 + i, name: `A${i}`, updated_at: 'x', description: '' }));
  const w = world({ data, perPage: 1, kwil: kwilRoutes({ want: [] }) });
  await sync(w);
  const m = w.pushed.manifests[0];
  assert.deepEqual(m.courses.map((c) => c.id), [111, 222]);   // 3 pages of courses
  assert.equal(m.courses[0].assignments.length, 10);           // 10 pages, then stop
  const pages = canvasCalls(w).filter((c) => c.url.includes('/courses/111/assignments'));
  assert.equal(pages.length, 10);
  assert.equal(nextLink('<https://evil.example/api/v1/courses?page=2>; rel="next"', CANVAS), null);
  assert.equal(nextLink('<http://school.instructure.com/x?page=2>; rel="next"', CANVAS), null);
  assert.equal(nextLink(`<${CANVAS}/x?page=2>; rel="next"`, CANVAS), `${CANVAS}/x?page=2`);
});

test('Canvas\'s rate limit is waited out (1 s, 2 s), then the run goes on', async () => {
  const w = world({ limitTimes: 2, kwil: kwilRoutes() });
  const out = await sync(w);
  assert.equal(out.result, 'ok');
  assert.deepEqual(out.slept, [1000, 2000]);
});

test('a lasting rate limit stops the run before anything is pushed', async () => {
  const w = world({ limitTimes: 100, kwil: kwilRoutes() });
  const out = await sync(w);
  assert.equal(out.result, 'canvas_rate_limited');
  assert.deepEqual(out.slept, [1000, 2000, 4000]);
  assert.equal(canvasCalls(w).length, 4);
  assert.equal(kwilCalls(w).length, 0);
});

test('signed out of Canvas: nothing is pushed', async () => {
  const w = world({ signedOut: true, kwil: kwilRoutes() });
  const out = await sync(w);
  assert.equal(out.result, 'canvas_signed_out');
  assert.equal(kwilCalls(w).length, 0);
  assert.equal(canvasCalls(w).length, 1);
});

test('the rate limit and the sign-in page are told apart from a permission', () => {
  const h = new Headers({ 'x-rate-limit-remaining': '0' });
  assert.equal(rateLimited(429, new Headers(), ''), true);
  assert.equal(rateLimited(403, h, ''), true);
  assert.equal(rateLimited(403, new Headers({ 'x-rate-limit-remaining': '600' }), 'nope'), false);
  assert.equal(stripGuard('while(1);[1]'), '[1]');
  assert.equal(stripGuard('[1]'), '[1]');
});

test('Kwil\'s answers: 401 unpairs, 429 backs off, a 409 on a file skips it', async () => {
  let w = world({ kwil: kwilRoutes({ manifestStatus: 401 }) });
  assert.equal((await sync(w)).result, 'unpaired');
  w = world({ kwil: kwilRoutes({ manifestStatus: 429 }) });
  const limited = await sync(w);
  assert.equal(limited.result, 'kwil_rate_limited');
  assert.equal(limited.retryAfter, 600);
  w = world({ kwil: kwilRoutes({ fileStatus: 409 }) });
  const out = await sync(w);
  assert.equal(out.result, 'ok');
  assert.equal(out.counts.files, 0);
});

// --- origin pinning ----------------------------------------------------------------------

test('Kwil and Canvas origins are pinned to https', () => {
  for (const origin of ['http://heykwil.com', 'https://heykwil.com:8443', 'https://localhost',
                        'https://u:p@heykwil.com', 'javascript:alert(1)']) {
    assert.throws(() => new KwilClient({ origin, key: KEY, fetch }), KwilError, origin);
    assert.throws(() => new CanvasReader({ origin, fetch }), origin);
  }
  assert.throws(() => new KwilClient({ origin: KWIL, key: 'kcs1.short', fetch }), KwilError);
  const c = new KwilClient({ origin: 'https://heykwil.com/some/path?x=1', key: KEY, fetch });
  assert.equal(c.origin, KWIL);
});

test('the helper pushes only to the origin it was paired on', async () => {
  const { chrome } = fakeChrome();
  const w = world({ kwil: kwilRoutes() });
  const helper = createHelper({ chrome, fetch: w.fetch, sleep: noSleep });
  assert.deepEqual(await helper.onMessage({ type: 'kwil-paired', key: KEY },
                                          tabSender(KWIL)), { ok: true });
  assert.deepEqual(await helper.onMessage({ type: 'canvas-seen' }, tabSender(CANVAS)),
                   { ok: true });
  const out = await helper.sync();
  assert.equal(out.result, 'ok');
  const posts = w.calls.filter((c) => c.opts.method === 'POST');
  assert.ok(posts.length >= 2 && posts.every((c) => c.url.startsWith(`${KWIL}/api/student/canvas/push/`)));
});

// --- pairing -----------------------------------------------------------------------------

test('pairing is accepted only from a Kwil page, and the key never reaches the popup', async () => {
  const { chrome, store, alarms } = fakeChrome();
  const helper = createHelper({ chrome, fetch: () => { throw new Error('no fetch'); },
                                sleep: noSleep, now: () => Date.parse('2026-10-27T12:00:00Z') });
  const refused = [
    [{ type: 'kwil-paired', key: KEY }, tabSender('https://evil.example')],
    [{ type: 'kwil-paired', key: KEY }, tabSender(CANVAS)],
    [{ type: 'kwil-paired', key: KEY }, tabSender('http://heykwil.com')],
    [{ type: 'kwil-paired', key: KEY }, tabSender(KWIL, 'another-extension')],
    [{ type: 'kwil-paired', key: KEY }, popupSender()],             // not from a page
    [{ type: 'kwil-paired', key: 'kcs1.nope' }, tabSender(KWIL)],
    [{ type: 'kwil-paired', key: KEY, origin: KWIL }, tabSender('https://evil.example')],
  ];
  for (const [msg, sender] of refused) {
    assert.deepEqual(await helper.onMessage(msg, sender), { ok: false });
  }
  assert.equal(store.pairing, undefined);
  assert.deepEqual(await helper.onMessage({ type: 'kwil-paired', key: KEY, maxFileBytes: 3e6,
                                            maxFiles: 40 }, tabSender(KWIL)), { ok: true });
  assert.equal(store.pairing.key, KEY);
  assert.equal(store.pairing.origin, KWIL);
  assert.ok(alarms['kwil-sync'] && alarms['kwil-sync'].periodInMinutes === 360);
  assert.ok(alarms['kwil-sync-soon']);              // a first sync soon after pairing
  const st = await helper.onMessage({ type: 'status' }, popupSender());
  assert.equal(st.paired, true);
  assert.ok(!JSON.stringify(st).includes(KEY));
  // a page can't ask for the status (the popup only), nor unpair
  assert.deepEqual(await helper.onMessage({ type: 'status' }, tabSender(KWIL)), { ok: false });
  assert.deepEqual(await helper.onMessage({ type: 'unpair' }, tabSender(KWIL)), { ok: false });
  assert.deepEqual(await helper.onMessage({ type: 'unpair' }, popupSender()), { ok: true });
  assert.equal(store.pairing, undefined);
});

function pairPage({ origin = KWIL, answer = { status: 200, body: { key: KEY,
                     manifest: '/api/student/canvas/push/manifest', max_file_bytes: 3934464,
                     max_files: 40 } } } = {}) {
  const u = new URL(origin);
  const el = { attrs: {}, getAttribute(k) { return this.attrs[k] ?? null; } };
  let observer = null;
  const sent = [];
  const fetches = [];
  const ctx = {
    location: { protocol: u.protocol, hostname: u.hostname, origin: u.origin },
    document: { documentElement: {},
                querySelector: (sel) => (sel === '[data-kwil-pair]' && 'data-kwil-pair' in el.attrs
                  ? el : null) },
    MutationObserver: class { constructor(fn) { observer = fn; } observe() {} },
    fetch: async (url, opts) => {
      fetches.push({ url, opts });
      return { status: answer.status, json: async () => answer.body };
    },
    chrome: { runtime: { sendMessage: async (m) => { sent.push(m); return { ok: true }; } } },
    JSON, Set, String,
  };
  ctx.globalThis = ctx;
  vm.createContext(ctx);
  vm.runInContext(readFileSync(new URL('../../tools/canvas-helper/content/kwil-pair.js',
                                       import.meta.url), 'utf8'), ctx);
  return { el, sent, fetches, show: async (code) => {
    el.attrs['data-kwil-pair'] = code;
    if (observer) observer();
    await new Promise((r) => setTimeout(r, 0));
    await new Promise((r) => setTimeout(r, 0));
  } };
}

test('the Kwil page\'s code is traded same-origin and the key handed to the worker', async () => {
  const p = pairPage();
  await p.show(CODE);
  assert.equal(p.fetches.length, 1);
  assert.equal(p.fetches[0].url, `${KWIL}/api/student/canvas/pair`);
  assert.equal(p.fetches[0].opts.method, 'POST');
  assert.equal(p.fetches[0].opts.credentials, 'omit');
  assert.deepEqual(JSON.parse(p.fetches[0].opts.body), { code: CODE });
  assert.ok(!p.fetches[0].url.includes(CODE));      // never in a URL
  // (objects made inside the vm are from another realm: compare as JSON)
  assert.deepEqual(JSON.parse(JSON.stringify(p.sent)),
                   [{ type: 'kwil-paired', key: KEY, maxFileBytes: 3934464, maxFiles: 40 }]);
  assert.ok(!JSON.stringify(p.el).includes(KEY));   // nothing written to the page
  await p.show(CODE);                               // the same code: not tried again
  assert.equal(p.fetches.length, 1);
  await p.show('kcp1.short');
  assert.equal(p.fetches.length, 1);
  // a Kwil on its own domain (enabled from the popup): its own origin only
  const own = pairPage({ origin: 'https://kwil.example.org' });
  await own.show(CODE);
  assert.deepEqual(own.fetches.map((f) => f.url), ['https://kwil.example.org/api/student/canvas/pair']);
});

test('a refused code or a non-key answer stores nothing; no pairing off https or on Canvas', async () => {
  let p = pairPage({ answer: { status: 401, body: { error: 'not paired' } } });
  await p.show(CODE);
  assert.deepEqual(p.sent, []);
  p = pairPage({ answer: { status: 200, body: { key: 'hc1.something' } } });
  await p.show(CODE);
  assert.deepEqual(p.sent, []);
  for (const origin of ['http://heykwil.com', CANVAS]) {
    p = pairPage({ origin });
    await p.show(CODE);
    assert.equal(p.fetches.length, 0, origin);
  }
});

// --- the worker's schedule and Canvas detection --------------------------------------

test('a lasting Canvas rate limit backs off: the next alarm inside it reads nothing', async () => {
  const { chrome, store } = fakeChrome();
  let t = Date.parse('2026-10-27T12:00:00Z');
  const w = world({ limitTimes: 1000, kwil: kwilRoutes() });
  const helper = createHelper({ chrome, fetch: w.fetch, sleep: noSleep, now: () => t });
  await helper.onMessage({ type: 'kwil-paired', key: KEY }, tabSender(KWIL));
  store.canvas = { origin: CANVAS, signedOut: false };
  assert.equal((await helper.onAlarm({ name: 'kwil-sync' })).result, 'canvas_rate_limited');
  assert.equal(store.status.nextAfter, t + 15 * 60_000);
  const before = w.calls.length;
  t += 60_000;
  assert.equal((await helper.onAlarm({ name: 'kwil-sync' })).result, 'waiting');
  assert.equal(w.calls.length, before);
  t += 15 * 60_000;
  await helper.onAlarm({ name: 'kwil-sync' });
  assert.equal(store.status.nextAfter, t + 30 * 60_000);   // doubled
});

test('signed out of Canvas: marked, and a later Canvas visit syncs soon', async () => {
  const { chrome, store, alarms } = fakeChrome();
  let t = Date.parse('2026-10-27T12:00:00Z');
  let signedOut = true;
  const inner = world({ kwil: kwilRoutes() });
  const out = world({ signedOut: true });
  const fetch = (url, opts) => (signedOut ? out.fetch : inner.fetch)(url, opts);
  const helper = createHelper({ chrome, fetch, sleep: noSleep, now: () => t });
  await helper.onMessage({ type: 'kwil-paired', key: KEY }, tabSender(KWIL));
  store.canvas = { origin: CANVAS, signedOut: false };
  await helper.sync();
  assert.equal(store.canvas.signedOut, true);
  assert.equal(store.status.result, 'canvas_signed_out');
  assert.equal(inner.pushed.manifests.length, 0);
  delete alarms['kwil-sync-soon'];
  signedOut = false;                     // the person signs in to Canvas again
  t += 60_000;
  await helper.onMessage({ type: 'canvas-seen' }, tabSender(CANVAS));
  assert.ok(alarms['kwil-sync-soon']);
  await helper.onAlarm({ name: 'kwil-sync-soon' });
  assert.equal(store.status.result, 'ok');
  assert.equal(store.canvas.signedOut, false);
});

test('Canvas is found from where the person browses, never from Kwil or a stranger', async () => {
  const { chrome, store } = fakeChrome();
  const w = world();
  const helper = createHelper({ chrome, fetch: w.fetch, sleep: noSleep });
  assert.deepEqual(await helper.onMessage({ type: 'canvas-seen' },
                                          tabSender('https://evil.example')), { ok: false });
  assert.deepEqual(await helper.onMessage({ type: 'canvas-seen' }, tabSender(KWIL)),
                   { ok: false });
  assert.equal(w.calls.length, 0);
  assert.deepEqual(await helper.onMessage({ type: 'canvas-seen' }, tabSender(CANVAS)),
                   { ok: true });
  assert.equal(store.canvas.origin, CANVAS);
  assert.ok(isInstructure('https://illinois.instructure.com'));
  assert.ok(!isInstructure('https://instructure.com.evil.example'));
  assert.ok(!isInstructure('http://x.instructure.com'));
});

test('a school domain is enabled only with its own granted permission', async () => {
  const school = 'https://canvas.school.edu';
  const { chrome, store, scripts, injected } = fakeChrome({ granted: false });
  const helper = createHelper({ chrome, fetch: world().fetch, sleep: noSleep });
  assert.deepEqual(await helper.onMessage({ type: 'enable-site', origin: school, tabId: 7 },
                                          popupSender()), { ok: false, error: 'permission' });
  assert.deepEqual(await helper.onMessage({ type: 'enable-site', origin: school, tabId: 7 },
                                          tabSender(school)), { ok: false });
  chrome.permissions.granted.add(`${school}/*`);
  assert.deepEqual(await helper.onMessage({ type: 'enable-site', origin: school, tabId: 7 },
                                          popupSender()), { ok: true });
  assert.deepEqual(store.sites, [school]);
  assert.deepEqual(scripts.map((s) => s.matches), [[`${school}/*`]]);
  assert.equal(injected[0].target.tabId, 7);
  await helper.permissionsRemoved({ origins: [`${school}/*`] });
  assert.deepEqual(store.sites, []);
  assert.deepEqual(scripts, []);
});

test('a sync without the host permissions reads nothing', async () => {
  const { chrome, store } = fakeChrome({ granted: false });
  const w = world({ kwil: kwilRoutes() });
  const helper = createHelper({ chrome, fetch: w.fetch, sleep: noSleep });
  store.pairing = PAIRING;
  store.canvas = { origin: 'https://canvas.school.edu', signedOut: false };
  assert.equal((await helper.sync()).result, 'needs_permission');
  assert.equal(w.calls.length, 0);
});

test('Unpair during a run stays unpaired', async () => {
  const { chrome, store } = fakeChrome();
  const w = world({ kwil: kwilRoutes() });
  let helper;
  const fetch = async (url, opts) => {
    if (url.includes('/push/manifest')) await helper.unpair();   // the popup, mid-run
    return w.fetch(url, opts);
  };
  helper = createHelper({ chrome, fetch, sleep: noSleep });
  store.pairing = { ...PAIRING, maxFileBytes: 1 };   // the run learns a new limit
  store.canvas = { origin: CANVAS, signedOut: false };
  await helper.sync();
  assert.equal(store.pairing, undefined);
  assert.equal(store.status, undefined);
});

test('one run at a time', async () => {
  const { chrome, store } = fakeChrome();
  const w = world({ kwil: kwilRoutes() });
  const helper = createHelper({ chrome, fetch: w.fetch, sleep: noSleep });
  store.pairing = PAIRING;
  store.canvas = { origin: CANVAS, signedOut: false };
  const [a, b] = [helper.sync(), helper.sync()];
  assert.equal(a, b);
  await a;
  assert.equal(w.pushed.manifests.length, 1);
});

// --- the manifest's size -----------------------------------------------------------------

test('a big manifest is split by course under the byte budget, each course once', () => {
  const big = '<p>' + 'x'.repeat(60_000) + '</p>';
  const courses = Array.from({ length: 12 }, (_, i) => buildCourse(
    { id: 1000 + i, name: `C${i}`, course_code: `C${i}`, syllabus_body: big },
    { pages: [{ page_id: 1, url: 'p', title: 'p', body: big }], assignments: [],
      modules: [], files: [] }));
  const news = courses.map((c) => ({ id: c.id, title: 't', message: 'm', posted_at: 'x',
                                     context_code: `course_${c.id}` }));
  const out = batches(courses, news, 300_000);
  assert.ok(out.length > 1);
  for (const b of out) {
    assert.ok(size(b) <= 300_000, `batch of ${size(b)}`);
    const ids = new Set(b.courses.map((c) => c.id));
    assert.ok(b.announcements.every((n) => ids.has(Number(n.context_code.slice(7)))));
  }
  assert.deepEqual(out.flatMap((b) => b.courses.map((c) => c.id)), courses.map((c) => c.id));
  assert.equal(out.flatMap((b) => b.announcements).length, 12);
  // one course bigger than the budget alone: its biggest listing is left
  // out (null: Kwil keeps what it has), never a partial list
  const [one] = batches([buildCourse({ id: 1, syllabus_body: 'syl' },
    { pages: [{ page_id: 1, url: 'p', body: 'y'.repeat(400_000) }], assignments: [],
      modules: [], files: [] })], null, 250_000);
  assert.equal(one.courses[0].pages, null);
  assert.deepEqual(one.courses[0].assignments, []);
  assert.equal(one.announcements, null);
});

// --- logs and the popup ------------------------------------------------------------------

test('logs carry counts and codes, never the key, a URL, a title or text', async () => {
  const w = world({ kwil: kwilRoutes() });
  const out = await sync(w);
  assert.ok(out.logs.length >= 2);
  const all = out.logs.join('\n');
  for (const never of [KEY, VERIFIER, 'MATH 241', 'Syllabus.pdf', 'Altgeld', 'Midterm',
                       CANVAS, KWIL, FILESTORE, 'https://']) {
    assert.ok(!all.includes(never), never);
  }
});

test('the popup shows status only, as text', () => {
  assert.equal(describe({ paired: false }).title, 'Not paired');
  const view = describe({ paired: true, kwil: KWIL, canvas: CANVAS,
                          lastSync: '2026-10-27T11:00:00Z', result: 'ok',
                          counts: { courses: 2, files: 1 } }, Date.parse('2026-10-27T12:00:00Z'));
  assert.deepEqual(view.lines, ['Kwil: heykwil.com', 'Canvas: school.instructure.com',
                                'Last sync: 1 h ago', '2 course(s), 1 file(s) sent']);
  assert.match(describe({ paired: true, kwil: KWIL, result: 'canvas_signed_out' }).lines.join(' '),
               /Sign in to Canvas/);
});
