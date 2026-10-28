// On the Kwil page (heykwil.com, or a Kwil domain the person confirmed in
// the popup): after "Set up Canvas helper", Kwil shows a one-time pairing
// code in the element with data-kwil-pair. This script picks it up
// (nothing to type), asks the service worker whether this page may pair
// (it may not replace a pairing with another Kwil unless the popup
// confirmed the switch; then the worker says "kwil-pair-now"), trades the
// code for the helper's sync key with a same-origin POST to
// /api/student/canvas/pair, and hands the key to the worker, which stores
// it and pins this origin. The key stays in this content script's own
// isolated world: never in the page's DOM, a URL or a log. Chrome tells
// the worker which origin sent each message (sender.origin), so the page
// can't claim another.
(() => {
  "use strict";
  if (globalThis.__kwilPairLoaded) return;   // injected twice (popup + registered)
  globalThis.__kwilPairLoaded = true;
  if (location.protocol !== "https:" || /\.instructure\.com$/i.test(location.hostname)) return;

  const PAIR_PATH = "/api/student/canvas/pair";
  const CODE = /^kcp1\.[A-Za-z0-9_-]{32}$/;
  const KEY = /^kcs1\.[A-Za-z0-9_-]{43}$/;
  const tried = new Set();

  async function ask(message) {
    try { return (await chrome.runtime.sendMessage(message)) || {}; } catch (e) { return {}; }
  }

  async function pair(code) {
    if (!(await ask({ type: "kwil-may-pair" })).ok) return;   // the code stays unused
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
    await ask({ type: "kwil-paired", key: body.key, maxFileBytes: body.max_file_bytes,
                maxFiles: body.max_files });
  }

  async function look(again) {
    const el = document.querySelector("[data-kwil-pair]");
    const code = el ? String(el.getAttribute("data-kwil-pair") || "").trim() : "";
    if (!CODE.test(code) || (tried.has(code) && !again)) return;
    if (tried.size >= 20) tried.clear();
    tried.add(code);
    await pair(code);   // refused or not, only "kwil-pair-now" tries this code again
  }

  chrome.runtime.onMessage.addListener((msg, sender) => {
    if (sender && sender.id === chrome.runtime.id && msg && msg.type === "kwil-pair-now") {
      look(true);
    }
  });
  new MutationObserver(() => { look(false); }).observe(document.documentElement, {
    subtree: true, childList: true, attributes: true, attributeFilter: ["data-kwil-pair"] });
  look(false);
})();
