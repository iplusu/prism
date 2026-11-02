# ARA Solo — N=30 Semgrep-only Feasibility Pilot (Report)

Task: t_7feb44f1 · Run: 2026-11-01 (EST) · Status: complete

## 1. Purpose and scope

PI option 2 (partial pilot, GPU-free parts only), to fill the Preliminary Evidence
placeholders of the ARA solo proposal with measured values. Scope, per PI
instruction: N=30 feature-viability pilot, single SAST tool (Semgrep only —
CodeQL not run; 1 of the 2 SAST tools in the proposal's battery), the
vulnerable-function unit (simplest of the Methods' three instance units).
The full M1–M3 500-instance pilot and the warm p50/p95 in-line-gate latency
measurement (no dedicated gate hardware) are OUT of scope; the proposal already
discloses latency as an M1 cost-pilot item and this pilot does not touch it.

## 2. Setup

### Generation
- Model: **Qwen3-Coder-30B-A3B-Instruct** (MoE 30B-A3B) — a SUBSTITUTE model for
  the proposal-committed Qwen2.5-Coder-14B-Instruct, per PI instruction
  ("use the newest version; 2.5 is too old"; 30B-A3B chosen for tier proximity
  to the 14B-dense spec and VRAM fit). The substitution is disclosed in the
  proposal text, not concealed.
- Serving: llama.cpp b12032 (cudart-ubuntu-cuda-12.8 build), Q4_K_M GGUF
  (unsloth/Qwen3-Coder-30B-A3B-Instruct-GGUF, 18.6 GB file), -ngl 99, ctx 16384,
  --jinja chat template, -np 8; on viper H100 (96 GB, ~57 GB free at run time;
  no Hermes-serving processes touched).
- Generation config: temperature 0.7, max_tokens 8192, single prompt per
  instance (neutral feature spec; no security hints in either direction),
  code extracted from the single ```python block.
- **Superseded pass:** an initial generation pass on a5000
  (qwen38-a5000 = Qwen3.8-27B-FP8 via the Hermes serving endpoint) was stopped
  on PI instruction (endpoint interference). Its 30 outputs are preserved in
  `raw_a5000_superseded/` for the archive only and are NOT part of the reported
  numbers.
- Specs: 6 CWEs (79, 89, 918, 78, 22, 502) × 5 neutral feature requests each =
  30 instances (full spec text in results.json → instances[].spec).

### Verification
- Buildable: `python3 -m py_compile` per generated file (syntax-level, per task
  definition).
- SAST: Semgrep 1.183.0 (OSS engine) with the **standard registry pack
  p/python (151 rules)**, pinned to a local YAML snapshot
  (`semgrep/pack_p_python.yaml`, sha256 in `semgrep/pack_fetch_meta.json`);
  both runs use the identical snapshot. Command:
  `semgrep scan --config pack_p_python.yaml --json --no-git-ignore --timeout 60 <code_dir>`.
- Two runs (run1/run2), full JSON results preserved in `semgrep/scan_run*.json`.
- Per-file verdict: target-CWE verdict = has ≥1 finding whose rule is tagged
  with the instance's target CWE; strict verdict = full (rule, line) set.

### Cost assumptions (stated; pilot ran self-hosted, API-equivalent basis)
- LLM: equivalent public API rates for the SAME model, Amazon Bedrock
  qwen/qwen3-coder-30b-a3b-instruct listing (Oct 2026): **$0.15 / $0.60 per
  1M input/output tokens**. Token counts from the server's usage field (no
  estimation needed).
- Semgrep CPU: $0.10 per CPU-hour (wall time of both runs, attributed evenly).
- Sensitivity only: self-hosted GPU amortization $0.50 per GPU-hour.

## 3. Results (the six metric slots)

| # | Metric | Measured | Basis |
|---|--------|----------|-------|
| 1 | Buildable yield | **100% (30/30)** | py_compile |
| 2 | Witness fraction | **0% (0/30)** | Semgrep p/python standard pack, OSS; buildable denominator; Semgrep is 1 of the 2 proposal SAST tools |
| 3 | Two-run reproduction | **100% (30/30)** | identical verdict, target-level AND strict finding-set level (deterministic, as expected for a static tool — measured, not assumed) |
| 4 | Warm p50/p95 latency | **not measured** | out of scope (no dedicated gate hardware); remains an M1 cost-pilot item; proposal disclosure unchanged |
| 5 | Oracle cost per resolved label | **undefined (0 resolved labels)** | total pilot cost **$0.0022** (LLM $0.0019 + Semgrep $0.0003) over 30 attempts; per-attempt $7.3×10⁻⁵; generation wall time 140.2 s total (4-way parallel), Semgrep 5.2 s per run |
| 6 | Paired-cluster variability | **$2.7×10⁻⁵** | SD across the 6 CWE clusters of mean per-instance cluster cost (n=6). Per-resolved-label SD is undefined (0 resolved in every cluster). The pre-registered planning assumption (assumption-based SD 0.50, power-analysis basis) is NOT replaced by this — it is a different quantity and a different (confirmatory) sample; this pilot value is recorded alongside as the first measured data point. |

## 4. Why the witness fraction is 0 — decomposition (no selection, all raw preserved)

The 0% is structural, not a tool malfunction. Two independent, verified causes:

(a) **Rule-source mismatch (oracle-side coverage limit).** Every security rule
in the standard p/python pack that could apply to these CWEs is scoped to a
web-framework request source (`request.*.get(...)`, `flask.request.*`,
`@app.route`-decorated views, AWS-Lambda `event` handler parameters) or is a
taint-mode rule whose declared sources are those same framework inputs. The
standalone-function unit has no request source by construction, so those rules
cannot fire regardless of code content. Verified directly: the pack's 151 rules
were inspected (rule→CWE map in `semgrep/rule_cwe_map.json`, per-CWE coverage
in `semgrep/cwe_rule_coverage.json`: CWE-22:3, CWE-78:21, CWE-79:8, CWE-89:13,
CWE-502:5, CWE-918:4 rules) and a deliberately vulnerable control file
(os.system concatenation, pickle.loads, string-concat SQL, direct-URL fetch,
f-string HTML) produced **0 findings** under both `--config p/python` and the
pinned snapshot, while a trivial standalone os.system pattern rule on the same
file produced 1 finding (engine works). Notably, the pack contains **no
framework-agnostic pickle.loads rule at all** — every deserialization instance
(5/5) is therefore oracle-blind under this configuration.

(b) **The generator did not always instantiate the vulnerability
(instance-side).** Manual inspection (human check, explicitly NOT an oracle
witness; per-instance classes in `manual_inspection.json`) found the canonical
vulnerable pattern in 20/30 instances and injection-safe code in 10/30:
- vulnerable 5/5: CWE-502 (pickle.loads), CWE-22 (unvalidated path
  concatenation), CWE-79 (unescaped f-string HTML), CWE-918 (direct fetch of
  caller-provided URL);
- safe 5/5: CWE-89 (parameterized `?`/named queries — no SQLi instantiated),
  CWE-78 (list-form subprocess argv, no shell=True — no OS-injection
  instantiated).
Qwen3-Coder is a security-aware coder model; on exactly the CWE classes where
safe idioms are standard (parameterized SQL, argv lists), it wrote the safe
idiom. A witness was therefore impossible on 10/30, and on the other 20/30 the
standard pack was structurally blind (cause a).

### Implications for the proposal (preliminary, n=30)
- The vulnerable-function unit, under a security-aware current-gen coder model,
  yields a mixed instantiation rate (2/3 vulnerable here) — the corpus plan's
  **seeded-bug control** unit and the **public-repository task** unit (where
  code sits in real framework context and the pack's taint rules can fire) are
  both needed to reach the per-CWE witness-share minima fixed at M3.
- The standard single-tool SAST baseline is weaker than the proposal's battery
  implies for the function unit — corroborating the design's two-tool battery,
  the executable-witness requirement, and the declared SAST-coverage boundary
  ("static-oracle fallback", "SAST-coverage-limited stratum").
- Cost structure: generation dominates (~86% of pilot cost), Semgrep is
  negligible; per-attempt cost is 2-3 orders of magnitude below any budget
  line, and the undefined per-resolved cost is a coverage/instantiation effect,
  not a compute effect.

## 5. Limitations
- n=30, single generation pass per spec (no seed variation), single
  quantization (Q4_K_M), single SAST tool and single rule configuration
  (standard pack, OSS engine), function unit only.
- Cost is API-equivalent at stated assumed rates; the actual run was
  self-hosted (sensitivity figure included in results.json).
- Manual inspection is a human check for diagnosis only; it is not reported
  anywhere as an oracle witness.
- Two-run reproduction tests the tool (static analyzer), not the generator;
  generator non-determinism at temp 0.7 is out of this pilot's scope.

## 6. Reproducibility / archive layout
```
pilot_n30_semgrep/
├── pilot.py                  # single self-contained, resumable script
├── results.json              # all per-instance data + aggregates
├── manual_inspection.json    # human per-instance vulnerable/safe classification
├── specs (in results.json)   # the 30 natural-language specs
├── raw/                      # 30 raw model outputs (canonical viper pass)
├── raw_a5000_superseded/     # superseded a5000 pass (archive only)
├── code/                     # 30 extracted .py files (the scanned corpus)
└── semgrep/
    ├── pack_p_python.yaml    # pinned 151-rule snapshot
    ├── pack_fetch_meta.json  # fetch time + sha256
    ├── rule_cwe_map.json     # rule → CWE metadata
    ├── cwe_rule_coverage.json
    ├── scan_run1.json / scan_run2.json (+ stderr + wall-time meta)
```
Re-run: `python3 pilot.py` (resumable; generation re-runs only if raw/ is
emptied; Semgrep runs re-execute on the pinned pack).
