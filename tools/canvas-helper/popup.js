// The popup: status only (paired or not, where, the last sync), Unpair,
// Revoke in Kwil (opens Kwil, where Revoke helper ends the key), and, on
// the current tab's https site (activeTab):
//   - "Use as my school's Canvas" (a Canvas on its own domain), or
//     "This is my Kwil", which asks "Send your Canvas courses to <host>?"
//     first: each asks Chrome for that one origin, from the click;
//   - "Switch to <host>" on a Kwil page while paired with another Kwil;
//   - "Allow on this site" (or, for the built-in hosts, where Chrome's
//     Site access setting is) when the paired Kwil or the Canvas lost its
//     permission.
// No settings to fill in; never the key.

import { canvasOrigin } from "./lib/canvas.js";
import { KWIL_ORIGINS, isInstructure } from "./lib/helper.js";
import { describe, hostOf } from "./lib/status.js";

export const SITE_ACCESS = "Chrome blocked the helper here. Open chrome://extensions (or " +
  "edge://extensions), Kwil Canvas helper, Details, and set Site access to allow this site.";

export async function start(doc, chrome, now = Date.now) {
  const $ = (id) => doc.getElementById(id);
  let st = {};
  let site = null;
  let tabId = null;
  let armed = false;
  let kwilArmed = false;

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
    const canvasSites = st.canvasSites || [];
    const kwilSites = st.kwilSites || [];
    const builtIn = Boolean(site) && (KWIL_ORIGINS.includes(site) || isInstructure(site));
    const known = !site || builtIn || canvasSites.includes(site) || kwilSites.includes(site) ||
      site === st.kwil || site === st.canvas;
    $("canvas-box").hidden = known;
    $("kwil-box").hidden = known;
    const kwilPage = Boolean(site) && (KWIL_ORIGINS.includes(site) || kwilSites.includes(site));
    $("switch-box").hidden = !(st.paired && kwilPage && site !== st.kwil);
    $("switch-ask").textContent = st.paired
      ? `Paired with ${hostOf(st.kwil)}. Send your Canvas courses to ${hostOf(site)} instead?`
      : "";
    $("switch").textContent = `Switch to ${hostOf(site)}`;
    let blocked = false;
    if (site && (site === st.kwil || site === st.canvas)) {
      try {
        blocked = !await chrome.permissions.contains({ origins: [`${site}/*`] });
      } catch (e) { blocked = false; }
    }
    $("access-box").hidden = !blocked;
    $("access-text").textContent = builtIn ? SITE_ACCESS
      : "The helper isn't allowed on this site any more.";
    $("regrant").hidden = builtIn;   // a built-in host: Chrome's Site access setting
  }

  try {
    const [tab] = await chrome.tabs.query({ active: true, currentWindow: true });
    site = tab && tab.url ? canvasOrigin(tab.url) : null;   // activeTab: this tab only
    tabId = tab ? tab.id : null;
  } catch (e) { site = null; }

  // Chrome needs the permission request straight from the click.
  function askThen(type) {
    return chrome.permissions.request({ origins: [`${site}/*`] }).then(async (ok) => {
      if (ok) await chrome.runtime.sendMessage({ type, origin: site, tabId });
      await render();
    });
  }

  $("enable-canvas").addEventListener("click", () => site && askThen("enable-canvas"));
  $("enable-kwil").addEventListener("click", () => {
    if (!site) return null;
    if (!kwilArmed) {
      kwilArmed = true;
      $("kwil-ask").textContent = `Send your Canvas courses to ${hostOf(site)}?`;
      $("enable-kwil").textContent = `Yes, send them to ${hostOf(site)}`;
      return null;
    }
    return askThen("enable-kwil");
  });
  $("regrant").addEventListener("click", () =>
    site && askThen(site === st.canvas ? "enable-canvas" : "enable-kwil"));
  $("switch").addEventListener("click", async () => {
    await chrome.runtime.sendMessage({ type: "switch-kwil", origin: site, tabId });
    await render();
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
