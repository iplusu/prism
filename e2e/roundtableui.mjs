// Browser check (Firefox, separate DEMO stack) of the Roundtable: the header button, no scrollbars in the AI panel at desktop and phone size,
// the dialog, and the payload Start would send. The POST is intercepted, so no tool or LLM runs; tool status is faked (UI logic only).
// PRISM_DB (the stack's SQLite file) lets the script insert one discussion message to check how it is laid out (rendering only; no tool or LLM runs).
// usage: PRISM_URL=http://127.0.0.1:8010 PRISM_DB=<sqlite file> node roundtableui.mjs <screenshot dir> <admin password>
import puppeteer from "puppeteer-core";
import { execFileSync } from "node:child_process";

const B = process.env.PRISM_URL;
const [OUT, PW] = process.argv.slice(2);
const PROFILE = `${process.env.HOME}/snap/firefox/common/prism-e2e-profile-${process.pid}`;
const browser = await puppeteer.launch({ browser: "firefox", executablePath: "/usr/bin/firefox", headless: true, userDataDir: PROFILE });
const p = await (await browser.createBrowserContext()).newPage();
const errors = [];
p.on("pageerror", (e) => errors.push(e.message));
await p.evaluateOnNewDocument(() => {
  const real = window.fetch.bind(window);
  window.fetch = (input, init) => {
    const url = typeof input === "string" ? input : input.url;
    if ((init?.method ?? "GET") === "POST" && /\/api\/projects\/[^/]+\/tasks$/.test(url)) {
      (window.__tasks ??= []).push(JSON.parse(init.body));
      return Promise.resolve(new Response(JSON.stringify({ detail: "blocked by test" }), { status: 400, headers: { "Content-Type": "application/json" } }));
    }
    if (/\/api\/system\/status$/.test(url)) {
      return real(input, init).then(async (r) => { const j = await r.json(); j.tools = (j.tools ?? []).map((t) => (["codex", "claude", "antigravity"].includes(t.name) ? { ...t, status: "available", detail: "" } : t)); return new Response(JSON.stringify(j), { status: 200, headers: { "Content-Type": "application/json" } }); });
    }
    return real(input, init);
  };
});
let failed = 0, PID;
async function step(name, fn) { try { console.log("PASS", name, (await fn()) ?? ""); } catch (e) { failed++; await p.screenshot({ path: `${OUT}/rt-fail-${failed}.png` }).catch(() => {}); console.log("FAIL", name, e.message); } }
const btn = (re) => p.evaluate((s) => [...document.querySelectorAll("button")].find((b) => new RegExp(s).test(b.textContent.trim()))?.click(), re);
const overflow = () => p.evaluate(() => {
  const bad = [];
  for (const sel of [".ai-panel", ".ai-input", ".ai-messages", ".panel-head"]) {
    const e = document.querySelector(sel);
    if (!e) continue;
    const cs = getComputedStyle(e);
    if (sel !== ".ai-messages" && e.scrollHeight > e.clientHeight + 1 && /(auto|scroll)/.test(cs.overflowY)) bad.push(`${sel} scrolls (${e.scrollHeight}>${e.clientHeight})`);
    if (e.scrollWidth > e.clientWidth + 1) bad.push(`${sel} overflows horizontally (${e.scrollWidth}>${e.clientWidth})`);
  }
  const d = document.documentElement;
  if (d.scrollWidth > d.clientWidth + 1) bad.push("page scrolls horizontally");
  return bad;
});
await step("login, create the sample project, open the workspace", async () => {
  await p.setViewport({ width: 1500, height: 950 });
  await p.goto(B + "/", { waitUntil: "networkidle0" });
  await p.type("input[autocomplete=username]", "admin"); await p.type("input[type=password]", PW); await p.click("button[type=submit]");
  await p.waitForFunction(() => !document.querySelector("input[type=password]"), { timeout: 15000 });
  PID = await p.evaluate(async () => (await (await fetch("/api/projects", { method: "POST", headers: { "Content-Type": "application/json", "X-Prism-CSRF": "1" }, body: JSON.stringify({ name: "ui check (temporary)", template: "sample" }) })).json()).id);
  if (!PID) throw new Error("no project");
  await p.goto(`${B}/p/${PID}`, { waitUntil: "domcontentloaded" });
  await p.waitForSelector("#ai-text", { timeout: 20000 });
  return PID;
});
for (const [w, h] of [[1500, 950], [1280, 640]]) {
  await step(`desktop ${w}x${h}: Roundtable is in the AI panel header; no scrollbar or overflow in the panel`, async () => {
    await p.setViewport({ width: w, height: h });
    await new Promise((r) => setTimeout(r, 400));
    const inHead = await p.evaluate(() => !![...document.querySelectorAll(".panel-head button")].find((b) => b.textContent.trim() === "Roundtable"));
    if (!inHead) throw new Error("no Roundtable button in the panel header");
    const bad = await overflow();
    await p.screenshot({ path: `${OUT}/rt-desktop-${w}.png` });
    if (bad.length) throw new Error(bad.join("; "));
  });
}
await step("the old buttons are gone (presets, Reviewer report, Tool discussion)", async () => {
  const t = await p.evaluate(() => [...document.querySelectorAll("button")].map((b) => b.textContent.trim()));
  const old = t.filter((x) => /^(Terminology|Missing citations|Reviewer report…|Tool discussion…|Discussion skill…)$/.test(x));
  if (old.length) throw new Error(old.join(", "));
});
await step("the dialog opens with prompt, how, goal, rounds, lead, three tools, Korean; Start needs a prompt", async () => {
  await btn("^Roundtable$");
  await p.waitForSelector(".skill-form", { timeout: 10000 });
  await p.waitForFunction(() => document.querySelectorAll(".skill-form input[type=checkbox]").length >= 6, { timeout: 15000 });
  const disabled = await p.evaluate(() => [...document.querySelectorAll(".skill-form button")].find((b) => /^Start/.test(b.textContent.trim())).disabled);
  if (!disabled) throw new Error("Start is enabled with an empty prompt");
  await p.screenshot({ path: `${OUT}/rt-dialog.png` });
  return (await p.evaluate(() => [...document.querySelectorAll(".skill-form .check-row")].map((l) => l.textContent.trim().replace(/\s+/g, " ")))).join(" | ");
});
await step("Goal has 3 choices; the Review template fills the prompt and switches the proposal on, Terminology leaves it off", async () => {
  const goals = await p.evaluate(() => [...document.querySelectorAll(".skill-form select")][1].options.length);
  if (goals !== 3) throw new Error("goal options: " + goals);
  const state = () => p.evaluate(() => ({ text: document.querySelector(".skill-form textarea").value, apply: [...document.querySelectorAll(".skill-form .check-row")].find((l) => /proposal/.test(l.textContent)).querySelector("input").checked }));
  await btn("^Review the manuscript$");
  const a = await state();
  if (!/peer reviewer/.test(a.text) || !a.apply) throw new Error(JSON.stringify(a));
  await btn("^Terminology$");
  const b = await state();
  if (!/terminology/i.test(b.text) || b.apply) throw new Error(JSON.stringify(b));
  await p.evaluate(() => { const t = document.querySelector(".skill-form textarea"); t.focus(); });
  await p.keyboard.down("Control"); await p.keyboard.press("a"); await p.keyboard.up("Control"); await p.keyboard.press("Backspace");
});
await step("Start sends a discuss task: custom goal, the prompt, 5 rounds, devil's advocate", async () => {
  await p.type(".skill-form textarea", "Is the ablation enough for claim 2?");
  const sels = await p.$$(".skill-form select"); // how, goal, rounds
  await sels[2].select("5");
  await sels[3].select("claude"); // Lead
  await p.evaluate(() => [...document.querySelectorAll(".skill-form .check-row")].find((l) => /devil/.test(l.textContent)).querySelector("input").click());
  await btn("^Start with 3 tools$");
  await new Promise((r) => setTimeout(r, 800));
  const t = await p.evaluate(() => window.__tasks?.at(-1));
  if (!t) throw new Error("no POST");
  const d = t.context?.discuss;
  if (!d || d.focus !== "custom" || d.rounds !== 5 || !!d.persona || d.adversary !== true || d.lead !== "claude" || d.tools.length !== 3 || d.apply !== false) throw new Error(JSON.stringify(t));
  if (!/Is the ablation enough for claim 2\?/.test(t.request) || !/^Roundtable:/.test(t.request)) throw new Error(t.request);
  return JSON.stringify(d);
});
await step("independent reviews mode sends a review task and hides the discussion-only options", async () => {
  const sels = await p.$$(".skill-form select");
  await sels[0].select("reviews");
  const hidden = await p.evaluate(() => !/Rounds|Goal|Lead|Role/.test(document.querySelector(".skill-form").textContent));
  if (!hidden) throw new Error("goal/rounds/lead still shown");
  await btn("^Start with 3 tools$");
  await new Promise((r) => setTimeout(r, 800));
  const t = await p.evaluate(() => window.__tasks?.at(-1));
  if (t.context.review?.persona) throw new Error("persona sent");
  if (!t.context?.review || t.context?.discuss) throw new Error(JSON.stringify(t));
});
if (process.env.PRISM_DB) await step("a discussion message: rounds first and folded, the summary after them", async () => {
  const meta = { kind: "discussion", focus: "custom", lead: "claude", notes: [], rounds: [1, 2, 3].map((r) => [
    { tool: "codex", label: "Codex", status: "succeeded", text: `CODEX-R${r} statement`, error: "", agree: r === 1 ? null : r === 3, round: r },
    { tool: "claude", label: "Claude Code", status: "succeeded", text: `CLAUDE-R${r} statement`, error: "", agree: r === 1 ? null : true, round: r }]) };
  execFileSync("python3", ["-c", "import sqlite3,sys,json; c=sqlite3.connect(sys.argv[1]); c.execute(\"insert into messages (project_id, user_id, role, content, task_id, meta, created_at) values (?, (select id from users where username='admin'), 'assistant', ?, NULL, ?, '2026-11-05 06:00:00.000000')\", (sys.argv[2], '## SUMMARY\\nSUMMARY-TEXT of the report', sys.argv[3])); c.commit()", process.env.PRISM_DB, PID, JSON.stringify(meta)]);
  await p.setViewport({ width: 1500, height: 950 });
  await p.goto(`${B}/p/${PID}`, { waitUntil: "domcontentloaded" });
  await p.waitForSelector(".review-one", { timeout: 20000 });
  const r = await p.evaluate(() => {
    const ds = [...document.querySelectorAll(".review-one")];
    const body = [...document.querySelectorAll(".msg-body")].find((b) => b.textContent.includes("SUMMARY-TEXT"));
    return { n: ds.length, open: ds.filter((d) => d.open).length, before: ds.every((d) => d.compareDocumentPosition(body) & Node.DOCUMENT_POSITION_FOLLOWING), summaries: ds.map((d) => d.querySelector("summary").textContent.trim().replace(/\s+/g, " ")) };
  });
  await p.screenshot({ path: `${OUT}/rt-message.png` });
  if (r.n !== 3 || r.open !== 0 || !r.before) throw new Error(JSON.stringify(r));
  return r.summaries.join(" || ");
});
await step("phone 412x915: AI tab has no horizontal overflow, Roundtable reachable and the dialog fits", async () => {
  await p.keyboard.press("Escape");
  await p.setViewport({ width: 412, height: 915, isMobile: true, hasTouch: true });
  await p.goto(`${B}/p/${PID}`, { waitUntil: "domcontentloaded" });
  await p.waitForSelector('[role=tab]', { timeout: 20000 });
  await p.evaluate(() => [...document.querySelectorAll('[role=tab]')].find((b) => /AI/.test(b.textContent))?.click());
  await p.waitForSelector("#ai-text", { timeout: 10000 });
  const bad = await overflow();
  await p.screenshot({ path: `${OUT}/rt-phone.png` });
  if (bad.length) throw new Error(bad.join("; "));
  await btn("^Roundtable$");
  await p.waitForSelector(".skill-form");
  const fit = await p.evaluate(() => { const m = document.querySelector(".modal"); const r = m.getBoundingClientRect(); return r.left >= 0 && r.right <= innerWidth + 1 && document.documentElement.scrollWidth <= innerWidth + 1; });
  await p.screenshot({ path: `${OUT}/rt-phone-dialog.png` });
  if (!fit) throw new Error("dialog wider than the screen");
});
await step("no page errors", async () => { const real = errors.filter((m) => !/Permission denied to access property/.test(m)); // Firefox/puppeteer automation noise
  if (real.length) throw new Error(real.join("; ")); });
await p.evaluate(async (pid) => fetch(`/api/projects/${pid}`, { method: "DELETE", headers: { "X-Prism-CSRF": "1" } }), PID).catch(() => {});
await browser.close();
console.log(failed ? `${failed} FAILED` : "ALL PASSED");
process.exit(failed ? 1 : 0);
