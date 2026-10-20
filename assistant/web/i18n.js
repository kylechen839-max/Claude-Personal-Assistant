// Chinese phone page: when the server sends <html lang="zh-CN"> ([assistant]
// language = "zh"), translate the page's fixed English text (text, placeholder,
// aria-label, title) at load and whenever app.js / voice.js change it, and the
// questions passed to confirm(). Only exact known strings are replaced, so
// messages and replies are left as they are. With lang="en" it does nothing.
// Also: the "Connect Google account" menu button ([webapp] google_connect:
// the server marks <html data-google>), shown only when /api/google/status
// says it is set up.
"use strict";

(function (root) {
  const ZH = {
    "Enter your access token (": "请输入访问令牌（",
    "in the PC's": "，在电脑的",
    "). You only need to do this once on this phone.": "里）。这部手机只需要输入一次。",
    "Access token": "访问令牌",
    "Sign in": "登录",
    "Incognito": "无痕",
    "Backend in use": "正在使用的服务器",
    "Conversation history": "对话记录",
    "Settings": "设置",
    "Use the phone's voice": "使用手机自带的声音",
    "Don't speak replies": "不朗读回复",
    "Incognito (don't save)": "无痕模式（不保存）",
    "Sign out": "退出登录",
    "Sign out everywhere": "在所有设备上退出",
    "Yes": "是",
    "No": "不",
    'Or hold the mic and say "yes".': "或者按住麦克风说“是”。",
    "Type a message": "输入消息",
    'Type "yes" or "no"': "输入“是”或“不”",
    "Send": "发送",
    "Hold to talk, or tap to start and stop": "按住说话，或点一下开始、再点一下结束",
    "Listening… release or tap to send": "正在听…松开或点一下发送",
    "Voice conversation": "语音对话",
    "Connecting…": "正在连接…",
    "Voice orb (tap to resume when paused)": "语音球（暂停时点一下继续）",
    "Tap to continue": "点一下继续",
    "Mute": "静音",
    "Unmute": "取消静音",
    "End": "结束",
    "Captions": "字幕",
    "Latency timings": "延迟时间",
    "Listening": "正在听",
    "Thinking…": "正在思考…",
    "Speaking": "正在说话",
    "Paused": "已暂停",
    "Ended": "已结束",
    "Loading…": "正在加载…",
    "Connection closed.": "连接已断开。",
    "Paused. Tap to continue.": "已暂停。点一下继续。",
    "The microphone stopped.": "麦克风停止了。",
    "Paused while the app was in the background.": "应用在后台时已暂停。",
    "Ended after a quiet stretch.": "安静了一段时间，已结束。",
    "Paused: the microphone was interrupted.": "已暂停：麦克风被打断了。",
    "Paused: audio was interrupted. Tap to continue.": "已暂停：声音被打断了。点一下继续。",
    "(nothing heard)": "（没有听到）",
    "Conversation ended.": "对话已结束。",
    "Listening back…": "正在识别…",
    "Too short. Try again.": "太短了，请再试一次。",
    "Please sign in.": "请先登录。",
    "Sign-in failed.": "登录失败。",
    "sign-in failed": "登录失败。",
    "too many attempts; try again later": "尝试次数太多，请稍后再试。",
    "still working on the last request": "还在处理上一个请求。",
    "Sign out on every phone and browser?": "要在所有手机和浏览器上退出登录吗？",
    "This browser can't record. It needs HTTPS (see the README's Tailscale steps).":
      "这个浏览器不能录音（需要 HTTPS 地址）。",
    ["Microphone access is blocked. On iPhone: Settings → Safari → Microphone → " +
      "Allow (or in Safari, aA → Website Settings → Microphone), then reopen the app. " +
      "On Android: tap the lock icon by the address → Permissions → Microphone."]:
      "麦克风权限被关闭了。iPhone：设置 → Safari → 麦克风 → 允许，然后重新打开应用。" +
      "安卓：点地址栏旁边的锁图标 → 权限 → 麦克风。",
    "History": "历史记录",
    "Back to chat": "返回聊天",
    "Search conversations": "搜索对话",
    "Load more": "加载更多",
    "Continue this conversation": "继续这个对话",
    "Cancel": "取消",
    "Save": "保存",
    "Delete": "删除",
    "Connect Google account": "连接 Google 账户",
    "Reconnect Google account": "重新连接 Google 账户",
    "Google account connected.": "Google 账户已连接。",
    "Opening Google…": "正在打开 Google…",
    "Couldn't start the Google sign-in.": "无法开始 Google 登录。",
    "Invite someone": "邀请家人",
    "Name": "名字",
    "Language": "语言",
    "Time zone": "时区",
    "Create invite link": "生成邀请链接",
    "Invite link": "邀请链接",
    "Invite link ready.": "邀请链接已准备好。",
    "Invite link copied.": "邀请链接已复制。",
    "Copy the invite link below.": "请复制下面的邀请链接。",
    "Active": "已加入",
    "Invited": "已邀请",
    "New link": "新链接",
    "Remove": "移除",
    "This invite link has expired or was already used. Ask for a new one.":
      "这个邀请链接已过期或已经用过了，请再要一个新的。",
    "this invite link has expired or was already used": "这个邀请链接已过期或已经用过了。",
    "Couldn't reach the server. Open the link again.": "连不上服务器，请再打开一次链接。",
    // first-run guide (data-google instances: a family member's own app)
    "Enter the sign-in code you were sent. You only need to do this once on this phone.":
      "请输入发给你的登录码。这部手机只需要输入一次。",
    "Welcome! A few one-time steps:": "欢迎！只需要做一次的几个步骤：",
    "Got it": "知道了",
    ["Add this app to your home screen: in Safari, tap the Share button (the square " +
      "with an arrow) at the bottom, then \"Add to Home Screen\", and open it from there."]:
      "把这个应用加到主屏幕：在 Safari 里点下面的分享按钮（带箭头的方框），" +
      "再点“添加到主屏幕”，以后从主屏幕打开。",
    ["Connect your Google account so I can see your calendar and email: tap the gear at " +
      "the top right, then \"Connect Google account\". If Google says it hasn't verified " +
      "this app, tap \"Advanced\", then \"Go to … (unsafe)\". That's expected: this " +
      "app was made by your family, not a company."]:
      "连接你的 Google 账户，我就能看你的日历和邮件：点右上角的齿轮，再点“连接 Google 账户”。" +
      "如果 Google 说“此应用未经验证”，点“高级”，再点“转至…（不安全）”。这是正常的：" +
      "这个应用是你的家人做的，不是公司做的。",
  };
  const PATTERNS = [
    [/^Working: (.+)…$/, "正在处理：$1…"],
    [/^Error (\d+)$/, "错误 $1"],
    [/^Microphone unavailable: (.*)$/, "麦克风不可用：$1"],
    [/^Remove (.+)\? This deletes their conversations\.$/, "要移除$1吗？这会删除他们的对话记录。"],
    [/^Voice mode couldn't start: (.*)$/, "语音模式无法启动：$1"],
  ];
  const ATTRS = ["placeholder", "aria-label", "title"];

  // The Chinese for a known English string (whitespace kept), else `text`.
  function translate(text) {
    if (typeof text !== "string") return text;
    const key = text.replace(/\s+/g, " ").trim();
    if (!key) return text;
    let out = Object.prototype.hasOwnProperty.call(ZH, key) ? ZH[key] : undefined;
    if (out === undefined) {
      for (const [re, rep] of PATTERNS) {
        if (re.test(key)) { out = key.replace(re, rep); break; }
      }
    }
    if (out === undefined) return text;
    // keep the spacing, except next to full-width punctuation ("，在电脑的")
    const lead = /^[，。、：；）]/.test(out) ? "" : text.match(/^\s*/)[0];
    const trail = /[（]$/.test(out) ? "" : text.match(/\s*$/)[0];
    return lead + out + trail;
  }

  function translateAttrs(el) {
    for (const name of ATTRS) {
      const value = el.getAttribute(name);
      if (!value) continue;
      const t = translate(value);
      if (t !== value) el.setAttribute(name, t);
    }
  }

  function translateNode(node) {
    if (!node) return;
    if (node.nodeType === 3) {  // text
      const t = translate(node.nodeValue);
      if (t !== node.nodeValue) node.nodeValue = t;
      return;
    }
    if (node.nodeType !== 1 || node.tagName === "SCRIPT" || node.tagName === "STYLE") return;
    translateAttrs(node);
    for (const child of Array.from(node.childNodes)) translateNode(child);
  }

  function isActive(doc) {
    const lang = (doc && doc.documentElement && doc.documentElement.getAttribute("lang")) || "";
    return lang.toLowerCase().startsWith("zh");
  }

  // Translate now, then everything app.js / voice.js add or change later.
  function start(win, doc) {
    translateNode(doc.body);
    if (typeof win.MutationObserver === "function") {
      new win.MutationObserver((records) => {
        for (const r of records) {
          if (r.type === "childList") r.addedNodes.forEach(translateNode);
          else if (r.type === "characterData") translateNode(r.target);
          else if (r.type === "attributes") translateAttrs(r.target);
        }
      }).observe(doc.body, { subtree: true, childList: true, characterData: true,
                             attributes: true, attributeFilter: ATTRS });
    }
    for (const name of ["confirm", "alert"]) {
      const original = win[name];
      if (typeof original === "function") {
        win[name] = (message) => original.call(win, translate(String(message)));
      }
    }
  }

  // --- first-run guide: the steps only she can do on her phone ---
  const STEP_HOME = "Add this app to your home screen: in Safari, tap the Share button (the " +
    "square with an arrow) at the bottom, then \"Add to Home Screen\", and open it from there.";
  const STEP_GOOGLE = "Connect your Google account so I can see your calendar and email: tap " +
    "the gear at the top right, then \"Connect Google account\". If Google says it hasn't " +
    "verified this app, tap \"Advanced\", then \"Go to … (unsafe)\". That's expected: this " +
    "app was made by your family, not a company.";

  function standalone(win) {
    try {
      return win.navigator?.standalone === true ||
        !!(win.matchMedia && win.matchMedia("(display-mode: standalone)").matches);
    } catch (e) { return false; }
  }

  function renderGuide(win, doc, st, say) {
    const box = doc.getElementById("guide");
    const text = doc.getElementById("guide-text");
    if (!box || !text) return;
    let dismissed = false;
    try { dismissed = win.sessionStorage?.getItem("guideDismissed") === "1"; } catch (e) { /* private */ }
    const steps = [];
    if (!standalone(win)) steps.push(STEP_HOME);
    if (st.enabled && !st.connected) steps.push(STEP_GOOGLE);
    if (!steps.length || dismissed) { box.hidden = true; return; }
    text.textContent = [say("Welcome! A few one-time steps:"),
      ...steps.map((step, i) => `${i + 1}. ${say(step)}`)].join("\n");
    box.hidden = false;
  }

  // --- "Connect Google account" (google_web.py) ---
  function setupGoogle(win, doc) {
    const btn = doc.getElementById("google-connect");
    const hint = doc.getElementById("google-status");
    if (!btn) return null;
    const say = (text) => (isActive(doc) ? translate(text) : text);
    const login = doc.getElementById("login-text");
    if (login) {
      login.textContent = say("Enter the sign-in code you were sent. You only need to do this once on this phone.");
    }
    const done = doc.getElementById("guide-done");
    if (done) {
      done.addEventListener("click", () => {
        try { win.sessionStorage?.setItem("guideDismissed", "1"); } catch (e) { /* private */ }
        const box = doc.getElementById("guide");
        if (box) box.hidden = true;
      });
    }

    let signedIn = false;
    async function refresh() {
      try {
        const res = await win.fetch("/api/google/status",
                                    { credentials: "same-origin", cache: "no-store" });
        if (!res.ok) return;  // not signed in yet, or the button is off
        const st = await res.json();
        signedIn = true;
        renderGuide(win, doc, st, say);
        btn.hidden = !st.enabled;
        btn.textContent = say(st.connected ? "Reconnect Google account" : "Connect Google account");
        if (hint) {
          hint.hidden = !(st.enabled && st.connected);
          hint.textContent = say("Google account connected.");
        }
      } catch (e) { /* offline: leave it as it is */ }
    }

    btn.addEventListener("click", async () => {
      btn.disabled = true;
      if (hint) { hint.hidden = false; hint.textContent = say("Opening Google…"); }
      try {
        const res = await win.fetch("/api/google/connect", {
          method: "POST", credentials: "same-origin",
          headers: { "Content-Type": "application/json" }, body: "{}",
        });
        const body = res.ok ? await res.json() : null;
        if (body && typeof body.url === "string" && body.url.startsWith("https://")) {
          win.location.href = body.url;  // back to / after signing in
          return;
        }
      } catch (e) { /* shown below */ }
      btn.disabled = false;
      if (hint) hint.textContent = say("Couldn't start the Google sign-in.");
    });
    const menu = doc.querySelector("details.menu");
    if (menu) menu.addEventListener("toggle", () => { if (menu.open) refresh(); });
    refresh();
    // After signing in the page doesn't reload: look again until it works.
    let tries = 0;
    const timer = typeof win.setInterval === "function" ? win.setInterval(() => {
      if (signedIn || ++tries > 200) { win.clearInterval(timer); return; }
      refresh();
    }, 3000) : null;
    return { refresh };
  }

  const api = { ZH, translate, translateNode, isActive, start, setupGoogle };
  root.HeyClaudeI18n = api;
  if (typeof root.document !== "undefined") {
    const doc = root.document;
    if (isActive(doc)) start(root, doc);
    if (doc.documentElement && doc.documentElement.hasAttribute("data-google")) {
      api.google = setupGoogle(root, doc);
    }
  }
})(typeof window !== "undefined" ? window : globalThis);
