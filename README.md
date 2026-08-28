# GridMind

> **Autonomous AI Electrical Grid Incident Commander & Decision-Support System**  
> *Calibrated to a Bengaluru-inspired distribution grid topology.*

---

## ⚡ Overview

**GridMind** is an autonomous multi-agent incident-response and operations platform for electrical distribution grids. When network distress occurs (heatwaves, line lockouts, demand surges, transformer overheating), GridMind:

1. **Observes**: Reads real-time network telemetry via standard Model Context Protocol (MCP) tools.
2. **Investigates**: The **Grid Analyst** identifies tripped equipment, root-cause hypotheses, and constraint violations.
3. **Simulates**: The **Simulation Agent** formulates multiple recovery strategies and runs sandboxed counterfactual simulations.
4. **Safety-Checks**: The **Safety Agent** independently audits plans for critical facility preservation and cascading overloads.
5. **Ranks**: The **Decision Engine** scores candidate interventions using a multi-objective transparent model.
6. **Asks Human**: Enforces a **mandatory operator approval gate** before consequential live grid switching.
7. **Executes & Verifies**: Applies the approved action on live grid hardware and verifies post-action stability.
8. **Recovers**: Initiates automated replanning loops if verification fails.

---

## 🏛 Architecture

```
                                 HUMAN OPERATOR
                                       │
                                       ▼
                       GRIDMIND COMMAND CENTER (UI)
                          (Interactive SVG & REST/WS)
                                       │
                                       ▼
                        INCIDENT COMMANDER (AGENT)
                                       │
              ┌────────────────────────┼────────────────────────┐
              ▼                        ▼                        ▼
        GRID ANALYST           SIMULATION AGENT           SAFETY AGENT
       (Telemetry &             (Counterfactual            (Constraint &
        Root Cause)                Sandboxes)            Cascading Check)
              │                        │                        │
              └────────────────────────┼────────────────────────┘
                                       │
                                       ▼
                            HUMAN APPROVAL GATE
                           (Mandatory Checkpoint)
                                       │
                           ┌───────────┴───────────┐
                           ▼                       ▼
                        APPROVE                 REJECT
                           │                       │
                           ▼                       ▼
                     EXECUTE ACTION             REPLAN
                           │
                           ▼
                    POST-VERIFICATION
                           │
                 ┌─────────┴─────────┐
                 ▼                   ▼
              STABLE              UNSTABLE
                 │                   │
                 ▼                   ▼
             RESOLVED          RECOVERY LOOP
```

---

## 🚀 Quickstart

### Prerequisites
- Python $\ge$ 3.11
- Web browser (Chrome, Edge, Firefox, Safari)

### Installation
```bash
# Clone and install dependencies in editable mode
pip install -e ".[dev]"
```

### Running the System
Start the unified server (hosts Command Center UI, REST API, WebSockets, and MCP endpoints on a single port):

```bash
python -m gridmind.http_server
```

Then open your browser:
- **Command Center Dashboard**: [http://127.0.0.1:8000/](http://127.0.0.1:8000/)
- **Streamable HTTP MCP Endpoint**: `http://127.0.0.1:8000/mcp`
- **SSE MCP Endpoint**: `http://127.0.0.1:8000/sse`
- **Health Check**: `http://127.0.0.1:8000/health`

### Running the Test Suite
```bash
python -m pytest tests/ -v
```
*(All 75 unit and integration tests run deterministically without requiring external API keys).*

---

## 🛠 Available MCP Tools

GridMind exposes six deterministic tools for agents and external orchestrators:

| Tool Name | Type | Description |
|---|---|---|
| `get_grid_state` | Read-only | Returns live frequency, line flows, transformer temperatures, and violations. |
| `get_incident_state` | Read-only | Returns active scenario conditions, tripped lines, and unserved critical loads. |
| `evaluate_action` | Sandbox | Evaluates candidate actions in an isolated deep-cloned sandbox without mutating live state. |
| `execute_action` | Consequential | Executes validated action on live grid hardware and recomputes all physical metrics. |
| `get_last_simulation_result` | Read-only | Returns the most recent sandbox evaluation or live execution result. |
| `load_scenario` | Idempotent | Resets and initializes the grid to a specific scenario (e.g. `SC01` heatwave overload). |

---

## 🔬 Multi-Agent Roles

- **Incident Commander (`agent/incident_manager.py`)**: Coordinates the incident lifecycle state machine (`DETECTED` $\to$ `INVESTIGATING` $\to$ `SIMULATING` $\to$ `AWAITING_APPROVAL` $\to$ `RESOLVED`).
- **Grid Analyst (`agent/grid_analyst.py`)**: Identifies root causes and affected assets grounded in real simulator telemetry.
- **Simulation Agent (`agent/simulation_agent.py`)**: Generates candidate plans and scores outcomes using multi-objective optimization.
- **Safety Agent (`agent/safety_agent.py`)**: Enforces hard constraints, preserves critical facilities (Hospital-A), and vetoes cascading failures.

---

## 📚 Documentation

- [Architecture & State Machine](docs/ARCHITECTURE.md)
- [Multi-Agent System & Scoring Model](docs/AGENTS.md)
- [End-to-End Demo Walkthrough](docs/DEMO.md)
- [Safety Architecture & Guardrails](docs/SAFETY.md)
- [Simulation Assumptions & Physical Models](docs/simulation_assumptions.md)

---

## 📄 License & AI Disclosure

This project was built as an agentic incident-response system for electrical distribution grids. Claude was used as an AI pair-programming assistant during development and design.
