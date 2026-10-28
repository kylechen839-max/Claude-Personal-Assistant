// The service worker's logic, with `chrome`, `fetch`, timers and the clock
// passed in (tests give fakes). background.js wires it to Chrome's events.
//
// State (chrome.storage.local; the helper's content scripts never read it):
//   pairing:     {key, origin, pairedAt, maxFileBytes, maxFiles}: the sync
//                key and the Kwil origin it was paired on (the only place
//                it goes)
//   canvas:      {origin, signedOut}: the school's Canvas, the one origin a
//                sync reads
//   status:      {lastSync, lastRun, running, result, counts, nextAfter,
//                backoffMs}
//   canvasSites: school Canvas domains of their own the person enabled in
//                the popup (only canvas-seen.js runs there)
//   kwilSites:   Kwil domains of their own the person confirmed in the
//                popup ("Send your Canvas courses to <host>?"; only
//                kwil-pair.js runs there)
//   switchTo:    {origin, until}: the popup's confirmation that a pairing
//                from another Kwil origin may replace the current one
// Each enabled site has its own granted host permission. The popup sees
// only `status()` (never the key).

import { CanvasReader, canvasOrigin } from "./canvas.js";
import { KEY, kwilOrigin } from "./kwil.js";
import { runSync } from "./sync.js";

export const KWIL_ORIGINS = ["https://heykwil.com"];
export const PERIOD_MINUTES = 360;           // a background sync every 6 hours
export const SOON_MINUTES = 0.5;             // after pairing or a Canvas visit
export const MIN_BACKOFF = 15 * 60_000;
export const MAX_BACKOFF = 6 * 3600_000;
export const PROBE_EVERY = 10 * 60_000;      // a Canvas origin probed at most this often
export const KEEPALIVE_MS = 20_000;          // a chrome.* call this often during a run
export const SWITCH_MS = 10 * 60_000;        // a confirmed Kwil switch is good this long
const MAX_SITES = 20;
const MAX_PROBES = 50;
const CANVAS_SCRIPTS = "kwil-canvas-sites";
const KWIL_SCRIPTS = "kwil-kwil-sites";
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
                               kwilOrigins = KWIL_ORIGINS,
                               timers = { setInterval, clearInterval } }) {
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

  async function list(name) {
    const { [name]: value } = await get(name);
    return Array.isArray(value) ? value.filter((o) => canvasOrigin(o)) : [];
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

  async function isKwil(origin) {
    return kwilOrigins.includes(origin) || (await list("kwilSites")).includes(origin);
  }

  // ---- pairing (from the content script on a Kwil page) ----
  // May the page at `sender` pair? A Kwil origin (heykwil.com, or one the
  // person confirmed in the popup), never a Canvas one, and never
  // replacing a pairing with another origin unless the popup confirmed
  // the switch.
  async function mayPair(sender) {
    const origin = kwilOrigin(senderOrigin(sender));
    const { canvas, pairing, switchTo } = await get("canvas", "pairing", "switchTo");
    const allowed = origin && sender.tab && await isKwil(origin);
    if (!allowed || isInstructure(origin) || (canvas && canvas.origin === origin) ||
        (await list("canvasSites")).includes(origin)) {
      return { ok: false };
    }
    if (pairing && pairing.key && pairing.origin !== origin &&
        !(switchTo && switchTo.origin === origin && now() < switchTo.until)) {
      return { ok: false, reason: "paired_elsewhere" };
    }
    return { ok: true, origin };
  }

  async function paired(msg, sender) {
    const may = await mayPair(sender);
    if (!may.ok) {
      log("Kwil helper: a pairing was refused");
      return { ok: false };
    }
    if (!msg || typeof msg.key !== "string" || !KEY.test(msg.key)) return { ok: false };
    const pairing = { key: msg.key, origin: may.origin, pairedAt: new Date(now()).toISOString(),
                      maxFileBytes: Math.max(0, Number(msg.maxFileBytes) || 0),
                      maxFiles: Math.max(0, Number(msg.maxFiles) || 0) };
    await local.set({ pairing, status: {} });
    await local.remove(["switchTo"]);
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

  // A Canvas page was opened. The first Canvas found is pinned; another
  // one replaces it only when the pinned one is signed out (or the person
  // chose it in the popup, enableCanvas): an enabled site can't take over
  // a working Canvas by being visited.
  async function canvasSeen(sender) {
    const origin = canvasOrigin(senderOrigin(sender));
    if (!origin || !sender.tab) return { ok: false };
    const ours = await list("canvasSites");
    const { pairing, canvas, status } = await get("pairing", "canvas", "status");
    if (!(isInstructure(origin) || ours.includes(origin)) || await isKwil(origin) ||
        (pairing && pairing.origin === origin)) {
      return { ok: false };
    }
    const same = canvas && canvas.origin === origin;
    if (canvas && canvas.origin && !same && !canvas.signedOut) return { ok: false };
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
             nextAfter: s.nextAfter || null, canvasSites: await list("canvasSites"),
             kwilSites: await list("kwilSites"), running: Boolean(running) };
  }

  async function unpair() {
    await local.remove(["pairing", "status", "canvas", "switchTo"]);
    await chrome.alarms.clear("kwil-sync-soon");
    log("Kwil helper: unpaired");
    return { ok: true };
  }

  async function register(id, origins, file) {
    const have = await chrome.scripting.getRegisteredContentScripts({ ids: [id] });
    if (have && have.length) await chrome.scripting.unregisterContentScripts({ ids: [id] });
    if (!origins.length) return;
    await chrome.scripting.registerContentScripts([{
      id, matches: origins.map((o) => `${o}/*`), runAt: "document_idle", js: [file],
      persistAcrossSessions: true }]);
  }

  async function inject(tabId, file) {
    if (!Number.isInteger(tabId)) return;
    try {
      await chrome.scripting.executeScript({ target: { tabId }, files: [file] });
    } catch (e) { /* the tab moved on: the registered script runs next visit */ }
  }

  // Add `origin` to one of the enabled lists (and drop it from the other:
  // a site is a Canvas or a Kwil, never both).
  async function enable(name, other, origin) {
    const mine = (await list(name)).filter((o) => o !== origin);
    mine.push(origin);
    while (mine.length > MAX_SITES) mine.shift();
    const theirs = (await list(other)).filter((o) => o !== origin);
    await local.set({ [name]: mine, [other]: theirs });
    await register(CANVAS_SCRIPTS, name === "canvasSites" ? mine : theirs,
                   "content/canvas-seen.js");
    await register(KWIL_SCRIPTS, name === "kwilSites" ? mine : theirs, "content/kwil-pair.js");
  }

  async function granted(origin) {
    try {
      return await chrome.permissions.contains({ origins: [`${origin}/*`] });
    } catch (e) { return false; }
  }

  // "Use this site as my school's Canvas" (the popup asked Chrome for this
  // one origin first): only canvas-seen.js runs there, and it becomes the
  // Canvas a sync reads (checked by the probe when its script reports).
  async function enableCanvas(msg) {
    const origin = canvasOrigin(msg && msg.origin);
    if (!origin || kwilOrigins.includes(origin) || isInstructure(origin)) return { ok: false };
    const { pairing } = await get("pairing");
    if (pairing && pairing.origin === origin) return { ok: false };
    if (!await granted(origin)) return { ok: false, error: "permission" };
    await enable("canvasSites", "kwilSites", origin);
    await local.set({ canvas: { origin, signedOut: true } });  // until its probe answers
    probes.delete(origin);
    await inject(msg.tabId, "content/canvas-seen.js");
    log("Kwil helper: a Canvas site was enabled");
    return { ok: true };
  }

  // "Send your Canvas courses to <host>?" confirmed in the popup: only
  // kwil-pair.js runs there, and its pairing may replace the current one
  // for the next SWITCH_MS.
  async function enableKwil(msg) {
    const origin = kwilOrigin(msg && msg.origin);
    if (!origin || isInstructure(origin)) return { ok: false };
    const { canvas } = await get("canvas");
    if (canvas && canvas.origin === origin) return { ok: false };
    if (!await granted(origin)) return { ok: false, error: "permission" };
    await enable("kwilSites", "canvasSites", origin);
    await local.set({ switchTo: { origin, until: now() + SWITCH_MS } });
    await inject(msg.tabId, "content/kwil-pair.js");
    log("Kwil helper: a Kwil site was enabled");
    return { ok: true };
  }

  // "Switch to <host>" confirmed in the popup, on a Kwil page (heykwil.com
  // or an enabled one) while paired with another: its pairing may replace
  // the current one; the page's script is told to try again.
  async function switchKwil(msg) {
    const origin = kwilOrigin(msg && msg.origin);
    if (!origin || !await isKwil(origin)) return { ok: false };
    await local.set({ switchTo: { origin, until: now() + SWITCH_MS } });
    if (Number.isInteger(msg.tabId)) {
      try {
        await chrome.tabs.sendMessage(msg.tabId, { type: "kwil-pair-now" });
      } catch (e) { /* no pairing script there: "Set up Canvas helper" again */ }
    }
    return { ok: true };
  }

  async function permissionsRemoved(removed) {
    const gone = new Set(((removed && removed.origins) || [])
      .map((p) => canvasOrigin(String(p).replace(/\/\*$/, ""))));
    const canvasSites = (await list("canvasSites")).filter((o) => !gone.has(o));
    const kwilSites = (await list("kwilSites")).filter((o) => !gone.has(o));
    await local.set({ canvasSites, kwilSites });
    await register(CANVAS_SCRIPTS, canvasSites, "content/canvas-seen.js");
    await register(KWIL_SCRIPTS, kwilSites, "content/kwil-pair.js");
  }

  // ---- a sync run ----
  async function hostsGranted(pairing, canvas) {
    try {
      return await chrome.permissions.contains({
        origins: [`${pairing.origin}/*`, `${canvas.origin}/*`] });
    } catch (e) { return false; }
  }

  // Still paired with the same key (not unpaired or re-paired meanwhile)?
  async function stillPaired(pairing) {
    const { pairing: current } = await get("pairing");
    return Boolean(current) && current.key === pairing.key;
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
    st.lastRun = iso();
    await local.set({ status: { ...st, running: true } });
    // Chrome stops an idle worker after about 30 s; a run's own fetches
    // don't count everywhere, so a chrome.* call every KEEPALIVE_MS (and a
    // progress note after each course and push) keeps it going.
    const keepalive = timers.setInterval(() => {
      try {
        const p = chrome.runtime.getPlatformInfo();
        if (p && p.catch) p.catch(() => {});
      } catch (e) { /* not available: the progress notes still count */ }
    }, KEEPALIVE_MS);
    let out;
    try {
      out = await runSync({ pairing, canvasOrigin: canvas.origin, fetch, sleep, now, log,
                            progress: async () => {
                              if (await stillPaired(pairing)) {
                                await local.set({ status: { ...st, running: true,
                                                            progressAt: iso() } });
                              }
                            } });
    } finally {
      timers.clearInterval(keepalive);
    }
    if (!await stillPaired(pairing)) return out;   // unpaired or re-paired meanwhile
    st.result = out.result;
    st.counts = out.counts;
    st.running = false;
    if (out.result === "ok" || out.result === "partial") st.lastSync = iso();
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
    if (msg.type === "kwil-may-pair") return { ok: (await mayPair(sender)).ok };
    if (msg.type === "kwil-paired") return paired(msg, sender);
    if (msg.type === "canvas-seen") return canvasSeen(sender);
    if (!fromPopup(sender)) return { ok: false };
    if (msg.type === "status") return status();
    if (msg.type === "unpair") return unpair();
    if (msg.type === "enable-canvas") return enableCanvas(msg);
    if (msg.type === "enable-kwil") return enableKwil(msg);
    if (msg.type === "switch-kwil") return switchKwil(msg);
    return { ok: false };
  }

  return { onMessage, onAlarm, ensureAlarms, sync, status, unpair, enableCanvas, enableKwil,
           switchKwil, permissionsRemoved };
}
