// Browser check of SyncTeX: "Show in PDF" highlights a region, double-clicking the PDF jumps to the source (also into an included file).
// usage: PRISM_URL=https://host node synctex.mjs <screenshot dir> <admin password>
import puppeteer from "puppeteer-core";

const B = process.env.PRISM_URL || "http://127.0.0.1:8000";
const [OUT, ADMIN_PW] = process.argv.slice(2);
const browser = await puppeteer.launch({ browser: "firefox", executablePath: "/usr/bin/firefox", headless: true });
const p = await (await browser.createBrowserContext()).newPage();
await p.setViewport({ width: 1500, height: 950 });
const errors = [];
p.on("pageerror", (e) => errors.push(e.message));
p.on("request", (r) => /synctex/.test(r.url()) && console.log("  req", r.url().replace(/^.*\/api/, "/api")));
const sleep = (ms) => new Promise((r) => setTimeout(r, ms));
let pid = null, failed = 0;
const api = (method, path, body) => p.evaluate(async (method, path, body) => {
  const r = await fetch(path, { method, credentials: "same-origin", headers: { "Content-Type": "application/json", "X-Prism-CSRF": "1" }, body: body ? JSON.stringify(body) : undefined });
  return r.json().catch(() => null);
}, method, path, body);
async function step(name, fn) {
  try { console.log("PASS", name, (await fn()) ?? ""); } catch (e) { failed++; await p.screenshot({ path: `${OUT}/sync-fail-${failed}.png` }).catch(() => {}); console.log("FAIL", name, e.message); }
}
await p.goto(B + "/", { waitUntil: "networkidle0" });
await p.type("input[autocomplete=username]", "admin");
await p.type("input[type=password]", ADMIN_PW);
await p.click("button[type=submit]");
await p.waitForFunction(() => !document.querySelector("input[type=password]"), { timeout: 15000 });
pid = (await api("POST", "/api/projects", { name: "synctex-check (temporary)", template: "sample" })).id;
await p.goto(`${B}/p/${pid}`, { waitUntil: "networkidle0" });
await p.waitForSelector(".cm-content", { timeout: 30000 });
let bid;
await step("compile, PDF shown", async () => {
  await p.evaluate(() => [...document.querySelectorAll("button")].find((b) => b.textContent.trim() === "Compile").click());
  await p.waitForSelector(".pdf-page canvas", { timeout: 90000 });
  bid = (await api("GET", `/api/projects/${pid}/builds?limit=1`))[0].id;
});
await step("Show in PDF: caret line in main.tex is highlighted on the PDF", async () => {
  // put the caret on the abstract line
  await p.evaluate(() => { const l = [...document.querySelectorAll(".cm-line")].find((x) => x.textContent.startsWith("We study")); const r = l.getBoundingClientRect(); window.__pt = { x: r.left + 20, y: r.top + 5 }; });
  const pt = await p.evaluate(() => window.__pt);
  await p.mouse.click(pt.x, pt.y);
  await p.evaluate(() => [...document.querySelectorAll("button")].find((b) => b.textContent.trim() === "Show in PDF").click());
  await p.waitForSelector(".pdf-mark", { timeout: 10000 });
  const m = await p.$eval(".pdf-mark", (e) => { const r = e.getBoundingClientRect(); return { w: r.width, h: r.height, page: e.closest(".pdf-page").dataset.page }; });
  if (m.page !== "1" || m.w < 40) throw new Error(JSON.stringify(m));
  await p.screenshot({ path: `${OUT}/s1-show-in-pdf.png` });
  return JSON.stringify(m);
});
const dbl = async (page, x, y) => {
  const t = await p.evaluate((page, x, y) => {
    const pg = document.querySelector(`.pdf-page[data-page="${page}"]`);
    const sc = parseInt(document.querySelector(".zoom-label").textContent) / 100;
    pg.scrollIntoView({ block: "start" });
    const r = pg.getBoundingClientRect();
    return { cx: r.left + x * sc, cy: r.top + y * sc };
  }, page, x, y);
  await sleep(400);
  const t2 = await p.evaluate((page, x, y) => { const pg = document.querySelector(`.pdf-page[data-page="${page}"]`); const sc = parseInt(document.querySelector(".zoom-label").textContent) / 100; const r = pg.getBoundingClientRect(); return { cx: r.left + x * sc, cy: r.top + y * sc }; }, page, x, y);
  await p.mouse.click(t2.cx, t2.cy, { count: 2 });
};
await step("double-click on the abstract in the PDF jumps to main.tex at that line", async () => {
  const f = await api("GET", `/api/builds/${bid}/synctex/forward?file=main.tex&line=19`);
  const b = f.boxes[0];
  await p.evaluate(() => { const l = [...document.querySelectorAll(".cm-line")].find((x) => x.textContent.startsWith("\\documentclass")); const r = l.getBoundingClientRect(); window.__pt = { x: r.left + 20, y: r.top + 5 }; });
  const away = await p.evaluate(() => window.__pt);
  await p.mouse.click(away.x, away.y); // caret away from the abstract, so landing there proves the jump
  if (/We study/.test(await p.$eval(".cm-activeLine", (e) => e.textContent))) throw new Error("caret did not move away");
  await dbl(b.page, b.x + b.w / 2, b.y + b.h / 2);
  await p.waitForFunction(() => /We study|proximal|sparse|curvature/.test(document.querySelector(".cm-activeLine")?.textContent || ""), { timeout: 8000 });
  return (await p.$eval(".cm-activeLine", (e) => e.textContent)).slice(0, 50);
});
await step("double-click on text from an included file opens that file", async () => {
  const intro = (await api("GET", `/api/projects/${pid}/files/content?path=sections/introduction.tex`)).text.split("\n");
  const line = intro.findIndex((l) => l.length > 60) + 1;
  const f = await api("GET", `/api/builds/${bid}/synctex/forward?file=sections/introduction.tex&line=${line}`);
  const b = f.boxes[0];
  const apiInv = await api("GET", `/api/builds/${bid}/synctex/inverse?page=${b.page}&x=${b.x + 10}&y=${b.y + b.h / 2}`);
  console.log("  line", line, "box", JSON.stringify(b), "API inverse ->", JSON.stringify(apiInv.source));
  await dbl(b.page, b.x + 10, b.y + b.h / 2);
  await sleep(1500);
  console.log("  active tab after click:", await p.$eval(".file-tab.active", (e) => e.title), "| notice:", await p.evaluate(() => document.querySelector(".sync-notice")?.textContent ?? null));
  await p.waitForFunction(() => document.querySelector(".file-tab.active")?.title === "sections/introduction.tex", { timeout: 8000 });
  await p.screenshot({ path: `${OUT}/s2-pdf-to-source.png` });
});
await step("no uncaught page errors", async () => {
  const real = errors.filter((e) => !/cross-origin|Permission denied to access property/i.test(e));
  if (real.length) throw new Error(real.join(" | "));
});
console.log("cleanup delete HTTP", await p.evaluate(async (id) => (await fetch(`/api/projects/${id}`, { method: "DELETE", credentials: "same-origin", headers: { "X-Prism-CSRF": "1" } })).status, pid));
await browser.close();
process.exit(failed ? 1 : 0);
