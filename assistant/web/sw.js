// Makes the page installable and opens it quickly. Only the app shell is
// cached (network first, so updates show up); API calls, reply audio and
// anything else are never cached.
const CACHE = "hey-claude-shell-v9";
const SHELL = ["/", "/i18n.js", "/app.js", "/history.js", "/backend-router.js", "/family.js", "/app.css", "/manifest.webmanifest", "/icon.svg",
               "/icon-180.png", "/icon-192.png", "/icon-512.png", "/silence.wav"];

self.addEventListener("install", (e) => {
  e.waitUntil(caches.open(CACHE).then((c) => c.addAll(SHELL)).then(() => self.skipWaiting()));
});

self.addEventListener("activate", (e) => {
  e.waitUntil(caches.keys()
    .then((keys) => Promise.all(keys.filter((k) => k !== CACHE).map((k) => caches.delete(k))))
    .then(() => self.clients.claim()));
});

self.addEventListener("fetch", (e) => {
  const url = new URL(e.request.url);
  if (e.request.method !== "GET" || url.origin !== location.origin ||
      !SHELL.includes(url.pathname)) return;  // the browser handles it as usual
  e.respondWith(fetch(e.request)
    .then((res) => {
      if (res.ok) { const copy = res.clone(); caches.open(CACHE).then((c) => c.put(e.request, copy)); }
      return res;
    })
    .catch(() => caches.match(e.request)));
});
