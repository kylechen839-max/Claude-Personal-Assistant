// Backend router for the phone app: Pi first, then PC, then the cloud.
//
// A standalone ES module (no DOM needed) so it runs in the browser and in
// node:test. Everything that touches the outside world is injected: fetch,
// the clock and timers. See FAILOVER.md for the protocol.
//
//   const router = new BackendRouter({backends, fetch, getToken});
//   await router.select();                      // on launch / resume / online
//   const r = await router.request('/api/state');
//   router.beginTurn(); ... router.endTurn();   // no switching mid-turn
//
// Selection: probe GET /healthz on every backend in parallel (1.5 s for
// tailnet hosts, 3 s for the cloud) and pick the highest-priority healthy
// one, waiting at most `graceMs` for a higher-priority backend that hasn't
// answered yet. A backend whose `api` version isn't supported is skipped
// and reported in status(). Mid-request failure (network error or 5xx):
// an idempotent request is retried once on the next healthy backend; a
// write (POST etc.) never is - it throws a NeedsUserRetry error so the UI
// can ask for a fresh tap. While the app is open it re-probes every 60 s
// and fails back to a higher-priority backend after it answered
// `failbackProbes` probes in a row, but never during a turn (a reply or a
// voice turn in progress): the switch waits for endTurn().

export const PRIORITY = ['pi', 'pc', 'cloud'];
export const LABELS = { pi: 'Pi', pc: 'PC', cloud: 'Cloud' };
export const SUPPORTED_API = { min: 1, max: 1 };
export const TIMEOUTS = { tailnet: 1500, cloud: 3000 };
const IDEMPOTENT = new Set(['GET', 'HEAD', 'OPTIONS']);

export class NeedsUserRetry extends Error {
  // A write failed and may or may not have run; it is not replayed.
  constructor(message, { backend, status } = {}) {
    super(message);
    this.name = 'NeedsUserRetry';
    this.backend = backend;
    this.status = status;
  }
}

export class NoBackend extends Error {
  constructor(message = 'No backend is reachable') {
    super(message);
    this.name = 'NoBackend';
  }
}

export function normalizeUrl(url) {
  // 'https://host[:port]' or null. http only for localhost.
  let u;
  try {
    u = new URL(String(url).trim());
  } catch {
    return null;
  }
  const local = ['localhost', '127.0.0.1', '[::1]'].includes(u.hostname);
  if (!(u.protocol === 'https:' || (u.protocol === 'http:' && local))) return null;
  if (u.username || u.password || u.search || u.hash) return null;
  if (u.pathname !== '/' && u.pathname !== '') return null;
  return u.origin;
}

export function mergeBackends(serverList = [], userSettings = {}, appOrigin = null) {
  // One {role, url} per role in priority order. The user's settings win
  // over the server-provided list; the cloud defaults to the app origin.
  const byRole = {};
  for (const b of serverList || []) {
    const url = b && normalizeUrl(b.url);
    if (url && PRIORITY.includes(b.role)) byRole[b.role] = url;
  }
  for (const role of PRIORITY) {
    const v = userSettings && userSettings[role];
    if (v === '') delete byRole[role]; // explicitly cleared
    else if (v && normalizeUrl(v)) byRole[role] = normalizeUrl(v);
  }
  if (!byRole.cloud && appOrigin) byRole.cloud = normalizeUrl(appOrigin);
  return PRIORITY.filter((r) => byRole[r]).map((role) => ({ role, url: byRole[role] }));
}

export function apiCompatible(api, range = SUPPORTED_API) {
  return Number.isInteger(api) && api >= range.min && api <= range.max;
}

export class BackendRouter {
  constructor({
    backends,
    fetch: fetchFn = globalThis.fetch,
    getToken = async () => null,
    timeouts = TIMEOUTS,
    apiRange = SUPPORTED_API,
    graceMs = 300,
    failbackProbes = 2,
    probeIntervalMs = 60000,
    now = () => Date.now(),
    setTimeout: setT = (...a) => globalThis.setTimeout(...a),
    clearTimeout: clearT = (id) => globalThis.clearTimeout(id),
    setInterval: setI = (...a) => globalThis.setInterval(...a),
    clearInterval: clearI = (id) => globalThis.clearInterval(id),
    onChange = () => {},
    override = 'auto',
  }) {
    // called unbound: window.fetch throws "Illegal invocation" as a method
    this.fetch = (...args) => fetchFn(...args);
    this.getToken = getToken;
    this.timeouts = timeouts;
    this.apiRange = apiRange;
    this.graceMs = graceMs;
    this.failbackProbes = failbackProbes;
    this.probeIntervalMs = probeIntervalMs;
    this.now = now;
    this.setT = setT;
    this.clearT = clearT;
    this.setI = setI;
    this.clearI = clearI;
    this.onChange = onChange;
    this.override = override;
    this.active = null; // role
    this.busy = 0; // turns in progress
    this.pendingSwitch = null;
    this.timer = null;
    this.health = {}; // role -> {healthy, api, compatible, reason, streak, checkedAt}
    this.setBackends(backends);
  }

  setBackends(backends) {
    this.backends = PRIORITY.map((role) => (backends || []).find((b) => b.role === role))
      .filter(Boolean)
      .map((b) => ({ role: b.role, url: normalizeUrl(b.url) }))
      .filter((b) => b.url);
    for (const b of this.backends) {
      if (!this.health[b.role]) this.health[b.role] = { healthy: false, streak: 0, reason: 'not checked' };
    }
    if (this.active && !this.backend(this.active)) this.active = null;
  }

  backend(role) {
    return this.backends.find((b) => b.role === role) || null;
  }

  timeoutFor(role) {
    return role === 'cloud' ? this.timeouts.cloud : this.timeouts.tailnet;
  }

  // --- probing ---------------------------------------------------------------

  async probe(b) {
    // -> {healthy, api, compatible, reason}. Never throws.
    const ctrl = typeof AbortController !== 'undefined' ? new AbortController() : null;
    let timer;
    const timeout = new Promise((resolve) => {
      timer = this.setT(() => {
        if (ctrl) ctrl.abort();
        resolve({ healthy: false, reason: 'timeout' });
      }, this.timeoutFor(b.role));
    });
    const attempt = (async () => {
      try {
        const r = await this.fetch(b.url + '/healthz', {
          method: 'GET',
          mode: 'cors',
          credentials: 'omit',
          cache: 'no-store',
          redirect: 'error',
          signal: ctrl ? ctrl.signal : undefined,
        });
        if (!r.ok) return { healthy: false, reason: `HTTP ${r.status}` };
        const body = await r.json();
        if (!body || body.ok !== true) return { healthy: false, reason: 'not ok' };
        if (!apiCompatible(body.api, this.apiRange)) {
          return {
            healthy: false, api: body.api, compatible: false,
            reason: `incompatible API version ${body.api} (app supports ${this.apiRange.min}-${this.apiRange.max}); update ${LABELS[b.role]}`,
          };
        }
        return { healthy: true, api: body.api, compatible: true, reason: '' };
      } catch (e) {
        return { healthy: false, reason: e && e.name === 'AbortError' ? 'timeout' : 'unreachable' };
      }
    })();
    const result = await Promise.race([attempt, timeout]);
    this.clearT(timer);
    return result;
  }

  record(role, result) {
    const prev = this.health[role] || { streak: 0 };
    this.health[role] = {
      ...result,
      compatible: result.compatible !== false,
      streak: result.healthy ? (prev.healthy ? prev.streak + 1 : 1) : 0,
      checkedAt: this.now(),
    };
  }

  probeAll({ onResult } = {}) {
    // Probe every backend at once; resolves when all have answered or
    // timed out. onResult(role, result) fires as each one settles.
    return Promise.all(this.backends.map(async (b) => {
      const result = await this.probe(b);
      this.record(b.role, result);
      if (onResult) onResult(b.role, result);
      return [b.role, result];
    })).then(Object.fromEntries);
  }

  forced() {
    return this.override !== 'auto' && this.backend(this.override) ? this.override : null;
  }

  best() {
    // Highest-priority healthy backend from the latest results.
    if (this.forced()) return this.forced();
    const b = this.backends.find((x) => this.health[x.role] && this.health[x.role].healthy);
    return b ? b.role : null;
  }

  async select() {
    // Launch, resume, network change. Returns the chosen role (or null).
    const forced = this.forced();
    if (forced) {
      this.probeAll(); // keep the badges current, don't wait
      this.switchTo(forced, 'override');
      return forced;
    }
    const settled = {};
    const choice = await new Promise((resolve) => {
      let graceTimer = null;
      let done = false;
      const finish = (role) => {
        if (done) return;
        done = true;
        if (graceTimer !== null) this.clearT(graceTimer);
        resolve(role);
      };
      const decide = (final) => {
        // the first healthy backend whose betters have all failed
        for (const b of this.backends) {
          const r = settled[b.role];
          if (!r) {
            if (final) continue;
            return null; // a higher one is still answering
          }
          if (r.healthy) return b.role;
        }
        return final ? null : undefined;
      };
      this.probeAll({
        onResult: (role, result) => {
          settled[role] = result;
          const role2 = decide(false);
          if (role2) return finish(role2);
          if (role2 === undefined) return finish(null); // everything failed
          // someone healthy, but a higher-priority backend hasn't answered:
          // give it a moment, then take the best we have
          if (result.healthy && graceTimer === null) {
            graceTimer = this.setT(() => finish(decide(true)), this.graceMs);
          }
        },
      }).then(() => finish(decide(true)));
    });
    if (choice) this.switchTo(choice, 'select');
    else if (!this.busy) this.switchTo(null, 'offline');
    return choice;
  }

  switchTo(role, why, { force = false } = {}) {
    if (role === this.active) {
      this.pendingSwitch = null;
      return;
    }
    if (this.busy && !force) {
      this.pendingSwitch = role; // applied by endTurn()
      return;
    }
    const from = this.active;
    this.active = role;
    this.pendingSwitch = null;
    this.onChange({ from, to: role, why, status: this.status() });
  }

  // --- turns: no switching while a reply or voice turn is in progress -------

  beginTurn() {
    this.busy += 1;
    return this.active;
  }

  endTurn() {
    this.busy = Math.max(0, this.busy - 1);
    if (!this.busy && this.pendingSwitch !== null && this.pendingSwitch !== this.active) {
      const role = this.pendingSwitch;
      this.pendingSwitch = null;
      this.switchTo(role, 'after turn');
    }
  }

  // --- background re-probe and fail-back ------------------------------------

  async tick() {
    await this.probeAll();
    if (this.forced()) return this.switchTo(this.forced(), 'override');
    const best = this.best();
    if (!best) return; // keep the current one; the next request will tell
    const cur = this.active;
    const curHealthy = cur && this.health[cur] && this.health[cur].healthy;
    if (!curHealthy) return this.switchTo(best, 'failover');
    if (PRIORITY.indexOf(best) < PRIORITY.indexOf(cur)) {
      // fail back only after it has stayed healthy for a few probes
      if (this.health[best].streak >= this.failbackProbes) this.switchTo(best, 'failback');
    }
  }

  start() {
    this.stop();
    this.timer = this.setI(() => { this.tick(); }, this.probeIntervalMs);
  }

  stop() {
    if (this.timer !== null) this.clearI(this.timer);
    this.timer = null;
  }

  setOverride(value) {
    this.override = value === 'auto' || PRIORITY.includes(value) ? value : 'auto';
    if (this.forced()) this.switchTo(this.forced(), 'override');
    else {
      const best = this.best();
      if (best) this.switchTo(best, 'auto');
    }
  }

  // --- requests ----------------------------------------------------------------

  async request(path, opts = {}) {
    // fetch(path) on the active backend, with one failover retry for
    // idempotent requests. Returns the Response (non-5xx).
    const method = (opts.method || 'GET').toUpperCase();
    const idempotent = opts.idempotent !== undefined ? opts.idempotent : IDEMPOTENT.has(method);
    if (!this.active) await this.select();
    if (!this.active) throw new NoBackend();
    const first = this.active;
    try {
      return await this.send(first, path, method, opts);
    } catch (err) {
      if (!(err instanceof RetryableFailure)) throw err;
      this.record(first, { healthy: false, reason: err.message });
      const next = this.forced() ? null : this.nextAfter(first);
      // the active backend is gone: this switch can't wait for the turn to end
      this.switchTo(next, 'request failed', { force: true });
      if (!idempotent) {
        throw new NeedsUserRetry(`${LABELS[first]} didn't answer; tap to try again`,
          { backend: first, status: err.status });
      }
      if (!next) throw new NoBackend();
      try {
        return await this.send(next, path, method, opts);
      } catch (err2) {
        if (err2 instanceof RetryableFailure) {
          this.record(next, { healthy: false, reason: err2.message });
          throw new NoBackend();
        }
        throw err2;
      }
    }
  }

  nextAfter(role) {
    // The next backend after `role` in priority order that isn't known to
    // be down, else a higher-priority one that is healthy, else null.
    const i = this.backends.findIndex((b) => b.role === role);
    const usable = (b) => {
      const h = this.health[b.role];
      return !h || h.healthy || h.reason === 'not checked';
    };
    const after = this.backends.slice(i + 1).find(usable);
    if (after) return after.role;
    const before = this.backends.slice(0, i).find((b) => this.health[b.role]?.healthy);
    return before ? before.role : null;
  }

  async send(role, path, method, opts) {
    const b = this.backend(role);
    const headers = { ...(opts.headers || {}) };
    const token = await this.getToken();
    if (token) headers.Authorization = `Bearer ${token}`;
    let r;
    try {
      r = await this.fetch(b.url + path, {
        method,
        headers,
        body: opts.body,
        mode: 'cors',
        credentials: 'omit',
        redirect: 'error',
        signal: opts.signal,
      });
    } catch (e) {
      if (e && e.name === 'AbortError' && opts.signal && opts.signal.aborted) throw e; // the user cancelled
      throw new RetryableFailure('network error');
    }
    if (r.status >= 500) throw new RetryableFailure(`HTTP ${r.status}`, r.status);
    return r;
  }

  url(role = this.active) {
    const b = this.backend(role);
    return b ? b.url : null;
  }

  wsUrl(path, role = this.active) {
    const u = this.url(role);
    return u ? u.replace(/^http/, 'ws') + path : null;
  }

  status() {
    return {
      active: this.active,
      badge: this.active ? LABELS[this.active] : 'Offline',
      override: this.override,
      busy: this.busy > 0,
      backends: this.backends.map((b) => ({
        role: b.role,
        label: LABELS[b.role],
        url: b.url,
        healthy: !!(this.health[b.role] && this.health[b.role].healthy),
        compatible: !this.health[b.role] || this.health[b.role].compatible !== false,
        reason: (this.health[b.role] && this.health[b.role].reason) || '',
      })),
    };
  }
}

class RetryableFailure extends Error {
  constructor(message, status) {
    super(message);
    this.name = 'RetryableFailure';
    this.status = status;
  }
}

// --- token storage: IndexedDB, with a memory fallback (tests, private mode) --

// The database keeps its pre-rebrand name (like the hc_session cookie): a
// new name would drop every phone's failover token and sign it out.
export const TOKEN_DB = 'hey-claude';

export function tokenStore(idb = globalThis.indexedDB) {
  const memory = new Map();
  if (!idb) {
    return {
      get: async (k) => memory.get(k) ?? null,
      set: async (k, v) => { memory.set(k, v); },
      delete: async (k) => { memory.delete(k); },
    };
  }
  const open = () => new Promise((resolve, reject) => {
    const req = idb.open(TOKEN_DB, 1);
    req.onupgradeneeded = () => req.result.createObjectStore('kv');
    req.onsuccess = () => resolve(req.result);
    req.onerror = () => reject(req.error);
  });
  const run = async (mode, fn) => {
    const db = await open();
    return new Promise((resolve, reject) => {
      const tx = db.transaction('kv', mode);
      const req = fn(tx.objectStore('kv'));
      tx.oncomplete = () => { db.close(); resolve(req && req.result); };
      tx.onerror = () => { db.close(); reject(tx.error); };
    });
  };
  return {
    get: async (k) => {
      try { return (await run('readonly', (s) => s.get(k))) ?? null; } catch { return memory.get(k) ?? null; }
    },
    set: async (k, v) => {
      memory.set(k, v);
      try { await run('readwrite', (s) => s.put(v, k)); } catch { /* memory only */ }
    },
    delete: async (k) => {
      memory.delete(k);
      try { await run('readwrite', (s) => s.delete(k)); } catch { /* ignore */ }
    },
  };
}
