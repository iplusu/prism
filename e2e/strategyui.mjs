// Browser check (Firefox, separate DEMO stack) of the Strategy option in the Tool discussion panel. The POST that Start
// sends is intercepted, so no tool or LLM runs.
// usage: PRISM_URL=http://127.0.0.1:8010 node strategyui.mjs <screenshot dir> <admin password>
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
await step("Tool discussion… opens: three tools with real status, topic, rounds, apply, role", async () => {
  await btn("^Tool discussion…$");
  await p.waitForFunction(() => document.querySelectorAll('[aria-label="Tool discussion"] input[type=checkbox]').length >= 4, { timeout: 15000 });
  const labels = await p.evaluate(() => [...document.querySelectorAll('[aria-label="Tool discussion"] .check-row')].map((l) => l.textContent.trim().replace(/\s+/g, " ")));
  await p.screenshot({ path: `${OUT}/ds-setup.png` });
  return labels.join(" | ");
});
await step("the Topic list offers Strategy; picking it and pressing Start sends focus=strategy with the strategy title", async () => {
  const sels = await p.$$('[aria-label="Tool discussion"] select');
  const opts = await sels[0].evaluate((s) => [...s.options].map((o) => o.value + ": " + o.textContent.trim()));
  if (!opts.some((o) => o.startsWith("strategy:"))) throw new Error("no strategy option: " + opts.join(" | "));
  await sels[0].select("strategy");
  await p.evaluate(() => (window.__block = true));
  await p.screenshot({ path: `${OUT}/st-setup.png` });
  const b = await p.evaluate(() => { const x = [...document.querySelectorAll("button")].find((y) => /^Start with/.test(y.textContent.trim())); return x ? [x.textContent.trim(), x.disabled] : null; });
  if (!b || b[1]) throw new Error("Start missing or disabled: " + JSON.stringify(b));
  await btn("^Start with");
  await p.waitForFunction(() => !!window.__task, { timeout: 5000 });
  const t = await p.evaluate(() => window.__task);
  if (t.context?.discuss?.focus !== "strategy" || !/strategy/i.test(t.request)) throw new Error(JSON.stringify(t).slice(0, 300));
  return opts.join(" | ") + " => " + JSON.stringify(t.context.discuss) + " title=" + t.request;
});
await step("the server accepts focus=strategy (real POST, then cancelled)", async () => {
  await p.evaluate(() => (window.__block = false));
  const r = await p.evaluate(async (pid) => {
    const h = { "Content-Type": "application/json", "X-Prism-CSRF": "1" };
    const c = await fetch(`/api/projects/${pid}/tasks`, { method: "POST", headers: h, body: JSON.stringify({ request: "x", mode: "llm", context: { scope: "project", discuss: { focus: "strategy" } } }) });
    const j = await c.json();
    if (j.id) await fetch(`/api/tasks/${j.id}/cancel`, { method: "POST", headers: h });
    return [c.status, j.id ? "queued" : JSON.stringify(j).slice(0, 100)];
  }, PID);
  if (r[0] !== 200) throw new Error(r.join(" "));
  return r.join(" ");
});
await p.evaluate((pid) => fetch(`/api/projects/${pid}`, { method: "DELETE", headers: { "X-Prism-CSRF": "1" } }), PID).catch(() => {});
const real = errors.filter((e) => !/cross-origin|Permission denied to access property|was interrupted while the page was loading/i.test(e));
if (real.length) { failed++; console.log("FAIL page errors:", real.slice(0, 3)); }
await browser.close();
rmSync(PROFILE, { recursive: true, force: true });
console.log(failed ? `${failed} step(s) failed` : "all steps passed");
process.exit(failed ? 1 : 0);
