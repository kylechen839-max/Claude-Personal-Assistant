// On the Kwil page (heykwil.com, or a Kwil domain enabled from the popup):
// after "Set up Canvas helper", Kwil shows a one-time pairing code in the
// element with data-kwil-pair. This script picks it up (nothing to type),
// trades it for the helper's sync key with a same-origin POST to
// /api/student/canvas/pair, and hands the key to the service worker, which
// stores it and pins this origin. The key stays in this content script's
// own isolated world: never in the page's DOM, a URL or a log. Chrome
// tells the worker which origin sent it (sender.origin), so the page can't
// claim another.
(() => {
  "use strict";
  if (globalThis.__kwilPairLoaded) return;   // injected twice (popup + registered)
  globalThis.__kwilPairLoaded = true;
  if (location.protocol !== "https:" || /\.instructure\.com$/i.test(location.hostname)) return;

  const PAIR_PATH = "/api/student/canvas/pair";
  const CODE = /^kcp1\.[A-Za-z0-9_-]{32}$/;
  const KEY = /^kcs1\.[A-Za-z0-9_-]{43}$/;
  const tried = new Set();

  async function pair(code) {
    let r;
    try {
      r = await fetch(location.origin + PAIR_PATH, {
        method: "POST", credentials: "omit", redirect: "error", cache: "no-store",
        headers: { "Content-Type": "application/json" }, body: JSON.stringify({ code }) });
    } catch (e) { return; }
    if (r.status !== 200) return;   // used, expired or wrong: Kwil shows "Set up" again
    let body = {};
    try { body = await r.json(); } catch (e) { return; }
    if (!body || typeof body.key !== "string" || !KEY.test(body.key)) return;
    try {
      await chrome.runtime.sendMessage({ type: "kwil-paired", key: body.key,
                                         maxFileBytes: body.max_file_bytes,
                                         maxFiles: body.max_files });
    } catch (e) { /* the worker restarts on the next event; pair again from Kwil */ }
  }

  function look() {
    const el = document.querySelector("[data-kwil-pair]");
    const code = el ? String(el.getAttribute("data-kwil-pair") || "").trim() : "";
    if (!CODE.test(code) || tried.has(code)) return;
    if (tried.size >= 20) tried.clear();
    tried.add(code);
    pair(code);
  }

  new MutationObserver(look).observe(document.documentElement, {
    subtree: true, childList: true, attributes: true, attributeFilter: ["data-kwil-pair"] });
  look();
})();
