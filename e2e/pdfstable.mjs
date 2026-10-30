// Measures whether the PDF view jumps while a recompile happens: samples the scroll area every 50 ms.
// usage: PRISM_URL=https://host node pdfstable.mjs <screenshot dir> <admin password>
import puppeteer from "puppeteer-core";

const B = process.env.PRISM_URL || "http://127.0.0.1:8000";
const [OUT, ADMIN_PW] = process.argv.slice(2);
const browser = await puppeteer.launch({ browser: "firefox", executablePath: "/usr/bin/firefox", headless: true });
const p = await (await browser.createBrowserContext()).newPage();
await p.setViewport({ width: 1500, height: 950 });
const sleep = (ms) => new Promise((r) => setTimeout(r, ms));
let pid = null, failed = 0;
const api = (method, path, body) => p.evaluate(async (method, path, body) => {
  const r = await fetch(path, { method, credentials: "same-origin", headers: { "Content-Type": "application/json", "X-Prism-CSRF": "1" }, body: body ? JSON.stringify(body) : undefined });
  return r.json().catch(() => null);
}, method, path, body);
async function step(name, fn) {
  try { console.log("PASS", name, (await fn()) ?? ""); } catch (e) { failed++; console.log("FAIL", name, e.message); }
}
await p.goto(B + "/", { waitUntil: "networkidle0" });
await p.type("input[autocomplete=username]", "admin");
await p.type("input[type=password]", ADMIN_PW);
await p.click("button[type=submit]");
await p.waitForFunction(() => !document.querySelector("input[type=password]"), { timeout: 15000 });
pid = (await api("POST", "/api/projects", { name: "pdfstable-check (temporary)", template: "sample" })).id;
// make the document long enough to scroll: append paragraphs through the API (real text, real compile)
const f = await api("GET", `/api/projects/${pid}/files/content?path=sections/results.tex`);
const filler = Array.from({ length: 40 }, (_, i) => `Paragraph ${i}. ` + "Gradient methods are analysed under standard smoothness assumptions. ".repeat(6)).join("\n\n");
await p.evaluate(async (pid, text, sha) => (await fetch(`/api/projects/${pid}/files/content`, { method: "PUT", credentials: "same-origin", headers: { "Content-Type": "application/json", "X-Prism-CSRF": "1" }, body: JSON.stringify({ path: "sections/results.tex", text, expected_sha: sha }) })).status, pid, f.text + "\n\n" + filler, f.sha);
await p.goto(`${B}/p/${pid}`, { waitUntil: "networkidle0" });
await p.waitForSelector(".cm-content", { timeout: 30000 });
const compileAndWait = async () => {
  const prev = ((await api("GET", `/api/projects/${pid}/builds?limit=1`))[0] || {}).id;
  await p.evaluate(() => [...document.querySelectorAll("button")].find((b) => b.textContent.trim() === "Compile").click());
  const t0 = Date.now();
  for (;;) {
    const b = await api("GET", `/api/projects/${pid}/builds?limit=1`);
    if (b[0] && b[0].id !== prev && !["queued", "running"].includes(b[0].status)) return b[0];
    if (Date.now() - t0 > 90000) throw new Error("compile timeout");
    await sleep(500);
  }
};
await step("first compile renders a multi-page PDF", async () => {
  const b = await compileAndWait();
  if (b.status !== "succeeded") throw new Error(b.status);
  await p.waitForSelector(".pdf-page canvas", { timeout: 30000 });
  const n = await p.$$eval(".pdf-page", (x) => x.length);
  if (n < 3) throw new Error("only " + n + " pages");
  return n + " pages";
});
await step("recompile while scrolled to page 3: no jump, no reload flash, no blank pages", async () => {
  await p.evaluate(() => document.querySelector('.pdf-page[data-page="3"]').scrollIntoView({ block: "start" }));
  await sleep(1500);
  const sample = () => p.evaluate(() => {
    const s = document.querySelector(".pdf-scroll");
    const pg = document.querySelector('.pdf-page[data-page="3"]');
    if (!pg) return { top: s.scrollTop, h: s.scrollHeight, w: s.clientWidth, pg3: null, gone: true, loading: /Loading PDF/.test(s.innerText), blank: 0, viewerY: Math.round(document.querySelector('.pdf-viewer').getBoundingClientRect().top) };
    return { top: s.scrollTop, h: s.scrollHeight, w: s.clientWidth, pg3: Math.round(pg.getBoundingClientRect().top - s.getBoundingClientRect().top),
      loading: /Loading PDF/.test(s.innerText), blank: [...document.querySelectorAll(".pdf-page canvas")].filter((c) => c.width < 10).length,
      viewerY: Math.round(document.querySelector(".pdf-viewer").getBoundingClientRect().top) };
  });
  const lastPage = () => p.evaluate(() => { const c = document.querySelector('.pdf-page[data-page="3"] canvas'); const d = c.getContext("2d").getImageData(0, 0, c.width, c.height).data; let h = 0; for (let i = 0; i < d.length; i += 37) h = (h * 31 + d[i]) | 0; return h; });
  const pixelsBefore = await lastPage();
  const base = await sample();
  if (base.top < 200) throw new Error("not scrolled: scrollTop=" + base.top + " (the test would prove nothing)");
  console.log("  base", JSON.stringify(base));
  const samples = [];
  let done = false;
  const sampler = (async () => { while (!done) { samples.push(await sample()); await sleep(40); } })();
  // change the manuscript so the build produces a genuinely new PDF
  const cur = await api("GET", `/api/projects/${pid}/files/content?path=sections/results.tex`);
  const putStatus = await p.evaluate(async (pid, text, sha) => (await fetch(`/api/projects/${pid}/files/content`, { method: "PUT", credentials: "same-origin", headers: { "Content-Type": "application/json", "X-Prism-CSRF": "1" }, body: JSON.stringify({ path: "sections/results.tex", text, expected_sha: sha }) })).status, pid, "An added opening paragraph that moves everything after it. ".repeat(12) + "\n\n" + cur.text, cur.sha);
  console.log("  PUT status", putStatus);
  let b;
  try { b = await compileAndWait(); await sleep(2500); } finally { done = true; await sampler; }
  if (b.status !== "succeeded") throw new Error(b.status);
  const t1 = Date.now();
  while ((await lastPage()) === pixelsBefore && Date.now() - t1 < 15000) await sleep(250);
  console.log("  new pixels on screen after", Date.now() - t1, "ms beyond the 2.5 s settle");
  if ((await lastPage()) === pixelsBefore) throw new Error("the new PDF never reached the screen (page 3 unchanged)");
  const dTop = Math.max(...samples.map((s) => Math.abs(s.top - base.top)));
  const gone = samples.filter((s) => s.gone).length;
  const dPg = Math.max(...samples.filter((s) => !s.gone).map((s) => Math.abs(s.pg3 - base.pg3)), 0);
  const dW = Math.max(...samples.map((s) => Math.abs(s.w - base.w)));
  const dView = Math.max(...samples.map((s) => Math.abs(s.viewerY - base.viewerY)));
  const loading = samples.filter((s) => s.loading).length, blank = Math.max(...samples.map((s) => s.blank));
  const msg = `${samples.length} samples; max scrollTop drift ${dTop}px, page-3 drift ${dPg}px, width change ${dW}px, viewer shifted ${dView}px, pages removed in ${gone} samples, 'Loading' in ${loading} samples, blank canvases ${blank}`;
  if (gone || dTop > 2 || dPg > 2 || loading || dView > 2 || blank) throw new Error(msg);
  return msg;
});
if (pid) console.log("cleanup delete HTTP", await p.evaluate(async (id) => (await fetch(`/api/projects/${id}`, { method: "DELETE", credentials: "same-origin", headers: { "X-Prism-CSRF": "1" } })).status, pid));
await browser.close();
process.exit(failed ? 1 : 0);
