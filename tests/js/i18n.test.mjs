// node --test tests/js/i18n.test.mjs  (Node 18+; no packages needed)
//
// Runs the phone page's kwil/web/i18n.js in node:vm with a minimal fake
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

const SCRIPT = readFileSync(new URL('../../kwil/web/i18n.js', import.meta.url), 'utf8');

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

function page(lang, { gstatus = { enabled: true, connected: false }, connect, google: on = true,
                      standalone = false, local = {}, disconnectOk = true, answer = true } = {}) {
  const google = new El('button', { id: 'google-connect', hidden: '' }, ['Connect Google account']);
  const unlink = new El('button', { id: 'google-disconnect', hidden: '' }, ['Disconnect Google account']);
  const hint = new El('p', { id: 'google-status', hidden: '' });
  const menu = new El('details', { class: 'menu' }, [google, hint]);
  const input = new El('input', { id: 'text', placeholder: 'Type a message' });
  const mic = new El('button', { 'aria-label': 'Hold to talk, or tap to start and stop', title: 'Send' });
  const status = new El('p', { id: 'status' }, ['']);
  const reply = new El('div', { class: 'msg claude' }, ['Mute']);  // looks like a label
  const loginText = new El('p', {}, ['Enter your access token (', new El('code', {}, ['WEB_TOKEN']),
    ' in the PC\'s ', new El('code', {}, ['.env']), ').\n     You only need to do this once on this phone.']);
  const guide = new El('section', { id: 'guide', hidden: '' });
  const guideText = new El('p', { id: 'guide-text' });
  const guideDone = new El('button', { id: 'guide-done' }, ['Got it']);
  const loginP = new El('p', { id: 'login-text' }, ['Enter your access token']);
  const loginForm = new El('form', { id: 'login-form' });
  const body = new El('body', {}, [loginText, menu, input, mic, status, reply]);
  const byId = { 'google-connect': google, 'google-disconnect': unlink, 'google-status': hint, text: input, status,
                 guide, 'guide-text': guideText, 'guide-done': guideDone, 'login-text': loginP,
                 'login-form': loginForm };
  const timers = new Map();  // setInterval: the test ticks them by hand
  let nextTimer = 1;
  const stored = {};
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
    confirm: (message) => { asked.push(message); return answer; },
    location: { href: '/' },
    navigator: { standalone },
    sessionStorage: { getItem: (k) => stored[k] ?? null, setItem: (k, v) => { stored[k] = v; } },
    localStorage: { getItem: (k) => local[k] ?? null, setItem: (k, v) => { local[k] = v; } },
    fetch: async (url, opts = {}) => {
      fetches.push({ url, opts });
      if (url === '/api/google/status') {
        return { ok: gstatus !== null, status: gstatus === null ? 401 : 200, json: async () => gstatus };
      }
      if (url === '/api/google/disconnect') {
        if (disconnectOk) gstatus = { ...gstatus, connected: false };
        return { ok: disconnectOk, json: async () => ({}) };
      }
      if (url === '/api/google/connect') {
        return connect ? { ok: true, json: async () => connect } : { ok: false, json: async () => ({}) };
      }
      return { ok: false };
    },
  };
  win.setInterval = (fn) => { timers.set(nextTimer, fn); return nextTimer++; };
  win.clearInterval = (id) => { timers.delete(id); };
  win.window = win;
  vm.createContext(win);
  vm.runInContext(SCRIPT, win);
  const flush = () => new Promise((r) => setImmediate(r));
  const mutate = (records) => observers[0].fn(records);
  return { win, guide, guideText, guideDone, loginP, google, unlink, hint, menu, input, mic, status, reply, loginText, observers,
           fetches, asked, flush, mutate, loginForm, timers,
           setStatus: (st) => { gstatus = st; } };
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
  const api = p.win.KwilI18n;
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

test('Google refused the sign-in: "expired, reconnect", never "connected"', async () => {
  // the owner on the cloud server: GOOGLE_TOKEN_JSON revoked (needs_reconnect)
  const expired = { enabled: true, connected: true, disconnect: false, server_owner: true,
                    needs_reconnect: true };
  const url = 'https://accounts.google.com/o/oauth2/auth?state=r';
  const en = page('en', { gstatus: expired, connect: { url } });
  await en.flush();
  assert.equal(en.google.hidden, false);
  assert.equal(en.google.textContent, 'Reconnect Google account');
  assert.equal(en.hint.hidden, false);
  assert.equal(en.hint.textContent, 'Google sign-in expired. Reconnect to keep using it.');
  for (const fn of en.google.listeners.click) await fn();  // the same connect flow
  assert.equal(en.fetches.find((f) => f.url === '/api/google/connect').opts.method, 'POST');
  assert.equal(en.win.location.href, url);

  const zh = page('zh-CN', { gstatus: expired });
  await zh.flush();
  assert.equal(zh.google.textContent, '重新连接 Google 账户');
  assert.equal(zh.hint.textContent, 'Google 登录已过期。请重新连接后继续使用。');

  // nothing to connect with here: still says it expired, no button
  const off = page('zh-CN', { gstatus: { ...expired, enabled: false } });
  await off.flush();
  assert.equal(off.google.hidden, true);
  assert.equal(off.hint.hidden, false);
  assert.equal(off.hint.textContent, 'Google 登录已过期。');

  // once reconnected the next status clears it
  const p = page('en', { gstatus: expired });
  await p.flush();
  p.setStatus({ ...expired, needs_reconnect: false });
  p.menu.open = true;
  for (const fn of p.menu.listeners.toggle) fn();
  await p.flush();
  assert.equal(p.hint.textContent, 'Google account connected.');
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

test('first-run guide: home screen and Google steps in Chinese, then gone', async () => {
  const p = page('zh-CN');
  await p.flush();
  assert.equal(p.loginP.textContent, '请输入发给你的登录码。这部手机只需要输入一次。');
  assert.equal(p.guide.hidden, false);
  assert.match(p.guideText.textContent, /^欢迎！/);
  assert.match(p.guideText.textContent, /1\. 把这个应用加到主屏幕/);
  assert.match(p.guideText.textContent, /2\. 连接你的 Google 账户/);
  assert.match(p.guideText.textContent, /点“高级”/);
  assert.match(p.guideText.textContent, /你可以这样问我/);
  assert.doesNotMatch(p.guideText.textContent, /我明天有什么安排/);  // no calendar before Google
  p.guideDone.listeners.click[0]();
  assert.equal(p.guide.hidden, true);
  await p.win.document.getElementById('google-connect');  // refresh again: stays dismissed
});

test('first-run guide: only the steps still to do; none when all done', async () => {
  const home = page('zh-CN', { standalone: true });
  await home.flush();
  assert.doesNotMatch(home.guideText.textContent, /主屏幕/);
  assert.match(home.guideText.textContent, /1\. 连接你的 Google 账户/);
  // setup done: the card shows only what she can ask, including calendar and email
  const local = {};
  const done = page('zh-CN', { standalone: true, gstatus: { enabled: true, connected: true }, local });
  await done.flush();
  assert.equal(done.guide.hidden, false);
  assert.doesNotMatch(done.guideText.textContent, /步骤/);
  assert.match(done.guideText.textContent, /^你可以这样问我/);
  assert.match(done.guideText.textContent, /“明天天气怎么样？”/);
  assert.match(done.guideText.textContent, /“我明天有什么安排？”/);
  done.guideDone.listeners.click[0]();
  assert.equal(local.examplesSeen, '1');
  const later = page('zh-CN', { standalone: true, gstatus: { enabled: true, connected: true }, local });
  await later.flush();
  assert.equal(later.guide.hidden, true);  // seen once: gone for good
});

test('first-run guide: not signed in yet shows nothing; no data-google never shows it', async () => {
  const out = page('zh-CN', { gstatus: null });
  await out.flush();
  assert.equal(out.guide.hidden, true);
  const owner = page('en', { google: false });
  await owner.flush();
  assert.equal(owner.guide.hidden, true);
  assert.equal(owner.loginP.textContent, 'Enter your access token');
});

test('Chinese page: tabs, settings, history and tutor labels are all translated', () => {
  const { win } = page('zh-CN');
  const t = (s) => win.KwilI18n.translate(s);
  for (const [en, zh] of [['Chat', '聊天'], ['Lecture', '讲座'], ['Tutor', '作业辅导'],
    ['Backends', '服务器'], ['Save and check', '保存并检查'], ['Pinned', '已置顶'],
    ['Rename', '重命名'], ['Today', '今天'], ['Take photo', '拍照'], ['CC', '字幕'],
    [' Show the full solution', ' 显示完整解答'], ['Auto: Pi, then PC, then cloud', '自动：先树莓派，再电脑，再云端']]) {
    assert.equal(t(en), zh, en);
  }
  assert.equal(t('12 messages'), '12 条消息');
  assert.equal(t('Actions for Dentist'), 'Dentist 的操作');
  assert.equal(t('Transcribing 40%'), '正在转写 40%');
  assert.equal(t('Kwil'), 'Kwil');  // the app name stays untranslated
});

test('Disconnect: shown only to a family member who is connected; asks, then posts', async () => {
  const owner = page('en', { gstatus: { enabled: true, connected: true, disconnect: false } });
  await owner.flush();
  assert.equal(owner.unlink.hidden, true);  // the owner's token is managed on the PC
  const notYet = page('zh-CN', { gstatus: { enabled: true, connected: false, disconnect: true } });
  await notYet.flush();
  assert.equal(notYet.unlink.hidden, true);

  const p = page('zh-CN', { gstatus: { enabled: true, connected: true, disconnect: true } });
  await p.flush();
  assert.equal(p.unlink.hidden, false);
  assert.equal(p.unlink.textContent, 'Disconnect Google account');  // translated by start() in a real page
  for (const fn of p.unlink.listeners.click) await fn();
  assert.deepEqual(p.asked, ['要断开你的 Google 账户吗？之后我就看不到你的日历和邮件了。']);
  const post = p.fetches.find((f) => f.url === '/api/google/disconnect');
  assert.equal(post.opts.method, 'POST');
  assert.equal(post.opts.credentials, 'same-origin');
  await p.flush();
  assert.equal(p.unlink.hidden, true);  // refreshed: not connected any more
  assert.equal(p.hint.textContent, 'Google 账户已断开。');
  assert.equal(p.google.textContent, '连接 Google 账户');
});

test('Disconnect: the cloud owner gets it for a token connected here, with its own warning', async () => {
  const st = { enabled: true, connected: true, disconnect: true, gmail: true, note: null,
               server_owner: true };
  const p = page('en', { gstatus: st });
  await p.flush();
  assert.equal(p.unlink.hidden, false);
  for (const fn of p.unlink.listeners.click) await fn();
  assert.equal(p.asked.length, 1);
  assert.match(p.asked[0], /^Disconnect the Google account connected here\?/);
  assert.match(p.asked[0], /calendar or email/);
  assert.match(p.asked[0], /may also sign out Kwil's Google on your PC/);
  assert.equal(p.fetches.filter((f) => f.url === '/api/google/disconnect').length, 1);
  // the same in Chinese, and without mail wording when Gmail is off
  const zh = page('zh-CN', { gstatus: { ...st, gmail: false } });
  await zh.flush();
  for (const fn of zh.unlink.listeners.click) await fn();
  assert.equal(zh.asked[0],
    '要断开在这里连接的 Google 账户吗？之后我就不能再通过它看你的日历了。这也可能会让电脑上的 Kwil 退出 Google（同一个 Google 项目）。');
  // the env's token only (disconnect false): nothing to disconnect here
  const env = page('en', { gstatus: { ...st, disconnect: false } });
  await env.flush();
  assert.equal(env.unlink.hidden, true);
});

test('signed out: a 401 stops the status polling; signing in starts it again', async () => {
  const p = page('en', { gstatus: null });  // the cloud's signed-out page
  await p.flush();
  assert.equal(p.timers.size, 0);  // stopped after the first 401
  // the cloud's login line is the invitees' (data-google on the signed-out page)
  assert.equal(p.loginP.textContent,
    'Enter the sign-in code you were sent. You only need to do this once on this phone.');
  const asked = () => p.fetches.filter((f) => f.url === '/api/google/status').length;
  assert.equal(asked(), 1);
  // the owner signs in with the form (the page doesn't reload)
  for (const fn of p.loginForm.listeners.submit) fn();
  assert.equal(p.timers.size, 1);
  p.setStatus({ enabled: true, connected: false, disconnect: false, server_owner: true });
  for (const fn of [...p.timers.values()]) fn();
  await p.flush();
  assert.equal(asked(), 2);
  assert.equal(p.google.hidden, false);  // the Connect button appeared
  for (const fn of [...p.timers.values()]) fn();  // signed in: polling ends
  assert.equal(p.timers.size, 0);
  // a wrong token: the next poll's 401 stops it again
  const q = page('en', { gstatus: null });
  await q.flush();
  for (const fn of q.loginForm.listeners.submit) fn();
  for (const fn of [...q.timers.values()]) fn();
  await q.flush();
  assert.equal(q.timers.size, 0);
});

test('first-run guide: off for the cloud owner, on for an invited person', async () => {
  const owner = page('en', { gstatus: { enabled: true, connected: false, disconnect: false,
                                        server_owner: true } });
  await owner.flush();
  assert.equal(owner.guide.hidden, true);
  assert.equal(owner.google.hidden, false);  // the Connect button still shows
  const member = page('en', { gstatus: { enabled: true, connected: false, disconnect: true,
                                         server_owner: false } });
  await member.flush();
  assert.equal(member.guide.hidden, false);
});

test('Disconnect: a "no" sends nothing; a failure says so', async () => {
  const no = page('en', { gstatus: { enabled: true, connected: true, disconnect: true }, answer: false });
  await no.flush();
  for (const fn of no.unlink.listeners.click) await fn();
  assert.equal(no.fetches.filter((f) => f.url === '/api/google/disconnect').length, 0);
  const bad = page('en', { gstatus: { enabled: true, connected: true, disconnect: true },
                           disconnectOk: false });
  await bad.flush();
  for (const fn of bad.unlink.listeners.click) await fn();
  assert.equal(bad.hint.textContent, "Couldn't disconnect. Try again.");
  assert.equal(bad.unlink.disabled, false);
});

test('first-run guide: the Google step is about their own account', async () => {
  const en = page('en');
  await en.flush();
  assert.match(en.guideText.textContent, /Connect your own Google account/);
  assert.match(en.guideText.textContent, /sign in with your own Google account/);
  const zh = page('zh-CN');
  await zh.flush();
  assert.match(zh.guideText.textContent, /用你自己的 Google 账户登录/);
});

test('Chinese page: attachment markers are shown in Chinese, other text is not', () => {
  const p = page('zh-CN');
  const t = (s) => p.win.KwilI18n.translate(s);
  // the "me" bubble: typed text, then the marker line (attachments.js summary())
  assert.equal(t('what is this?\n[photo] [file: plan.pdf]'), 'what is this?\n[照片] [文件：plan.pdf]');
  assert.equal(t('[photo]'), '[照片]');
  assert.equal(t('[file: notes.txt] [photo] '), '[文件：notes.txt] [照片] ');
  // a marker that isn't the whole last line is someone's words: left alone
  assert.equal(t('I typed [photo] myself'), 'I typed [photo] myself');
  assert.equal(t('[photo]\nand some text'), '[photo]\nand some text');
  // as app.js adds it
  const div = { nodeType: 1, tagName: 'DIV', getAttribute: () => null, setAttribute() {},
                childNodes: [{ nodeType: 3, nodeValue: '看看\n[photo]' }] };
  p.mutate([{ type: 'childList', addedNodes: [div] }]);
  assert.equal(div.childNodes[0].nodeValue, '看看\n[照片]');
});

test('Chinese page: the new attachment errors are translated', () => {
  const t = (s) => page('zh-CN').win.KwilI18n.translate(s);
  assert.equal(t('bad request'), '请求有误。');
  assert.equal(t('A PDF is over 100 pages'), '有一个 PDF 超过 100 页。');
  assert.equal(t('That file is too long for Claude (PDFs up to 100 pages).'),
               '这个文件太长了，Claude 处理不了（PDF 最多 100 页）。');
  assert.equal(t("Claude couldn't read that file."), 'Claude 读不了这个文件。');
});

test('Chinese page: queued messages and Stop are translated', () => {
  const t = (s) => page('zh-CN').win.KwilI18n.translate(s);
  assert.equal(t('Queued'), '排队中');
  assert.equal(t('Remove queued message'), '移除排队的消息');
  assert.equal(t('Stop'), '停止');
  assert.equal(t('Stopped.'), '已停止。');
  assert.equal(t('You can queue up to 3 messages.'), '最多可以排队 3 条消息。');
});

test('a beta tester (gmail false) never sees mail wording', async () => {
  const gs = { enabled: true, connected: false, disconnect: true, gmail: false, note: null };
  for (const lang of ['en', 'zh-CN']) {
    const p = page(lang, { gstatus: gs });
    await p.flush();
    assert.equal(p.guide.hidden, false);
    assert.doesNotMatch(p.guideText.textContent, /email|邮件|family|家人/);
    assert.match(p.guideText.textContent, lang === 'en' ? /see your calendar:/ : /我就能看你的日历：/);
  }
  const done = page('en', { gstatus: { ...gs, connected: true }, standalone: true });
  await done.flush();
  assert.match(done.guideText.textContent, /What's on my calendar tomorrow/);
  assert.doesNotMatch(done.guideText.textContent, /email/);
  for (const fn of done.unlink.listeners.click) await fn();
  assert.deepEqual(done.asked, ["Disconnect your Google account? I won't see your calendar any more."]);
  const zh = page('zh-CN', { gstatus: { ...gs, connected: true }, standalone: true });
  await zh.flush();
  for (const fn of zh.unlink.listeners.click) await fn();
  assert.deepEqual(zh.asked, ['要断开你的 Google 账户吗？之后我就看不到你的日历了。']);
  // a family member's status keeps today's wording
  const fam = page('en', { gstatus: { ...gs, gmail: true, connected: true }, standalone: true });
  await fam.flush();
  assert.match(fam.guideText.textContent, /Any new email today/);
});

test('a beta tester without the public client sees why Google is off', async () => {
  const note = "Google isn't set up for beta accounts yet.";
  for (const [lang, said] of [['en', note], ['zh-CN', '测试版账户暂时还不能连接 Google。']]) {
    const p = page(lang, { gstatus: { enabled: false, connected: false, disconnect: true,
                                      gmail: false, note } });
    await p.flush();
    assert.equal(p.google.hidden, true);
    assert.equal(p.hint.hidden, false);
    assert.equal(p.hint.textContent, said);
  }
});

test('the invite form\'s tier labels are translated', () => {
  const { win } = page('zh-CN');
  const t = (s) => win.KwilI18n.translate(s);
  assert.equal(t('Account'), '账户类型');
  assert.equal(t('Beta (Calendar only)'), '测试版（只有日历）');
  assert.equal(t('Family (Calendar and Gmail)'), '家人（日历和 Gmail）');
  assert.equal(t('Active · Beta'), '已加入 · 测试版');
});

test('the account section (Download my data, Delete my account) is all in Chinese', () => {
  const { win } = page('zh-CN');
  const t = (s) => win.KwilI18n.translate(s);
  // every message family.js shows there...
  const famWin = {};
  famWin.window = famWin;
  vm.createContext(famWin);
  vm.runInContext(readFileSync(new URL('../../kwil/web/family.js', import.meta.url), 'utf8'), famWin);
  const messages = Object.values(famWin.KwilFamily.ACCOUNT_TEXT);
  assert.ok(messages.length >= 10);
  // ...and every text in its markup
  const html = readFileSync(new URL('../../kwil/web/index.html', import.meta.url), 'utf8');
  const section = html.match(/<section id="account-self"[\s\S]*?<\/section>/)[0];
  const texts = [...section.matchAll(/>([^<>]+)</g)].map((m) => m[1].replace(/\s+/g, ' ').trim()).filter(Boolean);
  assert.ok(texts.includes('Download my data') && texts.includes('Delete my account'));
  for (const en of [...messages, ...texts]) {
    const zh = t(en);
    assert.notEqual(zh, en, en);
    assert.match(zh, /[一-鿿]/, en);
  }
});

test('every string of the "Your Anthropic API key" settings is translated', () => {
  const { win } = page('zh-CN');
  const t = (s) => win.KwilI18n.translate(s);
  // the section's own text in index.html (text nodes and the aria-label)
  const html = readFileSync(new URL('../../kwil/web/index.html', import.meta.url), 'utf8');
  const section = html.match(/<section id="api-key"[\s\S]*?<\/section>/)[0];
  const texts = [...section.matchAll(/>([^<>]+)</g)].map((m) => m[1].replace(/\s+/g, ' ').trim())
    .filter((x) => x && !x.startsWith('console.anthropic.com'));  // the address stays
  texts.push(section.match(/aria-label="([^"]+)"/)[1]);
  assert.ok(texts.length >= 5, texts.join('|'));
  for (const en of texts) assert.notEqual(t(en), en, en);
  // what family.js and the server (userkeys_web.py, userkeys.py) say there
  const family = readFileSync(new URL('../../kwil/web/family.js', import.meta.url), 'utf8');
  const keyPart = family.slice(family.indexOf('function setupKey'), family.indexOf('let owner = null'));
  const said = [...keyPart.matchAll(/"([A-Z][^"]* [^"]{4,})"/g)].map((m) => m[1]);  // sentences
  said.push('Save key', 'Replace key');
  assert.ok(said.length >= 8, said.join('|'));
  for (const en of said) assert.notEqual(t(en), en, en);
  for (const en of ["that doesn't look like an Anthropic API key (it starts with sk-ant-)",
    "Anthropic didn't accept that key", "couldn't reach Anthropic to check the key; try again",
    "couldn't check the key with Anthropic; try again", 'too many tries; wait a minute',
    "this server can't store keys yet (its owner needs to set USER_DATA_KEY)"]) {
    assert.notEqual(t(en), en, en);
  }
  assert.equal(t('Your key ending in wxyz is saved.'), '你的密钥（结尾 wxyz）已保存。');
  assert.equal(t('About $1.25 this month on your key.'), '这个月用你的密钥大约花了 $1.25。');
});
