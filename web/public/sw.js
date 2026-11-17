// Offline support (spec section 7): cached app shell, cached Home and Queue data.
// Review actions are queued in the page (lib/outbox.ts) and sent when the connection returns.
const CACHE = "clipping-v1";
const DATA = ["/api/home", "/api/queue", "/api/reference", "/api/review-later"];

self.addEventListener("install", (event) => {
  event.waitUntil(caches.open(CACHE).then((cache) => cache.addAll(["/", "/queue", "/manifest.webmanifest"]).catch(() => undefined)));
  self.skipWaiting();
});

self.addEventListener("activate", (event) => {
  event.waitUntil(caches.keys().then((keys) => Promise.all(keys.filter((k) => k !== CACHE).map((k) => caches.delete(k)))));
  self.clients.claim();
});

self.addEventListener("fetch", (event) => {
  const request = event.request;
  if (request.method !== "GET") return;
  const url = new URL(request.url);
  if (url.origin !== location.origin || url.pathname.startsWith("/api/media/")) return; // media links are short-lived: never cached
  const isData = DATA.includes(url.pathname);
  if (url.pathname.startsWith("/api/") && !isData) return;
  // Network first, cache as the offline fallback.
  event.respondWith(
    fetch(request)
      .then((response) => {
        if (response.ok) {
          const copy = response.clone();
          caches.open(CACHE).then((cache) => cache.put(request, copy));
        }
        return response;
      })
      .catch(() => caches.match(request).then((hit) => hit || caches.match("/")))
  );
});
