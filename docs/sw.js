// sw.js: lets the installed web app start and work offline. Network first:
// an online visit always gets the current files, and every response is kept
// in the cache for the next offline start. On install the whole app (pages,
// scripts, fonts, icons and the trained models) is stored once.
const CACHE = "target2plan-1.0.0";
const APP = [
  "./",
  "manifest.webmanifest",
  "css/app.css",
  "css/fonts.css",
  "fonts/inter-latin-400-normal.woff2",
  "fonts/inter-latin-500-normal.woff2",
  "fonts/inter-latin-600-normal.woff2",
  "fonts/inter-latin-700-normal.woff2",
  "fonts/inter-latin-ext-400-normal.woff2",
  "fonts/inter-latin-ext-500-normal.woff2",
  "fonts/inter-latin-ext-600-normal.woff2",
  "fonts/inter-latin-ext-700-normal.woff2",
  "fonts/jetbrains-mono-latin-400-normal.woff2",
  "fonts/jetbrains-mono-latin-600-normal.woff2",
  "fonts/jetbrains-mono-latin-ext-400-normal.woff2",
  "fonts/jetbrains-mono-latin-ext-600-normal.woff2",
  "icons/apple-touch-icon.png",
  "icons/favicon-64.png",
  "icons/icon-192.png",
  "icons/icon-512.png",
  "icons/icon-maskable-512.png",
  "index.html",
  "js/app.js",
  "js/engine/area.js",
  "js/engine/connectivity.js",
  "js/engine/nn.js",
  "js/engine/pymath.js",
  "js/engine/relocation.js",
  "js/engine/rng.js",
  "js/engine/schematic.js",
  "js/engine/scoring.js",
  "js/engine/zones.js",
  "js/runtime.js",
  "js/store.js",
  "js/ui/about.js",
  "js/ui/common.js",
  "js/ui/detail.js",
  "js/ui/exporters.js",
  "js/ui/geometry.js",
  "js/ui/roomPopup.js",
  "js/ui/schematicPaint.js",
  "js/ui/stage0.js",
  "js/ui/stage1.js",
  "js/ui/stage2.js",
  "js/ui/stage3.js",
  "js/ui/stage4.js",
  "js/vendor/gifenc.esm.js",
  "js/vendor/mp4-muxer.js",
  "js/worker.js",
  "models/area.bin",
  "models/area.json",
  "models/conn.bin",
  "models/conn.json",
  "models/zone.bin",
  "models/zone.json",
];

self.addEventListener("install", (e) => {
  e.waitUntil(caches.open(CACHE).then((c) => Promise.all(APP.map((u) => c.add(u).catch(() => null)))).then(() => self.skipWaiting()));
});

self.addEventListener("activate", (e) => {
  e.waitUntil(caches.keys().then((keys) => Promise.all(keys.filter((k) => k !== CACHE).map((k) => caches.delete(k)))).then(() => self.clients.claim()));
});

self.addEventListener("fetch", (e) => {
  const req = e.request;
  if (req.method !== "GET" || new URL(req.url).origin !== self.location.origin) return;
  e.respondWith(fetch(req).then((res) => {
    if (res.ok) { const copy = res.clone(); caches.open(CACHE).then((c) => c.put(req, copy)); }
    return res;
  }).catch(async () => (await caches.match(req, { ignoreSearch: true })) || (req.mode === "navigate" ? caches.match("./") : Response.error())));
});
