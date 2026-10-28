// The manifest pushed to Kwil (kwil/canvas_helper.py clean_manifest):
// Canvas's own JSON, cut down to the fields Kwil's pipeline reads
// (coursefiles.sync_content). Nothing else goes: no grades, scores,
// submissions, enrollments, people, and no file `url` (it carries a
// Canvas capability, verifier=; Kwil fetches nothing and the helper pushes
// the bytes itself). A listing that couldn't be read is null (Kwil keeps
// what it has); a list is all of it (Kwil drops what it no longer has).
// Big manifests are split into batches by course, each under the
// server's body limit: a course missing from a manifest is untouched.

export const MAX_COURSES = 100;     // canvas_helper.MAX_COURSES
export const MAX_LISTING = 1000;    // canvas_helper.MAX_LISTING
export const MAX_HTML = 400_000;    // coursefiles' text cap per document
export const MAX_NAME = 300;        // canvas_helper.MAX_NAME
export const LISTINGS = ["pages", "assignments", "modules", "files"];

const FILE_FIELDS = ["display_name", "filename", "content-type", "size", "updated_at",
                     "modified_at", "locked_for_user", "hidden_for_user", "hidden",
                     "locked", "mime_class"];

// A Canvas id (a positive integer, or its digits), else null.
export function canvasId(value) {
  if (typeof value === "string" && /^\d{1,15}$/.test(value.trim())) value = Number(value.trim());
  return Number.isSafeInteger(value) && value > 0 && value <= 1e15 ? value : null;
}

function html(value) {
  return typeof value === "string" ? value.slice(0, MAX_HTML) : null;
}

function text(value, max = MAX_NAME) {
  return typeof value === "string" ? value.slice(0, max) : "";
}

function pick(obj, fields) {
  const out = {};
  for (const f of fields) if (obj[f] !== undefined) out[f] = obj[f];
  return out;
}

export function pickPage(p) {
  const out = pick(p, ["page_id", "url", "title", "updated_at", "published",
                       "locked_for_user"]);
  if (typeof p.body === "string") out.body = html(p.body);
  return out;
}

export function pickAssignment(a) {
  return { id: canvasId(a.id), name: text(a.name), due_at: a.due_at || null,
           updated_at: a.updated_at || null, description: html(a.description) };
}

export function pickModule(m) {
  const items = Array.isArray(m.items) ? m.items : [];
  return { id: canvasId(m.id), name: text(m.name),
           items: items.slice(0, MAX_LISTING).filter((i) => i && typeof i === "object")
             .map((i) => ({ title: text(i.title), type: text(i.type, 40) })) };
}

export function pickFile(f) {
  return { id: canvasId(f.id), ...pick(f, FILE_FIELDS) };
}

export function pickAnnouncement(n) {
  return { id: canvasId(n.id), title: text(n.title), message: html(n.message),
           posted_at: n.posted_at || null, context_code: text(n.context_code, 40) };
}

function listing(items, pickOne) {
  if (!Array.isArray(items)) return null;
  return items.filter((x) => x && typeof x === "object").slice(0, MAX_LISTING).map(pickOne)
    .filter((x) => !("id" in x) || x.id !== null);
}

// One course of the manifest, from Canvas's course object and what was
// read of its listings (null: not read).
export function buildCourse(course, got) {
  const out = { id: canvasId(course.id), name: text(course.name),
                course_code: text(course.course_code),
                syllabus_body: html(course.syllabus_body) || "" };
  out.pages = listing(got.pages, pickPage);
  out.assignments = listing(got.assignments, pickAssignment);
  out.modules = listing(got.modules, pickModule);
  out.files = listing(got.files, pickFile);
  return out;
}

export function size(value) {
  return new TextEncoder().encode(JSON.stringify(value)).length;
}

// A course made to fit `budget` bytes alone: its biggest listings become
// null (Kwil keeps what it has of them), then the syllabus is cut.
function fit(course, budget) {
  let c = course;
  const room = budget - 2048;  // the batch's own wrapping and announcements' share
  while (size(c) > room) {
    const biggest = LISTINGS.filter((n) => c[n] !== null)
      .sort((a, b) => size(c[b]) - size(c[a]))[0];
    if (!biggest) {
      const over = size(c) - room;
      c = { ...c, syllabus_body: c.syllabus_body.slice(0, Math.max(0,
        c.syllabus_body.length - over - 64)) };
      break;
    }
    c = { ...c, [biggest]: null };
  }
  return c;
}

// [{courses, announcements}, ...]: every course once, each batch at most
// MAX_COURSES courses and about `budget` bytes, with the announcements of
// its own courses (or null for all when they couldn't be read).
export function batches(courses, announcements, budget) {
  const news = Array.isArray(announcements) ? announcements.map(pickAnnouncement)
    .filter((n) => n.id !== null) : null;
  const newsOf = (ids) => news === null ? null
    : news.filter((n) => ids.has(Number((n.context_code.match(/^course_(\d+)$/) || [])[1])));
  const out = [];
  let current = [];
  let used = 0;
  const flush = () => {
    if (!current.length) return;
    const ids = new Set(current.map((c) => c.id));
    out.push({ courses: current, announcements: newsOf(ids) });
    current = [];
    used = 0;
  };
  for (const raw of courses) {
    const c = fit(raw, budget);
    const n = size(c) + size(newsOf(new Set([c.id])) || []);
    if (current.length && (used + n > budget - 1024 || current.length >= MAX_COURSES)) flush();
    current.push(c);
    used += n;
  }
  flush();
  return out;
}
