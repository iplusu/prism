// Browser check of the failed-build help: a missing TeX package is found in the log and installed with the
// real tlmgr, then the build succeeds; for another LaTeX error "Fix with AI" is offered and sends the errors
// (the task POST is intercepted in the page, so no LLM or tool runs).
// usage: PRISM_URL=https://host node buildhelp.mjs <screenshot dir> <admin password>
// Installs the real package `multirow` into the server's TeX Live if it is not there (remove it afterwards
// with `tlmgr remove multirow` to repeat the test from scratch).
import puppeteer from "puppeteer-core";

const B = process.env.PRISM_URL || "http://127.0.0.1:8000";
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
let failed = 0, pid = null;
async function step(name, fn) {
  try { console.log("PASS", name, (await fn()) ?? ""); } catch (e) { failed++; await p.screenshot({ path: `${OUT}/bh-fail-${failed}.png` }).catch(() => {}); console.log("FAIL", name, e.message); }
}
const api = (method, path, body) => p.evaluate(async (method, path, body) => {
  const r = await fetch(path, { method, headers: { "X-Prism-CSRF": "1", "Content-Type": "application/json" }, body: body ? JSON.stringify(body) : undefined });
  return { status: r.status, json: await r.json().catch(() => null) };
}, method, path, body);
const lastBuild = async () => (await api("GET", `/api/projects/${pid}/status`)).json.last_build;
const click = (text) => p.evaluate((text) => { const b = [...document.querySelectorAll("button")].find((x) => x.textContent.trim() === text && !x.disabled); b?.click(); return !!b; }, text);
const waitBuild = async (prev) => {
  for (let i = 0; i < 160; i++) { const lb = await lastBuild(); if (lb && lb.id !== prev && !["queued", "running"].includes(lb.status)) return lb; await new Promise((r) => setTimeout(r, 500)); }
  throw new Error("build did not finish");
};
const ready = () => p.waitForFunction(() => { const b = [...document.querySelectorAll("button")].find((x) => x.textContent.trim() === "Compile"); return !!b && !b.disabled; }, { timeout: 20000 });

await step("login", async () => {
  await p.goto(B + "/", { waitUntil: "networkidle0" });
  await p.type("input[autocomplete=username]", "admin");
  await p.type("input[type=password]", ADMIN_PW);
  await p.click("button[type=submit]");
  await p.waitForFunction(() => !document.querySelector("input[type=password]"), { timeout: 15000 });
});
await step("project using \\usepackage{multirow}", async () => {
  const r = await api("POST", "/api/projects", { name: "buildhelp-check (temporary)", template: "blank" });
  pid = r.json?.id; if (!pid) throw new Error("create " + r.status);
  const sha = (await api("GET", `/api/projects/${pid}/files/content?path=main.tex`)).json.sha;
  await api("PUT", `/api/projects/${pid}/files/content`, { path: "main.tex", base_sha: sha, text: "\\documentclass{article}\n\\usepackage{multirow}\n\\begin{document}\nHello multirow.\n\\end{document}\n" });
  await p.goto(`${B}/p/${pid}`, { waitUntil: "networkidle0" });
  await p.waitForSelector(".cm-content");
});
let prev = null;
await step("compile fails with multirow.sty missing and the install offer appears", async () => {
  await ready(); await click("Compile");
  const lb = await waitBuild(prev); prev = lb.id;
  if (lb.status !== "failed") throw new Error("status " + lb.status);
  await p.waitForFunction(() => /Missing LaTeX file\s*multirow\.sty/.test(document.body.innerText) && document.body.innerText.includes("Install package and recompile"), { timeout: 15000 });
  await p.screenshot({ path: `${OUT}/bh-offer.png` });
});
await step("install: real tlmgr installs the package and the recompile succeeds", async () => {
  if (!(await click("Install package and recompile"))) throw new Error("no install button");
  const lb = await waitBuild(prev); prev = lb.id;
  if (lb.status !== "succeeded") throw new Error("after install: " + lb.status + " " + lb.error);
  await p.waitForFunction(() => !document.body.innerText.includes("Install package and recompile"), { timeout: 15000 });
  await p.screenshot({ path: `${OUT}/bh-installed.png` });
});
await step("another LaTeX error offers Fix with AI, which sends the errors as the request", async () => {
  const sha = (await api("GET", `/api/projects/${pid}/files/content?path=main.tex`)).json.sha;
  await api("PUT", `/api/projects/${pid}/files/content`, { path: "main.tex", base_sha: sha, text: "\\documentclass{article}\n\\begin{document}\nHello \\undefinedmacro{x}.\n\\end{document}\n" });
  await p.reload({ waitUntil: "networkidle0" }); await p.waitForSelector(".cm-content"); await ready();
  await click("Compile");
  const lb = await waitBuild(prev); prev = lb.id;
  if (lb.status !== "failed") throw new Error("status " + lb.status);
  await p.waitForFunction(() => document.body.innerText.includes("Fix with AI"), { timeout: 15000 });
  if (/Install package and recompile/.test(await p.evaluate(() => document.body.innerText))) throw new Error("install offered for a source error");
  await click("Fix with AI");
  await p.waitForFunction(() => !!window.__task, { timeout: 5000 });
  const t = await p.evaluate(() => window.__task);
  if (!/undefinedmacro|Undefined control sequence/i.test(t.request) || t.context.scope !== "project" || t.mode !== "auto") throw new Error(JSON.stringify(t).slice(0, 300));
});
await step("no uncaught page errors", async () => {
  const real = errors.filter((e) => !/cross-origin|Permission denied to access property/i.test(e));
  if (real.length) throw new Error(real.join(" | "));
});
if (pid) console.log("cleanup delete HTTP", (await api("DELETE", `/api/projects/${pid}`)).status);
await browser.close();
process.exit(failed ? 1 : 0);
