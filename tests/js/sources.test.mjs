// node --test tests/js/sources.test.mjs  (Node 18+; no packages needed)
//
// Runs the phone page's kwil/web/sources.js in node:vm with a tiny fake DOM
// and checks a reply's sources: http(s) links only (javascript: and data:
// dropped, the superscripts renumbered), everything drawn with textContent,
// links opening in a new tab with rel="noopener noreferrer", a chat source
// opening its transcript, bounds, and the phone's own voice never reading a
// URL.

import test from 'node:test';
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import vm from 'node:vm';

const SCRIPT = readFileSync(new URL('../../kwil/web/sources.js', import.meta.url), 'utf8');

class Node {
  constructor(tag) {
    this.tagName = tag; this.children = []; this.attrs = {}; this.listeners = {};
    this.className = ''; this._text = '';
  }
  set textContent(v) { this._text = String(v); this.children = []; }
  get textContent() { return this._text + this.children.map((c) => c.textContent).join(''); }
  appendChild(c) { this.children.push(c); return c; }
  setAttribute(k, v) { this.attrs[k] = String(v); }
  addEventListener(type, fn) { (this.listeners[type] ??= []).push(fn); }
  click() { for (const fn of this.listeners.click || []) fn({}); }
  all() { return [this, ...this.children.flatMap((c) => (c.all ? c.all() : [c]))]; }
}
class TextNode { constructor(t) { this.textContent = t; this.tagName = '#text'; } }

function load() {
  const win = { URL, console };
  win.window = win;
  vm.createContext(win);
  vm.runInContext(SCRIPT, win);
  const doc = { createElement: (t) => new Node(t), createTextNode: (t) => new TextNode(t) };
  return { S: win.KwilSources, doc };
}

test('only http(s) links with a host are kept', () => {
  const { S } = load();
  for (const bad of ['javascript:alert(1)', 'JAVASCRIPT:alert(1)', 'data:text/html,x', '/x',
    'https://', 'ftp://a.b/', 'https://a b.com', ' https://a.com', 'https://a.com/\nb', null, 3,
    `https://${'a'.repeat(2001)}.com`]) {
    assert.equal(S.safeUrl(bad), null, String(bad));
  }
  assert.equal(S.safeUrl('https://www.example.com/a?b=1'), 'https://www.example.com/a?b=1');
  assert.equal(S.domain('https://www.example.com/a'), 'example.com');
});

test('a reply with web citations: superscripts and numbered links', () => {
  const { S, doc } = load();
  const bubble = new Node('div');
  const text = 'The exam is Friday. Bring a calculator.';
  const box = S.render(doc, bubble, text, {
    sources: [
      { kind: 'web', url: 'javascript:alert(1)', title: '<img src=x onerror=alert(1)>' },
      { kind: 'web', url: 'https://physics.example.edu/exams', title: 'PHYS 361 <b>exams</b>' },
      { kind: 'web', url: 'https://www.news.example.org/a', title: '' },
    ],
    marks: [[19, [1, 2]], [39, [3]], [5, [9]]],
  });
  // the unsafe one is gone and the others renumbered 1, 2
  const sups = bubble.all().filter((n) => n.tagName === 'sup');
  assert.deepEqual(sups.map((s) => s.textContent), ['1', '2']);
  assert.equal(sups[0].attrs['aria-label'], 'Source 1');
  assert.equal(bubble.children[0].textContent, 'The exam is Friday.');
  const links = box.all().filter((n) => n.tagName === 'a');
  assert.equal(links.length, 2);
  for (const a of links) {
    assert.equal(a.target, '_blank');
    assert.equal(a.rel, 'noopener noreferrer');
    assert.match(a.href, /^https:\/\//);
  }
  assert.equal(links[0].textContent, '1PHYS 361 <b>exams</b>physics.example.edu');  // text, not HTML
  assert.equal(links[1].textContent, '2news.example.orgnews.example.org');  // no title: the domain
  assert.ok(!SCRIPT.includes('innerHTML'), 'sources.js never parses HTML');
  assert.ok(!/\son[a-z]+=/.test(SCRIPT), 'no inline handlers');
});

test("Kwil's own data: one line, a chat opens its transcript", () => {
  const { S, doc } = load();
  const opened = [];
  const bubble = new Node('div');
  const box = S.render(doc, bubble, 'Mondays and Thursdays.', {
    sources: [
      { kind: 'chat', ref: 'c'.repeat(32), title: 'Chat on Tue 28 Oct' },
      { kind: 'project_file', ref: 'f1', project: 'prj-0123456789ab', title: 'syllabus.pdf' },
      { kind: 'course_file', ref: '12', title: 'PHYS 361 syllabus' },
      { kind: 'chat', title: 'no ref' },
      { kind: 'other', ref: 'x', title: 'x' },
    ],
    marks: [[22, [1]]],
  }, { openConversation: (id) => opened.push(['chat', id]),
       openProject: (id) => opened.push(['project', id]) });
  assert.equal(box.textContent, 'SourcesChat on Tue 28 Oct·syllabus.pdf·PHYS 361 syllabus');
  // own sources get no superscript (they're named in the line)
  assert.equal(bubble.all().filter((n) => n.tagName === 'sup').length, 0);
  const [chat, file, course] = box.all().filter((n) => n.className === 'source source-own');
  assert.equal(chat.tagName, 'button');
  chat.click();
  file.click();
  assert.deepEqual(opened, [['chat', 'c'.repeat(32)], ['project', 'prj-0123456789ab']]);
  assert.equal(course.tagName, 'span');
});

test('bounded like the server', () => {
  const { S } = load();
  const many = Array.from({ length: 40 }, (_, i) => (
    { kind: 'web', url: `https://s${i}.example.com/`, title: 'T'.repeat(500) }));
  const { sources, marks } = S.normalize({ sources: many,
    marks: Array.from({ length: 80 }, (_, i) => [i, [1, 2, 3, 4, 5, 6, 7]]) });
  assert.equal(sources.length, 20);
  assert.ok(sources.every((s) => s.title.length <= 200));
  assert.equal(marks.length, 50);
  assert.ok(marks.every((m) => m[1].length <= 5));
  assert.deepEqual([...S.normalize(null).sources], []);
});

test("the phone's own voice never reads a URL", () => {
  const { S } = load();
  assert.equal(S.speakable('See [the page](https://a.example/x) or https://b.example now.'),
    'See the page or now.');
});

test('marks count UTF-16 units, so they land after an emoji, never inside one', () => {
  const { S, doc } = load();
  const text = 'Party 🎉 on Friday. Then 📅 Monday.';
  const bubble = new Node('div');
  const first = 'Party 🎉 on Friday.'.length;  // the server's utf16_len
  // the second mark splits 📅's surrogate pair (one unit too early)
  const split = text.indexOf('📅') + 1;
  S.render(doc, bubble, text, {
    sources: [{ kind: 'web', url: 'https://a.example/', title: 'A' },
              { kind: 'web', url: 'https://b.example/', title: 'B' }],
    marks: [[first, [1]], [split, [2]]],
  });
  const parts = bubble.children.map((c) => (c.tagName === 'sup' ? `[${c.textContent}]` : c.textContent));
  assert.deepEqual(parts.slice(0, 2), ['Party 🎉 on Friday.', '[1]']);
  assert.equal(parts[2], ' Then 📅');  // whole emoji kept before the mark
  assert.equal(parts[3], '[2]');
  for (const p of parts) assert.ok(!/[\uD800-\uDBFF]$|^[\uDC00-\uDFFF]/.test(p), 'no half emoji');
});

test('an attached file is named, never a link or a button', () => {
  const { S, doc } = load();
  const box = S.render(doc, new Node('div'), 'Mondays.', {
    sources: [{ kind: 'attachment', title: 'plan.pdf (attached)' }, { kind: 'attachment', title: '' }] });
  const items = box.all().filter((n) => n.className === 'source source-own');
  assert.deepEqual(items.map((n) => [n.tagName, n.textContent]), [['span', 'plan.pdf (attached)']]);
});
