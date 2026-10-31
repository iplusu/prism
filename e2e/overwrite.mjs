// Browser check that uploads never replace a file silently: a dialog lists the clashes; Keep existing / Replace.
// usage: PRISM_URL=https://host node overwrite.mjs <screenshot dir> <admin password>
// Drops are dispatched as real DOM drag events carrying a DataTransfer with File objects. Folder
// drops (webkitGetAsEntry) cannot be produced synthetically, so only the file path is exercised here.
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
  try { console.log("PASS", name, (await fn()) ?? ""); } catch (e) { failed++; await p.screenshot({ path: `${OUT}/overwrite-fail-${failed}.png` }).catch(() => {}); console.log("FAIL", name, e.message); }
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
await step("temporary project + workspace", async () => {
  const r = await api("POST", "/api/projects", { name: "drop-check (temporary)", template: "blank" });
  pid = r.json?.id; if (!pid) throw new Error("create failed " + r.status);
  await p.goto(`${B}/p/${pid}`, { waitUntil: "networkidle0" });
  await p.waitForSelector(".tree");
});
const raw = (path) => p.evaluate(async (pid, path) => (await fetch(`/api/projects/${pid}/files/raw?path=${encodeURIComponent(path)}`)).text(), pid, path);
const modal = () => p.evaluate(() => { const m = document.querySelector(".modal"); return m ? { title: m.querySelector("h2")?.textContent, text: m.innerText } : null; });
const clickModal = (label) => p.evaluate((label) => { const b = [...document.querySelectorAll(".modal button")].find((x) => x.textContent.trim().startsWith(label)); if (!b) throw new Error("no button " + label); b.click(); }, label);
const gone = () => until(async () => !(await modal()));

await step("new names upload without any dialog", async () => {
  await dropOn(".side-panel", [{ name: "a.txt", text: "A1" }, { name: "b.txt", text: "B1" }]);
  if (!(await until(async () => (await raw("b.txt")) === "B1"))) throw new Error("not uploaded");
  if (await modal()) throw new Error("dialog shown for new files");
});
await step("same names ask first; new names in the same drop are added at once; nothing is replaced yet", async () => {
  await dropOn(".side-panel", [{ name: "a.txt", text: "A2" }, { name: "b.txt", text: "B2" }, { name: "c.txt", text: "C1" }]);
  if (!(await until(async () => !!(await modal())))) throw new Error("no dialog");
  const m = await modal();
  if (!/Replace 2 existing files/.test(m.title) || !/a\.txt/.test(m.text) || !/b\.txt/.test(m.text) || /c\.txt/.test(m.text)) throw new Error("dialog: " + JSON.stringify(m));
  if ((await raw("c.txt")) !== "C1") throw new Error("new file not added");
  if ((await raw("a.txt")) !== "A1" || (await raw("b.txt")) !== "B1") throw new Error("replaced before the answer");
  await p.screenshot({ path: `${OUT}/overwrite-dialog.png` });
});
await step("Keep existing leaves both files as they were", async () => {
  await clickModal("Keep existing");
  if (!(await gone())) throw new Error("dialog still open");
  if ((await raw("a.txt")) !== "A1" || (await raw("b.txt")) !== "B1") throw new Error("changed");
});
await step("Escape also keeps the existing file", async () => {
  await dropOn(".side-panel", [{ name: "a.txt", text: "A9" }]);
  await until(async () => !!(await modal()));
  await p.keyboard.press("Escape");
  if (!(await gone())) throw new Error("dialog still open");
  if ((await raw("a.txt")) !== "A1") throw new Error("changed");
});
await step("Replace replaces only the clashing file and the old content is in the history", async () => {
  await dropOn(".side-panel", [{ name: "a.txt", text: "A3" }]);
  await until(async () => !!(await modal()));
  const m = await modal();
  if (!/Replace existing file\?/.test(m.title)) throw new Error("title " + m.title);
  await clickModal("Replace");
  if (!(await gone())) throw new Error("dialog still open");
  if (!(await until(async () => (await raw("a.txt")) === "A3"))) throw new Error("not replaced: " + (await raw("a.txt")));
  if ((await raw("b.txt")) !== "B1") throw new Error("b changed");
  const v = await api("GET", `/api/projects/${pid}/versions`);
  if (!v.json.some((c) => /^Before upload replaced a\.txt/.test(c.message ?? ""))) throw new Error("no history entry: " + JSON.stringify(v.json.map((c) => c.message)));
});
await step("the file-picker upload asks as well", async () => {
  const fs = await import("node:fs");
  const f = `${process.env.TMPDIR || "/tmp"}/b.txt`;
  fs.writeFileSync(f, "B-picked");
  const input = await p.$("input[type=file]");
  await input.uploadFile(f);
  if (!(await until(async () => !!(await modal())))) throw new Error("no dialog");
  if ((await raw("b.txt")) !== "B1") throw new Error("replaced before the answer");
  await clickModal("Replace");
  if (!(await until(async () => (await raw("b.txt")) === "B-picked"))) throw new Error("not replaced");
  fs.unlinkSync(f);
});
await step("no uncaught page errors", async () => {
  const real = errors.filter((e) => !/cross-origin|Permission denied to access property/i.test(e));
  if (real.length) throw new Error(real.join(" | "));
});
if (pid) console.log("cleanup delete HTTP", (await api("DELETE", `/api/projects/${pid}`)).status);
await browser.close();
process.exit(failed ? 1 : 0);
