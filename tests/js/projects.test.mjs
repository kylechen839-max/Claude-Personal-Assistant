// node --test tests/js/projects.test.mjs  (Node 18+; no packages needed)
//
// Runs the phone page's kwil/web/projects.js in node:vm with a minimal fake
// DOM: "Projects" shows in the menu only with {"projects": true}; the list
// loads /api/projects and the suggestions (Create POSTs {name, course} and
// opens the new project); a project's page shows its files, memories and
// chats, Save POSTs the instructions, a file's Delete asks first, Add file
// uploads one file as a form, and "New chat in this project" asks app.js
// for a new chat in it. The header's pill shows the project's name (text,
// never HTML), its name opens the page and its × leaves the project.
// And app.js: a new chat's turns carry "project_id" (once "" after leaving),
// a bound pane's never do, and a turn's "done" sets the pane's project.
// Every fixed string has Chinese in i18n.js; the page wires it up.

import test from 'node:test';
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import vm from 'node:vm';

const SCRIPT = readFileSync(new URL('../../kwil/web/projects.js', import.meta.url), 'utf8');
const APP = readFileSync(new URL('../../kwil/web/app.js', import.meta.url), 'utf8');
const I18N = readFileSync(new URL('../../kwil/web/i18n.js', import.meta.url), 'utf8');
const HTML = readFileSync(new URL('../../kwil/web/index.html', import.meta.url), 'utf8');
const SW = readFileSync(new URL('../../kwil/web/sw.js', import.meta.url), 'utf8');

class El {
  constructor(tag, id = '', hidden = false) {
    this.tagName = tag.toUpperCase(); this.id = id; this.hidden = hidden;
    this.className = ''; this._text = ''; this.children = []; this.listeners = {};
    this.dataset = {}; this.attrs = {}; this.type = ''; this.value = ''; this.open = false;
  }
  get textContent() { return this._text + this.children.map((c) => c.textContent).join(''); }
  set textContent(v) { this._text = String(v); this.children = []; }
  set innerHTML(v) { throw new Error('never innerHTML'); }
  setAttribute(k, v) { this.attrs[k] = String(v); }
  getAttribute(k) { return k in this.attrs ? this.attrs[k] : null; }
  appendChild(c) { this.children.push(c); return c; }
  addEventListener(type, fn) { (this.listeners[type] ??= []).push(fn); }
  removeEventListener(type, fn) { this.listeners[type] = (this.listeners[type] || []).filter((f) => f !== fn); }
  async fire(type) { for (const fn of [...(this.listeners[type] || [])]) await fn({ preventDefault() {} }); }
  all(cls) {
    const out = [];
    for (const c of this.children) {
      if (String(c.className).split(' ').includes(cls)) out.push(c);
      out.push(...c.all(cls));
    }
    return out;
  }
  click() { this.clicked = (this.clicked || 0) + 1; }
  select() {}
  showModal() { this.open = true; if (this.onShow) this.onShow(this); }
}

const IDS = ['projects-open', 'projects-view', 'project-view', 'projects-list', 'projects-empty',
  'projects-status', 'projects-suggestions', 'projects-suggestions-empty', 'projects-new',
  'projects-back', 'project-title', 'project-back', 'project-chat', 'project-instructions',
  'project-instructions-count', 'project-instructions-save', 'project-files',
  'project-files-empty', 'project-add-file', 'project-file-input', 'project-memories',
  'project-memories-empty', 'project-chats', 'project-chats-empty', 'project-rename',
  'project-delete', 'project-status', 'project-pill', 'project-pill-text', 'project-leave',
  'project-name-dialog', 'project-name-title', 'project-name-input'];

const PROJECT = { id: 'prj-0123456789ab', name: 'PHYS 361', instructions: 'Use Taylor.',
  course: 'phys361', created: '2026-10-30T00:00:00Z', updated: '2026-10-30T00:00:00Z',
  memories: 1, files: 2 };
const PAGE = {
  project: PROJECT,
  files: [{ id: 'pfile-0123456789ab', name: 'syllabus.pdf', kind: 'pdf', size: 1_200_000,
    pages: 3, created: '2026-10-30T00:00:00Z' }],
  memories: [{ id: 'mem-0123456789ab', text: 'Exams are closed-book', created: '2026-10-30T00:00:00Z',
    untrusted: false }],
  conversations: [{ id: 'conv-aaaaaaaaaaaaaaaa', title: 'Lagrangians', updated_at: 1793000000 }],
};

function load({ answer = true, routes = {} } = {}) {
  const els = Object.fromEntries(IDS.map((id) => [id, new El(id.endsWith('-input') ? 'input' : 'div',
    id, ['projects-open', 'projects-view', 'project-view', 'project-pill'].includes(id))]));
  const menu = new El('details', 'menu');
  menu.open = true;
  const fetches = [], shown = [], chats = [], opened = [], asked = [];
  const answers = {
    '/api/projects': { projects: [PROJECT], limits: { instructions: 4000 } },
    '/api/projects/suggestions': { suggestions: [
      { name: 'CS 171', course: 'cs171', source: 'canvas', project: null },
      { name: 'PHYS 361', course: 'phys361', source: 'deadlines', project: PROJECT.id }] },
    [`/api/projects/${PROJECT.id}`]: PAGE,
    ...routes,
  };
  const win = {
    fetch: async (url, opts = {}) => {
      const isForm = opts.body instanceof FormData;
      fetches.push({ url, method: opts.method || 'GET', form: isForm ? opts.body : null,
        body: opts.body && !isForm ? JSON.parse(opts.body) : null });
      let body = answers[url];
      if (url === '/api/projects' && opts.method === 'POST') body = { project: { ...PROJECT, id: 'prj-bbbbbbbbbbbb' } };
      else if (opts.method === 'POST') body = { ok: true, project: PROJECT };
      return { ok: body !== undefined, status: body === undefined ? 404 : 200,
        json: async () => body ?? { error: 'not found' } };
    },
    confirm: (q) => { asked.push(q); return answer; },
    document: { getElementById: (id) => els[id] ?? null, createElement: (tag) => new El(tag),
      querySelector: (sel) => (sel === 'details.menu' ? menu : null) },
    FormData, JSON, String, Number, Boolean, Error, Math, Promise, Date,
    encodeURIComponent,
  };
  win.window = win;
  vm.createContext(win);
  vm.runInContext(SCRIPT, win);
  win.KwilProjects.configure({ show: (v) => shown.push(v), newChat: (p) => chats.push(p),
    openConversation: (id) => opened.push(id) });
  const flush = () => new Promise((r) => setImmediate(r));
  return { win, els, menu, fetches, shown, chats, opened, asked, flush, api: win.KwilProjects };
}

const state = (t, s) => t.api.onState(s, t.win, t.win.document);
// objects made inside the vm are from another realm: compare plain copies
const plain = (x) => JSON.parse(JSON.stringify(x));

test('the menu row shows only with projects, and the list loads with suggestions', async () => {
  const t = load();
  state(t, { projects: false });
  assert.equal(t.els['projects-open'].hidden, true);
  state(t, { projects: true });
  assert.equal(t.els['projects-open'].hidden, false);
  await t.els['projects-open'].fire('click');
  assert.equal(t.menu.open, false);
  assert.deepEqual(t.shown, ['projects-view']);
  assert.deepEqual(t.fetches.map((f) => f.url), ['/api/projects', '/api/projects/suggestions']);
  const [row] = t.els['projects-list'].all('project-row');
  assert.equal(row.textContent, 'PHYS 3612 files · 1 memory');
  const rows = t.els['projects-suggestions'].all('project-suggestion');
  assert.deepEqual(rows.map((r) => r.textContent), ['CS 171Create', 'PHYS 361Open']);
  await rows[0].children[1].fire('click');  // one tap: created and opened
  assert.deepEqual(t.fetches.find((f) => f.method === 'POST').body, { name: 'CS 171', course: 'cs171' });
  assert.equal(t.fetches.at(-1).url, '/api/projects/prj-bbbbbbbbbbbb');
  assert.equal(t.shown.at(-1), 'project-view');
});

test('a project page: files, memories, chats, Save, Delete asks, Add file, New chat', async () => {
  const t = load({ answer: false });
  state(t, { projects: true });
  await t.els['projects-open'].fire('click');
  await t.els['projects-list'].all('project-row')[0].fire('click');
  assert.equal(t.els['project-title'].textContent, 'PHYS 361');
  assert.equal(t.els['project-instructions'].value, 'Use Taylor.');
  assert.equal(t.els['project-instructions-count'].textContent, '11 / 4000');
  assert.match(t.els['project-files'].textContent, /^syllabus\.pdfPDF · 1\.2 MBDelete$/);
  assert.match(t.els['project-memories'].textContent, /Exams are closed-book$/);
  const [chat] = t.els['project-chats'].all('project-chat-row');
  assert.match(chat.textContent, /^Lagrangians/);
  await chat.fire('click');
  assert.deepEqual(t.opened, ['conv-aaaaaaaaaaaaaaaa']);
  t.els['project-instructions'].value = 'Use Goldstein.';
  await t.els['project-instructions-save'].fire('click');
  assert.deepEqual(t.fetches.at(-1), { url: `/api/projects/${PROJECT.id}/update`, method: 'POST',
    form: null, body: { instructions: 'Use Goldstein.' } });
  const before = t.fetches.length;
  await t.els['project-files'].all('mem-delete')[0].fire('click');  // asks; "no" sends nothing
  assert.equal(t.fetches.length, before);
  assert.deepEqual(t.asked, [t.api.TEXT.fileAsk]);
  t.els['project-file-input'].files = [new File(['hello'], 'notes.txt')];
  await t.els['project-file-input'].fire('change');
  const sent = t.fetches.find((f) => f.form);
  assert.equal(sent.url, `/api/projects/${PROJECT.id}/files`);
  assert.equal(sent.form.get('file').name, 'notes.txt');
  await t.els['project-chat'].fire('click');
  assert.deepEqual(plain(t.chats), [{ id: PROJECT.id, name: 'PHYS 361' }]);
  assert.equal(t.shown.at(-1), 'app');
});

test('rename uses the name sheet, delete asks and posts {confirm: true}', async () => {
  const t = load();
  state(t, { projects: true });
  await t.els['projects-open'].fire('click');
  await t.els['projects-list'].all('project-row')[0].fire('click');
  t.els['project-name-dialog'].onShow = (d) => {
    t.els['project-name-input'].value = '  PHYS 361 (Fall)  ';
    d.returnValue = 'save';
    queueMicrotask(() => d.fire('close'));
  };
  await t.els['project-rename'].fire('click');
  await t.flush();
  assert.equal(t.els['project-name-title'].textContent, 'Rename project');
  assert.deepEqual(t.fetches.at(-1).body, { name: 'PHYS 361 (Fall)' });
  await t.els['project-delete'].fire('click');
  assert.deepEqual(t.asked, [t.api.TEXT.deleteAsk]);
  assert.deepEqual(t.fetches.find((f) => f.url.endsWith('/delete')).body, { confirm: true });
  assert.equal(t.shown.at(-1), 'projects-view');
});

test('the header pill: the name as text, opens its page, the × leaves', async () => {
  const t = load();
  state(t, { projects: true });
  t.api.pill({ id: PROJECT.id, name: '<img src=x onerror=alert(1)>' }, t.win, t.win.document);
  assert.equal(t.els['project-pill'].hidden, false);
  assert.equal(t.els['project-pill-text'].textContent, '<img src=x onerror=alert(1)>');
  await t.els['project-pill-text'].fire('click');
  assert.equal(t.fetches.at(-1).url, `/api/projects/${PROJECT.id}`);
  await t.els['project-leave'].fire('click');
  assert.deepEqual(t.chats, [null]);
  t.api.pill(null, t.win, t.win.document);
  assert.equal(t.els['project-pill'].hidden, true);
  state(t, { projects: false });  // off for this person: no pill
  t.api.pill({ id: PROJECT.id, name: 'PHYS 361' }, t.win, t.win.document);
  assert.equal(t.els['project-pill'].hidden, true);
});

// --- app.js: the pane's project ----------------------------------------------------------

class AppEl {
  constructor(id = '') {
    this.id = id; this.hidden = false; this.disabled = false; this.value = '';
    this.textContent = ''; this.className = ''; this.dataset = {}; this.children = [];
    this.listeners = {}; this.scrollTop = 0; this.scrollHeight = 0; this.placeholder = '';
    this.classList = { add() {}, remove() {}, toggle() {}, contains: () => false };
  }
  appendChild(c) { this.children.push(c); return c; }
  addEventListener(type, fn) { (this.listeners[type] ??= []).push(fn); }
  setAttribute(k, v) { (this.attrs ??= {})[k] = String(v); }
  async fire(type) { for (const fn of this.listeners[type] || []) await fn({ preventDefault() {} }); }
  focus() {}
}

function ndjson(events) {
  const bytes = new TextEncoder().encode(events.map((e) => JSON.stringify(e) + '\n').join(''));
  let sent = false;
  return { ok: true, status: 200, body: { getReader: () => ({ read: async () => (sent ? { done: true }
    : (sent = true, { value: bytes, done: false })) }) } };
}

function loadApp(stateBody = { busy: false, confirm: null }) {
  const byId = {};
  const $ = (id) => (byId[id] ??= new AppEl(id));
  const fetches = [], replies = [], pills = [];
  const win = {
    document: { getElementById: $, createElement: () => new AppEl(), addEventListener() {},
      visibilityState: 'visible' },
    Audio: class { play() { return Promise.resolve(); } pause() {} },
    localStorage: { getItem: () => null, setItem() {} },
    navigator: {}, location: { hash: '', pathname: '/', origin: 'https://pc.example' },
    history: { replaceState() {} },
    KwilProjects: { configure() {}, onState() {}, pill: (p) => pills.push(p) },
    FormData, Blob, TextDecoder, setTimeout, clearTimeout, console,
    fetch: async (url, opts = {}) => {
      fetches.push({ url, opts });
      if (url === '/healthz') return { ok: true, status: 200, json: async () => ({}) };
      if (url === '/api/state') return { ok: true, status: 200, json: async () => stateBody };
      return replies.shift();
    },
  };
  win.window = win;
  vm.createContext(win);
  vm.runInContext(APP, win);
  const flush = () => new Promise((r) => setImmediate(r));
  async function submit(text) {
    $('text').value = text;
    await $('text-form').fire('submit');
    for (let i = 0; i < 5; i++) await flush();
  }
  const sent = () => fetches.filter((f) => f.url === '/api/turn').map((f) => JSON.parse(f.opts.body));
  return { win, $, replies, submit, sent, flush, pills };
}

test('a new chat in a project carries project_id; leaving sends "" once', async () => {
  const p = loadApp();
  await p.flush();
  p.win.KwilApp.newChat({ id: PROJECT.id, name: 'PHYS 361' });
  assert.deepEqual(plain(p.pills.at(-1)), { id: PROJECT.id, name: 'PHYS 361' });
  assert.equal(p.$('log').children.at(-1).textContent, 'New chat in PHYS 361.');
  p.replies.push(ndjson([{ type: 'reply', text: 'Hi.' },
    { type: 'done', conversation: 'c1', project: { id: PROJECT.id, name: 'PHYS 361' } }]));
  await p.submit('hello');
  assert.deepEqual(p.sent().at(-1), { text: 'hello', project_id: PROJECT.id });
  p.win.KwilApp.newChat(null);  // the pill's ×
  assert.equal(p.pills.at(-1), null);
  p.replies.push(ndjson([{ type: 'reply', text: 'Ok.' }, { type: 'done', conversation: 'c2' }]));
  await p.submit('other');
  assert.deepEqual(p.sent().at(-1), { text: 'other', project_id: '' });
  p.replies.push(ndjson([{ type: 'reply', text: 'Ok.' }, { type: 'done', conversation: 'c2' }]));
  await p.submit('again');
  assert.deepEqual(p.sent().at(-1), { text: 'again' });  // a plain chat says nothing
});

test('a project deleted elsewhere: its 404 leaves it, and the next message is in none', async () => {
  const p = loadApp();
  await p.flush();
  p.win.KwilApp.newChat({ id: PROJECT.id, name: 'PHYS 361' });
  p.replies.push({ ok: false, status: 404, json: async () => ({ error: 'not found' }) });
  await p.submit('hello');
  assert.deepEqual(p.sent().at(-1), { text: 'hello', project_id: PROJECT.id });
  assert.equal(p.pills.at(-1), null);
  p.replies.push(ndjson([{ type: 'reply', text: 'Hi.' }, { type: 'done', conversation: 'c1' }]));
  await p.submit('hello again');
  assert.deepEqual(p.sent().at(-1), { text: 'hello again', project_id: '' });
  // when the turn itself finds it gone, its "done" says no project
  p.win.KwilApp.newChat({ id: PROJECT.id, name: 'PHYS 361' });
  p.replies.push(ndjson([{ type: 'error', project_gone: true, message: 'gone' }, { type: 'done', conversation: null }]));
  await p.submit('once more');
  assert.equal(p.pills.at(-1), null);
});

test('a bound pane never sends a project; its done sets the pill', async () => {
  const p = loadApp();
  await p.flush();
  p.win.KwilApp.bindPane('conv-bbbbbbbbbbbbbbbb');
  p.replies.push(ndjson([{ type: 'reply', text: 'Hi.' },
    { type: 'done', conversation: 'conv-bbbbbbbbbbbbbbbb', project: { id: PROJECT.id, name: 'PHYS 361' } }]));
  await p.submit('continue');
  assert.deepEqual(p.sent().at(-1), { text: 'continue', conversation_id: 'conv-bbbbbbbbbbbbbbbb' });
  assert.deepEqual(plain(p.pills.at(-1)), { id: PROJECT.id, name: 'PHYS 361' });
  assert.deepEqual(plain(p.win.KwilApp.pane().project), { id: PROJECT.id, name: 'PHYS 361' });
  p.replies.push(ndjson([{ type: 'reply', text: 'Ok.' },
    { type: 'done', conversation: 'conv-bbbbbbbbbbbbbbbb', project: { id: PROJECT.id, name: 'PHYS 361' } }]));
  await p.submit('and then?');  // still bound: its conversation keeps its own project
  assert.deepEqual(p.sent().at(-1), { text: 'and then?', conversation_id: 'conv-bbbbbbbbbbbbbbbb' });
});

test('a fresh pane takes the chat\'s project from /api/state', async () => {
  const p = loadApp({ busy: false, confirm: null, project: { id: PROJECT.id, name: 'PHYS 361' } });
  for (let i = 0; i < 5; i++) await p.flush();
  assert.deepEqual(plain(p.pills.at(-1)), { id: PROJECT.id, name: 'PHYS 361' });
  p.replies.push(ndjson([{ type: 'reply', text: 'Hi.' },
    { type: 'done', conversation: 'c1', project: { id: PROJECT.id, name: 'PHYS 361' } }]));
  await p.submit('hello');
  assert.deepEqual(p.sent().at(-1), { text: 'hello', project_id: PROJECT.id });
});

test('every fixed string has Chinese, and the page, app.js and sw.js wire it up', () => {
  const ctx = { console };
  vm.createContext(ctx);
  vm.runInContext(I18N, ctx);
  const tr = ctx.KwilI18n.translate;
  const t = load();
  const strings = [...Object.values(t.api.TEXT), t.api.summary({ files: 2, memories: 1 }),
    t.api.summary({ files: 1, memories: 3 }), 'New chat in PHYS 361.', 'New chat.',
    'Text · 3 KB', 'Projects', 'New project', 'Suggested from your courses',
    'New chat in this project', 'Instructions', 'Files', 'Add file', 'Things Kwil remembered here',
    'Chats', 'Delete project', 'Open project', 'Leave project', 'Back to projects',
    'For example, PHYS 361', 'Kwil follows these in this project\'s chats, from the next one.',
    'Each project has its own instructions, files, memories and chats. What Kwil remembers in a project stays in it.',
    'A project file is a PDF or a text file (attach photos to a message instead)',
    'A project holds at most 20 files', 'You have 30 projects: delete one first'];
  for (const s of strings) {
    const zh = tr(s);
    assert.notEqual(zh, s, s);
    assert.match(zh, /[一-鿿]/, s);
  }
  for (const id of IDS) assert.ok(HTML.includes(`id="${id}"`), id);
  assert.ok(HTML.indexOf('/projects.js') < HTML.indexOf('/app.js'));
  assert.match(APP, /KwilProjects\.onState\(state\)/);
  assert.match(APP, /KwilProjects\.configure\(/);
  assert.match(APP, /KwilProjects\.pill\(pane\.project\)/);
  assert.ok(SW.includes('"/projects.js"') && SW.includes('kwil-shell-v28'));
});
