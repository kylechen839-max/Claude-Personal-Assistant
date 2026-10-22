// node --test tests/js/attachments.test.mjs  (Node 18+; no packages needed)
//
// Runs the phone page's kwil/web/attachments.js in node:vm: which
// picked files are allowed, the 4-file limit, chips with a remove button,
// "+" disabled at the limit, photos sent as their shrunk JPEG, and the
// FormData parts /api/turn gets.

import test from 'node:test';
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import vm from 'node:vm';

const SCRIPT = readFileSync(new URL('../../kwil/web/attachments.js', import.meta.url), 'utf8');

class El {
  constructor(tag = 'div', id = '') {
    this.tagName = tag.toUpperCase(); this.id = id; this.hidden = false; this.disabled = false;
    this.children = []; this.listeners = {}; this.dataset = {}; this.attrs = {};
    this.textContent = ''; this.className = ''; this.value = ''; this.files = [];
    this.clicked = 0; this.open = false;
  }
  set textContent(v) { this._text = v; if (v === '') this.children = []; }
  get textContent() { return this._text; }
  appendChild(c) { this.children.push(c); return c; }
  setAttribute(k, v) { this.attrs[k] = String(v); }
  addEventListener(type, fn) { (this.listeners[type] ??= []).push(fn); }
  async fire(type) { for (const fn of this.listeners[type] || []) await fn({ preventDefault() {} }); }
  click() { this.clicked += 1; return this.fire('click'); }
  showModal() { this.open = true; }
  close() { this.open = false; }
  querySelectorAll(sel) { return sel === 'button[data-pick]' ? this.pickers : []; }
  querySelector(sel) { return sel === 'button.cancel' ? this.cancel : null; }
  // canvas
  getContext() { return { drawImage() {} }; }
  toBlob(cb, type) { cb({ type, size: 1234, shrunk: true }); }
  toDataURL() { return 'data:image/jpeg;base64,AAAA'; }
}

function load({ withDocument = false, imageFails = false } = {}) {
  const els = {};
  for (const id of ['attach', 'attach-sheet', 'attach-chips', 'attach-camera',
                    'attach-library', 'attach-file']) els[id] = new El('div', id);
  els['attach-sheet'].pickers = ['camera', 'library', 'file'].map((pick) => {
    const b = new El('button'); b.dataset.pick = pick; return b;
  });
  els['attach-sheet'].cancel = new El('button');
  const notes = [];
  const doc = {
    getElementById: (id) => els[id] || null,
    createElement: (tag) => new El(tag),
  };
  const ctx = {
    createImageBitmap: async () => {
      if (imageFails) throw new Error('bad');
      return { width: 4000, height: 3000 };
    },
    FileReader: class { readAsDataURL() { this.onerror(new Error('bad')); } },
    Image: class {},
    addMessage: (kind, text) => notes.push([kind, text]),
  };
  if (withDocument) ctx.document = doc;
  vm.createContext(ctx);
  ctx.window = ctx;
  vm.runInContext(SCRIPT, ctx);
  return { api: ctx.KwilAttach, els, notes, doc };
}

const file = (name, type, size = 10) => ({ name, type, size });

test('kindOf trusts the type, then the extension, and refuses the rest', () => {
  const { api } = load();
  assert.equal(api.kindOf(file('a.jpg', 'image/jpeg')), 'image');
  assert.equal(api.kindOf(file('IMG_1.HEIC', '')), 'image');
  assert.equal(api.kindOf(file('x', 'application/pdf')), 'pdf');
  assert.equal(api.kindOf(file('notes.md', '')), 'text');
  assert.equal(api.kindOf(file('a.txt', 'text/plain')), 'text');
  assert.equal(api.kindOf(file('a.zip', 'application/zip')), null);
  assert.equal(api.kindOf(file('a.html', 'text/html')), null);
  assert.equal(api.kindOf(file('movie.mov', 'video/quicktime')), null);
});

test('at most 4 files; refused ones say why; the old list is untouched', () => {
  const { api } = load();
  const first = api.addFiles([], [file('a.jpg', 'image/jpeg'), file('b.zip', 'application/zip')]);
  assert.equal(first.list.length, 1);
  assert.deepEqual([...first.rejected].map((r) => ({ ...r })), [{ name: 'b.zip', reason: 'type' }]);
  const many = api.addFiles(first.list, ['1', '2', '3', '4'].map((n) => file(`${n}.pdf`, 'application/pdf')));
  assert.equal(first.list.length, 1);
  assert.equal(many.list.length, 4);
  assert.deepEqual([...many.rejected].map((r) => r.reason), ['limit']);
  assert.equal(api.canAdd(many.list), false);
  assert.equal(api.canAdd(api.removeAt(many.list, 0)), true);
  assert.deepEqual([...api.removeAt(many.list, 1)].map((a) => a.name), ['a.jpg', '2.pdf', '3.pdf']);
  assert.equal(api.summary(many.list.slice(0, 2)), '[photo] [file: 1.pdf]');
});

test('appendTo sends shrunk photos as .jpg and files as they are', () => {
  const { api } = load();
  const parts = [];
  const form = { append: (...a) => parts.push(a) };
  const pdf = file('plan.pdf', 'application/pdf');
  const photo = file('IMG_2.HEIC', 'image/heic');
  api.appendTo(form, [{ file: photo, blob: { shrunk: true }, kind: 'image', name: 'IMG_2.HEIC' },
                      { file: pdf, kind: 'pdf', name: 'plan.pdf' }]);
  assert.equal(parts.length, 2);
  assert.equal(parts[0][0], 'attachment');
  assert.deepEqual({ ...parts[0][1] }, { shrunk: true });
  assert.equal(parts[0][2], 'IMG_2.jpg');
  assert.equal(parts[1][1], pdf);
  assert.equal(parts[1][2], 'plan.pdf');
});

test('the tray: sheet, chips, remove, "+" disabled at 4, clear', async () => {
  const { api, els, notes } = load({ withDocument: true });
  const tray = api.tray;
  assert.ok(tray);
  assert.equal(els['attach-chips'].hidden, true);
  assert.equal(els.attach.disabled, false);
  await els.attach.click();
  assert.equal(els['attach-sheet'].open, true);
  const [camera, library, choose] = els['attach-sheet'].pickers;
  await library.click();
  assert.equal(els['attach-sheet'].open, false);
  assert.equal(els['attach-library'].clicked, 1);
  await camera.click();
  assert.equal(els['attach-camera'].clicked, 1);
  await choose.click();
  assert.equal(els['attach-file'].clicked, 1);

  els['attach-library'].files = [file('a.jpg', 'image/jpeg')];
  await els['attach-library'].fire('change');
  assert.equal(els['attach-library'].value, '');  // the same photo can be picked again
  await tray.whenReady();
  assert.equal(tray.count(), 1);
  assert.equal(els['attach-chips'].hidden, false);
  const chip = els['attach-chips'].children[0];
  assert.equal(chip.children[0].tagName, 'IMG');
  assert.equal(chip.children[0].src, 'data:image/jpeg;base64,AAAA');  // data:, allowed by the CSP

  els['attach-file'].files = [file('a.pdf', 'application/pdf'), file('b.txt', 'text/plain'),
                              file('c.md', ''), file('d.pdf', 'application/pdf'),
                              file('e.exe', 'application/x-msdownload')];
  await els['attach-file'].fire('change');
  assert.equal(tray.count(), 4);
  assert.equal(els.attach.disabled, true);
  assert.deepEqual(notes.map((n) => n[1]), ['You can attach up to 4 files.',
    'Only photos, PDFs and text files can be attached.']);
  await els.attach.click();  // disabled: no sheet
  assert.equal(els['attach-sheet'].open, false);
  assert.equal(tray.summary(), '[photo] [file: a.pdf] [file: b.txt] [file: c.md]');

  const parts = [];
  tray.appendTo({ append: (...a) => parts.push(a) });
  assert.deepEqual(parts.map((p) => p[2]), ['a.jpg', 'a.pdf', 'b.txt', 'c.md']);
  assert.equal(parts[0][1].shrunk, true);  // the shrunk JPEG, not the original

  const remove = els['attach-chips'].children[1].children.find((c) => c.className === 'attach-remove');
  assert.equal(remove.attrs['aria-label'], 'Remove');
  await remove.click();
  assert.equal(tray.count(), 3);
  assert.equal(els.attach.disabled, false);
  assert.equal(tray.summary(), '[photo] [file: b.txt] [file: c.md]');
  tray.clear();
  assert.equal(tray.count(), 0);
  assert.equal(els['attach-chips'].hidden, true);
});

test('a photo that cannot be opened is dropped with a note', async () => {
  const { api, els, notes } = load({ withDocument: true, imageFails: true });
  els['attach-camera'].files = [file('x.jpg', 'image/jpeg')];
  await els['attach-camera'].fire('change');
  await api.tray.whenReady();
  assert.equal(api.tray.count(), 0);
  assert.deepEqual(notes.map((n) => n[1]), ["That photo couldn't be opened. Try a JPEG or PNG."]);
});

test('shrinkImage keeps photos within 1600 px as JPEG', async () => {
  const { api, doc } = load();
  const { blob, canvas } = await api.shrinkImage(file('a.jpg', 'image/jpeg'), 1600, doc);
  assert.equal(Math.max(canvas.width, canvas.height), 1600);
  assert.equal(canvas.width, 1600);
  assert.equal(canvas.height, 1200);
  assert.equal(blob.type, 'image/jpeg');
});
