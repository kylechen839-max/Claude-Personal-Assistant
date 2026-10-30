// Settings, Reminders (reminders.py): everyone's own reminders and
// scheduled tasks, when /api/state says {"reminders": true}. "Reminders" in
// the settings menu opens the list: each active one with its text, its
// next time in the person's own time zone and its repeat ("every Monday at
// 8:00 AM"), a "Task" badge for a scheduled prompt and "Outside source" for one
// made while outside text was in the conversation; Pause / Resume and
// Delete (asks first). Missed one-time reminders (the server was down) are
// listed under "Missed". A notification's tap (/#reminders) opens it.
// Times and repeats come from the server already worded in the person's
// language. Text goes in with textContent only.
// app.js calls configure({ request, show }) and onState(state) on every
// /api/state (request goes through the failover router when there is one).
// Loaded before app.js.
"use strict";

(function (root) {
  let request = null;   // app.js's request(url, options) -> Response
  let showView = null;  // app.js's show(view)

  function configure(opts = {}) {
    if (typeof opts.request === "function") request = opts.request;
    if (typeof opts.show === "function") showView = opts.show;
  }

  const TEXT = {
    empty: "No reminders yet.",
    paused: "Paused",
    task: "Task",
    untrusted: "Outside source",
    untrustedHint: "Made while a web page or email was in the conversation",
    pause: "Pause",
    resume: "Resume",
    delete: "Delete",
    ask: "Delete this reminder? This can't be undone.",
    deleted: "Deleted.",
    pausedNow: "Reminder paused.",
    resumedNow: "Reminder on.",
    missed: "Missed",
  };
  const URL_HASH = "#reminders";

  function setup(win, doc) {
    const $ = (id) => doc.getElementById(id);
    const view = $("reminders-view"), openBtn = $("reminders-open"), back = $("reminders-back");
    const items = $("reminders-items"), empty = $("reminders-empty");
    const missedHead = $("reminders-missed-title"), missedList = $("reminders-missed");
    const pushNote = $("reminders-push"), status = $("reminders-status");
    if (!view || !items || !openBtn) return null;
    let data = null, seq = 0, on = false;
    const say = (text) => { if (status) status.textContent = text; };

    function el(tag, cls, text) {
      const e = doc.createElement(tag);
      if (cls) e.className = cls;
      if (text !== undefined) e.textContent = text;
      return e;
    }

    async function call(url, options = {}) {
      const res = request ? await request(url, options)
        : await win.fetch(url, { credentials: "same-origin", ...options });
      let body = {};
      try { body = await res.json(); } catch (e) { /* not JSON */ }
      if (!res.ok) throw new Error(body.error || `Error ${res.status}`);
      return body;
    }
    // POST, not DELETE: with failover this goes cross-origin, and the
    // backends' preflight allows only GET and POST
    const post = (url, payload) => call(url, {
      method: "POST", headers: { "Content-Type": "application/json" },
      body: JSON.stringify(payload || {}) });

    function node(r) {
      const row = el("div", "mem-item rem-item");
      row.dataset.id = r.id;
      row.setAttribute("role", "listitem");
      const top = el("div", "mem-top");
      const when = r.state === "missed" ? TEXT.missed : r.paused ? TEXT.paused : r.next_local;
      top.appendChild(el("span", "mem-when rem-when", when || ""));
      if (r.prompt_task) top.appendChild(el("span", "badge rem-task", TEXT.task));
      if (r.untrusted) {
        const flag = el("span", "badge mem-untrusted", TEXT.untrusted);
        flag.setAttribute("title", TEXT.untrustedHint);
        top.appendChild(flag);
      }
      if (r.state === "active") {
        const toggle = el("button", "mem-edit rem-pause", r.paused ? TEXT.resume : TEXT.pause);
        toggle.type = "button";
        toggle.addEventListener("click", () => setPaused(r, !r.paused));
        top.appendChild(toggle);
      }
      const del = el("button", "mem-delete danger rem-delete", TEXT.delete);
      del.type = "button";
      del.addEventListener("click", () => remove(r));
      top.appendChild(del);
      row.appendChild(top);
      row.appendChild(el("p", "mem-text", r.text));
      if (r.repeat) row.appendChild(el("p", "mem-when rem-repeat", r.repeat));
      return row;
    }

    function render() {
      const all = (data && data.reminders) || [];
      const active = all.filter((r) => r.state === "active");
      const missed = all.filter((r) => r.state === "missed");
      items.textContent = "";
      for (const r of active) items.appendChild(node(r));
      items.hidden = !active.length;
      if (empty) { empty.textContent = active.length ? "" : TEXT.empty; empty.hidden = Boolean(active.length); }
      if (missedList) {
        missedList.textContent = "";
        for (const r of missed) missedList.appendChild(node(r));
        missedList.hidden = !missed.length;
      }
      if (missedHead) missedHead.hidden = !missed.length;
      if (pushNote) pushNote.hidden = !(data && data.notifications === false);
    }

    async function load() {
      const me = ++seq;
      say("");
      try {
        const body = await call("/api/reminders");
        if (me !== seq) return;
        data = body;
        render();
      } catch (e) { if (me === seq) say(e.message || String(e)); }
    }

    async function setPaused(r, paused) {
      try {
        await post(`/api/reminders/${encodeURIComponent(r.id)}/pause`, { paused });
        await load();
        say(paused ? TEXT.pausedNow : TEXT.resumedNow);
      } catch (e) { say(e.message || String(e)); }
    }

    async function remove(r) {
      if (!win.confirm(TEXT.ask)) return;
      try {
        await post(`/api/reminders/${encodeURIComponent(r.id)}/delete`);
        await load();
        say(TEXT.deleted);
      } catch (e) { say(e.message || String(e)); }
    }

    function open() {
      if (!on) return null;
      const menu = doc.querySelector("details.menu");
      if (menu) menu.open = false;
      if (showView) showView("reminders-view"); else view.hidden = false;
      return load();
    }

    openBtn.addEventListener("click", open);
    if (back) back.addEventListener("click", () => {
      if (win.history && win.location && win.location.hash === URL_HASH) {
        win.history.replaceState(null, "", win.location.pathname);
      }
      if (showView) showView("app"); else view.hidden = true;
    });
    if (win.addEventListener) {
      win.addEventListener("hashchange", () => { if (win.location.hash === URL_HASH) open(); });
    }
    const nav = win.navigator || {};
    if (nav.serviceWorker && nav.serviceWorker.addEventListener) {
      nav.serviceWorker.addEventListener("message", (e) => {  // sw.js: a reminder tapped
        if (e.data && e.data.type === "open" && typeof e.data.url === "string"
            && e.data.url.endsWith(URL_HASH)) open();
      });
    }
    let opened = false;

    return {
      enable(yes) {
        on = Boolean(yes);
        openBtn.hidden = !on;
        if (!on && !view.hidden && showView) showView("app");
        // the first /api/state after a tap that opened the app at /#reminders
        if (on && !opened) {
          opened = true;
          if (win.location && win.location.hash === URL_HASH) return open();
        }
        return null;
      },
      load, open,
      get data() { return data; },
    };
  }

  let screen = null;
  function onState(state, win = root, doc = root.document) {
    if (!doc) return null;
    if (!screen) screen = setup(win, doc);
    return screen ? screen.enable(Boolean(state && state.reminders)) : null;
  }

  root.KwilReminders = { configure, onState, setup, TEXT };
})(typeof window !== "undefined" ? window : globalThis);
