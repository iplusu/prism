// Browser check of the Copy buttons in the AI Assistant panel (what is copied, honest feedback when the browser refuses).
// usage: PRISM_URL=https://host PRISM_E2E_PSQL=<psql> PRISM_E2E_DB=<uri of the stack's database> node copybtn.mjs <screenshot dir> <admin password>
// Drops are dispatched as real DOM drag events carrying a DataTransfer with File objects. Folder
// drops (webkitGetAsEntry) cannot be produced synthetically, so only the file path is exercised here.
import puppeteer from "puppeteer-core";
import { execFileSync } from "node:child_process";

const B = process.env.PRISM_URL || "http://127.0.0.1:8000";
const [OUT, ADMIN_PW] = process.argv.slice(2);
const browser = await puppeteer.launch({ browser: "firefox", executablePath: "/usr/bin/firefox", headless: true });
const p = await (await browser.createBrowserContext()).newPage();
await p.setViewport({ width: 1500, height: 950 });
const errors = [];
p.on("pageerror", (e) => errors.push(e.message));
let failed = 0, pid = null;
async function step(name, fn) {
  try { console.log("PASS", name, (await fn()) ?? ""); } catch (e) { failed++; await p.screenshot({ path: `${OUT}/copy-fail-${failed}.png` }).catch(() => {}); console.log("FAIL", name, e.message); }
}
const api = (method, path, body) => p.evaluate(async (method, path, body) => {
  const r = await fetch(path, { method, headers: { "X-Prism-CSRF": "1", "Content-Type": "application/json" }, body: body ? JSON.stringify(body) : undefined });
  return { status: r.status, json: await r.json().catch(() => null) };
}, method, path, body);
// dispatch dragover (+ drop) with the given files on the first element matching sel
const dropOn = (sel, files, { over = false } = {}) => p.evaluate(async (sel, files, over) => {
  const el = [...document.querySelectorAll(sel)][0];
  if (!el) throw new Error("no element " + sel);
  const dt = new DataTransfer();
  for (const f of files) dt.items.add(new File([f.text], f.name, { type: "text/plain" }));
  const fire = (type) => el.dispatchEvent(new DragEvent(type, { bubbles: true, cancelable: true, dataTransfer: dt }));
  fire("dragenter"); fire("dragover");
  await new Promise((r) => setTimeout(r, 200)); // let React render the hint
  const hint = document.querySelector(".drop-hint")?.textContent ?? null;
  if (!over) fire("drop");
  return hint;
}, sel, files, over);
const names = () => api("GET", `/api/projects/${pid}/files`).then((r) => r.json.map((f) => f.path));
const until = async (fn, ms = 10000) => { const t = Date.now(); while (Date.now() - t < ms) { if (await fn()) return true; await new Promise((r) => setTimeout(r, 300)); } return false; };

await step("login", async () => {
  await p.goto(B + "/", { waitUntil: "networkidle0" });
  await p.type("input[autocomplete=username]", "admin");
  await p.type("input[type=password]", ADMIN_PW);
  await p.click("button[type=submit]");
  await p.waitForFunction(() => !document.querySelector("input[type=password]"), { timeout: 15000 });
});
const psql = (sql) => execFileSync(process.env.PRISM_E2E_PSQL, [process.env.PRISM_E2E_DB, "-Atc", sql], { encoding: "utf8" }).trim();
const REVIEW = "Reviewer one says: the abstract overclaims.\nSecond line with $x_1$ and 50% of the text.";
const REQUEST = "Tighten the introduction";

await step("a demo task and a merged-report message with per-tool reviews exist", async () => {
  const r = await api("POST", "/api/projects", { name: "copy-check (temporary)", template: "sample" });
  pid = r.json?.id; if (!pid) throw new Error("create failed " + r.status);
  const t = await api("POST", `/api/projects/${pid}/tasks`, { request: REQUEST, mode: "demo", context: { scope: "project" } });
  if (t.status !== 200) throw new Error("task " + t.status);
  if (!(await until(async () => ["completed", "needs_review", "failed"].includes((await api("GET", `/api/tasks/${t.json.id}`)).json.status), 60000))) throw new Error("task did not finish");
  const uid = psql("select id from users where username='admin'");
  const meta = JSON.stringify({ kind: "review_report", reviews: [{ tool: "codex", label: "Codex", status: "succeeded", persona: "", text: REVIEW, error: "" },
                                                              { tool: "antigravity", label: "Antigravity", status: "failed", persona: "", text: "", error: "timed out" }] }).replace(/'/g, "''");
  psql(`insert into messages (project_id, user_id, role, content, meta, created_at) values ('${pid}','${uid}','assistant','Merged report: both reviewers agree.','${meta}'::jsonb, now())`);
  await p.goto(`${B}/p/${pid}`, { waitUntil: "networkidle0" });
  await p.waitForFunction(() => document.querySelectorAll(".msg").length >= 3, { timeout: 15000 });
});
// replace the clipboard with a recorder: it sees exactly what the button hands to the browser
await p.evaluate(() => { window.__copied = []; Object.defineProperty(navigator, "clipboard", { configurable: true, value: { writeText: async (t) => { window.__copied.push(t); } } }); });
const clickCopy = (msgSel, aria) => p.evaluate((msgSel, aria) => { const m = [...document.querySelectorAll(msgSel)][0]; const b = [...m.querySelectorAll("button.copy-btn")].find((x) => !aria || x.getAttribute("aria-label").startsWith(aria)); if (!b) throw new Error("no copy button in " + msgSel); b.click(); }, msgSel, aria);
const last = () => p.evaluate(() => window.__copied[window.__copied.length - 1]);
const feedback = (msgSel, aria) => p.evaluate((msgSel, aria) => { const m = [...document.querySelectorAll(msgSel)][0]; const b = [...m.querySelectorAll("button.copy-btn")].find((x) => !aria || x.getAttribute("aria-label").startsWith(aria)); return b?.innerText.trim(); }, msgSel, aria);

await step("copying your own message gives exactly the request text, and the button says Copied", async () => {
  await clickCopy(".msg-user");
  if ((await last()) !== REQUEST) throw new Error("copied: " + (await last()));
  if ((await feedback(".msg-user")) !== "Copied") throw new Error("feedback: " + (await feedback(".msg-user")));
});
await step("copying an assistant message gives its full text", async () => {
  const shown = await p.evaluate(() => [...document.querySelectorAll(".msg-assistant")].find((m) => m.querySelector(".msg-body")?.innerText.length > 20)?.querySelector(".msg-body").innerText);
  const idx = await p.evaluate(() => [...document.querySelectorAll(".msg-assistant")].findIndex((m) => m.querySelector(".msg-body")?.innerText.length > 20));
  await p.evaluate((idx) => document.querySelectorAll(".msg-assistant")[idx].querySelector("button.copy-btn").click(), idx);
  if ((await last()) !== shown) throw new Error(`copied ${JSON.stringify(await last())} vs shown ${JSON.stringify(shown)}`);
});
await step("each succeeded review has its own button that copies the original review text", async () => {
  await p.evaluate(() => [...document.querySelectorAll(".review-one summary")].forEach((s) => s.click()));
  const n = await p.evaluate(() => document.querySelectorAll(".review-one button.copy-btn").length);
  if (n !== 1) throw new Error("copy buttons in reviews: " + n + " (the failed reviewer must have none)");
  await p.evaluate(() => document.querySelector(".review-one button.copy-btn").click());
  if ((await last()) !== REVIEW) throw new Error("copied: " + JSON.stringify(await last()));
  await p.screenshot({ path: `${OUT}/copy-buttons.png` });
});
await step("without the Clipboard API the selection-based fallback copies the same text", async () => {
  await p.evaluate(() => { Object.defineProperty(navigator, "clipboard", { configurable: true, value: undefined }); window.__fallback = null;
    document.execCommand = (cmd) => { window.__fallback = document.querySelector("textarea[readonly]")?.value ?? null; return cmd === "copy"; }; });
  await clickCopy(".msg-user");
  await until(async () => (await p.evaluate(() => window.__fallback)) !== null, 3000);
  if ((await p.evaluate(() => window.__fallback)) !== REQUEST) throw new Error("fallback text: " + (await p.evaluate(() => window.__fallback)));
  if ((await feedback(".msg-user")) !== "Copied") throw new Error("feedback: " + (await feedback(".msg-user")));
});
await step("when the browser refuses both ways the button says Copy failed (no fake success)", async () => {
  await p.evaluate(() => { document.execCommand = () => false; });
  await new Promise((r) => setTimeout(r, 2000)); // let the previous "Copied" reset
  await clickCopy(".msg-user");
  await until(async () => (await feedback(".msg-user")) === "Copy failed", 3000);
  if ((await feedback(".msg-user")) !== "Copy failed") throw new Error("feedback: " + (await feedback(".msg-user")));
});
await step("no uncaught page errors", async () => {
  const real = errors.filter((e) => !/cross-origin|Permission denied to access property/i.test(e));
  if (real.length) throw new Error(real.join(" | "));
});
if (pid) console.log("cleanup delete HTTP", (await api("DELETE", `/api/projects/${pid}`)).status);
await browser.close();
process.exit(failed ? 1 : 0);
