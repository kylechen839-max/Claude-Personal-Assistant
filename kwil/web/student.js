// "School deadlines" in the menu (kwil/student_web.py): connect Canvas,
// sync, and "What I captured" with confirm, conflict and delete buttons.
// Self-contained: it builds its own card (in #student if the page has one,
// else just before the API key section), asks /api/student/items when the
// menu opens, and stays hidden when that is 404 ([student] off) or 401.
// Everything shown from the server (titles, courses, the school's host) is
// set as text, never HTML. The feed link and token are sent once and never
// shown back. It never asks for a school password. Loaded with defer.
// The guided way in: find the school (/api/student/schools, each with its
// token policy; a typed address asks /api/student/canvas/policy), then
// Kwil picks the one way in and shows only that step (pickMethod): an
// access token where the school allows them (Open my Canvas settings),
// the Canvas helper on desktop Chrome or Edge where it blocks them or
// nobody knows yet, else the calendar feed: open its Canvas calendar in a
// new tab from the tap (so iOS allows it), and Paste the copied feed link
// back into the field. The person still taps Connect. "My Canvas has no
// New access token button" tells the server (it learns the school blocks
// them) and falls back at once.
// The Canvas helper (kwil/canvas_helper.py), for schools that block tokens:
// "Set up Canvas helper" shows a one-time pairing code for 10 minutes in
// the element with data-kwil-pair (the helper extension's content script
// reads it there, nothing to type; never in a URL), then removes it once
// the helper paired or the code ran out. The helper's key never reaches
// the page.
"use strict";

(function (root) {
  const SOURCES = { ics: "Canvas calendar", canvas_api: "Canvas", modules: "Canvas pages",
                    course_text: "Course files", gmail: "Canvas email",
                    screenshot: "Screenshot", manual: "Added by you" };
  let setup = null;

  // A school's Canvas host from a lookup answer or what was typed, checked
  // as far as a page can (canvas.public_url's rules without the DNS
  // lookup, which the server does on the pasted link): https on the normal
  // port, no user:password@, a dotted DNS name (never an IP, localhost or a
  // LAN-only name). null if not.
  const LOCAL_TLDS = /\.(local|localhost|internal|lan|home|arpa|test|invalid|example)$/;
  function schoolHost(value) {
    let text = String(value || "").trim();
    if (!text || text.length > 300) return null;
    if (!/^[a-z][a-z0-9+.-]*:\/\//i.test(text)) text = "https://" + text;
    let url;
    try { url = new URL(text); } catch (e) { return null; }
    if (url.protocol !== "https:" || url.username || url.password ||
        (url.port && url.port !== "443")) return null;
    const host = url.hostname.toLowerCase().replace(/\.$/, "");
    if (!/^(?=.{4,253}$)([a-z0-9]([a-z0-9-]{0,61}[a-z0-9])?\.)+[a-z]{2,63}$/.test(host) ||
        LOCAL_TLDS.test(host)) return null;
    return host;
  }

  // A phone or tablet: userAgentData.mobile where the browser has it
  // (Chromium), else the user agent; iPadOS says it is a Mac, so a Mac
  // with a touch screen counts too.
  function isMobile(nav) {
    nav = nav || {};
    const data = nav.userAgentData;
    if (data && typeof data.mobile === "boolean") return data.mobile;
    const ua = String(nav.userAgent || "");
    if (/Android|iPhone|iPad|iPod|Mobile/i.test(ua)) return true;
    return /Macintosh/.test(ua) && Number(nav.maxTouchPoints) > 1;
  }

  // Desktop Chrome or Edge (or another Chromium), where the Canvas helper
  // extension can be loaded.
  function helperBrowser(nav) {
    nav = nav || {};
    if (isMobile(nav)) return false;
    if (nav.userAgentData && Array.isArray(nav.userAgentData.brands)) return true;
    return /\b(Chrome|Chromium|Edg)\//.test(String(nav.userAgent || ""));
  }

  // The one way in for a school: {method: "token" | "helper" | "feed",
  // extra: "token" (offer "Get course files too") | "helper_elsewhere"
  // (point at the helper on a computer) | null}. tokens: the school's
  // policy ("allowed", "blocked", "unknown"); tokenOk: this person may use
  // a token ([canvas] tokens_for); helper: the server has the helper on;
  // chromium: helperBrowser().
  function pickMethod({ tokens, tokenOk, helper, chromium }) {
    if (tokens === "allowed" && tokenOk) return { method: "token", extra: null };
    if (helper && chromium) return { method: "helper", extra: null };
    if (tokens !== "blocked" && tokens !== "allowed" && tokenOk) {
      return { method: "feed", extra: "token" };
    }
    return { method: "feed", extra: helper ? "helper_elsewhere" : null };
  }

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

    // connect: find the school, then only the step Kwil picked for it
    const connectBox = el("div");
    const find = input("text", "Your school's name or Canvas address", "Find your school");
    const findBtn = button("Find my school", findSchool);
    const findRow = el("div", undefined, "api-key-row");
    findRow.append(findBtn);
    const results = el("div", undefined, "api-key-row");  // a button per school, names as text
    const chosenHost = el("p", "", "hint");                // the chosen school's host, as text
    let chosen = null;
    let tokens = "unknown";   // the chosen school's token policy
    let picked = null;        // pickMethod()'s answer for it
    let filesToo = false;     // "Get course files too" opened the token step under the feed
    let feedChosen = false;   // "Use the calendar feed instead" was tapped
    let current = {};         // the last status from the server
    // the feed: open its Canvas calendar, paste the link back
    const feedBox = el("div");
    feedBox.hidden = true;
    const openCal = button("Open my Canvas calendar", openCalendar);
    const openHelp = el("p", "Tap Calendar Feed (bottom right), copy the link, come back.",
                        "hint");
    const feed = input("url", "https://…/feeds/calendars/user_….ics", "Canvas calendar feed link");
    const clip = win.navigator && win.navigator.clipboard;
    const paste = button("Paste", pasteFeed);
    paste.hidden = !(clip && typeof clip.readText === "function");
    const feedHelp = el("p", "In Canvas, open Calendar, tap Calendar Feed and copy the " +
                        "link. I never ask for your school password.", "hint");
    const connectFeed = button("Connect Canvas", () => connect({ ics_url: feed.value.trim() }));
    const connectRow = el("div", undefined, "api-key-row");
    connectRow.append(paste, connectFeed);
    const helperElsewhere = el("p", "For course files too, set up the Kwil Canvas helper in " +
                               "Chrome or Edge on a computer.", "hint");
    const moreBtn = button("Get course files too", () => { filesToo = true; showStep(); });
    feedBox.append(openCal, openHelp, feed, feedHelp, connectRow, helperElsewhere, moreBtn);
    // the access token: open their Canvas settings, make one, paste it back
    const tokenBox = el("div");
    tokenBox.hidden = true;
    const openSettings = button("Open my Canvas settings", openTokenPage);
    const tokenHelp = el("p", "Tap New access token, pick an expiry date (a student's " +
                         "token lasts at most 120 days), copy the token, come back.", "hint");
    const token = input("password", "Access token", "Canvas access token");
    const connectToken = button("Connect with token", () =>
      connect({ base_url: chosen || "", token: token.value.trim() }));
    const noToken = button("My Canvas has no New access token button", reportBlocked);
    tokenBox.append(openSettings, tokenHelp, token, connectToken, noToken);
    const useFeed = button("Use the calendar feed instead", () => {
      feedChosen = true;  // kept through refreshes until another school is picked
      filesToo = false;
      picked = pick();
      showStep();
    });
    useFeed.hidden = true;
    connectBox.append(find, findRow, results, chosenHost, feedBox, tokenBox, useFeed);

    const actions = el("div", undefined, "api-key-row");
    const syncBtn = button("Sync now", sync);
    const unlink = button("Disconnect Canvas", disconnect, "danger");
    actions.append(syncBtn, unlink);

    // the Canvas helper: pair it with a one-time code, or revoke it
    const helperBox = el("div");
    helperBox.hidden = true;
    const helperTitle = el("p", "Canvas helper", "api-key-title");
    const helperState = el("p", "", "hint");
    const helperHelp = el("p", "For schools that block access tokens: the Kwil Canvas helper " +
                          "reads your own courses with your own sign-in, like opening the " +
                          "pages yourself. Some schools' rules limit automated access; check " +
                          "yours. It is read-only and rate-limited.", "hint");
    const pairCode = el("code", "", "pair-code");  // the code, as text, for the helper to read
    pairCode.hidden = true;
    const pairHelp = el("p", "Open the Kwil Canvas helper in this browser: it picks this code " +
                        "up by itself. It works once, for 10 minutes.", "hint");
    pairHelp.hidden = true;
    const setupHelper = button("Set up Canvas helper", startPairing);
    const revokeHelper = button("Revoke helper", revoke, "danger");
    const helperRow = el("div", undefined, "api-key-row");
    helperRow.append(setupHelper, revokeHelper);
    helperBox.append(helperTitle, helperState, helperHelp, pairCode, pairHelp, helperRow);
    let pairTimer = null;
    let pairUntil = 0;

    const listTitle = el("p", "What I captured", "api-key-title");
    const list = el("ul", undefined, "student-items");
    const empty = el("p", "Nothing captured yet.", "hint");

    card.append(title, state, host, connectBox, actions, helperBox, message, listTitle, list,
                empty);

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
      const helper = st.helper || {};
      // connected, or the helper paired: done, no connect step
      connectBox.hidden = owner || Boolean(st.connected) || Boolean(helper.paired) ||
        st.can_store === false;
      // env-managed: Disconnect only forgets an older link the app stored
      // (or the helper's key)
      unlink.hidden = owner ? !(st.app_link || helper.paired) : !(st.connected || helper.paired);
      syncBtn.hidden = !st.connected && !st.gmail;
      current = st;
      if (chosen) picked = pick();  // the helper or tokens_offered may have changed
      showStep();
      const items = Array.isArray(body.items) ? body.items : [];
      list.replaceChildren(...items.map(row));
      empty.hidden = items.length > 0;
    }

    function clearCode() {
      pairCode.textContent = "";
      pairCode.removeAttribute("data-kwil-pair");
      pairCode.hidden = true;
      pairHelp.hidden = true;
      if (pairTimer !== null) { win.clearInterval(pairTimer); pairTimer = null; }
    }

    function pick() {
      const helper = current.helper || {};
      if (feedChosen) return { method: "feed", extra: null };
      return pickMethod({ tokens, tokenOk: current.tokens_offered !== false,
                          helper: Boolean(helper.enabled),
                          chromium: helperBrowser(win.navigator) });
    }

    // Only the picked step: the feed (with its one extra), the token, or
    // the helper; "Use the calendar feed instead" under the other two.
    function showStep() {
      const method = chosen && picked ? picked.method : null;
      const extra = method === "feed" ? picked.extra : null;
      feedBox.hidden = method !== "feed";
      helperElsewhere.hidden = extra !== "helper_elsewhere";
      moreBtn.hidden = extra !== "token" || filesToo;
      tokenBox.hidden = !(method === "token" || (extra === "token" && filesToo));
      useFeed.hidden = !method || method === "feed";
      renderHelper(current.helper || {});
    }

    function renderHelper(helper) {
      // shown once it is the way in, set up, or next to a connected Canvas
      // (the helper adds course files to a feed)
      if (helper.paired || Date.now() > pairUntil) clearCode();
      helperBox.hidden = !helper.enabled || !(helper.paired || current.connected ||
        (chosen && picked && picked.method === "helper") || !pairCode.hidden);
      helperState.textContent = helper.paired ? "Canvas helper connected." : "";
      helperState.hidden = !helper.paired;
      setupHelper.hidden = Boolean(helper.paired) || !pairCode.hidden;
      revokeHelper.hidden = !helper.paired;
    }

    async function startPairing() {
      setupHelper.disabled = true;
      try {
        const r = await call("/api/student/canvas/helper", { body: {} });
        const code = r.ok && typeof r.body.code === "string" ? r.body.code : "";
        if (!/^kcp1\.[A-Za-z0-9_-]{32}$/.test(code)) {
          say(r.body.error || "Couldn't start pairing. Try again.");
          return;
        }
        say("");
        pairUntil = Date.now() + 1000 * Math.min(Number(r.body.expires_in) || 600, 600);
        pairCode.textContent = code;
        pairCode.setAttribute("data-kwil-pair", code);
        pairCode.hidden = false;
        pairHelp.hidden = false;
        setupHelper.hidden = true;
        // until the helper paired (refresh() then clears the code) or it ran out
        if (pairTimer === null && typeof win.setInterval === "function") {
          pairTimer = win.setInterval(refresh, 4000);
        }
      } catch (e) {
        say("Couldn't start pairing. Try again.");
      } finally {
        setupHelper.disabled = false;
      }
    }

    async function revoke() {
      const ask = "Revoke the Canvas helper? It stops sending your courses; what it " +
        "sent stays until you disconnect.";
      if (typeof win.confirm === "function" && !win.confirm(translated(ask))) return;
      const r = await call("/api/student/canvas/helper/revoke", { body: {} });
      say(r.ok ? "Canvas helper revoked." : "Couldn't revoke the helper. Try again.");
      await refresh();
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
        if (!r.ok) {
          say(r.body.error || "Couldn't connect Canvas.");
          if (r.body.code === "tokens_owner_only") {  // the server says no tokens for them
            token.value = "";
            current = { ...current, tokens_offered: false };
            picked = pick();
            filesToo = false;
            showStep();
          }
          return;
        }
        feed.value = ""; token.value = "";  // never kept on the page
        say("Connected. Syncing…");
        await sync();
      } catch (e) { say("Couldn't connect Canvas. Try again."); }
    }

    async function findSchool() {
      let q = find.value.trim().slice(0, 100);
      if (q.length < 3) { say("Type at least 3 letters of your school's name."); return; }
      // a Canvas address typed or pasted ("https://canvas.school.edu/",
      // "canvas.school.edu/"): use it as it is; a bare domain ("school.edu")
      // still goes to the lookup, which knows the school's Canvas host (or
      // guesses canvas.<domain>)
      const typed = /\s/.test(q) ? null : schoolHost(q);
      if (typed && (/:\/\//.test(q) || /(^|\.)(canvas|instructure)\./.test(typed))) {
        results.replaceChildren();
        say("");
        await choose(typed);
        return;
      }
      if (typed) q = typed;
      findBtn.disabled = true;
      try {
        const r = await call("/api/student/schools?q=" + encodeURIComponent(q));
        if (!r.ok) { say(r.body.error || "Couldn't look up schools. Try again."); return; }
        const schools = (Array.isArray(r.body.schools) ? r.body.schools : [])
          .map((s) => ({ name: String((s && s.name) || "").slice(0, 100),
                         host: schoolHost(s && s.base_url), tokens: s && s.tokens }))
          .filter((s) => s.host).slice(0, 10);
        results.replaceChildren(...schools.map((s) =>
          button(s.name || s.host, () => choose(s.host, s.tokens))));
        say(schools.length ? "" : "I couldn't find that school. Type its Canvas address, " +
            "e.g. canvas.school.edu.");
        if (schools.length === 1) await choose(schools[0].host, schools[0].tokens);
      } catch (e) {
        say("Couldn't look up schools. Try again.");
      } finally {
        findBtn.disabled = false;
      }
    }

    // A school picked: its token policy (from the lookup's answer, else
    // asked; unknown if that fails), then the one step for it.
    async function choose(host, known) {
      const asked = schoolHost(host);
      chosen = asked;
      chosenHost.textContent = asked || "";
      filesToo = false;
      feedChosen = false;
      tokens = "unknown";
      picked = null;
      showStep();
      if (!asked) return;
      let found = "unknown";
      if (["allowed", "blocked", "unknown"].includes(known)) {
        found = known;
      } else {
        try {
          const r = await call("/api/student/canvas/policy?host=" + encodeURIComponent(asked));
          if (r.ok && ["allowed", "blocked"].includes(r.body.tokens)) found = r.body.tokens;
        } catch (e) { /* offline: unknown */ }
        if (chosen !== asked) return;  // another school was picked meanwhile
      }
      tokens = found;
      picked = pick();
      showStep();
    }

    // straight from the tap (no await before it), or iOS blocks the new tab
    function openCalendar() {
      if (!chosen || typeof win.open !== "function") return;
      win.open("https://" + chosen + "/calendar", "_blank", "noopener");
    }

    // the page with Canvas's New access token button, from the tap too
    function openTokenPage() {
      if (!chosen || typeof win.open !== "function") return;
      win.open("https://" + chosen + "/profile/settings", "_blank", "noopener");
    }

    // "My Canvas has no New access token button": another way in at once,
    // and the server learns the school blocks them
    async function reportBlocked() {
      const host = chosen;
      if (!host) return;
      tokens = "blocked";
      token.value = "";
      filesToo = false;
      picked = pick();
      showStep();
      say("Thanks. Here is another way in for your school.");
      try {
        await call("/api/student/canvas/report", { body: { host, tokens: "blocked" } });
      } catch (e) { /* offline: the fallback is shown anyway */ }
    }

    // only fills the field: the person still checks it and taps Connect
    async function pasteFeed() {
      let text;
      try { text = await clip.readText(); } catch (e) { return; }  // denied: paste by hand
      text = typeof text === "string" ? text.trim() : "";
      if (!/^(https|webcal):\/\//i.test(text) || text.length > 2000) {
        say("Copy the Calendar Feed link in Canvas first.");
        return;
      }
      feed.value = text;
      say("");
    }

    async function sync() {
      syncBtn.disabled = true;
      try {
        const r = await call("/api/student/sync", { body: {} });
        if (!r.ok) {
          say(r.body.error || "Couldn't sync. Try again.");
        } else if (r.body.background) {  // the server's own sync of you is running
          say("Syncing in the background.");
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
      clearCode();
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

  root.KwilStudent = { start, create, pickMethod, isMobile, helperBrowser };
  if (root.document && root.document.readyState !== "loading") start();
  else if (root.document) root.document.addEventListener("DOMContentLoaded", () => start());
})(typeof window !== "undefined" ? window : globalThis);
