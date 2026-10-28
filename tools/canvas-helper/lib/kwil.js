// Talking to Kwil: only the origin the helper was paired on (https), only
// its two push routes, the sync key as a Bearer header (never in a URL),
// no cookies (`credentials: "omit"`: the push routes never read one, and
// no Canvas cookie can ride along), no redirects followed (a redirect
// could carry the key elsewhere). See kwil/canvas_helper.py.

export const PAIR_PATH = "/api/student/canvas/pair";
export const MANIFEST_PATH = "/api/student/canvas/push/manifest";
export const FILE_PATH = "/api/student/canvas/push/file";
export const KEY = /^kcs1\.[A-Za-z0-9_-]{43}$/;
export const CODE = /^kcp1\.[A-Za-z0-9_-]{32}$/;

export class KwilError extends Error {
  // codes: unpaired (401), off (404), busy (409), not_wanted (409 on a
  // file), rate_limited (429), too_large (413), rejected (400), network,
  // http_<status>
  constructor(code, retryAfter = 0) { super(code); this.code = code; this.retryAfter = retryAfter; }
}

// The Kwil origin a pairing pins: https, default port, a dotted name, no
// user:password@, else null.
export function kwilOrigin(text) {
  let u;
  try { u = new URL(String(text)); } catch (e) { return null; }
  if (u.protocol !== "https:" || u.username || u.password || u.port) return null;
  if (!u.hostname.includes(".")) return null;
  return u.origin;
}

export class KwilClient {
  constructor({ origin, key, fetch }) {
    this.origin = kwilOrigin(origin);
    if (!this.origin || !KEY.test(String(key))) throw new KwilError("unpaired");
    this.key = key;
    this.fetch = fetch;
  }

  async _post(path, body, headers) {
    let r;
    try {
      r = await this.fetch(this.origin + path, {
        method: "POST", credentials: "omit", redirect: "error", cache: "no-store",
        headers: { Authorization: `Bearer ${this.key}`, ...headers }, body });
    } catch (e) {
      throw new KwilError("network");
    }
    let answer = {};
    try { answer = await r.json(); } catch (e) { answer = {}; }
    if (r.status === 200) return answer || {};
    const retry = Number(r.headers && r.headers.get ? r.headers.get("retry-after") : 0) || 0;
    if (r.status === 401) throw new KwilError("unpaired");
    if (r.status === 404) throw new KwilError("off");
    if (r.status === 409) {
      throw new KwilError(path === FILE_PATH && /not asked/.test(String(answer.error || ""))
        ? "not_wanted" : "busy");
    }
    if (r.status === 429) throw new KwilError("rate_limited", retry || 600);
    if (r.status === 413) throw new KwilError("too_large");
    if (r.status === 400) throw new KwilError("rejected");
    throw new KwilError(`http_${r.status}`);
  }

  // {counts, want: [{course_id, file_id}], max_file_bytes}
  pushManifest(manifest) {
    return this._post(MANIFEST_PATH, JSON.stringify(manifest),
                      { "Content-Type": "application/json" });
  }

  // {result, note}: one file a manifest asked for, multipart
  // (course_id, file_id, file).
  pushFile(courseId, fileId, bytes) {
    const form = new FormData();
    form.append("course_id", String(courseId));
    form.append("file_id", String(fileId));
    form.append("file", new Blob([bytes], { type: "application/octet-stream" }), "file");
    return this._post(FILE_PATH, form, {});
  }
}
