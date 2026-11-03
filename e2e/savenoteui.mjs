// Browser check (Firefox, separate DEMO stack) of "Save as file" on a discussion message. The message is INSERTED by the
// caller (INS command, gets the project id as argument); nothing is run by a model.
// usage: PRISM_URL=http://127.0.0.1:8010 INS="<cmd>" node savenoteui.mjs <screenshot dir> <admin password>
import puppeteer from "puppeteer-core";
import { rmSync } from "node:fs";
import { execSync } from "node:child_process";

const B = process.env.PRISM_URL;
// (the real status of the tools on the deployed site is shown by e2e/reviewui.mjs)
const [OUT, PW] = process.argv.slice(2);
// the snap build of Firefox cannot use a profile under /tmp: keep it in a folder the snap may write
const PROFILE = `${process.env.HOME}/snap/firefox/common/prism-e2e-profile-${process.pid}`;
const browser = await puppeteer.launch({ browser: "firefox", executablePath: "/usr/bin/firefox", headless: true, userDataDir: PROFILE });
const p = await (await browser.createBrowserContext()).newPage();
await p.setViewport({ width: 1500, height: 950 });
const errors = [];
p.on("pageerror", (e) => errors.push(e.message));
await p.evaluateOnNewDocument(() => {
  const real = window.fetch.bind(window);
  window.fetch = (input, init) => {
    const url = typeof input === "string" ? input : input.url;
    if (window.__block && (init?.method ?? "GET") === "POST" && /\/api\/projects\/[^/]+\/tasks$/.test(url)) {
      window.__task = JSON.parse(init.body);
      return Promise.resolve(new Response(JSON.stringify({ detail: "blocked by test" }), { status: 400, headers: { "Content-Type": "application/json" } }));
    }
    if (/\/api\/system\/status$/.test(url) && window.__fakeTools) {
      // UI-logic test only: the separate stack has no tool logins, so report the three tools as available
      return real(input, init).then(async (r) => { const j = await r.json(); j.tools = (j.tools ?? []).map((t) => (["codex", "claude", "antigravity"].includes(t.name) ? { ...t, status: "available", detail: "" } : t)); return new Response(JSON.stringify(j), { status: 200, headers: { "Content-Type": "application/json" } }); });
    }
    return real(input, init);
  };
  window.__fakeTools = true;
});
let failed = 0, PID;
async function step(name, fn) { try { console.log("PASS", name, (await fn()) ?? ""); } catch (e) { failed++; await p.screenshot({ path: `${OUT}/ds-fail-${failed}.png` }).catch(() => {}); console.log("FAIL", name, e.message); } }
const btn = (re) => p.evaluate((s) => [...document.querySelectorAll("button")].find((b) => new RegExp(s).test(b.textContent.trim()))?.click(), re);
await step("login, create the sample project, open the workspace", async () => {
  await p.goto(B + "/", { waitUntil: "networkidle0" });
  await p.type("input[autocomplete=username]", "admin"); await p.type("input[type=password]", PW); await p.click("button[type=submit]");
  await p.waitForFunction(() => !document.querySelector("input[type=password]"), { timeout: 15000 });
  PID = await p.evaluate(async () => (await (await fetch("/api/projects", { method: "POST", headers: { "Content-Type": "application/json", "X-Prism-CSRF": "1" }, body: JSON.stringify({ name: "ui check (temporary)", template: "sample" }) })).json()).id);
  if (!PID) throw new Error("no project");
  await p.goto(`${B}/p/${PID}`, { waitUntil: "domcontentloaded" });
  await p.waitForSelector("#ai-text", { timeout: 20000 });
  return PID;
});
await step("Save as file creates notes/<topic>-<time>.md with the report; the manuscript is untouched", async () => {
  const get = (path) => p.evaluate(async (pid, path) => { const r = await fetch(`/api/projects/${pid}/files/content?path=${encodeURIComponent(path)}`); return r.ok ? (await r.json()) : null; }, PID, path);
  const before = (await get("main.tex")).sha;
  execSync(process.env.INS + " " + PID, { stdio: "pipe" });
  await p.reload({ waitUntil: "domcontentloaded" });
  await p.waitForSelector("#ai-text", { timeout: 20000 });
  await p.waitForFunction(() => [...document.querySelectorAll("button")].some((b) => b.textContent.trim() === "Save as file"), { timeout: 15000 });
  await btn("^Save as file$");
  await p.waitForFunction(() => [...document.querySelectorAll("button")].some((b) => b.textContent.trim() === "Saved"), { timeout: 15000 });
  const where = await p.evaluate(() => document.body.innerText.match(/notes\/review-\d{8}-\d{6}\.md/)?.[0]);
  if (!where) throw new Error("no path shown");
  const f = await get(where);
  if (!f || !f.text.includes("MODERATOR-SUMMARY") || !f.text.startsWith("# Tool discussion (review)")) throw new Error("file content: " + JSON.stringify(f).slice(0, 200));
  const after = (await get("main.tex")).sha;
  if (before !== after) throw new Error("main.tex changed");
  await p.screenshot({ path: `${OUT}/sv-saved.png` });
  const disabled = await p.evaluate(() => [...document.querySelectorAll("button")].find((b) => b.textContent.trim() === "Saved")?.disabled);
  return `${where}, ${f.text.length} chars, main.tex unchanged, button disabled after saving: ${disabled}`;
});
await step("the file appears in the file tree", async () => {
  await p.waitForFunction(() => document.body.innerText.includes("notes"), { timeout: 8000 });
  await p.evaluate(() => [...document.querySelectorAll("*")].find((e) => e.children.length === 0 && e.textContent.trim() === "notes")?.click());
  await p.waitForFunction(() => /review-\d{8}-\d{6}\.md/.test(document.querySelector(".workspace")?.innerText ?? ""), { timeout: 8000 });
});
await p.evaluate((pid) => fetch(`/api/projects/${pid}`, { method: "DELETE", headers: { "X-Prism-CSRF": "1" } }), PID).catch(() => {});
const real = errors.filter((e) => !/cross-origin|Permission denied to access property|was interrupted while the page was loading/i.test(e));
if (real.length) { failed++; console.log("FAIL page errors:", real.slice(0, 3)); }
await browser.close();
rmSync(PROFILE, { recursive: true, force: true });
console.log(failed ? `${failed} step(s) failed` : "all steps passed");
process.exit(failed ? 1 : 0);
