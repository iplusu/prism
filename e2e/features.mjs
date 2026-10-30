// Browser check of the template picker, References (search + add by id) and Check tabs.
// usage: PRISM_URL=https://host node features.mjs <screenshot dir> <admin password>
// Needs /usr/bin/firefox; with snap Firefox set TMPDIR to a directory under $HOME.
// Uses the live arXiv / doi.org services (through the server), so it needs internet access there.
import puppeteer from "puppeteer-core";

const B = process.env.PRISM_URL || "http://127.0.0.1:8000";
const [OUT, ADMIN_PW] = process.argv.slice(2);
const sleep = (ms) => new Promise((r) => setTimeout(r, ms));
const browser = await puppeteer.launch({ browser: "firefox", executablePath: "/usr/bin/firefox", headless: true });
const ctx = await browser.createBrowserContext();
const p = await ctx.newPage();
await p.setViewport({ width: 1500, height: 950 });
const errors = [];
p.on("pageerror", (e) => errors.push(e.message));
const shot = (n) => p.screenshot({ path: `${OUT}/${n}.png` });
const bodyHas = (re, timeout = 90000) => p.waitForFunction((src) => new RegExp(src).test(document.body.innerText), { timeout }, re);
let failed = 0;
async function step(name, fn) {
  try { console.log("PASS", name, (await fn()) ?? ""); } catch (e) {
    failed++;
    const n = `fail-${failed}`;
    await shot(n).catch(() => {});
    const shown = await p.evaluate(() => [...document.querySelectorAll("[role=alert], .error, .error-box")].map((x) => x.textContent.trim()).join(" | ")).catch(() => "");
    const ref = await p.evaluate(() => document.querySelector("#ref-id")?.value ?? null).catch(() => null);
    console.log("FAIL", name, e.message, `| on-screen error: ${shown || "(none)"} | #ref-id=${JSON.stringify(ref)} | screenshot ${n}.png`);
  }
}
async function api(method, path) {
  return p.evaluate(async (method, path) => {
    const r = await fetch(path, { method, headers: { "X-Prism-CSRF": "1" } });
    return r.status;
  }, method, path);
}
async function clickText(sel, text, exact = true) {
  const ok = await p.evaluate((sel, text, exact) => {
    const b = [...document.querySelectorAll(sel)].find((x) => (exact ? x.textContent.trim() === text : x.textContent.trim().startsWith(text)) && !x.disabled);
    if (b) b.click();
    return !!b;
  }, sel, text, exact);
  if (!ok) throw new Error(`no ${sel} with text ${text}`);
}

let pid = null;
await step("login", async () => {
  await p.goto(B + "/", { waitUntil: "networkidle0" });
  await p.waitForSelector("input[type=password]");
  await p.type("input[autocomplete=username]", "admin");
  await p.type("input[type=password]", ADMIN_PW);
  await p.click("button[type=submit]");
  await p.waitForFunction(() => !document.querySelector("input[type=password]"), { timeout: 15000 });
});
await step("new-project dialog lists the official templates and names their source", async () => {
  await clickText("button, a", "New project", false);
  await p.waitForSelector("input[name=tpl]");
  await bodyHas("ACL / \\*ACL[\\s\\S]*CVPR / ICCV[\\s\\S]*NeurIPS 2025[\\s\\S]*ICML 2026[\\s\\S]*arXiv preprint");
  await p.evaluate(() => [...document.querySelectorAll("label.radio")].find((l) => l.textContent.includes("arXiv preprint")).querySelector("input").click());
  await bodyHas("downloaded from github.com/kourgeorge/arxiv-style");
  await shot("f1-template-dialog");
});
await step("create project from the arXiv template and open the workspace", async () => {
  await p.type("input[required]", "feature-check (temporary)");
  await clickText("button", "Create");
  try {
    await p.waitForFunction(() => location.pathname.startsWith("/p/"), { timeout: 90000 });
  } catch (e) {
    await shot("f-create-failed");
    const shown = await p.evaluate(() => [...document.querySelectorAll("[role=alert], .error, .error-box")].map((x) => x.textContent.trim()).join(" | "));
    throw new Error(`no navigation; on-screen error: ${shown || "(none)"}`);
  }
  pid = await p.evaluate(() => location.pathname.split("/")[2]);
  await p.waitForSelector(".cm-content", { timeout: 30000 });
  return pid;
});
await step("References tab: add by arXiv id", async () => {
  await clickText("[role=tab]", "Refs", false);
  await p.waitForSelector("#ref-id");
  await p.type("#ref-id", "1706.03762");
  await clickText("button", "Add");
  await bodyHas("Added[\\s\\S]*as vaswani");
  await shot("f2-reference-added");
});
await step("References tab: add the same paper again is recognised, not duplicated", async () => {
  await p.click("#ref-id");
  await p.keyboard.down("Control"); await p.keyboard.press("a"); await p.keyboard.up("Control");
  await p.type("#ref-id", "https://arxiv.org/abs/1706.03762v7");
  await clickText("button", "Add");
  await bodyHas("Already in");
});
await step("References tab: live arXiv search shows the paper with an abstract", async () => {
  await p.type("#ref-q", "attention is all you need");
  await p.click("form[role=search] button[type=submit]"); // not the sidebar "Search" tab, which has the same label
  await bodyHas("Attention Is All You Need[\\s\\S]*arXiv:1706\\.03762");
  await shot("f3-search-results");
});
await step("Check tab: run checks, finds the new bib entry unused, no errors", async () => {
  await clickText("[role=tab]", "Check", false);
  await clickText("button", "Run checks");
  await bodyHas("0 errors");
  await p.click(".side-panel input[type=checkbox]");
  await bodyHas("never cited");
  await shot("f4-check");
});
await step("no uncaught page errors", async () => {
  const real = errors.filter((e) => !/cross-origin|Permission denied to access property/i.test(e));
  if (real.length) throw new Error(real.join(" | "));
});
if (pid) console.log("cleanup delete HTTP", await api("DELETE", `/api/projects/${pid}`));
await browser.close();
process.exit(failed ? 1 : 0);
