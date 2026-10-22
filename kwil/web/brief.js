// Notifications and the study brief (push.py, proactive.py).
// Settings: "Enable notifications" asks for permission from that tap (iOS
// only asks from a tap, and only in the app added to the Home Screen),
// subscribes this phone with the server's VAPID key (GET /api/push/key)
// and saves the subscription (POST /api/push/subscribe); tapped again it
// turns them off (POST /api/push/unsubscribe). Without push support on an
// iPhone it says to add the app to the Home Screen first.
// The brief: a notification's tap opens /#brief (sw.js), which shows the
// last message (GET /api/student/brief) with a Play button: nothing plays
// until it is tapped. Text goes in with textContent only.
// app.js calls configure({ request, show, player }) and onState(state) on
// every /api/state (request goes through the failover router when there
// is one). Loaded before app.js.
"use strict";

(function (root) {
  let request = null;   // app.js's request(url, options) -> Response
  let showView = null;  // app.js's show(view)
  let player = null;    // app.js's Audio element (unlocked on iOS by a tap)

  function configure(opts = {}) {
    if (typeof opts.request === "function") request = opts.request;
    if (typeof opts.show === "function") showView = opts.show;
    if (opts.player) player = opts.player;
  }

  const TEXT = {
    enable: "Enable notifications",
    disable: "Turn off notifications",
    on: "Notifications are on for this phone.",
    off: "Notifications are off.",
    denied: "Notifications are blocked for this app. Turn them on in the phone's Settings, then try again.",
    homeScreen: "To get notifications on an iPhone, add this app to your Home Screen (Share, then Add to Home Screen) and open it from there.",
    notReady: "This server can't store notification settings yet.",
    failed: "Couldn't turn on notifications. Try again.",
    loading: "Loading…",
    playFailed: "Couldn't play the brief.",
    latest: "Latest brief",
    week: "Your week",
  };

  // the VAPID key (base64url) as the bytes pushManager.subscribe wants
  function keyBytes(key, win = root) {
    const b64 = (key + "===".slice((key.length + 3) % 4)).replace(/-/g, "+").replace(/_/g, "/");
    const raw = win.atob(b64);
    const out = new Uint8Array(raw.length);
    for (let i = 0; i < raw.length; i += 1) out[i] = raw.charCodeAt(i);
    return out;
  }

  function sameKey(sub, bytes) {
    const have = sub && sub.options && sub.options.applicationServerKey;
    if (!have) return true;  // can't tell: keep it
    const a = new Uint8Array(have);
    return a.length === bytes.length && a.every((v, i) => v === bytes[i]);
  }

  function isBriefUrl(url) { return typeof url === "string" && url.endsWith("#brief"); }

  function setup(win, doc) {
    const $ = (id) => doc.getElementById(id);
    const toggle = $("push-toggle"), hint = $("push-status");
    const view = $("brief-view"), text = $("brief-text"), when = $("brief-when");
    const play = $("brief-play"), status = $("brief-status"), back = $("brief-back");
    if (!toggle || !view) return null;
    const nav = win.navigator || {};
    const supported = Boolean(nav.serviceWorker && win.PushManager && win.Notification);
    const iphone = /iPhone|iPad|iPod/.test(nav.userAgent || "");
    let key = null, ready = false, subscribed = false, busy = false;
    let audio = null, objectUrl = null, opened = false;

    async function call(url, options = {}) {
      const res = request ? await request(url, options)
        : await win.fetch(url, { credentials: "same-origin", ...options });
      let body = {};
      try { body = await res.json(); } catch (e) { /* not JSON */ }
      if (!res.ok) throw Object.assign(new Error(body.error || `Error ${res.status}`), { status: res.status });
      return body;
    }
    const post = (url, payload) => call(url, {
      method: "POST", headers: { "Content-Type": "application/json" },
      body: JSON.stringify(payload || {}) });

    function say(message) { hint.textContent = message || ""; hint.hidden = !message; }

    function render() {
      toggle.textContent = subscribed ? TEXT.disable : TEXT.enable;
      toggle.disabled = busy;
    }

    async function registration() {
      return nav.serviceWorker.ready;
    }

    async function refresh() {
      if (!supported) {
        toggle.hidden = true;
        say(iphone && nav.serviceWorker ? TEXT.homeScreen : "");
        return;
      }
      if (key === null) {
        try {
          const body = await call("/api/push/key");
          key = body.key; ready = Boolean(body.ready);
        } catch (e) {
          toggle.hidden = true; say(""); return;  // off on this server
        }
      }
      try {
        const reg = await registration();
        subscribed = Boolean(await reg.pushManager.getSubscription())
          && win.Notification.permission === "granted";
      } catch (e) { subscribed = false; }
      toggle.hidden = false;
      render();
    }

    async function enable() {
      if (!ready) { say(TEXT.notReady); return; }
      // first thing in the tap: iOS only shows the prompt from a user gesture
      const permission = await win.Notification.requestPermission();
      if (permission !== "granted") { say(TEXT.denied); return; }
      const bytes = keyBytes(key, win);
      const reg = await registration();
      let sub = await reg.pushManager.getSubscription();
      if (sub && !sameKey(sub, bytes)) { await sub.unsubscribe(); sub = null; }
      if (!sub) sub = await reg.pushManager.subscribe({ userVisibleOnly: true, applicationServerKey: bytes });
      await post("/api/push/subscribe", { subscription: sub.toJSON() });
      subscribed = true;
      say(TEXT.on);
    }

    async function disable() {
      const reg = await registration();
      const sub = await reg.pushManager.getSubscription();
      if (sub) {
        await post("/api/push/unsubscribe", { endpoint: sub.endpoint });
        await sub.unsubscribe();
      }
      subscribed = false;
      say(TEXT.off);
    }

    toggle.addEventListener("click", async () => {
      if (busy) return;
      busy = true; render();
      try {
        if (subscribed) await disable(); else await enable();
      } catch (e) {
        say(e && e.status === 503 ? TEXT.notReady : (subscribed ? (e.message || TEXT.failed) : TEXT.failed));
      } finally {
        busy = false; render();
      }
    });

    async function open() {
      const menu = doc.querySelector && doc.querySelector("details.menu");
      if (menu) menu.open = false;
      if (showView) showView("brief-view"); else view.hidden = false;
      text.textContent = TEXT.loading;
      when.textContent = ""; status.textContent = "";
      play.hidden = true; audio = null;
      try {
        const body = await call("/api/student/brief");
        text.textContent = body.text || "";
        when.textContent = body.at ? TEXT.latest : TEXT.week;
        audio = body.audio || null;
        play.hidden = !audio;
      } catch (e) {
        text.textContent = "";
        status.textContent = e.message || String(e);
      }
    }

    play.addEventListener("click", async () => {
      if (!audio) return;
      status.textContent = "";
      try {
        const res = request ? await request(audio) : await win.fetch(audio, { credentials: "same-origin" });
        if (!res.ok) throw new Error("no audio");
        const blob = await res.blob();
        if (objectUrl) win.URL.revokeObjectURL(objectUrl);
        objectUrl = win.URL.createObjectURL(blob);
        const out = player || new win.Audio();
        out.src = objectUrl;
        await out.play();
      } catch (e) {
        status.textContent = TEXT.playFailed;
      }
    });

    if (back) back.addEventListener("click", () => {
      if (win.history && win.location && win.location.hash === "#brief") {
        win.history.replaceState(null, "", win.location.pathname);
      }
      if (showView) showView("app"); else view.hidden = true;
    });

    if (win.addEventListener) {
      win.addEventListener("hashchange", () => { if (win.location.hash === "#brief") open(); });
    }
    if (nav.serviceWorker && nav.serviceWorker.addEventListener) {
      nav.serviceWorker.addEventListener("message", (e) => {  // sw.js: a notification tapped
        if (e.data && e.data.type === "open" && isBriefUrl(e.data.url)) open();
      });
    }

    return {
      refresh, open,
      openFromLink() {  // the first /api/state after a tap that opened the app
        if (opened) return null;
        opened = true;
        return win.location && win.location.hash === "#brief" ? open() : null;
      },
      get subscribed() { return subscribed; },
    };
  }

  let screen = null;
  // From app.js after each /api/state (signed in).
  function onState(state, win = root, doc = root.document) {
    if (!doc) return null;
    if (!screen) screen = setup(win, doc);
    if (!screen) return null;
    return Promise.all([screen.refresh(), screen.openFromLink()]);
  }

  root.KwilBrief = { configure, onState, setup, keyBytes, TEXT };
})(typeof window !== "undefined" ? window : globalThis);
