// Browser check (Firefox, separate DEMO stack) of the push notice: a script (node's fetch, not a browser) uploads files to the
// open project through the API; the workspace must say so within a few seconds, with the client name and the file count, show a
// failed push as a failure that stays, and list both in the History panel. The browser's own saves must not show up.
// usage: PRISM_URL=http://127.0.0.1:8010 node pushui.mjs <screenshot dir> <admin password>
import puppeteer from "puppeteer-core";

const B = process.env.PRISM_URL;
const [OUT, PW] = process.argv.slice(2);
const PROFILE = `${process.env.HOME}/snap/firefox/common/prism-e2e-profile-${process.pid}`;
const browser = await puppeteer.launch({ browser: "firefox", executablePath: "/usr/bin/firefox", headless: true, userDataDir: PROFILE });
const p = await (await browser.createBrowserContext()).newPage();
await p.setViewport({ width: 1500, height: 950 });
const errors = [];
p.on("pageerror", (e) => errors.push(e.message));
let failed = 0, PID, cookie = "";
async function step(name, fn) { try { console.log("PASS", name, (await fn()) ?? ""); } catch (e) { failed++; await p.screenshot({ path: `${OUT}/push-fail-${failed}.png` }).catch(() => {}); console.log("FAIL", name, e.message); } }
const H = (extra = {}) => ({ "X-Prism-CSRF": "1", Cookie: cookie, ...extra });
async function api(method, path, body, extra) {
  const r = await fetch(B + path, { method, headers: H(body && !(body instanceof FormData) ? { "Content-Type": "application/json", ...extra } : extra), body: body instanceof FormData ? body : body ? JSON.stringify(body) : undefined });
  return { status: r.status, json: await r.json().catch(() => null) };
}
async function upload(name, text, extra = {}, overwrite = "true") {
  const f = new FormData(); f.append("file", new Blob([text]), name); f.append("directory", "pushed"); f.append("overwrite", overwrite);
  return api("POST", `/api/projects/${PID}/files/upload`, f, extra);
}
const toast = () => p.evaluate(() => document.querySelector(".push-toast")?.innerText.replace(/\s+/g, " ") ?? null);
const wait = (fn, ms = 20000) => p.waitForFunction(fn, { timeout: ms });
await step("login (browser and script), project, workspace open", async () => {
  await p.goto(B + "/", { waitUntil: "networkidle0" });
  await p.type("input[autocomplete=username]", "admin"); await p.type("input[type=password]", PW); await p.click("button[type=submit]");
  await p.waitForFunction(() => !document.querySelector("input[type=password]"), { timeout: 15000 });
  const r = await fetch(B + "/api/auth/login", { method: "POST", headers: { "Content-Type": "application/json", "X-Prism-CSRF": "1" }, body: JSON.stringify({ username: "admin", password: PW }) });
  cookie = (r.headers.getSetCookie?.() ?? []).map((c) => c.split(";")[0]).join("; ");
  if (!cookie) throw new Error("script login failed");
  PID = await p.evaluate(async () => (await (await fetch("/api/projects", { method: "POST", headers: { "Content-Type": "application/json", "X-Prism-CSRF": "1" }, body: JSON.stringify({ name: "push check (temporary)", template: "sample" }) })).json()).id);
  await p.goto(`${B}/p/${PID}`, { waitUntil: "domcontentloaded" });
  await p.waitForSelector("#ai-text", { timeout: 20000 });
  await new Promise((r) => setTimeout(r, 6000)); // the first poll records what is already there
  if (await toast()) throw new Error("a notice before any push: " + (await toast()));
});
await step("the browser's own save shows no notice", async () => {
  await p.evaluate(async (pid) => fetch(`/api/projects/${pid}/files/content`, { method: "PUT", headers: { "Content-Type": "application/json", "X-Prism-CSRF": "1" }, body: JSON.stringify({ path: "main.tex", text: "% browser save\n" }) }), PID);
  await new Promise((r) => setTimeout(r, 7000));
  if (await toast()) throw new Error(await toast());
  const n = (await api("GET", `/api/projects/${PID}/activity`)).json.length;
  if (n !== 0) throw new Error("recorded " + n);
});
await step("a script pushes 3 files: the notice names the client and the count, the file tree shows them", async () => {
  for (const n of ["a.tex", "b.tex", "c.tex"]) { const r = await upload(n, "% " + n + "\n", { "X-Prism-Client": "agent-test" }); if (r.status !== 200) throw new Error(JSON.stringify(r)); }
  await wait(() => document.querySelector(".push-toast.ok"), 15000);
  const t = await toast();
  await p.screenshot({ path: `${OUT}/push-ok.png` });
  if (!/agent-test pushed 3 files: done/.test(t)) throw new Error(t);
  await wait(() => document.body.innerText.includes("pushed"), 10000);
  return t;
});
await step("a failed push shows a failure that stays", async () => {
  await p.evaluate(() => document.querySelector(".push-toast button")?.click());
  const r = await upload("a.tex", "again", { "X-Prism-Client": "agent-test" }, "false");
  if (r.status !== 409) throw new Error("expected 409, got " + r.status);
  await wait(() => document.querySelector(".push-toast.bad"), 15000);
  await new Promise((x) => setTimeout(x, 22000)); // longer than a success notice stays
  const t = await toast();
  await p.screenshot({ path: `${OUT}/push-bad.png` });
  if (!t || !/push failed/.test(t) || !/already exists/.test(t)) throw new Error(String(t));
  return t;
});
await step("the History panel lists both pushes", async () => {
  await p.evaluate(() => [...document.querySelectorAll("button, [role=tab]")].find((b) => /History/.test(b.textContent) || /History/.test(b.getAttribute("aria-label") ?? ""))?.click());
  await p.waitForSelector(".push-list", { timeout: 10000 });
  const rows = await p.$$eval(".push-list li", (l) => l.map((x) => x.innerText.replace(/\s+/g, " ")));
  await p.screenshot({ path: `${OUT}/push-history.png` });
  if (rows.length !== 2 || !rows.some((r) => /failed/.test(r)) || !rows.some((r) => /done/.test(r) && /3 files/.test(r))) throw new Error(JSON.stringify(rows));
  return rows.join(" || ");
});
await step("no page errors", async () => { const real = errors.filter((m) => !/Permission denied to access property/.test(m)); if (real.length) throw new Error(real.join("; ")); });
await api("DELETE", `/api/projects/${PID}`);
await browser.close();
console.log(failed ? `${failed} FAILED` : "ALL PASSED");
process.exit(failed ? 1 : 0);
