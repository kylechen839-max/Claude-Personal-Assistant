// node --test tests/js/  (Node 18+; no packages needed)
//
// Three fake backends (Pi, PC, cloud) on a virtual clock: each can be up,
// down (connection refused), hanging (no answer until the timeout), slow,
// failing with 5xx, or speaking another API version.

import test from 'node:test';
import assert from 'node:assert/strict';

import {
  BackendRouter, NeedsUserRetry, NoBackend, mergeBackends, normalizeUrl, tokenStore,
} from '../../assistant/web/backend-router.js';

const URLS = {
  pi: 'https://pi.tail1234.ts.net',
  pc: 'https://pc.tail1234.ts.net',
  cloud: 'https://hey-claude.onrender.com',
};
const BACKENDS = Object.entries(URLS).map(([role, url]) => ({ role, url }));

class VirtualClock {
  constructor() {
    this.t = 0;
    this.timers = new Map();
    this.nextId = 1;
  }
  now = () => this.t;
  setTimeout = (fn, ms) => {
    const id = this.nextId++;
    this.timers.set(id, { at: this.t + ms, fn, every: null });
    return id;
  };
  clearTimeout = (id) => { this.timers.delete(id); };
  setInterval = (fn, ms) => {
    const id = this.nextId++;
    this.timers.set(id, { at: this.t + ms, fn, every: ms });
    return id;
  };
  clearInterval = (id) => { this.timers.delete(id); };
  async flush() {
    for (let i = 0; i < 20; i++) await Promise.resolve();
  }
  async advance(ms) {
    const end = this.t + ms;
    await this.flush();
    for (;;) {
      let due = null;
      for (const [id, tm] of this.timers) {
        if (tm.at <= end && (!due || tm.at < due[1].at)) due = [id, tm];
      }
      if (!due) break;
      const [id, tm] = due;
      this.t = tm.at;
      if (tm.every) tm.at += tm.every;
      else this.timers.delete(id);
      tm.fn();
      await this.flush();
    }
    this.t = end;
    await this.flush();
  }
}

class FakeNet {
  // state per role: 'up' | 'down' | 'hang' | '5xx' | {api} ; latency per role
  constructor(clock) {
    this.clock = clock;
    this.state = { pi: 'up', pc: 'up', cloud: 'up' };
    this.latency = { pi: 20, pc: 30, cloud: 200 };
    this.api = { pi: 1, pc: 1, cloud: 1 };
    this.log = [];
  }
  roleOf(url) {
    return Object.keys(URLS).find((r) => url.startsWith(URLS[r]));
  }
  fetch = (url, opts = {}) => {
    const role = this.roleOf(url);
    const path = url.slice(URLS[role].length);
    this.log.push({ role, path, method: opts.method || 'GET', auth: opts.headers?.Authorization, opts });
    const state = this.state[role];
    return new Promise((resolve, reject) => {
      if (opts.signal) {
        opts.signal.addEventListener('abort', () => {
          const e = new Error('aborted');
          e.name = 'AbortError';
          reject(e);
        });
      }
      if (state === 'hang') return; // never answers
      this.clock.setTimeout(() => {
        if (state === 'down') return reject(new TypeError('Failed to fetch'));
        if (state === '5xx') return resolve(response(503, { error: 'down' }));
        if (path === '/healthz') return resolve(response(200, { ok: true, api: this.api[role], role }));
        resolve(response(200, { role, path }));
      }, this.latency[role]);
    });
  };
  calls(path) {
    return this.log.filter((c) => c.path === path).map((c) => c.role);
  }
}

function response(status, body) {
  return { status, ok: status >= 200 && status < 300, json: async () => body };
}

function setup(opts = {}) {
  const clock = new VirtualClock();
  const net = new FakeNet(clock);
  const changes = [];
  const router = new BackendRouter({
    backends: BACKENDS,
    fetch: net.fetch,
    getToken: async () => 'hc1.app.sid.9999999999.1.sig',
    now: clock.now,
    setTimeout: clock.setTimeout,
    clearTimeout: clock.clearTimeout,
    setInterval: clock.setInterval,
    clearInterval: clock.clearInterval,
    onChange: (c) => changes.push(c),
    ...opts,
  });
  return { clock, net, router, changes };
}

async function run(clock, promise, ms = 10000) {
  let done = false;
  let value;
  let error;
  promise.then((v) => { done = true; value = v; }, (e) => { done = true; error = e; });
  for (let waited = 0; !done && waited < ms; waited += 50) await clock.advance(50);
  if (!done) throw new Error('promise never settled');
  if (error) throw error;
  return value;
}

// --- selection ------------------------------------------------------------------

test('all up: the Pi is chosen', async () => {
  const { clock, router } = setup();
  assert.equal(await run(clock, router.select()), 'pi');
  assert.equal(router.status().badge, 'Pi');
});

test('Pi down -> PC', async () => {
  const { clock, net, router } = setup();
  net.state.pi = 'down';
  assert.equal(await run(clock, router.select()), 'pc');
  assert.equal(router.status().badge, 'PC');
});

test('Pi and PC down -> cloud', async () => {
  const { clock, net, router } = setup();
  net.state.pi = 'down';
  net.state.pc = 'hang';
  assert.equal(await run(clock, router.select()), 'cloud');
});

test('everything down -> offline', async () => {
  const { clock, net, router } = setup();
  net.state = { pi: 'down', pc: 'hang', cloud: '5xx' };
  assert.equal(await run(clock, router.select()), null);
  assert.equal(router.status().badge, 'Offline');
});

test('probes run in parallel with tailnet 1.5 s and cloud 3 s timeouts', async () => {
  const { clock, net, router } = setup();
  net.state = { pi: 'hang', pc: 'hang', cloud: 'hang' };
  const started = clock.now();
  await run(clock, router.select());
  // all three started together; done at the cloud's 3 s timeout, not 1.5+1.5+3
  assert.deepEqual(net.calls('/healthz').sort(), ['cloud', 'pc', 'pi']);
  assert.ok(clock.now() - started <= 3050, `took ${clock.now() - started} ms`);
  assert.equal(router.health.pi.reason, 'timeout');
});

test('probes send no credentials and no token', async () => {
  const { clock, net, router } = setup();
  await run(clock, router.select());
  for (const c of net.log.filter((x) => x.path === '/healthz')) {
    assert.equal(c.opts.credentials, 'omit');
    assert.equal(c.auth, undefined);
  }
});

test('a hanging Pi does not hold up the PC for the full timeout', async () => {
  const { clock, net, router } = setup();
  net.state.pi = 'hang';
  const p = router.select();
  const role = await run(clock, p);
  assert.equal(role, 'pc');
  // PC answered at 30 ms; waited at most graceMs (300) more for the Pi
  assert.ok(clock.now() <= 400, `took ${clock.now()} ms`);
});

test('a slower Pi still wins if it answers within the grace window', async () => {
  const { clock, net, router } = setup();
  net.latency.pi = 250; // PC answers at 30 ms, Pi at 250 ms (< 30 + 300)
  assert.equal(await run(clock, router.select()), 'pi');
});

test('the cloud answering first does not beat a Pi that is about to answer', async () => {
  const { clock, net, router } = setup();
  net.latency = { pi: 150, pc: 1600, cloud: 10 };
  net.state.pc = 'hang';
  assert.equal(await run(clock, router.select()), 'pi');
});

test('incompatible API version is skipped and explained', async () => {
  const { clock, net, router } = setup();
  net.api.pi = 2;
  assert.equal(await run(clock, router.select()), 'pc');
  const pi = router.status().backends.find((b) => b.role === 'pi');
  assert.equal(pi.compatible, false);
  assert.match(pi.reason, /incompatible API version 2/);
});

// --- requests and mid-request failover -----------------------------------------

test('requests carry the bearer token, never cookies', async () => {
  const { clock, net, router } = setup();
  await run(clock, router.select());
  const r = await run(clock, router.request('/api/state'));
  assert.deepEqual(await r.json(), { role: 'pi', path: '/api/state' });
  const call = net.log.find((c) => c.path === '/api/state');
  assert.equal(call.auth, 'Bearer hc1.app.sid.9999999999.1.sig');
  assert.equal(call.opts.credentials, 'omit');
});

test('GET that fails mid-request is retried once on the next backend', async () => {
  const { clock, net, router } = setup();
  await run(clock, router.select());
  net.state.pi = 'down'; // the Pi is unplugged
  const r = await run(clock, router.request('/api/history'));
  assert.equal((await r.json()).role, 'pc');
  assert.deepEqual(net.calls('/api/history'), ['pi', 'pc']);
  assert.equal(router.active, 'pc');
});

test('5xx counts as a failure for idempotent requests', async () => {
  const { clock, net, router } = setup();
  await run(clock, router.select());
  net.state.pi = '5xx';
  const r = await run(clock, router.request('/api/state'));
  assert.equal((await r.json()).role, 'pc');
});

test('retry happens only once', async () => {
  const { clock, net, router } = setup();
  await run(clock, router.select());
  net.state.pi = 'down';
  net.state.pc = 'down';
  await assert.rejects(run(clock, router.request('/api/state')), NoBackend);
  assert.deepEqual(net.calls('/api/state'), ['pi', 'pc']);
});

test('a write is never replayed automatically', async () => {
  const { clock, net, router } = setup();
  await run(clock, router.select());
  net.state.pi = 'down';
  await assert.rejects(
    run(clock, router.request('/api/turn', { method: 'POST', body: '{}' })),
    (e) => e instanceof NeedsUserRetry && e.backend === 'pi',
  );
  assert.deepEqual(net.calls('/api/turn'), ['pi']); // not sent to the PC
  assert.equal(router.active, 'pc'); // the fresh tap goes to the PC
  const r = await run(clock, router.request('/api/turn', { method: 'POST', body: '{}' }));
  assert.equal((await r.json()).role, 'pc');
});

test('a tool action that returns 5xx is not replayed either', async () => {
  const { clock, net, router } = setup();
  await run(clock, router.select());
  net.state.pi = '5xx';
  await assert.rejects(run(clock, router.request('/api/confirm', { method: 'POST' })), NeedsUserRetry);
  assert.deepEqual(net.calls('/api/confirm'), ['pi']);
});

test('a POST marked idempotent may be retried', async () => {
  const { clock, net, router } = setup();
  await run(clock, router.select());
  net.state.pi = 'down';
  const r = await run(clock, router.request('/api/history/query', { method: 'POST', idempotent: true }));
  assert.equal((await r.json()).role, 'pc');
});

test('4xx is returned, not failed over', async () => {
  const { clock, net, router } = setup();
  await run(clock, router.select());
  net.fetch = net.fetch.bind(net);
  router.fetch = async (url, opts) => (url.endsWith('/api/state') ? response(401, {}) : net.fetch(url, opts));
  const r = await run(clock, router.request('/api/state'));
  assert.equal(r.status, 401);
  assert.equal(router.active, 'pi');
});

// --- background re-probe, fail-back hysteresis, no switch mid-turn -------------

test('Pi recovers -> fail back after hysteresis, not on the first probe', async () => {
  const { clock, net, router, changes } = setup();
  net.state.pi = 'down';
  await run(clock, router.select());
  assert.equal(router.active, 'pc');
  router.start();
  net.state.pi = 'up';
  await clock.advance(60000 + 500); // first probe sees the Pi up
  assert.equal(router.active, 'pc', 'one good probe is not enough');
  await clock.advance(60000); // second one in a row
  assert.equal(router.active, 'pi');
  assert.equal(changes.at(-1).why, 'failback');
  router.stop();
});

test('a flapping Pi does not cause switching back and forth', async () => {
  const { clock, net, router } = setup();
  net.state.pi = 'down';
  await run(clock, router.select());
  router.start();
  for (let i = 0; i < 6; i++) {
    net.state.pi = i % 2 ? 'down' : 'up';
    await clock.advance(60000);
    assert.equal(router.active, 'pc', `probe ${i}`);
  }
  router.stop();
});

test('no fail-back mid-turn: the switch waits for the turn to end', async () => {
  const { clock, net, router } = setup();
  net.state.pi = 'down';
  await run(clock, router.select());
  router.start();
  router.beginTurn(); // a voice turn / streaming reply
  net.state.pi = 'up';
  await clock.advance(3 * 60000);
  assert.equal(router.active, 'pc');
  const r = await run(clock, router.request('/api/events'));
  assert.equal((await r.json()).role, 'pc', 'the rest of the turn stays on the PC');
  router.endTurn();
  assert.equal(router.active, 'pi');
  router.stop();
});

test('select() during a turn does not switch either', async () => {
  const { clock, net, router } = setup();
  net.state.pi = 'down';
  await run(clock, router.select());
  router.beginTurn();
  net.state.pi = 'up';
  await run(clock, router.select()); // e.g. a network-change event
  assert.equal(router.active, 'pc');
  router.endTurn();
  assert.equal(router.active, 'pi');
});

test('the active backend dying is noticed by the background probe', async () => {
  const { clock, net, router } = setup();
  await run(clock, router.select());
  router.start();
  net.state.pi = 'down';
  await clock.advance(60000 + 500);
  assert.equal(router.active, 'pc');
  net.state.pc = 'down';
  await clock.advance(60000);
  assert.equal(router.active, 'cloud');
  router.stop();
});

test('a backend that dies mid-turn is left at once', async () => {
  const { clock, net, router } = setup();
  await run(clock, router.select());
  router.beginTurn();
  net.state.pi = 'down';
  const r = await run(clock, router.request('/api/events'));
  assert.equal((await r.json()).role, 'pc');
  const r2 = await run(clock, router.request('/api/events'));
  assert.equal((await r2.json()).role, 'pc');
  router.endTurn();
});

// --- manual override ----------------------------------------------------------------

test('override forces one backend; auto goes back to priority', async () => {
  const { clock, router } = setup();
  await run(clock, router.select());
  router.setOverride('cloud');
  assert.equal(router.active, 'cloud');
  await run(clock, router.select());
  assert.equal(router.active, 'cloud');
  router.start();
  await clock.advance(3 * 60000);
  assert.equal(router.active, 'cloud', 'no fail-back while forced');
  router.stop();
  router.setOverride('auto');
  assert.equal(router.active, 'pi');
  assert.equal(router.status().override, 'auto');
});

test('a forced backend that fails is not silently replaced', async () => {
  const { clock, net, router } = setup();
  router.setOverride('pc');
  await run(clock, router.select());
  net.state.pc = 'down';
  await assert.rejects(run(clock, router.request('/api/state')), NoBackend);
  assert.deepEqual(net.calls('/api/state'), ['pc']);
});

test('unknown override values mean auto', () => {
  const { router } = setup();
  router.setOverride('mac');
  assert.equal(router.override, 'auto');
});

// --- the full scenario --------------------------------------------------------

test('three-backend simulation: unplug Pi, PC off, then both come back', async () => {
  const { clock, net, router } = setup();
  assert.equal(await run(clock, router.select()), 'pi');
  router.start();
  router.beginTurn();
  net.state.pi = 'down'; // unplugged mid-conversation
  const r = await run(clock, router.request('/api/history'));
  assert.equal((await r.json()).role, 'pc');
  router.endTurn();
  net.state.pc = 'down'; // PC turned off
  await clock.advance(60000 + 500); // the next background probe (cloud answers in 200 ms)
  assert.equal(router.active, 'cloud');
  net.state.pi = 'up';
  net.state.pc = 'up';
  await clock.advance(60000);
  assert.equal(router.active, 'cloud', 'hysteresis: one good probe');
  await clock.advance(60000);
  assert.equal(router.active, 'pi', 'Pi, not PC: highest priority wins');
  router.stop();
});

// --- settings helpers -----------------------------------------------------------------

test('mergeBackends: user settings win, cloud defaults to the app origin', () => {
  const server = [{ role: 'pi', url: 'https://old-pi.t.ts.net' }, { role: 'pc', url: URLS.pc }];
  assert.deepEqual(mergeBackends(server, { pi: URLS.pi }, URLS.cloud), BACKENDS);
  assert.deepEqual(mergeBackends(server, { pc: '' }, URLS.cloud).map((b) => b.role), ['pi', 'cloud']);
  assert.deepEqual(mergeBackends([{ role: 'evil', url: 'https://x.example' }], {}, null), []);
});

test('normalizeUrl only allows https (http for localhost) without paths or credentials', () => {
  assert.equal(normalizeUrl('https://PI.tail1234.ts.net/'), URLS.pi);
  assert.equal(normalizeUrl('http://pi.tail1234.ts.net'), null);
  assert.equal(normalizeUrl('http://localhost:8765'), 'http://localhost:8765');
  assert.equal(normalizeUrl('https://u:p@pi.tail1234.ts.net'), null);
  assert.equal(normalizeUrl('https://pi.tail1234.ts.net/x'), null);
  assert.equal(normalizeUrl('javascript:alert(1)'), null);
  assert.equal(normalizeUrl('not a url'), null);
});

test('wsUrl swaps the scheme and never carries the token', async () => {
  const { clock, router } = setup();
  await run(clock, router.select());
  assert.equal(router.wsUrl('/api/ws'), 'wss://pi.tail1234.ts.net/api/ws');
});

test('tokenStore falls back to memory without IndexedDB', async () => {
  const store = tokenStore(undefined);
  assert.equal(await store.get('token'), null);
  await store.set('token', 'abc');
  assert.equal(await store.get('token'), 'abc');
  await store.delete('token');
  assert.equal(await store.get('token'), null);
});
