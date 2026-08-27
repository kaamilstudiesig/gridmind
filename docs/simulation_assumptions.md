# GridMind Simulation Assumptions & Engineering Specifications

This document records the exact deterministic formulations, parameter calibrations, and modeling assumptions implemented in GridMind's distribution simulation engine.

> [!NOTE]
> All electrical, thermal, and mechanical models implemented here are **simplified deterministic approximations** designed for explainability, predictability, and reproducible incident evaluation. They do not claim IEEE-standard compliance or AC power-flow fidelity.

---

## 1. Network Topology & Power Scaling

The synthetic distribution network represents a 3-feeder urban sub-distribution system derived from a 66 kV / 11 kV / 0.415 kV topology:

- **Substations (66 kV Sources)**:
  - `N01` (North-Urban-1) $\to$ feeds Feeder-A (`N04`) via line `L01` (capacity: 2.0 MW).
  - `N02` (Central-Urban-1) $\to$ feeds Feeder-B (`N05`) via line `L02` (capacity: 2.5 MW).
  - `N03` (South-Urban-1) $\to$ feeds Feeder-C (`N06`) via line `L03` (capacity: 2.5 MW).

- **Emergency Inter-Feeder Tie-Line (`L08`)**:
  - Connects Feeder-A (`N04`) and Feeder-B (`N05`).
  - **Normal Operating Status**: `OPEN` (carries $0.00\text{ kW}$ under normal radial operation).
  - **Operational Purpose**: Emergency load-transfer relief pathway (capacity: 1.0 MW). Can be closed explicitly by an operator/control action when healthy. If equipment lockout occurs (`TRIPPED`), it is unavailable.

- **Feeders & Load Zones (11 kV $\to$ 0.415 kV)**:
  - Nominal Power Factor: $\text{pf} = 0.95$.
  - `LZ01` (Residential-A at `N07`): Base demand $0.315875\text{ MW}$ ($315.88\text{ kW}$). Fed by `N04` via line `L04` (0.8 MW capacity).
  - `LZ02` (Commercial-A at `N08`): Base demand $0.447875\text{ MW}$ ($447.88\text{ kW}$). Fed by `N05` via line `L05` (1.0 MW capacity).
  - `LZ04` (Hospital-A at `N10`, Priority: **Critical**): Base demand $0.172000\text{ MW}$ ($172.00\text{ kW}$). Fed by `N05` via line `L07` (0.6 MW capacity).
  - `LZ03` (Industrial-A at `N09`, Priority: High): Base demand $0.323000\text{ MW}$ ($323.00\text{ kW}$). Fed by `N06` via line `L06` (1.2 MW capacity).
  - **Total System Base Demand ($P_{\text{gen\_base}}$)**: $1.258750\text{ MW}$ ($1258.75\text{ kW}$).

---

## 2. Transformer Banks & Load Sharing Model

Total installed transformer capacity is $1750\text{ kVA}$ ($1.6625\text{ MW}$ at $0.95\text{ pf}$), giving a system-wide baseline utilization of $75.71\%$.

### Feeder-B (`N05`) Shared Transformer Bank
Feeder `N05` has co-located transformers `T02` ($500\text{ kVA}$) and `T04` ($250\text{ kVA}$), forming a $750\text{ kVA}$ ($712.5\text{ kW}$) bank:
- **Baseline Allocation**: `T02` carries $420.0\text{ kVA}$ ($84.0\%$ loading); `T04` carries $232.5\text{ kVA}$ ($93.0\%$ loading).
- **Dynamic Load Scaling**: When demand on `N05` changes, each unit's loading scales with the bus demand ratio $M = \frac{P_{\text{N05}}}{P_{\text{N05, base}}}$:
  $$\text{load\_pct}_{\text{T02}} = 84.0\% \times M, \quad \text{load\_pct}_{\text{T04}} = 93.0\% \times M$$
- **Isolation Redistribution**: If `T04` is isolated without demand reduction, all $S_{\text{N05}}$ is transferred to `T02`:
  $$\text{load\_pct}_{\text{T02}} = \frac{S_{\text{N05, kVA}}}{500\text{ kVA}} \times 100\%$$
- **Uprate / Replacement**: If `T04` is uprated to $500\text{ kVA}$, both units form a balanced $1000\text{ kVA}$ bank, sharing the total kVA equally ($50\%$ each).

---

## 3. Simplified Thermal Model

Transformer top-oil / hot-spot operating temperature is computed strictly as a function of ambient temperature and active electrical loading:

$$T = T_{\text{ambient}} + 60.0 \times \left(\frac{\text{load\_pct}}{100}\right)^{1.8}$$

- **Degradation & Failure History**: `prior_failures` and `age_years` are preserved as asset risk attributes and are not added directly to temperature calculations.
- **Isolated State**: When a transformer is isolated, its load is $0.0\%$ and temperature settles to $T_{\text{ambient}}$.
- **Thermal Limit**: $T \le 110.0^\circ\text{C}$.

---

## 4. Deterministic Frequency Droop Model

System frequency is derived directly from the generation-vs-demand balance:

$$f = f_{\text{nominal}} - K_{\text{droop}} \times \left(\frac{\sum P_{\text{demand}} - P_{\text{gen\_base}}}{P_{\text{gen\_base}}}\right)$$

- $f_{\text{nominal}} = 50.0000\text{ Hz}$
- $P_{\text{gen\_base}} = 1.258750\text{ MW}$
- $K_{\text{droop}} = 0.4000\text{ Hz}$
- **Frequency Limits**: $49.50\text{ Hz} \le f \le 50.50\text{ Hz}$.

---

## 5. Hard Operational Constraints

A grid state is declared **Stable** (`is_stable = True`) if and only if **zero** hard constraints are violated:

1. **Frequency Limits**: $49.50\text{ Hz} \le f \le 50.50\text{ Hz}$ (`FREQUENCY_OUT_OF_BOUNDS`).
2. **Line Loading Limit**: Line loading $\le 100.0\%$ for all active lines (`LINE_OVERLOAD`).
3. **Transformer Thermal Limit**: $T \le 110.0^\circ\text{C}$ for all active transformers (`TRANSFORMER_OVERHEAT`).
4. **Critical Load Service**: Served power to priority `critical` loads $\ge 100.0\%$ of required demand (`CRITICAL_LOAD_UNSERVED`).

---

## 6. Action Taxonomy & Guardrails

- **Immediate Operational Control Actions**:
  - `load_restriction`: Curtails demand by `reduction_pct` at a specified load zone.
  - `load_transfer` / `close_tie_line`: Closes emergency tie-line `L08` to transfer up to $100\text{ kW}$ from Feeder-B to Feeder-A. **Rejected with violation** if `L08` is `TRIPPED` / `UNAVAILABLE`.
  - `isolate_transformer`: Opens transformer breaker, shifting downstream load to surviving bank units.
- **Longer-Term Planning Actions**:
  - `transformer_replacement`: Replaces/uprates an asset (e.g. $T04 \to 500\text{ kVA}$). Modeled as a planning-state operation.
- **Sandbox Isolation Invariant**: `evaluate_sandbox(state, action)` deep-clones the grid state and evaluates the candidate action without mutating the original state.
