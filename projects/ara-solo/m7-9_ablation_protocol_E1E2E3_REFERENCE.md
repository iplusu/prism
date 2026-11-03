> **Status: post-award reference only — NOT executed pre-submission, proposal NOT edited.** Costs/timing are explicitly resource-conditional on M1 measurement per the protocol itself; sample sizes/hyperparameters (γ, optimizer, matching tolerance) stay frozen at M3 per the current `ara-solo-proposal.tex` design on purpose (adding this level of detail now would contradict the proposal's existing "fixed at M1/M3" language and isn't needed for the 4-page submission).
>
> Source: external Codex+Claude Code multi-agent protocol discussion (Antigravity dropped round 1, quota exhausted). Saved verbatim 2026-11-03 by 2크루나 per PI decision "A" (reference only). Use when M1 manifest is frozen post-award.

## SUMMARY
The discussion concluded that the manuscript requires three additional offline protocols to strengthen the proposed controlled evaluation without human subjects: a factorial ablation of ordering and focal weighting (E1), a decision-policy diagnostic via re-scoring (E2), and a deferred acquisition efficiency study (E3). The participants agreed on a shared execution framework requiring a frozen M3 manifest, specific seed streams, and estimation-only reporting for secondary claims to preserve the integrity of the primary efficacy decision. Disagreements regarding multiplicity families, the definition of component effects, and the feasibility of the acquisition study were resolved by adopting arithmetic marginal contrasts, a single pre-declared secondary family for specific claims, and deferring the acquisition study due to resource constraints.

## EXPERIMENT PROTOCOLS

### E1: Factorial Ablation: Ordering × Focal Weighting

**ID and name:** E1: Factorial Ablation: Ordering × Focal Weighting

**Claim tested and directional hypothesis:**
*   **Claim:** The bundle effect attributed to the primary policy can be decomposed into the contributions of FP/FN-first ordering and focal weighting.
*   **Hypothesis (PROPOSED, directional):**
    1.  $E_O > 0$: FP/FN-first ordering reduces first-qualifying oracle cost per detected defect relative to random ordering, averaged over focal-weighting conditions.
    2.  $E_F > 0$: Focal weighting reduces that endpoint relative to uniform weighting, averaged over ordering conditions.
    3.  Interaction $I \leq 0$: The bundle gains less than the sum of the parts (synergy or additivity, not super-additivity).

**Conditions/arms:**

| Arm ID | Example Order | Loss Weight | Operational Decision Policy |
| :--- | :--- | :--- | :--- |
| A0 (Q00) | Seeded random | Uniform | Refit on calibration only |
| A1 (Q10) | Resolved FP/FN first; seeded random within groups | Uniform | Refit on calibration only |
| A2 (Q01) | Seeded random | $(1-p_{\mathrm{true}})^\gamma$ | Refit on calibration only |
| A3 (Q11) | Resolved FP/FN first; seeded random within groups | $(1-p_{\mathrm{true}})^\gamma$ | Refit on calibration only |

*   **Note:** A0 and A3 are the existing primary arms and are not re-run. A1 and A2 are the new arms. All arms share the initial adapter, acquired IDs, optimizer, update budget, calibration set, and fitting procedure.

**Units, sample size and the reason it suffices:**
*   **Units:** Confirmatory-audit clusters.
*   **Sample Size:** Target 50 clusters overall, with approximately 35 clusters allocated to executable-eligible CWEs before attrition. Three paired seeds.
*   **Adequacy:** This sample supports paired estimation. It does not establish power for component hypothesis testing. The manuscript's approximate MDE (approx. 0.237 at 35 clusters) concerns the original comparison, not these additional contrasts. Adequacy is determined by the E5 design simulation gate (see below).

**Randomisation, pairing, seeds:**
*   **Root Seeds (PROPOSED):** 1729, 2718, 31415.
*   **Bootstrap Seed (PROPOSED):** 161803.
*   **Streams:** Derive separate named RNG streams for acquisition, training, within-group ordering, and simulation.
*   **Pairing:** Pair arms on acquisition, initial adapter, available labels, and applicable random streams. Couple corresponding RNG streams where their operations agree. Do not claim identical minibatches after changing example order.

**Procedure:**
1.  Freeze the M3 manifest, resource reservation, and estimation-only status.
2.  Retain the existing A0 and A3 trajectories; initialize A1 and A2 from the same seed-specific adapter.
3.  For each scheduled round ($t=1..10$):
    *   Predict on the shared feedback batch before revealing labels.
    *   Log raw logits.
    *   Reveal feedback labels.
    *   Define each arm's FP/FN groups using its own pre-update predictions and resolved feedback labels.
    *   Train using the assigned order and loss, with matched steps and replay.
    *   Refit the operational policy on calibration only.
    *   Score the checkpoint.
4.  Keep confirmatory outcomes sealed until the common stopping cap.
5.  Apply first qualification ($R \geq 0.80$ and $P \geq 0.80$) and the fallback exactly as in the manuscript.

**Inputs (data, models, parameters):**
*   **Data:** Python corpus covering CWE-79, 89, 918, 78, 94, 22, 798, 352, 502, 863. Cluster-disjoint allocation: 1,500 training, 1,500 feedback, 500 calibration, 500 pilot-audit, 500 confirmatory-audit, 500 final-holdout candidates.
*   **Model:** Qwen3-Coder-30B-A3B-Instruct with LoRA/QLoRA.
*   **Parameters:**
    *   $\gamma$: TO BE DECIDED BY THE AUTHORS (frozen at M3).
    *   Optimizer, learning rate, batch size, gradient steps, replay: TO BE DECIDED BY THE AUTHORS (frozen at M3).
    *   Focal weight normalization: TO BE DECIDED BY THE AUTHORS (frozen at M3).
    *   Matching tolerance: TO BE DECIDED BY THE AUTHORS (frozen at M3).

**Metrics with exact definitions or formulas:**
*   Let $D$ be distinct correctly matched Tier-1 defects, $V$ all Tier-1 defects, and $A$ all deduplicated alerts in the primary evaluable stratum.
*   $R = D/V$, $P = D/A$, $F_1 = 2PR/(P+R)$.
*   $Q_{of}$: Arithmetic mean across the three seeds of the qualifying $C/D$ endpoint, where $o \in \{0,1\}$ indicates ordering and $f \in \{0,1\}$ indicates focal weighting.
*   $E_O = 1 - \frac{Q_{10}+Q_{11}}{Q_{00}+Q_{01}}$
*   $E_F = 1 - \frac{Q_{01}+Q_{11}}{Q_{00}+Q_{10}}$
*   $I = \log Q_{11} - \log Q_{10} - \log Q_{01} + \log Q_{00}$

**Statistical analysis:**
*   **Method:** 10,000 paired cluster-bootstrap replicates, retaining all seeds and recomputing first qualification within each replicate.
*   **Multiplicity:** $E_O$ and $E_F$ are part of a pre-declared secondary family of $m=4$ (including SAST-blind recall and repair-discrimination gains). Use Bonferroni-adjusted 98.75% two-sided percentile intervals.
*   **Effect Size:** $E_O$ and $E_F$ are equal-weight marginal cost-benefit contrasts. $I$ is descriptive.

**Decision rule fixed in advance:**
*   **Gate:** If the E5 design simulation shows at least 80% unconditional probability of a supported conclusion at benefit 0.20, inferential claims are enabled. Otherwise, E1 is estimation-only.
*   **Supported:** A component "contributes" if its adjusted interval lies above zero, the original sample qualifies, and at least 95% of relevant bootstrap replicates qualify.
*   **Useful:** A claim of a "useful" 0.20 improvement additionally requires the lower bound to exceed 0.20.
*   **Failure:** Do not interpret a failed significance criterion as equivalence.

**Table/figure produced and placement:**
*   **Output:** Table "Factorial ablation" with rows A0–A3 giving $C/D$, attained recall and precision, $E_O, E_F$, interaction, and adjusted intervals.
*   **Placement:** In the Evaluation section after the primary trajectory figure (manuscript line 95). Design-simulation grid in an appendix.

**Compute, time, cost:**
*   **Compute:** 60 adapter updates (2 new arms × 3 seeds × 10 rounds) and 66 arm/seed checkpoint evaluations.
*   **Cost Formula:** $G_{\mathrm{extra}} = 60g_u + 66g_s + G_{\mathrm{uncached\ policy\ scoring}}$, where $g_u$ and $g_s$ are M1-measured hours per update and checkpoint score.
*   **Time:** Calendar duration is measured resource demand divided by reserved throughput, plus serial dependencies.
*   **Note:** Numeric dollars and completion time remain unavailable until M1 measurement. This experiment is explicitly resource-conditional.

**Threats to validity and the control for each:**
*   **Unequal optimization:** Control: Identical steps, replay, optimizer, and resolved-label availability.
*   **$\gamma$ tuned on one arm:** Control: $\gamma$ is frozen at M3 for all arms.
*   **Audit leakage into ordering:** Control: Only pre-update predictions and revealed feedback labels define the groups.
*   **Seeds mistaken for tasks:** Control: Cluster bootstrap, with per-seed values reported.
*   **Qualification bias:** Control: Report failure frequencies.

---

### E2: Weights versus Decision-Policy Adaptation (Re-scoring)

**ID and name:** E2: Weights versus Decision-Policy Adaptation (Re-scoring)

**Claim tested and directional hypothesis:**
*   **Claim:** The gain comes from weight adaptation, not from recalibration.
*   **Hypothesis (PROPOSED):** Retraining-only recovers most of the full gain. Threshold-only is close to frozen.

**Conditions/arms:**

| Arm ID | Weights | Decision Rule / Calibrator |
| :--- | :--- | :--- |
| F (Frozen) | t=0 | t=0 |
| T1 (Threshold-only, literal) | t=0 | Refit each round on calibration only |
| R (Retraining-only) | Round t (A3) | t=0, frozen |
| Full (= A3) | Round t | Refit each round |

*   **Note:** All arms are derived from stored raw logits; no new training is performed. T1 is a sanity check (should equal F). T2 (refit on calibration + feedback) is excluded.

**Units, sample size and the reason it suffices:**
*   **Units:** Confirmatory-audit clusters.
*   **Sample Size:** 50 clusters, 3 seeds.
*   **Adequacy:** Same as E1; supports estimation.

**Randomisation, pairing, seeds:**
*   Same as E1. No new randomization required as this is a re-scoring of existing trajectories.

**Procedure:**
1.  Save pre-calibration raw logits for F and A3 at all 11 checkpoints ($t=0..10$) for each seed.
2.  For R, apply the t=0 calibrator, thresholds, and $\tau$ to the updated-weight logits.
3.  For T1, refit on the unchanged calibration set using the same fitting code.
4.  Score the pilot audit and then the confirmatory audit.
5.  Charge feedback label cost identically in all arms.

**Inputs (data, models, parameters):**
*   **Data:** Stored raw logits from A3 and F trajectories.
*   **Model:** Qwen3-Coder-30B-A3B-Instruct.
*   **Parameters:** t=0 calibrator, thresholds, and $\tau$.

**Metrics with exact definitions or formulas:**
*   **Primary:** Paired differences in final-checkpoint recall, precision, and F1: $Full - R$, $R - F$, and $Full - F$.
*   **Secondary:** $C/D$ at first qualification where defined.
*   **Check:** $T1 - F$ must be exactly 0. If not, the implementation is wrong.

**Statistical analysis:**
*   **Method:** 10,000 paired cluster-bootstrap replicates.
*   **Multiplicity:** Descriptive only. 95% percentile intervals. No p-values.
*   **Effect Size:** Paired differences.

**Decision rule fixed in advance:**
*   **Descriptive only.** A large $R - F$ with a small $Full - R$ is read as weight-driven. The reverse is read as policy-driven. Neither is a confirmatory claim.

**Table/figure produced and placement:**
*   **Output:** Extra lines F, R, and Full in the self-improvement curve (manuscript line 95), and an appendix table.
*   **Placement:** Evaluation section.

**Compute, time, cost:**
*   **Compute:** About 99 checkpoint scorings (3 seeds × 3 arms × 11 points). No training, no new labels.
*   **Cost:** $99 \times g_s$.
*   **Time:** Small CPU/GPU inference cost.

**Threats to validity and the control for each:**
*   **Missing raw logits:** Control: Saving them is a requirement at M3.
*   **Frozen policy mismatch:** Control: Report the shift in score distribution.
*   **T1 ≠ F:** Control: Signals a bug, not a finding; E2 is void if this occurs.

---

### E3: Acquisition Efficiency (Deferred)

**ID and name:** E3: Acquisition Efficiency (Deferred, Separate Study)

**Claim tested and directional hypothesis:**
*   **Claim:** Score-based selection of which IDs to label lowers $C/D$ versus random acquisition, under the same update policy.
*   **Hypothesis (PROPOSED):** $B_{acq} = 1 - Q_{Q1}/Q_{Q0} > 0$.

**Conditions/arms:**

| Arm ID | Selection Rule | Update Policy |
| :--- | :--- | :--- |
| Q0 (Random) | Seeded random draw per CWE from remaining pool | Bundle (A3) |
| Q1 (Lowest Confidence) | 15 IDs per CWE with lowest pre-round confidence | Bundle (A3) |

*   **Note:** Q0 must be re-run on the new pool P. The primary A3 used different feedback IDs, so its $C/D$ is not comparable.

**Units, sample size and the reason it suffices:**
*   **Units:** Confirmatory-audit clusters.
*   **Sample Size:** Same audit clusters as E1. 3 seeds.
*   **Adequacy:** Exploratory.

**Randomisation, pairing, seeds:**
*   Same root seeds. Acquisition is deterministic given the model; seed variance comes mainly from training.

**Procedure:**
1.  Before each round, score the remaining pool with the current verifier.
2.  Select IDs by the arm's rule.
3.  Run the oracle battery on the selected IDs. Charge every attempt: synthesis, scans, fuzzing, failures, and timeouts.
4.  Unresolved attempts consume their slots. No replacement.
5.  Update, refit the calibration, and score the audit as in the primary loop.

**Inputs (data, models, parameters):**
*   **Data:** Pool P: PROPOSED 3,000 generated candidates (300 per CWE), cluster-disjoint from all six existing splits.
*   **Model:** Qwen3-Coder-30B-A3B-Instruct.
*   **Parameters:** Selection rule (lowest confidence) frozen at M3.

**Metrics with exact definitions or formulas:**
*   **Primary:** $B_{acq} = 1 - Q_{Q1}/Q_{Q0}$.
*   **Diagnostics:** Tier-1 yield per labeled ID, positive fraction of acquired labels, and mean label cost per ID.

**Statistical analysis:**
*   **Method:** Paired cluster bootstrap, 95% interval.
*   **Multiplicity:** Single contrast, not in the $m=4$ family.

**Decision rule fixed in advance:**
*   **Supported:** If the lower bound of $B_{acq}$ is above $\delta = 0.20$.
*   **Excluded:** If the upper bound is below $\delta$.
*   **Otherwise:** Unresolved.
*   **Note:** $\delta$ is borrowed for comparability, so this is exploratory.

**Table/figure produced and placement:**
*   **Output:** A table placed after the primary result, with a one-line mention in Impact (manuscript line 102).

**Compute, time, cost:**
*   **Compute:** Up to 9,000 arm/seed labeling slots (2 arms × 3 seeds × 1,500 IDs). 60 adapter updates.
*   **Cost:** Largest of the set. Witness synthesis dominates.
*   **Status:** Deferred. Run only if M1 funnel shows spare capacity.

**Threats to validity and the control for each:**
*   **Cost variability:** Control: Charge actual cost, and report cost per ID.
*   **Selection bias:** Control: Audit stays randomly drawn.

---

### Summary Table of All Experiments

| ID | Question | Arms | N (Clusters/Seeds) | Cost | Order and Dependency |
| :--- | :--- | :--- | :--- | :--- | :--- |
| E1 | Component attribution (Ordering vs Focal) | A0, A1, A2, A3 | 50 / 3 | $60g_u + 66g_s$ | Primary. Requires M3 manifest. |
| E2 | Weights vs Policy Adaptation | F, T1, R, Full | 50 / 3 | $99g_s$ | Secondary. Depends on E1 logits. |
| E3 | Acquisition Efficiency | Q0, Q1 | 50 / 3 | High (Labeling) | Deferred. Requires new pool P. |

## CONSENSUS
*   **M3 Manifest Prerequisite:** Both [Codex] and [Claude Code] agreed that a versioned M3 manifest containing exact corpus IDs, model revisions, optimizer settings, and hardware costs is a prerequisite for execution, as the manuscript defers these to M1/M3 (`ara-solo-proposal.tex:70–76, 93, 119`).
*   **Seed Streams:** Both agreed on using root seeds 1729, 2718, and 31415, and bootstrap seed 161803, with derived streams for acquisition, training, and simulation [Codex, Claude Code].
*   **Estimation-Only Status:** Both agreed that the additional experiments should be reported as estimation-only unless a specific design simulation gate is passed, to avoid overclaiming secondary efficacy [Codex, Claude Code].
*   **Exclusion of T2:** Both agreed to exclude the "threshold-only with feedback labels" arm (T2) because it violates the calibration-only rule (`ara-solo-proposal.tex:72`) [Codex, Claude Code].
*   **Single Holdout Opening:** Both agreed that any use of the final holdout must be pre-registered and executed in the single M10 opening (`ara-solo-proposal.tex:85`) [Codex, Claude Code].
*   **Resource-Conditional Pricing:** Both agreed that costs must be expressed as multiples of M1-measured unit costs, with no invented GPU-hours (`ara-solo-proposal.tex:119`) [Codex, Claude Code].

## DISAGREEMENTS
*   **Multiplicity Family Definition:**
    *   [Codex] initially rejected separate multiplicity families for every experiment, advocating for estimation-only reporting or a single family.
    *   [Claude Code] initially proposed separate families for each experiment with Holm adjustment.
    *   **Resolution:** [moderator's recommendation] Adopt [Claude Code]'s final position: One pre-declared secondary family of $m=4$ (including $E_O, E_F$, SAST-blind recall, and repair-discrimination) with Bonferroni-adjusted 98.75% intervals. E2 and E3 are descriptive. [Codex] argued for estimation-only, but [Claude Code] argued that a specific gate (E5) could enable inferential claims. The gate approach is adopted.
*   **Component Effect Form:**
    *   [Codex] proposed arithmetic marginal fractional benefits ($E_O, E_F$ as defined in the protocol).
    *   [Claude Code] initially proposed log-scale main effects.
    *   **Resolution:** [moderator's recommendation] Adopt [Codex]'s arithmetic marginal contrasts ($E_O = 1 - \frac{Q_{10}+Q_{11}}{Q_{00}+Q_{01}}$), as they align with the manuscript's ratio-of-arm-means estimator and are easier to interpret as cost savings. [Claude Code] conceded this point in Round 3.
*   **Acquisition Study Feasibility:**
    *   [Claude Code] initially proposed running the acquisition study (E3) with a 5-round fallback.
    *   [Codex] rejected the 5-round fallback and the necessity of a larger pool for ordering tests, but agreed a larger pool is needed for subset selection.
    *   **Resolution:** [moderator's recommendation] Adopt [Codex]'s position: E3 is deferred. The 5-round fallback is rejected because it changes operating characteristics. The study requires a new pool and significant resources, so it is a separate, deferred study.

## DROPPED
*   **T2 Arm (Threshold-only with feedback labels):** Raised by [Claude Code] in Round 1 as a sensitivity arm. Rebutted by [Codex] in Round 2 as violating the calibration-only rule (`ara-solo-proposal.tex:72`). [Claude Code] conceded and dropped it in Round 3.
*   **Holm Adjustment:** Raised by [Claude Code] in Round 1. Rebutted by [Codex] in Round 2 as not following from the manuscript. [Claude Code] conceded and switched to Bonferroni in Round 3.
*   **Share-of-Gain Ratio:** Raised by [Claude Code] in Round 1 for E2. Rebutted by [Codex] in Round 2 as not establishing that at least half the gain comes from weights. [Claude Code] conceded and dropped it in Round 3.
*   **5-Round Fallback for E3:** Raised by [Claude Code] in Round 1. Rebutted by [Codex] in Round 2 as invalid without redesign. [Claude Code] conceded and dropped it in Round 3.

Antigravity left the discussion in round 1 (failed: Individual quota reached. Please upgrade your subscription to increase your limits. Resets in 55h59m37s.)
