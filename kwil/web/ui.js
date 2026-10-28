// Small touch behaviours the phone page should have, like other apps:
// - the settings menu closes on a tap anywhere outside it (and on Escape);
// - swiping down while the keyboard is open closes the keyboard.
// And the owner's health banner (state.health from /api/state, written by
// the watchdog, kwil/watch.py): "Mom's app is down since 06:13".
"use strict";

(function (root) {
  function closeMenuOutside(doc) {
    const menu = doc.querySelector("details.menu");
    if (!menu) return null;
    const onDown = (e) => {
      if (menu.open && !menu.contains(e.target)) menu.open = false;
    };
    const onKey = (e) => {
      // with a Connections sheet open (connections.js), Escape closes only it
      const sheet = typeof menu.querySelector === "function" && menu.querySelector("dialog[open]");
      if (e.key === "Escape" && menu.open && !sheet) menu.open = false;
    };
    doc.addEventListener("pointerdown", onDown, true);
    doc.addEventListener("keydown", onKey);
    return { onDown, onKey };
  }

  const SWIPE_PX = 40;  // a deliberate downward swipe, not a tap or a scroll jitter

  function swipeDownHidesKeyboard(doc) {
    let startY = null;
    const typing = () => {
      const el = doc.activeElement;
      return !!el && (el.tagName === "INPUT" || el.tagName === "TEXTAREA");
    };
    const onStart = (e) => {
      startY = e.touches && e.touches.length === 1 ? e.touches[0].clientY : null;
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
