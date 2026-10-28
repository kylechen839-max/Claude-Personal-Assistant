// The popup: status only (paired or not, where, the last sync), Unpair,
// Revoke in Kwil (opens Kwil, where Revoke helper ends the key), and, on a
// school or Kwil site with its own domain, Enable on this site (asks
// Chrome for that one origin). No settings to fill in; never the key.

import { canvasOrigin } from "./lib/canvas.js";
import { KWIL_ORIGINS, isInstructure } from "./lib/helper.js";
import { describe } from "./lib/status.js";

export async function start(doc, chrome, now = Date.now) {
  const $ = (id) => doc.getElementById(id);
  let st = {};
  let site = null;
  let tabId = null;
  let armed = false;

  async function render() {
    st = (await chrome.runtime.sendMessage({ type: "status" })) || {};
    const view = describe(st, now());
    $("title").textContent = view.title;
    $("lines").replaceChildren(...view.lines.map((line) => {
      const li = doc.createElement("li");
      li.textContent = line;   // text, never HTML
      return li;
    }));
    $("paired-box").hidden = !st.paired;
    $("enable-box").hidden = !site || KWIL_ORIGINS.includes(site) || isInstructure(site) ||
      (st.sites || []).includes(site) || site === st.kwil || site === st.canvas;
  }

  try {
    const [tab] = await chrome.tabs.query({ active: true, currentWindow: true });
    site = tab && tab.url ? canvasOrigin(tab.url) : null;   // activeTab: this tab only
    tabId = tab ? tab.id : null;
  } catch (e) { site = null; }

  $("enable").addEventListener("click", () => {
    if (!site) return;
    // asked straight from the click (Chrome needs the gesture)
    chrome.permissions.request({ origins: [`${site}/*`] }).then(async (granted) => {
      if (granted) await chrome.runtime.sendMessage({ type: "enable-site", origin: site, tabId });
      await render();
    });
  });
  $("unpair").addEventListener("click", async () => {
    if (!armed) {
      armed = true;
      $("unpair").textContent = "Click again to unpair";
      return;
    }
    await chrome.runtime.sendMessage({ type: "unpair" });
    armed = false;
    $("unpair").textContent = "Unpair this browser";
    await render();
  });
  $("revoke").addEventListener("click", async () => {
    if (st.kwil) await chrome.tabs.create({ url: `${st.kwil}/` });
  });
  await render();
}

if (typeof document !== "undefined" && typeof chrome !== "undefined" && chrome.runtime &&
    chrome.runtime.id) {
  start(document, chrome);
}
