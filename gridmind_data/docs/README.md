# GridMind — Bengaluru Starter Data Pack

This pack is intentionally split into **public evidence** and **synthetic simulation data**.

## Public evidence
`source_inventory.csv` lists the datasets/reports to download and cite.
`verified_public_facts.csv` contains only figures we have verified from the public sources.

Key facts include:
- BESCOM reported 497,991 distribution transformers as of 31 Mar 2024.
- 119,632 DTs were metered; 364,999 were unmetered; 87,067 were still to be metered.
- All feeders up to 11 kV were reported as metered.
- KERC/BESCOM reported 38,288 transformer failures in FY2023-24 (7.69%) and 31,852 through Jan 2025 for FY2024-25 (6.08%).

## Synthetic simulation
`synthetic_grid_*.csv`, `synthetic_transformers.csv`, `synthetic_load_zones.csv` and `seed_scenario_SC01.json` are synthetic.

They are **not BESCOM telemetry** and must never be presented as such.

## First build target
Do not add an LLM yet.

Build a deterministic simulator that accepts:
1. a grid state
2. one proposed action

and returns:
- frequency
- line loading
- transformer temperature
- critical-load service
- constraint violations
- stable=true/false

Once that evaluator is trustworthy, expose it through MCP and let the agent propose/test actions.
