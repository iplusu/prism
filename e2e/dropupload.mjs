// Browser check of drag-and-drop upload into the file tree.
// usage: PRISM_URL=https://host node dropupload.mjs <screenshot dir> <admin password>
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
  try { console.log("PASS", name, (await fn()) ?? ""); } catch (e) { failed++; await p.screenshot({ path: `${OUT}/drop-fail-${failed}.png` }).catch(() => {}); console.log("FAIL", name, e.message); }
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
await step("dragging over the file panel shows the drop hint with the target", async () => {
  const hint = await dropOn(".side-panel", [{ name: "x.txt", text: "x" }], { over: true });
  if (!/project root/.test(hint ?? "")) throw new Error("hint: " + hint);
  await p.screenshot({ path: `${OUT}/drop-hint.png` });
});
await step("dropping two files on the panel uploads both to the root", async () => {
  await dropOn(".side-panel", [{ name: "dropped-a.txt", text: "A" }, { name: "dropped-b.txt", text: "B" }]);
  if (!(await until(async () => { const n = await names(); return n.includes("dropped-a.txt") && n.includes("dropped-b.txt"); }))) throw new Error("not uploaded: " + (await names()));
  const raw = await p.evaluate(async (pid) => (await fetch(`/api/projects/${pid}/files/raw?path=dropped-b.txt`)).text(), pid);
  if (raw !== "B") throw new Error("content " + raw);
});
await step("new files appear in the tree without reload", async () => {
  await p.waitForFunction(() => document.body.innerText.includes("dropped-a.txt"), { timeout: 10000 });
});
await step("dropping on a folder row uploads into that folder", async () => {
  await api("POST", `/api/projects/${pid}/files`, { path: "figs/keep.txt", text: "k" });
  await p.reload({ waitUntil: "networkidle0" });
  await p.waitForSelector(".tree-row");
  await p.evaluate(() => [...document.querySelectorAll(".tree-row")].find((b) => b.textContent.trim() === "figs")?.setAttribute("data-t", "1"));
  await dropOn("[data-t='1']", [{ name: "in-figs.txt", text: "F" }]);
  if (!(await until(async () => (await names()).includes("figs/in-figs.txt")))) throw new Error("not in folder: " + (await names()));
});
await step("a file named ../bad.txt cannot escape the project; the next file still uploads", async () => {
  await dropOn(".side-panel", [{ name: "bad.txt", text: "x" }, { name: "good.txt", text: "ok" }].map((f, i) => i === 0 ? { name: "../bad.txt", text: "x" } : f));
  if (!(await until(async () => (await names()).includes("good.txt")))) throw new Error("good.txt missing");
  const files = await names();
  if (files.some((n) => n.includes(".."))) throw new Error("traversal name stored: " + files);
});
await step("no uncaught page errors", async () => {
  const real = errors.filter((e) => !/cross-origin|Permission denied to access property/i.test(e));
  if (real.length) throw new Error(real.join(" | "));
});
if (pid) console.log("cleanup delete HTTP", (await api("DELETE", `/api/projects/${pid}`)).status);
await browser.close();
process.exit(failed ? 1 : 0);
