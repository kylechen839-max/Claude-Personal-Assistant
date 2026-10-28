// One sync run: read the person's active courses from their Canvas (with
// the browser's own session), push the manifest(s) to the Kwil they
// paired with, then push only the files Kwil's answers asked for, at most
// `maxFiles` a run. Canvas reads and Kwil pushes never share anything but
// the extracted JSON and file bytes: the key goes only to Kwil (KwilClient),
// the Canvas session only to Canvas (CanvasReader).
//
// Returns {result, counts, retryAfter}. result: "ok", "partial" (Canvas's
// rate limit or the time budget cut the reading; what was read in full
// was pushed), "canvas_signed_out", "canvas_rate_limited", "unpaired",
// "kwil_off", "kwil_busy", "kwil_rate_limited", "kwil_unreachable",
// "kwil_rejected". Logs carry counts and codes only: never the key, a
// URL, a title or any course text.

import { CanvasError, CanvasReader } from "./canvas.js";
import { KwilClient, KwilError } from "./kwil.js";
import { batches, buildCourse, canvasId } from "./manifest.js";

export const RUN_SECONDS = 240;         // a run's time budget (a service worker's life)
export const MAX_FILES = 40;            // files a run (the server's [canvas] max_files)
export const ANNOUNCEMENT_DAYS = 120;   // coursefiles.ANNOUNCEMENT_DAYS
export const MAX_PAGE_FETCHES = 30;     // pages read one by one when a listing had no body
export const DEFAULT_BUDGET = 3_000_000;  // manifest bytes when Kwil didn't say
const STOPS = new Set(["signed_out", "rate_limited", "timed_out", "network"]);

export function manifestBudget(maxFileBytes) {
  const n = Number(maxFileBytes);
  if (!Number.isFinite(n) || n <= 0) return DEFAULT_BUDGET;
  return Math.max(200_000, Math.min(n, 8_000_000));
}

function startDate(nowMs) {
  return new Date(nowMs - ANNOUNCEMENT_DAYS * 86_400_000).toISOString().slice(0, 10);
}

// What was read, course by course; stops early (keeping the courses read
// in full) on a rate limit, the time budget or a lost connection.
async function readCanvas(canvas, nowMs, log) {
  const all = await canvas.courses();   // a stop here stops the run
  const courses = all.filter((c) => c && typeof c === "object" && canvasId(c.id) !== null
                                    && !c.access_restricted_by_date);
  const done = [];
  const fileUrls = new Map();   // "course:file" -> Canvas URL, for the wanted ones only
  let stopped = null;
  let pageFetches = 0;
  const optional = async (read) => {
    try { return await read(); } catch (e) {
      if (e instanceof CanvasError && !STOPS.has(e.code)) return null;  // a hidden tab
      throw e;
    }
  };
  for (const c of courses) {
    const cid = canvasId(c.id);
    try {
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
      done.push(buildCourse(c, got));
    } catch (e) {
      if (!(e instanceof CanvasError)) throw e;
      stopped = e.code;
      break;
    }
  }
  let news = null;
  if (!stopped && done.length) {
    try {
      news = await optional(() => canvas.announcements(done.map((c) => c.id), startDate(nowMs)));
    } catch (e) {
      if (!(e instanceof CanvasError)) throw e;
      stopped = e.code;
    }
  }
  log(`Kwil helper: read ${done.length} of ${courses.length} course(s)` +
      (stopped ? ` (stopped: ${stopped})` : ""));
  return { courses: done, news, fileUrls, stopped };
}

export async function runSync({ pairing, canvasOrigin, fetch, sleep, now = Date.now,
                                log = () => {}, budget = RUN_SECONDS }) {
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

  let read;
  try {
    read = await readCanvas(canvas, now(), log);
  } catch (e) {
    if (!(e instanceof CanvasError)) throw e;
    const result = e.code === "signed_out" ? "canvas_signed_out"
      : e.code === "rate_limited" ? "canvas_rate_limited"
      : e.code === "network" ? "canvas_unreachable" : "canvas_error";
    log(`Kwil helper: Canvas couldn't be read (${e.code})`);
    return { result, counts };
  }

  let maxFileBytes = Number(pairing.maxFileBytes) || 0;
  const want = [];
  try {
    for (const batch of batches(read.courses, read.news, manifestBudget(maxFileBytes))) {
      const answer = await kwil.pushManifest(batch);
      counts.courses += batch.courses.length;
      for (const k of ["added", "updated", "unchanged", "removed"]) {
        counts[k] += Number(answer.counts && answer.counts[k]) || 0;
      }
      if (Number(answer.max_file_bytes) > 0) maxFileBytes = Number(answer.max_file_bytes);
      for (const w of Array.isArray(answer.want) ? answer.want : []) {
        const cid = canvasId(w && w.course_id);
        const fid = canvasId(w && w.file_id);
        if (cid !== null && fid !== null) want.push([cid, fid]);
      }
    }
    counts.wanted = want.length;
    const limit = Math.min(Number(pairing.maxFiles) || MAX_FILES, MAX_FILES);
    for (const [cid, fid] of want.slice(0, limit)) {
      const url = read.fileUrls.get(`${cid}:${fid}`);
      if (!url) continue;   // only files this run listed, on this Canvas
      let got;
      try {
        // one byte over Kwil's limit: Kwil keeps it as "too large", unread
        got = await canvas.download(url, maxFileBytes > 0 ? maxFileBytes + 1 : 4_000_000);
      } catch (e) {
        if (!(e instanceof CanvasError)) throw e;
        if (STOPS.has(e.code) && e.code !== "network") {
          read.stopped = read.stopped || e.code;
          break;
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
    }
  } catch (e) {
    if (!(e instanceof KwilError)) throw e;
    const result = { unpaired: "unpaired", off: "kwil_off", busy: "kwil_busy",
                     rate_limited: "kwil_rate_limited", network: "kwil_unreachable",
                     too_large: "kwil_too_large" }[e.code] || "kwil_rejected";
    log(`Kwil helper: Kwil answered ${e.code}`);
    return { result, counts, retryAfter: e.retryAfter || 0, maxFileBytes };
  }
  const result = read.stopped === "rate_limited" ? "canvas_rate_limited"
    : read.stopped === "signed_out" ? "canvas_signed_out"
    : read.stopped ? "partial" : "ok";
  log(`Kwil helper: pushed ${counts.courses} course(s): ${counts.added} added, ` +
      `${counts.updated} updated, ${counts.unchanged} unchanged, ${counts.removed} removed, ` +
      `${counts.files} of ${counts.wanted} file(s) wanted (${result})`);
  return { result, counts, maxFileBytes };
}
