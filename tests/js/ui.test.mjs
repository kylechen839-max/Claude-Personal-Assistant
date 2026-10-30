// node --test tests/js/ui.test.mjs
// kwil/web/ui.js in node:vm with a minimal fake document: the settings
// menu closes on a tap outside it (not inside) and on Escape; a downward
// swipe while typing blurs the field (hides the keyboard), a short one or a
// swipe when not typing does nothing. The phone's tab bar: a tap in it
// leaves Settings to its tabs, and Done hands focus to its Settings tab on a
// phone (the header's gear is hidden there). The owner's health banner: its
// lines (an app down, one failing, stale checks, events) and the element.

import test from 'node:test';
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import vm from 'node:vm';

const SCRIPT = readFileSync(new URL('../../kwil/web/ui.js', import.meta.url), 'utf8');

function setup() {
  const inside = { id: 'inside' };
  const outside = { id: 'outside' };
  const menu = { open: true, contains: (n) => n === inside || n === menu };
  const input = { tagName: 'INPUT', blurred: 0, blur() { this.blurred++; doc.activeElement = null; } };
  const listeners = {};
  const doc = {
    activeElement: input,
    querySelector: (sel) => (sel === 'details.menu' ? menu : null),
    addEventListener: (type, fn) => { (listeners[type] ??= []).push(fn); },
  };
  const win = { document: doc };
  win.window = win;
  vm.createContext(win);
  vm.runInContext(SCRIPT, win);
  const fire = (type, ev) => (listeners[type] || []).forEach((fn) => fn(ev));
  return { menu, input, doc, fire, inside, outside };
}

test('menu: a tap inside keeps it open, a tap outside closes it', () => {
  const t = setup();
  t.fire('pointerdown', { target: t.inside });
  assert.equal(t.menu.open, true);
  t.fire('pointerdown', { target: t.outside });
  assert.equal(t.menu.open, false);
});

test('menu: Escape closes it', () => {
  const t = setup();
  t.fire('keydown', { key: 'Enter' });
  assert.equal(t.menu.open, true);
  t.fire('keydown', { key: 'Escape' });
  assert.equal(t.menu.open, false);
});

test('menu: Escape with a Connections sheet open closes only the sheet', () => {
  const t = setup();
  let sheet = { open: true };
  t.menu.querySelector = (sel) => (sel === 'dialog[open]' ? sheet : null);
  t.fire('keydown', { key: 'Escape' });  // the dialog closes itself
  assert.equal(t.menu.open, true);
  sheet = null;
  t.fire('keydown', { key: 'Escape' });
  assert.equal(t.menu.open, false);
});

// The menu as the full-screen Settings page: a gear (summary), a title and
// Done; the fake records focus and the menu's own listeners.
function setupPage() {
  const focused = [];
  const node = (id) => ({ id, focus() { focused.push(id); } });
  const summary = node('summary');
  const title = node('settings-title');
  const menuListeners = {};
  const done = { id: 'settings-done', listeners: {},
    addEventListener(type, fn) { (this.listeners[type] ??= []).push(fn); } };
  const menu = {
    open: false, contains: () => true,
    querySelector: (sel) => (sel === 'summary' ? summary : null),
    addEventListener: (type, fn) => { (menuListeners[type] ??= []).push(fn); },
  };
  const listeners = {};
  const doc = {
    querySelector: (sel) => (sel === 'details.menu' ? menu : null),
    getElementById: (id) => ({ 'settings-title': title, 'settings-done': done })[id] || null,
    addEventListener: (type, fn) => { (listeners[type] ??= []).push(fn); },
  };
  const win = { document: doc };
  win.window = win;
  vm.createContext(win);
  vm.runInContext(SCRIPT, win);
  const fire = (type, ev) => (listeners[type] || []).forEach((fn) => fn(ev));
  const toggle = () => (menuListeners.toggle || []).forEach((fn) => fn({}));
  const tapDone = () => (done.listeners.click || []).forEach((fn) => fn({}));
  return { menu, focused, fire, toggle, tapDone, doc, win };
}

test('settings page: opening it focuses the title; Done closes it and focuses the gear', () => {
  const t = setupPage();
  t.menu.open = true;
  t.toggle();
  assert.deepEqual(t.focused, ['settings-title']);
  t.tapDone();
  assert.equal(t.menu.open, false);
  assert.deepEqual(t.focused, ['settings-title', 'summary']);
  t.toggle();  // the close's own toggle event moves nothing
  assert.deepEqual(t.focused, ['settings-title', 'summary']);
});

test('settings page: Escape gives focus back to the gear too', () => {
  const t = setupPage();
  t.menu.open = true;
  t.fire('keydown', { key: 'Escape' });
  assert.equal(t.menu.open, false);
  assert.deepEqual(t.focused, ['summary']);
});

test('menu: a tap inside the tab bar keeps Settings open (its tabs handle it)', () => {
  const t = setup();
  const tab = { id: 'tab-settings' };
  const bar = { id: 'tabbar', hidden: false, contains: (n) => n === tab || n === bar };
  t.doc.getElementById = (id) => (id === 'tabbar' ? bar : null);
  t.fire('pointerdown', { target: tab });
  assert.equal(t.menu.open, true);
  t.fire('pointerdown', { target: t.outside });
  assert.equal(t.menu.open, false);
});

// Done on a phone (matchMedia matches) with the tab bar showing focuses its
// Settings tab; on a wide screen, with the bar hidden or no matchMedia, the gear.
function doneFocus({ phone, barHidden = false, tabHidden = false, media = true }) {
  const t = setupPage();
  const bar = { id: 'tabbar', hidden: barHidden };
  const tab = { id: 'tab-settings', hidden: tabHidden, focus() { t.focused.push('tab-settings'); } };
  const byId = t.doc.getElementById;
  t.doc.getElementById = (id) => ({ tabbar: bar, 'tab-settings': tab })[id] || byId(id);
  if (media) t.win.matchMedia = (q) => ({ matches: phone && q === '(max-width: 47.99rem)' });
  t.menu.open = true;
  t.tapDone();
  assert.equal(t.menu.open, false);
  return t.focused.at(-1);
}

test('settings page: Done focuses the Settings tab on a phone, the gear otherwise', () => {
  assert.equal(doneFocus({ phone: true }), 'tab-settings');
  assert.equal(doneFocus({ phone: false }), 'summary');
  assert.equal(doneFocus({ phone: true, barHidden: true }), 'summary');
  assert.equal(doneFocus({ phone: true, tabHidden: true }), 'summary');
  assert.equal(doneFocus({ phone: true, media: false }), 'summary');  // no matchMedia
});

const touch = (y) => ({ touches: [{ clientY: y }] });

test('keyboard: a swipe down while typing hides it; a small move does not', () => {
  const t = setup();
  t.fire('touchstart', touch(300));
  t.fire('touchmove', touch(320));
  assert.equal(t.input.blurred, 0);
  t.fire('touchmove', touch(360));
  assert.equal(t.input.blurred, 1);
});

test('keyboard: swiping up, or when nothing is focused, does nothing', () => {
  const t = setup();
  t.fire('touchstart', touch(300));
  t.fire('touchmove', touch(200));
  assert.equal(t.input.blurred, 0);
  t.doc.activeElement = { tagName: 'BUTTON' };
  t.fire('touchstart', touch(100));
  t.fire('touchmove', touch(400));
  assert.equal(t.input.blurred, 0);
});

test('keyboard: a swipe that scrolls a long message inside the text field does not hide it', () => {
  const t = setup();
  const field = { id: 'text', tagName: 'TEXTAREA', scrollHeight: 300, clientHeight: 150, blurred: 0,
    contains: (n) => n === field, blur() { this.blurred++; t.doc.activeElement = null; } };
  t.doc.getElementById = (id) => (id === 'text' ? field : null);
  t.doc.activeElement = field;
  const drag = (y, target) => ({ touches: [{ clientY: y }], target });
  t.fire('touchstart', drag(300, field));
  t.fire('touchmove', drag(400, field));
  assert.equal(field.blurred, 0);  // it scrolled the text instead
  // a swipe that starts elsewhere still hides the keyboard
  t.fire('touchstart', drag(300, t.outside));
  t.fire('touchmove', drag(400, t.outside));
  assert.equal(field.blurred, 1);
  // a short message (nothing to scroll): the swipe on it hides the keyboard too
  t.doc.activeElement = field;
  field.scrollHeight = 150;
  t.fire('touchstart', drag(300, field));
  t.fire('touchmove', drag(400, field));
  assert.equal(field.blurred, 2);
});

// The owner's health banner (state.health from /api/state, kwil/watch.py).
const at = (iso) => (iso ? iso.slice(11, 16) : '');

test('health banner: lines and the element', () => {
  const banner = { textContent: '', hidden: true };
  const win = { document: { getElementById: (id) => (id === 'health' ? banner : null),
    querySelector: () => null, addEventListener() {} } };
  win.window = win;
  vm.createContext(win);
  vm.runInContext(SCRIPT, win);
  const ui = win.KwilUI;
  const health = {
    issues: [{ label: "Mom's app", since: '2026-10-20T06:13:00+00:00', what: 'healthz: HTTP 502' },
             { label: 'Your app', since: '2026-10-20T07:00:00+00:00', what: 'follow-up turn: HTTP 500' }],
    stale: true, checked: '2026-10-20T05:00:00+00:00',
    events: [{ at: '2026-10-20T04:00:00+00:00', text: 'New version abc1234 failed its go-live check.' }],
  };
  assert.deepEqual([...ui.healthLines(health, at)], [
    "Mom's app is down since 06:13 (healthz: HTTP 502)",
    "Your app isn't working right since 07:00 (follow-up turn: HTTP 500)",
    "Health checks haven't run since 05:00.",
    '04:00: New version abc1234 failed its go-live check.',
  ]);
  ui.renderHealth(win.document, health, at);
  assert.equal(banner.hidden, false);
  assert.match(banner.textContent, /^Mom's app is down since 06:13/);
  ui.renderHealth(win.document, null, at);  // all well, or not the owner
  assert.equal(banner.hidden, true);
  assert.equal(banner.textContent, '');
});
