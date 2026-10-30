// Browser check of the Reviewer report UI on the deployed site (no tool or LLM runs):
// (1) the setup panel lists the three tools with their real status, takes a persona, and the task it
//     creates carries tools + persona (the POST is intercepted in the page);
// (2) a merged report message with per-tool reviews renders the merged text and one expandable review per tool.
//     For (2) a chat message with that shape is INSERTED into a temporary project by this script's caller
//     (env REVIEW_MSG_SQL_DONE=1 tells the script it was done); it only checks rendering, not content quality.
// usage: PRISM_URL=https://host PID=<temp project id> node reviewui.mjs <screenshot dir> <admin password>
import puppeteer from "puppeteer-core";

const B = process.env.PRISM_URL, PID = process.env.PID;
const [OUT, ADMIN_PW] = process.argv.slice(2);
const browser = await puppeteer.launch({ browser: "firefox", executablePath: "/usr/bin/firefox", headless: true });
const p = await (await browser.createBrowserContext()).newPage();
await p.setViewport({ width: 1500, height: 950 });
const errors = [];
p.on("pageerror", (e) => errors.push(e.message));
await p.evaluateOnNewDocument(() => {
  const real = window.fetch.bind(window);
  window.fetch = (input, init) => {
    const url = typeof input === "string" ? input : input.url;
    if ((init?.method ?? "GET") === "POST" && /\/api\/projects\/[^/]+\/tasks$/.test(url)) {
      window.__task = JSON.parse(init.body);
      return Promise.resolve(new Response(JSON.stringify({ detail: "blocked by test" }), { status: 400, headers: { "Content-Type": "application/json" } }));
    }
    return real(input, init);
  };
});
let failed = 0;
async function step(name, fn) { try { console.log("PASS", name, (await fn()) ?? ""); } catch (e) { failed++; await p.screenshot({ path: `${OUT}/rv-fail-${failed}.png` }).catch(() => {}); console.log("FAIL", name, e.message); } }
await step("login + workspace", async () => {
  await p.goto(B + "/", { waitUntil: "networkidle0" });
  await p.type("input[autocomplete=username]", "admin"); await p.type("input[type=password]", ADMIN_PW); await p.click("button[type=submit]");
  await p.waitForFunction(() => !document.querySelector("input[type=password]"), { timeout: 15000 });
  await p.goto(`${B}/p/${PID}`, { waitUntil: "domcontentloaded" });
  await p.waitForSelector("#ai-text", { timeout: 20000 });
});
await step("Reviewer report… opens the panel with the three tools and their status", async () => {
  await p.evaluate(() => [...document.querySelectorAll("button")].find((b) => b.textContent.trim() === "Reviewer report…")?.click());
  await p.waitForFunction(() => document.querySelectorAll(".review-setup input[type=checkbox]").length === 3, { timeout: 15000 });
  const labels = await p.evaluate(() => [...document.querySelectorAll(".review-setup .check-row")].map((l) => l.textContent.trim().replace(/\s+/g, " ")));
  await p.screenshot({ path: `${OUT}/rv-setup.png` });
  return labels.join(" | ");
});
await step("persona + Run creates a review task with the chosen tools and the persona", async () => {
  await p.type(".review-setup input[maxlength='2000']", "a strict statistician");
  await p.evaluate(() => [...document.querySelectorAll(".review-setup input[type=checkbox]")][2].click()); // untick the third tool
  await p.evaluate(() => [...document.querySelectorAll("button")].find((b) => /^Run 2 reviewer/.test(b.textContent.trim()))?.click());
  await p.waitForFunction(() => !!window.__task, { timeout: 5000 });
  const t = await p.evaluate(() => window.__task);
  const r = t.context?.review;
  if (t.mode !== "llm" || !r || r.persona !== "a strict statistician" || r.tools.length !== 2 || t.context.scope !== "project") throw new Error(JSON.stringify(t).slice(0, 300));
  return JSON.stringify(r);
});
if (process.env.REVIEW_MSG_SQL_DONE) {
  await step("a merged report renders with one expandable review per tool", async () => {
    await p.reload({ waitUntil: "domcontentloaded" });
    await p.waitForSelector(".review-reports", { timeout: 20000 });
    const n = await p.$$eval(".review-one", (d) => d.length);
    if (n !== 3) throw new Error("reviews shown: " + n);
    await p.evaluate(() => document.querySelector(".review-one summary").click());
    await p.waitForFunction(() => document.querySelector(".review-one[open]")?.innerText.includes("REVIEW-TEXT"), { timeout: 5000 });
    await p.screenshot({ path: `${OUT}/rv-report.png` });
  });
}
await step("no uncaught page errors", async () => {
  const real = errors.filter((e) => !/cross-origin|Permission denied to access property|was interrupted while the page was loading/i.test(e));
  if (real.length) throw new Error(real.join(" | "));
});
await browser.close();
process.exit(failed ? 1 : 0);
