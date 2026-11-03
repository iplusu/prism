// Preview PDF on the chat proposal card: the button opens the candidate PDF over the chat before anything is applied.
// Needs a DEMO_MODE stack. usage: PRISM_URL=http://127.0.0.1:8010 node previewpdf.mjs <admin password>
import puppeteer from "puppeteer-core";
import { rmSync } from "node:fs";

const B = process.env.PRISM_URL;
const [ADMIN_PW] = process.argv.slice(2);
const sleep = (ms) => new Promise((r) => setTimeout(r, ms));
const profile = `${process.env.HOME}/snap/firefox/common/prism-e2e-profile-${process.pid}`;
const browser = await puppeteer.launch({ browser: "firefox", executablePath: "/usr/bin/firefox", headless: true, userDataDir: profile });
const p = await (await browser.createBrowserContext()).newPage();
await p.setViewport({ width: 1500, height: 950 });
let failed = 0, pid = null;
async function step(name, fn) { try { console.log("PASS", name, (await fn()) ?? ""); } catch (e) { failed++; console.log("FAIL", name, e.message); } }
const api = (method, path, body) => p.evaluate(async (method, path, body) => {
  const r = await fetch(path, { method, headers: { "X-Prism-CSRF": "1", "Content-Type": "application/json" }, body: body ? JSON.stringify(body) : undefined });
  return { status: r.status, json: await r.json().catch(() => null) };
}, method, path, body);
const until = async (fn, ms = 90000) => { const t = Date.now(); while (Date.now() - t < ms) { const v = await fn(); if (v) return v; await sleep(400); } return null; };
const proposals = async () => (await api("GET", `/api/projects/${pid}/proposals?status=all`)).json;
const btn = (text) => p.evaluate((text) => { const b = [...document.querySelectorAll("button")].find((x) => x.textContent.trim() === text && !x.disabled); b?.click(); return !!b; }, text);

let prop = null;
await step("login and a ready proposal", async () => {
  await p.goto(B + "/", { waitUntil: "networkidle0" });
  await p.type("input[autocomplete=username]", "admin"); await p.type("input[type=password]", ADMIN_PW); await p.click("button[type=submit]");
  await p.waitForFunction(() => !document.querySelector("input[type=password]"), { timeout: 15000 });
  pid = (await api("POST", "/api/projects", { name: "previewpdf-check (temporary)", template: "sample" })).json.id;
  await p.goto(`${B}/p/${pid}`, { waitUntil: "networkidle0" });
  await p.waitForSelector(".cm-content", { timeout: 20000 });
  await p.type("#ai-text", "Add a short remark to the main file");
  await (await p.$("select[aria-label='Tool that performs the task']")).select("demo");
  await p.keyboard.down("Control"); await p.keyboard.press("Enter"); await p.keyboard.up("Control");
  prop = await until(async () => (await proposals()).find((x) => x.status === "ready"));
  if (!prop) throw new Error("no ready proposal: " + JSON.stringify((await proposals()).map((x) => [x.status, x.error])) + " tasks " + JSON.stringify((await api("GET", `/api/projects/${pid}/tasks`)).json.map((t) => [t.status, t.error])));
  return `candidate_build_id=${!!prop.candidate_build_id}`;
});
await step("the card has a Preview PDF button; nothing is applied by opening it", async () => {
  await p.waitForFunction(() => [...document.querySelectorAll(".proposal-card button")].some((b) => b.textContent.trim() === "Preview PDF"), { timeout: 20000 });
  await p.evaluate(() => [...document.querySelectorAll(".proposal-card button")].find((b) => b.textContent.trim() === "Preview PDF").click());
  await p.waitForSelector(".modal .candidate-pdf-view", { timeout: 10000 });
  const rendered = await until(() => p.evaluate(() => [...document.querySelectorAll(".modal canvas")].some((c) => c.width > 100 && c.height > 100)), 30000);
  if (!rendered) throw new Error("the PDF did not render in the modal");
  const st = (await proposals()).find((x) => x.id === prop.id).status;
  if (st !== "ready") throw new Error("proposal status changed to " + st);
  await p.screenshot({ path: process.env.SHOT || "/tmp/previewpdf.png" });
  return "PDF canvas rendered; proposal still ready";
});
await step("the changes can be marked (latexdiff) in the preview", async () => {
  if (!(await btn("Show changes marked (latexdiff)"))) throw new Error("no marked button");
  const ok = await until(() => p.evaluate(() => [...document.querySelectorAll(".modal button")].some((b) => b.textContent.trim() === "Show the plain PDF")), 120000);
  if (!ok) { const err = await p.evaluate(() => document.querySelector(".modal .error-box")?.textContent ?? ""); throw new Error("marked PDF not shown: " + err); }
  const rendered = await until(() => p.evaluate(() => [...document.querySelectorAll(".modal canvas")].some((c) => c.width > 100)), 30000);
  if (!rendered) throw new Error("marked PDF not rendered");
});
await step("Escape closes it", async () => {
  await p.keyboard.press("Escape");
  await sleep(400);
  if (await p.$(".modal")) throw new Error("modal still open");
});
if (pid && !process.env.KEEP) await api("DELETE", `/api/projects/${pid}`);
await browser.close();
rmSync(profile, { recursive: true, force: true });
console.log(failed ? `${failed} FAILED` : "ALL PASSED");
process.exit(failed ? 1 : 0);
