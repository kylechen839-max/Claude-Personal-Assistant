// Small touch behaviours the phone page should have, like other apps:
// - the settings menu closes on a tap anywhere outside it (and on Escape);
// - it is a full-screen Settings page: opening it moves focus to its title,
//   makes the chat behind it inert (#app's parts outside the header, so
//   VoiceOver and Tab stay on the page; the tab bar is outside #app), and Done (like Escape) closes it and gives focus back to the gear (on a
//   phone, to the tab bar's Settings: the header's gear is hidden there);
// - swiping down while the keyboard is open closes the keyboard (not when
//   the swipe scrolls a message long enough to scroll inside its field).
// And the owner's health banner (state.health from /api/state, written by
// the watchdog, kwil/watch.py): "Mom's app is down since 06:13".
"use strict";

(function (root) {
  function closeMenuOutside(doc) {
    const menu = doc.querySelector("details.menu");
    if (!menu) return null;
    const find = (sel) => (typeof menu.querySelector === "function" ? menu.querySelector(sel) : null);
    const byId = (id) => (typeof doc.getElementById === "function" ? doc.getElementById(id) : null);
    const focus = (el) => { if (el && typeof el.focus === "function") el.focus(); };
    // the phone's tab bar (app.css shows it below 48rem): its Settings tab
    // stands in for the gear there
    const phone = () => typeof root.matchMedia === "function"
      && root.matchMedia("(max-width: 47.99rem)").matches;
    const tabbar = () => byId("tabbar");
    // laid out (html.kb-open hides the bar with CSS, not [hidden])
    const rendered = (el) => typeof el.getClientRects !== "function" || el.getClientRects().length > 0;
    // The keyboard is up, so the bar isn't drawn and can't take focus: put
    // the keyboard away and focus the tab once the bar is back (the
    // viewport's next resize, or a frame after kb-open clears; about 2 s at
    // most). With neither available, the gear as before.
    const focusWhenShown = (tab) => {
      const active = doc.activeElement;
      if (active && typeof active.blur === "function") active.blur();
      const vv = root.visualViewport;
      const raf = typeof root.requestAnimationFrame === "function" ? root.requestAnimationFrame.bind(root) : null;
      const canResize = vv && typeof vv.addEventListener === "function";
      if (!raf && !canResize) { focus(find("summary")); return; }
      let done = false, frames = 0;
      const finish = () => {
        done = true;
        if (canResize && typeof vv.removeEventListener === "function") vv.removeEventListener("resize", onResize);
      };
      const tryNow = () => {
        if (done) return true;
        if (menu.open) { finish(); return true; }  // opened again meanwhile: its title has focus
        if (rendered(tab)) { finish(); focus(tab); return true; }
        return false;
      };
      const tick = () => { if (!tryNow() && ++frames < 120) raf(tick); else if (!done) finish(); };
      const onResize = () => {
        if (tryNow()) return;
        if (raf) raf(tick); else if (++frames >= 5) finish();  // a few resizes at most
      };
      if (canResize) vv.addEventListener("resize", onResize);
      if (raf) raf(tick);
    };
    // the chat behind the open page: out of reach for VoiceOver and Tab
    const behind = (on) => {
      if (typeof doc.querySelectorAll !== "function") return;
      for (const el of doc.querySelectorAll("#app > :not(header)")) el.inert = on;
    };
    // close the page and hand focus back to the gear (VoiceOver lands there),
    // on a phone to the tab bar's Settings
    const close = () => {
      menu.open = false;
      behind(false);
      const tab = byId("tab-settings"), bar = tabbar();
      if (!(tab && !tab.hidden && bar && !bar.hidden && phone())) focus(find("summary"));
      else if (rendered(tab)) focus(tab);
      else focusWhenShown(tab);
    };
    // a tap in the tab bar is its own (app.js): Settings stays open for it
    const inBar = (target) => {
      const bar = tabbar();
      return !!bar && typeof bar.contains === "function" && bar.contains(target);
    };
    const onDown = (e) => {
      if (menu.open && !menu.contains(e.target) && !inBar(e.target)) menu.open = false;
    };
    const onKey = (e) => {
      // with a Connections sheet open (connections.js), Escape closes only it
      const sheet = find("dialog[open]");
      if (e.key === "Escape" && menu.open && !sheet) close();
    };
    const onToggle = () => {
      behind(!!menu.open);
      if (menu.open) focus(byId("settings-title"));
    };
    const done = byId("settings-done");
    if (done && typeof done.addEventListener === "function") done.addEventListener("click", close);
    if (typeof menu.addEventListener === "function") menu.addEventListener("toggle", onToggle);
    doc.addEventListener("pointerdown", onDown, true);
    doc.addEventListener("keydown", onKey);
    return { onDown, onKey, onToggle, close };
  }

  const SWIPE_PX = 40;  // a deliberate downward swipe, not a tap or a scroll jitter

  function swipeDownHidesKeyboard(doc) {
    let startY = null;
    const typing = () => {
      const el = doc.activeElement;
      return !!el && (el.tagName === "INPUT" || el.tagName === "TEXTAREA");
    };
    // the composer's field once its text is taller than it (about 6 lines)
    const scrollsText = (target) => {
      const field = typeof doc.getElementById === "function" ? doc.getElementById("text") : null;
      if (!field || !target) return false;
      const inside = target === field || (typeof field.contains === "function" && field.contains(target));
      return inside && field.scrollHeight > field.clientHeight;
    };
    const onStart = (e) => {
      startY = e.touches && e.touches.length === 1 && !scrollsText(e.target) ? e.touches[0].clientY : null;
    };
    const onMove = (e) => {
      if (startY === null || !e.touches || !e.touches.length || !typing()) return;
      if (e.touches[0].clientY - startY > SWIPE_PX) {
        doc.activeElement.blur();
        startY = null;
      }
    };
    doc.addEventListener("touchstart", onStart, { passive: true });
    doc.addEventListener("touchmove", onMove, { passive: true });
    return { onStart, onMove };
  }

  function clock(iso) {
    const d = new Date(iso);
    if (isNaN(d.getTime())) return "";
    return d.toLocaleTimeString([], { hour: "2-digit", minute: "2-digit" });
  }

  // The banner's lines for state.health (null: nothing to show). Only
  // names, times and fixed reasons come from the server; never chat text.
  function healthLines(health, fmt) {
    fmt = fmt || clock;
    if (!health) return [];
    const lines = [];
    for (const i of health.issues || []) {
      const down = /^healthz\b/.test(i.what || "");
      const since = fmt(i.since);
      lines.push(`${i.label} ${down ? "is down" : "isn't working right"}`
        + (since ? ` since ${since}` : "") + (i.what ? ` (${i.what})` : ""));
    }
    if (health.stale) {
      const at = fmt(health.checked);
      lines.push("Health checks haven't run" + (at ? ` since ${at}` : " yet") + ".");
    }
    for (const e of health.events || []) {
      const at = fmt(e.at);
      lines.push((at ? `${at}: ` : "") + e.text);
    }
    return lines;
  }

  function renderHealth(doc, health, fmt) {
    const el = doc.getElementById("health");
    if (!el) return [];
    const lines = healthLines(health, fmt);
    el.textContent = lines.join("\n");
    el.hidden = !lines.length;
    return lines;
  }

  const api = { closeMenuOutside, swipeDownHidesKeyboard, SWIPE_PX, healthLines,
    renderHealth };
  root.KwilUI = api;
  if (typeof root.document !== "undefined" && root.document.addEventListener) {
    api.menu = closeMenuOutside(root.document);
    api.swipe = swipeDownHidesKeyboard(root.document);
  }
})(typeof window !== "undefined" ? window : globalThis);
