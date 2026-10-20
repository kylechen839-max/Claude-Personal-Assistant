// Small touch behaviours the phone page should have, like other apps:
// - the settings menu closes on a tap anywhere outside it (and on Escape);
// - swiping down while the keyboard is open closes the keyboard.
"use strict";

(function (root) {
  function closeMenuOutside(doc) {
    const menu = doc.querySelector("details.menu");
    if (!menu) return null;
    const onDown = (e) => {
      if (menu.open && !menu.contains(e.target)) menu.open = false;
    };
    const onKey = (e) => {
      if (e.key === "Escape" && menu.open) menu.open = false;
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

  const api = { closeMenuOutside, swipeDownHidesKeyboard, SWIPE_PX };
  root.HeyClaudeUI = api;
  if (typeof root.document !== "undefined" && root.document.addEventListener) {
    api.menu = closeMenuOutside(root.document);
    api.swipe = swipeDownHidesKeyboard(root.document);
  }
})(typeof window !== "undefined" ? window : globalThis);
