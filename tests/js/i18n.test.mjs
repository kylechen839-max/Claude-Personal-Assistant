// node --test tests/js/i18n.test.mjs  (Node 18+; no packages needed)
//
// Runs the phone page's assistant/web/i18n.js in node:vm with a minimal fake
// DOM, MutationObserver, confirm() and fetch(), and checks: nothing changes
// with lang="en"; with lang="zh-CN" known strings (text, placeholder,
// aria-label, title) are translated at load and when app.js changes them
// later, unknown text (replies) is left alone, confirm() questions are
// translated; and the "Connect Google account" button appears only when the
// server says it is set up, then sends the phone to Google's URL.

import test from 'node:test';
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import vm from 'node:vm';

const SCRIPT = readFileSync(new URL('../../assistant/web/i18n.js', import.meta.url), 'utf8');

class Text {
  constructor(value) { this.nodeType = 3; this.nodeValue = value; }
}
class El {
  constructor(tag, attrs = {}, children = []) {
    this.nodeType = 1;
    this.tagName = tag.toUpperCase();
    this.attrs = { ...attrs };
    this.childNodes = children.map((c) => (typeof c === 'string' ? new Text(c) : c));
    this.listeners = {};
    this.hidden = 'hidden' in attrs;
    this.disabled = false;
    this.open = false;
  }
  getAttribute(name) { return Object.hasOwn(this.attrs, name) ? this.attrs[name] : null; }
  setAttribute(name, value) { this.attrs[name] = String(value); }
  addEventListener(type, fn) { (this.listeners[type] ??= []).push(fn); }
  get textContent() { return this.childNodes.map((c) => c.nodeValue ?? c.textContent).join(''); }
  set textContent(v) { this.childNodes = [new Text(v)]; }
}

function page(lang, { gstatus = { enabled: true, connected: false }, connect, google: on = true } = {}) {
  const google = new El('button', { id: 'google-connect', hidden: '' }, ['Connect Google account']);
  const hint = new El('p', { id: 'google-status', hidden: '' });
  const menu = new El('details', { class: 'menu' }, [google, hint]);
  const input = new El('input', { id: 'text', placeholder: 'Type a message' });
  const mic = new El('button', { 'aria-label': 'Hold to talk, or tap to start and stop', title: 'Send' });
  const status = new El('p', { id: 'status' }, ['']);
  const reply = new El('div', { class: 'msg claude' }, ['Mute']);  // looks like a label
  const loginText = new El('p', {}, ['Enter your access token (', new El('code', {}, ['WEB_TOKEN']),
    ' in the PC\'s ', new El('code', {}, ['.env']), ').\n     You only need to do this once on this phone.']);
  const body = new El('body', {}, [loginText, menu, input, mic, status, reply]);
  const byId = { 'google-connect': google, 'google-status': hint, text: input, status };
  const observers = [];
  const fetches = [];
  const asked = [];
  const win = {
    document: {
      documentElement: Object.assign(new El('html', { lang }), {
        hasAttribute: (name) => name === 'data-google' && on,
      }),
      body,
      getElementById: (id) => byId[id] ?? null,
      querySelector: (sel) => (sel === 'details.menu' ? menu : null),
    },
    MutationObserver: class {
      constructor(fn) { this.fn = fn; observers.push(this); }
      observe(target, options) { this.target = target; this.options = options; }
    },
    confirm: (message) => { asked.push(message); return true; },
    location: { href: '/' },
    fetch: async (url, opts = {}) => {
      fetches.push({ url, opts });
      if (url === '/api/google/status') return { ok: gstatus !== null, json: async () => gstatus };
      if (url === '/api/google/connect') {
        return connect ? { ok: true, json: async () => connect } : { ok: false, json: async () => ({}) };
      }
      return { ok: false };
    },
  };
  win.window = win;
  vm.createContext(win);
  vm.runInContext(SCRIPT, win);
  const flush = () => new Promise((r) => setImmediate(r));
  const mutate = (records) => observers[0].fn(records);
  return { win, google, hint, menu, input, mic, status, reply, loginText, observers,
           fetches, asked, flush, mutate };
}

test('no data-google: the button stays hidden and nothing is fetched', async () => {
  const p = page('zh-CN', { google: false });
  await p.flush();
  assert.equal(p.google.hidden, true);
  assert.equal(p.fetches.length, 0);
});

test('English page: nothing is translated or observed', async () => {
  const p = page('en');
  await p.flush();
  assert.equal(p.input.getAttribute('placeholder'), 'Type a message');
  assert.equal(p.observers.length, 0);
  p.win.confirm('Sign out on every phone and browser?');
  assert.deepEqual(p.asked, ['Sign out on every phone and browser?']);
  // the Google button still works (English labels)
  assert.equal(p.google.hidden, false);
  assert.equal(p.google.textContent, 'Connect Google account');
});

test('Chinese page: labels, placeholders, aria-labels and titles at load', async () => {
  const p = page('zh-CN');
  await p.flush();
  assert.equal(p.input.getAttribute('placeholder'), '输入消息');
  assert.equal(p.mic.getAttribute('aria-label'), '按住说话，或点一下开始、再点一下结束');
  assert.equal(p.mic.getAttribute('title'), '发送');
  assert.equal(p.google.textContent, '连接 Google 账户');
  assert.equal(p.loginText.textContent,
               '请输入访问令牌（WEB_TOKEN，在电脑的 .env里）。这部手机只需要输入一次。');
  const opts = p.observers[0].options;
  assert.ok(opts.subtree && opts.childList && opts.characterData);
  assert.deepEqual([...opts.attributeFilter], ['placeholder', 'aria-label', 'title']);
});

test('Chinese page: later changes, patterns, and replies left alone', () => {
  const p = page('zh-CN');
  p.status.textContent = 'Thinking…';
  p.mutate([{ type: 'childList', addedNodes: p.status.childNodes }]);
  assert.equal(p.status.textContent, '正在思考…');
  p.status.textContent = 'Working: list calendar events…';
  p.mutate([{ type: 'childList', addedNodes: p.status.childNodes }]);
  assert.equal(p.status.textContent, '正在处理：list calendar events…');
  p.input.setAttribute('placeholder', 'Type "yes" or "no"');
  p.mutate([{ type: 'attributes', target: p.input }]);
  assert.equal(p.input.getAttribute('placeholder'), '输入“是”或“不”');
  const t = p.status.childNodes[0];
  t.nodeValue = 'Here is your answer: the meeting is at three.';
  p.mutate([{ type: 'characterData', target: t }]);
  assert.equal(t.nodeValue, 'Here is your answer: the meeting is at three.');
  const api = p.win.HeyClaudeI18n;
  assert.equal(api.translate('  Sign out  '), '  退出登录  ');  // whitespace kept
  assert.equal(api.translate('明天下午三点'), '明天下午三点');
  assert.equal(api.translate('constructor'), 'constructor');  // not Object.prototype
});

test('Chinese page: confirm() questions are translated', () => {
  const p = page('zh-CN');
  assert.equal(p.win.confirm('Sign out on every phone and browser?'), true);
  assert.deepEqual(p.asked, ['要在所有手机和浏览器上退出登录吗？']);
});

test('Google button: hidden unless set up; connected; click goes to Google', async () => {
  const off = page('zh-CN', { gstatus: { enabled: false, connected: false } });
  await off.flush();
  assert.equal(off.google.hidden, true);
  const signedOut = page('zh-CN', { gstatus: null });  // 401 before sign-in
  await signedOut.flush();
  assert.equal(signedOut.google.hidden, true);

  const done = page('zh-CN', { gstatus: { enabled: true, connected: true } });
  await done.flush();
  assert.equal(done.google.textContent, '重新连接 Google 账户');
  assert.equal(done.hint.hidden, false);
  assert.equal(done.hint.textContent, 'Google 账户已连接。');

  const url = 'https://accounts.google.com/o/oauth2/auth?state=s';
  const p = page('zh-CN', { connect: { url } });
  await p.flush();
  p.menu.open = true;
  for (const fn of p.menu.listeners.toggle) fn();  // opening the menu re-checks
  await p.flush();
  assert.equal(p.fetches.filter((f) => f.url === '/api/google/status').length, 2);
  for (const fn of p.google.listeners.click) await fn();
  const post = p.fetches.find((f) => f.url === '/api/google/connect');
  assert.equal(post.opts.method, 'POST');
  assert.equal(post.opts.credentials, 'same-origin');
  assert.equal(p.win.location.href, url);
});

test('Google button: a failed or non-https answer never navigates', async () => {
  for (const connect of [undefined, { url: 'javascript:alert(1)' }]) {
    const p = page('zh-CN', { connect });
    await p.flush();
    for (const fn of p.google.listeners.click) await fn();
    assert.equal(p.win.location.href, '/');
    assert.equal(p.google.disabled, false);
    assert.equal(p.hint.textContent, '无法开始 Google 登录。');
  }
});
