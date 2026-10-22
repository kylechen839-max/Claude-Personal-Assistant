// "School deadlines" in the menu (kwil/student_web.py): connect Canvas,
// sync, and "What I captured" with confirm, conflict and delete buttons.
// Self-contained: it builds its own card (in #student if the page has one,
// else just before the API key section), asks /api/student/items when the
// menu opens, and stays hidden when that is 404 ([student] off) or 401.
// Everything shown from the server (titles, courses, the school's host) is
// set as text, never HTML. The feed link and token are sent once and never
// shown back. It never asks for a school password. Loaded with defer.
"use strict";

(function (root) {
  const SOURCES = { ics: "Canvas calendar", canvas_api: "Canvas", modules: "Canvas pages",
                    gmail: "Canvas email", screenshot: "Screenshot", manual: "Added by you" };
  let setup = null;

  function create(win, doc) {
    const menu = doc.querySelector("details.menu");
    let card = doc.getElementById("student");
    if (!card) {
      if (!menu) return null;
      card = doc.createElement("section");
      card.id = "student";
      const before = doc.getElementById("api-key");
      if (before && before.parentNode) before.parentNode.insertBefore(card, before);
      else menu.appendChild(card);
    }
    card.className = "api-key student";
    card.hidden = true;

    function el(tag, text, cls) {
      const e = doc.createElement(tag);
      if (text !== undefined) e.textContent = text;
      if (cls) e.className = cls;
      return e;
    }
    function button(text, onClick, cls) {
      const b = el("button", text, cls);
      b.type = "button";
      b.addEventListener("click", onClick);
      return b;
    }
    function input(type, placeholder, label) {
      const i = el("input");
      i.type = type;
      i.placeholder = placeholder;
      i.autocomplete = "off";
      i.autocapitalize = "off";
      i.spellcheck = false;
      i.maxLength = 2000;
      i.setAttribute("aria-label", label);
      return i;
    }

    const title = el("p", "School deadlines", "api-key-title");
    const state = el("p", "", "hint");
    state.setAttribute("role", "status");
    const host = el("p", "", "hint");  // the school's host, as text
    host.hidden = true;
    const message = el("p", "", "hint");
    message.hidden = true;

    // connect: the calendar feed link, or school + access token
    const connectBox = el("div");
    const feed = input("url", "https://…/feeds/calendars/user_….ics", "Canvas calendar feed link");
    const feedHelp = el("p", "In Canvas, open Calendar, tap Calendar Feed and copy the " +
                        "link. I never ask for your school password.", "hint");
    const connectFeed = button("Connect Canvas", () => connect({ ics_url: feed.value.trim() }));
    const useToken = button("Use an access token instead", () => {
      tokenBox.hidden = !tokenBox.hidden;
    });
    const tokenBox = el("div");
    tokenBox.hidden = true;
    const school = input("text", "Your school's Canvas, e.g. canvas.school.edu", "School");
    const token = input("password", "Access token", "Canvas access token");
    const tokenHelp = el("p", "In Canvas: Account, Settings, New access token.", "hint");
    const connectToken = button("Connect with token", () =>
      connect({ base_url: school.value.trim(), token: token.value.trim() }));
    tokenBox.append(school, token, tokenHelp, connectToken);
    const connectRow = el("div", undefined, "api-key-row");
    connectRow.append(connectFeed, useToken);
    connectBox.append(feed, feedHelp, connectRow, tokenBox);

    const actions = el("div", undefined, "api-key-row");
    const syncBtn = button("Sync now", sync);
    const unlink = button("Disconnect Canvas", disconnect, "danger");
    actions.append(syncBtn, unlink);

    const listTitle = el("p", "What I captured", "api-key-title");
    const list = el("ul", undefined, "student-items");
    const empty = el("p", "Nothing captured yet.", "hint");

    card.append(title, state, host, connectBox, actions, message, listTitle, list, empty);

    // confirm() text isn't in the page, so i18n.js's observer can't see it
    function translated(text) {
      const i18n = win.KwilI18n;
      return i18n && i18n.isActive(doc) ? i18n.translate(text) : text;
    }

    function say(text) {
      message.hidden = !text;
      message.textContent = text || "";
    }

    async function call(url, opts = {}) {
      const res = await win.fetch(url, {
        credentials: "same-origin", cache: "no-store",
        ...(opts.body !== undefined ? {
          method: "POST", headers: { "Content-Type": "application/json" },
          body: JSON.stringify(opts.body) } : {}),
      });
      let body = null;
      try { body = await res.json(); } catch (e) { body = null; }
      return { ok: res.ok, status: res.status, body: body || {} };
    }

    function when(due) {
      if (typeof due !== "string") return "";
      if (/^\d{4}-\d{2}-\d{2}$/.test(due)) {
        const [y, m, d] = due.split("-").map(Number);
        return new Date(y, m - 1, d).toLocaleDateString(undefined,
          { weekday: "short", month: "short", day: "numeric" });
      }
      const t = new Date(due);
      return isNaN(t) ? due : t.toLocaleString(undefined, { weekday: "short", month: "short",
                                                            day: "numeric", hour: "numeric",
                                                            minute: "2-digit" });
    }

    function row(item) {
      const li = el("li", undefined, "student-item status-" + String(item.status));
      const head = [item.title, item.course].filter((p) => typeof p === "string" && p)
        .map((p) => p.slice(0, 200)).join(" · ");
      li.append(el("span", head, "student-title"));
      // each piece its own element, so i18n.js can translate the fixed ones
      const meta = el("p", undefined, "hint");
      meta.append(el("span", when(item.due)), el("span", " · "),
                  el("span", SOURCES[item.source] || ""));
      const tag = item.status === "conflict" ? "Dates disagree"
        : item.status === "draft" ? "Not checked" : "";
      if (tag) meta.append(el("span", " · "), el("span", tag));
      li.append(meta);
      const buttons = el("div", undefined, "api-key-row");
      if (item.status === "draft" || item.status === "conflict") {
        buttons.append(button(item.status === "conflict" ? "This date is right" : "Looks right",
                              () => resolve(item, "confirm")));
      }
      if (item.status === "conflict") {
        buttons.append(button("Not this one", () => resolve(item, "dismiss")));
      }
      buttons.append(button("Delete", () => remove(item), "danger"));
      li.append(buttons);
      return li;
    }

    function render(body) {
      const st = body.canvas || {};
      const owner = st.managed === "env";
      host.hidden = !(st.connected && st.school);
      host.textContent = st.school ? String(st.school).slice(0, 100) : "";
      if (st.connected) {
        state.textContent = st.gmail ? "Canvas connected, and Canvas emails in Gmail."
          : "Canvas connected.";
      } else if (owner) {
        state.textContent = "Put CANVAS_ICS_URL (or CANVAS_TOKEN) in your server's .env " +
          "to connect Canvas.";
      } else {
        state.textContent = st.can_store === false
          ? "This server can't store sign-ins yet."
          : "Connect Canvas and I'll keep track of your deadlines.";
      }
      connectBox.hidden = owner || Boolean(st.connected) || st.can_store === false;
      unlink.hidden = owner || !st.connected;
      syncBtn.hidden = !st.connected && !st.gmail;
      const items = Array.isArray(body.items) ? body.items : [];
      list.replaceChildren(...items.map(row));
      empty.hidden = items.length > 0;
    }

    async function refresh() {
      try {
        const r = await call("/api/student/items");
        if (!r.ok) { card.hidden = true; return; }
        card.hidden = false;
        render(r.body);
      } catch (e) { /* offline: leave it as it is */ }
    }

    async function connect(body) {
      say("Connecting…");
      try {
        const r = await call("/api/student/canvas", { body });
        if (!r.ok) { say(r.body.error || "Couldn't connect Canvas."); return; }
        feed.value = ""; token.value = "";  // never kept on the page
        say("Connected. Syncing…");
        await sync();
      } catch (e) { say("Couldn't connect Canvas. Try again."); }
    }

    async function sync() {
      syncBtn.disabled = true;
      try {
        const r = await call("/api/student/sync", { body: {} });
        if (!r.ok) {
          say(r.body.error || "Couldn't sync. Try again.");
        } else {
          const added = Object.values(r.body.sources || {})
            .reduce((n, c) => n + (Number(c && c.added) || 0), 0);
          const failed = Object.keys(r.body.errors || {}).length;
          say(failed ? "Synced, but some sources didn't answer. Try reconnecting."
            : added ? "Found " + added + " new." : "Nothing new.");
        }
      } catch (e) { say("Couldn't sync. Try again."); }
      syncBtn.disabled = false;
      await refresh();
    }

    async function disconnect() {
      const ask = "Disconnect Canvas? I'll forget the link.";
      if (typeof win.confirm === "function" && !win.confirm(translated(ask))) return;
      const also = typeof win.confirm === "function" &&
        win.confirm(translated("Also delete what I captured from Canvas?"));
      const r = await call("/api/student/disconnect", { body: { delete_items: also } });
      say(r.ok ? "Canvas disconnected." : "Couldn't disconnect. Try again.");
      await refresh();
    }

    async function resolve(item, choice) {
      const r = await call("/api/student/items/" + Number(item.id) + "/resolve",
                           { body: { choice } });
      if (!r.ok) say("Couldn't save that. Try again.");
      await refresh();
    }

    async function remove(item) {
      const ask = "Delete this deadline?";
      if (typeof win.confirm === "function" && !win.confirm(translated(ask))) return;
      const r = await call("/api/student/items/" + Number(item.id) + "/delete", { body: {} });
      if (!r.ok) say("Couldn't delete it. Try again.");
      await refresh();
    }

    if (menu) menu.addEventListener("toggle", () => { if (menu.open) refresh(); });
    return { refresh, render };
  }

  function start(win = root, doc = root.document) {
    if (!doc) return null;
    if (!setup) setup = create(win, doc);
    if (setup) setup.refresh();
    return setup;
  }

  root.KwilStudent = { start, create };
  if (root.document && root.document.readyState !== "loading") start();
  else if (root.document) root.document.addEventListener("DOMContentLoaded", () => start());
})(typeof window !== "undefined" ? window : globalThis);
