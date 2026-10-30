// Browser check of AI ghost-text completion against the live orchestrator LLM.
// usage: PRISM_URL=https://host node complete.mjs <screenshot dir> <admin password>
import puppeteer from "puppeteer-core";

const B = process.env.PRISM_URL || "http://127.0.0.1:8000";
const [OUT, ADMIN_PW] = process.argv.slice(2);
const browser = await puppeteer.launch({ browser: "firefox", executablePath: "/usr/bin/firefox", headless: true });
const p = await (await browser.createBrowserContext()).newPage();
await p.setViewport({ width: 1500, height: 950 });
const errors = [];
p.on("pageerror", (e) => errors.push(e.message));
const reqs = [];
p.on("request", (r) => r.url().endsWith("/complete") && reqs.push(r.url()));
let failed = 0, pid = null;
async function step(name, fn) {
  try { console.log("PASS", name, (await fn()) ?? ""); } catch (e) {
    failed++; await p.screenshot({ path: `${OUT}/fail-${failed}.png` }).catch(() => {});
    console.log("FAIL", name, e.message);
  }
}
const ghost = () => p.evaluate(() => document.querySelector(".cm-ghost")?.textContent ?? null);
const docText = () => p.evaluate(() => { const c = document.querySelector(".cm-content").cloneNode(true); c.querySelectorAll(".cm-ghost").forEach((g) => g.remove()); return c.innerText; });
const sleep = (ms) => new Promise((r) => setTimeout(r, ms));

await step("login and create a sample project", async () => {
  await p.goto(B + "/", { waitUntil: "networkidle0" });
  await p.type("input[autocomplete=username]", "admin");
  await p.type("input[type=password]", ADMIN_PW);
  await p.click("button[type=submit]");
  await p.waitForFunction(() => !document.querySelector("input[type=password]"), { timeout: 15000 });
  pid = await p.evaluate(async () => {
    const r = await fetch("/api/projects", { method: "POST", credentials: "same-origin",
      headers: { "Content-Type": "application/json", "X-Prism-CSRF": "1" }, body: JSON.stringify({ name: "ghost-check (temporary)", template: "sample" }) });
    return (await r.json()).id;
  });
  await p.goto(`${B}/p/${pid}`, { waitUntil: "networkidle0" });
  await p.waitForSelector(".cm-content", { timeout: 30000 });
});
await step("toggle is off by default and nothing is requested while typing", async () => {
  const t = await p.$eval(".ai-toggle", (b) => b.textContent);
  if (!/off/.test(t)) throw new Error("toggle text: " + t);
  await p.click(".cm-content");
  await p.keyboard.down("Control"); await p.keyboard.press("End"); await p.keyboard.up("Control");
  await p.keyboard.press("Enter");
  await p.keyboard.type("Gradient descent converges slowly when the step size", { delay: 20 });
  await sleep(2500);
  if (reqs.length) throw new Error("requested while off");
  if (await ghost()) throw new Error("ghost shown while off");
});
let suggestion;
await step("turn on, pause after typing: a greyed suggestion appears", async () => {
  await p.click(".ai-toggle");
  await p.waitForSelector("select.ai-model option", { timeout: 15000 });
  const opts = await p.$$eval("select.ai-model option", (o) => o.map((x) => x.textContent));
  if (!opts[0].startsWith("Default (qwen38")) throw new Error("model options: " + opts);
  await p.click(".cm-content");
  await p.keyboard.down("Control"); await p.keyboard.press("End"); await p.keyboard.up("Control");
  await p.keyboard.type(" is", { delay: 20 });
  await p.keyboard.type(" too", { delay: 20 });
  await p.waitForFunction(() => document.querySelector(".cm-ghost"), { timeout: 60000 });
  suggestion = await ghost();
  await p.screenshot({ path: `${OUT}/g1-ghost.png` });
  return JSON.stringify(suggestion);
});
await step("Tab accepts the suggestion into the document", async () => {
  await p.keyboard.press("Tab");
  await sleep(300);
  if (await ghost()) throw new Error("ghost still visible");
  const t = await docText();
  if (!t.includes("step size is too" + suggestion.trimEnd()) && !t.includes(suggestion.trim())) throw new Error("not inserted: " + t.slice(-120));
  await p.screenshot({ path: `${OUT}/g2-accepted.png` });
});
await step("Esc dismisses a suggestion without changing the text", async () => {
  await p.keyboard.press("Enter");
  await p.keyboard.type("We evaluate the method on three", { delay: 20 });
  await p.waitForFunction(() => document.querySelector(".cm-ghost"), { timeout: 60000 });
  const before = await docText();
  await p.keyboard.press("Escape");
  await sleep(200);
  if (await ghost()) throw new Error("ghost still visible");
  if ((await docText()) !== before) throw new Error("text changed");
});
await step("typing on discards the suggestion; turning off stops requests", async () => {
  await p.keyboard.type(" datasets", { delay: 20 });
  if (await ghost()) throw new Error("ghost survived typing");
  await p.click(".ai-toggle");
  const n = reqs.length;
  await p.click(".cm-content");
  await p.keyboard.down("Control"); await p.keyboard.press("End"); await p.keyboard.up("Control");
  await p.keyboard.type(" and then", { delay: 20 });
  await sleep(2500);
  if (reqs.length !== n) throw new Error("request sent while off");
});
await step("no uncaught page errors", async () => {
  const real = errors.filter((e) => !/cross-origin|Permission denied to access property/i.test(e));
  if (real.length) throw new Error(real.join(" | "));
});
if (pid) console.log("cleanup delete HTTP", await p.evaluate(async (id) => (await fetch(`/api/projects/${id}`, { method: "DELETE", credentials: "same-origin", headers: { "X-Prism-CSRF": "1" } })).status, pid));
await browser.close();
process.exit(failed ? 1 : 0);
