// Browser check (Firefox, separate DEMO stack) of the Roundtable: the header button, no scrollbars in the AI panel at desktop and phone size,
// the dialog, and the payload Start would send. The POST is intercepted, so no tool or LLM runs; tool status is faked (UI logic only).
// usage: PRISM_URL=http://127.0.0.1:8010 node roundtableui.mjs <screenshot dir> <admin password>
import puppeteer from "puppeteer-core";

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
await step("the dialog opens with prompt, how, goal, rounds, role, three tools, Korean; Start needs a prompt", async () => {
  await btn("^Roundtable$");
  await p.waitForSelector(".skill-form", { timeout: 10000 });
  await p.waitForFunction(() => document.querySelectorAll(".skill-form input[type=checkbox]").length >= 6, { timeout: 15000 });
  const disabled = await p.evaluate(() => [...document.querySelectorAll(".skill-form button")].find((b) => /^Start/.test(b.textContent.trim())).disabled);
  if (!disabled) throw new Error("Start is enabled with an empty prompt");
  await p.screenshot({ path: `${OUT}/rt-dialog.png` });
  return (await p.evaluate(() => [...document.querySelectorAll(".skill-form .check-row")].map((l) => l.textContent.trim().replace(/\s+/g, " ")))).join(" | ");
});
await step("Start sends a discuss task: custom goal, the prompt, 3 rounds, role, devil's advocate", async () => {
  await p.type(".skill-form textarea", "Is the ablation enough for claim 2?");
  const sels = await p.$$(".skill-form select"); // how, goal, rounds
  await sels[2].select("3");
  await p.type('.skill-form input[maxlength="2000"]', "a JAMS referee");
  await p.evaluate(() => [...document.querySelectorAll(".skill-form .check-row")].find((l) => /devil/.test(l.textContent)).querySelector("input").click());
  await btn("^Start with 3 tools$");
  await new Promise((r) => setTimeout(r, 800));
  const t = await p.evaluate(() => window.__tasks?.at(-1));
  if (!t) throw new Error("no POST");
  const d = t.context?.discuss;
  if (!d || d.focus !== "custom" || d.rounds !== 3 || d.persona !== "a JAMS referee" || d.adversary !== true || d.tools.length !== 3 || d.apply !== false) throw new Error(JSON.stringify(t));
  if (!/Is the ablation enough for claim 2\?/.test(t.request) || !/^Roundtable:/.test(t.request)) throw new Error(t.request);
  return JSON.stringify(d);
});
await step("independent reviews mode sends a review task and hides the discussion-only options", async () => {
  const sels = await p.$$(".skill-form select");
  await sels[0].select("reviews");
  const hidden = await p.evaluate(() => !/Rounds|Goal/.test(document.querySelector(".skill-form").textContent));
  if (!hidden) throw new Error("goal/rounds still shown");
  await btn("^Start with 3 tools$");
  await new Promise((r) => setTimeout(r, 800));
  const t = await p.evaluate(() => window.__tasks?.at(-1));
  if (!t.context?.review || t.context?.discuss) throw new Error(JSON.stringify(t));
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
