// Browser check: Compile builds the open .tex file (PDF named after it); a section file falls back to main.
// usage: PRISM_URL=https://host node compilefile.mjs <screenshot dir> <admin password>
import puppeteer from "puppeteer-core";

const B = process.env.PRISM_URL || "http://127.0.0.1:8000";
const [OUT, ADMIN_PW] = process.argv.slice(2);
const browser = await puppeteer.launch({ browser: "firefox", executablePath: "/usr/bin/firefox", headless: true });
const p = await (await browser.createBrowserContext()).newPage();
await p.setViewport({ width: 1500, height: 950 });
const errors = [];
p.on("pageerror", (e) => errors.push(e.message));
let failed = 0, pid = null;
async function step(name, fn) {
  try { console.log("PASS", name, (await fn()) ?? ""); } catch (e) { failed++; await p.screenshot({ path: `${OUT}/cf-fail-${failed}.png` }).catch(() => {}); console.log("FAIL", name, e.message); }
}
const api = (method, path, body) => p.evaluate(async (method, path, body) => {
  const r = await fetch(path, { method, headers: { "X-Prism-CSRF": "1", "Content-Type": "application/json" }, body: body ? JSON.stringify(body) : undefined });
  return { status: r.status, json: await r.json().catch(() => null), cd: r.headers.get("content-disposition") };
}, method, path, body);
const doc = (word) => `\\documentclass{article}\n\\begin{document}\n${word}\n\\end{document}\n`;
const openFile = async (name) => {
  await p.evaluate((name) => [...document.querySelectorAll(".tree-file button")].find((b) => b.textContent.includes(name))?.click(), name);
  await p.waitForFunction((name) => document.querySelector(".file-tab.active")?.textContent.includes(name), { timeout: 8000 }, name);
};
const lastBuild = async () => (await api("GET", `/api/projects/${pid}/status`)).json.last_build;
const compileAndWait = async (prevId) => {
  // the app ignores clicks while its previous compile request is still settling (button disabled)
  await p.waitForFunction(() => { const b = [...document.querySelectorAll("button")].find((x) => x.textContent.trim() === "Compile"); return !!b && !b.disabled; }, { timeout: 15000 });
  await p.evaluate(() => [...document.querySelectorAll("button")].find((b) => b.textContent.trim() === "Compile")?.click());
  for (let i = 0; i < 100; i++) {
    const lb = await lastBuild();
    if (lb && lb.id !== prevId && !["queued", "running"].includes(lb.status)) return lb;
    await new Promise((r) => setTimeout(r, 500));
  }
  throw new Error("build did not finish");
};

await step("login", async () => {
  await p.goto(B + "/", { waitUntil: "networkidle0" });
  await p.type("input[autocomplete=username]", "admin");
  await p.type("input[type=password]", ADMIN_PW);
  await p.click("button[type=submit]");
  await p.waitForFunction(() => !document.querySelector("input[type=password]"), { timeout: 15000 });
});
await step("project with main.tex, alpha.tex, beta.tex and a section file", async () => {
  const r = await api("POST", "/api/projects", { name: "compilefile-check (temporary)", template: "blank" });
  pid = r.json?.id; if (!pid) throw new Error("create " + r.status);
  const put = (path, text) => api("POST", `/api/projects/${pid}/files`, { path, text });
  await api("PUT", `/api/projects/${pid}/files/content`, { path: "main.tex", text: doc("MAINWORD"), base_sha: (await api("GET", `/api/projects/${pid}/files/content?path=main.tex`)).json.sha });
  await put("alpha.tex", doc("ALPHAWORD")); await put("beta.tex", doc("BETAWORD")); await put("sec.tex", "Only a section, no documentclass.\n");
  await p.goto(`${B}/p/${pid}`, { waitUntil: "networkidle0" });
  await p.waitForSelector(".tree-file button");
});
let prev = null;
for (const [file, stem] of [["alpha.tex", "alpha"], ["beta.tex", "beta"]]) {
  await step(`open ${file} -> Compile builds it and the PDF is named ${stem}.pdf`, async () => {
    await openFile(file);
    await p.waitForFunction((f) => document.querySelector(".compile-target")?.textContent.trim() === f, { timeout: 5000 }, file);
    const lb = await compileAndWait(prev); prev = lb.id;
    if (lb.main_file !== file || lb.status !== "succeeded") throw new Error(JSON.stringify({ f: lb.main_file, s: lb.status, e: lb.error }));
    await p.waitForFunction((f) => document.querySelector(".build-info")?.textContent.includes(f), { timeout: 15000 }, file);
    const pdfText = await api("GET", `/api/projects/${pid}/export.pdf`);
    if (!pdfText.cd?.includes(`${stem}.pdf`)) throw new Error("download name " + pdfText.cd);
    await p.screenshot({ path: `${OUT}/cf-${stem}.png` });
  });
}
await step("a section file without \\documentclass compiles the main file and says so", async () => {
  await openFile("sec.tex");
  const lb = await compileAndWait(prev); prev = lb.id;
  if (lb.main_file !== "main.tex" || lb.status !== "succeeded") throw new Error(JSON.stringify({ f: lb.main_file, s: lb.status }));
  await p.waitForFunction(() => /no \\documentclass, so main\.tex was compiled/.test(document.body.innerText), { timeout: 5000 });
});
await step("switching the toggle to main file always builds main.tex", async () => {
  await openFile("alpha.tex");
  await p.click(".compile-target");
  await p.waitForFunction(() => document.querySelector(".compile-target")?.textContent.trim() === "main.tex", { timeout: 5000 });
  const lb = await compileAndWait(prev); prev = lb.id;
  if (lb.main_file !== "main.tex") throw new Error("built " + lb.main_file);
  await p.click(".compile-target"); // restore the default for this browser profile
});
await step("no uncaught page errors", async () => {
  const real = errors.filter((e) => !/cross-origin|Permission denied to access property/i.test(e));
  if (real.length) throw new Error(real.join(" | "));
});
if (pid) console.log("cleanup delete HTTP", (await api("DELETE", `/api/projects/${pid}`)).status);
await browser.close();
process.exit(failed ? 1 : 0);
