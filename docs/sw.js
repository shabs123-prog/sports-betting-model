// Network-first for everything, including the shell (app.js, index.html,
// styles.css, manifest) -- not just data.json. Cache-first for the shell
// was the cause of a real, confusing bug: a normal reload would serve the
// OLD cached shell immediately, while the browser only checked for a new
// service worker in the background -- and even once a new worker
// installed, the OLD page's already-running JS had no way to know to
// reload, so it looked "stuck" until a second manual refresh. Network-
// first means every load tries the live server first (so you always see
// the latest version while online) and only falls back to cache when
// there's genuinely no connection -- a small latency cost on each load,
// worth it while this app is still actively changing. CACHE_NAME still
// matters for the offline fallback itself, bump it on real shell changes,
// but normal updates no longer depend on that at all.
const CACHE_NAME = "value-bets-v3";
const SHELL = [
  "./",
  "./index.html",
  "./styles.css",
  "./app.js",
  "./manifest.webmanifest",
  "./icons/icon-192.png",
  "./icons/icon-512.png",
];

self.addEventListener("install", (event) => {
  event.waitUntil(caches.open(CACHE_NAME).then((cache) => cache.addAll(SHELL)));
  self.skipWaiting();
});

self.addEventListener("activate", (event) => {
  event.waitUntil(
    caches.keys().then((keys) =>
      Promise.all(keys.filter((k) => k !== CACHE_NAME).map((k) => caches.delete(k)))
    )
  );
  self.clients.claim();
});

self.addEventListener("fetch", (event) => {
  event.respondWith(
    fetch(event.request)
      .then((resp) => {
        const copy = resp.clone();
        caches.open(CACHE_NAME).then((cache) => cache.put(event.request, copy));
        return resp;
      })
      .catch(() => caches.match(event.request))
  );
});
