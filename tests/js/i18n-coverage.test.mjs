// node --test tests/js/i18n-coverage.test.mjs  (Node 18+; no packages needed)
//
// The Chinese page leaves nothing in English by accident: runs
// kwil/web/i18n.js in node:vm (as i18n.test.mjs does) and checks that every
// text node and every aria-label, placeholder and title in index.html
// (script, style and svg stripped), and every string in the scripts' fixed
// text maps (`const TEXT|*_TEXT|SOURCES|KIND = {…}`), has a translation.
// The allowlist holds proper nouns and technical text only; a new English
// string gets a ZH entry, never an allowlist line.

import test from 'node:test';
import assert from 'node:assert/strict';
import { readFileSync, readdirSync } from 'node:fs';
import vm from 'node:vm';

const WEB = new URL('../../kwil/web/', import.meta.url);
const SCRIPT = readFileSync(new URL('i18n.js', WEB), 'utf8');
const HTML = readFileSync(new URL('index.html', WEB), 'utf8');

function i18n() {
  const ctx = {};
  vm.createContext(ctx);
  vm.runInContext(SCRIPT, ctx);
  return ctx.KwilI18n;
}

// Proper nouns and technical text that stay as they are in Chinese.
const ALLOW = new Set([
  'Kwil', 'Google', 'Notion', 'Canvas', 'Home Assistant', 'Spotify', 'Anthropic',
  'WEB_TOKEN', '.env', 'sk-ant-…', 'console.anthropic.com/settings/keys',
  'English', '中文 (Chinese)',
]);
const allowed = (s) => ALLOW.has(s) || s.startsWith('https://');

const ENTITIES = { amp: '&', lt: '<', gt: '>', quot: '"', apos: "'", nbsp: ' ', hellip: '…',
                   rsquo: '’', lsquo: '‘', ldquo: '“', rdquo: '”', middot: '·', mdash: '—',
                   ndash: '–', rarr: '→', larr: '←', times: '×' };
function decode(s) {
  return s.replace(/&(#x[0-9a-f]+|#\d+|[a-z]+);/gi, (all, e) => {
    if (e[0] === '#') return String.fromCodePoint(e[1].toLowerCase() === 'x' ? parseInt(e.slice(2), 16) : +e.slice(1));
    return ENTITIES[e.toLowerCase()] ?? all;
  });
}
const norm = (s) => decode(s).replace(/\s+/g, ' ').trim();
// only text with a word in it: "0 / 4000", "·" and Chinese need nothing
const wordy = (s) => /[A-Za-z]{2,}/.test(s);

// index.html's visible text and its labelled attributes
function htmlStrings() {
  const body = HTML
    .replace(/<!--[\s\S]*?-->/g, '')
    .replace(/<(script|style|svg)\b[\s\S]*?<\/\1>/gi, '');
  const out = [];
  for (const part of body.split(/(<[^>]*>)/)) {
    if (part.startsWith('<')) {
      for (const m of part.matchAll(/\s(aria-label|placeholder|title)="([^"]*)"/g)) {
        out.push({ where: m[1], text: norm(m[2]) });
      }
    } else {
      out.push({ where: 'text', text: norm(part) });
    }
  }
  return out.filter((s) => s.text && wordy(s.text));
}

// the string values of every `const TEXT|*_TEXT|SOURCES|KIND = {…}` map
function mapStrings() {
  const out = [];
  for (const file of readdirSync(WEB).filter((f) => f.endsWith('.js')).sort()) {
    const src = readFileSync(new URL(file, WEB), 'utf8');
    for (const m of src.matchAll(/const (TEXT|[A-Z]+_TEXT|SOURCES|KIND) = \{/g)) {
      let depth = 0;
      let quote = null;
      let end = -1;
      const start = m.index + m[0].length - 1;
      for (let i = start; i < src.length && end < 0; i++) {
        const c = src[i];
        if (quote) {
          if (c === '\\') i++;
          else if (c === quote) quote = null;
        } else if (c === '"' || c === "'" || c === '`') quote = c;
        else if (c === '{') depth++;
        else if (c === '}' && --depth === 0) end = i;
      }
      const map = vm.runInNewContext(`(${src.slice(start, end + 1)})`);
      const walk = (v) => {
        if (typeof v === 'string') out.push({ where: `${file} ${m[1]}`, text: norm(v) });
        else if (v && typeof v === 'object') Object.values(v).forEach(walk);
      };
      walk(map);
    }
  }
  return out.filter((s) => s.text && wordy(s.text));
}

function untranslated(strings) {
  const { translate } = i18n();
  const missing = new Set();
  for (const { where, text } of strings) {
    if (!allowed(text) && translate(text) === text) missing.add(`${where}: ${text}`);
  }
  return [...missing];
}

test('the scan finds the page\'s strings (a broken parser would pass anything)', () => {
  const html = htmlStrings().map((s) => s.text);
  for (const s of ['Reminders', 'Your instructions', 'Sign in']) assert.ok(html.includes(s), s);
  const maps = mapStrings().map((s) => s.text);
  for (const s of ['Phone voice mode', 'Course files', 'Summary', 'Source']) assert.ok(maps.includes(s), s);
});

test('every text node, aria-label, placeholder and title in index.html translates', () => {
  assert.deepEqual(untranslated(htmlStrings()), []);
});

test('every string in the scripts\' fixed text maps translates', () => {
  assert.deepEqual(untranslated(mapStrings()), []);
});

test('the allowlist holds only names and technical text the page really shows', () => {
  const shown = new Set([...htmlStrings(), ...mapStrings()].map((s) => s.text));
  for (const s of ALLOW) {
    if (s === 'English' || s === '中文 (Chinese)') continue;  // the language picker's own names
    assert.ok(shown.has(s), `unused allowlist entry: ${s}`);
  }
});

test('a citation\'s aria-label is in Chinese', () => {
  const { translate } = i18n();
  assert.match(translate('Source 1'), /^来源 1$/);
  assert.match(translate('Source 1, 2'), /^来源 1, 2$/);
  assert.equal(translate('Source'), '来源');
});

test('a reply that happens to start with "Source" is left alone', () => {
  const { translate } = i18n();
  assert.equal(translate('Source of the Nile'), 'Source of the Nile');
});

test('History rows have no English-only template aria-label', () => {
  const src = readFileSync(new URL('history.js', WEB), 'utf8');
  // ("Actions for …" is a PATTERN; a list of joined parts can't be one)
  assert.doesNotMatch(src, /setAttribute\("aria-label",\s*`\$\{c\.title\},/);
  assert.doesNotMatch(src, /", pinned"/);
});

test('the Reminders list head is not People\'s "Active"', () => {
  const head = HTML.match(/id="reminders-items-title">([^<]*)</);
  assert.ok(head);
  assert.equal(head[1], 'Upcoming');
  assert.equal(i18n().translate('Upcoming'), '即将提醒');
});

test('the strings shown outside the maps match their ZH keys byte for byte', () => {
  const { translate } = i18n();
  for (const [file, en] of [
    ['history.js', "This is the conversation you're in now."],
    ['connect.js', 'Disconnect this service?'],
    ['reminders.js', 'No reminders yet.'],
    ['projects.js', 'No projects yet.'],
    ['app.js', "This browser can't record here. Open Kwil from its https:// address."],
    ['memory.js', 'Outside source'],
    ['reminders.js', 'Outside source'],
    ['index.html', 'Long-term notes'],
    ['index.html', "This app's address"],
    ['index.html', 'Leave empty to use the address the server suggests.'],
    ['index.html', 'For example, Asia/Shanghai'],
  ]) {
    const src = readFileSync(new URL(file, WEB), 'utf8');
    assert.ok(src.includes(en), `${file}: ${en}`);
    assert.notEqual(translate(en), en, en);
  }
  assert.equal(translate('Outside source'), '外部来源');
  assert.equal(translate('Long-term notes'), '长期笔记');
});

test('no exclamation marks in the Chinese or English UI copy', () => {
  const { ZH } = i18n();
  for (const [en, zh] of Object.entries(ZH)) {
    assert.ok(!en.includes('!'), en);
    assert.ok(!zh.includes('！') && !zh.includes('!'), zh);
  }
});
