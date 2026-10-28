// The Kwil Canvas helper's service worker: wires Chrome's events to
// lib/helper.js (all the logic, tested in node with fakes). Listeners are
// added at the top level so Chrome wakes the worker for them.

import { createHelper } from "./lib/helper.js";

const helper = createHelper({
  chrome,
  fetch: (...args) => fetch(...args),
  sleep: (ms) => new Promise((resolve) => setTimeout(resolve, ms)),
  log: (line) => console.info(line),   // counts and codes only (lib/sync.js)
});

// The key lives in chrome.storage.local, which page scripts can't reach.
// Chrome lets this extension's own content scripts read storage.local
// (only storage.session can be limited to trusted contexts, and it is
// emptied when the browser closes); ours never read it. SECURITY.md
// records this.

chrome.runtime.onInstalled.addListener(() => { helper.ensureAlarms(); });
chrome.runtime.onStartup.addListener(() => { helper.ensureAlarms(); });
chrome.alarms.onAlarm.addListener((alarm) => { helper.onAlarm(alarm); });
chrome.permissions.onRemoved.addListener((removed) => { helper.permissionsRemoved(removed); });
chrome.runtime.onMessage.addListener((msg, sender, sendResponse) => {
  helper.onMessage(msg, sender).then(sendResponse, () => sendResponse({ ok: false }));
  return true;   // answered asynchronously
});
