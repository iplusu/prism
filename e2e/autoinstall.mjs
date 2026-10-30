// A paper that needs a TeX package the server lacks compiles with ONE press of Compile: the worker installs
// the package with the real tlmgr and rebuilds. Remove the package first (`tlmgr remove multirow`) to repeat.
// usage: PRISM_URL=https://host node autoinstall.mjs <admin password>
import puppeteer from "puppeteer-core";

const B = process.env.PRISM_URL || "http://127.0.0.1:8000";
const [ADMIN_PW] = process.argv.slice(2);
const browser = await puppeteer.launch({ browser: "firefox", executablePath: "/usr/bin/firefox", headless: true });
const p = await (await browser.createBrowserContext()).newPage();
const api = (method, path, body) => p.evaluate(async (method, path, body) => {
  const r = await fetch(path, { method, headers: { "X-Prism-CSRF": "1", "Content-Type": "application/json" }, body: body ? JSON.stringify(body) : undefined });
  return { status: r.status, json: await r.json().catch(() => null) };
}, method, path, body);
let failed = 0, pid = null;
const check = (name, ok, extra = "") => { console.log(ok ? "PASS" : "FAIL", name, extra); if (!ok) failed++; };
await p.goto(B + "/", { waitUntil: "networkidle0" });
await p.type("input[autocomplete=username]", "admin"); await p.type("input[type=password]", ADMIN_PW); await p.click("button[type=submit]");
await p.waitForFunction(() => !document.querySelector("input[type=password]"), { timeout: 15000 });
const r = await api("POST", "/api/projects", { name: "autoinstall-check (temporary)", template: "blank" });
pid = r.json.id;
const sha = (await api("GET", `/api/projects/${pid}/files/content?path=main.tex`)).json.sha;
await api("PUT", `/api/projects/${pid}/files/content`, { path: "main.tex", base_sha: sha, text: "\\documentclass{article}\n\\usepackage{multirow}\n\\begin{document}\n\\begin{tabular}{|c|c|}\\hline \\multirow{2}{*}{A} & B \\\\ \\hline & C \\\\ \\hline\\end{tabular}\n\\end{document}\n" });
const b = (await api("POST", `/api/projects/${pid}/builds`)).json;
let got = null;
for (let i = 0; i < 240 && !got; i++) { const x = (await api("GET", `/api/builds/${b.id}`)).json; if (!["queued", "running"].includes(x.status)) got = x; else await new Promise((r) => setTimeout(r, 500)); }
check("one Compile press -> the build succeeds", got?.status === "succeeded", `status=${got?.status} ${got?.error ?? ""}`);
check("the log says which package was installed and that it rebuilt", /^\[Prism\] installed TeX package\(s\) multirow and rebuilt/.test(got?.log ?? ""));
check("a PDF exists", (await api("GET", `/api/builds/${b.id}`)).json.has_pdf === true);
console.log("cleanup delete HTTP", (await api("DELETE", `/api/projects/${pid}`)).status);
await browser.close();
process.exit(failed ? 1 : 0);
