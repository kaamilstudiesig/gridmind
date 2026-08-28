# GridMind Multi-Agent System

This document describes the role, capabilities, and decision-making mechanisms of each agent in GridMind.

---

## 1. Multi-Agent Team

### A. Grid Analyst Agent (`GridAnalyst`)
- **Primary Mission**: Read live network telemetry and diagnose the root cause of grid distress.
- **Tools Used**: `get_grid_state()`, `get_incident_state()`
- **Output Artifact**: `GridAnalysis`
  - Active violations (e.g. `TRANSFORMER_OVERHEAT` on T04)
  - Affected components (tripped lines, overloaded equipment)
  - Root cause hypotheses (e.g. tie-line L08 lockout combined with ambient heatwave and commercial demand spike)
  - Critical constraints that must be preserved.

### B. Simulation & Planning Agent (`SimulationAgent`)
- **Primary Mission**: Formulate diverse recovery strategies and evaluate each in an isolated counterfactual sandbox.
- **Tools Used**: `evaluate_action()`
- **Candidate Strategy Space**:
  - `load_restriction`: Targeted curtailment on non-critical feeders.
  - `load_transfer`: Inter-feeder load routing over emergency tie-lines.
  - `isolate_transformer`: Emergency breaker trip on overheated units.
  - `transformer_replacement`: Planning work order to uprate bank capacity.
- **Output Artifact**: List of `CandidatePlan` records with authoritative simulation predictions.

### C. Safety Agent (`SafetyAgent`)
- **Primary Mission**: Independent safety review and guardrail enforcement with full veto power.
- **Checks Performed**:
  - Critical facility power preservation (Hospital-A at N10 must remain at 100% service).
  - Frequency boundary compliance ($49.50\text{ Hz} \le f \le 50.50\text{ Hz}$).
  - Line thermal limits ($< 100\%$).
  - Transformer thermal ceiling ($T \le 110.0^\circ\text{C}$).
  - **Cascading Failure Prevention**: Explicitly detects if an action (e.g. isolating T04) causes secondary overloads on surviving bank units (e.g. overloading T02 to $178^\circ\text{C}$).
- **Output Artifact**: `SafetyAssessment` (`approved`, `risk_level`, `reasons`, `mitigations`).

### D. Incident Commander (`IncidentCommander`)
- **Primary Mission**: End-to-end incident lifecycle management, ranking candidate strategies, enforcing human approval, executing live actions, and managing post-action verification and recovery.

---

## 2. Multi-Objective Transparent Scoring Model

Every candidate plan is evaluated against a transparent scoring formula:

$$\text{Score} = S_{\text{stability}} + S_{\text{validity}} + S_{\text{critical}} + S_{\text{frequency}} + S_{\text{thermal}} - P_{\text{curtailment}} - P_{\text{readiness}}$$

| Factor | Description | Weight |
|---|---|---|
| **Stability** | Predicted grid stability after intervention | $+50.0$ if stable, $-40.0$ if unstable |
| **Action Validity** | Valid parameter & equipment schema check | $+20.0$ if valid, $-100.0$ if invalid |
| **Critical Load** | Hospital-A (LZ04) continuous supply | $+20.0$ if $100\%$, $-100.0$ if reduced |
| **Frequency** | Frequency within $[49.5, 50.5]\text{ Hz}$ | $+10.0$ |
| **Thermal Headroom** | Margin below $110^\circ\text{C}$ thermal ceiling | Up to $+15.0$ ($0.75 \times \Delta T$) |
| **Curtailment Penalty** | Impact on commercial/residential customers | $-0.40 \times \text{reduction}\%$ |
| **Operational Readiness** | Immediacy vs planning delay | $+10.0$ for immediate control |

---

## 3. Human-in-the-Loop Checkpoint

Consequential actions that mutate the live electrical grid are **never executed silently**. When the Incident Commander selects the optimal safety-approved strategy:
1. State transitions to `AWAITING_APPROVAL`.
2. The UI renders the mandatory approval modal with predicted outcomes and safety verdicts.
3. If the operator clicks **Approve**, the action is executed via `execute_action()`.
4. If the operator clicks **Reject**, the system enters the `PLANNING` state and generates alternate options.
