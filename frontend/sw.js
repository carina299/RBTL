/* RBTL — service worker (offline shell + Web Push).
   IMPORTANT: bump CACHE on every front-end change, or installed clients keep the
   old shell (the precached index.html won't refresh until the SW reinstalls). */
const AI_NAME = "Claude";          // push-title fallback; keep in sync with the relay's RELAY_AI_NAME
const CACHE = "rbtl-v3";
const PRECACHE = [
  "./index.html",
  "./desktop.html",
  "./chat.html",
  "./memories.html",
  "./css/shared.css",
  "./css/intro.css",
  "./css/desktop.css",
  "./js/intro.js",
  "./js/desktop.js",
  "./icon-192.png",
];

self.addEventListener("install", (e) => {
  e.waitUntil(
    caches.open(CACHE)
      .then((c) => c.addAll(PRECACHE))
      .then(() => self.skipWaiting())
      .catch(() => self.skipWaiting())
  );
});
self.addEventListener("activate", (e) => {
  e.waitUntil(
    caches.keys()
      .then((ks) => Promise.all(ks.filter((k) => k !== CACHE).map((k) => caches.delete(k))))
      .then(() => self.clients.claim())
  );
});
// Network-first for everything same-origin, cache only as the offline fallback.
//
// The obvious alternative — cache-first for assets — is what made edits to this
// frontend appear to do nothing: a cached copy is returned forever and the
// network is never consulted, so a change only lands after CACHE is bumped.
// Always asking the network first costs a round trip and removes that whole
// class of confusion; the cache still covers being offline.
self.addEventListener("fetch", (e) => {
  const url = new URL(e.request.url);
  if (url.origin !== location.origin) return;              // the relay lives elsewhere — never touch it
  if (url.pathname.startsWith("/relay/")) return;          // …or here, when nginx reverse-proxies it
  if (e.request.method !== "GET") return;

  e.respondWith(
    fetch(e.request, e.request.mode === "navigate" ? { cache: "reload" } : undefined)
      .then((res) => {
        if (res && res.ok){
          const copy = res.clone();
          caches.open(CACHE).then((c) => c.put(e.request, copy));
        }
        return res;
      })
      .catch(() =>
        caches.match(e.request).then((hit) =>
          hit || (e.request.mode === "navigate" ? caches.match("./index.html") : Response.error())
        )
      )
  );
});

// ── Web Push (VAPID) ──────────────────────────────
// The relay sends a push when the AI replies and no PWA tab is holding the stream;
// here we surface it on the lock screen.
self.addEventListener("push", (e) => {
  let d = {};
  try { d = e.data ? e.data.json() : {}; }
  catch (_) { d = { body: (e.data && e.data.text && e.data.text()) || "" }; }
  const title = d.title || AI_NAME;                        // backend sends RELAY_AI_NAME as title
  const body  = d.body  || "You have a new message";
  const tag   = d.id ? ("companion-" + d.id) : "companion-msg";
  e.waitUntil(
    self.registration.showNotification(title, {
      body,
      tag,
      renotify: true,
      icon:  "./icon-192.png",
      badge: "./icon-192.png",
      vibrate: [80, 40, 80],
      data: { url: d.url || "./" },
    })
  );
});

self.addEventListener("notificationclick", (e) => {
  e.notification.close();
  const target = (e.notification.data && e.notification.data.url) || "./";
  e.waitUntil(
    // matchAll only returns clients this SW controls (our own scope), so focus the first one.
    self.clients.matchAll({ type: "window", includeUncontrolled: true }).then((cls) => {
      for (const c of cls) {
        if ("focus" in c){ c.postMessage({ type: "backfill" }); return c.focus(); }
      }
      return self.clients.openWindow ? self.clients.openWindow(target) : null;
    })
  );
});
