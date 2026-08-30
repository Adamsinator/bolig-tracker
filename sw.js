/* Bolig Tracker service worker — makes the installed app load instantly and
   work offline.

   Page loads (navigations) are network-first with a cached fallback, so a
   reload always reflects what is actually deployed. Sub-resources — scripts,
   styles, fonts — are stale-while-revalidate: serve the cached copy
   immediately, then refresh it from the network in the background, which is
   what makes a repeat visit instant and an offline one work at all. Data JSON
   is network-first with a cache fallback so you always get fresh listings
   when online. */
const CACHE = 'bolig-tracker-v108';
const SHELL = [
  './', './index.html', './styles.css?v=70', './app.js?v=83',
  './model.html', './model.js?v=13', './modelpage.js?v=14',
  './renter.html', './renter.js?v=44', './om.html',
  './vendor/leaflet/leaflet.js', './vendor/leaflet/leaflet.css',
  './logo.svg?v=17', './icon-192.png?v=17', './apple-touch-icon.png?v=17',
  './manifest.webmanifest?v=18',
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

  // Page loads: network first, cached copy only as an offline fallback.
  //
  // Stale-while-revalidate below is right for scripts, styles and fonts, but
  // it was wrong for the HTML itself: it hands back the *previous* deploy and
  // only refreshes the cache afterwards, so a returning visitor renders the
  // old page — with the old versioned asset URLs in it — and does not see a
  // release until their second load. That made every deploy look like it had
  // not gone out. The HTML is a couple of KB; fetch it fresh whenever we can.
  if (req.mode === 'navigate') {
    e.respondWith(
      fetch(req)
        .then(res => { const copy = res.clone(); caches.open(CACHE).then(c => c.put(req, copy)); return res; })
        .catch(() => caches.match(req).then(c => c || caches.match('./index.html')))
    );
    return;
  }

  // everything else: serve from cache immediately for speed/offline, but always
  // refresh the cache from the network in the background too — the old
  // version here only fetched when there was NO cached copy, so a cached
  // shell URL never got updated by a normal visit, only by a SW reinstall.
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
