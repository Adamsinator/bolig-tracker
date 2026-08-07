/* Bolig Tracker service worker — makes the installed app load instantly and
   work offline. Shell is stale-while-revalidate: serve the cached copy
   immediately (instant load, works offline), then refresh it from the
   network in the background — this matters most for un-versioned shell URLs
   (model.html, index.html, ...) that would otherwise never update once
   cached, no matter how many times a returning visitor reloads. Data JSON
   is network-first with a cache fallback so you always get fresh listings
   when online. */
const CACHE = 'bolig-tracker-v83';
const SHELL = [
  './', './index.html', './styles.css?v=55', './app.js?v=69',
  './model.html', './model.js?v=8', './modelpage.js?v=8',
  './renter.html', './renter.js?v=43', './om.html',
  './vendor/leaflet/leaflet.js', './vendor/leaflet/leaflet.css',
  './logo.svg?v=15', './icon-192.png?v=15', './apple-touch-icon.png?v=15',
  './manifest.webmanifest?v=15',
  './fonts/nunito-900.woff2', './fonts/nunito-700.woff2', './fonts/nunito-500.woff2',
  './fonts/jetbrains-mono-400.woff2', './fonts/jetbrains-mono-700.woff2',
];

self.addEventListener('install', e => {
  e.waitUntil(
    caches.open(CACHE).then(c => Promise.allSettled(SHELL.map(u => c.add(u)))).then(() => self.skipWaiting())
  );
});

self.addEventListener('activate', e => {
  e.waitUntil(
    caches.keys().then(keys => Promise.all(keys.filter(k => k !== CACHE).map(k => caches.delete(k)))).then(() => self.clients.claim())
  );
});

self.addEventListener('fetch', e => {
  const req = e.request;
  if (req.method !== 'GET') return;
  const url = new URL(req.url);
  if (url.origin !== location.origin) return;   // leave tiles / DAWA / CDNs alone

  // data files: fresh when online, cached copy when offline
  if (url.pathname.includes('/data/')) {
    e.respondWith(
      fetch(req).then(res => { const copy = res.clone(); caches.open(CACHE).then(c => c.put(req, copy)); return res; })
        .catch(() => caches.match(req))
    );
    return;
  }

  // app shell: serve from cache immediately for speed/offline, but always
  // refresh the cache from the network in the background too — the old
  // version here only fetched when there was NO cached copy, so a cached
  // shell URL (especially an un-versioned one like model.html) never got
  // updated by a normal visit, only by a service-worker reinstall.
  e.respondWith(
    caches.match(req).then(cached => {
      const refresh = fetch(req).then(res => {
        const copy = res.clone(); caches.open(CACHE).then(c => c.put(req, copy)); return res;
      }).catch(() => cached);
      e.waitUntil(refresh);
      return cached || refresh;
    })
  );
});
