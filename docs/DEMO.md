# GridMind End-to-End Demo Guide

This guide walks through running the primary hackathon demonstration: **Scenario SC01 (Heatwave, Tie-Line Lockout & Feeder Overload)**.

---

## 1. Startup

1. Open a terminal in the project directory:
   ```bash
   python -m gridmind.http_server
   ```
2. Open your web browser to:
   ```
   http://127.0.0.1:8000/
   ```

---

## 2. Demonstration Flow

### Step 1: Establish Nominal Baseline
- In the top navigation bar, select `BASE: Nominal Operating State` and click **Reset / Load**.
- Observe the Command Center:
  - System frequency is **50.0000 Hz**.
  - Total demand is **1258.8 kW**.
  - All lines and transformers show **green** (nominal).
  - Stability status is **STABLE** (0 active violations).

### Step 2: Inject Scenario SC01 (Distress Event)
- In the scenario selector, choose `SC01: Peak Overload & Tie Lockout` and click **Reset / Load**.
- **Observe the Incident Detection**:
  - Emergency tie-line **L08** trips (`L08 (TIE): TRIPPED / LOCKED OUT` in red dashed line).
  - Ambient temperature rises to **34.0°C** with a **+15% demand multiplier** and a **+12% spike** on Commercial Zone N08.
  - Transformer **T04** overheats to **112.65°C** (exceeding the 110.0°C safety limit).
  - System frequency drops to **49.9204 Hz**.
  - Stability transitions to **UNSTABLE** with active violation `TRANSFORMER_OVERHEAT: T04`.

### Step 3: Run AI Incident Commander
- Click the glowing blue button: **Run AI Incident Commander**.
- Watch the multi-agent orchestration pipeline progress live:
  1. **Grid Analyst**: Identifies L08 lockout, ambient heatwave, commercial spike on N08, and T04 thermal breach.
  2. **Simulation Agent**: Formulates 4 candidate strategies and simulates each in an isolated sandbox.
  3. **Safety Agent**: Performs safety review on all 4 plans.
     - Rejects Plan B (Load transfer over tripped L08 is physically impossible).
     - Rejects Plan C (Isolating T04 triggers a cascading overload on T02 to 178°C).
     - Approves Plan A (Targeted load curtailment restores stability and keeps Hospital-A online).

### Step 4: Human-in-the-Loop Checkpoint
- Notice the prominent modal banner: **CONSEQUENTIAL ACTION REQUIRES HUMAN APPROVAL**.
- Review the recommended action:
  - **Plan A**: Targeted Load Curtailment (-15% on N08)
  - **Safety Review**: APPROVED
  - **Critical Facilities**: 0 Affected (100% Protected)
  - **Score**: 89.2

### Step 5: Approve & Execute
- Click **Approve & Execute On Live Grid**.
- Watch the execution pipeline:
  - `APPROVED` $\to$ `EXECUTING` $\to$ `VERIFYING` $\to$ `RESOLVED`.
- The live grid state updates immediately:
  - T04 temperature drops to **97.55°C** (safe operating zone).
  - System frequency recovers to **49.9320 Hz**.
  - All active violations clear to **0**.
  - Hospital-A remains at **100% service**.
  - Stability status updates to **STABLE**.
  - Incident timeline logs the final **INCIDENT RESOLVED** event.

---

## 3. Alternative Operator Rejection & Replanning Demo

To demonstrate the agent's adaptability:
1. Reload `SC01` and click **Run AI Incident Commander**.
2. When the approval gate appears, click **Reject Action & Replan**.
3. The Incident Commander will log the operator override in the audit timeline, re-enter the planning phase, and generate alternative viable plans.
