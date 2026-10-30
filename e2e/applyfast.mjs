// One-click apply: (1) the "Apply" button on the proposal card in the AI panel, without opening Review;
// (2) "Apply all changes" on the Review page, with no confirmation step. Needs a DEMO_MODE stack.
// usage: PRISM_URL=http://127.0.0.1:8010 node applyfast.mjs <admin password>
// Run it against a stack with COLLAB_ENABLED=true and one with false (the editors refresh differently).
import puppeteer from "puppeteer-core";

const B = process.env.PRISM_URL;
const [ADMIN_PW] = process.argv.slice(2);
const sleep = (ms) => new Promise((r) => setTimeout(r, ms));
const browser = await puppeteer.launch({ browser: "firefox", executablePath: "/usr/bin/firefox", headless: true });
const p = await (await browser.createBrowserContext()).newPage();
await p.setViewport({ width: 1500, height: 950 });
const errors = [];
p.on("pageerror", (e) => errors.push(e.message));
let failed = 0, pid = null;
async function step(name, fn) { try { console.log("PASS", name, (await fn()) ?? ""); } catch (e) { failed++; console.log("FAIL", name, e.message); } }
const api = (method, path, body) => p.evaluate(async (method, path, body) => {
  const r = await fetch(path, { method, headers: { "X-Prism-CSRF": "1", "Content-Type": "application/json" }, body: body ? JSON.stringify(body) : undefined });
  return { status: r.status, json: await r.json().catch(() => null) };
}, method, path, body);
const until = async (fn, ms = 90000) => { const t = Date.now(); while (Date.now() - t < ms) { const v = await fn(); if (v) return v; await sleep(400); } return null; };
const proposals = async () => (await api("GET", `/api/projects/${pid}/proposals?status=all`)).json;
const clickExact = (text) => p.evaluate((text) => { const b = [...document.querySelectorAll("button")].find((x) => x.textContent.trim() === text && !x.disabled); b?.click(); return !!b; }, text);
const editorText = () => p.evaluate(() => document.querySelector(".cm-content")?.innerText ?? "");
async function newProposal(request) {
  await p.goto(`${B}/p/${pid}`, { waitUntil: "networkidle0" });
  await p.waitForSelector(".cm-content", { timeout: 20000 });
  const before = (await proposals()).length;
  await p.type("#ai-text", request);
  const sel = await p.$("select[aria-label='Tool that performs the task']");
  await sel.select("demo");
  await p.keyboard.down("Control"); await p.keyboard.press("Enter"); await p.keyboard.up("Control");
  const prop = await until(async () => (await proposals()).find((x) => x.status === "ready"));
  if (!prop) throw new Error("no ready proposal");
  return prop;
}

await step("login", async () => {
  await p.goto(B + "/", { waitUntil: "networkidle0" });
  await p.type("input[autocomplete=username]", "admin"); await p.type("input[type=password]", ADMIN_PW); await p.click("button[type=submit]");
  await p.waitForFunction(() => !document.querySelector("input[type=password]"), { timeout: 15000 });
  pid = (await api("POST", "/api/projects", { name: "applyfast-check (temporary)", template: "sample" })).json.id;
});
let first = null;
await step("card in the AI panel offers Apply for a ready proposal; one click applies it", async () => {
  first = await newProposal("Add a short remark to the main file");
  const changed = (await api("GET", `/api/proposals/${first.id}`)).json.files[0].path;
  // show the file that will change, so a stale editor would be visible
  await p.evaluate((changed) => [...document.querySelectorAll(".tree-file button")].find((b) => b.textContent.trim() === changed.split("/").pop())?.click(), changed);
  await sleep(1200);
  if ((await editorText()).includes("DEMO MODE")) throw new Error("editor already shows the change before applying");
  await p.waitForFunction(() => [...document.querySelectorAll(".proposal-card button")].some((b) => b.textContent.trim() === "Apply"), { timeout: 20000 });
  const clicked = await p.evaluate(() => { const b = [...document.querySelectorAll(".proposal-card button")].find((x) => x.textContent.trim() === "Apply"); b?.click(); return !!b; });
  if (!clicked) throw new Error("no Apply button on the card");
  const done = await until(async () => (await proposals()).find((x) => x.id === first.id && x.status === "applied"), 120000);
  if (!done) throw new Error("not applied: " + (await proposals()).map((x) => x.status));
  const onServer = (await api("GET", `/api/projects/${pid}/files/content?path=${encodeURIComponent(changed)}`)).json.text;
  if (!onServer.includes("DEMO MODE")) throw new Error("server text lacks the change");
  if (!(await until(async () => (await editorText()).includes("DEMO MODE"), 20000))) throw new Error("the open editor did not show the applied change");
  return `applied; editor refreshed (${changed})`;
});
await step("the card no longer offers Apply once applied", async () => {
  const gone = await until(async () => p.evaluate(() => ![...document.querySelectorAll(".proposal-card button")].some((b) => b.textContent.trim() === "Apply")), 15000);
  if (!gone) throw new Error("Apply still offered");
});
await step("Review page: ONE click on 'Apply all changes' applies, no confirmation", async () => {
  const prop = await newProposal("Add one more short remark to the main file");
  await p.goto(`${B}/p/${pid}/review/${prop.id}`, { waitUntil: "domcontentloaded" });
  await p.waitForFunction(() => [...document.querySelectorAll("button")].some((b) => b.textContent.trim() === "Apply all changes"), { timeout: 20000 });
  await clickExact("Apply all changes");
  const done = await until(async () => (await proposals()).find((x) => x.id === prop.id && x.status === "applied"), 120000);
  if (!done) throw new Error("not applied after one click: " + (await proposals()).map((x) => x.status));
});
await step("no uncaught page errors", async () => {
  const real = errors.filter((e) => !/cross-origin|Permission denied to access property|was interrupted while the page was loading/i.test(e));
  if (real.length) throw new Error(real.join(" | "));
});
if (pid) console.log("cleanup delete HTTP", (await api("DELETE", `/api/projects/${pid}`)).status);
await browser.close();
process.exit(failed ? 1 : 0);
