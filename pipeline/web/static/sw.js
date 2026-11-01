// Minimální service worker: aplikace jde nainstalovat a obal se načte i bez sítě. API vždy ze sítě.
const CACHE = "kos-v41";
const SHELL = ["/", "/static/app.js", "/static/meetings.js", "/static/app.css", "/static/jkdoc.css", "/static/fonts.css", "/static/icon.svg", "/manifest.webmanifest"];
self.addEventListener("install", e => { e.waitUntil(caches.open(CACHE).then(c => c.addAll(SHELL))); self.skipWaiting(); });
self.addEventListener("activate", e => { e.waitUntil(caches.keys().then(ks => Promise.all(ks.filter(k => k !== CACHE).map(k => caches.delete(k))))); self.clients.claim(); });
self.addEventListener("fetch", e => {
  const u = new URL(e.request.url);
  if (u.pathname.startsWith("/api/") || e.request.method !== "GET") return;
  e.respondWith(fetch(e.request).then(r => { const c = r.clone(); caches.open(CACHE).then(x => x.put(e.request, c)); return r; })
    .catch(() => caches.match(e.request)));
});

// Klepnutí na oznámení „Nahrává se schůzka“ → zpět do aplikace
self.addEventListener("notificationclick", e => {
  e.notification.close();
  const url = (e.notification.data && e.notification.data.url) || "/";
  e.waitUntil(self.clients.matchAll({type: "window", includeUncontrolled: true}).then(cs => {
    const c = cs.find(x => new URL(x.url).origin === self.location.origin);
    return c ? c.focus() : self.clients.openWindow(url);
  }));
});
