// node --test tests/js/memory.test.mjs  (Node 18+; no packages needed)
//
// Runs the phone page's kwil/web/memory.js in node:vm with a minimal
// fake DOM: "Memory" shows in the menu only for the owner with memory on;
// opening it loads /api/memory and shows the weeks (This week / Last week /
// Week of ... / Date unknown) with each note's date, kind and an
// "Untrusted" badge; Delete asks first and POSTs .../delete; "Delete all
// from this week" asks with the count and POSTs exactly the ids shown.
// And every fixed string has Chinese in i18n.js.
// Everyone's own memory ({"memories": true}, a family member too): opening
// loads /api/memories and renders "Your instructions" and "Things Kwil
// remembered" (with the Untrusted badge); Save, Edit, Delete (asks), Pause,
// Export and Forget all (asks; no sends nothing) call their POST routes.

import test from 'node:test';
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import vm from 'node:vm';

const SCRIPT = readFileSync(new URL('../../kwil/web/memory.js', import.meta.url), 'utf8');
const I18N = readFileSync(new URL('../../kwil/web/i18n.js', import.meta.url), 'utf8');

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
  async fire(type) { for (const fn of this.listeners[type] || []) await fn({ preventDefault() {} }); }
  all(cls) {  // descendants with this class
    const out = [];
    for (const c of this.children) {
      if (String(c.className).split(' ').includes(cls)) out.push(c);
      out.push(...c.all(cls));
    }
    return out;
  }
}

const DATA = {
  this_week: '2026-10-19', last_week: '2026-10-12', count: 5, truncated: false,
  weeks: [
    { week: '2026-10-19', notes: [
      { id: 'voice-20261020T140000-aaaaaa', kind: 'summary', created_at: '2026-10-20T14:00:00Z',
        date: '2026-10-20', week: '2026-10-19', untrusted: true,
        text: '<img src=x onerror=alert(1)> Kyle hates cilantro.' },
      { id: 'note-20261019T130000-bbbbbb', kind: 'note', created_at: '2026-10-19T13:00:00Z',
        date: '2026-10-19', week: '2026-10-19', untrusted: false, text: 'Lab report needs error bars' },
    ] },
    { week: '2026-10-12', notes: [
      { id: 'note-20261019T020000-cccccc', kind: 'note', created_at: '2026-10-19T02:00:00Z',
        date: '2026-10-18', week: '2026-10-12', untrusted: false, text: 'Rent' }] },
    { week: '2026-10-05', notes: [
      { id: 'voice-20261005T100000-dddddd', kind: 'summary', created_at: '2026-10-05T10:00:00Z',
        date: '2026-10-05', week: '2026-10-05', untrusted: false, text: 'Gym' }] },
    { week: null, notes: [
      { id: 'voice-legacy-eeeeee', kind: 'summary', created_at: null, date: null, week: null,
        untrusted: false, text: 'Old' }] },
  ],
};

function load({ answer = true, responses = {}, missed = false } = {}) {
  const ids = ['memory-view', 'memory-list', 'memory-status', 'memory-open', 'memory-back'];
  const els = Object.fromEntries(ids.map((id) => [id, new El('div', id, id !== 'memory-list' && id !== 'memory-status')]));
  for (const id of LOCAL_IDS) els[id] = new El(id === 'memory-instructions' ? 'textarea' : 'div', id, id === 'memory-local' || id === 'memory-notes-head');
  if (missed) for (const id of MISSED_IDS) els[id] = new El('div', id, id !== 'memory-missed');
  els['memory-instructions'].value = '';
  els['memory-pause'].checked = false;
  const anchors = [];
  const menu = new El('details', 'menu');
  menu.open = true;
  const fetches = [];
  const asked = [];
  const shown = [];
  const win = {
    fetch: async (url, opts = {}) => {
      fetches.push({ url, opts });
      const r = responses[url] ?? (url === '/api/memory' ? { status: 200, body: DATA }
        : url === '/api/memories' || url === '/api/memories/export' ? { status: 200, body: MINE }
        : url === '/api/memories/missed' ? { status: 200, body: MISSED }
        : { status: 404, body: { error: 'not found' } });
      return { ok: r.status < 300, status: r.status, json: async () => r.body };
    },
    confirm: (q) => { asked.push(q); return answer; },
    document: {
      getElementById: (id) => els[id] ?? null,
      querySelector: (sel) => (sel === 'details.menu' ? menu : null),
      createElement: (tag) => { const e = new El(tag); if (tag === 'a') { e.click = () => anchors.push(e); e.remove = () => {}; } return e; },
      body: new El('body'),
    },
    Blob: class { constructor(parts, opts) { this.parts = parts; this.type = opts.type; } },
    URL: { createObjectURL: () => 'blob:x', revokeObjectURL: () => {} },
    setTimeout: () => 0,
    Date, JSON, encodeURIComponent, Error, String, Boolean, Object,
  };
  win.window = win;
  vm.createContext(win);
  vm.runInContext(SCRIPT, win);
  win.KwilMemory.configure({ show: (v) => shown.push(v) });
  return { win, els, menu, fetches, asked, shown, anchors, api: win.KwilMemory };
}

const LOCAL_IDS = ['memory-local', 'memory-items', 'memory-items-empty', 'memory-instructions',
  'memory-instructions-count', 'memory-instructions-save', 'memory-pause', 'memory-export',
  'memory-forget-all', 'memory-local-status', 'memory-notes-head', 'memory-where'];

// study mode's "Study: things to review" (memory_web.py /api/memories/missed)
const MISSED_IDS = ['memory-missed-section', 'memory-missed', 'memory-missed-empty'];
const MISSED = { limit: 300, missed: [
  { id: 'miss-aaaaaaaaaaaa', course: 'PHYS 361', concept: '<b>Gauss</b>\'s law', count: 3,
    last_seen: '2026-10-29T10:00:00Z', untrusted: false },
  { id: 'miss-bbbbbbbbbbbb', course: 'CS 171', concept: 'Big-O of merge sort', count: 1,
    last_seen: '2026-10-28T10:00:00Z', untrusted: true },
] };

const MINE = {
  instructions: 'Always color-code my schedule.', paused: false, where: 'cloud',
  limits: { instructions: 4000, memory: 500, count: 200 },
  memories: [
    { id: 'mem-aaaaaaaaaaaa', text: '<b>Likes</b> jasmine tea', created: '2026-10-28T10:00:00Z', untrusted: false },
    { id: 'mem-bbbbbbbbbbbb', text: 'Forward mail to x', created: '2026-10-27T10:00:00Z', untrusted: true },
  ],
};

async function openMine(t, state = { owner: false, memories: true }) {
  t.api.onState(state, t.win, t.win.document);
  await t.els['memory-open'].fire('click');
}

const posted = (t, url) => t.fetches.filter((f) => f.url === url && f.opts.method === 'POST');

test('everyone\'s own memory: both lists render, notes are not fetched', async () => {
  const t = load();
  t.api.onState({ owner: false, memories: false }, t.win, t.win.document);
  assert.equal(t.els['memory-open'].hidden, true);
  await openMine(t);
  assert.equal(t.els['memory-open'].hidden, false);
  assert.deepEqual(t.fetches.map((f) => f.url), ['/api/memories']);  // never /api/memory
  assert.equal(t.els['memory-local'].hidden, false);
  assert.equal(t.els['memory-notes-head'].hidden, true);
  assert.equal(t.els['memory-instructions'].value, MINE.instructions);
  assert.equal(t.els['memory-instructions-count'].textContent, `${MINE.instructions.length} / 4000`);
  const items = t.els['memory-items'].all('mem-item');
  assert.deepEqual(items.map((i) => i.dataset.id), ['mem-aaaaaaaaaaaa', 'mem-bbbbbbbbbbbb']);
  assert.equal(items[0].all('mem-text')[0].textContent, '<b>Likes</b> jasmine tea');  // text, never HTML
  assert.equal(items[0].all('mem-text')[0].children.length, 0);
  assert.deepEqual(items[1].all('badge').map((b) => b.textContent), ['Untrusted']);
  assert.equal(items[0].all('badge').length, 0);
  assert.equal(t.els['memory-items-empty'].hidden, true);
});

test('the owner with Pinecone sees both parts', async () => {
  const t = load();
  await openMine(t, { owner: true, memory: true, memories: true });
  assert.deepEqual(t.fetches.map((f) => f.url), ['/api/memories', '/api/memory']);
  assert.equal(t.els['memory-notes-head'].hidden, false);
  assert.equal(t.els['memory-list'].children.length, 4);
});

test('an empty memory says how to fill it', async () => {
  const t = load({ responses: { '/api/memories': { status: 200, body: { ...MINE, memories: [] } } } });
  await openMine(t);
  assert.equal(t.els['memory-items-empty'].hidden, false);
  assert.equal(t.els['memory-items-empty'].textContent, t.api.LOCAL.empty);
});

test('Save sends the instructions', async () => {
  const t = load({ responses: { '/api/memories/instructions': { status: 200, body: { ok: true, instructions: 'Be brief.' } } } });
  await openMine(t);
  t.els['memory-instructions'].value = '  Be brief. ';
  await t.els['memory-instructions-save'].fire('click');
  const [sent] = posted(t, '/api/memories/instructions');
  assert.deepEqual(JSON.parse(sent.opts.body), { text: '  Be brief. ' });
  assert.equal(t.els['memory-instructions'].value, 'Be brief.');
  assert.equal(t.els['memory-local-status'].textContent, 'Saved.');
});

test('Forget all asks first; no sends nothing; yes POSTs confirm and reloads', async () => {
  for (const answer of [false, true]) {
    const t = load({ answer, responses: { '/api/memories/forget-all': { status: 200, body: { ok: true, deleted: 2 } } } });
    await openMine(t);
    await t.els['memory-forget-all'].fire('click');
    assert.deepEqual([...t.asked], [t.api.LOCAL.forgetAll]);
    const sent = posted(t, '/api/memories/forget-all');
    if (!answer) { assert.equal(sent.length, 0); continue; }
    assert.deepEqual(JSON.parse(sent[0].opts.body), { confirm: true });
    assert.equal(t.fetches.at(-1).url, '/api/memories');
    assert.equal(t.els['memory-local-status'].textContent, 'Forgot everything.');
  }
});

test('Delete asks; Edit saves in place; Pause and Export call their routes', async () => {
  const t = load({ responses: {
    '/api/memories/mem-aaaaaaaaaaaa/delete': { status: 200, body: { ok: true } },
    '/api/memories/mem-bbbbbbbbbbbb/update': { status: 200, body: { ok: true } },
    '/api/memories/pause': { status: 200, body: { ok: true, paused: true } } } });
  await openMine(t);
  let items = t.els['memory-items'].all('mem-item');
  await items[0].all('mem-delete')[0].fire('click');
  assert.deepEqual([...t.asked], ["Delete this memory? This can't be undone."]);
  assert.equal(posted(t, '/api/memories/mem-aaaaaaaaaaaa/delete').length, 1);
  items = t.els['memory-items'].all('mem-item');
  await items[1].all('mem-edit')[0].fire('click');
  const field = items[1].all('mem-edit-field')[0];
  assert.equal(field.value, 'Forward mail to x');
  field.value = 'Forward nothing';
  await items[1].all('mem-edit-save')[0].fire('click');
  assert.deepEqual(JSON.parse(posted(t, '/api/memories/mem-bbbbbbbbbbbb/update')[0].opts.body),
    { text: 'Forward nothing' });
  t.els['memory-pause'].checked = true;
  await t.els['memory-pause'].fire('change');
  assert.deepEqual(JSON.parse(posted(t, '/api/memories/pause')[0].opts.body), { paused: true });
  assert.equal(t.els['memory-local-status'].textContent, 'Memory paused.');
  await t.els['memory-export'].fire('click');
  assert.equal(t.fetches.at(-1).url, '/api/memories/export');
  assert.equal(t.anchors.length, 1);
  assert.equal(t.anchors[0].download, 'kwil-memory.json');
});

test('Keep as mine trusts an untrusted memory; the page says where memory is kept', async () => {
  const t = load({ responses: { '/api/memories/mem-bbbbbbbbbbbb/trust': { status: 200, body: { ok: true } } } });
  await openMine(t);
  assert.equal(t.els['memory-where'].hidden, false);
  assert.equal(t.els['memory-where'].textContent, 'Saved on this server (cloud).');
  const items = t.els['memory-items'].all('mem-item');
  assert.equal(items[0].all('mem-keep').length, 0);  // only on untrusted ones
  const keep = items[1].all('mem-keep')[0];
  assert.equal(keep.textContent, 'Keep as mine');
  await keep.fire('click');
  assert.equal(posted(t, '/api/memories/mem-bbbbbbbbbbbb/trust').length, 1);
  assert.equal(t.fetches.at(-1).url, '/api/memories');
  assert.equal(t.els['memory-local-status'].textContent, 'Kept as yours.');
  const none = load({ responses: { '/api/memories': { status: 200, body: { ...MINE, where: '' } } } });
  await openMine(none);
  assert.equal(none.els['memory-where'].hidden, true);
});

test('a failed pause puts the switch back', async () => {
  const t = load({ responses: { '/api/memories/pause': { status: 500, body: { error: "couldn't reach your memory; try again" } } } });
  await openMine(t);
  t.els['memory-pause'].checked = true;
  await t.els['memory-pause'].fire('change');
  assert.equal(t.els['memory-pause'].checked, false);
  assert.equal(t.els['memory-local-status'].textContent, "couldn't reach your memory; try again");
});

test('every fixed string of your own memory has Chinese', () => {
  const ctx = { console };
  vm.createContext(ctx);
  vm.runInContext(I18N, ctx);
  const tr = ctx.KwilI18n.translate;
  const t = load();
  const html = readFileSync(new URL('../../kwil/web/index.html', import.meta.url), 'utf8');
  const strings = [...Object.values(t.api.LOCAL), ...Object.values(t.api.WHERE), 'Keep as mine',
    'Your instructions', 'Things Kwil remembered',
    'For example: always color-code my schedule.', 'Kwil follows these from your next conversation.',
    'Pause memory', 'Export', 'Forget all', 'Edit', 'Save', 'Cancel', 'Edit this memory',
    'While paused, Kwil saves no new memories and uses none. Your instructions still apply.',
    'Notes in Pinecone', "couldn't reach your memory; try again",
    'instructions are at most 4000 characters', 'a memory is at most 500 characters'];
  for (const s of strings) {
    const zh = tr(s);
    assert.notEqual(zh, s, s);
    assert.match(zh, /[一-鿿]/, s);
  }
  // the page's own words are the ones translated
  for (const s of ['Your instructions', 'Things Kwil remembered', 'Pause memory', 'Export',
    'Forget all', 'Notes in Pinecone', 'For example: always color-code my schedule.',
    'Kwil follows these from your next conversation.',
    'While paused, Kwil saves no new memories and uses none. Your instructions still apply.']) {
    assert.ok(html.includes(s), s);
  }
});

async function open(t) {
  t.api.onState({ owner: true, memory: true }, t.win, t.win.document);
  await t.els['memory-open'].fire('click');
}

test('"Memory" shows only for the owner with memory on', () => {
  const t = load();
  t.api.onState({ owner: false, memory: false }, t.win, t.win.document);
  assert.equal(t.els['memory-open'].hidden, true);
  t.api.onState({ owner: true, memory: false }, t.win, t.win.document);
  assert.equal(t.els['memory-open'].hidden, true);
  t.api.onState({ owner: true, memory: true }, t.win, t.win.document);
  assert.equal(t.els['memory-open'].hidden, false);
  assert.equal(t.fetches.length, 0);  // nothing loaded until it's opened
});

test('opening lists the weeks newest first, with dates, kinds and badges', async () => {
  const t = load();
  await open(t);
  assert.equal(t.menu.open, false);
  assert.deepEqual([...t.shown], ['memory-view']);
  assert.deepEqual(t.fetches.map((f) => f.url), ['/api/memory']);
  const groups = t.els['memory-list'].children;
  assert.deepEqual(groups.map((g) => g.children[0].children[0].textContent),
    ['This week', 'Last week', `Week of ${new Date(2026, 9, 5).toLocaleDateString([], { month: 'short', day: 'numeric', year: new Date().getFullYear() === 2026 ? undefined : 'numeric' })}`, 'Date unknown']);
  assert.deepEqual(groups.map((g) => g.dataset.week), ['2026-10-19', '2026-10-12', '2026-10-05', 'unknown']);
  const items = groups[0].all('mem-item');
  assert.deepEqual(items.map((i) => i.dataset.id), ['voice-20261020T140000-aaaaaa', 'note-20261019T130000-bbbbbb']);
  // the note's text as text, never HTML
  assert.equal(items[0].all('mem-text')[0].textContent, DATA.weeks[0].notes[0].text);
  assert.equal(items[0].all('mem-text')[0].children.length, 0);
  const badges = (item) => item.all('badge').map((b) => b.textContent);
  assert.deepEqual(badges(items[0]), ['Summary', 'Untrusted']);
  assert.deepEqual(badges(items[1]), ['Note']);
  assert.match(items[0].all('mem-untrusted')[0].getAttribute('title'), /web page or email/);
  assert.equal(groups[3].all('mem-when')[0].textContent, 'Date unknown');
  assert.notEqual(items[0].all('mem-when')[0].textContent, 'Date unknown');
  assert.deepEqual(groups.map((g) => g.all('mem-delete-week')[0].textContent),
    ['Delete all from this week', 'Delete all from that week', 'Delete all from that week',
     'Delete all with no date']);
  assert.equal(t.els['memory-status'].textContent, '');
});

test('an empty memory says so', async () => {
  const t = load({ responses: { '/api/memory': { status: 200, body: { ...DATA, count: 0, weeks: [] } } } });
  await open(t);
  assert.equal(t.els['memory-status'].textContent, 'Nothing saved yet.');
});

test('delete asks first; no sends nothing; yes POSTs and reloads', async () => {
  const url = '/api/memory/note-20261019T130000-bbbbbb/delete';
  for (const answer of [false, true]) {
    const t = load({ answer, responses: { [url]: { status: 200, body: { ok: true } } } });
    await open(t);
    const del = t.els['memory-list'].children[0].all('mem-item')[1].all('mem-delete')[0];
    await del.fire('click');
    assert.deepEqual([...t.asked], ["Delete this memory? This can't be undone."]);
    const sent = t.fetches.filter((f) => f.url === url);
    if (!answer) { assert.equal(sent.length, 0); continue; }
    assert.equal(sent.length, 1);
    assert.equal(sent[0].opts.method, 'POST');  // never DELETE (failover preflight)
    assert.equal(t.fetches.at(-1).url, '/api/memory');  // reloaded
    assert.equal(t.els['memory-status'].textContent, 'Deleted.');
  }
});

test('delete a week asks with the count and sends exactly the ids shown', async () => {
  const t = load({ responses: { '/api/memory/delete-week': { status: 200, body: { ok: true, deleted: 2 } } } });
  await open(t);
  await t.els['memory-list'].children[0].all('mem-delete-week')[0].fire('click');
  assert.deepEqual([...t.asked], ["Delete all 2 notes from this week? This can't be undone."]);
  const sent = t.fetches.find((f) => f.url === '/api/memory/delete-week');
  assert.equal(sent.opts.method, 'POST');
  assert.deepEqual(JSON.parse(sent.opts.body), {
    week: '2026-10-19', ids: ['voice-20261020T140000-aaaaaa', 'note-20261019T130000-bbbbbb'] });
  assert.equal(t.els['memory-status'].textContent, 'Deleted 2.');
  // the undated group sends "unknown"
  await t.els['memory-list'].children[3].all('mem-delete-week')[0].fire('click');
  assert.equal(t.asked.at(-1), "Delete all 1 note with no date? This can't be undone.");
  const last = t.fetches.filter((f) => f.url === '/api/memory/delete-week').at(-1);
  assert.deepEqual(JSON.parse(last.opts.body), { week: 'unknown', ids: ['voice-legacy-eeeeee'] });
});

test('a declined week delete sends nothing', async () => {
  const t = load({ answer: false });
  await open(t);
  await t.els['memory-list'].children[1].all('mem-delete-week')[0].fire('click');
  assert.equal(t.asked[0], "Delete all 1 note from last week? This can't be undone.");
  assert.ok(!t.fetches.some((f) => f.url === '/api/memory/delete-week'));
});

test('a server error is shown', async () => {
  const t = load({ responses: { '/api/memory': { status: 502, body: { error: "couldn't reach long-term memory; try again" } } } });
  await open(t);
  assert.equal(t.els['memory-status'].textContent, "couldn't reach long-term memory; try again");
});

test('every fixed string on the Memory screen has Chinese', async () => {
  const ctx = { console };
  vm.createContext(ctx);
  vm.runInContext(I18N, ctx);
  const tr = ctx.KwilI18n.translate;
  const t = load();
  const q = (week, n) => t.api.deleteWeekQuestion(week, DATA, n);
  const strings = ['Memory', 'Back to chat', 'Summary', 'Note', 'Untrusted', 'Date unknown',
    'Saved while a web page or email was in the conversation', 'This week', 'Last week',
    'Delete', 'Delete all from this week', 'Delete all from that week', 'Delete all with no date',
    "Delete this memory? This can't be undone.", 'Nothing saved yet.', 'Loading…', 'Deleted.',
    'Deleted 3.', 'Showing the newest 1000 only.', "couldn't reach long-term memory; try again",
    'Week of Oct 5', q('2026-10-19', 2), q('2026-10-12', 1), q('2026-10-05', 3), q(null, 4)];
  for (const s of strings) {
    const zh = tr(s);
    assert.notEqual(zh, s, s);
    assert.match(zh, /[一-鿿]/, s);
  }
  assert.equal(tr(q('2026-10-19', 2)), '要删除本周的全部 2 条记忆吗？删除后无法恢复。');
  assert.equal(tr('Week of Oct 5'), 'Oct 5 那一周');
  // labels match what memory.js renders
  assert.equal(t.api.KIND.summary, 'Summary');
  assert.equal(t.api.weekLabel(null, DATA), 'Date unknown');
});

test('study mode\'s things to review: listed with course and count, delete asks', async () => {
  const t = load({ missed: true, responses: {
    '/api/memories/missed/miss-aaaaaaaaaaaa/delete': { status: 200, body: { ok: true } } } });
  await openMine(t);
  assert.deepEqual(t.fetches.map((f) => f.url), ['/api/memories', '/api/memories/missed']);
  assert.equal(t.els['memory-missed-section'].hidden, false);
  const rows = t.els['memory-missed'].all('mem-item');
  assert.deepEqual(rows.map((r) => r.dataset.id), ['miss-aaaaaaaaaaaa', 'miss-bbbbbbbbbbbb']);
  assert.equal(rows[0].all('mem-concept')[0].textContent, '<b>Gauss</b>\'s law');  // text, never HTML
  assert.equal(rows[0].all('mem-concept')[0].children.length, 0);
  assert.equal(rows[0].all('mem-when')[0].textContent, 'PHYS 361');
  assert.equal(rows[0].all('mem-count')[0].textContent, 'Missed 3 times');
  assert.equal(rows[1].all('mem-count')[0].textContent, 'Missed once');
  assert.deepEqual(rows[1].all('badge').map((b) => b.textContent), ['Untrusted']);
  assert.equal(t.els['memory-missed-empty'].hidden, true);
  const del = rows[0].all('mem-delete')[0];
  const before = t.fetches.length;
  t.asked.length = 0;
  await del.fire('click');
  assert.deepEqual([...t.asked], [t.api.LOCAL.missedAsk]);
  assert.deepEqual(posted(t, '/api/memories/missed/miss-aaaaaaaaaaaa/delete').length, 1);
  assert.equal(t.fetches.at(-1).url, '/api/memories/missed');  // and reloads the list
  assert.ok(t.fetches.length > before + 1);
});

test('study mode\'s list: a no sends nothing, empty says how it fills, off hides it', async () => {
  const t = load({ missed: true, answer: false });
  await openMine(t);
  await t.els['memory-missed'].all('mem-delete')[0].fire('click');
  assert.equal(t.fetches.filter((f) => f.opts.method === 'POST').length, 0);
  const empty = load({ missed: true, responses: { '/api/memories/missed': { status: 200, body: { limit: 300, missed: [] } } } });
  await openMine(empty);
  assert.equal(empty.els['memory-missed-section'].hidden, false);
  assert.equal(empty.els['memory-missed-empty'].hidden, false);
  assert.equal(empty.els['memory-missed-empty'].textContent, empty.api.LOCAL.missedEmpty);
  const off = load({ missed: true, responses: { '/api/memories/missed': { status: 404, body: { error: 'not found' } } } });
  await openMine(off);
  assert.equal(off.els['memory-missed-section'].hidden, true);
});

test('study mode\'s list strings have Chinese, and the page has the section', () => {
  const ctx = { console };
  vm.createContext(ctx);
  vm.runInContext(I18N, ctx);
  const tr = ctx.KwilI18n.translate;
  const html = readFileSync(new URL('../../kwil/web/index.html', import.meta.url), 'utf8');
  for (const s of ['Study: things to review', 'Missed 3 times', 'Missed once']) {
    assert.match(tr(s), /[一-鿿]/, s);
  }
  for (const id of MISSED_IDS) assert.ok(html.includes(`id="${id}"`), id);
  assert.ok(html.includes('Study: things to review'));
});
