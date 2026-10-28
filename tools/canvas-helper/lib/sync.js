// One sync run: read the person's active courses from their Canvas (with
// the browser's own session) and push them to the Kwil they paired with as
// they are read: a manifest as soon as a batch of courses fills (Kwil's
// size limit), then the files Kwil's answer to it asked for, at most
// `maxFiles` a run. A worker that Chrome stops part-way has still sent
// what it read. Canvas reads and Kwil pushes never share anything but the
// extracted JSON and file bytes: the key goes only to Kwil (KwilClient),
// the Canvas session only to Canvas (CanvasReader).
//
// Returns {result, counts, retryAfter}. result: "ok", "partial" (Canvas's
// rate limit or the time budget cut the reading; what was read in full
// was pushed), "canvas_signed_out", "canvas_rate_limited", "unpaired",
// "kwil_off", "kwil_busy", "kwil_rate_limited", "kwil_unreachable",
// "kwil_rejected". The first manifest also carries what the person's
// Canvas settings page says of access tokens (`canvas`: {host,
// can_create_tokens, only_student}; CanvasReader.tokenPolicy, one GET,
// never a token made), when it said anything, so Kwil can pick the way in
// for that school. `progress()` is awaited after each course read and each
// push (the worker keeps itself alive and notes progress there). Logs
// carry counts and codes only: never the key, a URL, a title or any
// course text.

import { CanvasError, CanvasReader, policyHost } from "./canvas.js";
import { KwilClient, KwilError } from "./kwil.js";
import { MAX_COURSES, batches, buildCourse, canvasId, size } from "./manifest.js";

export const RUN_SECONDS = 240;         // a run's time budget for reading Canvas
export const MAX_FILES = 40;            // files a run (the server's [canvas] max_files)
export const ANNOUNCEMENT_DAYS = 120;   // coursefiles.ANNOUNCEMENT_DAYS
export const MAX_PAGE_FETCHES = 30;     // pages read one by one when a listing had no body
export const DEFAULT_BUDGET = 3_000_000;  // manifest bytes when Kwil didn't say
const NEWS_ROOM = 64_000;               // a batch's room kept for its announcements
const STOPS = new Set(["signed_out", "rate_limited", "timed_out", "network"]);

export function manifestBudget(maxFileBytes) {
  const n = Number(maxFileBytes);
  if (!Number.isFinite(n) || n <= 0) return DEFAULT_BUDGET;
  return Math.max(200_000, Math.min(n, 8_000_000));
}

function startDate(nowMs) {
  return new Date(nowMs - ANNOUNCEMENT_DAYS * 86_400_000).toISOString().slice(0, 10);
}

// A listing, or null when that one listing can't be read (a hidden tab, a
// 404); a rate limit, the time budget, a lost connection or a sign-out
// stop the run (thrown).
async function optional(read) {
  try { return await read(); } catch (e) {
    if (e instanceof CanvasError && !STOPS.has(e.code)) return null;
    throw e;
  }
}

export async function runSync({ pairing, canvasOrigin, fetch, sleep, now = Date.now,
                                log = () => {}, budget = RUN_SECONDS,
                                progress = async () => {} }) {
  const counts = { courses: 0, added: 0, updated: 0, unchanged: 0, removed: 0, files: 0,
                   wanted: 0 };
  const deadline = now() + budget * 1000;
  let kwil;
  try {
    kwil = new KwilClient({ origin: pairing && pairing.origin, key: pairing && pairing.key,
                            fetch });
  } catch (e) {
    return { result: "unpaired", counts };
  }
  let canvas;
  try {
    canvas = new CanvasReader({ origin: canvasOrigin, fetch, sleep, now, deadline });
  } catch (e) {
    return { result: "no_canvas", counts };
  }

  let all;
  try {
    all = await canvas.courses();   // a stop here stops the run
  } catch (e) {
    if (!(e instanceof CanvasError)) throw e;
    const result = e.code === "signed_out" ? "canvas_signed_out"
      : e.code === "rate_limited" ? "canvas_rate_limited"
      : e.code === "network" ? "canvas_unreachable" : "canvas_error";
    log(`Kwil helper: Canvas couldn't be read (${e.code})`);
    return { result, counts };
  }
  const courses = all.filter((c) => c && typeof c === "object" && canvasId(c.id) !== null
                                    && !c.access_restricted_by_date);
  // may this account make a token? (unknown, or a host Kwil wouldn't take: not sent)
  const policy = await canvas.tokenPolicy();
  const host = policyHost(new URL(canvas.origin).hostname);
  let policyNote = typeof policy.can_create_tokens === "boolean" && host
    ? { host, can_create_tokens: policy.can_create_tokens, only_student: policy.only_student }
    : null;

  let maxFileBytes = Number(pairing.maxFileBytes) || 0;
  const budgetBytes = manifestBudget(maxFileBytes);
  let filesLeft = Math.min(Number(pairing.maxFiles) || MAX_FILES, MAX_FILES);
  const fileUrls = new Map();   // "course:file" -> Canvas URL, from this run's listings
  let stopped = null;
  let pageFetches = 0;
  let read = 0;

  async function readCourse(c) {
    const cid = canvasId(c.id);
    const got = {};
    got.pages = await optional(() => canvas.pages(cid));
    for (const p of got.pages || []) {
      if (p && typeof p === "object" && typeof p.body !== "string" && p.url
          && p.published !== false && !p.locked_for_user && pageFetches < MAX_PAGE_FETCHES) {
        pageFetches += 1;
        const full = await optional(() => canvas.page(cid, p.url));
        if (full && typeof full.body === "string") p.body = full.body;
      }
    }
    got.assignments = await optional(() => canvas.assignments(cid));
    got.modules = await optional(() => canvas.modules(cid));
    got.files = await optional(() => canvas.files(cid));
    for (const f of got.files || []) {
      const fid = f && canvasId(f.id);
      if (fid !== null && typeof f.url === "string") fileUrls.set(`${cid}:${fid}`, f.url);
    }
    return buildCourse(c, got);
  }

  // The files one manifest's answer asked for, while the run's share lasts.
  async function pushFiles(want) {
    for (const [cid, fid] of want) {
      if (filesLeft <= 0 || stopped) return;
      const url = fileUrls.get(`${cid}:${fid}`);
      if (!url) continue;   // only files this run listed, on this Canvas
      filesLeft -= 1;
      let got;
      try {
        // one byte over Kwil's limit: Kwil keeps it as "too large", unread
        got = await canvas.download(url, maxFileBytes > 0 ? maxFileBytes + 1 : 4_000_000);
      } catch (e) {
        if (!(e instanceof CanvasError)) throw e;
        if (STOPS.has(e.code) && e.code !== "network") {
          stopped = stopped || e.code;
          return;
        }
        // this file only (a 403/404, or a file store the browser can't
        // reach): Kwil asks again next time
        continue;
      }
      try {
        await kwil.pushFile(cid, fid, got.bytes);
        counts.files += 1;
      } catch (e) {
        if (!(e instanceof KwilError) || e.code !== "not_wanted") throw e;
      }
      await progress();
    }
  }

  // Push the courses read so far (with their announcements), then the
  // files Kwil asked for.
  let pending = [];
  let pendingSize = 0;
  async function flush() {
    if (!pending.length) return;
    const ids = pending.map((c) => c.id);
    let news = null;
    if (!stopped) {
      try {
        news = await optional(() => canvas.announcements(ids, startDate(now())));
      } catch (e) {
        if (!(e instanceof CanvasError)) throw e;
        stopped = e.code;
      }
    }
    for (const batch of batches(pending, news, budgetBytes)) {
      if (policyNote) { batch.canvas = policyNote; policyNote = null; }
      const answer = await kwil.pushManifest(batch);
      counts.courses += batch.courses.length;
      for (const k of ["added", "updated", "unchanged", "removed"]) {
        counts[k] += Number(answer.counts && answer.counts[k]) || 0;
      }
      if (Number(answer.max_file_bytes) > 0) maxFileBytes = Number(answer.max_file_bytes);
      const want = [];
      for (const w of Array.isArray(answer.want) ? answer.want : []) {
        const cid = canvasId(w && w.course_id);
        const fid = canvasId(w && w.file_id);
        if (cid !== null && fid !== null) want.push([cid, fid]);
      }
      counts.wanted += want.length;
      await progress();
      await pushFiles(want);
    }
    pending = [];
    pendingSize = 0;
  }

  try {
    for (const c of courses) {
      let course;
      try {
        course = await readCourse(c);
      } catch (e) {
        if (!(e instanceof CanvasError)) throw e;
        stopped = e.code;   // the courses read in full are still pushed
        break;
      }
      read += 1;
      await progress();
      const n = size(course);
      if (pending.length && (pendingSize + n > budgetBytes - NEWS_ROOM ||
                             pending.length >= MAX_COURSES)) {
        await flush();
      }
      pending.push(course);
      pendingSize += n;
    }
    await flush();
  } catch (e) {
    if (!(e instanceof KwilError)) throw e;
    const result = { unpaired: "unpaired", off: "kwil_off", busy: "kwil_busy",
                     rate_limited: "kwil_rate_limited", network: "kwil_unreachable",
                     too_large: "kwil_too_large" }[e.code] || "kwil_rejected";
    log(`Kwil helper: Kwil answered ${e.code}`);
    return { result, counts, retryAfter: e.retryAfter || 0, maxFileBytes };
  }
  const result = stopped === "rate_limited" ? "canvas_rate_limited"
    : stopped === "signed_out" ? "canvas_signed_out"
    : stopped ? "partial" : "ok";
  log(`Kwil helper: read ${read} of ${courses.length} course(s)` +
      (stopped ? ` (stopped: ${stopped})` : ""));
  log(`Kwil helper: pushed ${counts.courses} course(s): ${counts.added} added, ` +
      `${counts.updated} updated, ${counts.unchanged} unchanged, ${counts.removed} removed, ` +
      `${counts.files} of ${counts.wanted} file(s) wanted (${result})`);
  return { result, counts, maxFileBytes };
}
