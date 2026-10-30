// Browser check of: bibliography verification, Zotero import (public group), arXiv preflight + export, AI analysis presets.
// usage: PRISM_URL=https://host node features2.mjs <screenshot dir> <admin password>
import puppeteer from "puppeteer-core";

const B = process.env.PRISM_URL || "http://127.0.0.1:8000";
const [OUT, ADMIN_PW] = process.argv.slice(2);
const browser = await puppeteer.launch({ browser: "firefox", executablePath: "/usr/bin/firefox", headless: true });
const p = await (await browser.createBrowserContext()).newPage();
await p.setViewport({ width: 1500, height: 950 });
const errors = [];
p.on("pageerror", (e) => errors.push(e.message));
const sleep = (ms) => new Promise((r) => setTimeout(r, ms));
let pid = null, failed = 0;
const api = (method, path, body) => p.evaluate(async (method, path, body) => {
  const r = await fetch(path, { method, credentials: "same-origin", headers: { "Content-Type": "application/json", "X-Prism-CSRF": "1" }, body: body ? JSON.stringify(body) : undefined });
  const t = await r.text(); try { return JSON.parse(t); } catch { return { _status: r.status, _text: t.slice(0, 200) }; }
}, method, path, body);
async function step(name, fn) {
  try { console.log("PASS", name, (await fn()) ?? ""); } catch (e) { failed++; await p.screenshot({ path: `${OUT}/f2-fail-${failed}.png` }).catch(() => {}); console.log("FAIL", name, e.message); }
}
const bodyHas = (re, timeout = 90000) => p.waitForFunction((src) => new RegExp(src).test(document.body.innerText), { timeout }, re);
const clickText = async (sel, text, exact = true) => {
  const ok = await p.evaluate((sel, text, exact) => {
    const b = [...document.querySelectorAll(sel)].find((x) => (exact ? x.textContent.trim() === text : x.textContent.trim().startsWith(text)) && !x.disabled);
    if (b) b.click();
    return !!b;
  }, sel, text, exact);
  if (!ok) throw new Error(`no enabled ${sel} with text ${text}`);
};
await p.goto(B + "/", { waitUntil: "networkidle0" });
await p.type("input[autocomplete=username]", "admin");
await p.type("input[type=password]", ADMIN_PW);
await p.click("button[type=submit]");
await p.waitForFunction(() => !document.querySelector("input[type=password]"), { timeout: 15000 });
pid = (await api("POST", "/api/projects", { name: "features2-check (temporary)", template: "sample" })).id;
// a deliberately invented reference, and arXiv-unfriendly LaTeX, added through the API
const bib = await api("GET", `/api/projects/${pid}/files/content?path=references.bib`);
await api("PUT", `/api/projects/${pid}/files/content`, { path: "references.bib", expected_sha: bib.sha,
  text: bib.text + "\n@article{ghost2031,\n  title={A Totally Invented Method for Quantum Gradient Telepathy},\n  author={Nobody, N.},\n  year={2031}\n}\n" });
const res = await api("GET", `/api/projects/${pid}/files/content?path=sections/results.tex`);
await api("PUT", `/api/projects/${pid}/files/content`, { path: "sections/results.tex", expected_sha: res.sha, text: res.text + "\n\\includegraphics{/home/me/plot.png}\n" });
await p.goto(`${B}/p/${pid}`, { waitUntil: "networkidle0" });
await p.waitForSelector(".cm-content", { timeout: 30000 });

await step("Refs > Verify: the invented reference is flagged 'not found', real ones are not; click jumps to the .bib line", async () => {
  await clickText("[role=tab]", "Refs", false);
  await p.waitForSelector(".verify-bib");
  await clickText(".verify-bib button", "Verify");
  await bodyHas("not found · ghost2031");
  await p.screenshot({ path: `${OUT}/f5-verify.png` });
  const summary = await p.$eval(".verify-bib [role=status]", (e) => e.innerText.replace(/\s+/g, " "));
  await p.evaluate(() => [...document.querySelectorAll(".verify-bib .search-row")].find((b) => b.innerText.includes("ghost2031")).click());
  await p.waitForFunction(() => document.querySelector(".file-tab.active")?.title === "references.bib", { timeout: 8000 });
  const line = await p.$eval(".cm-activeLine", (e) => e.textContent);
  if (!/ghost2031/.test(line)) throw new Error("caret line: " + line);
  return summary;
});
await step("Refs > Zotero: public group 100 imported into references.bib, then nothing twice", async () => {
  await clickText("[role=tab]", "Refs", false);
  await p.evaluate(() => (document.querySelector(".zotero-import").open = true));
  await p.select("#zot-lib", "public");
  await p.type('input[aria-label="Public group id"]', "100");
  await clickText("button", "Import into bibliography");
  await bodyHas("Added \\d+ to references\\.bib");
  const msg = await p.$eval(".zotero-import [role=status]", (e) => e.innerText.replace(/\s+/g, " "));
  const n = Number(/Added (\d+)/.exec(msg)[1]);
  if (n < 20) throw new Error(msg);
  const text = (await api("GET", `/api/projects/${pid}/files/content?path=references.bib`)).text;
  if (!text.includes("The art of doing (geographies of) music")) throw new Error("entry not in .bib");
  await p.screenshot({ path: `${OUT}/f6-zotero.png` });
  await clickText("button", "Import into bibliography");
  await bodyHas("Added 0 to references\\.bib");
  return msg;
});
await step("Check > arXiv preflight: finds the absolute path (line shown), notes the TeX Live caveat", async () => {
  await clickText("[role=tab]", "Check", false);
  await p.select('select[aria-label="Kind of check"]', "arxiv");
  await clickText("button", "Run checks");
  await bodyHas("Absolute path '/home/me/plot.png'");
  await p.click(".side-panel input[type=checkbox]");
  await bodyHas("arXiv compiles with its own TeX Live");
  await p.screenshot({ path: `${OUT}/f7-arxiv-check.png` });
});
await step("Export > arXiv source: before a compile the page says why it refuses (and saves no file)", async () => {
  await clickText("button", "Export", false);
  await clickText(".menu-item", "arXiv source (.zip)");
  await bodyHas("Compile the paper first");
});
await step("after compiling, the arXiv zip is real: has main.bbl produced by BibTeX and no aux files", async () => {
  // restore a compilable text first (the absolute-path line is only a LaTeX warning-level problem, but keep it clean)
  const cur = await api("GET", `/api/projects/${pid}/files/content?path=sections/results.tex`);
  await api("PUT", `/api/projects/${pid}/files/content`, { path: "sections/results.tex", expected_sha: cur.sha, text: cur.text.replace("\n\\includegraphics{/home/me/plot.png}\n", "\n") });
  await api("POST", `/api/projects/${pid}/builds`);
  const t0 = Date.now();
  for (;;) {
    const b = (await api("GET", `/api/projects/${pid}/builds?limit=1`))[0];
    if (b && !["queued", "running"].includes(b.status)) { if (b.status !== "succeeded") throw new Error("build " + b.status); break; }
    if (Date.now() - t0 > 90000) throw new Error("build timeout");
    await sleep(1500);
  }
  const info = await p.evaluate(async (pid) => {
    const r = await fetch(`/api/projects/${pid}/export-arxiv.zip`, { credentials: "same-origin" });
    const buf = new Uint8Array(await r.arrayBuffer());
    const text = new TextDecoder("latin1").decode(buf);
    return { status: r.status, size: buf.length, hasBbl: text.includes("main.bbl"), hasAux: /\.aux|\.log/.test(text), type: r.headers.get("content-type") };
  }, pid);
  if (info.status !== 200 || !info.hasBbl || info.hasAux) throw new Error(JSON.stringify(info));
  return JSON.stringify(info);
});
let taskId;
if (process.env.SKIP_AI) console.log("SKIP AI preset (SKIP_AI set)");
else await step("AI preset 'Terminology': answers from the real orchestrator LLM; no proposal, no file changed", async () => {
  const before = (await api("GET", `/api/projects/${pid}/files/content?path=main.tex`)).sha;
  await clickText("button", "Terminology");
  const t0 = Date.now();
  for (;;) {
    const ts = await api("GET", `/api/projects/${pid}/tasks`);
    const t = ts[0];
    if (t && ["completed", "failed", "cancelled", "needs_review"].includes(t.status)) { taskId = t.id; if (t.status !== "completed") throw new Error(`status ${t.status}: ${t.error}`); if (t.proposal_id) throw new Error("a proposal was created"); break; }
    if (Date.now() - t0 > 900000) throw new Error("timeout after 15 min");
    await sleep(5000);
  }
  const msgs = await api("GET", `/api/projects/${pid}/messages`);
  const answer = msgs.filter((m) => m.role !== "user").pop();
  if (!answer || answer.content.length < 80) throw new Error("no real answer: " + JSON.stringify(answer));
  const after = (await api("GET", `/api/projects/${pid}/files/content?path=main.tex`)).sha;
  if (before !== after) throw new Error("main.tex changed");
  await p.waitForSelector(".ai-messages", { timeout: 5000 });
  await p.screenshot({ path: `${OUT}/f8-preset-answer.png` });
  return `${Math.round((Date.now() - t0) / 1000)} s, ${answer.content.length} chars: ${answer.content.slice(0, 160).replace(/\s+/g, " ")}`;
});
await step("no uncaught page errors", async () => {
  const real = errors.filter((e) => !/cross-origin|Permission denied to access property/i.test(e));
  if (real.length) throw new Error(real.join(" | "));
});
console.log("cleanup delete HTTP", await p.evaluate(async (id) => (await fetch(`/api/projects/${id}`, { method: "DELETE", credentials: "same-origin", headers: { "X-Prism-CSRF": "1" } })).status, pid));
await browser.close();
process.exit(failed ? 1 : 0);
