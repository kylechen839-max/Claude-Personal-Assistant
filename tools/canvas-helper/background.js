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

// The key lives in chrome.storage.local: keep it out of content scripts'
// reach (they never read storage; this is belt and braces).
try {
  const p = chrome.storage.local.setAccessLevel &&
    chrome.storage.local.setAccessLevel({ accessLevel: "TRUSTED_CONTEXTS" });
  if (p && p.catch) p.catch(() => {});
} catch (e) { /* older Chrome: content scripts still never read it */ }

chrome.runtime.onInstalled.addListener(() => { helper.ensureAlarms(); });
chrome.runtime.onStartup.addListener(() => { helper.ensureAlarms(); });
chrome.alarms.onAlarm.addListener((alarm) => { helper.onAlarm(alarm); });
chrome.permissions.onRemoved.addListener((removed) => { helper.permissionsRemoved(removed); });
chrome.runtime.onMessage.addListener((msg, sender, sendResponse) => {
  helper.onMessage(msg, sender).then(sendResponse, () => sendResponse({ ok: false }));
  return true;   // answered asynchronously
});
