# GridMind Safety Architecture & Guardrails

This document outlines the safety principles, hard constraint guardrails, and cascading failure protections implemented in GridMind.

---

## 1. Safety Guardrails & Hard Constraints

GridMind enforces four hard physical and operational boundaries in the electrical distribution network:

| Constraint Type | Limit Boundary | Violation Trigger | Physical Consequence |
|---|---|---|---|
| **System Frequency** | $49.50\text{ Hz} \le f \le 50.50\text{ Hz}$ | `FREQUENCY_OUT_OF_BOUNDS` | Generator trip / under-frequency load shedding |
| **Line Loading** | $\le 100.0\%$ rated capacity | `LINE_OVERLOAD` | Conductor sag, thermal damage, line trip |
| **Transformer Temperature** | $T \le 110.0^\circ\text{C}$ | `TRANSFORMER_OVERHEAT` | Insulation breakdown, catastrophic oil fire |
| **Critical Facility Service** | $\ge 100.0\%$ continuous supply | `CRITICAL_LOAD_UNSERVED` | Power loss to hospital life-support systems |

---

## 2. Guardrail Enforcement Layers

### A. Server-Side Parameter & Whitelist Validation
- All action requests undergo strict whitelist validation (`ALLOWED_ACTION_TYPES`).
- Pydantic models validate numeric ranges (e.g. $0\% \le \text{reduction} \le 100\%$, $\text{transfer\_mw} > 0$).
- Field aliases are normalized server-side to prevent parameter injection.

### B. Sandbox Isolation Invariance
- `evaluate_action()` operates exclusively on deep-cloned state instances (`state.clone()`).
- Live physical equipment states, transformer temperatures, and breaker positions are **never mutated** during exploratory evaluations.

### C. Cascading Failure Prevention
- Actions that relieve a local overload by shifting burden elsewhere are evaluated across the entire network topology.
- Example: In SC01, isolating overheated transformer `T04` transfers all Feeder-B demand onto co-located unit `T02`, forcing `T02` from $82^\circ\text{C}$ to $178^\circ\text{C}$ ($163\%$ load).
- The **Safety Agent** explicitly models this cascading dynamic and vetoes the isolation plan before it can reach the operator.

### D. Critical Load Protection
- Priority `CRITICAL` loads (such as Hospital-A at Node N10) cannot be curtailed or transferred as a source for load relief.
- Any action attempting to reduce power delivery to critical facilities is rejected by the engine and vetoed by the Safety Agent.

### E. Human-in-the-Loop Checkpoint
- No consequential grid-changing action can execute without explicit operator authorization.
- The approval gate exposes full simulation metrics, safety verdicts, and predicted side-effects to ensure human oversight.
