"""Compile a project snapshot with latexmk inside the sandbox."""

from __future__ import annotations

import os
import re
import shutil
import subprocess
import time
from dataclasses import dataclass, field
from pathlib import Path

from ..config import get_settings
from ..sandbox import Limits, SandboxSpec, bwrap_works, kill_tree, spawn
from ..security.paths import is_protected, safe_join

ENGINES = {"pdflatex": "-pdf", "xelatex": "-pdfxe", "lualatex": "-pdflua"}


@dataclass
class CompileResult:
    status: str  # succeeded | failed | blocked | cancelled
    isolation: str
    exit_code: int | None = None
    log: str = ""
    diagnostics: list[dict] = field(default_factory=list)
    pdf_path: Path | None = None
    duration: float = 0.0
    error: str = ""


def tex_bin_dir() -> str | None:
    s = get_settings()
    if s.tex_bin_dir:
        return s.tex_bin_dir
    lm = shutil.which("latexmk")
    return os.path.dirname(os.path.realpath(lm)) if lm else None


def tex_status() -> dict:
    d = tex_bin_dir()
    if not d or not os.path.exists(os.path.join(d, "latexmk")):
        return {"available": False, "detail": "latexmk not found (set TEX_BIN_DIR)"}
    try:
        v = subprocess.run([os.path.join(d, "pdflatex"), "--version"], capture_output=True, timeout=10).stdout
        line = v.decode(errors="replace").splitlines()[0] if v else ""
    except Exception as e:
        return {"available": False, "detail": str(e)}
    return {"available": True, "detail": line, "bin_dir": d}


def sandbox_status() -> dict:
    s = get_settings()
    if s.compile_sandbox == "bwrap":
        ok, detail = bwrap_works()
        if ok:
            return {"mode": "bwrap", "isolated": True, "detail": detail}
        if s.allow_unsandboxed_compile:
            return {"mode": "none", "isolated": False, "detail": f"{detail}; running WITHOUT isolation (ALLOW_UNSANDBOXED_COMPILE=true)"}
        return {"mode": "blocked", "isolated": False, "detail": f"{detail}; compilation blocked"}
    if s.allow_unsandboxed_compile:
        return {"mode": "none", "isolated": False, "detail": "COMPILE_SANDBOX=none; running WITHOUT isolation"}
    return {"mode": "blocked", "isolated": False, "detail": "COMPILE_SANDBOX=none requires ALLOW_UNSANDBOXED_COMPILE=true"}


def _tex_roots(bin_dir: str) -> list[str]:
    s = get_settings()
    roots = []
    if s.tex_root:
        roots += [r for r in s.tex_root.split(":") if r]
    else:
        real = os.path.realpath(bin_dir)
        # .../texlive/2026/bin/x86_64-linux  or  ~/.TinyTeX/bin/x86_64-linux
        if real.rstrip("/").split("/")[-2] == "bin":
            roots.append(os.path.dirname(os.path.dirname(real)))
    roots += [d for d in ("/etc/texmf", "/var/lib/texmf", "/etc/perl") if os.path.exists(d)]
    return roots


def write_sources(files: dict[str, bytes], src: Path) -> None:
    for rel, data in files.items():
        if is_protected(rel):
            continue  # e.g. latexmkrc from the project is never used
        p = safe_join(src, rel)
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_bytes(data)


def compile_project(files: dict[str, bytes], build_dir: Path, main_file: str, engine: str,
                    should_cancel=lambda: False) -> CompileResult:
    s = get_settings()
    sb = sandbox_status()
    if sb["mode"] == "blocked":
        return CompileResult(status="blocked", isolation="none", error=sb["detail"])
    ts = tex_status()
    if not ts["available"]:
        return CompileResult(status="blocked", isolation=sb["mode"], error=ts["detail"])
    if engine not in ENGINES:
        return CompileResult(status="failed", isolation=sb["mode"], error=f"unknown engine {engine}")
    if main_file not in files:
        return CompileResult(status="failed", isolation=sb["mode"], error=f"main file {main_file!r} not found")

    if build_dir.exists():
        shutil.rmtree(build_dir)
    src = build_dir / "src"
    src.mkdir(parents=True)
    write_sources(files, src)
    out = src / "_prism_out"
    out.mkdir()

    bin_dir = ts["bin_dir"]
    eng_cmd = f"{engine} -no-shell-escape -file-line-error -interaction=nonstopmode -halt-on-error %O %S"
    argv = [
        os.path.join(bin_dir, "latexmk"), "-norc", ENGINES[engine], f"-{engine}={eng_cmd}" if engine != "pdflatex" else f"-pdflatex={eng_cmd}",
        "-interaction=nonstopmode", "-halt-on-error", "-file-line-error", "-outdir=_prism_out",
        "-f-", main_file,
    ]
    env = {
        "PATH": f"{bin_dir}:/usr/bin:/bin",
        "HOME": "/tmp",
        "TEXMFVAR": "/tmp/texmf-var",
        "TEXMFCONFIG": "/tmp/texmf-config",
        "shell_escape": "f",
        "openout_any": "p",
        "openin_any": "p",
        "max_print_line": "10000",
        "error_line": "254",
        "half_error_line": "238",
        "LANG": "C.UTF-8",
        "SOURCE_DATE_EPOCH": "0",
        "FORCE_SOURCE_DATE": "1",
    }
    spec = SandboxSpec(
        argv=argv, workdir=src, env=env, ro_binds=[bin_dir, *_tex_roots(bin_dir)], network=False,
        limits=Limits(memory_mb=s.compile_memory_mb, cpu_seconds=s.compile_cpu_seconds,
                      max_processes=s.compile_max_processes, max_file_mb=s.compile_max_output_mb),
    )
    sandboxed = sb["mode"] == "bwrap"
    t0 = time.monotonic()
    log_file = build_dir / "latexmk.out"
    with open(log_file, "wb") as lf:
        proc = spawn(spec, sandboxed=sandboxed, stdout=lf, stderr=subprocess.STDOUT)
        status = None
        while proc.poll() is None:
            if time.monotonic() - t0 > s.compile_timeout_seconds:
                kill_tree(proc)
                status = "timeout"
                break
            if should_cancel():
                kill_tree(proc)
                status = "cancelled"
                break
            time.sleep(0.1)
    dur = time.monotonic() - t0
    stem = os.path.splitext(os.path.basename(main_file))[0]
    tex_log_p = out / f"{stem}.log"
    tex_log = tex_log_p.read_text(errors="replace") if tex_log_p.exists() else ""
    runner_log = log_file.read_text(errors="replace")
    diags = parse_log(tex_log, main_file)
    for blg in out.glob("*.blg"):
        diags += parse_blg(blg.read_text(errors="replace"))
    pdf = out / f"{stem}.pdf"
    isolation = "bwrap" if sandboxed else "none"
    full_log = tex_log + ("\n\n===== latexmk =====\n" + runner_log[-20000:] if runner_log else "")
    if status == "timeout":
        return CompileResult(status="failed", isolation=isolation, log=full_log, diagnostics=diags, duration=dur,
                             error=f"compilation exceeded {s.compile_timeout_seconds}s and was stopped")
    if status == "cancelled":
        return CompileResult(status="cancelled", isolation=isolation, log=full_log, duration=dur)
    code = proc.returncode
    ok = code == 0 and pdf.exists() and pdf.stat().st_size > 0
    if ok:
        final = build_dir / "output.pdf"
        shutil.copyfile(pdf, final)
        return CompileResult(status="succeeded", isolation=isolation, exit_code=code, log=full_log,
                             diagnostics=diags, pdf_path=final, duration=dur)
    err = next((d["message"] for d in diags if d["severity"] == "error"), "") or f"latexmk exited with code {code}"
    return CompileResult(status="failed", isolation=isolation, exit_code=code, log=full_log, diagnostics=diags,
                         duration=dur, error=err)


# ------------------------------------------------------------- log parsing ---

_FLE = re.compile(r"^(?:\./)?(?P<file>[^\s:()][^:()\n]*?\.(?:tex|sty|cls|bib|ltx|bbl|def|cfg|tikz)):(?P<line>\d+): (?P<msg>.+)$")
_WARN = re.compile(r"^(?:LaTeX|Package (?P<pkg>\S+)|Class \S+) Warning: (?P<msg>.+)$")
_INPUT_LINE = re.compile(r"on input line (\d+)")
_BOX = re.compile(r"^(?P<kind>Overfull|Underfull) \\[hv]box .*? (?:at lines? (?P<l1>\d+)(?:--(?P<l2>\d+))?|has occurred while \\output is active|detected at line (?P<l3>\d+))")
_OPEN_FILE = re.compile(r"\((\./[^\s()]+\.(?:tex|ltx|bbl))")


def parse_log(log: str, main_file: str) -> list[dict]:
    diags: list[dict] = []
    lines = log.splitlines()
    file_stack: list[str] = [main_file]
    seen = set()

    def add(sev, file, line, msg):
        key = (sev, file, line, msg)
        if key in seen:
            return
        seen.add(key)
        diags.append({"severity": sev, "file": file, "line": line, "message": msg.strip()})

    i = 0
    while i < len(lines):
        ln = lines[i]
        for m in _OPEN_FILE.finditer(ln):
            file_stack.append(m.group(1)[2:])
        m = _FLE.match(ln)
        if m:
            msg = m.group("msg")
            # Pull the context line (`l.12 ...`) if present.
            for j in range(i + 1, min(i + 6, len(lines))):
                if lines[j].startswith("l.") or lines[j].startswith("<argument>"):
                    msg += "  [" + lines[j].strip()[:160] + "]"
                    break
            add("error", m.group("file"), int(m.group("line")), msg)
            i += 1
            continue
        if ln.startswith("! "):
            line_no = None
            for j in range(i + 1, min(i + 12, len(lines))):
                mm = re.match(r"l\.(\d+)", lines[j])
                if mm:
                    line_no = int(mm.group(1))
                    break
            add("error", file_stack[-1] if file_stack else main_file, line_no, ln[2:])
            i += 1
            continue
        m = _WARN.match(ln)
        if m:
            msg = m.group("msg")
            j = i + 1
            while j < len(lines) and lines[j].startswith(" ") and lines[j].strip() and j < i + 5:
                msg += " " + lines[j].strip()
                j += 1
            mm = _INPUT_LINE.search(msg)
            add("warning", file_stack[-1] if file_stack else main_file, int(mm.group(1)) if mm else None,
                (f"[{m.group('pkg')}] " if m.group("pkg") else "") + msg)
            i = j
            continue
        m = _BOX.match(ln)
        if m:
            line_no = m.group("l1") or m.group("l3")
            add("info", file_stack[-1] if file_stack else main_file, int(line_no) if line_no else None, ln.strip())
        i += 1
    return diags


def parse_blg(blg: str) -> list[dict]:
    out = []
    for ln in blg.splitlines():
        if ln.startswith("Warning--") or "WARN" in ln[:20]:
            out.append({"severity": "warning", "file": None, "line": None, "message": "BibTeX: " + ln.strip()})
        elif ln.startswith("I couldn't") or ln.startswith("I found no") or "ERROR" in ln[:20]:
            out.append({"severity": "error", "file": None, "line": None, "message": "BibTeX: " + ln.strip()})
    return out
