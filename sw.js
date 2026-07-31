/* Bolig Tracker service worker — makes the installed app load instantly and
   work offline. Shell is cache-first (versioned); data JSON is network-first
   with a cache fallback so you always get fresh listings when online. */
const CACHE = 'bolig-tracker-v61';
const SHELL = [
  './', './index.html', './styles.css?v=47', './app.js?v=61',
  './model.html', './model.js?v=2', './modelpage.js?v=1',
  './renter.html', './renter.js?v=43', './om.html',
  './vendor/leaflet/leaflet.js', './vendor/leaflet/leaflet.css',
  './logo.svg?v=14', './icon-192.png?v=14', './apple-touch-icon.png?v=14',
  './manifest.webmanifest?v=14',
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

  // app shell: serve from cache, refresh in the background
  e.respondWith(
    caches.match(req).then(cached => cached || fetch(req).then(res => {
      const copy = res.clone(); caches.open(CACHE).then(c => c.put(req, copy)); return res;
    }))
  );
});
