// node --test tests/js/voice-picker.test.mjs  (Node 18+; no packages needed)
//
// Runs the settings menu's kwil/web/voice-picker.js in node:vm with a
// minimal fake page: the "Reply voice" box shows only when the server offers
// the picker, lists the voices with a "paid" tag on ElevenLabs ones, saves a
// choice with POST /api/voices (and goes back to the server's choice when
// that fails), plays a preview through the app's audio element, and takes a
// typed-in ElevenLabs voice ID only where the server allows it.

import test from 'node:test';
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import vm from 'node:vm';

const SCRIPT = readFileSync(new URL('../../kwil/web/voice-picker.js', import.meta.url), 'utf8');

class El {
  constructor(id, hidden = false) {
    this.id = id; this.hidden = hidden; this.value = ''; this.textContent = '';
    this.listeners = {}; this.children = []; this.disabled = false;
  }
  addEventListener(type, fn) { (this.listeners[type] ??= []).push(fn); }
  appendChild(c) { this.children.push(c); return c; }
  async fire(type, ev = {}) {
    for (const fn of this.listeners[type] || []) await fn({ preventDefault() {}, ...ev });
  }
}

// A <select> whose value can only be one of its options' (as in a browser).
class Select extends El {
  get textContent() { return ''; }
  set textContent(v) { this.children = []; }
  get value() { return this._value ?? ''; }
  set value(v) {
    const options = this.children || [];  // the El constructor sets value first
    this._value = options.some((o) => o.value === v) ? v : (options[0]?.value ?? '');
  }
}

const VOICES = {
  voices: [
    { id: '', name: 'Default (en_US-lessac-medium)', paid: false, lang: 'en' },
    { id: 'piper:en_GB-alan-low', name: 'Alan (en_GB, low)', paid: false, lang: 'en' },
    { id: 'elevenlabs:21m00Tcm4TlvDq8ikWAM', name: 'Rachel (ElevenLabs)', paid: true, lang: 'multi' },
  ],
  current: 'piper:en_GB-alan-low', language: 'en', custom: true,
};

async function settle() { for (let i = 0; i < 10; i++) await new Promise((r) => setImmediate(r)); }

function load({ responses = {}, withPlayer = true } = {}) {
  const ids = ['voice-picker', 'voice-paid', 'voice-preview', 'voice-status',
    'voice-custom-row', 'voice-custom', 'voice-custom-use'];
  const els = Object.fromEntries(ids.map((id) => [id, new El(id, ['voice-picker', 'voice-paid', 'voice-custom-row'].includes(id))]));
  els['voice-select'] = new Select('voice-select');
  const menu = new El('menu');
  const fetches = [];
  const played = [];
  const player = { src: '', play: async () => { played.push(player.src); } };
  const win = {
    fetch: async (url, opts = {}) => {
      fetches.push({ url, opts });
      let r = responses[`${opts.method || 'GET'} ${url}`] ?? { status: 404, body: { error: 'not found' } };
      if (typeof r === 'function') r = r(opts);
      return { ok: r.status < 300, status: r.status, json: async () => r.body,
               blob: async () => ({ size: 10, type: 'audio/wav' }) };
    },
    URL: { createObjectURL: () => 'blob:sample', revokeObjectURL: () => {} },
    Audio: function () { return player; },
    document: {
      getElementById: (id) => els[id] ?? null,
      querySelector: (sel) => (sel === 'details.menu' ? menu : null),
      createElement: (tag) => new El(tag),
    },
  };
  win.window = win;
  vm.createContext(win);
  vm.runInContext(SCRIPT, win);
  const api = win.KwilVoicePicker;
  if (withPlayer) api.configure({ player });
  return { win, els, menu, fetches, played, player, api };
}

const LIST = { 'GET /api/voices': { status: 200, body: VOICES } };

test('hidden, and nothing fetched, when the server has no picker', async () => {
  const t = load({ responses: LIST });
  t.api.onState({ voice_picker: false }, t.win, t.win.document);
  await settle();
  assert.equal(t.els['voice-picker'].hidden, true);
  assert.equal(t.fetches.length, 0);
});

test('lists the voices, marks paid ones, and selects the current one', async () => {
  const t = load({ responses: LIST });
  t.api.onState({ voice_picker: true }, t.win, t.win.document);
  await settle();
  assert.equal(t.els['voice-picker'].hidden, false);
  const opts = t.els['voice-select'].children;
  assert.deepEqual(opts.map((o) => o.value), ['', 'piper:en_GB-alan-low', 'elevenlabs:21m00Tcm4TlvDq8ikWAM']);
  assert.equal(opts[2].textContent, 'Rachel (ElevenLabs) · paid');
  assert.equal(opts[1].textContent, 'Alan (en_GB, low)');
  assert.equal(t.els['voice-select'].value, 'piper:en_GB-alan-low');
  assert.equal(t.els['voice-paid'].hidden, true);
  assert.equal(t.els['voice-custom-row'].hidden, false);  // custom: true
  // a later /api/state doesn't fetch again; opening the menu does
  t.api.onState({ voice_picker: true }, t.win, t.win.document);
  await settle();
  assert.equal(t.fetches.length, 1);
  t.menu.open = true;
  await t.menu.fire('toggle');
  await settle();
  assert.equal(t.fetches.length, 2);
});

test('picking a voice saves it on the server; a refusal goes back', async () => {
  const saved = [];
  const t = load({ responses: { ...LIST,
    'POST /api/voices': (opts) => {
      const voice = JSON.parse(opts.body).voice;
      saved.push(voice);
      return voice.startsWith('elevenlabs:')
        ? { status: 400, body: { error: 'unknown voice' } }
        : { status: 200, body: { ok: true, current: voice } };
    } } });
  t.api.onState({ voice_picker: true }, t.win, t.win.document);
  await settle();
  const select = t.els['voice-select'];
  select.value = '';
  await select.fire('change');
  await settle();
  assert.deepEqual(saved, ['']);
  const post = t.fetches.find((f) => f.opts.method === 'POST');
  assert.equal(post.url, '/api/voices');
  assert.equal(post.opts.credentials, 'same-origin');
  assert.equal(post.opts.headers['Content-Type'], 'application/json');
  assert.equal(t.els['voice-status'].textContent, 'Saved.');
  select.value = 'elevenlabs:21m00Tcm4TlvDq8ikWAM';
  const change = select.fire('change');
  assert.equal(t.els['voice-paid'].hidden, false);  // the tag shows at once
  await change;
  await settle();
  assert.equal(select.value, '');  // back to what the server has
  assert.equal(t.els['voice-paid'].hidden, true);
  assert.equal(t.els['voice-status'].textContent, 'unknown voice');
});

test('preview plays a sample of the selected voice through the app player', async () => {
  const t = load({ responses: { ...LIST,
    'POST /api/voices/preview': { status: 200, body: {} } } });
  t.api.onState({ voice_picker: true }, t.win, t.win.document);
  await settle();
  await t.els['voice-preview'].fire('click');
  const post = t.fetches.find((f) => f.url === '/api/voices/preview');
  assert.deepEqual(JSON.parse(post.opts.body), { voice: 'piper:en_GB-alan-low' });
  assert.deepEqual(t.played, ['blob:sample']);
  assert.equal(t.els['voice-preview'].disabled, false);
  assert.equal(t.els['voice-status'].textContent, '');
});

test('a failed preview says so', async () => {
  const t = load({ responses: { ...LIST,
    'POST /api/voices/preview': { status: 500, body: { error: 'could not make audio' } } } });
  t.api.onState({ voice_picker: true }, t.win, t.win.document);
  await settle();
  await t.els['voice-preview'].fire('click');
  assert.deepEqual(t.played, []);
  assert.equal(t.els['voice-status'].textContent, "Couldn't play the preview.");
});

test('a typed ElevenLabs voice ID is checked, then saved', async () => {
  const saved = [];
  const t = load({ responses: { ...LIST,
    'POST /api/voices': (opts) => { saved.push(JSON.parse(opts.body).voice);
      return { status: 200, body: { ok: true, current: saved.at(-1) } }; } } });
  t.api.onState({ voice_picker: true }, t.win, t.win.document);
  await settle();
  t.els['voice-custom'].value = '../../x';
  await t.els['voice-custom-use'].fire('click');
  assert.deepEqual(saved, []);
  assert.equal(t.els['voice-status'].textContent, 'unknown voice');
  t.els['voice-custom'].value = ' AbCdEf1234567890 ';
  await t.els['voice-custom-use'].fire('click');
  await settle();
  assert.deepEqual(saved, ['elevenlabs:AbCdEf1234567890']);
  assert.equal(t.els['voice-custom'].value, '');
});

test('calls go through the app request() when configured (failover)', async () => {
  const t = load({ responses: LIST });
  const via = [];
  t.api.configure({ request: async (url, opts = {}) => { via.push(url); return t.win.fetch(url, opts); } });
  t.api.onState({ voice_picker: true }, t.win, t.win.document);
  await settle();
  assert.deepEqual(via, ['/api/voices']);
});

test('label adds the paid tag only to paid voices', () => {
  const { api } = load();
  assert.equal(api.label({ name: 'A', paid: false }), 'A');
  assert.equal(api.label({ name: 'B', paid: true }), 'B · paid');
});
