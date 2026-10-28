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
  let level = null;
  chrome.storage.local.setAccessLevel = async (opts) => { level = opts.accessLevel; };
  globalThis.chrome = chrome;
  try {
    await import('../../tools/canvas-helper/background.js');
  } finally {
    delete globalThis.chrome;
  }
  assert.equal(level, 'TRUSTED_CONTEXTS');
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

async function popup({ tabUrl, status }) {
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
    permissions: { request: async (p) => { requested.push(p); return true; } },
  };
  await start(doc, chrome, () => Date.parse('2026-10-27T12:00:00Z'));
  return { els, sent, requested, opened };
}

const PAIRED = { paired: true, kwil: KWIL, canvas: CANVAS, lastSync: '2026-10-27T11:00:00Z',
                 result: 'ok', counts: { courses: 2, files: 1 }, sites: [] };

test('the popup shows status as text and never the key', async () => {
  const p = await popup({ tabUrl: `${CANVAS}/courses/111`, status: PAIRED });
  assert.equal(p.els.title.textContent, 'Paired');
  assert.deepEqual(p.els.lines.children.map((li) => li.textContent),
                   ['Kwil: heykwil.com', 'Canvas: school.instructure.com', 'Last sync: 1 h ago',
                    '2 course(s), 1 file(s) sent']);
  assert.equal(p.els['paired-box'].hidden, false);
  assert.equal(p.els['enable-box'].hidden, true);    // *.instructure.com needs no click
  const none = await popup({ tabUrl: 'chrome://extensions', status: { paired: false } });
  assert.equal(none.els.title.textContent, 'Not paired');
  assert.equal(none.els['paired-box'].hidden, true);
  assert.equal(none.els['enable-box'].hidden, true);
});

test('Enable on this site asks Chrome for that one origin, from the click', async () => {
  const p = await popup({ tabUrl: 'https://canvas.school.edu/courses/1', status: PAIRED });
  assert.equal(p.els['enable-box'].hidden, false);
  await p.els.enable.click();
  await new Promise((r) => setTimeout(r, 0));
  assert.deepEqual(p.requested, [{ origins: ['https://canvas.school.edu/*'] }]);
  assert.deepEqual(p.sent.find((m) => m.type === 'enable-site'),
                   { type: 'enable-site', origin: 'https://canvas.school.edu', tabId: 7 });
  for (const url of ['http://canvas.school.edu/', `${KWIL}/`]) {
    const q = await popup({ tabUrl: url, status: PAIRED });
    assert.equal(q.els['enable-box'].hidden, true, url);
  }
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
