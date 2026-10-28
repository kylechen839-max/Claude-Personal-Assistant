// node --test tests/js/canvas-helper-ui.test.mjs  (Node 18+; no packages needed)
//
// The Kwil Canvas helper's wiring and popup: background.js adds its
// listeners at the top level (so Chrome wakes the worker for them) and
// answers messages asynchronously; the popup shows status as text only,
// never the key, offers "Enable on this site" only on a custom https
// domain (asking Chrome for that one origin from the click), and unpairs
// only on a second click.

import test from 'node:test';
import assert from 'node:assert/strict';

import { CANVAS, KEY, KWIL, fakeChrome, popupSender } from './canvas-helper-fakes.mjs';

function events() {
  const listeners = [];
  return { listeners, addListener: (fn) => listeners.push(fn) };
}

test('background.js wires every event at the top level', async () => {
  const { chrome, store } = fakeChrome();
  chrome.runtime.onInstalled = events();
  chrome.runtime.onStartup = events();
  chrome.runtime.onMessage = events();
  chrome.alarms.onAlarm = events();
  chrome.permissions.onRemoved = events();
  globalThis.chrome = chrome;
  try {
    await import('../../tools/canvas-helper/background.js');
  } finally {
    delete globalThis.chrome;
  }
  for (const e of [chrome.runtime.onInstalled, chrome.runtime.onStartup, chrome.runtime.onMessage,
                   chrome.alarms.onAlarm, chrome.permissions.onRemoved]) {
    assert.equal(e.listeners.length, 1);
  }
  store.pairing = { key: KEY, origin: KWIL };
  const answer = await new Promise((resolve) => {
    const async = chrome.runtime.onMessage.listeners[0]({ type: 'status' }, popupSender(), resolve);
    assert.equal(async, true);
  });
  assert.equal(answer.paired, true);
  assert.ok(!JSON.stringify(answer).includes(KEY));
});

class El {
  constructor(id) {
    this.id = id; this.hidden = false; this.textContent = ''; this.children = [];
    this.listeners = {};
  }
  addEventListener(type, fn) { (this.listeners[type] ??= []).push(fn); }
  replaceChildren(...kids) { this.children = kids; }
  set innerHTML(v) { throw new Error('innerHTML used'); }
  async click() { for (const fn of this.listeners.click || []) await fn(); }
}

async function popup({ tabUrl, status, allowed = () => true }) {
  const { start } = await import('../../tools/canvas-helper/popup.js');
  const els = {};
  const doc = {
    getElementById: (id) => (els[id] ??= new El(id)),
    createElement: (tag) => new El(tag),
  };
  const sent = [];
  const requested = [];
  const opened = [];
  let current = status;
  const chrome = {
    runtime: { id: 'x', sendMessage: async (m) => {
      sent.push(m);
      if (m.type === 'status') return current;
      if (m.type === 'unpair') current = { paired: false };
      return { ok: true };
    } },
    tabs: { query: async () => [{ id: 7, url: tabUrl }],
            create: async (o) => { opened.push(o.url); } },
    permissions: { request: async (p) => { requested.push(p); return true; },
                   contains: async ({ origins }) => origins.every(allowed) },
  };
  await start(doc, chrome, () => Date.parse('2026-10-27T12:00:00Z'));
  return { els, sent, requested, opened };
}

const PAIRED = { paired: true, kwil: KWIL, canvas: CANVAS, lastSync: '2026-10-27T11:00:00Z',
                 result: 'ok', counts: { courses: 2, files: 1 }, canvasSites: [],
                 kwilSites: [] };
const SCHOOL = 'https://canvas.school.edu';
const OWN_KWIL = 'https://kwil.example.org';
const settle = () => new Promise((r) => setTimeout(r, 0));
const boxes = (p) => ['canvas-box', 'kwil-box', 'switch-box', 'access-box']
  .filter((id) => p.els[id] && !p.els[id].hidden);

test('the popup shows status as text and never the key', async () => {
  const p = await popup({ tabUrl: `${CANVAS}/courses/111`, status: PAIRED });
  assert.equal(p.els.title.textContent, 'Paired');
  assert.deepEqual(p.els.lines.children.map((li) => li.textContent),
                   ['Kwil: heykwil.com', 'Canvas: school.instructure.com', 'Last sync: 1 h ago',
                    '2 course(s), 1 file(s) sent']);
  assert.equal(p.els['paired-box'].hidden, false);
  assert.deepEqual(boxes(p), []);    // *.instructure.com needs no click
  const none = await popup({ tabUrl: 'chrome://extensions', status: { paired: false } });
  assert.equal(none.els.title.textContent, 'Not paired');
  assert.equal(none.els['paired-box'].hidden, true);
  assert.deepEqual(boxes(none), []);
});

test('a school Canvas domain: one click asks Chrome for that origin and enables Canvas only', async () => {
  const p = await popup({ tabUrl: `${SCHOOL}/courses/1`, status: PAIRED });
  assert.deepEqual(boxes(p), ['canvas-box', 'kwil-box']);
  await p.els['enable-canvas'].click();
  await settle();
  assert.deepEqual(p.requested, [{ origins: [`${SCHOOL}/*`] }]);
  assert.deepEqual(p.sent.find((m) => m.type.startsWith('enable')),
                   { type: 'enable-canvas', origin: SCHOOL, tabId: 7 });
  for (const url of [`http://canvas.school.edu/`, `${KWIL}/`]) {
    const q = await popup({ tabUrl: url, status: PAIRED });
    assert.deepEqual(boxes(q), [], url);
  }
  const known = await popup({ tabUrl: `${SCHOOL}/`, status: { ...PAIRED, canvasSites: [SCHOOL] } });
  assert.deepEqual(boxes(known), []);
});

test('"This is my Kwil" names the host and asks Chrome only on the second click', async () => {
  const p = await popup({ tabUrl: `${OWN_KWIL}/`, status: PAIRED });
  await p.els['enable-kwil'].click();
  assert.deepEqual(p.requested, []);
  assert.equal(p.els['kwil-ask'].textContent, 'Send your Canvas courses to kwil.example.org?');
  assert.equal(p.els['enable-kwil'].textContent, 'Yes, send them to kwil.example.org');
  await p.els['enable-kwil'].click();
  await settle();
  assert.deepEqual(p.requested, [{ origins: [`${OWN_KWIL}/*`] }]);
  assert.deepEqual(p.sent.find((m) => m.type.startsWith('enable')),
                   { type: 'enable-kwil', origin: OWN_KWIL, tabId: 7 });
});

test('on another Kwil while paired: Switch names both hosts', async () => {
  const p = await popup({ tabUrl: `${KWIL}/`, status: { ...PAIRED, kwil: OWN_KWIL } });
  assert.deepEqual(boxes(p), ['switch-box']);
  assert.equal(p.els['switch-ask'].textContent,
               'Paired with kwil.example.org. Send your Canvas courses to heykwil.com instead?');
  await p.els.switch.click();
  assert.deepEqual(p.sent.find((m) => m.type === 'switch-kwil'),
                   { type: 'switch-kwil', origin: KWIL, tabId: 7 });
  const same = await popup({ tabUrl: `${KWIL}/`, status: PAIRED });
  assert.deepEqual(boxes(same), []);
});

test('a lost permission: Allow on a custom site, Chrome\'s Site access for a built-in one', async () => {
  const st = { ...PAIRED, canvas: SCHOOL, canvasSites: [SCHOOL], result: 'needs_permission' };
  const p = await popup({ tabUrl: `${SCHOOL}/`, status: st, allowed: () => false });
  assert.deepEqual(boxes(p), ['access-box']);
  assert.equal(p.els.regrant.hidden, false);
  assert.match(p.els.lines.children.map((li) => li.textContent).join(' '),
               /click the helper's icon there/);
  await p.els.regrant.click();
  await settle();
  assert.deepEqual(p.requested, [{ origins: [`${SCHOOL}/*`] }]);
  assert.ok(p.sent.some((m) => m.type === 'enable-canvas' && m.origin === SCHOOL));
  const builtIn = await popup({ tabUrl: `${CANVAS}/`, status: { ...PAIRED, result: 'needs_permission' },
                                allowed: () => false });
  assert.deepEqual(boxes(builtIn), ['access-box']);
  assert.equal(builtIn.els.regrant.hidden, true);
  assert.match(builtIn.els['access-text'].textContent, /Site access/);
  const fine = await popup({ tabUrl: `${SCHOOL}/`, status: st });
  assert.deepEqual(boxes(fine), []);
});

test('Unpair needs a second click; Revoke in Kwil opens the paired Kwil', async () => {
  const p = await popup({ tabUrl: `${CANVAS}/`, status: PAIRED });
  await p.els.unpair.click();
  assert.ok(!p.sent.some((m) => m.type === 'unpair'));
  assert.equal(p.els.unpair.textContent, 'Click again to unpair');
  await p.els.unpair.click();
  assert.ok(p.sent.some((m) => m.type === 'unpair'));
  assert.equal(p.els.title.textContent, 'Not paired');
  const q = await popup({ tabUrl: `${CANVAS}/`, status: PAIRED });
  await q.els.revoke.click();
  assert.deepEqual(q.opened, [`${KWIL}/`]);
});
