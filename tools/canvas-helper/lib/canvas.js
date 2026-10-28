// Reading the school's Canvas with the person's own signed-in browser
// session: GET only, `credentials: "include"` (the browser attaches its
// own Canvas cookies; this code never reads or sees them), never an
// Authorization header, so the Kwil key can't reach Canvas. Every request
// starts on the one Canvas origin given (a "next" link or a file link
// elsewhere is not fetched). The browser follows redirects: a file
// download goes on to Canvas's file store (it carries no key, only the
// browser's own cookies for that host); an API answer that ended up on
// another origin is an error, never read. The bounds mirror
// kwil/canvas.py's token sync: 100 items a page, at most 10 pages and
// 1000 items a listing (10 MB an answer here, not 2: the browser holds a
// page of 100 page bodies), Canvas's rate limit (429, or a 403 saying so)
// waited out three times (1, 2, 4 s) and then the run stops. Canvas
// prefixes JSON answered to a session with `while(1);`, which is stripped.

export const MAX_PAGES = 10;
export const MAX_ITEMS = 1000;
export const MAX_API_BYTES = 10_000_000;  // a page of 100 page bodies can be big
export const MAX_RETRIES = 3;
export const PER_PAGE = 100;
export const MAX_SETTINGS_BYTES = 2_000_000;  // the settings page, read once a run
const SLUG = /^[\w.-]{1,200}$/;

export class CanvasError extends Error {
  // codes: signed_out, forbidden, not_found, rate_limited, timed_out,
  // too_large, bad_answer, network, off_origin, http_<status>
  constructor(code) { super(code); this.code = code; }
}

// "https://school.instructure.com" for an https origin on the default
// port with no user:password@, else null.
export function canvasOrigin(text) {
  let u;
  try { u = new URL(String(text)); } catch (e) { return null; }
  if (u.protocol !== "https:" || u.username || u.password || u.port) return null;
  if (!u.hostname.includes(".")) return null;
  return u.origin;
}

export function stripGuard(text) {
  return String(text).replace(/^\s*while\(1\);/, "");
}

// The rel="next" URL of a Link header, if it is on `origin` over https.
export function nextLink(header, origin) {
  for (const part of String(header || "").split(",")) {
    const m = part.match(/<([^>]+)>\s*;(.*)/);
    if (m && /rel="?next"?/.test(m[2])) {
      try {
        const u = new URL(m[1]);
        return u.protocol === "https:" && u.origin === origin ? u.href : null;
      } catch (e) { return null; }
    }
  }
  return null;
}

// Canvas's throttle (kwil/canvas.py _rate_limited): 429, or a 403 saying
// "Rate Limit Exceeded" or with no quota left. A plain 403 is a permission.
export function rateLimited(status, headers, text) {
  if (status === 429) return true;
  if (status !== 403) return false;
  if (String(text || "").slice(0, 500).includes("Rate Limit Exceeded")) return true;
  const left = Number(headers && headers.get ? headers.get("x-rate-limit-remaining") : NaN);
  return Number.isFinite(left) && left <= 0;
}

// The Canvas host as Kwil takes it for a token-policy reading
// (kwil/canvas_schools.py normalize): lower case, no trailing dot, a dotted
// public DNS name with a letters-only top-level domain (no IP, punycode
// TLD or LAN-only name), else null (the reading is then not sent).
const LOCAL_TLDS = /\.(local|localhost|internal|lan|home|arpa|test|invalid|example)$/;
export function policyHost(hostname) {
  const host = String(hostname || "").toLowerCase().replace(/\.$/, "");
  if (!/^(?=.{4,253}$)([a-z0-9]([a-z0-9-]{0,61}[a-z0-9])?\.)+[a-z]{2,63}$/.test(host) ||
      LOCAL_TLDS.test(host)) return null;
  return host;
}

// What Canvas's settings page says of access tokens, from the JSON of its
// ENV ("can_update_tokens": the same check as making one;
// "user_is_only_student"): {can_create_tokens, only_student}, each true,
// false, or null when the key is missing or appears with both values.
export function parseTokenPolicy(html) {
  const text = String(html || "");
  const one = (key) => {
    const seen = new Set();
    for (const m of text.matchAll(new RegExp(`"${key}"\\s*:\\s*(true|false)\\b`, "g"))) {
      seen.add(m[1]);
    }
    return seen.size === 1 ? seen.has("true") : null;
  };
  return { can_create_tokens: one("can_update_tokens"), only_student: one("user_is_only_student") };
}

// Sent on to Canvas's sign-in page (a session that ended)?
function signedOut(response, url, origin) {
  if (!response.redirected) return false;
  try {
    const u = new URL(response.url || url);
    return u.origin === origin && /^\/login\b/.test(u.pathname);
  } catch (e) { return false; }
}

// Did the answer come from `origin` (after any redirect the fetch followed)?
function sameOrigin(response, url, origin) {
  try { return new URL(response.url || url).origin === origin; } catch (e) { return false; }
}

// Canvas answers 401 both for "not signed in" ({"status":
// "unauthenticated"}) and for "not allowed" ({"status": "unauthorized"},
// e.g. a course that hides its Files tab): only the first ends the run.
function unauthorized(text) {
  const t = String(text);
  return /"unauthorized"/.test(t) && !/unauthenticated/.test(t) ? "forbidden" : "signed_out";
}

// At most `max` bytes of a response's body; `over` is true when it had
// more (what was read stops there).
export async function readBounded(response, max) {
  if (!response.body || typeof response.body.getReader !== "function") {
    const all = new Uint8Array(await response.arrayBuffer());
    return { bytes: all.slice(0, max), over: all.length > max };
  }
  const reader = response.body.getReader();
  const parts = [];
  let size = 0;
  let over = false;
  for (;;) {
    const { done, value } = await reader.read();
    if (done) break;
    const room = max - size;
    if (value.length > room) {
      parts.push(value.slice(0, room));
      size += room;
      over = true;
      try { await reader.cancel(); } catch (e) { /* already closed */ }
      break;
    }
    parts.push(value);
    size += value.length;
  }
  const bytes = new Uint8Array(size);
  let at = 0;
  for (const p of parts) { bytes.set(p, at); at += p.length; }
  return { bytes, over };
}

export class CanvasReader {
  constructor({ origin, fetch, sleep, now = Date.now, deadline = Infinity }) {
    const pinned = canvasOrigin(origin);
    if (!pinned) throw new CanvasError("off_origin");
    this.origin = pinned;
    this.fetch = fetch;
    this.sleep = sleep;
    this.now = now;
    this.deadline = deadline;
    this.requests = 0;
  }

  _url(path, params) {
    const u = new URL(path, this.origin);
    for (const [k, v] of Object.entries(params || {})) {
      for (const one of Array.isArray(v) ? v : [v]) u.searchParams.append(k, String(one));
    }
    return u.href;
  }

  async _request(url, accept = "application/json") {
    if (new URL(url).origin !== this.origin) throw new CanvasError("off_origin");
    if (this.now() >= this.deadline) throw new CanvasError("timed_out");
    this.requests += 1;
    try {
      // the browser's own Canvas session; no Authorization header, ever
      return await this.fetch(url, { method: "GET", credentials: "include",
                                     headers: { Accept: accept },
                                     redirect: "follow", cache: "no-store" });
    } catch (e) {
      throw new CanvasError("network");
    }
  }

  // One GET of a JSON answer, waiting out the rate limit.
  async getJSON(url) {
    for (let attempt = 0; ; attempt++) {
      const r = await this._request(url);
      const { bytes, over } = await readBounded(r, MAX_API_BYTES);
      const text = new TextDecoder().decode(bytes);
      if (rateLimited(r.status, r.headers, text)) {
        if (attempt >= MAX_RETRIES) throw new CanvasError("rate_limited");
        const wait = 1000 * 2 ** attempt;
        if (this.now() + wait > this.deadline) throw new CanvasError("timed_out");
        await this.sleep(wait);
        continue;
      }
      if (signedOut(r, url, this.origin)) throw new CanvasError("signed_out");
      if (!sameOrigin(r, url, this.origin)) {
        throw new CanvasError("off_origin");  // an API answer redirected elsewhere: not Canvas's
      }
      if (r.status === 401) throw new CanvasError(unauthorized(text));
      if (r.status === 403) throw new CanvasError("forbidden");
      if (r.status === 404) throw new CanvasError("not_found");
      if (r.status !== 200) throw new CanvasError(`http_${r.status}`);
      if (over) throw new CanvasError("too_large");
      try {
        return { body: JSON.parse(stripGuard(text)), headers: r.headers };
      } catch (e) {
        throw new CanvasError("bad_answer");
      }
    }
  }

  // Every item of a listing (Link rel="next" on this origin only).
  async list(path, params) {
    let url = this._url(path, { per_page: PER_PAGE, ...params });
    const items = [];
    for (let page = 0; page < MAX_PAGES && url; page++) {
      const { body, headers } = await this.getJSON(url);
      if (!Array.isArray(body)) throw new CanvasError("bad_answer");
      items.push(...body.slice(0, MAX_ITEMS - items.length));
      if (items.length >= MAX_ITEMS) break;
      url = nextLink(headers && headers.get ? headers.get("link") : "", this.origin);
    }
    return items;
  }

  courses() {
    return this.list("/api/v1/courses", { enrollment_state: "active",
                                          "include[]": "syllabus_body" });
  }

  pages(cid) {
    return this.list(`/api/v1/courses/${Number(cid)}/pages`,
                     { published: "true", sort: "updated_at", order: "desc",
                       "include[]": "body" });
  }

  async page(cid, slug) {
    if (!SLUG.test(String(slug))) throw new CanvasError("bad_answer");
    const { body } = await this.getJSON(this._url(`/api/v1/courses/${Number(cid)}/pages/${slug}`));
    if (!body || typeof body !== "object") throw new CanvasError("bad_answer");
    return body;
  }

  assignments(cid) { return this.list(`/api/v1/courses/${Number(cid)}/assignments`); }

  modules(cid) {
    return this.list(`/api/v1/courses/${Number(cid)}/modules`, { "include[]": "items" });
  }

  files(cid) {
    return this.list(`/api/v1/courses/${Number(cid)}/files`,
                     { sort: "updated_at", order: "desc" });
  }

  // The courses' announcements since `start` (YYYY-MM-DD), ten courses a
  // request, at most MAX_ITEMS.
  async announcements(ids, start) {
    const out = [];
    for (let i = 0; i < ids.length && out.length < MAX_ITEMS; i += 10) {
      out.push(...await this.list("/api/v1/announcements", {
        "context_codes[]": ids.slice(i, i + 10).map((c) => `course_${Number(c)}`),
        start_date: start }));
    }
    return out.slice(0, MAX_ITEMS);
  }

  // May this account make an access token? One GET of the person's own
  // Canvas settings page (/profile/settings, this origin only, at most
  // MAX_SETTINGS_BYTES), reading what Canvas puts in the page's ENV for
  // its own "New access token" button: {can_create_tokens, only_student},
  // each true, false or null (not found). Never throws: a sign-out, a rate
  // limit, a redirect elsewhere, an error or a page without the keys is
  // all null ("unknown"). It never makes a token and posts nothing.
  async tokenPolicy() {
    const unknown = { can_create_tokens: null, only_student: null };
    const url = this._url("/profile/settings");
    let r;
    try { r = await this._request(url, "text/html"); } catch (e) { return unknown; }
    if (r.status !== 200 || signedOut(r, url, this.origin) || !sameOrigin(r, url, this.origin)) {
      return unknown;
    }
    let read;
    try { read = await readBounded(r, MAX_SETTINGS_BYTES); } catch (e) { return unknown; }
    return parseTokenPolicy(new TextDecoder().decode(read.bytes));
  }

  // A file's bytes from its Canvas `url` (on this origin only; Canvas
  // sends it on to its file store), at most `max` bytes read: `over` when
  // it had more. The URL holds a capability (verifier=): never logged.
  async download(url, max) {
    let u;
    try { u = new URL(String(url)); } catch (e) { throw new CanvasError("off_origin"); }
    if (u.origin !== this.origin) throw new CanvasError("off_origin");
    for (let attempt = 0; ; attempt++) {
      const r = await this._request(u.href);
      const head = r.status === 403 || r.status === 401
        ?new TextDecoder().decode((await readBounded(r, 500)).bytes) : "";
      if (rateLimited(r.status, r.headers, head)) {
        if (attempt >= MAX_RETRIES) throw new CanvasError("rate_limited");
        const wait = 1000 * 2 ** attempt;
        if (this.now() + wait > this.deadline) throw new CanvasError("timed_out");
        await this.sleep(wait);
        continue;
      }
      if (signedOut(r, u.href, this.origin)) {
        throw new CanvasError("signed_out");  // never the sign-in page as a file
      }
      if (r.status === 401) throw new CanvasError(unauthorized(head));
      if (r.status === 403) throw new CanvasError("forbidden");
      if (r.status === 404) throw new CanvasError("not_found");
      if (r.status !== 200) throw new CanvasError(`http_${r.status}`);
      return readBounded(r, max);
    }
  }
}
