// Fakes for the Kwil Canvas helper's tests (tests/js/canvas-helper*.test.mjs
// and the Python round trip, tests/js/canvas-helper-roundtrip.mjs): a
// school's Canvas answering with its own API shapes (pagination by Link
// header, the `while(1);` prefix on session answers, a file download that
// redirects to the file store), a Kwil recording what it was sent, and a
// fake `chrome`. No network: every request lands here.

export const CANVAS = 'https://school.instructure.com';
export const KWIL = 'https://heykwil.com';
export const FILESTORE = 'https://files-iad.inscloudgate.net';
export const KEY = 'kcs1.' + 'K'.repeat(43);
export const CODE = 'kcp1.' + 'C'.repeat(32);
export const VERIFIER = 'Vf'.repeat(20);
export const PDF_BYTES = new TextEncoder().encode('%PDF-1.4 late policy: ten percent a day');

export function canvasFile(id, name, ctype, mime, size, extra = {}) {
  // GET /api/v1/courses/:id/files, one File object (Canvas's fields)
  return { id, uuid: `uuid${id}`, folder_id: 7, display_name: name,
           filename: name.replace(/ /g, '+'), upload_status: 'success', 'content-type': ctype,
           url: `${CANVAS}/files/${id}/download?download_frd=1&verifier=${VERIFIER}`,
           size, created_at: '2026-09-01T12:00:00Z', updated_at: '2026-10-01T12:00:00Z',
           unlock_at: null, locked: false, hidden: false, lock_at: null,
           hidden_for_user: false, thumbnail_url: null, modified_at: '2026-10-01T12:00:00Z',
           mime_class: mime, media_entry_id: null, category: 'uncategorized',
           locked_for_user: false, ...extra };
}

export function school() {
  // One student's Canvas: course 111 (everything), course 222 (hides Files).
  return {
    courses: [
      { id: 111, name: 'MATH 241 - Fall 2026', course_code: 'MATH 241', workflow_state: 'available',
        enrollment_term_id: 1, uuid: 'c111', start_at: null, end_at: null,
        enrollments: [{ type: 'student', role: 'StudentEnrollment', enrollment_state: 'active',
                        computed_current_score: 91.5, computed_current_grade: 'A-' }],
        syllabus_body: '<h2>MATH 241</h2><p>Grading: homework 30 points, exams 70.</p>' },
      { id: 222, name: 'PHYS 211', course_code: 'PHYS 211', workflow_state: 'available',
        syllabus_body: null },
      { id: 333, access_restricted_by_date: true },
    ],
    pages: {
      111: [{ page_id: 7, url: 'week-1', title: 'Week 1', created_at: '2026-09-01T12:00:00Z',
              updated_at: '2026-10-02T12:00:00Z', published: true, front_page: false,
              locked_for_user: false, editing_roles: 'teachers',
              body: '<p>Office hours in Altgeld 141.</p>' },
            { page_id: 8, url: 'week-2', title: 'Week 2', updated_at: '2026-10-03T12:00:00Z',
              published: true, locked_for_user: false }],   // no body in the listing
      222: [],
    },
    page: { '111/week-2': { page_id: 8, url: 'week-2', title: 'Week 2',
                            body: '<p>Lab in Loomis 276.</p>' } },
    assignments: {
      111: [{ id: 9, name: 'Problem Set 5', due_at: '2026-11-04T04:59:00Z',
              updated_at: '2026-10-03T12:00:00Z', description: '<p>Show your work.</p>',
              points_possible: 10, grading_type: 'points', has_submitted_submissions: true,
              submission: { score: 9.5, grade: '9.5', workflow_state: 'graded',
                            body: 'my answers' } }],
      222: [{ id: 19, name: 'Lab 1', due_at: null, updated_at: '2026-10-01T00:00:00Z',
              description: null }],
    },
    modules: {
      111: [{ id: 5, name: 'Week 1', position: 1, items_count: 1,
              items: [{ id: 51, title: 'Week 1', type: 'Page', html_url: `${CANVAS}/x`,
                        completion_requirement: { type: 'must_view', completed: true } }] }],
      222: [],
    },
    files: {
      111: [canvasFile(501, 'Syllabus.pdf', 'application/pdf', 'pdf', PDF_BYTES.length),
            canvasFile(502, 'Campus map.png', 'image/png', 'image', 2048)],
      // 222: hidden Files tab -> 401 {"status": "unauthorized"}
    },
    announcements: [
      { id: 31, title: 'Midterm room', context_code: 'course_111',
        message: '<p>The midterm is in Foellinger Auditorium.</p>',
        posted_at: '2026-10-20T15:00:00Z', author: { display_name: 'Prof. X' },
        user_name: 'Prof. X' },
    ],
    bytes: { 501: PDF_BYTES, 502: new Uint8Array(2048) },
  };
}

// GET /profile/settings as Canvas renders it for a signed-in person: the
// page's ENV (ProfileController#settings js_env) holds PERMISSIONS
// {can_update_tokens} and user_is_only_student among many other keys.
export function settingsPage({ canUpdate = false, onlyStudent = true, missing = false } = {}) {
  const env = { ASSET_HOST: 'https://du11hjcvx0uqb.cloudfront.net', current_user_id: '4242',
                current_user_roles: ['user', 'student'], DOMAIN_ROOT_ACCOUNT_ID: '1',
                PERMISSIONS: { can_update_name: false, can_update_tokens: canUpdate,
                               can_add_pronouns: false },
                user_is_only_student: onlyStudent, LOCALE: 'en' };
  if (missing) delete env.PERMISSIONS.can_update_tokens;
  return '<!DOCTYPE html><html><head><title>User Settings</title>\n<script>\n//<![CDATA[\n' +
    `window.ENV = ${JSON.stringify(env)};\n//]]>\n</script></head>` +
    '<body><h2>Approved Integrations</h2></body></html>';
}

function response(body, { status = 200, headers = {}, url, redirected = false } = {}) {
  const r = new Response(body, { status, headers });
  if (url) Object.defineProperty(r, 'url', { value: url });
  if (redirected) Object.defineProperty(r, 'redirected', { value: true });
  return r;
}

const json = (value, init = {}) => response('while(1);' + JSON.stringify(value),
  { ...init, headers: { 'content-type': 'application/json; charset=utf-8', ...init.headers } });

// A fetch that answers as Canvas, Kwil and the file store. `calls` holds
// every request. Options: `limit` (Canvas answers rate-limited this many
// times first), `signedOut`, `kwil` (route -> answer function).
export function world({ data = school(), limitTimes = 0, signedOut = false, kwil = {},
                        perPage } = {}) {
  const calls = [];
  let limited = 0;
  const pushed = { manifests: [], files: [] };
  const answerList = (u, items) => {
    const per = perPage || Number(u.searchParams.get('per_page')) || 10;
    const page = Number(u.searchParams.get('page') || 1);
    const slice = items.slice((page - 1) * per, page * per);
    const headers = {};
    if (page * per < items.length) {
      const next = new URL(u);
      next.searchParams.set('page', String(page + 1));
      headers.link = `<${next.href}>; rel="current",<${next.href}>; rel="next"`;
    }
    return json(slice, { headers });
  };
  async function fetch(url, opts = {}) {
    calls.push({ url: String(url), opts });
    const u = new URL(url);
    if (u.origin === CANVAS) {
      if (signedOut) {
        return response(JSON.stringify({ status: 'unauthenticated',
          errors: [{ message: 'user authorization required' }] }), { status: 401 });
      }
      if (limited < limitTimes) {
        limited += 1;
        return response('403 Forbidden (Rate Limit Exceeded)',
                        { status: 403, headers: { 'x-rate-limit-remaining': '0' } });
      }
      const p = u.pathname;
      let m;
      if (p === '/api/v1/courses') return answerList(u, data.courses);
      if ((m = p.match(/^\/api\/v1\/courses\/(\d+)\/pages\/([\w.-]+)$/))) {
        const page = data.page[`${m[1]}/${m[2]}`];
        return page ? json(page) : response('{}', { status: 404 });
      }
      if ((m = p.match(/^\/api\/v1\/courses\/(\d+)\/(pages|assignments|modules|files)$/))) {
        const items = (data[m[2]] || {})[m[1]];
        if (!items) {
          return response(JSON.stringify({ status: 'unauthorized',
            errors: [{ message: 'user not authorized to perform that action' }] }),
          { status: 401 });
        }
        return answerList(u, items);
      }
      if (p === '/profile/settings' && data.settings) {   // an HTML page, with ENV in it
        return response(data.settings, { headers: { 'content-type': 'text/html; charset=utf-8' } });
      }
      if (p === '/api/v1/announcements') {
        const codes = u.searchParams.getAll('context_codes[]');
        return answerList(u, data.announcements.filter((n) => codes.includes(n.context_code)));
      }
      if ((m = p.match(/^\/files\/(\d+)\/download$/))) {
        // Canvas sends the bytes on to its file store (the fetch follows)
        if ((data.unreachable || []).includes(Number(m[1]))) {
          throw new TypeError('Failed to fetch');   // a file store the browser can't read
        }
        const bytes = data.bytes[m[1]];
        if (!bytes || u.searchParams.get('verifier') !== VERIFIER) {
          return response('', { status: 404 });
        }
        return response(bytes, { url: `${FILESTORE}/${m[1]}?token=jwt`, redirected: true });
      }
      return response('{}', { status: 404 });
    }
    if (u.origin === KWIL) {
      const route = kwil[u.pathname];
      if (u.pathname === '/api/student/canvas/push/manifest') {
        pushed.manifests.push(JSON.parse(opts.body));
      } else if (u.pathname === '/api/student/canvas/push/file') {
        pushed.files.push({ course_id: opts.body.get('course_id'),
                            file_id: opts.body.get('file_id'),
                            bytes: new Uint8Array(await opts.body.get('file').arrayBuffer()) });
      }
      const out = route ? await route(opts, pushed) : { status: 404, body: { error: 'not found' } };
      return response(JSON.stringify(out.body || {}),
                      { status: out.status || 200, headers: out.headers || {} });
    }
    throw new TypeError('Failed to fetch');   // anywhere else: no network here
  }
  return { fetch, calls, pushed };
}

// Kwil's answers as kwil/canvas_helper.py gives them.
export function kwilRoutes({ want = [{ course_id: 111, file_id: 501 }], manifestStatus = 200,
                             fileStatus = 200 } = {}) {
  return {
    '/api/student/canvas/push/manifest': () => manifestStatus === 200
      ? { body: { counts: { added: 6, updated: 0, unchanged: 0, skipped: 0, removed: 0,
                            files: 0, more: 0, courses: 2 },
                  max_file_bytes: 3_934_464, want } }
      : { status: manifestStatus, body: { error: 'x' },
          headers: manifestStatus === 429 ? { 'retry-after': '600' } : {} },
    '/api/student/canvas/push/file': () => fileStatus === 200
      ? { body: { result: 'added', note: null } }
      : { status: fileStatus, body: { error: 'not asked for; push the manifest first' } },
  };
}

// chrome.* as the service worker and popup see it.
export function fakeChrome({ granted = true } = {}) {
  const store = {};
  const alarms = {};
  const scripts = [];
  const injected = [];
  const tabMessages = [];
  const keepalive = [];
  const chrome = {
    runtime: { id: 'abcdefghijklmnopabcdefghijklmnop',
               getURL: (p) => `chrome-extension://abcdefghijklmnopabcdefghijklmnop/${p}`,
               async getPlatformInfo() { keepalive.push(Date.now()); return { os: 'linux' }; } },
    tabs: { async sendMessage(tabId, msg) { tabMessages.push({ tabId, msg }); } },
    storage: { local: {
      async get(keys) {
        const out = {};
        for (const k of [].concat(keys)) if (k in store) out[k] = structuredClone(store[k]);
        return out;
      },
      async set(obj) { for (const [k, v] of Object.entries(obj)) store[k] = structuredClone(v); },
      async remove(keys) { for (const k of [].concat(keys)) delete store[k]; },
    } },
    alarms: {
      async create(name, info) { alarms[name] = info; },
      async get(name) { return alarms[name] ? { name, ...alarms[name] } : undefined; },
      async clear(name) { delete alarms[name]; return true; },
    },
    permissions: {
      granted: new Set(),
      async contains({ origins }) {
        return granted === true || origins.every((o) => chrome.permissions.granted.has(o));
      },
    },
    scripting: {
      async getRegisteredContentScripts({ ids }) { return scripts.filter((s) => ids.includes(s.id)); },
      async unregisterContentScripts({ ids }) {
        for (const id of ids) { const i = scripts.findIndex((s) => s.id === id); if (i >= 0) scripts.splice(i, 1); }
      },
      async registerContentScripts(list) { scripts.push(...structuredClone(list)); },
      async executeScript(opts) { injected.push(opts); return []; },
    },
  };
  return { chrome, store, alarms, scripts, injected, tabMessages, keepalive };
}

export const tabSender = (origin, id = 'abcdefghijklmnopabcdefghijklmnop') =>
  ({ id, origin, url: `${origin}/`, tab: { id: 7 } });
export const popupSender = () => ({ id: 'abcdefghijklmnopabcdefghijklmnop',
  url: 'chrome-extension://abcdefghijklmnopabcdefghijklmnop/popup.html' });
