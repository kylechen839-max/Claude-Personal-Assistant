// node --test tests/js/ui.test.mjs
// assistant/web/ui.js in node:vm with a minimal fake document: the settings
// menu closes on a tap outside it (not inside) and on Escape; a downward
// swipe while typing blurs the field (hides the keyboard), a short one or a
// swipe when not typing does nothing.

import test from 'node:test';
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import vm from 'node:vm';

const SCRIPT = readFileSync(new URL('../../assistant/web/ui.js', import.meta.url), 'utf8');

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
