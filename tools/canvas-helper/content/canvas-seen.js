// On a Canvas page (*.instructure.com, or a school domain enabled from the
// popup): tell the service worker the person is on their Canvas, so it can
// find the school's Canvas and sync soon after a fresh sign-in. It sends
// nothing from the page: the worker takes the origin from Chrome
// (sender.origin) and checks it is a signed-in Canvas itself.
(() => {
  "use strict";
  if (globalThis.__kwilCanvasSeen) return;
  globalThis.__kwilCanvasSeen = true;
  if (location.protocol !== "https:" || window.top !== window) return;
  try {
    const sent = chrome.runtime.sendMessage({ type: "canvas-seen" });
    if (sent && sent.catch) sent.catch(() => {});
  } catch (e) { /* the extension was reloaded: the next page load tells it */ }
})();
