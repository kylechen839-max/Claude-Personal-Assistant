// node --test tests/js/settings-page.test.mjs  (Node 18+; no packages needed)
//
// The gear's <details class="menu"> is a full-screen Settings page like
// iOS Settings (kwil/web/index.html): a large title and Done, then sections
// in a fixed order, each a <div class="set-sec"> with a .group-head over a
// .group of rows. Every element the scripts look up stays inside its own
// section, and every head, Done and Settings are in Chinese (i18n.js).

import test from 'node:test';
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import vm from 'node:vm';

const HTML = readFileSync(new URL('../../kwil/web/index.html', import.meta.url), 'utf8');
const I18N = readFileSync(new URL('../../kwil/web/i18n.js', import.meta.url), 'utf8');

// [head, the ids that live in that section], in page order
const SECTIONS = [
  ['Voice', ['phone-voice', 'mute', 'voice-picker', 'voice-select', 'voice-preview']],
  ['Memory and projects', ['memory-open', 'projects-open']],
  ['Notifications', ['push-toggle', 'reminders-open', 'push-status']],
  ['Connections', ['connections', 'conn-google', 'google-connect', 'notion-connect', 'student', 'api-key']],
  ['Privacy and data', ['incognito-row', 'incognito', 'account-self', 'account-export', 'account-delete-form']],
  ['People', ['family', 'family-list', 'invite-open', 'invite-form']],
  ['Backends', ['backends', 'backend-mode', 'url-pi', 'url-pc', 'url-cloud', 'backends-save', 'backend-status']],
  ['Your account', ['logout', 'logout-all']],
];

// the markup of the <details class="menu">, and of each <div class="set-sec"> in it
function menu() {
  const start = HTML.indexOf('<details class="menu">');
  assert.ok(start >= 0, 'the menu is still a <details class="menu">');
  return HTML.slice(start, HTML.indexOf('</details>', start) + '</details>'.length);
}

function balanced(html, from) {
  const tag = /<(\/?)div\b[^>]*>/g;
  tag.lastIndex = from;
  let depth = 0;
  for (let m; (m = tag.exec(html));) {
    depth += m[1] ? -1 : 1;
    if (depth === 0) return html.slice(from, tag.lastIndex);
  }
  throw new Error('unbalanced <div>');
}

function sections() {
  const body = menu();
  const out = [];
  for (let i = body.indexOf('<div class="set-sec">'); i >= 0; i = body.indexOf('<div class="set-sec">', i + 1)) {
    const html = balanced(body, i);
    const head = html.match(/class="group-head"[^>]*>([^<]+)</);
    out.push({ head: head ? head[1].trim() : null, html, at: i });
    i += html.length - 1;
  }
  return out;
}

const translate = () => {
  const win = {};
  win.window = win;
  vm.createContext(win);
  vm.runInContext(I18N, win);
  return (s) => win.KwilI18n.translate(s);
};

test('settings page: the section heads come in order, one section each', () => {
  assert.deepEqual(sections().map((s) => s.head), SECTIONS.map(([head]) => head));
});

test('settings page: every stable id sits inside its own section', () => {
  const found = sections();
  SECTIONS.forEach(([head, ids], i) => {
    for (const id of ids) {
      assert.ok(found[i].html.includes(`id="${id}"`), `#${id} under ${head}`);
      found.forEach((other, j) => {
        if (j !== i) assert.ok(!other.html.includes(`id="${id}"`), `#${id} only under ${head}`);
      });
    }
  });
  // the notification status is the section's footnote, outside its card
  assert.match(found[2].html, /<p id="push-status" class="hint group-foot"/);
  // the Backends legend is its group head; no <section> nests in another
  assert.match(found[6].html, /<legend class="group-head">Backends<\/legend>/);
  for (const id of ['account-self', 'api-key']) {
    const part = HTML.match(new RegExp(`<section id="${id}"[\\s\\S]*?</section>`))[0];
    assert.ok(!part.slice(1).includes('<section'), `#${id} holds no <section>`);
  }
});

test('settings page: a large title and a Done button above the sections', () => {
  const body = menu();
  assert.match(body, /<summary aria-label="Settings">/);
  assert.match(body, /<h2 id="settings-title" tabindex="-1">Settings<\/h2>/);
  assert.match(body, /<button id="settings-done" type="button"[^>]*>Done<\/button>/);
  assert.ok(body.indexOf('id="settings-done"') < sections()[0].at);
});

test('settings page: every head, Done and Settings are in Chinese; no head is "Account"', () => {
  const t = translate();
  for (const en of [...SECTIONS.map(([head]) => head), 'Done', 'Settings']) {
    const zh = t(en);
    assert.notEqual(zh, en, en);
    assert.match(zh, /[一-鿿]/, en);
  }
  assert.equal(t('Memory and projects'), '记忆和项目');
  assert.equal(t('Notifications'), '通知');
  assert.equal(t('Privacy and data'), '隐私和数据');
  assert.equal(t('Your account'), '你的账户');
  // "Account" is the invite form's field (账户类型), never a section head
  const heads = [...menu().matchAll(/class="group-head"[^>]*>([^<]+)</g)].map((m) => m[1].trim());
  assert.ok(heads.length >= SECTIONS.length);
  assert.ok(!heads.includes('Account'), heads.join('|'));
});
