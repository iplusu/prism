// Prism service worker. It exists so the browser offers "Install app"; it deliberately caches nothing:
// the paper, the builds and the AI chat must always come from the server, never from a stale copy.
self.addEventListener("install", () => self.skipWaiting());
self.addEventListener("activate", (e) => e.waitUntil(self.clients.claim()));
self.addEventListener("fetch", () => {});
