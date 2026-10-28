// The service worker's logic, with `chrome`, `fetch` and the clock passed
// in (tests give fakes). background.js wires it to Chrome's events.
//
// State (chrome.storage.local, read only here and by nothing on a page):
//   pairing: {key, origin, pairedAt, maxFileBytes, maxFiles}: the sync key
//            and the Kwil origin it was paired on (the only place it goes)
//   canvas:  {origin, signedOut}: the school's Canvas, learned from where
//            the person browses
//   status:  {lastSync, lastRun, result, counts, nextAfter, backoffMs}
//   sites:   origins the person enabled from the popup (custom Canvas or
//            Kwil domains), each with its own granted host permission
// The popup sees only `status()` (never the key).

import { CanvasReader, canvasOrigin } from "./canvas.js";
import { KEY, kwilOrigin } from "./kwil.js";
import { runSync } from "./sync.js";

export const KWIL_ORIGINS = ["https://heykwil.com"];
export const PERIOD_MINUTES = 360;           // a background sync every 6 hours
export const SOON_MINUTES = 0.5;             // after pairing or a Canvas visit
export const MIN_BACKOFF = 15 * 60_000;
export const MAX_BACKOFF = 6 * 3600_000;
export const PROBE_EVERY = 10 * 60_000;      // a Canvas origin probed at most this often
const MAX_SITES = 20;
const MAX_PROBES = 50;
const SCRIPT_ID = "kwil-sites";
const BACKOFF = new Set(["canvas_rate_limited", "kwil_rate_limited", "kwil_busy",
                         "kwil_unreachable", "canvas_unreachable"]);

export function isInstructure(origin) {
  const o = canvasOrigin(origin);
  return Boolean(o) && /^https:\/\/[a-z0-9-]+(\.[a-z0-9-]+)*\.instructure\.com$/i.test(o);
}

// The origin Chrome says a message came from (never what the message says).
export function senderOrigin(sender) {
  if (!sender) return null;
  const text = sender.origin || sender.url;
  try { return new URL(String(text)).origin; } catch (e) { return null; }
}

export function createHelper({ chrome, fetch, sleep, now = Date.now, log = () => {},
                               kwilOrigins = KWIL_ORIGINS }) {
  const local = chrome.storage.local;
  const probes = new Map();      // origin -> last probe time (bounded)
  let running = null;

  async function get(...keys) { return (await local.get(keys)) || {}; }

  function fromSelf(sender) {
    return Boolean(sender) && sender.id === chrome.runtime.id;
  }

  function fromPopup(sender) {
    // an extension page of ours (the popup), not a content script
    return fromSelf(sender) && !sender.tab &&
      String(sender.url || "").startsWith(chrome.runtime.getURL(""));
  }

  async function sites() {
    const { sites: list } = await get("sites");
    return Array.isArray(list) ? list.filter((o) => canvasOrigin(o)) : [];
  }

  async function scheduleSoon() {
    await chrome.alarms.create("kwil-sync-soon", { delayInMinutes: SOON_MINUTES });
  }

  async function ensureAlarms() {
    const alarm = await chrome.alarms.get("kwil-sync");
    if (!alarm) {
      await chrome.alarms.create("kwil-sync", { delayInMinutes: 1,
                                                periodInMinutes: PERIOD_MINUTES });
    }
  }

  // ---- pairing (from the content script on the Kwil page) ----
  async function paired(msg, sender) {
    const origin = kwilOrigin(senderOrigin(sender));
    const { canvas } = await get("canvas");
    const allowed = origin && sender.tab && (kwilOrigins.includes(origin) ||
                                             (await sites()).includes(origin));
    if (!allowed || isInstructure(origin) || (canvas && canvas.origin === origin)) {
      log("Kwil helper: a pairing from a page that isn't Kwil was ignored");
      return { ok: false };
    }
    if (!msg || typeof msg.key !== "string" || !KEY.test(msg.key)) return { ok: false };
    const pairing = { key: msg.key, origin, pairedAt: new Date(now()).toISOString(),
                      maxFileBytes: Math.max(0, Number(msg.maxFileBytes) || 0),
                      maxFiles: Math.max(0, Number(msg.maxFiles) || 0) };
    await local.set({ pairing, status: {} });
    log("Kwil helper: paired");
    await ensureAlarms();
    await scheduleSoon();
    return { ok: true };
  }

  // ---- the school's Canvas (from the content script on a Canvas page) ----
  async function probe(origin) {
    const reader = new CanvasReader({ origin, fetch, sleep, now,
                                      deadline: now() + 30_000 });
    try {
      const { body } = await reader.getJSON(
        `${origin}/api/v1/courses?enrollment_state=active&per_page=1`);
      return Array.isArray(body);
    } catch (e) { return false; }
  }

  async function canvasSeen(sender) {
    const origin = canvasOrigin(senderOrigin(sender));
    if (!origin || !sender.tab) return { ok: false };
    const ours = await sites();
    const { pairing, canvas, status } = await get("pairing", "canvas", "status");
    if (!(isInstructure(origin) || ours.includes(origin)) || kwilOrigins.includes(origin) ||
        (pairing && pairing.origin === origin)) {
      return { ok: false };
    }
    const same = canvas && canvas.origin === origin;
    const stale = !status || !status.lastSync ||
      now() - Date.parse(status.lastSync) > PERIOD_MINUTES * 60_000;
    if (same && !canvas.signedOut && !stale) return { ok: true };
    const last = probes.get(origin) || 0;
    if (now() - last < PROBE_EVERY) return { ok: true };
    probes.delete(origin);
    probes.set(origin, now());
    while (probes.size > MAX_PROBES) probes.delete(probes.keys().next().value);
    if (!await probe(origin)) return { ok: false };   // not Canvas, or signed out there
    await local.set({ canvas: { origin, signedOut: false } });
    log("Kwil helper: Canvas found");
    if (pairing) await scheduleSoon();
    return { ok: true };
  }

  // ---- the popup ----
  async function status() {
    const { pairing, canvas, status: st } = await get("pairing", "canvas", "status");
    const s = st || {};
    return { paired: Boolean(pairing && pairing.key), kwil: pairing ? pairing.origin : null,
             pairedAt: pairing ? pairing.pairedAt : null,
             canvas: canvas ? canvas.origin : null,
             canvasSignedOut: Boolean(canvas && canvas.signedOut),
             lastSync: s.lastSync || null, lastRun: s.lastRun || null,
             result: s.result || null, counts: s.counts || null,
             nextAfter: s.nextAfter || null, sites: await sites(), running: Boolean(running) };
  }

  async function unpair() {
    await local.remove(["pairing", "status", "canvas"]);
    await chrome.alarms.clear("kwil-sync-soon");
    log("Kwil helper: unpaired");
    return { ok: true };
  }

  async function registerSites(list) {
    const have = await chrome.scripting.getRegisteredContentScripts({ ids: [SCRIPT_ID] });
    if (have && have.length) await chrome.scripting.unregisterContentScripts({ ids: [SCRIPT_ID] });
    if (!list.length) return;
    await chrome.scripting.registerContentScripts([{
      id: SCRIPT_ID, matches: list.map((o) => `${o}/*`), runAt: "document_idle",
      js: ["content/kwil-pair.js", "content/canvas-seen.js"], persistAcrossSessions: true }]);
  }

  // A custom domain (the school's Canvas, or a Kwil of its own), enabled by
  // a click in the popup, which asked Chrome for that one origin first.
  async function enableSite(msg) {
    const origin = canvasOrigin(msg && msg.origin);
    if (!origin) return { ok: false };
    const granted = await chrome.permissions.contains({ origins: [`${origin}/*`] });
    if (!granted) return { ok: false, error: "permission" };
    const list = (await sites()).filter((o) => o !== origin);
    list.push(origin);
    while (list.length > MAX_SITES) list.shift();
    await local.set({ sites: list });
    await registerSites(list);
    if (Number.isInteger(msg.tabId)) {
      try {
        await chrome.scripting.executeScript({
          target: { tabId: msg.tabId }, files: ["content/kwil-pair.js", "content/canvas-seen.js"] });
      } catch (e) { /* the tab moved on: the registered script runs next visit */ }
    }
    log("Kwil helper: a site was enabled");
    return { ok: true };
  }

  async function permissionsRemoved(removed) {
    const gone = new Set(((removed && removed.origins) || [])
      .map((p) => canvasOrigin(String(p).replace(/\/\*$/, ""))));
    const list = (await sites()).filter((o) => !gone.has(o));
    await local.set({ sites: list });
    await registerSites(list);
  }

  // ---- a sync run ----
  async function hostsGranted(pairing, canvas) {
    try {
      return await chrome.permissions.contains({
        origins: [`${pairing.origin}/*`, `${canvas.origin}/*`] });
    } catch (e) { return false; }
  }

  async function syncOnce() {
    const { pairing, canvas, status: old } = await get("pairing", "canvas", "status");
    if (!pairing || !pairing.key) return { result: "unpaired" };
    const st = { ...(old || {}) };
    if (!canvas || !canvas.origin) {
      await local.set({ status: { ...st, result: "no_canvas" } });
      return { result: "no_canvas" };
    }
    if (st.nextAfter && now() < st.nextAfter) {   // backing off (a pairing clears it)
      return { result: "waiting" };
    }
    if (!await hostsGranted(pairing, canvas)) {
      await local.set({ status: { ...st, result: "needs_permission", lastRun: iso() } });
      return { result: "needs_permission" };
    }
    const out = await runSync({ pairing, canvasOrigin: canvas.origin, fetch, sleep, now, log });
    const { pairing: current } = await get("pairing");
    if (!current || current.key !== pairing.key) return out;   // unpaired or re-paired meanwhile
    st.lastRun = iso();
    st.result = out.result;
    st.counts = out.counts;
    if (out.result === "ok" || out.result === "partial") st.lastSync = st.lastRun;
    if (BACKOFF.has(out.result)) {
      st.backoffMs = Math.min(MAX_BACKOFF, Math.max(MIN_BACKOFF, 2 * (st.backoffMs || 0),
                                                     1000 * (out.retryAfter || 0)));
      st.nextAfter = now() + st.backoffMs;
    } else {
      st.backoffMs = 0;
      st.nextAfter = 0;
    }
    const writes = { status: st };
    if (out.result === "canvas_signed_out") writes.canvas = { ...canvas, signedOut: true };
    if (out.result === "ok" && canvas.signedOut) writes.canvas = { ...canvas, signedOut: false };
    if (out.result === "unpaired") {
      await local.remove(["pairing"]);   // revoked or replaced in Kwil: pair again
    } else {
      let max = Number(out.maxFileBytes) || pairing.maxFileBytes;
      if (out.result === "kwil_too_large") max = Math.floor((max || 3_000_000) / 2);
      if (max !== pairing.maxFileBytes) writes.pairing = { ...pairing, maxFileBytes: max };
    }
    await local.set(writes);
    return out;
  }

  function iso() { return new Date(now()).toISOString(); }

  // One run at a time.
  function sync() {
    if (running) return running;
    running = syncOnce().finally(() => { running = null; });
    return running;
  }

  async function onAlarm(alarm) {
    if (alarm && (alarm.name === "kwil-sync" || alarm.name === "kwil-sync-soon")) {
      return sync();
    }
    return null;
  }

  async function onMessage(msg, sender) {
    if (!fromSelf(sender) || !msg || typeof msg.type !== "string") return { ok: false };
    if (msg.type === "kwil-paired") return paired(msg, sender);
    if (msg.type === "canvas-seen") return canvasSeen(sender);
    if (!fromPopup(sender)) return { ok: false };
    if (msg.type === "status") return status();
    if (msg.type === "unpair") return unpair();
    if (msg.type === "enable-site") return enableSite(msg);
    return { ok: false };
  }

  return { onMessage, onAlarm, ensureAlarms, sync, status, unpair, enableSite,
           permissionsRemoved };
}
