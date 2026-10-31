// Makes the page installable and opens it quickly. Only the app shell is
// cached (network first, so updates show up); API calls, reply audio and
// anything else are never cached.
// Also shows the study briefings' and reminders' push notifications
// (push.py) and opens the brief (/#brief, brief.js) or Settings, Reminders
// (/#reminders, reminders.js) or a research report (/#research=<id>,
// research.js) when one is tapped.
const CACHE = "kwil-shell-v30";
const SHELL = ["/", "/i18n.js", "/app.js", "/history.js", "/backend-router.js", "/family.js", "/ui.js", "/attachments.js", "/app.css", "/manifest.webmanifest", "/icon.svg", "/memory.js", "/reminders.js", "/spotify.js", "/notion.js", "/voice-picker.js",
               "/student.js", "/brief.js", "/connect.js", "/connections.js", "/study.js", "/projects.js",
               "/sources.js", "/research.js", "/today.js",
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

// --- push notifications (push.py sends {title, body, url}) ---
const BRIEF_URL = "/#brief";
const MAX_TITLE = 80;
const MAX_BODY = 300;

// Only a path on this site: a payload can never send the tap elsewhere.
// Resolved the way the browser will ("/\t/evil.example" drops the tab and
// becomes https://evil.example/), then its origin must be ours.
function safeUrl(url) {
  if (typeof url !== "string" || !url.startsWith("/")) return BRIEF_URL;
  let resolved;
  try { resolved = new URL(url, self.location.origin); } catch (err) { return BRIEF_URL; }
  if (resolved.origin !== self.location.origin) return BRIEF_URL;
  return resolved.pathname + resolved.search + resolved.hash;
}

function payloadOf(data) {
  let body = {};
  try { body = data ? data.json() : {}; } catch (err) { body = {}; }
  if (!body || typeof body !== "object") body = {};
  return {
    title: typeof body.title === "string" && body.title ? body.title.slice(0, MAX_TITLE) : "Kwil",
    body: typeof body.body === "string" ? body.body.slice(0, MAX_BODY) : "",
    url: safeUrl(body.url),
  };
}

self.addEventListener("push", (e) => {
  // iOS wants every push to show a notification (userVisibleOnly)
  const p = payloadOf(e.data);
  e.waitUntil(self.registration.showNotification(p.title, {
    body: p.body, icon: "/icon-192.png", badge: "/icon-192.png",
    // the brief replaces the last brief; each reminder shows on its own
    ...(p.url === BRIEF_URL ? { tag: "kwil-brief" } : {}), data: { url: p.url },
  }));
});

self.addEventListener("notificationclick", (e) => {
  e.notification.close();
  const url = safeUrl(e.notification.data && e.notification.data.url);
  e.waitUntil(self.clients.matchAll({ type: "window", includeUncontrolled: true })
    .then((list) => {
      for (const client of list) {
        if (new URL(client.url).origin === location.origin && "focus" in client) {
          client.postMessage({ type: "open", url });  // brief.js opens the brief
          return client.focus();
        }
      }
      return self.clients.openWindow(url);  // the brief, with a Play button (no autoplay)
    }));
});
