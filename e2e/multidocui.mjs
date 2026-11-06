// Browser check (Firefox, separate DEMO stack) of a proposal that changes several documents: the review page lists one candidate PDF per
// document, switches between them, and the marked PDF follows the chosen document. The proposal is created and verified by a Python helper
// running the same service code the worker runs (the stack has no worker); the compiles are real.
// usage: PRISM_URL=http://127.0.0.1:8010 PRISM_DB=<sqlite file> PRISM_DATA=<data dir> node multidocui.mjs <screenshot dir> <admin password>
import puppeteer from "puppeteer-core";
import { execFileSync } from "node:child_process";

const B = process.env.PRISM_URL;
const [OUT, PW] = process.argv.slice(2);
const PROFILE = `${process.env.HOME}/snap/firefox/common/prism-e2e-profile-${process.pid}`;
const browser = await puppeteer.launch({ browser: "firefox", executablePath: "/usr/bin/firefox", headless: true, userDataDir: PROFILE });
const p = await (await browser.createBrowserContext()).newPage();
await p.setViewport({ width: 1500, height: 950 });
const errors = [];
p.on("pageerror", (e) => errors.push(e.message));
let failed = 0, PID, PROP;
async function step(name, fn) { try { console.log("PASS", name, (await fn()) ?? ""); } catch (e) { failed++; await p.screenshot({ path: `${OUT}/md-fail-${failed}.png` }).catch(() => {}); console.log("FAIL", name, e.message); } }
const doc = (name) => `\\documentclass{article}\n\\begin{document}\n${name} ORIGINAL\n\\end{document}\n`;
await step("login, a project with two standalone documents, a proposal changing both plus the main file", async () => {
  await p.goto(B + "/", { waitUntil: "networkidle0" });
  await p.type("input[autocomplete=username]", "admin"); await p.type("input[type=password]", PW); await p.click("button[type=submit]");
  await p.waitForFunction(() => !document.querySelector("input[type=password]"), { timeout: 15000 });
  PID = await p.evaluate(async (a, b) => {
    const h = { "Content-Type": "application/json", "X-Prism-CSRF": "1" };
    const id = (await (await fetch("/api/projects", { method: "POST", headers: h, body: JSON.stringify({ name: "multi doc check (temporary)", template: "sample" }) })).json()).id;
    for (const [path, text] of [["cv.tex", a], ["letter.tex", b]]) await fetch(`/api/projects/${id}/files`, { method: "POST", headers: h, body: JSON.stringify({ path, text }) });
    return id;
  }, doc("CVDOC"), doc("LETTERDOC"));
  const py = `
import sys, json
from app.db import SessionLocal
from app.models import Project
from app.projects import documents
from app.proposals import service
pid = sys.argv[1]
db = SessionLocal()
proj = db.get(Project, pid)
snap = documents.snapshot(pid, message="e2e base")
ch = {"cv.tex": snap.files["cv.tex"].replace(b"ORIGINAL", b"CVEDITED"), "letter.tex": snap.files["letter.tex"].replace(b"ORIGINAL", b"LETTEREDITED"),
      "sections/conclusion.tex": snap.files["sections/conclusion.tex"].replace(b"future work", b"later work")}
prop = service.create(db, proj, base_revision=snap.revision, base_files=snap.files, changes=ch, origin="agent", title="three documents")
service.verify(db, prop)
print(prop.id)
`;
  PROP = execFileSync("uv", ["run", "--frozen", "python", "-c", py, PID], { cwd: `${process.env.HOME}/prism/backend`, encoding: "utf-8",
    env: { HOME: process.env.HOME, PATH: `${process.env.HOME}/.TinyTeX/bin/x86_64-linux:${process.env.HOME}/.local/bin:/usr/bin:/bin`, DATABASE_URL: `sqlite:///${process.env.PRISM_DB}`, PRISM_DATA_DIR: process.env.PRISM_DATA, DEMO_MODE: "true" } }).trim().split("\n").pop();
  if (!/^[0-9a-f]{32}$/.test(PROP)) throw new Error("no proposal id: " + PROP);
  return PROP;
});
const state = () => p.evaluate(() => ({ opts: [...document.querySelectorAll(".candidate-pdf select option")].map((o) => o.textContent.trim()), sel: document.querySelector(".candidate-pdf select")?.value, text: document.querySelector(".candidate-pdf label")?.innerText.replace(/\s+/g, " ") }));
await step("the review page offers one PDF per changed document, the main file first", async () => {
  await p.goto(`${B}/p/${PID}/review/${PROP}`, { waitUntil: "domcontentloaded" });
  await p.waitForSelector("[role=tab], button", { timeout: 15000 });
  await p.evaluate(() => [...document.querySelectorAll("button, [role=tab]")].find((b) => b.textContent.trim() === "Candidate PDF")?.click());
  await p.waitForSelector(".candidate-pdf select", { timeout: 20000 });
  const s = await state();
  await p.screenshot({ path: `${OUT}/md-main.png` });
  if (JSON.stringify(s.opts) !== JSON.stringify(["main.tex", "cv.tex", "letter.tex"])) throw new Error(JSON.stringify(s));
  return s.text;
});
await step("choosing another document loads that document's PDF; the marked PDF follows it", async () => {
  const urls = [];
  p.on("request", (r) => { if (/\/api\/proposals\/.*(pdf)/.test(r.url())) urls.push(r.url().replace(B, "")); });
  await p.select(".candidate-pdf select", "letter.tex");
  await new Promise((r) => setTimeout(r, 2500));
  if (!urls.some((u) => u.includes("/pdf?doc=letter.tex"))) throw new Error("no request for letter.tex: " + urls.join(" "));
  await p.evaluate(() => [...document.querySelectorAll(".candidate-pdf button")].find((b) => /Show changes marked/.test(b.textContent))?.click());
  await p.waitForFunction(() => [...document.querySelectorAll(".candidate-pdf button")].some((b) => /plain candidate/.test(b.textContent)), { timeout: 120000 });
  await new Promise((r) => setTimeout(r, 1500));
  await p.screenshot({ path: `${OUT}/md-marked-letter.png` });
  if (!urls.some((u) => u.includes("/marked-pdf?doc=letter.tex"))) throw new Error("no marked request for letter.tex: " + urls.join(" "));
  return urls.join(" ");
});
await step("no page errors", async () => { const real = errors.filter((m) => !/Permission denied to access property/.test(m)); if (real.length) throw new Error(real.join("; ")); });
await p.evaluate(async (pid) => fetch(`/api/projects/${pid}`, { method: "DELETE", headers: { "X-Prism-CSRF": "1" } }), PID).catch(() => {});
await browser.close();
console.log(failed ? `${failed} FAILED` : "ALL PASSED");
process.exit(failed ? 1 : 0);
