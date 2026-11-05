// Browser check (Firefox, separate DEMO stack) of: the Tool discussion panel, a rendered discussion message,
// the Tidy button and the changes-marked PDF button. The discussion message is INSERTED by the caller (rendering only);
// the POST that Start would send is intercepted, so no tool or LLM runs.
// usage: PRISM_URL=http://127.0.0.1:8010 INS="<cmd inserting the message, gets PID as argument>" node discussui.mjs <screenshot dir> <admin password>
import puppeteer from "puppeteer-core";
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
await step("a discussion message renders: summary, one expandable block per round, agree marks", async () => {
  execSync(process.env.INS + " " + PID, { stdio: "pipe" });
  await p.evaluate(() => (window.__block = false));
  await p.reload({ waitUntil: "domcontentloaded" });
  await p.waitForSelector(".review-reports", { timeout: 20000 });
  const n = await p.$$eval(".review-one", (d) => d.length);
  if (n !== 2) throw new Error("rounds shown: " + n);
  const sum = await p.$$eval(".review-one summary", (s) => s.map((x) => x.textContent.trim().replace(/\s+/g, " ")));
  await p.evaluate(() => document.querySelectorAll(".review-one summary")[1].click());
  await p.waitForFunction(() => document.querySelector(".review-one[open]")?.innerText.includes("CLAUDE-R2 still disagree"), { timeout: 5000 });
  if (!(await p.evaluate(() => document.body.innerText.includes("MODERATOR-SUMMARY")))) throw new Error("moderator summary missing");
  await p.screenshot({ path: `${OUT}/ds-message.png` });
  return sum.join(" || ");
});
await step("Tidy button on main.tex opens a tidy proposal (or says it is already tidy)", async () => {
  await btn("^Tidy$");
  await p.waitForFunction(() => /\/review\//.test(location.pathname) || /already tidy|nothing to tidy|failed|error/i.test(document.querySelector(".error, [role=alert]")?.innerText ?? ""), { timeout: 60000 });
  await p.screenshot({ path: `${OUT}/ds-tidy.png` });
  return new URL(p.url()).pathname + (/\/review\//.test(p.url()) ? " " + (await p.evaluate(() => /tidy \(latexindent\)/.test(document.body.innerText) ? "label shown" : "no label")) : " no navigation: " + (await p.evaluate(() => document.querySelector(".error, [role=alert]")?.innerText)));
});
await step("Show changes marked (latexdiff) builds and shows a PDF", async () => {
  if (!/\/review\//.test(p.url())) throw new Error("not on a review page (Tidy produced no proposal)");
  await p.evaluate(() => [...document.querySelectorAll('[role=tab], button')].find((b) => b.textContent.trim() === "Candidate PDF")?.click());
  await p.waitForFunction(() => [...document.querySelectorAll("button")].some((b) => /Show changes marked/.test(b.textContent)), { timeout: 120000 });
  await p.screenshot({ path: `${OUT}/ds-candidate.png` });
  await btn("Show changes marked");
  await p.waitForFunction(() => [...document.querySelectorAll("button")].some((b) => /Show the plain candidate PDF/.test(b.textContent)) || !!document.querySelector(".error-box.soft"), { timeout: 180000 });
  const err = await p.evaluate(() => document.querySelector(".error-box.soft")?.innerText);
  if (err) throw new Error(err);
  await p.waitForSelector("canvas", { timeout: 30000 });
  await new Promise((r) => setTimeout(r, 3000));
  await p.screenshot({ path: `${OUT}/ds-marked.png` });
  return await p.evaluate(async () => { const id = location.pathname.split("/").pop(); const r = await fetch(`/api/proposals/${id}/marked-pdf`); const b = new Uint8Array(await r.arrayBuffer()); return `GET marked-pdf ${r.status}, ${b.length} bytes, header ${String.fromCharCode(...b.slice(0, 5))}`; });
});
await step("no uncaught page errors", async () => {
  const real = errors.filter((e) => !/cross-origin|Permission denied to access property|was interrupted while the page was loading/i.test(e));
  if (real.length) throw new Error(real.join(" | "));
});
await p.evaluate(async (pid) => fetch(`/api/projects/${pid}`, { method: "DELETE", headers: { "X-Prism-CSRF": "1" } }), PID).catch(() => {});
await browser.close();
execSync(`rm -rf ${PROFILE}`);
process.exit(failed ? 1 : 0);
