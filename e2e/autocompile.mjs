// Browser check of Auto compile on a real build: no Compile click, no retry loop on a failing build, off means off.
// usage: PRISM_URL=https://host node autocompile.mjs <screenshot dir> <admin password>
import puppeteer from "puppeteer-core";

const B = process.env.PRISM_URL || "http://127.0.0.1:8000";
const [OUT, ADMIN_PW] = process.argv.slice(2);
const browser = await puppeteer.launch({ browser: "firefox", executablePath: "/usr/bin/firefox", headless: true });
const p = await (await browser.createBrowserContext()).newPage();
await p.setViewport({ width: 1500, height: 950 });
const errors = [];
p.on("pageerror", (e) => errors.push(e.message));
let failed = 0, pid = null;
const sleep = (ms) => new Promise((r) => setTimeout(r, ms));
async function step(name, fn) {
  try { console.log("PASS", name, (await fn()) ?? ""); } catch (e) {
    failed++; await p.screenshot({ path: `${OUT}/ac-fail-${failed}.png` }).catch(() => {});
    console.log("FAIL", name, e.message);
  }
}
const builds = () => p.evaluate(async (id) => (await (await fetch(`/api/projects/${id}/builds?limit=50`, { credentials: "same-origin" })).json()), pid);
const endOfDoc = async () => {
  await p.click(".cm-content");
  await p.keyboard.down("Control"); await p.keyboard.press("End"); await p.keyboard.up("Control");
};
async function waitBuild(n, ms = 90000) {
  const t0 = Date.now();
  for (;;) {
    const b = await builds();
    if (b.length >= n && !["queued", "running"].includes(b[0].status)) return b;
    if (Date.now() - t0 > ms) throw new Error(`no finished build #${n}; have ${b.map((x) => x.status)}`);
    await sleep(1500);
  }
}

await step("login, sample project, no build yet, Auto is off", async () => {
  await p.goto(B + "/", { waitUntil: "networkidle0" });
  await p.type("input[autocomplete=username]", "admin");
  await p.type("input[type=password]", ADMIN_PW);
  await p.click("button[type=submit]");
  await p.waitForFunction(() => !document.querySelector("input[type=password]"), { timeout: 15000 });
  pid = await p.evaluate(async () => (await (await fetch("/api/projects", { method: "POST", credentials: "same-origin",
    headers: { "Content-Type": "application/json", "X-Prism-CSRF": "1" }, body: JSON.stringify({ name: "autocompile-check (temporary)", template: "sample" }) })).json()).id);
  await p.goto(`${B}/p/${pid}`, { waitUntil: "networkidle0" });
  await p.waitForSelector(".cm-content", { timeout: 30000 });
  const t = await p.$eval(".auto-compile", (b) => b.textContent);
  if (!/off/.test(t)) throw new Error(t);
  await sleep(8000);
  if ((await builds()).length) throw new Error("built while off");
});
await step("Auto on: the paper compiles by itself and a PDF appears (no Compile click)", async () => {
  await p.click(".auto-compile");
  const b = await waitBuild(1);
  if (b[0].status !== "succeeded") throw new Error("first auto build: " + b[0].status);
  await p.waitForSelector(".pdf-page canvas", { timeout: 30000 });
  return `build ${b[0].status}`;
});
await step("editing triggers exactly one new build after the pause", async () => {
  await endOfDoc();
  await p.keyboard.press("Enter");
  await p.keyboard.type("An extra sentence typed while auto compile is on.", { delay: 15 });
  const b = await waitBuild(2);
  await sleep(10000);
  const after = await builds();
  if (after.length !== 2) throw new Error(`expected 2 builds, got ${after.length}`);
  if (b[0].status !== "succeeded") throw new Error(b[0].status);
});
await step("a broken edit gives ONE failed build, not a retry loop", async () => {
  await p.click(".cm-content");
  await p.keyboard.down("Control"); await p.keyboard.press("Home"); await p.keyboard.up("Control");
  await p.keyboard.type("\\undefinedcommandxyz ", { delay: 15 }); // before \documentclass: a real LaTeX error
  const b = await waitBuild(3);
  if (b[0].status === "succeeded") throw new Error("the broken text compiled; the test would prove nothing");
  await sleep(15000);
  const after = await builds();
  if (after.length !== 3) throw new Error(`expected 3 builds, got ${after.length}`);
  return `last build ${b[0].status}`;
});
await step("Auto off: edits do not compile", async () => {
  await p.click(".auto-compile");
  const n = (await builds()).length;
  await endOfDoc();
  await p.keyboard.type(" more", { delay: 15 });
  await sleep(9000);
  if ((await builds()).length !== n) throw new Error("built while off");
});
await step("no uncaught page errors", async () => {
  const real = errors.filter((e) => !/cross-origin|Permission denied to access property/i.test(e));
  if (real.length) throw new Error(real.join(" | "));
});
if (pid) console.log("cleanup delete HTTP", await p.evaluate(async (id) => (await fetch(`/api/projects/${id}`, { method: "DELETE", credentials: "same-origin", headers: { "X-Prism-CSRF": "1" } })).status, pid));
await browser.close();
process.exit(failed ? 1 : 0);
