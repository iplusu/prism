// Requires a running instance on http://127.0.0.1:8000 with DEMO_MODE=true and COLLAB_ENABLED=true,
// an admin account, and /usr/bin/firefox. With snap Firefox set TMPDIR to a directory under $HOME.
// End-to-end browser verification (headless Firefox via puppeteer-core).
import puppeteer from "puppeteer-core";
const B = process.env.PRISM_URL || "http://127.0.0.1:8000";
const [OUT, ADMIN_PW] = process.argv.slice(2);
const sleep = (ms) => new Promise((r) => setTimeout(r, ms));
const results = [];
const browser = await puppeteer.launch({ browser: "firefox", executablePath: "/usr/bin/firefox", headless: true });
async function newPage(w = 1500, h = 950) {
  const ctx = await browser.createBrowserContext();
  const p = await ctx.newPage();
  await p.setViewport({ width: w, height: h });
  p.errors = [];
  p.on("pageerror", (e) => p.errors.push(e.message));
  return p;
}
const shot = (p, n) => p.screenshot({ path: `${OUT}/${n}.png` });
async function step(name, fn) {
  try { const r = await fn(); results.push(["PASS", name, r ?? ""]); console.log("PASS", name, r ?? ""); }
  catch (e) { results.push(["FAIL", name, e.message]); console.log("FAIL", name, e.message); }
}
async function api(p, method, path, body) {
  return p.evaluate(async (method, path, body) => {
    const r = await fetch(path, { method, headers: { "Content-Type": "application/json", "X-Prism-CSRF": "1" }, body: body ? JSON.stringify(body) : undefined });
    return { status: r.status, json: await r.json().catch(() => null) };
  }, method, path, body);
}
async function login(p, user, pw) {
  await p.goto(B + "/", { waitUntil: "networkidle0" });
  await p.waitForSelector("input[type=password]");
  await p.type("input[autocomplete=username]", user);
  await p.type("input[type=password]", pw);
  await p.click("button[type=submit]");
  await p.waitForFunction(() => !document.querySelector("input[type=password]"), { timeout: 10000 });
}
const textOf = (p, sel) => p.$eval(sel, (e) => e.textContent);
// CodeMirror renders only visible lines: read the top and the bottom of the document.
async function editorText(p) {
  await p.click(".cm-content");
  await p.keyboard.down("Control"); await p.keyboard.press("Home"); await p.keyboard.up("Control");
  await sleep(300);
  const top = await textOf(p, ".cm-content");
  await p.keyboard.down("Control"); await p.keyboard.press("End"); await p.keyboard.up("Control");
  await sleep(300);
  return top + "\n" + (await textOf(p, ".cm-content"));
}
async function clickButton(p, prefix, exact = false) {
  const ok = await p.evaluate((prefix, exact) => {
    const b = [...document.querySelectorAll("button, [role=tab], a")].find((x) => (exact ? x.textContent.trim() === prefix : x.textContent.trim().startsWith(prefix)) && !x.disabled);
    if (b) b.click();
    return !!b;
  }, prefix, exact);
  if (!ok) throw new Error("no button: " + prefix);
}
const bodyHas = (p, re, timeout = 20000) => p.waitForFunction((src) => new RegExp(src).test(document.body.innerText), { timeout }, re);

const A = await newPage();
await step("login page + admin login", async () => {
  await A.goto(B + "/", { waitUntil: "networkidle0" });
  await shot(A, "01-login");
  await login(A, "admin", ADMIN_PW);
});
let pid;
await step("setup: users, sample project, member (API)", async () => {
  await api(A, "POST", "/api/users", { username: "bob", password: "bobpassword1", display_name: "Bob" });
  const r = await api(A, "POST", "/api/projects", { name: "Adaptive step-size paper", template: "sample" });
  pid = r.json.id;
  await api(A, "POST", `/api/projects/${pid}/members`, { username: "bob", role: "editor" });
  return pid;
});
await step("project home lists project", async () => {
  await A.goto(B + "/", { waitUntil: "networkidle0" });
  await A.waitForSelector(`::-p-text(Adaptive step-size paper)`, { timeout: 10000 });
  await shot(A, "02-projects");
});
await step("workspace opens with editor", async () => {
  await A.goto(`${B}/p/${pid}`, { waitUntil: "networkidle0" });
  await A.waitForSelector(".cm-content", { timeout: 15000 });
});
await step("compile -> real PDF rendered by pdf.js", async () => {
  await A.click("::-p-text(Compile)");
  await A.waitForSelector(".pdf-page canvas", { timeout: 60000 });
  await sleep(2000);
  const px = await A.$eval(".pdf-page canvas", (c) => { const d = c.getContext("2d").getImageData(0, 0, c.width, c.height).data; let k = 0; for (let i = 0; i < d.length; i += 4) if (d[i] < 128) k++; return k; });
  if (!px) throw new Error("blank canvas");
  await shot(A, "03-workspace-compiled");
  return `${px} dark pixels`;
});

// second user in a separate browser context
const Bp = await newPage();
await step("bob logs in, opens same project (collab)", async () => {
  await login(Bp, "bob", "bobpassword1");
  await Bp.goto(`${B}/p/${pid}`, { waitUntil: "networkidle0" });
  await Bp.waitForSelector(".cm-content", { timeout: 15000 });
});
await step("concurrent edits in two browsers converge", async () => {
  await A.click(".cm-content"); await A.keyboard.down("Control"); await A.keyboard.press("Home"); await A.keyboard.up("Control");
  await Bp.click(".cm-content"); await Bp.keyboard.down("Control"); await Bp.keyboard.press("End"); await Bp.keyboard.up("Control");
  await Promise.all([A.keyboard.type("% alice-was-here\n"), Bp.keyboard.type("\n% bob-was-here")]);
  await sleep(2500);
  const ta = await editorText(A), tb = await editorText(Bp);
  for (const [who, t] of [["alice", ta], ["bob", tb]]) {
    if (!(t.includes("alice-was-here") && t.includes("bob-was-here"))) throw new Error(who + "'s editor lacks an edit");
  }
  const live = await api(A, "GET", `/api/projects/${pid}/files/content?path=main.tex`);
  if (!(live.json.text.includes("alice-was-here") && live.json.text.includes("bob-was-here"))) throw new Error("server state lacks an edit");
  await A.keyboard.down("Control"); await A.keyboard.press("Home"); await A.keyboard.up("Control");
  await sleep(500);
  await shot(A, "04-collab-alice"); await shot(Bp, "05-collab-bob");
  return "both editors and the server hold both edits";
});
await step("stale PDF badge after edit", async () => {
  await A.bringToFront();
  await bodyHas(A, "\\bstale\\b");
  await shot(A, "04b-stale-badge");
});
await step("AI task (DEMO executor) -> proposal -> review page", async () => {
  await A.type("textarea", "Add a short remark to the main file");
  const sel = await A.$("select[aria-label='Tool that performs the task']");
  const opts = await sel.$$eval("option", (os) => os.map((o) => o.value));
  if (opts.includes("demo")) await sel.select("demo");
  await A.keyboard.down("Control"); await A.keyboard.press("Enter"); await A.keyboard.up("Control");
  await A.waitForSelector("::-p-text(Review changes)", { timeout: 120000 });
  await sleep(1000);
  await shot(A, "06-ai-panel-task");
  return `modes: ${opts.join(",")}`;
});
await step("review screen shows diff, checks, candidate PDF; apply", async () => {
  const r = await api(A, "GET", `/api/projects/${pid}/proposals`);
  const prop = r.json.find((x) => x.status === "ready");
  if (!prop) throw new Error("no ready proposal: " + JSON.stringify(r.json.map((x) => x.status)));
  await A.goto(`${B}/p/${pid}/review/${prop.id}`, { waitUntil: "networkidle0" });
  await A.waitForSelector("::-p-text(DEMO)", { timeout: 10000 });
  await sleep(1500);
  await shot(A, "07-review-diff");
  await clickButton(A, "Verification");
  await sleep(800);
  await shot(A, "07b-review-verification");
  await clickButton(A, "Candidate PDF");
  await A.waitForSelector("canvas", { timeout: 20000 });
  await sleep(4000);
  await shot(A, "07c-review-candidate-pdf");
  await clickButton(A, "Apply all");  // one click applies
  await bodyHas(A, "Applied to the manuscript", 120000);
  await shot(A, "08-review-applied");
  await A.goto(`${B}/p/${pid}`, { waitUntil: "networkidle0" });
  await sleep(3000);
  const changed = (await api(A, "GET", `/api/proposals/${prop.id}`)).json.files[0].path;
  if (changed !== "main.tex") {
    // bob is looking at main.tex; open the changed file through the API check instead
    const t = await api(A, "GET", `/api/projects/${pid}/files/content?path=${encodeURIComponent(changed)}`);
    if (!t.json.text.includes("DEMO MODE")) throw new Error("applied change missing on server");
  }
  const tb = await editorText(Bp);
  if (changed === "main.tex" && !tb.includes("DEMO MODE")) throw new Error("bob did not receive applied AI change");
  if (!tb.includes("bob-was-here")) throw new Error("bob's edit lost");
  return "bob's editor received the applied change; his edit kept";
});
await step("settings: LLM connection test reports not configured honestly", async () => {
  await A.goto(`${B}/p/${pid}/settings`, { waitUntil: "networkidle0" });
  await sleep(1000);
  await clickButton(A, "Orchestrator LLM");
  await sleep(500);
  await clickButton(A, "Test connection");
  await bodyHas(A, "reachability");
  await sleep(500);
  await shot(A, "09-settings-llm");
  const body = await textOf(A, "body");
  if (!/LLM_BASE_URL/.test(body)) throw new Error("no config hint");
});
await step("settings: tools status", async () => {
  await clickButton(A, "Tools"); await sleep(3000);
  await shot(A, "10-settings-tools");
  const body = await textOf(A, "body");
  return ["needs_auth", "unavailable", "available"].filter((s) => body.includes(s)).join(",");
});
await step("dark mode + narrow layout", async () => {
  const N = await newPage(420, 860);
  await login(N, "bob", "bobpassword1");
  await N.evaluate(() => localStorage.setItem("prism-theme", "dark"));
  await N.goto(`${B}/p/${pid}`, { waitUntil: "networkidle0" });
  await N.waitForSelector(".cm-content", { timeout: 15000 });
  await sleep(1000);
  await shot(N, "11-narrow-dark");
});
await step("no uncaught page errors", async () => {
  // "__bidi_args"/"Permission denied to access property" come from Firefox's WebDriver BiDi
  // instrumentation of cross-origin worker objects, not from the application.
  const noise = /__bidi_args|Permission denied to access property/;
  const errs = [...A.errors, ...Bp.errors].filter((e) => !noise.test(e));
  if (errs.length) throw new Error(errs.join(" | "));
});
console.log(JSON.stringify(results));
await browser.close();
