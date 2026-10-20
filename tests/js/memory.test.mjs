// node --test tests/js/memory.test.mjs  (Node 18+; no packages needed)
//
// Runs the phone page's assistant/web/memory.js in node:vm with a minimal
// fake DOM: "Memory" shows in the menu only for the owner with memory on;
// opening it loads /api/memory and shows the weeks (This week / Last week /
// Week of ... / Date unknown) with each note's date, kind and an
// "Untrusted" badge; Delete asks first and POSTs .../delete; "Delete all
// from this week" asks with the count and POSTs exactly the ids shown.
// And every fixed string has Chinese in i18n.js.

import test from 'node:test';
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import vm from 'node:vm';

const SCRIPT = readFileSync(new URL('../../assistant/web/memory.js', import.meta.url), 'utf8');
const I18N = readFileSync(new URL('../../assistant/web/i18n.js', import.meta.url), 'utf8');

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

function load({ answer = true, responses = {} } = {}) {
  const ids = ['memory-view', 'memory-list', 'memory-status', 'memory-open', 'memory-back'];
  const els = Object.fromEntries(ids.map((id) => [id, new El('div', id, id !== 'memory-list' && id !== 'memory-status')]));
  const menu = new El('details', 'menu');
  menu.open = true;
  const fetches = [];
  const asked = [];
  const shown = [];
  const win = {
    fetch: async (url, opts = {}) => {
      fetches.push({ url, opts });
      const r = responses[url] ?? (url === '/api/memory' ? { status: 200, body: DATA } : { status: 404, body: { error: 'not found' } });
      return { ok: r.status < 300, status: r.status, json: async () => r.body };
    },
    confirm: (q) => { asked.push(q); return answer; },
    document: {
      getElementById: (id) => els[id] ?? null,
      querySelector: (sel) => (sel === 'details.menu' ? menu : null),
      createElement: (tag) => new El(tag),
    },
    Date, JSON, encodeURIComponent, Error, String, Boolean, Object,
  };
  win.window = win;
  vm.createContext(win);
  vm.runInContext(SCRIPT, win);
  win.HeyClaudeMemory.configure({ show: (v) => shown.push(v) });
  return { win, els, menu, fetches, asked, shown, api: win.HeyClaudeMemory };
}

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
  const tr = ctx.HeyClaudeI18n.translate;
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
