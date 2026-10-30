// node --test tests/js/sw-push.test.mjs  (Node 18+; no packages needed)
//
// Runs the service worker (kwil/web/sw.js) in node:vm with a fake
// `self`: a push shows a notification from push.py's {title, body, url}
// (with defaults for an empty or broken payload, and always one: iOS
// requires it), a tap focuses an open app and tells it to open the brief,
// or opens /#brief, and a payload can never send the tap to another site.
// A reminder (/#reminders) gets no tag, so two never replace each other.
// Also: the cache version and SHELL list brief.js.

import test from 'node:test';
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import vm from 'node:vm';

const SCRIPT = readFileSync(new URL('../../kwil/web/sw.js', import.meta.url), 'utf8');
const ORIGIN = 'https://hey-claude.example.com';

function load({ windows = [] } = {}) {
  const handlers = {};
  const shown = [];
  const opened = [];
  const self = {
    addEventListener: (type, fn) => { handlers[type] = fn; },
    location: new URL(ORIGIN + '/sw.js'),
    registration: { showNotification: async (title, options) => { shown.push({ title, options }); } },
    clients: {
      matchAll: async () => windows,
      openWindow: async (url) => { opened.push(url); return {}; },
      claim: async () => {},
    },
    skipWaiting: () => {},
  };
  const ctx = { self, location: new URL(ORIGIN + '/sw.js'), URL, caches: {}, fetch: () => {} };
  vm.createContext(ctx);
  vm.runInContext(SCRIPT, ctx);
  return { handlers, shown, opened, ctx };
}

async function push(sw, data) {
  let waited = null;
  sw.handlers.push({ data, waitUntil: (p) => { waited = p; } });
  await waited;
}

async function tap(sw, data) {
  let closed = false;
  let waited = null;
  sw.handlers.notificationclick({
    notification: { data, close: () => { closed = true; } },
    waitUntil: (p) => { waited = p; },
  });
  await waited;
  return closed;
}

const json = (value) => ({ json: () => value });

test('a push shows the notification push.py sent', async () => {
  const sw = load();
  await push(sw, json({ title: 'Kwil', body: 'Essay 2 is due tomorrow.', url: '/#brief' }));
  assert.equal(sw.shown.length, 1);
  const [{ title, options }] = sw.shown;
  assert.equal(title, 'Kwil');
  assert.equal(options.body, 'Essay 2 is due tomorrow.');
  assert.equal(options.data.url, '/#brief');
  assert.equal(options.tag, 'kwil-brief');
  assert.equal(options.icon, '/icon-192.png');
});

test('each reminder shows on its own; only the brief replaces the last brief', async () => {
  const sw = load();
  await push(sw, json({ title: 'Reminder', body: 'Submit HW7', url: '/#reminders' }));
  await push(sw, json({ title: 'Reminder', body: 'Stretch', url: '/#reminders' }));
  assert.deepEqual(sw.shown.map((s) => s.options.body), ['Submit HW7', 'Stretch']);
  assert.ok(sw.shown.every((s) => s.options.tag === undefined));  // neither replaces the other
  assert.equal(sw.shown[0].options.data.url, '/#reminders');
  await tap(sw, { url: '/#reminders' });
  assert.deepEqual(sw.opened, ['/#reminders']);
});

test('an empty or broken payload still shows a notification', async () => {
  const sw = load();
  await push(sw, null);
  await push(sw, { json: () => { throw new SyntaxError('bad'); } });
  await push(sw, json('just a string'));
  assert.equal(sw.shown.length, 3);
  for (const { title, options } of sw.shown) {
    assert.equal(title, 'Kwil');  // the app's name when the payload has none
    assert.equal(options.body, '');
    assert.equal(options.data.url, '/#brief');
  }
});

test('long text is cut and an off-site url is replaced by the brief', async () => {
  const sw = load();
  for (const url of ['https://evil.example/', '//evil.example/x', '/\\evil.example', 'javascript:alert(1)', 42,
                     '/\t/evil.example', '/\n/evil.example', '/\r\n/evil.example']) {
    await push(sw, json({ title: 'T'.repeat(500), body: 'B'.repeat(1000), url }));
  }
  for (const { title, options } of sw.shown) {
    assert.equal(title.length, 80);
    assert.equal(options.body.length, 300);
    assert.equal(options.data.url, '/#brief');
  }
});

test('a tap opens the brief when the app is not open', async () => {
  const sw = load();
  assert.equal(await tap(sw, { url: '/#brief' }), true);
  assert.deepEqual(sw.opened, ['/#brief']);
});

test('a tap focuses the open app and asks it to open the brief', async () => {
  const messages = [];
  let focused = 0;
  const other = { url: 'https://other.example/', postMessage: () => assert.fail('other site'),
                  focus: async () => assert.fail('other site') };
  const app = { url: ORIGIN + '/', postMessage: (m) => messages.push(m),
                focus: async () => { focused += 1; } };
  const sw = load({ windows: [other, app] });
  await tap(sw, { url: '/#brief' });
  assert.equal(focused, 1);
  assert.equal(messages.length, 1);
  assert.equal(messages[0].type, 'open');
  assert.equal(messages[0].url, '/#brief');
  assert.deepEqual(sw.opened, []);
});

test('a tap with an off-site url still opens only this site', async () => {
  const sw = load();
  await tap(sw, { url: 'https://evil.example/' });
  await tap(sw, null);
  await tap(sw, { url: '/\t/evil.example' });  // the browser drops the tab: //evil.example
  await tap(sw, { url: '/\n/evil.example' });
  assert.deepEqual(sw.opened, ['/#brief', '/#brief', '/#brief', '/#brief']);
  await tap(sw, { url: '/?x=1#brief' });  // a real path stays as it is
  assert.equal(sw.opened.at(-1), '/?x=1#brief');
});

test('the shell caches brief.js under a new cache version', () => {
  assert.match(SCRIPT, /const CACHE = "kwil-shell-v25";/);
  const sw = load();
  assert.ok(vm.runInContext('SHELL', sw.ctx).includes('/brief.js'));
});
