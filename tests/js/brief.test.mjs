// node --test tests/js/brief.test.mjs  (Node 18+; no packages needed)
//
// Runs the phone page's assistant/web/brief.js in node:vm with a fake DOM,
// fetch(), service worker, PushManager and Notification: "Enable
// notifications" asks for permission first thing in the tap, subscribes
// with the server's VAPID key and saves the subscription; a refusal saves
// nothing; push off on the server hides the button; an iPhone outside the
// Home Screen app is told to add it; turning off removes it at the server
// too. The brief (/#brief, or a tapped notification while the app is open)
// shows the text with textContent and a Play button that plays only when
// tapped. And every fixed string has Chinese in i18n.js.

import test from 'node:test';
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import vm from 'node:vm';

const SCRIPT = readFileSync(new URL('../../assistant/web/brief.js', import.meta.url), 'utf8');
const I18N = readFileSync(new URL('../../assistant/web/i18n.js', import.meta.url), 'utf8');
const HTML = readFileSync(new URL('../../assistant/web/index.html', import.meta.url), 'utf8');
// a real P-256 point, base64url (65 bytes, 0x04 first)
const KEY = Buffer.concat([Buffer.from([4]), Buffer.alloc(64, 7)]).toString('base64url');

class El {
  constructor(id, hidden = false) {
    this.id = id; this.hidden = hidden; this.disabled = false; this.textContent = '';
    this.listeners = {}; this.src = ''; this.played = 0;
  }
  addEventListener(type, fn) { (this.listeners[type] ??= []).push(fn); }
  async fire(type, e = {}) { for (const fn of this.listeners[type] || []) await fn({ preventDefault() {}, ...e }); }
}

function load({ hash = '', permission = 'default', answer = 'granted', responses = {},
                push = true, ua = 'Mozilla/5.0 (iPhone; CPU iPhone OS 18_0 like Mac OS X)',
                existing = null } = {}) {
  const ids = ['push-toggle', 'push-status', 'brief-view', 'brief-text', 'brief-when',
               'brief-play', 'brief-status', 'brief-back'];
  const els = Object.fromEntries(ids.map((id) => [id, new El(id, ['push-toggle', 'push-status', 'brief-view', 'brief-play'].includes(id))]));
  const menu = { open: true };
  const log = [];  // everything that happens, in order
  const fetches = [];
  const shown = [];
  let current = existing;
  const subscription = (opts) => ({
    endpoint: 'https://web.push.apple.com/QGuQ-new',
    options: { applicationServerKey: opts.applicationServerKey.buffer },
    toJSON() { return { endpoint: this.endpoint, expirationTime: null, keys: { p256dh: 'BP' + 'x'.repeat(85), auth: 'YXV0aA' } }; },
    unsubscribe: async () => { log.push('sub.unsubscribe'); current = null; return true; },
  });
  const pushManager = {
    getSubscription: async () => current,
    subscribe: async (opts) => { log.push(['subscribe', opts.userVisibleOnly, [...opts.applicationServerKey]]); current = subscription(opts); return current; },
  };
  const swListeners = {};
  const player = { src: '', play: async () => { log.push('play'); } };
  const win = {
    navigator: {
      userAgent: ua,
      serviceWorker: { ready: Promise.resolve({ pushManager }),
                       addEventListener: (t, fn) => { swListeners[t] = fn; } },
    },
    Notification: {
      permission,
      requestPermission: async () => { log.push('requestPermission'); win.Notification.permission = answer; return answer; },
    },
    location: { hash, pathname: '/' },
    history: { replaceState: (a, b, url) => { log.push(['replaceState', url]); win.location.hash = ''; } },
    fetch: async (url, opts = {}) => {
      log.push(['fetch', url]);
      fetches.push({ url, opts });
      const r = responses[url] ?? (url === '/api/push/key' ? { status: 200, body: { key: KEY, ready: true } }
        : url === '/api/student/brief' ? { status: 200, body: { text: '<b>Essay 2</b> is due tomorrow.', at: '2026-10-21T07:35-04:00', audio: '/api/audio/7-abc' } }
          : url === '/api/audio/7-abc' ? { status: 200, body: {} }
            : { status: 200, body: { ok: true, devices: 1, removed: true } });
      return { ok: r.status < 300, status: r.status, json: async () => r.body, blob: async () => 'wav-bytes' };
    },
    URL: { createObjectURL: () => 'blob:brief', revokeObjectURL: () => {} },
    atob: (s) => Buffer.from(s, 'base64').toString('binary'),
    addEventListener: () => {},
    document: { getElementById: (id) => els[id] ?? null, querySelector: (s) => (s === 'details.menu' ? menu : null) },
    Uint8Array, Promise, Error, Object, JSON, String, Boolean,
  };
  if (push) win.PushManager = function PushManager() {};
  win.window = win;
  vm.createContext(win);
  vm.runInContext(SCRIPT, win);
  win.HeyClaudeBrief.configure({ show: (v) => shown.push(v), player });
  const state = () => win.HeyClaudeBrief.onState({}, win, win.document);
  return { win, els, menu, log, fetches, shown, player, state, swListeners, api: win.HeyClaudeBrief };
}

const posts = (t) => t.fetches.filter((f) => f.opts.method === 'POST')
  .map((f) => [f.url, JSON.parse(f.opts.body)]);

test('"Enable notifications" asks first in the tap, then subscribes and saves it', async () => {
  const t = load();
  await t.state();
  assert.equal(t.els['push-toggle'].hidden, false);
  assert.equal(t.els['push-toggle'].textContent, 'Enable notifications');
  t.log.length = 0;
  await t.els['push-toggle'].fire('click');
  assert.equal(t.log[0], 'requestPermission');  // nothing awaited before it in the tap
  const [, visible, bytes] = t.log.find((e) => e[0] === 'subscribe');
  assert.equal(visible, true);
  assert.equal(bytes.length, 65);
  assert.equal(bytes[0], 4);
  const [[url, body]] = posts(t);
  assert.equal(url, '/api/push/subscribe');
  assert.equal(body.subscription.endpoint, 'https://web.push.apple.com/QGuQ-new');
  assert.equal(t.els['push-toggle'].textContent, 'Turn off notifications');
  assert.equal(t.els['push-status'].textContent, 'Notifications are on for this phone.');
});

test('a refused permission subscribes and saves nothing', async () => {
  const t = load({ answer: 'denied' });
  await t.state();
  await t.els['push-toggle'].fire('click');
  assert.equal(t.log.some((e) => e[0] === 'subscribe'), false);
  assert.deepEqual(posts(t), []);
  assert.match(t.els['push-status'].textContent, /blocked/);
  assert.equal(t.els['push-toggle'].textContent, 'Enable notifications');
});

test('a server that cannot store subscriptions says so before asking', async () => {
  const t = load({ responses: { '/api/push/key': { status: 200, body: { key: KEY, ready: false } } } });
  await t.state();
  await t.els['push-toggle'].fire('click');
  assert.equal(t.log.includes('requestPermission'), false);
  assert.equal(t.els['push-status'].textContent, "This server can't store notification settings yet.");
});

test('push off on the server hides the button', async () => {
  const t = load({ responses: { '/api/push/key': { status: 404, body: { error: 'not found' } } } });
  await t.state();
  assert.equal(t.els['push-toggle'].hidden, true);
});

test('an iPhone outside the Home Screen app is told to add it', async () => {
  const t = load({ push: false });
  await t.state();
  assert.equal(t.els['push-toggle'].hidden, true);
  assert.match(t.els['push-status'].textContent, /Home Screen/);
  assert.equal(t.fetches.length, 0);
  const desktop = load({ push: false, ua: 'Mozilla/5.0 (X11; Linux x86_64)' });
  await desktop.state();
  assert.equal(desktop.els['push-status'].hidden, true);
});

test('turning off removes it at the server and in the browser', async () => {
  const existing = { endpoint: 'https://web.push.apple.com/old', options: {},
                     unsubscribe: async () => true };
  const t = load({ permission: 'granted', existing });
  await t.state();
  assert.equal(t.els['push-toggle'].textContent, 'Turn off notifications');
  await t.els['push-toggle'].fire('click');
  assert.deepEqual(posts(t), [['/api/push/unsubscribe', { endpoint: 'https://web.push.apple.com/old' }]]);
  assert.equal(t.els['push-toggle'].textContent, 'Enable notifications');
});

test('/#brief opens the brief with its text and a Play button, playing nothing until tapped', async () => {
  const t = load({ hash: '#brief' });
  await t.state();
  assert.deepEqual([...t.shown], ['brief-view']);
  assert.equal(t.menu.open, false);
  assert.equal(t.els['brief-text'].textContent, '<b>Essay 2</b> is due tomorrow.');  // text, not HTML
  assert.equal(t.els['brief-when'].textContent, 'Latest brief');
  assert.equal(t.els['brief-play'].hidden, false);
  assert.equal(t.log.includes('play'), false);  // no autoplay
  await t.els['brief-play'].fire('click');
  assert.ok(t.fetches.some((f) => f.url === '/api/audio/7-abc'));
  assert.equal(t.player.src, 'blob:brief');
  assert.equal(t.log.includes('play'), true);
  await t.state();  // a later /api/state doesn't reopen it
  assert.equal(t.shown.length, 1);
  await t.els['brief-back'].fire('click');
  assert.deepEqual(t.log.find((e) => e[0] === 'replaceState'), ['replaceState', '/']);
  assert.equal(t.shown.at(-1), 'app');
});

test('a notification tapped while the app is open opens the brief', async () => {
  const t = load();
  await t.state();
  assert.deepEqual([...t.shown], []);
  await t.swListeners.message({ data: { type: 'open', url: '/#brief' } });
  assert.deepEqual([...t.shown], ['brief-view']);
  await t.swListeners.message({ data: { type: 'other' } });
  assert.equal(t.shown.length, 1);
});

test('a brief that fails to load says why and shows no Play button', async () => {
  const t = load({ hash: '#brief', responses: { '/api/student/brief': { status: 404, body: { error: 'not found' } } } });
  await t.state();
  assert.equal(t.els['brief-status'].textContent, 'not found');
  assert.equal(t.els['brief-play'].hidden, true);
});

test('every fixed string of notifications and the brief has Chinese', () => {
  const ctx = { console };
  vm.createContext(ctx);
  vm.runInContext(I18N, ctx);
  const tr = ctx.HeyClaudeI18n.translate;
  const t = load();
  const strings = [...Object.values(t.api.TEXT), 'Brief', 'Play', 'Back to chat'];
  for (const s of strings) {
    const zh = tr(s);
    assert.notEqual(zh, s, s);
    assert.match(zh, /[一-鿿]/, s);
  }
  // the markup's own text is among them
  for (const s of ['Enable notifications', '>Brief<', '>Play<']) assert.ok(HTML.includes(s), s);
});
