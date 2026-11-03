# ARA solo — M3 전 estimator-level design simulation (REPORT)

- Task: t_bea2653e · 실행: 2026-11-02 (EST) · 머신: local (CPU-only, GPU/LLM 호출 없음)
- 재현: `cd /home/ubuntu/grants/ara-solo/design_sim && python3 sim.py [seed]` (기본 seed `20261102`, numpy 2.4.3, 소요 4.5s)
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
- 라운드 과정: m_t = min(1, m_0 + Σ G·y·h_q·ξ_t), m ∈ {recall, precision}. G_recall=0.30, G_precision=0.27, ξ_t ~ N(1, 0.2)를 [0.2, 2.0] 클리핑. **ξ는 2 policy가 공유** (pairing — 동일 seed/동일 라운드의 환경적 변동).
- hit rate h: targeted = top-2 CWE 클러스터 가중치 합 (CWE 클러스터별 witness 농도 w에 스케줄링 집중), standard = 고정 0.25 (CWE 타겟팅 없음, 데이터 믹스에 의한 중간 농도).
- 변동 요인 2종 (명시적 모수화): ① witness-yield y ∈ {10%, 20%, 30%} (후 §4), ② 6 CWE 클러스터 간 witness 농도 이질성 — 결정적 3 프로필 (파일럿과 동일 CWE 6종: 79/89/918/78/22/502):
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
- cross-check: exact paired permutation test(2³ sign-flip)의 n=3 실현 가능 최소 p-value = **0.25 > 0.05** (최소값은 3/3 일치라는 가장 강한 데이터 패턴에서도) — 즉 n=3에서는 permutation test가 α=0.05에서 원리적으로 기각 불가. 따라서 CI 기반 판정(위)이 실제 운용 기준이며, 이것이 estimator-level 검증 대상.

## 6. 결과

설계: 셀당 MC 15,000 반복 (총 270,000 run), 4.5s. CI 폭은 확률 단위 (0.40 = 40%p).

### 6.1 (a) policy별 primary qualification 확률 — DGP 참값 + n=3 추정 불안정성

| yield | 농도 | P(qualify) targeted (SE) | P(qualify) standard (SE) | Δ CI 0배제(+) | Δ CI 0포함 | Δ CI 폭 mean | Δ CI 폭 P95 | Δ 폭≥0.5 run비율 |
|---|---|---|---|---|---|---|---|---|
| 10% | low | 0.000 ± 0.000 | 0.000 ± 0.000 | 0.000 | 1.000 | 0.00 | 0.00 | 0.00 |
| 10% | med | 0.000 ± 0.000 | 0.000 ± 0.000 | 0.000 | 1.000 | 0.00 | 0.00 | 0.00 |
| 10% | high | 0.006 ± 0.000 | 0.000 ± 0.000 | 0.000 | 1.000 | 0.01 | 0.00 | 0.02 |
| 20% | low | 0.031 ± 0.001 | 0.000 ± 0.000 | 0.004 | 0.997 | 0.07 | 0.78 | 0.09 |
| 20% | med | 0.784 ± 0.002 | 0.000 ± 0.000 | 0.880 | 0.120 | 0.40 | 0.78 | 0.51 |
| 20% | high | 1.000 ± 0.000 | 0.000 ± 0.000 | 1.000 | 0.000 | 0.00 | 0.00 | 0.00 |
| 30% | low | 0.944 ± 0.001 | 0.023 ± 0.001 | 0.984 | 0.016 | 0.17 | 0.78 | 0.22 |
| 30% | med | 1.000 ± 0.000 | 0.022 ± 0.001 | 0.999 | 0.001 | 0.05 | 0.78 | 0.07 |
| 30% | high | 1.000 ± 0.000 | 0.023 ± 0.001 | 0.999 | 0.002 | 0.05 | 0.78 | 0.07 |

(참값 SE = MC 표준오차(≤0.002). Δ CI 폭·비율은 **n=3 seed로 실제로 측정할 추정량**의 셀별 분포. coverage는 §6.3 참고.)

### 6.2 (b) conditional power — targeted vs standard-SFT 차이

- 무조건적 CP (95% paired bootstrap CI가 Δ=0을 양 방향으로 배제할 run 비율, = M6 규칙에서 "targeted 우위"로 판정되는 비율): 표 6.1의 "Δ CI 0배제(+)" 열.
- n=3의 데이터는 seed별 pair outcome (1,1)/(1,0)/(0,1)/(0,0)의 3개 다중집합 — 서로 다른 pattern은 20종뿐. 각 pattern의 conditional power(해당 관측 데이터에서 CI가 0을 배제하는지)는 exact로 결정되며, 대표 셀 (y=20%, med) 관측 빈도와 함께:

| pattern (a,b,c,d) | 관측 빈도 (n=15,000) | CP (CI 0 배제) |
|---|---|---|
| (0,3,0,0) | 7223 | 있음 (0 배제) |
| (0,2,0,1) | 5980 | 있음 (0 배제) |
| (0,1,0,2) | 1630 | 없음 (0 포함 → inconclusive) |
| (0,0,0,3) | 167 | 없음 (0 포함 → inconclusive) |

- cross-check: exact permutation test(n=3)은 min p = 0.25(3/3 일치 패턴)라 α=0.05 기각 불가(§5) — CP는 CI 기준에서만 해석 가능.

### 6.3 (c) seed=3 충분성 (variance / CI 폭)

| yield | 농도 | P(T) 참값 (n=6 run) | CP (n=6) | Δ CI 폭 mean | Δ CI 폭 P95 | p̂_T CI 폭 mean |
|---|---|---|---|---|---|---|
| 10% | low | 0.000 | 0.000 | 0.00 | 0.00 | 0.00 |
| 10% | med | 0.000 | 0.000 | 0.00 | 0.00 | 0.00 |
| 10% | high | 0.006 | 0.000 | 0.02 | 0.00 | 0.02 |
| 20% | low | 0.031 | 0.000 | 0.12 | 0.67 | 0.12 |
| 20% | med | 0.785 | 0.618 | 0.64 | 1.00 | 0.64 |
| 20% | high | 1.000 | 1.000 | 0.00 | 0.00 | 0.00 |
| 30% | low | 0.942 | 0.921 | 0.29 | 1.00 | 0.22 |
| 30% | med | 1.000 | 0.992 | 0.09 | 0.67 | 0.00 |
| 30% | high | 1.000 | 0.992 | 0.09 | 0.67 | 0.00 |

(n=6 행은 6 paired seed 변이 체; CP/폭은 n=6 기준.)

- 그리드 전체 평균 및 판정:
- **coverage (대표 셀 y=20%/med, targeted 참값 0.784 vs 명목 95%)**: n=3 실제 coverage **39.9%** (놓침 60%), n=6 **76.5%** — n=3의 paired 95% CI는 명목 95% 대비 심한 under-coverage; n=6도 95%에 미달. 경계 셀(p≈0 또는 p≈1)에서는 폭이 0으로 퇴화(false precision)하는 셀도 있다(§6.1).
- per-arm p̂ 95% CI 폭 그리드 평균: n=3 0.07 → n=6 0.11; Δ 95% CI 폭 그리드 평균: n=3 0.08 → n=6 0.14. 즉 seed 3→6로 CI 폭은 개선되지 않는다 — 퍼센타일 기준이 27/216종 재표본의 하위 꼬리에 붙어, 중간 확률 셀일수록 n=6이 오히려 넓어지는 현상(exact 열거로 검증).
- CP 평균: n=3 0.541 → n=6 0.503 — 단조가 아님. CI-0배제 이벤트는 관측 pattern에 이산적으로 귀약된다: 대표 셀(y=20%, med, standard≈0)에서 n=3 '3개 중 2개 이상 qualify' (패턴 (0,2,0,1)/(0,3,0,0)) ⟺ n=6 '6개 중 5개 이상 qualify' (패턴 (0,5,0,1)/(0,6,0,0)) — **n=6이 더 엄격한 기준**. 또한 n=3 permutation test는 min p = 0.25(3/3 일치 패턴에서도)라 α=0.05 기각 자체가 불가능(§5).
- **맹점(blind cell)**: true difference가 존재함에도 CP < 5%인 셀 — yield=20%/low (true diff 3.1%, CP n=3 0.4% / n=6 0.0%). 즉 1–10pp 수준의 정책 차이는 n=3(그리고 n=6)으로 사실상 검출 불가; 검출 가능 영역은 약 ≳50pp의 큰 차이뿐.
- **판정**: seed=3은 policy 차이의 추정에 불충분하다 — (i) 큰 효과는 '3개 중 2개' 수준의 coarse 판단에 귀약되고, (ii) 1–10pp 효과는 검출 불가, (iii) CI coverage는 명목 95% 대비 심각하게 낮다(위). seed 3→6도 이 endpoint의 이산성(binary)이 근본 제약이므로 폭·CP·coverage를 유의하게 개선하지 못한다. 권장: M6 inconclusive 규칙(paired 95% interval 0 포함 → inconclusive, 효과 부정이 아님)을 그대로 적용하고 seed count·관측 pattern을 결과에 포함해 보고; decision statistic은 binary qualification이 아니라 연속형 cost/defect contrast(실제 primary)로 잡을 것.

## 7. 한계 (integrity)

1. **DGP placeholder**: 초기값·개선 상수·standard hit rate·노이즈는 전부 가정값(중간 그리드 보정). 절대 qualification 확률은 실제 M7-9의 예측이 아니며, 전가 가능한 결론은 estimator-level 결과(CI 폭, CP 구조, n=3의 이산성)뿐이다.
2. **grids ≠ 실측**: §4 — 10/20/30% 그리드는 미측정 유닛의 사전 범위이고, vulnerable-function+Semgrep 단일오라클 실측 0%(0/30)보다 최저 시나리오조차 낙관적이다.
3. **n=3의 이산성**: p̂ ∈ {0, 1/3, 2/3, 1}, Δ ∈ {-1,...,1}/3 — CI가 연속 분포를 전제하는 정규 근사 없이도 실질 폭이 큼. CI coverage(§6.1 주석의 참값 대비)는 p가 경계(≈0, ≈1) 셀에서 명목 95%와 크게 벌어질 수 있음 — 이것은 설계의 한계이지 추정량 오류가 아님.
4. **메커니즘 단순화**: precision/recall이 라운드당 기대 단조 증가(overfit으로 precision이 일부 라운드 하락하는 trade-off 메커니즘 없음) → qualification 확률은 실제보다 약간 낙관적일 수 있음.
5. **비용 차원 부재**: 실제 primary contrast는 fixed-recall에서의 cost/defect contrast이며, 본 시뮬레이션의 endpoint(qualification)은 task body가 지정한 그 대체 지표.
6. **클러스터 가중치가 결정적**: run마다 w를 재추출하지 않고 셀당 고정 프로필 3종 사용 (실 6-CWE 농도 미지 → 3 프로필로 범위 커버).
7. 내적 bootstrap은 정확 열거(27/216)로 내부 MC 오차 0; 외부 MC 15,000/셀은 요구된 10,000+를 만족(총 run 수 270,000) — 실행 시간 4.5s라 반복 수 축소 불필요.

## 8. 다음 단계 (범위 외 — 이번 작업에서 실행 안 함)

- 이 결과를 **2크루나에게 보고** → PI 가드레일 재확인 → Preliminary Evidence 단락 반영 여부는 PI 결정.
- **제안서(ara-solo-proposal.tex)는 미수정** 유지.
