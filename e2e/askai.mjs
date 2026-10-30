// Browser check of "Ask AI about selection": the button appears only for a selection, pins it as the AI
// context and focuses the request box. The task request is intercepted and aborted, so no LLM/tool runs;
// what is checked is the exact context the browser would send.
// usage: PRISM_URL=https://host node askai.mjs <screenshot dir> <admin password>
import puppeteer from "puppeteer-core";

const B = process.env.PRISM_URL || "http://127.0.0.1:8000";
const [OUT, ADMIN_PW] = process.argv.slice(2);
const browser = await puppeteer.launch({ browser: "firefox", executablePath: "/usr/bin/firefox", headless: true });
const p = await (await browser.createBrowserContext()).newPage();
await p.setViewport({ width: 1500, height: 950 });
const errors = [];
p.on("pageerror", (e) => errors.push(e.message));
let failed = 0, pid = null, sent = null;
async function step(name, fn) {
  try { console.log("PASS", name, (await fn()) ?? ""); } catch (e) { failed++; await p.screenshot({ path: `${OUT}/ask-fail-${failed}.png` }).catch(() => {}); console.log("FAIL", name, e.message); }
}
const api = (method, path, body) => p.evaluate(async (method, path, body) => {
  const r = await fetch(path, { method, headers: { "X-Prism-CSRF": "1", "Content-Type": "application/json" }, body: body ? JSON.stringify(body) : undefined });
  return { status: r.status, json: await r.json().catch(() => null) };
}, method, path, body);
const askBtn = () => p.$(".ask-ai");

// Firefox's WebDriver BiDi cannot read request bodies, so the page's fetch is wrapped: the task POST is
// recorded and answered with an error instead of being sent (no LLM or tool run).
await p.evaluateOnNewDocument(() => {
  const real = window.fetch.bind(window);
  window.fetch = (input, init) => {
    const url = typeof input === "string" ? input : input.url;
    if ((init?.method ?? "GET") === "POST" && /\/api\/projects\/[^/]+\/tasks$/.test(url)) {
      window.__sent = JSON.parse(init.body);
      return Promise.resolve(new Response(JSON.stringify({ detail: "blocked by test" }), { status: 400, headers: { "Content-Type": "application/json" } }));
    }
    return real(input, init);
  };
});

await step("login", async () => {
  await p.goto(B + "/", { waitUntil: "networkidle0" });
  await p.type("input[autocomplete=username]", "admin");
  await p.type("input[type=password]", ADMIN_PW);
  await p.click("button[type=submit]");
  await p.waitForFunction(() => !document.querySelector("input[type=password]"), { timeout: 15000 });
});
await step("temporary project with known lines, workspace open", async () => {
  const r = await api("POST", "/api/projects", { name: "askai-check (temporary)", template: "blank" });
  pid = r.json?.id; if (!pid) throw new Error("create failed " + r.status);
  const text = "\\documentclass{article}\n\\begin{document}\nFirst sentence here.\nSecond sentence to rewrite.\nThird sentence here.\n\\end{document}\n";
  const w = await api("POST", `/api/projects/${pid}/files`, { path: "chap.tex", text });
  if (w.status >= 300) throw new Error("file " + w.status);
  await p.goto(`${B}/p/${pid}`, { waitUntil: "networkidle0" });
  await p.waitForSelector(".tree-file button");
  await p.evaluate(() => [...document.querySelectorAll(".tree-file button")].find((b) => b.textContent.includes("chap.tex"))?.click());
  await p.waitForSelector(".cm-content");
});
await step("no button without a selection", async () => {
  await p.click(".cm-content");
  await new Promise((r) => setTimeout(r, 500));
  if (await askBtn()) throw new Error("button visible");
});
await step("selecting a line shows the button", async () => {
  await p.keyboard.down("Control"); await p.keyboard.press("Home"); await p.keyboard.up("Control");
  await p.keyboard.press("ArrowDown"); await p.keyboard.press("ArrowDown"); await p.keyboard.press("ArrowDown"); // line 4
  await p.keyboard.press("Home");
  await p.keyboard.down("Shift"); await p.keyboard.press("End"); await p.keyboard.up("Shift");
  await p.waitForSelector(".ask-ai", { timeout: 5000 });
  await p.screenshot({ path: `${OUT}/ask-button.png` });
});
await step("click: Selection context pinned, request box focused", async () => {
  await p.click(".ask-ai");
  await p.waitForFunction(() => /chap\.tex, lines 4–4/.test(document.body.innerText), { timeout: 5000 });
  const ok = await p.evaluate(() => document.activeElement?.id === "ai-text" && document.querySelector(".seg-btn.active")?.textContent.trim() === "Selection");
  if (!ok) throw new Error("focus/scope wrong");
  await p.screenshot({ path: `${OUT}/ask-panel.png` });
});
await step("sending posts exactly that selection as the context", async () => {
  await p.type("#ai-text", "Make this sentence more formal");
  await p.keyboard.press("Enter");
  await p.waitForFunction(() => true);
  await p.waitForFunction(() => !!window.__sent, { timeout: 5000 }).catch(() => {});
  sent = await p.evaluate(() => window.__sent ?? null);
  if (!sent) throw new Error("no task request seen");
  const c = sent.context;
  if (c.scope !== "selection" || c.path !== "chap.tex" || c.selection.from_line !== 4 || c.selection.to_line !== 4 || c.selection.text !== "Second sentence to rewrite.")
    throw new Error("context " + JSON.stringify(c));
});
await step("the button disappears again when the selection is cleared", async () => {
  await p.click(".cm-content"); // collapses the selection
  await p.waitForFunction(() => !document.querySelector(".ask-ai"), { timeout: 5000 });
});
await step("no uncaught page errors", async () => {
  const real = errors.filter((e) => !/cross-origin|Permission denied to access property/i.test(e));
  if (real.length) throw new Error(real.join(" | "));
});
if (pid) console.log("cleanup delete HTTP", (await api("DELETE", `/api/projects/${pid}`)).status);
await browser.close();
process.exit(failed ? 1 : 0);
