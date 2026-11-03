#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
ARA solo — M7-9 estimator-level design simulation (pre-M3, CPU-only)
====================================================================
시뮬레이션 대상 = 실제 M7-9 설계:
  2 policies (bundled targeted-scheduling vs standard-SFT)
  x 3 paired seeds x 10 rounds = 60 adapter updates,
공유 initial-adapter fit, 66 policy/seed checkpoints (t=0..10).
Primary endpoint: recall>=0.80 AND precision>=0.80을 처음 동시 충족하는
checkpoint (policy별/seed별 first-qualification round; t=0 포함 검색).

목적: 실제 GPU 루프(M7-9) 전, 합성 궤적으로 통계 설계의 추정 가능성을 검증.
  (a) policy별 primary qualification 확률 (10라운드 내 endpoint 도달)
  (b) targeted vs standard-SFT 차이의 conditional power
  (c) seed=3이 너무 적은지 (variance / CI 폭)

경고 (가드레일): 모든 DGP 상수는 선언적 ASSUMPTION. witness-yield 그리드
(10/20/30%)는 현재 측정되지 않은 repo-context/seeded-bug 유닛에 대한 사전
가정 범위이며, vulnerable-function 유닛 + Semgrep 단일오라클의 N=30 실측
witness fraction 0% (0/30) 와는 다른 양이다. REPORT.md §4 참조.

재현:  python3 sim.py [seed]   (기본 20261102)
산출:  results.json, REPORT.md (이 파일과 같은 디렉토리)
"""
import json
import itertools
import sys
import time

import numpy as np

# ----------------------------- 파라미터 (전부 assumption) -----------------------------
SEED = int(sys.argv[1]) if len(sys.argv) > 1 else 20261102
N_ROUNDS = 10                    # 60 adapter updates = 2 policies x 3 seeds x 10
CHECKPOINTS = N_ROUNDS + 1       # 66 = 2 x 3 x 11 (t=0 포함)
THR = 0.80                       # endpoint 임계: recall AND precision >= 0.80
MC = 15_000                      # 셀당 몬테카를로 반복 (>= 10,000 요구)

# DGP (표시용 placeholder — 중간 그리드가 informative하도록 보정)
R0, S0 = 0.55, 0.03              # 공유 initial-adapter fit: seed별 recall/precision 초기값
INIT_CLIP = (0.40, 0.70)         # 초기값 클리핑 (t=0은 0.70 < 0.80 이므로 endpoint 불가)
G_R, G_P = 0.30, 0.27            # 라운드당 최대 개선 상수 (recall, precision)
H_STD = 0.25                     # standard-SFT: CWE 타겟팅 없음, 데이터 믹스 의한 중간 농도 (고정)
XI_SD, XI_LO, XI_HI = 0.2, 0.2, 2.0   # 라운드 노이즈 multiplier ~ N(1, 0.2) 클리핑 (policy 공유 → paired)
YIELDS = (0.10, 0.20, 0.30)      # witness-yield 그리드 (사전 가정 범위 — 미측정 유닛용)
# 6 CWE 클러스터 = 파일럿과 동일한 6개 CWE (79, 89, 918, 78, 22, 502)
CLUSTERS = {
    "low":  (0.20, 0.18, 0.18, 0.17, 0.15, 0.12),   # 이질성 낮음
    "med":  (0.30, 0.22, 0.15, 0.13, 0.10, 0.10),   # 중간
    "high": (0.45, 0.25, 0.12, 0.08, 0.06, 0.04),   # 이질성 높음 (top-1 우세)
}
N_SEEDS_MAIN = 3                 # 실제 설계 (pair = seed, policy 2개 동일 seed 공유)
N_SEEDS_ALT = 6                  # (c) seed=3 충분성 대비용 민감도 분석

# ----------------------------- 시뮬레이션 -----------------------------
def simulate(rng, B, S, y, w):
    """B개 MC run x S paired seeds. 공유 초기값 + 공유 라운드 노이즈 (paired).
    targeted: top-2 CWE 클러스터 스케줄링 -> hit rate = w_1 + w_2
    standard : hit rate = H_STD (고정)
    m_t = min(1, m_0 + sum_{t'<=t} G*y*h*xi_{t'})  (모든 gain >= 0 이므로 하한 클립 불필요)
    """
    hT = sum(sorted(w, reverse=True)[:2])
    hS = H_STD
    r0 = rng.normal(R0, S0, (B, S)).clip(*INIT_CLIP)
    p0 = rng.normal(R0, S0, (B, S)).clip(*INIT_CLIP)   # precision도 동일 초기 분포
    xi = rng.normal(1.0, XI_SD, (B, S, N_ROUNDS)).clip(XI_LO, XI_HI)
    out = {}
    for name, G, h in (("T", G_R, hT), ("S", G_R, hS)):
        r = np.concatenate([r0[..., None], r0[..., None] + np.cumsum(G * y * h * xi, axis=2)], axis=2)
        r = np.minimum(1.0, r)
        p = np.concatenate([p0[..., None], p0[..., None] + np.cumsum(G_P * y * h * xi, axis=2)], axis=2)
        p = np.minimum(1.0, p)
        out[name] = (r, p)
    Y, T = {}, {}
    for name, (r, p) in out.items():
        qual = (r >= THR) & (p >= THR)            # (B, S, 11)
        T[name] = np.where(qual.any(axis=2), qual.argmax(axis=2), 99)
        Y[name] = qual.any(axis=2)
    return Y["T"], Y["S"], T["T"], T["S"]

# --------------------- paired cluster-bootstrap (exact) ---------------------
TR3 = np.array(list(itertools.product(range(3), repeat=3)))     # (27, 3)
TR6 = np.array(list(itertools.product(range(6), repeat=3)))     # (216, 6)

def boot(X, triples):
    """X (B,S) -> 95% 퍼센타일 CI (B,2). 각 재표본은 S개 클러스터를 all-with-replacement
    재채택 (재표본 크기 = 원 클러스터 수 = 100% >= 요구된 95% 하한).
    S^3 전체 재표본을 정확(구) 열거 -> 내부 MC 오차 0."""
    st = X[:, None, :][..., triples].mean(axis=-1).reshape(X.shape[0], -1)  # (B, K)
    return np.percentile(st, 2.5, axis=1), np.percentile(st, 97.5, axis=1)

def pattern_cp(n_seeds):
    """관측 가능 pattern (a,b,c,d) = seed별 (Y_T,Y_S) ∈ {(1,1),(1,0),(0,1),(0,0)} 개수.
    pattern별 conditional power = 그 데이터에서 paired bootstrap 95% CI(Δ)가 0을
    양의 방향으로 배제하는지 (exact 열거). M6 규칙: 95% paired interval이 0 포함 = inconclusive."""
    tr = TR3 if n_seeds == 3 else TR6
    dvec_from = {(1, 1): 0, (1, 0): 1, (0, 1): -1, (0, 0): 0}
    pats = [(a, b, c, d) for a in range(n_seeds + 1) for b in range(n_seeds + 1 - a)
            for c in range(n_seeds + 1 - a - b) for d in (n_seeds - a - b - c,)]
    out = {}
    for (a, b, c, d) in pats:
        dv = np.array([dvec_from[k] for k in ((1, 1),) * a + ((1, 0),) * b + ((0, 1),) * c + ((0, 0),) * d])
        lo, hi = boot(dv[None, :], tr)
        out[(a, b, c, d)] = bool((lo[0] > 0) and (hi[0] > 0))
    return out

def perm_min_p(n_seeds):
    """exact paired permutation test의 실현 가능한 최소 p-value (모든 pair outcome pattern)."""
    pats = [(a, b, c, d) for a in range(4) for b in range(4 - a) for c in range(4 - a - b)
            for d in (3 - a - b - c,)]
    dvec_from = {(1, 1): 0, (1, 0): 1, (0, 1): -1, (0, 0): 0}
    flips = np.array(list(itertools.product((1, -1), repeat=n_seeds)))
    min_p = 1.0
    for (a, b, c, d) in pats:
        dv = np.array([dvec_from[k] for k in ((1, 1),) * a + ((1, 0),) * b + ((0, 1),) * c + ((0, 0),) * d], float)
        obs = abs(dv.sum())
        min_p = min(min_p, (np.abs(flips @ dv) >= obs - 1e-12).mean())
    return min_p

def cell_stats(YT, YS, TT, TS, B, S, tr, pat_cp):
    pT, pS = YT.mean(), YS.mean()
    seT = np.sqrt(pT * (1 - pT) / (B * S))
    seS = np.sqrt(pS * (1 - pS) / (B * S))
    loT, hiT = boot(YT, tr)
    loS, hiS = boot(YS, tr)
    d = YT.astype(float) - YS.astype(float)
    loD, hiD = boot(d, tr)
    qT = TT[TT < 99]
    qS = TS[TS < 99]
    # pattern 집계
    keys = np.stack([(YT & YS).sum(1), (YT & ~YS).sum(1), (~YT & YS).sum(1), (~YT & ~YS).sum(1)], axis=1)
    uniq, cnt = np.unique(keys, axis=0, return_counts=True)
    pats = {tuple(int(v) for v in u): int(c) for u, c in zip(uniq, cnt)}
    return {
        "true_p": {"targeted": [round(pT, 5), round(seT, 5)],
                    "standard": [round(pS, 5), round(seS, 5)]},
        "first_qual_round_median": {"targeted": float(np.median(qT)) if len(qT) else None,
                                     "standard": float(np.median(qS)) if len(qS) else None},
        "p_ci_width": {"targeted": [round(float((hiT - loT).mean()), 4),
                                     round(float(np.percentile(hiT - loT, 95)), 4)],
                        "standard": [round(float((hiS - loS).mean()), 4),
                                      round(float(np.percentile(hiS - loS, 95)), 4)]},
        "p_ci_coverage": {"targeted": round(float(((loT <= pT) & (hiT >= pT)).mean()), 4),
                          "standard": round(float(((loS <= pS) & (hiS >= pS)).mean()), 4)},
        "delta_ci": {"excludes0_positive": round(float(((loD > 0) & (hiD > 0)).mean()), 4),
                     "contains0_inconclusive": round(float(((loD <= 0) & (hiD >= 0)).mean()), 4),
                     "excludes0_negative": round(float(((hiD < 0) & (loD < 0)).mean()), 4),
                     "width_mean": round(float((hiD - loD).mean()), 4),
                     "width_p95": round(float(np.percentile(hiD - loD, 95)), 4),
                     "width_ge_0p5_frac": round(float((hiD - loD >= 0.5).mean()), 4)},
        "patterns": {",".join(map(str, k)): [int(cnt), bool(pat_cp[k])]
                     for k, cnt in sorted(pats.items(), key=lambda kv: -kv[1])},
    }

# ----------------------------- main -----------------------------
def main():
    t0 = time.time()
    rng = np.random.default_rng(SEED)
    pc3, pc6 = pattern_cp(3), pattern_cp(6)
    perm_min = perm_min_p(3)
    results = {"meta": {"seed": SEED, "mc_per_cell": MC, "n_rounds": N_ROUNDS,
                         "checkpoints": CHECKPOINTS, "thr": THR,
                         "numpy": np.__version__,
                         "wall_s": None},
               "dgp": {"R0_S0": [R0, S0], "init_clip": list(INIT_CLIP),
                        "G_R_G_P": [G_R, G_P], "H_STD": H_STD,
                        "xi": [1.0, XI_SD, XI_LO, XI_HI],
                        "h_targeted": "sum(top-2 cluster weights)",
                        "clusters": CLUSTERS, "yields": list(YIELDS)},
               "pilot_n30": {"witness_fraction_measured": "0% (0/30)",
                              "unit": "vulnerable-function",
                              "oracle": "Semgrep p/python 단일 오라클 (CodeQL 미실행)",
                              "cwe_clusters": [79, 89, 918, 78, 22, 502],
                              "buildable_yield": "100% (30/30)",
                              "source": "grants/ara-solo/pilot_n30_semgrep/ (2026-11-01)"}}
    cells = {}
    for y in YIELDS:
        for cname, w in CLUSTERS.items():
            key = f"y{int(y*100):02d}_c_{cname}"
            YT, YS, TT, TS = simulate(rng, MC, N_SEEDS_MAIN, y, w)
            Y6T, Y6S, T6T, T6S = simulate(rng, MC, N_SEEDS_ALT, y, w)
            cells[key] = {
                "yield": y, "concentration": cname,
                "n3": cell_stats(YT, YS, TT, TS, MC, 3, TR3, pc3),
                "n6": cell_stats(Y6T, Y6S, T6T, T6S, MC, 6, TR6, pc6),
            }
            print(f"{key}: pT={cells[key]['n3']['true_p']['targeted'][0]:.3f} "
                  f"pS={cells[key]['n3']['true_p']['standard'][0]:.3f} "
                  f"CP3={cells[key]['n3']['delta_ci']['excludes0_positive']:.3f} "
                  f"wD3={cells[key]['n3']['delta_ci']['width_mean']:.3f} "
                  f"CP6={cells[key]['n6']['delta_ci']['excludes0_positive']:.3f} "
                  f"wD6={cells[key]['n6']['delta_ci']['width_mean']:.3f}")
    results["cells"] = cells
    results["meta"]["wall_s"] = round(time.time() - t0, 1)
    results["permutation_min_p_n3"] = perm_min
    selfcheck(results)
    write_report(results)
    here = "/home/ubuntu/grants/ara-solo/design_sim"
    with open(f"{here}/results.json", "w") as f:
        json.dump(results, f, indent=2, ensure_ascii=False)
    print(f"done in {results['meta']['wall_s']} s -> results.json, REPORT.md")

# ----------------------------- self-check -----------------------------
def selfcheck(res):
    # 1) endpoint 로직 수동 검증: t=3에서 recall, t=4에서 precision 도달 -> T=4
    r = np.array([[0.50, 0.75, 0.79, 0.81, 0.85, 0.85, 0.85, 0.85, 0.85, 0.85, 0.85]])
    p = np.array([[0.90, 0.90, 0.90, 0.79, 0.81, 0.81, 0.81, 0.81, 0.81, 0.81, 0.81]])
    qual = (r >= THR) & (p >= THR)
    assert qual.any() and qual.argmax() == 4, "endpoint first-qualification index error"
    # 2) bootstrap exactness: marginal uniformity + known-data CI
    #    Y=[1,0,0]: 27 재표본 값 = {0:8, 1/3:12, 2/3:6, 1:1} -> percentile(2.5)=0,
    #    percentile(97.5) = interp(idx 25.35) = 2/3 + 0.35*(1/3) = 47/60
    assert all(int((TR3 == i).sum()) == 27 for i in range(3)), "bootstrap marginal not uniform"
    lo, hi = boot(np.array([[1, 0, 0]], float), TR3)
    assert abs(lo[0]) < 1e-9 and abs(hi[0] - 47 / 60) < 1e-9, "known-data bootstrap CI wrong"
    # 3) yield 단조성 (targeted, 각 concentration): p(0.30) >= p(0.10) - 여유
    for c in CLUSTERS:
        p_lo = res["cells"][f"y10_c_{c}"]["n3"]["true_p"]["targeted"][0]
        p_hi = res["cells"][f"y30_c_{c}"]["n3"]["true_p"]["targeted"][0]
        assert p_hi >= p_lo - 0.015, f"yield monotonicity broken at {c}"
    # 4) DGP 배관 교차검증: 중간 셀 targeted 200k 독립 표본 vs run 풀링 추정
    rng = np.random.default_rng(SEED + 1)
    YT, YS, TT, TS = simulate(rng, 200_000, 1, 0.20, CLUSTERS["med"])
    pooled = res["cells"]["y20_c_med"]["n3"]["true_p"]["targeted"][0]
    assert abs(YT.mean() - pooled) < 0.01, "independent DGP draw disagrees with pooled estimate"
    # 5) n=3 exact permutation test: 실현 가능 최소 p-value
    #    d=(1,1,1): |sum| flips = {3:2, 1:6} -> p=2/8=0.25 (모든 pattern 중 최소)
    assert abs(res["permutation_min_p_n3"] - 0.25) < 1e-9, "permutation min-p wrong"
    print("self-check: 5/5 PASS")

# ----------------------------- REPORT.md -----------------------------
def write_report(r):
    c = r["cells"]
    meta = r["meta"]
    def row3(tag):
        out = []
        for y in YIELDS:
            for cn in ("low", "med", "high"):
                k = f"y{int(y*100):02d}_c_{cn}"
                t = c[k][tag]
                pT, seT = t["true_p"]["targeted"]
                pS, seS = t["true_p"]["standard"]
                d = t["delta_ci"]
                out.append(
                    f"| {int(y*100)}% | {cn} | {pT:.3f} ± {seT:.3f} | {pS:.3f} ± {seS:.3f} "
                    f"| {d['excludes0_positive']:.3f} | {d['contains0_inconclusive']:.3f} "
                    f"| {d['width_mean']:.2f} | {d['width_p95']:.2f} | {d['width_ge_0p5_frac']:.2f} |")
        return "\n".join(out)
    def row6(tag):
        out = []
        for y in YIELDS:
            for cn in ("low", "med", "high"):
                k = f"y{int(y*100):02d}_c_{cn}"
                t = c[k][tag]
                pT = t["true_p"]["targeted"][0]
                d = t["delta_ci"]
                pt = t["p_ci_width"]["targeted"]
                out.append(f"| {int(y*100)}% | {cn} | {pT:.3f} | {d['excludes0_positive']:.3f} "
                           f"| {d['width_mean']:.2f} | {d['width_p95']:.2f} | {pt[0]:.2f} |")
        return "\n".join(out)
    # 대표 pattern 테이블 (중간 셀)
    pm = c["y20_c_med"]["n3"]["patterns"]
    pat_rows = "\n".join(
        f"| ({a},{b},{c},{d}) | {n} | {'있음 (0 배제)' if cp else '없음 (0 포함 → inconclusive)'} |"
        for (k, (n, cp)) in sorted(pm.items(), key=lambda kv: -kv[1][0])
        for (a, b, c, d) in [tuple(map(int, k.split(",")))]
    )
    vd = seed3_verdict(r)
    md = f"""# ARA solo — M3 전 estimator-level design simulation (REPORT)

- Task: t_bea2653e · 실행: {time.strftime('%Y-%m-%d')} (EST) · 머신: local (CPU-only, GPU/LLM 호출 없음)
- 재현: `cd /home/ubuntu/grants/ara-solo/design_sim && python3 sim.py [seed]` (기본 seed `{meta['seed']}`, numpy {meta['numpy']}, 소요 {meta['wall_s']}s)
- 산출: `sim.py` (코드), `results.json` (전체 수치), 이 리포트

## 1. 목적과 범위

PI 요청(원안 섹션 (f) "M3 동결 전 estimator-level design simulation")에 따라, M7-9
(self-improvement loop)의 통계적 설계를 **실제 GPU 루프를 돌리기 전에** 순수
Python/numpy 몬테카를로 시뮬레이션으로 검증. M7-9의 paired 구조
(2 policies × 3 paired seeds × 10 rounds = 60 adapter updates, 공유 initial-adapter fit,
66 policy/seed checkpoints, primary endpoint = recall≥0.80 AND precision≥0.80을 **처음**
동시 충족하는 checkpoint)을 그대로 따르는 합성 궤적에서 (a) policy별 qualification
확률, (b) policy 차이의 conditional power, (c) seed=3의 충분성(CI 폭)을 평가.

**범위 외**: 실제 LoRA 파인튜닝/LLM 생성 없음. 이 시뮬레이션의 DGP 상수는 전부
선언적 가정이며, **전가 가능(transferrable)한 결론은 estimator-level 결과뿐**이다.

## 2. 시뮬레이션 대상 설계 (M7-9)

| 항목 | 값 |
|---|---|
| policies | bundled targeted-scheduling (T) vs standard-SFT (S) |
| paired seeds | 3 (seed = 독립 단위, 2 policy가 동일 seed/초기fit 공유) |
| rounds | 10 (adapter updates 60 = 2×3×10) |
| checkpoints | 66 = 2×3×11 (t=0 포함) |
| primary endpoint | (policy,seed)별 recall≥0.80 AND precision≥0.80 **최초 동시 충족** 라운드 (10 라운드 내 도달 여부) |

## 3. DGP — 가정 (전부 placeholder)

- 초기값: seed별 recall0 = precision0 ~ N(0.55, 0.03), [0.40, 0.70] 클리핑, **2 policy 공유** (공유 initial-adapter fit). 초기값 ≤0.70 < 0.80이라 t=0은 endpoint 불가(구조적).
- 라운드 과정: m_t = min(1, m_0 + Σ G·y·h_q·ξ_t), m ∈ {{recall, precision}}. G_recall=0.30, G_precision=0.27, ξ_t ~ N(1, 0.2)를 [0.2, 2.0] 클리핑. **ξ는 2 policy가 공유** (pairing — 동일 seed/동일 라운드의 환경적 변동).
- hit rate h: targeted = top-2 CWE 클러스터 가중치 합 (CWE 클러스터별 witness 농도 w에 스케줄링 집중), standard = 고정 0.25 (CWE 타겟팅 없음, 데이터 믹스에 의한 중간 농도).
- 변동 요인 2종 (명시적 모수화): ① witness-yield y ∈ {{10%, 20%, 30%}} (후 §4), ② 6 CWE 클러스터 간 witness 농도 이질성 — 결정적 3 프로필 (파일럿과 동일 CWE 6종: 79/89/918/78/22/502):
  - low  (0.20, 0.18, 0.18, 0.17, 0.15, 0.12) — 이질성 낮음 → top-2 집중 이득 소폭
  - med  (0.30, 0.22, 0.15, 0.13, 0.10, 0.10)
  - high (0.45, 0.25, 0.12, 0.08, 0.06, 0.04) — top-1 우세 → targeted 이득 최대
- 보정 상태: G·h 상수는 "중간 그리드(y=20%, med)에서 targeted가 informative 중간 범위(약 0.5–0.8)에 위치"하도록 선택. **절대 확률치는 예측이 아님.**

## 4. witness-yield 그리드 vs N=30 실측 (가드레일 — 반드시 함께 읽을 것)

| 항목 | witness yield | 성질 |
|---|---|---|
| vulnerable-function 유닛 + Semgrep 단일 오라클 (CodeQL 미실행) | **실측 0% (0/30)** | N=30 파일럿 실측 (2026-11-01, `pilot_n30_semgrep/`): 표준 p/python pack의 security rule은 web-framework request source를 요구 → standalone-function 유닛에서 구조적 0 |
| repo-context / seeded-bug 유닛 (현재 측정 안 됨) | **10 / 20 / 30% 그리드** | 본 시뮬레이션의 **사전 가정 범위** (prior assumption range) — 미측정 유닛 + 멀티오라클 배터리에 대한 계획치 |

- **이 시뮬레이션의 최저 시나리오(10%)조차 vulnerable-function 단일 유닛 + Semgrep 단일오라클 조합의 실측치(0%)보다 낙관적**이다. 이 낙관도는 감추지 않고 그대로 명시한다.
- 따라서 그리드와 실측 0%는 **다른 양**이다: 실측 0%는 (가장 단순한) 단일 유닛 + 단일 오라클 조합의 하한측정이며, 그리드는 실제 M7-9가 사용할 (repo-context/seeded-bug 유닛 × CodeQL+Semgrep+실행 오라클 배터리) 조합에 대한 미측정 대상의 사전 범위. 두 수치를 같은 척도로 혼동하여 "그리드가 실측과 일치한다"거나 "10%가 보수적이다"고 읽어서는 안 된다.
- 제안서 본문 Preliminary Evidence의 기존 서술("the null witness result motivates the repository-context and seeded-bug units")과 일관됨: 0% 실측이 오히려 유닛 확장의 동기가 되며, 그 확대된 유닛의 yield는 미측정이므로 계획 단계에서는 그리드로만 다룸.

## 5. 추정량 — paired cluster-bootstrap (정확 열거)

- 클러스터(독립 단위) = paired seed. 각 bootstrap 재표본은 S개 seed를 **all-with-replacement** 재채택: 재표본 크기 = 원 클러스터 수(3/3 또는 6/6) = 원 집단의 **100% ≥ 요구된 95% 재표본 비율 하한**.
- 3 seed 시 재표본 총 3³=27종, 6 seed 시 6³=216종 — **전체 재표본을 정확(구) 열거**하여 내부 MC 오차 0. 95% CI = 퍼센타일(선형 보간).
- Δ = p̂_T − p̂_S는 동일 재표본 seed로 2 policy를 함께 계산 (paired).
- 판정 규칙 (제안서 M6 규칙과 동일): 95% paired interval이 0 포함 → inconclusive (효과 부정이 아님).
- cross-check: exact paired permutation test(2³ sign-flip)의 n=3 실현 가능 최소 p-value = **{r['permutation_min_p_n3']:.2f} > 0.05** (최소값은 3/3 일치라는 가장 강한 데이터 패턴에서도) — 즉 n=3에서는 permutation test가 α=0.05에서 원리적으로 기각 불가. 따라서 CI 기반 판정(위)이 실제 운용 기준이며, 이것이 estimator-level 검증 대상.

## 6. 결과

설계: 셀당 MC {meta['mc_per_cell']:,} 반복 (총 {meta['mc_per_cell']*9*2:,} run), {meta['wall_s']}s. CI 폭은 확률 단위 (0.40 = 40%p).

### 6.1 (a) policy별 primary qualification 확률 — DGP 참값 + n=3 추정 불안정성

| yield | 농도 | P(qualify) targeted (SE) | P(qualify) standard (SE) | Δ CI 0배제(+) | Δ CI 0포함 | Δ CI 폭 mean | Δ CI 폭 P95 | Δ 폭≥0.5 run비율 |
|---|---|---|---|---|---|---|---|---|
{row3("n3")}

(참값 SE = MC 표준오차(≤0.002). Δ CI 폭·비율은 **n=3 seed로 실제로 측정할 추정량**의 셀별 분포. coverage는 §6.3 참고.)

### 6.2 (b) conditional power — targeted vs standard-SFT 차이

- 무조건적 CP (95% paired bootstrap CI가 Δ=0을 양 방향으로 배제할 run 비율, = M6 규칙에서 "targeted 우위"로 판정되는 비율): 표 6.1의 "Δ CI 0배제(+)" 열.
- n=3의 데이터는 seed별 pair outcome (1,1)/(1,0)/(0,1)/(0,0)의 3개 다중집합 — 서로 다른 pattern은 20종뿐. 각 pattern의 conditional power(해당 관측 데이터에서 CI가 0을 배제하는지)는 exact로 결정되며, 대표 셀 (y=20%, med) 관측 빈도와 함께:

| pattern (a,b,c,d) | 관측 빈도 (n={meta['mc_per_cell']:,}) | CP (CI 0 배제) |
|---|---|---|
{pat_rows}

- cross-check: exact permutation test(n=3)은 min p = 0.25(3/3 일치 패턴)라 α=0.05 기각 불가(§5) — CP는 CI 기준에서만 해석 가능.

### 6.3 (c) seed=3 충분성 (variance / CI 폭)

| yield | 농도 | P(T) 참값 (n=6 run) | CP (n=6) | Δ CI 폭 mean | Δ CI 폭 P95 | p̂_T CI 폭 mean |
|---|---|---|---|---|---|---|
{row6("n6")}

(n=6 행은 6 paired seed 변이 체; CP/폭은 n=6 기준.)

- 그리드 전체 평균 및 판정:
{vd["text"]}

## 7. 한계 (integrity)

1. **DGP placeholder**: 초기값·개선 상수·standard hit rate·노이즈는 전부 가정값(중간 그리드 보정). 절대 qualification 확률은 실제 M7-9의 예측이 아니며, 전가 가능한 결론은 estimator-level 결과(CI 폭, CP 구조, n=3의 이산성)뿐이다.
2. **grids ≠ 실측**: §4 — 10/20/30% 그리드는 미측정 유닛의 사전 범위이고, vulnerable-function+Semgrep 단일오라클 실측 0%(0/30)보다 최저 시나리오조차 낙관적이다.
3. **n=3의 이산성**: p̂ ∈ {{0, 1/3, 2/3, 1}}, Δ ∈ {{-1,...,1}}/3 — CI가 연속 분포를 전제하는 정규 근사 없이도 실질 폭이 큼. CI coverage(§6.1 주석의 참값 대비)는 p가 경계(≈0, ≈1) 셀에서 명목 95%와 크게 벌어질 수 있음 — 이것은 설계의 한계이지 추정량 오류가 아님.
4. **메커니즘 단순화**: precision/recall이 라운드당 기대 단조 증가(overfit으로 precision이 일부 라운드 하락하는 trade-off 메커니즘 없음) → qualification 확률은 실제보다 약간 낙관적일 수 있음.
5. **비용 차원 부재**: 실제 primary contrast는 fixed-recall에서의 cost/defect contrast이며, 본 시뮬레이션의 endpoint(qualification)은 task body가 지정한 그 대체 지표.
6. **클러스터 가중치가 결정적**: run마다 w를 재추출하지 않고 셀당 고정 프로필 3종 사용 (실 6-CWE 농도 미지 → 3 프로필로 범위 커버).
7. 내적 bootstrap은 정확 열거(27/216)로 내부 MC 오차 0; 외부 MC {meta['mc_per_cell']:,}/셀은 요구된 10,000+를 만족(총 run 수 {meta['mc_per_cell']*9*2:,}) — 실행 시간 {meta['wall_s']}s라 반복 수 축소 불필요.

## 8. 다음 단계 (범위 외 — 이번 작업에서 실행 안 함)

- 이 결과를 **2크루나에게 보고** → PI 가드레일 재확인 → Preliminary Evidence 단락 반영 여부는 PI 결정.
- **제안서(ara-solo-proposal.tex)는 미수정** 유지.
"""
    with open("/home/ubuntu/grants/ara-solo/design_sim/REPORT.md", "w") as f:
        f.write(md)

def seed3_verdict(r):
    c = r["cells"]
    def grid(tag, key):
        return np.mean([c[f"y{int(y*100):02d}_c_{cn}"][tag]["delta_ci"][key]
                        for y in YIELDS for cn in ("low", "med", "high")])
    w3, w6 = grid("n3", "width_mean"), grid("n6", "width_mean")
    cp3, cp6 = grid("n3", "excludes0_positive"), grid("n6", "excludes0_positive")
    w3p = np.mean([c[f"y{int(y*100):02d}_c_{cn}"]["n3"]["p_ci_width"]["targeted"][0]
                   for y in YIELDS for cn in ("low", "med", "high")])
    w6p = np.mean([c[f"y{int(y*100):02d}_c_{cn}"]["n6"]["p_ci_width"]["targeted"][0]
                   for y in YIELDS for cn in ("low", "med", "high")])
    # blind cells: true diff >= 2pp but CP3 < 5%
    bnd = []
    for y in YIELDS:
        for cn in ("low", "med", "high"):
            k = f"y{int(y*100):02d}_c_{cn}"
            t3, t6 = c[k]["n3"], c[k]["n6"]
            dT = t3["true_p"]["targeted"][0] - t3["true_p"]["standard"][0]
            if dT >= 0.02 and t3["delta_ci"]["excludes0_positive"] < 0.05:
                bnd.append((f"yield={int(y*100)}%/{cn}", dT,
                            t3["delta_ci"]["excludes0_positive"], t6["delta_ci"]["excludes0_positive"]))
    bnd.sort(key=lambda x: -x[1])
    bnd_txt = ("; ".join(f"{k} (true diff {d:.1%}, CP n=3 {p3:.1%} / n=6 {p6:.1%})"
                         for k, d, p3, p6 in bnd[:3]) or "관측되지 않음")
    cov3 = c["y20_c_med"]["n3"]["p_ci_coverage"]["targeted"]
    cov6 = c["y20_c_med"]["n6"]["p_ci_coverage"]["targeted"]
    lines = [
        f"- **coverage (대표 셀 y=20%/med, targeted 참값 {c['y20_c_med']['n3']['true_p']['targeted'][0]:.3f} vs 명목 95%)**: n=3 실제 coverage **{cov3:.1%}** (놓침 {1-cov3:.0%}), n=6 **{cov6:.1%}** — n=3의 paired 95% CI는 명목 95% 대비 심한 under-coverage; n=6도 95%에 미달. 경계 셀(p≈0 또는 p≈1)에서는 폭이 0으로 퇴화(false precision)하는 셀도 있다(§6.1).",
        f"- per-arm p̂ 95% CI 폭 그리드 평균: n=3 {w3p:.2f} → n=6 {w6p:.2f}; Δ 95% CI 폭 그리드 평균: n=3 {w3:.2f} → n=6 {w6:.2f}. 즉 seed 3→6로 CI 폭은 개선되지 않는다 — 퍼센타일 기준이 27/216종 재표본의 하위 꼬리에 붙어, 중간 확률 셀일수록 n=6이 오히려 넓어지는 현상(exact 열거로 검증).",
        f"- CP 평균: n=3 {cp3:.3f} → n=6 {cp6:.3f} — 단조가 아님. CI-0배제 이벤트는 관측 pattern에 이산적으로 귀약된다: 대표 셀(y=20%, med, standard≈0)에서 n=3 '3개 중 2개 이상 qualify' (패턴 (0,2,0,1)/(0,3,0,0)) ⟺ n=6 '6개 중 5개 이상 qualify' (패턴 (0,5,0,1)/(0,6,0,0)) — **n=6이 더 엄격한 기준**. 또한 n=3 permutation test는 min p = {r['permutation_min_p_n3']:.2f}(3/3 일치 패턴에서도)라 α=0.05 기각 자체가 불가능(§5).",
        f"- **맹점(blind cell)**: true difference가 존재함에도 CP < 5%인 셀 — {bnd_txt}. 즉 1–10pp 수준의 정책 차이는 n=3(그리고 n=6)으로 사실상 검출 불가; 검출 가능 영역은 약 ≳50pp의 큰 차이뿐.",
        f"- **판정**: seed=3은 policy 차이의 추정에 불충분하다 — (i) 큰 효과는 '3개 중 2개' 수준의 coarse 판단에 귀약되고, (ii) 1–10pp 효과는 검출 불가, (iii) CI coverage는 명목 95% 대비 심각하게 낮다(위). seed 3→6도 이 endpoint의 이산성(binary)이 근본 제약이므로 폭·CP·coverage를 유의하게 개선하지 못한다. 권장: M6 inconclusive 규칙(paired 95% interval 0 포함 → inconclusive, 효과 부정이 아님)을 그대로 적용하고 seed count·관측 pattern을 결과에 포함해 보고; decision statistic은 binary qualification이 아니라 연속형 cost/defect contrast(실제 primary)로 잡을 것.",
    ]
    return {
        "w3": w3, "w6": w6, "cp3": cp3, "cp6": cp6, "w3p": w3p, "w6p": w6p,
        "text": "\n".join(lines),
    }

if __name__ == "__main__":
    main()
