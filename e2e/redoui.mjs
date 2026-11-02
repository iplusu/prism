// Browser check (Firefox, separate DEMO stack) of "Redo on the current manuscript" on a conflicted proposal.
// A tidy proposal re-indents the abstract; the abstract is then edited in the live file, so applying the proposal conflicts.
// usage: PRISM_URL=http://127.0.0.1:8010 node redoui.mjs <screenshot dir> <admin password>
import puppeteer from "puppeteer-core";
import { execSync } from "node:child_process";

const B = process.env.PRISM_URL;
const [OUT, PW] = process.argv.slice(2);
const PROFILE = `${process.env.HOME}/snap/firefox/common/prism-e2e-profile-${process.pid}`;
const browser = await puppeteer.launch({ browser: "firefox", executablePath: "/usr/bin/firefox", headless: true, userDataDir: PROFILE });
const p = await (await browser.createBrowserContext()).newPage();
await p.setViewport({ width: 1500, height: 950 });
const errors = [];
p.on("pageerror", (e) => errors.push(e.message));
let failed = 0, PID, PROP;
async function step(name, fn) { try { console.log("PASS", name, (await fn()) ?? ""); } catch (e) { failed++; await p.screenshot({ path: `${OUT}/redo-fail-${failed}.png` }).catch(() => {}); console.log("FAIL", name, e.message); } }
const api = (method, path, body) => p.evaluate(async (method, path, body) => {
  const r = await fetch(path, { method, headers: { "Content-Type": "application/json", "X-Prism-CSRF": "1" }, body: body ? JSON.stringify(body) : undefined });
  return { status: r.status, json: await r.json().catch(() => null) };
}, method, path, body);
await step("login and create the sample project", async () => {
  await p.goto(B + "/", { waitUntil: "networkidle0" });
  await p.type("input[autocomplete=username]", "admin"); await p.type("input[type=password]", PW); await p.click("button[type=submit]");
  await p.waitForFunction(() => !document.querySelector("input[type=password]"), { timeout: 15000 });
  PID = (await api("POST", "/api/projects", { name: "redo check (temporary)", template: "sample" })).json.id;
  return PID;
});
await step("make a conflicted proposal (tidy, then an overlapping live edit, then apply)", async () => {
  const t = await api("POST", `/api/projects/${PID}/tidy`, { path: "main.tex" });
  PROP = t.json?.id;
  if (!PROP) throw new Error("tidy: " + JSON.stringify(t));
  const c = (await api("GET", `/api/projects/${PID}/files/content?path=main.tex`)).json;
  const text = c.text.replace("We study an adaptive step-size rule", "We study a fast adaptive step-size rule");
  const s = await api("PUT", `/api/projects/${PID}/files/content`, { path: "main.tex", text, base_sha: c.sha });
  if (s.status !== 200) throw new Error("save " + s.status);
  for (let i = 0; i < 60; i++) { const r = (await api("GET", `/api/proposals/${PROP}`)).json; if (r.status === "ready") break; await new Promise((x) => setTimeout(x, 1000)); }
  await api("POST", `/api/proposals/${PROP}/apply`);
  let st = "";
  for (let i = 0; i < 60; i++) { st = (await api("GET", `/api/proposals/${PROP}`)).json.status; if (st === "conflict") break; await new Promise((x) => setTimeout(x, 1000)); }
  if (st !== "conflict") throw new Error("status is " + st);
  return st;
});
await step("the Conflicts tab shows the Redo button", async () => {
  await p.goto(`${B}/p/${PID}/review/${PROP}`, { waitUntil: "domcontentloaded" });
  await p.waitForFunction(() => [...document.querySelectorAll("button")].some((b) => /Redo on the current manuscript/.test(b.textContent)), { timeout: 30000 });
  await p.screenshot({ path: `${OUT}/redo-conflict.png` });
});
await step("clicking it queues a task with the same request and returns to the workspace", async () => {
  await p.evaluate(() => [...document.querySelectorAll("button")].find((b) => /Redo on the current manuscript/.test(b.textContent)).click());
  await p.waitForFunction((pid) => location.pathname === `/p/${pid}`, { timeout: 20000 }, PID);
  const tasks = (await api("GET", `/api/projects/${PID}/tasks`)).json;
  const t = tasks.find((x) => /REDO ON THE CURRENT MANUSCRIPT/.test(x.request));
  if (!t) throw new Error("no redo task: " + JSON.stringify(tasks).slice(0, 300));
  const still = (await api("GET", `/api/proposals/${PROP}`)).json.status;
  await new Promise((x) => setTimeout(x, 2000));
  await p.screenshot({ path: `${OUT}/redo-workspace.png` });
  return `task ${t.id.slice(0, 8)} mode=${t.mode} status=${t.status}; old proposal still ${still}`;
});
await step("no uncaught page errors", async () => {
  const real = errors.filter((e) => !/cross-origin|Permission denied to access property|was interrupted while the page was loading/i.test(e));
  if (real.length) throw new Error(real.join(" | "));
});
await api("DELETE", `/api/projects/${PID}`).catch(() => {});
await browser.close();
execSync(`rm -rf ${PROFILE}`);
process.exit(failed ? 1 : 0);
