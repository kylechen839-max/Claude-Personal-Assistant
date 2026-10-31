// node --test tests/js/onboarding.test.mjs  (Node 18+; no packages needed)
//
// Beta onboarding on the phone page (kwil/web/family.js and i18n.js) in
// node:vm with a small fake DOM: a beta tester's starter credit line and
// key-guide link (and their Chinese), the invite's ready-to-send message
// in the share sheet and the clipboard, the first-run checklist (beta
// testers only, ticks kept in localStorage), the feedback sheet (POST, the
// 429 answer), the one-time survey card and its dismiss, and the owner's
// feedback list (other people's text set as text, never HTML).

import test from 'node:test';
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import vm from 'node:vm';

const WEB = new URL('../../kwil/web/', import.meta.url);
const FAMILY = readFileSync(new URL('family.js', WEB), 'utf8');
const I18N = readFileSync(new URL('i18n.js', WEB), 'utf8');
const HTML = readFileSync(new URL('index.html', WEB), 'utf8');

class El {
  constructor(tag, id = '', hidden = false) {
    this.tagName = tag.toUpperCase(); this.id = id; this.hidden = hidden;
    this.value = ''; this.children = []; this.listeners = {}; this.dataset = {};
    this.attrs = {}; this.open = false; this.disabled = false; this.checked = false;
    this.className = ''; this._text = '';
    this.classList = { set: new Set(), add: (c) => this.classList.set.add(c),
                       remove: (c) => this.classList.set.delete(c),
                       contains: (c) => this.classList.set.has(c) };
  }
  get textContent() {
    return this._text + this.children.map((c) => (typeof c === 'string' ? c : c.textContent)).join('');
  }
  set textContent(v) { this._text = String(v); this.children = []; }
  setAttribute(k, v) { this.attrs[k] = String(v); }
  getAttribute(k) { return k in this.attrs ? this.attrs[k] : null; }
  hasAttribute(k) { return k in this.attrs; }
  addEventListener(type, fn) { (this.listeners[type] ??= []).push(fn); }
  appendChild(c) { this.children.push(c); return c; }
  append(...cs) { this.children.push(...cs); }
  focus() { this.doc.active = this; }
  showModal() { this.open = true; }
  close() { if (!this.open) return; this.open = false; this.fire('close'); }
  async fire(type, ev = {}) {
    for (const fn of this.listeners[type] || []) await fn({ preventDefault() {}, target: this, ...ev });
  }
  all() { return this.children.flatMap((c) => (typeof c === 'string' ? [] : [c, ...c.all()])); }
  querySelector(sel) { return this.querySelectorAll(sel)[0] ?? null; }
  querySelectorAll(sel) {
    if (sel === 'input[data-step]') return this.all().filter((e) => e.tagName === 'INPUT' && e.getAttribute('data-step'));
    if (sel.startsWith('.')) return this.all().filter((e) => e.className.split(' ').includes(sel.slice(1)));
    return [];
  }
}

const IDS = [
  // Settings: the key, People, the feedback rows
  'app', 'api-key', 'api-key-form', 'api-key-input', 'api-key-save', 'api-key-remove', 'api-key-state',
  'api-key-cost', 'api-key-credit', 'api-key-help',
  'family', 'invite-open', 'invite-form', 'invite-name', 'invite-language', 'invite-tz', 'invite-status',
  'invite-link', 'family-list', 'family-credit',
  'feedback-send', 'feedback-read', 'feedback-sheet', 'feedback-form', 'feedback-title', 'feedback-submit',
  'feedback-status', 'feedback-broke', 'feedback-confused', 'feedback-pay',
  'feedback-due', 'feedback-due-open', 'feedback-due-dismiss',
  'feedback-list-sheet', 'feedback-list', 'feedback-list-status',
  // the chat screen's checklist
  'checklist', 'checklist-items', 'checklist-title', 'checklist-hide',
  'text',  // the composer's message field
];
const HIDDEN = new Set(['api-key', 'api-key-remove', 'api-key-cost', 'api-key-credit', 'api-key-help',
  'family', 'invite-form', 'invite-link', 'family-credit', 'feedback-send', 'feedback-read', 'feedback-due',
  'checklist']);

function load({ lang = 'en', responses = {}, share, clipboard = true, store = {}, throwStorage = false } = {}) {
  const doc = { active: null };
  const els = {};
  for (const id of IDS) {
    const tag = id.startsWith('feedback-') && /broke|confused|pay/.test(id) ? 'textarea'
      : id.endsWith('-sheet') ? 'dialog' : 'div';
    els[id] = new El(tag, id, HIDDEN.has(id));
    els[id].doc = doc;
  }
  for (const id of ['feedback-sheet', 'feedback-list-sheet']) {
    const close = new El('button');
    close.className = 'icon-btn conn-close';
    close.doc = doc;
    els[id].appendChild(close);
  }
  const menu = new El('details');
  const html = new El('html');
  html.attrs.lang = lang;
  const fetches = [], shared = [], copied = [];
  const local = { ...store };
  const storage = {
    getItem: (k) => { if (throwStorage) throw new Error('private'); return k in local ? local[k] : null; },
    setItem: (k, v) => { if (throwStorage) throw new Error('private'); local[k] = String(v); },
    removeItem: (k) => { delete local[k]; },
  };
  const made = [];
  const win = {
    location: { hash: '', pathname: '/', replace() {} },
    history: { replaceState() {} },
    localStorage: storage,
    navigator: {
      ...(share ? { share: async (data) => { shared.push(data); if (share !== true) throw share; } } : {}),
      clipboard: { writeText: async (t) => { if (!clipboard) throw new Error('no'); copied.push(t); } },
    },
    fetch: async (url, opts = {}) => {
      fetches.push({ url, method: opts.method || 'GET', body: opts.body });
      const r = typeof responses[url] === 'function' ? responses[url](opts) : responses[url] ?? { status: 404, body: {} };
      return { ok: r.status < 300, status: r.status, json: async () => r.body };
    },
    confirm: () => true,
    Intl,
    Date,
    document: Object.assign(doc, {
      documentElement: html,
      body: new El('body'),
      getElementById: (id) => els[id] ?? null,
      querySelector: (sel) => (sel === 'details.menu' ? menu : null),
      createElement: (tag) => { const e = new El(tag); e.doc = doc; made.push(e); return e; },
    }),
  };
  Object.defineProperty(doc, 'activeElement', { get: () => doc.active });
  win.window = win;
  vm.createContext(win);
  vm.runInContext(I18N, win);
  vm.runInContext(FAMILY, win);
  const flush = () => new Promise((r) => setTimeout(r, 0));
  return { win, els, fetches, shared, copied, local, made, flush, menu,
           api: win.KwilFamily, i18n: win.KwilI18n };
}

const BETA = { owner: false, api_key: 'required' };
const KEY = (extra = {}) => ({ status: 200, body: { set: false, last4: null, unreadable: false, required: true,
                                                     available: true, ...extra } });

// --- the starter credit in Settings -------------------------------------------

test('a beta tester on the credit sees what is left, and the key guide where it is served', async () => {
  const t = load({ responses: { '/api/me/anthropic-key': KEY({ credit_usd: 5, credit_left_usd: 4.371 }) } });
  t.api.onState({ ...BETA, key_help: '/help/anthropic-key' }, t.win, t.win.document);
  await t.flush();
  assert.equal(t.els['api-key-credit'].hidden, false);
  assert.equal(t.els['api-key-credit'].textContent, '$4.37 of $5 starter credit left');
  assert.equal(t.els['api-key-state'].textContent, t.api.BETA_TEXT.onCredit);
  assert.equal(t.els['api-key-help'].hidden, false);
  assert.equal(t.els['api-key-help'].getAttribute('href'), '/help/anthropic-key');
  // no public site here: no link to a page that would 404
  const off = load({ responses: { '/api/me/anthropic-key': KEY({ credit_usd: 5, credit_left_usd: 5 }) } });
  off.api.onState(BETA, off.win, off.win.document);
  await off.flush();
  assert.equal(off.els['api-key-credit'].textContent, '$5.00 of $5 starter credit left');
  assert.equal(off.els['api-key-help'].hidden, true);
  // an odd value for key_help is never used as a link
  const odd = load({ responses: { '/api/me/anthropic-key': KEY({ credit_usd: 5, credit_left_usd: 5 }) } });
  odd.api.onState({ ...BETA, key_help: 'https://evil.example/' }, odd.win, odd.win.document);
  await odd.flush();
  assert.equal(odd.els['api-key-help'].hidden, true);
});

test('the credit used up, a saved key, the credit off and a family member', async () => {
  const gone = load({ responses: { '/api/me/anthropic-key': KEY({ credit_usd: 5, credit_left_usd: 0 }) } });
  gone.api.onState(BETA, gone.win, gone.win.document);
  await gone.flush();
  assert.equal(gone.els['api-key-credit'].textContent, '$0.00 of $5 starter credit left');
  assert.equal(gone.els['api-key-state'].textContent, gone.api.BETA_TEXT.creditGone);
  const own = load({ responses: { '/api/me/anthropic-key': KEY({ set: true, last4: 'wxyz', credit_usd: 5,
                                                                   credit_left_usd: 3, month_usd: 0.4 }) } });
  own.api.onState({ ...BETA, key_help: '/help/anthropic-key' }, own.win, own.win.document);
  await own.flush();
  assert.equal(own.els['api-key-credit'].hidden, true);  // their own key: the credit doesn't matter
  assert.equal(own.els['api-key-help'].hidden, true);
  const off = load({ responses: { '/api/me/anthropic-key': KEY() } });  // beta_credit_usd = 0
  off.api.onState(BETA, off.win, off.win.document);
  await off.flush();
  assert.equal(off.els['api-key-credit'].hidden, true);
  assert.equal(off.els['api-key-state'].textContent, 'Add your key to start talking with Claude.');
  const fam = load({ responses: { '/api/me/anthropic-key': KEY({ required: false }) } });
  fam.api.onState({ owner: false, api_key: 'optional', key_help: '/help/anthropic-key' }, fam.win, fam.win.document);
  await fam.flush();
  assert.equal(fam.els['api-key-credit'].hidden, true);
  assert.equal(fam.els['api-key-help'].hidden, true);
});

test("on the credit the key's Connections row isn't flagged, and shows what is left", async () => {
  const t = load({ responses: { '/api/me/anthropic-key': KEY({ credit_usd: 5, credit_left_usd: 2.5 }) } });
  const marks = [];
  t.win.KwilConnections = { set: (id, info) => marks.push([id, JSON.parse(JSON.stringify(info))]) };
  t.api.onState(BETA, t.win, t.win.document);
  await t.flush();
  assert.deepEqual(marks.at(-1), ['anthropic', { state: 'available', status: 'Starter credit', action: 'Add key',
                                                 detail: '$2.50 of $5 starter credit left' }]);
  const gone = load({ responses: { '/api/me/anthropic-key': KEY({ credit_usd: 5, credit_left_usd: 0 }) } });
  const after = [];
  gone.win.KwilConnections = { set: (id, info) => after.push(info) };
  gone.api.onState(BETA, gone.win, gone.win.document);
  await gone.flush();
  assert.equal(after.at(-1).alert, true);  // used up: a key is needed now
});

test('the credit lines in Chinese', () => {
  const { i18n } = load({ lang: 'zh-CN' });
  assert.equal(i18n.translate('$4.37 of $5 starter credit left'), '新手额度还剩 $4.37（共 $5）');
  assert.equal(i18n.translate('$1.20 used'), '已用 $1.20');
  assert.equal(i18n.translate('Starter credit: $12.40 of $50 used'), '新手额度：已用 $12.40（共 $50）');
  assert.equal(i18n.translate('Starter credit'), '新手额度');
  // the brief's pattern, as written
  const pattern = /^\$(\d+\.\d\d) of \$(\d+) starter credit left$/;
  assert.ok(pattern.test('$4.37 of $5 starter credit left'));
  assert.ok(I18N.includes(String.raw`/^\$(\d+\.\d\d) of \$(\d+(?:\.\d\d)?) starter credit left$/`));
});

// --- the owner's People list ---------------------------------------------------

test("the owner's list shows each tester's credit used and everyone's total", async () => {
  const t = load({ responses: { '/api/users': { status: 200, body: {
    users: [{ id: 'u_a', name: 'Tester', status: 'active', tier: 'beta', credit_used_usd: 1.2 },
            { id: 'u_b', name: 'Mom', status: 'active', tier: 'family' }],
    max_users: 10, credit_total_used: 12.4, credit_total_cap: 50 } } } });
  t.api.onState({ owner: true, family: true }, t.win, t.win.document);
  await t.flush();
  const [tester, mom] = t.els['family-list'].children;
  assert.equal(tester.children[2].textContent, 'Active · Beta · $1.20 used');
  assert.equal(mom.children[2].textContent, 'Active');
  assert.equal(t.els['family-credit'].hidden, false);
  assert.equal(t.els['family-credit'].textContent, 'Starter credit: $12.40 of $50 used');
  // the credit off (cap 0): no dollar lines
  const off = load({ responses: { '/api/users': { status: 200, body: {
    users: [{ id: 'u_a', name: 'Tester', status: 'active', tier: 'beta', credit_used_usd: 0 }],
    credit_total_used: 0, credit_total_cap: 0 } } } });
  off.api.onState({ owner: true, family: true }, off.win, off.win.document);
  await off.flush();
  assert.equal(off.els['family-list'].children[0].children[2].textContent, 'Active · Beta');
  assert.equal(off.els['family-credit'].hidden, true);
});

// --- the invite's message ----------------------------------------------------------

test('the share sheet gets the invite text alone (it holds the link); the clipboard gets the text', async () => {
  const url = 'https://x.example/#invite=hcinv.abc';
  const message = `Hi Ada, here's your Kwil beta invite: ${url}\nYou have $5 of starter credit.`;
  const responses = {
    '/api/invites': { status: 200, body: { url, expires_at: 1, message } },
    '/api/users': { status: 200, body: { users: [{ id: 'u_a', name: 'Ada', status: 'invited', tier: 'beta' }] } },
    '/api/users/u_a/invite': { status: 200, body: { url, expires_at: 2, message } },
  };
  const s = load({ responses, share: true });
  s.api.onState({ owner: true, family: true }, s.win, s.win.document);
  s.els['invite-name'].value = 'Ada';
  await s.els['invite-form'].fire('submit');
  // {text, url} would show the link twice: the message already ends with it
  assert.equal(JSON.stringify(s.shared), JSON.stringify([{ text: message }]));
  assert.equal(s.els['invite-status'].textContent, 'Invite message ready.');
  const c = load({ responses });
  c.api.onState({ owner: true, family: true }, c.win, c.win.document);
  c.els['invite-name'].value = 'Ada';
  await c.els['invite-form'].fire('submit');
  assert.deepEqual(c.copied, [message]);
  assert.equal(c.els['invite-status'].textContent, 'Invite message copied.');
  // "New link" shares the new message too
  await c.flush();
  const again = c.els['family-list'].children[0].children.find((e) => e.textContent === 'New link');
  await again.fire('click');
  assert.deepEqual(c.copied, [message, message]);
});

// --- the first-run checklist ---------------------------------------------------

const TEMPLATE = 'https://five-leo-17d.notion.site/Kwil-Assignments-template-40af';
const CHAT = 'https://chat.example.org/beta';

test('the checklist shows only for a beta tester, with the template and chat links when set', () => {
  for (const state of [{ owner: true, family: true }, { owner: false, api_key: 'optional' }, {}]) {
    const t = load();
    t.i18n.renderChecklist(t.win, t.win.document, state);
    assert.equal(t.els.checklist.hidden, true, JSON.stringify(state));
  }
  const t = load();
  t.i18n.renderChecklist(t.win, t.win.document, { ...BETA, notion_template_url: TEMPLATE, beta_chat_url: CHAT });
  assert.equal(t.els.checklist.hidden, false);
  const items = t.els['checklist-items'].children;
  assert.equal(items.length, 4);
  assert.match(items[0].textContent, /Canvas or Notion/);
  assert.match(items[1].textContent, /What’s due this week\?/);
  assert.match(items[2].textContent, /voice mode/);
  assert.match(items[3].textContent, /beta testers' chat/);
  const links = t.made.filter((e) => e.tagName === 'A');
  assert.deepEqual(links.map((a) => a.href), [TEMPLATE, CHAT]);
  assert.ok(links.every((a) => a.rel === 'noopener noreferrer' && a.target === '_blank'));
  // no chat link and no template: three steps, no links; a non-https link is dropped
  const bare = load();
  bare.i18n.renderChecklist(bare.win, bare.win.document,
    { ...BETA, notion_template_url: 'javascript:alert(1)', beta_chat_url: 'http://chat.example.org' });
  assert.equal(bare.els['checklist-items'].children.length, 3);
  assert.equal(bare.made.filter((e) => e.tagName === 'A').length, 0);
});

test('family.js draws the checklist on every /api/state', async () => {
  const t = load({ responses: { '/api/me/anthropic-key': KEY() } });
  t.api.onState(BETA, t.win, t.win.document);
  assert.equal(t.els.checklist.hidden, false);
  t.api.onState({ owner: true, family: true }, t.win, t.win.document);
  assert.equal(t.els.checklist.hidden, true);
});

test('ticks are kept in localStorage; all ticked or Hide puts it away for good', async () => {
  const t = load();
  t.i18n.renderChecklist(t.win, t.win.document, BETA);
  const boxes = t.els['checklist-items'].querySelectorAll('input[data-step]');
  assert.equal(boxes.length, 3);
  boxes[1].checked = true;
  await boxes[1].fire('change');
  assert.deepEqual(JSON.parse(t.local.betaChecklist), ['ask']);
  // drawn again (another /api/state): the same boxes, the tick kept
  t.i18n.renderChecklist(t.win, t.win.document, BETA);
  assert.equal(t.els['checklist-items'].querySelectorAll('input[data-step]')[1], boxes[1]);
  assert.equal(boxes[1].checked, true);
  boxes[0].checked = true; await boxes[0].fire('change');
  boxes[2].checked = true; await boxes[2].fire('change');
  assert.equal(t.els.checklist.hidden, true);
  const later = load({ store: { ...t.local } });
  later.i18n.renderChecklist(later.win, later.win.document, BETA);
  assert.equal(later.els.checklist.hidden, true);
  // Hide
  const h = load();
  h.i18n.renderChecklist(h.win, h.win.document, BETA);
  await h.els['checklist-hide'].fire('click');
  assert.equal(h.els.checklist.hidden, true);
  assert.equal(h.local.betaChecklistHidden, '1');
  // private mode (localStorage throws): it still shows and ticks
  const p = load({ throwStorage: true });
  p.i18n.renderChecklist(p.win, p.win.document, BETA);
  assert.equal(p.els.checklist.hidden, false);
  const pb = p.els['checklist-items'].querySelectorAll('input[data-step]')[0];
  pb.checked = true;
  await pb.fire('change');
});

test('the checklist in Chinese', () => {
  const t = load({ lang: 'zh-CN' });
  t.i18n.renderChecklist(t.win, t.win.document, { ...BETA, notion_template_url: TEMPLATE });
  const text = t.els.checklist.textContent + t.els['checklist-items'].textContent;
  assert.match(text, /连接 Canvas 或 Notion/);
  assert.match(text, /这周有什么要交的/);
  assert.match(text, /语音模式/);
  assert.match(text, /Notion 模板/);
  assert.doesNotMatch(t.els['checklist-items'].textContent, /What’s due|voice mode|template|Settings/);
  assert.equal(t.els['checklist-title'].textContent, '可以先试试这些');
  assert.equal(t.els['checklist-hide'].textContent, '隐藏');
});

// --- feedback ----------------------------------------------------------------

test('"Send feedback" shows for testers and family, "Feedback" for the owner', () => {
  const t = load();
  t.api.onState(BETA, t.win, t.win.document);
  assert.equal(t.els['feedback-send'].hidden, false);
  assert.equal(t.els['feedback-read'].hidden, true);
  t.api.onState({ owner: true, family: true }, t.win, t.win.document);
  assert.equal(t.els['feedback-send'].hidden, true);
  assert.equal(t.els['feedback-read'].hidden, false);
  t.api.onState({ owner: true, family: false }, t.win, t.win.document);
  assert.equal(t.els['feedback-read'].hidden, true);
});

test('the feedback sheet POSTs the three answers once, then thanks; nothing sent when empty', async () => {
  const t = load({ responses: { '/api/me/feedback': { status: 200, body: { ok: true, id: 'fb_1' } } } });
  t.api.onState(BETA, t.win, t.win.document);
  t.els['feedback-send'].focus();
  await t.els['feedback-send'].fire('click');
  assert.equal(t.els['feedback-sheet'].open, true);
  assert.equal(t.win.document.activeElement, t.els['feedback-broke']);  // focus into the sheet
  assert.equal(t.els['feedback-title'].textContent, 'Send feedback');
  await t.els['feedback-form'].fire('submit');
  assert.equal(t.fetches.filter((f) => f.url === '/api/me/feedback').length, 0);
  assert.equal(t.els['feedback-status'].textContent, 'Write something first.');
  t.els['feedback-broke'].value = ' The mic stopped ';
  t.els['feedback-pay'].value = 'Canvas sync';
  await t.els['feedback-form'].fire('submit');
  const sent = t.fetches.filter((f) => f.url === '/api/me/feedback');
  assert.equal(sent.length, 1);
  assert.equal(sent[0].method, 'POST');
  assert.deepEqual(JSON.parse(sent[0].body), { kind: 'general', broke: 'The mic stopped', confused: '',
                                               pay: 'Canvas sync' });
  assert.equal(t.els['feedback-status'].textContent, 'Thanks. Your feedback was sent.');
  assert.equal(t.els['feedback-broke'].value, '');
  // Close: the sheet shuts and focus goes back to the row
  await t.els['feedback-sheet'].children[0].fire('click');
  assert.equal(t.els['feedback-sheet'].open, false);
  assert.equal(t.win.document.activeElement, t.els['feedback-send']);
});

test('a 429 (and the other refusals) keep the answers and say why', async () => {
  for (const [status, key] of [[429, 'tooMany'], [409, 'full'], [413, 'tooLong'], [503, 'noStore'], [500, 'failed']]) {
    const t = load({ responses: { '/api/me/feedback': { status, body: { error: "thanks! that's plenty" } } } });
    t.api.onState(BETA, t.win, t.win.document);
    await t.els['feedback-send'].fire('click');
    t.els['feedback-confused'].value = 'Where is voice mode?';
    await t.els['feedback-form'].fire('submit');
    assert.equal(t.els['feedback-status'].textContent, t.api.FEEDBACK_TEXT[key], String(status));
    assert.equal(t.els['feedback-confused'].value, 'Where is voice mode?');  // not lost
    assert.equal(t.els['feedback-submit'].disabled, false);
  }
  assert.ok(!Object.values(load().api.FEEDBACK_TEXT).some((s) => s.includes('!')));
});

test('the survey card: shown when due, answered as a survey, dismissed for good', async () => {
  const t = load({ responses: {
    '/api/me/feedback': { status: 200, body: { ok: true, id: 'fb_2' } },
    '/api/me/feedback/dismiss': { status: 200, body: { ok: true } } } });
  t.api.onState({ ...BETA, feedback_due: false }, t.win, t.win.document);
  assert.equal(t.els['feedback-due'].hidden, true);
  t.api.onState({ ...BETA, feedback_due: true }, t.win, t.win.document);
  assert.equal(t.els['feedback-due'].hidden, false);
  await t.els['feedback-due-open'].fire('click');
  assert.equal(t.els['feedback-title'].textContent, "How's the beta going?");
  t.els['feedback-broke'].value = 'Nothing';
  await t.els['feedback-form'].fire('submit');
  assert.equal(JSON.parse(t.fetches.at(-1).body).kind, 'survey');
  assert.equal(t.els['feedback-due'].hidden, true);  // answered
  // the owner never gets the card
  t.api.onState({ owner: true, family: true, feedback_due: true }, t.win, t.win.document);
  assert.equal(t.els['feedback-due'].hidden, true);

  const d = load({ responses: { '/api/me/feedback/dismiss': { status: 200, body: { ok: true } } } });
  d.api.onState({ ...BETA, feedback_due: true }, d.win, d.win.document);
  await d.els['feedback-due-dismiss'].fire('click');
  assert.equal(d.els['feedback-due'].hidden, true);
  const posts = d.fetches.filter((f) => f.url === '/api/me/feedback/dismiss');
  assert.equal(posts.length, 1);
  assert.equal(posts[0].method, 'POST');
  // a stale /api/state (sent before the dismiss landed) doesn't bring it back
  d.api.onState({ ...BETA, feedback_due: true }, d.win, d.win.document);
  assert.equal(d.els['feedback-due'].hidden, true);
  // offline: still hidden on this page
  const off = load({ responses: { '/api/me/feedback/dismiss': () => { throw new Error('offline'); } } });
  off.api.onState({ ...BETA, feedback_due: true }, off.win, off.win.document);
  await off.els['feedback-due-dismiss'].fire('click');
  assert.equal(off.els['feedback-due'].hidden, true);
});

test("the owner's feedback list sets everyone's words as text, newest first", async () => {
  const evil = '<img src=x onerror=alert(1)>';
  const t = load({ responses: { '/api/feedback': { status: 200, body: {
    owner: [{ id: 'fb_o', kind: 'general', at: 1000, broke: 'my own note', confused: '', pay: '' }],
    people: [{ id: 'u_a', name: `Ada ${evil}`, tier: 'beta', entries: [
      { id: 'fb_1', kind: 'survey', at: 3000, broke: evil, confused: '<b>bold</b>', pay: 'a & b' }]}] } } } });
  t.api.onState({ owner: true, family: true }, t.win, t.win.document);
  await t.els['feedback-read'].fire('click');
  await t.flush();
  assert.equal(t.els['feedback-list-sheet'].open, true);
  const items = t.els['feedback-list'].children;
  assert.equal(items.length, 2);
  const [first, second] = items;
  assert.equal(first.children[0].children[0].textContent, `Ada ${evil}`);  // newest first
  assert.equal(first.children[0].children[2].textContent, 'Survey');
  assert.deepEqual(first.children.slice(1).map((e) => e.textContent),
    ['What broke?', evil, 'What confused you?', '<b>bold</b>', 'What would you pay for?', 'a & b']);
  assert.equal(second.children[0].children[0].textContent, 'You');
  assert.equal(second.children.length, 3);  // the empty answers left out
  // only text nodes: nothing was parsed as HTML
  assert.ok(t.made.every((e) => ['LI', 'P', 'SPAN', 'BUTTON'].includes(e.tagName)), t.made.map((e) => e.tagName).join());
  assert.equal(t.els['feedback-list-status'].textContent, '');
  for (const src of [FAMILY, I18N]) assert.doesNotMatch(src, /innerHTML|insertAdjacentHTML|outerHTML/);
});

test('the owner list: empty, and a failed load', async () => {
  const e = load({ responses: { '/api/feedback': { status: 200, body: { owner: [], people: [] } } } });
  e.api.onState({ owner: true, family: true }, e.win, e.win.document);
  await e.els['feedback-read'].fire('click');
  assert.equal(e.els['feedback-list-status'].textContent, 'No feedback yet.');
  const f = load({ responses: { '/api/feedback': { status: 500, body: {} } } });
  f.api.onState({ owner: true, family: true }, f.win, f.win.document);
  await f.els['feedback-read'].fire('click');
  assert.equal(f.els['feedback-list-status'].textContent, "Couldn't load feedback. Try again.");
});

// --- the page -------------------------------------------------------------------

test('the page has the new controls, each labelled, and the sheets are dialogs', () => {
  for (const id of ['api-key-credit', 'api-key-help', 'family-credit', 'feedback-send', 'feedback-read',
                    'feedback-due', 'feedback-due-open', 'feedback-due-dismiss', 'checklist', 'checklist-items',
                    'checklist-hide']) {
    assert.match(HTML, new RegExp(`id="${id}"`), id);
  }
  assert.match(HTML, /<dialog id="feedback-sheet" class="conn-sheet fb-sheet" aria-labelledby="feedback-title">/);
  assert.match(HTML, /<dialog id="feedback-list-sheet" class="conn-sheet fb-sheet" aria-labelledby="feedback-list-title">/);
  for (const k of ['broke', 'confused', 'pay']) {
    assert.match(HTML, new RegExp(`<label for="feedback-${k}">`), k);
    assert.match(HTML, new RegExp(`<textarea id="feedback-${k}" rows="3" maxlength="2000">`), k);
  }
  // the guide's link is same-site and opens apart from the app
  assert.match(HTML, /<a id="api-key-help" class="api-key-help" href="\/help\/anthropic-key" target="_blank" rel="noopener" hidden>/);
});

// --- judge round 1 ---------------------------------------------------------------

test('a second submit while the POST is out sends nothing more', async () => {
  let release;
  const pending = new Promise((r) => { release = r; });
  const t = load({ responses: { '/api/me/feedback': () => ({ status: 200, body: { ok: true } }) } });
  const realFetch = t.win.fetch;
  t.win.fetch = async (url, opts) => { if (url === '/api/me/feedback') await pending; return realFetch(url, opts); };
  t.api.onState(BETA, t.win, t.win.document);
  await t.els['feedback-send'].fire('click');
  t.els['feedback-broke'].value = 'twice?';
  const first = t.els['feedback-form'].fire('submit');
  const second = t.els['feedback-form'].fire('submit');  // Enter again, before the answer
  release();
  await Promise.all([first, second]);
  assert.equal(t.fetches.filter((f) => f.url === '/api/me/feedback').length, 1);
  assert.equal(t.els['feedback-status'].textContent, 'Thanks. Your feedback was sent.');
  // and once it is back, another note can go
  t.els['feedback-broke'].value = 'again';
  await t.els['feedback-form'].fire('submit');
  assert.equal(t.fetches.filter((f) => f.url === '/api/me/feedback').length, 2);
});

test('a card that hides with focus in it hands focus to the message field', async () => {
  const d = load({ responses: { '/api/me/feedback/dismiss': { status: 200, body: { ok: true } } } });
  d.api.onState({ ...BETA, feedback_due: true }, d.win, d.win.document);
  d.els['feedback-due-dismiss'].focus();
  await d.els['feedback-due-dismiss'].fire('click');
  assert.equal(d.win.document.activeElement, d.els.text, 'No thanks');

  const h = load();
  h.i18n.renderChecklist(h.win, h.win.document, BETA);
  h.els['checklist-hide'].focus();
  await h.els['checklist-hide'].fire('click');
  assert.equal(h.win.document.activeElement, h.els.text, 'Hide');

  const t = load();
  t.i18n.renderChecklist(t.win, t.win.document, BETA);
  const boxes = t.els['checklist-items'].querySelectorAll('input[data-step]');
  for (const b of boxes.slice(0, -1)) { b.checked = true; await b.fire('change'); }
  const last = boxes.at(-1);
  last.focus();
  assert.equal(t.win.document.activeElement, last);  // not moved before the card goes
  last.checked = true;
  await last.fire('change');
  assert.equal(t.els.checklist.hidden, true);
  assert.equal(t.win.document.activeElement, t.els.text, 'the last tick');
});
