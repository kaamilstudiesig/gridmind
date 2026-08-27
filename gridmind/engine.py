"""
Deterministic simulation engine for GridMind.

Implements simplified deterministic approximations for:
- Power flow and line loading
- Transformer bank load allocation and thermal rise
- Generation-vs-demand frequency droop
- Critical load preservation
- Hard constraint validation and stability evaluation
- Isolated sandbox action evaluation and live state execution
"""

from typing import Optional, Tuple

from gridmind.models import (
    Action,
    ActionCategory,
    ALLOWED_ACTION_TYPES,
    ConstraintViolation,
    GridState,
    IncidentEvent,
    LineStatus,
    LoadPriority,
    SimulationResult,
    TransformerStatus,
    ViolationType,
)


class GridMindEngine:
    """Deterministic simulation engine for distribution grid operations."""

    def __init__(
        self,
        ambient_rise_coeff: float = 60.0,
        thermal_exponent: float = 1.8,
        f_nominal: float = 50.0000,
        k_droop: float = 0.4000,
        power_factor: float = 0.95,
    ) -> None:
        self.ambient_rise_coeff = ambient_rise_coeff
        self.thermal_exponent = thermal_exponent
        self.f_nominal = f_nominal
        self.k_droop = k_droop
        self.power_factor = power_factor

    def solve(self, state: GridState) -> SimulationResult:
        """
        Solves the complete grid state deterministically and returns a SimulationResult.
        Updates operational metrics in the state.
        """
        env = state.environment
        violations: list[ConstraintViolation] = []

        # 1. Calculate Target Demands for Load Zones
        load_demands_mw: dict[str, float] = {}
        load_served_mw: dict[str, float] = {}

        for l_id, lz in state.load_zones.items():
            spike_mult = 1.0 + (lz.demand_spike_pct / 100.0)
            curtail_mult = 1.0 - (lz.curtailment_pct / 100.0)
            req_mw = lz.base_mw * env.demand_multiplier * spike_mult * curtail_mult
            load_demands_mw[l_id] = req_mw
            load_served_mw[l_id] = req_mw

        # 2. Check Line States & Emergency Tie-Line (L08) Transfer
        # By default L08 carries 0 flow unless CLOSED by an action
        l08_edge = next((e for e in state.edges.values() if e.line_id == "L08"), None)
        l08_flow = 0.0

        t_src = "N08"
        t_dst = "N04"
        if l08_edge and l08_edge.status == LineStatus.CLOSED:
            # Transfers parameterized amount from state.active_transfers, or defaults to 0.100 MW
            t_data = state.active_transfers.get("L08", 0.100)
            if isinstance(t_data, dict):
                l08_flow = float(t_data.get("transfer_mw", 0.100))
                t_src = str(t_data.get("source", "N08"))
                t_dst = str(t_data.get("destination", "N04"))
            else:
                l08_flow = float(t_data)
                t_src = "N08"
                t_dst = "N04"

        # Check line connectivity to load zones
        # N07 is fed by L04, N08 by L05, N10 by L07, N09 by L06
        line_status_map = {e.line_id: e.status for e in state.edges.values()}

        if line_status_map.get("L04") in (LineStatus.TRIPPED, LineStatus.ISOLATED):
            load_served_mw["LZ01"] = 0.0
        if line_status_map.get("L05") in (LineStatus.TRIPPED, LineStatus.ISOLATED):
            load_served_mw["LZ02"] = 0.0
        if line_status_map.get("L07") in (LineStatus.TRIPPED, LineStatus.ISOLATED):
            load_served_mw["LZ04"] = 0.0
        if line_status_map.get("L06") in (LineStatus.TRIPPED, LineStatus.ISOLATED):
            load_served_mw["LZ03"] = 0.0

        # 3. Calculate Power Flow on Lines based on topology and transfer endpoints
        line_flows_mw: dict[str, float] = {}
        line_loadings_pct: dict[str, float] = {}

        flow_l04 = load_served_mw.get("LZ01", 0.0)
        flow_l05 = load_served_mw.get("LZ02", 0.0)
        flow_l07 = load_served_mw.get("LZ04", 0.0)
        flow_l06 = load_served_mw.get("LZ03", 0.0)

        flow_l08 = l08_flow if (l08_edge and l08_edge.status == LineStatus.CLOSED) else 0.0

        if flow_l08 > 0.0:
            if t_src in ("N08", "LZ02"):
                # Transferred specifically from LZ02 at N08 to Feeder-A
                flow_l05 = max(0.0, flow_l05 - flow_l08)
                flow_l02 = flow_l05 + flow_l07
                flow_l01 = flow_l04 + flow_l08
            elif t_src == "N05":
                # Transferred from Feeder-B bus N05 to Feeder-A
                flow_l02 = max(0.0, (flow_l05 + flow_l07) - flow_l08)
                flow_l01 = flow_l04 + flow_l08
            elif t_src in ("N07", "LZ01"):
                # Transferred from Feeder-A LZ01 to Feeder-B
                flow_l04 = max(0.0, flow_l04 - flow_l08)
                flow_l01 = flow_l04
                flow_l02 = flow_l05 + flow_l07 + flow_l08
            elif t_src == "N04":
                # Transferred from Feeder-A bus N04 to Feeder-B
                flow_l01 = max(0.0, flow_l04 - flow_l08)
                flow_l02 = flow_l05 + flow_l07 + flow_l08
            else:
                # Default N08 -> N04
                flow_l05 = max(0.0, flow_l05 - flow_l08)
                flow_l02 = flow_l05 + flow_l07
                flow_l01 = flow_l04 + flow_l08
        else:
            flow_l01 = flow_l04
            flow_l02 = flow_l05 + flow_l07

        flow_l03 = flow_l06

        line_flows_mw["L01"] = flow_l01
        line_flows_mw["L02"] = flow_l02
        line_flows_mw["L03"] = flow_l03
        line_flows_mw["L04"] = flow_l04
        line_flows_mw["L05"] = flow_l05
        line_flows_mw["L06"] = flow_l06
        line_flows_mw["L07"] = flow_l07
        line_flows_mw["L08"] = flow_l08

        # Line loadings and constraint checks
        for edge in state.edges.values():
            flow = line_flows_mw.get(edge.line_id, 0.0)
            if edge.status in (LineStatus.TRIPPED, LineStatus.ISOLATED, LineStatus.OPEN):
                loading_pct = 0.0
            else:
                loading_pct = (flow / edge.capacity_mw) * 100.0

            line_loadings_pct[edge.line_id] = loading_pct

            if (
                edge.status == LineStatus.CLOSED
                and loading_pct > state.constraints.line_loading_pct_max
            ):
                violations.append(
                    ConstraintViolation(
                        violation_type=ViolationType.LINE_OVERLOAD,
                        target_id=edge.line_id,
                        actual_value=loading_pct,
                        limit_value=state.constraints.line_loading_pct_max,
                        description=f"Line {edge.line_id} loading {loading_pct:.2f}% exceeds {state.constraints.line_loading_pct_max:.1f}% limit",
                    )
                )

        # 4. Calculate Transformer Loadings & Temperatures
        transformer_loadings_pct: dict[str, float] = {}
        transformer_temperatures_c: dict[str, float] = {}

        # Feeder N04 Bank: T01 (250 kVA), T05 (250 kVA)
        p_n04 = flow_l01
        base_n04 = state.load_zones["LZ01"].base_mw
        ratio_n04 = (p_n04 / base_n04) if base_n04 > 0 else 1.0

        t01 = state.transformers.get("T01")
        t05 = state.transformers.get("T05")
        if t01:
            if t01.status == TransformerStatus.ISOLATED:
                t01_load = 0.0
            else:
                t01_load = 72.0 * ratio_n04
            transformer_loadings_pct["T01"] = t01_load
            t01.load_pct = t01_load

        if t05:
            if t05.status == TransformerStatus.ISOLATED:
                t05_load = 0.0
            else:
                t05_load = 61.0 * ratio_n04
            transformer_loadings_pct["T05"] = t05_load
            t05.load_pct = t05_load

        # Feeder N05 Bank: T02 (500 kVA), T04 (250 kVA / 500 kVA)
        p_n05 = flow_l02
        base_n05 = state.load_zones["LZ02"].base_mw + state.load_zones["LZ04"].base_mw
        ratio_n05 = (p_n05 / base_n05) if base_n05 > 0 else 1.0

        t02 = state.transformers.get("T02")
        t04 = state.transformers.get("T04")
        n05_total_kva = (p_n05 / self.power_factor) * 1000.0

        if t02 and t04:
            if t04.status == TransformerStatus.ISOLATED:
                t04_load = 0.0
                # All N05 load shifts to T02 (500 kVA)
                t02_load = (n05_total_kva / t02.rating_kva) * 100.0
            elif t02.status == TransformerStatus.ISOLATED:
                t02_load = 0.0
                # All N05 load shifts to T04
                t04_load = (n05_total_kva / t04.rating_kva) * 100.0
            elif t04.rating_kva == 500.0:
                # Uprated bank: 500 kVA + 500 kVA = 1000 kVA total
                t02_load = (n05_total_kva / 1000.0) * 100.0
                t04_load = (n05_total_kva / 1000.0) * 100.0
            else:
                # Normal baseline proportions
                t02_load = 84.0 * ratio_n05
                t04_load = 93.0 * ratio_n05

            transformer_loadings_pct["T02"] = t02_load
            transformer_loadings_pct["T04"] = t04_load
            t02.load_pct = t02_load
            t04.load_pct = t04_load

        # Feeder N06 Bank: T03 (500 kVA)
        p_n06 = flow_l03
        base_n06 = state.load_zones["LZ03"].base_mw
        ratio_n06 = (p_n06 / base_n06) if base_n06 > 0 else 1.0

        t03 = state.transformers.get("T03")
        if t03:
            if t03.status == TransformerStatus.ISOLATED:
                t03_load = 0.0
            else:
                t03_load = 68.0 * ratio_n06
            transformer_loadings_pct["T03"] = t03_load
            t03.load_pct = t03_load

        # Compute Operating Temperatures
        for t_id, trans in state.transformers.items():
            load_p = transformer_loadings_pct.get(t_id, 0.0)
            if trans.status == TransformerStatus.ISOLATED:
                temp_c = env.ambient_temp_c
            else:
                temp_c = env.ambient_temp_c + self.ambient_rise_coeff * (
                    (load_p / 100.0) ** self.thermal_exponent
                )

            transformer_temperatures_c[t_id] = temp_c
            trans.temperature_c = temp_c

            if (
                trans.status != TransformerStatus.ISOLATED
                and temp_c > state.constraints.transformer_temperature_c_max
            ):
                violations.append(
                    ConstraintViolation(
                        violation_type=ViolationType.TRANSFORMER_OVERHEAT,
                        target_id=t_id,
                        actual_value=temp_c,
                        limit_value=state.constraints.transformer_temperature_c_max,
                        description=f"Transformer {t_id} temperature {temp_c:.2f}°C exceeds {state.constraints.transformer_temperature_c_max:.1f}°C limit",
                    )
                )

        # 5. Calculate Total Demand & System Frequency
        total_demand_mw = sum(load_served_mw.values())
        avail_gen_mw = state.available_generation_mw if state.available_generation_mw > 0 else state.p_gen_base
        total_generation_mw = avail_gen_mw

        imbalance = (total_demand_mw - avail_gen_mw) / avail_gen_mw if avail_gen_mw > 0 else 0.0
        freq_hz = self.f_nominal - self.k_droop * imbalance

        if (
            freq_hz < state.constraints.frequency_hz_min
            or freq_hz > state.constraints.frequency_hz_max
        ):
            violations.append(
                ConstraintViolation(
                    violation_type=ViolationType.FREQUENCY_OUT_OF_BOUNDS,
                    target_id="GRID_FREQUENCY",
                    actual_value=freq_hz,
                    limit_value=self.f_nominal,
                    description=f"System frequency {freq_hz:.4f} Hz is outside allowable range [{state.constraints.frequency_hz_min:.2f}, {state.constraints.frequency_hz_max:.2f}] Hz",
                )
            )

        # 6. Critical Load Service Preservation
        critical_service_pct: dict[str, float] = {}
        for l_id, lz in state.load_zones.items():
            if lz.priority == LoadPriority.CRITICAL:
                required = lz.base_mw * env.demand_multiplier
                served = load_served_mw.get(l_id, 0.0)
                svc_pct = (served / required) * 100.0 if required > 0 else 100.0
                critical_service_pct[l_id] = svc_pct

                if svc_pct < state.constraints.critical_load_min_service_pct:
                    violations.append(
                        ConstraintViolation(
                            violation_type=ViolationType.CRITICAL_LOAD_UNSERVED,
                            target_id=l_id,
                            actual_value=svc_pct,
                            limit_value=state.constraints.critical_load_min_service_pct,
                            description=f"Critical load zone {l_id} service {svc_pct:.1f}% is below required {state.constraints.critical_load_min_service_pct:.1f}%",
                        )
                    )

        # 7. Overall Stability Status
        is_stable = len(violations) == 0

        summary = (
            f"Grid State: {'STABLE' if is_stable else 'UNSTABLE'} | "
            f"Freq: {freq_hz:.4f} Hz | Total Load: {total_demand_mw*1000:.1f} kW | "
            f"Violations: {len(violations)}"
        )

        result = SimulationResult(
            frequency_hz=freq_hz,
            total_demand_mw=total_demand_mw,
            total_generation_mw=total_generation_mw,
            available_generation_mw=avail_gen_mw,
            generation_demand_imbalance_mw=total_demand_mw - avail_gen_mw,
            line_flows_mw=line_flows_mw,
            line_loadings_pct=line_loadings_pct,
            transformer_loadings_pct=transformer_loadings_pct,
            transformer_temperatures_c=transformer_temperatures_c,
            load_demands_mw=load_demands_mw,
            load_served_mw=load_served_mw,
            critical_load_service_pct=critical_service_pct,
            violations=violations,
            is_stable=is_stable,
            summary=summary,
        )

        state.latest_result = result
        return result

    def apply_event(self, state: GridState, event: IncidentEvent) -> GridState:
        """Applies an incident event (line trip, demand spike, weather) to state."""
        state.applied_events.append(event)
        etype = event.event_type

        if etype == "line_failure":
            line_id = event.parameters.get("line_id")
            for edge in state.edges.values():
                if edge.line_id == line_id:
                    edge.status = LineStatus.TRIPPED

        elif etype == "demand_spike":
            target = event.parameters.get("target")
            pct = float(event.parameters.get("increase_pct", 0.0))
            for lz in state.load_zones.values():
                if lz.node_id == target or lz.load_id == target:
                    lz.demand_spike_pct += pct

        elif etype == "environment":
            if "ambient_temp_c" in event.parameters:
                state.environment.ambient_temp_c = float(
                    event.parameters["ambient_temp_c"]
                )
            if "demand_multiplier" in event.parameters:
                state.environment.demand_multiplier = float(
                    event.parameters["demand_multiplier"]
                )
            if "storm" in event.parameters:
                state.environment.storm = bool(event.parameters["storm"])

        self.solve(state)
        return state

    def validate_action(
        self, state: GridState, action: Action
    ) -> Tuple[bool, Optional[str]]:
        """
        Validates if an action can be physically or operationally executed in the current state.
        Rejects unknown actions, invalid parameters, out-of-bound operations, and actions that
        create new network constraint overloads.
        """
        atype = action.action_type

        # 1. Whitelist validation
        if atype not in ALLOWED_ACTION_TYPES:
            return (
                False,
                f"Unknown action type '{atype}'. Allowed action types: {sorted(ALLOWED_ACTION_TYPES)}",
            )

        # 2. Parameter and operational validations per action type
        if atype in ("load_transfer", "close_tie_line"):
            line_id = (
                action.parameters.get("line_id")
                or action.parameters.get("line")
                or ("L08" if atype == "close_tie_line" else None)
            )
            if not line_id:
                return False, "Missing required parameter 'line_id' for load_transfer"

            line_edge = next((e for e in state.edges.values() if e.line_id == line_id), None)
            if not line_edge:
                return False, f"Line '{line_id}' does not exist in network topology"

            if line_edge.status in (LineStatus.TRIPPED, LineStatus.ISOLATED):
                return False, f"Cannot transfer load: Emergency tie-line {line_id} is tripped/locked out"

            if atype == "load_transfer":
                source = (
                    action.parameters.get("source")
                    or action.parameters.get("from")
                    or action.parameters.get("from_node")
                )
                destination = (
                    action.parameters.get("destination")
                    or action.parameters.get("to")
                    or action.parameters.get("to_node")
                )

                if not source or source not in state.nodes:
                    return False, f"Source node '{source}' is invalid or does not exist"
                if not destination or destination not in state.nodes:
                    return False, f"Destination node '{destination}' is invalid or does not exist"
                if source == destination:
                    return False, "Source and destination nodes must be different"

                # Critical load protection
                if source in ("N10", "LZ04"):
                    return False, "Load transfer rejected: cannot curtail or transfer critical load zone LZ04 (Hospital-A at N10)"

                # Validate endpoints against network topology for tie-line
                feeder_b_nodes = {"N05", "N08", "LZ02"}
                feeder_a_nodes = {"N04", "N07", "LZ01", "N01"}
                is_b_to_a = source in feeder_b_nodes and destination in feeder_a_nodes
                is_a_to_b = source in feeder_a_nodes and destination in feeder_b_nodes

                if not (is_b_to_a or is_a_to_b):
                    if (source in feeder_b_nodes and destination in feeder_b_nodes) or (source in feeder_a_nodes and destination in feeder_a_nodes):
                        return (
                            False,
                            f"Load transfer rejected: source '{source}' and destination '{destination}' are on the same feeder side of tie-line {line_id}",
                        )
                    return (
                        False,
                        f"Load transfer rejected: unsupported endpoint combination '{source}' -> '{destination}' for tie-line {line_id}. Tie-line {line_id} connects Feeder-A ({line_edge.from_node}) and Feeder-B ({line_edge.to_node}).",
                    )

                raw_mw = action.parameters.get(
                    "transfer_mw",
                    action.parameters.get("mw", action.parameters.get("amount_mw")),
                )
                if raw_mw is None:
                    return False, "Missing required parameter 'transfer_mw' for load_transfer"
                try:
                    transfer_mw = float(raw_mw)
                except (ValueError, TypeError):
                    return False, f"Invalid transfer_mw value '{raw_mw}'"

                if transfer_mw <= 0.0:
                    return False, f"Transfer amount must be greater than 0 MW (got {transfer_mw} MW)"
                if transfer_mw > line_edge.capacity_mw:
                    return (
                        False,
                        f"Transfer amount {transfer_mw:.3f} MW exceeds line {line_id} capacity {line_edge.capacity_mw:.3f} MW",
                    )

            # Generic sandbox evaluation to check resulting network constraints
            candidate = state.clone()
            self._execute_action_mutation(candidate, action, is_sandbox=True)
            cand_res = self.solve(candidate)

            initial_res = state.latest_result or self.solve(state.clone())
            initial_viol_keys = {
                (v.violation_type, v.target_id) for v in initial_res.violations
            }
            new_violations = [
                v
                for v in cand_res.violations
                if (v.violation_type, v.target_id) not in initial_viol_keys
            ]

            if new_violations:
                viol_summary = "; ".join(v.description for v in new_violations)
                return (
                    False,
                    f"Load transfer rejected: transfer creates network constraint violation(s): {viol_summary}",
                )

        elif atype == "isolate_transformer":
            t_id = action.parameters.get("transformer_id") or action.parameters.get("target")
            if not t_id:
                return False, "Missing required parameter 'transformer_id' for isolate_transformer"
            trans = state.transformers.get(t_id)
            if not trans:
                return False, f"Transformer {t_id} does not exist"
            if trans.status == TransformerStatus.ISOLATED:
                return False, f"Transformer {t_id} is already isolated"

        elif atype == "load_restriction":
            target = (
                action.parameters.get("target")
                or action.parameters.get("node_id")
                or action.parameters.get("load_id")
            )
            if not target:
                return False, "Missing required parameter 'target' for load_restriction"

            lz = next(
                (
                    lz
                    for lz in state.load_zones.values()
                    if lz.node_id == target or lz.load_id == target
                ),
                None,
            )
            if not lz:
                return False, f"Load target {target} does not exist"

            if lz.priority == LoadPriority.CRITICAL:
                return (
                    False,
                    f"Load restriction rejected: Zone {lz.load_id} ({lz.node_id}) is CRITICAL",
                )

            if "reduction_pct" not in action.parameters and "reduction" not in action.parameters:
                return False, "Missing required parameter 'reduction_pct' for load_restriction"

            raw_reduction = action.parameters.get(
                "reduction_pct", action.parameters.get("reduction")
            )
            try:
                reduction_pct = float(raw_reduction)
            except (ValueError, TypeError):
                return False, f"Invalid reduction_pct value '{raw_reduction}'"

            if reduction_pct < 0.0:
                return (
                    False,
                    f"Load restriction rejected: reduction_pct {reduction_pct}% cannot be negative",
                )
            if reduction_pct > 100.0:
                return (
                    False,
                    f"Load restriction rejected: reduction_pct {reduction_pct}% cannot exceed 100%",
                )

            # Respect target LoadZone's min_service_pct
            min_svc = lz.min_service_pct * 100.0 if lz.min_service_pct <= 1.0 else lz.min_service_pct
            remaining_service_pct = 100.0 - reduction_pct
            if remaining_service_pct < min_svc:
                return (
                    False,
                    f"Load restriction rejected: reduction {reduction_pct:.1f}% reduces service to {remaining_service_pct:.1f}%, below minimum service requirement of {min_svc:.1f}% for {lz.load_id}",
                )

        elif atype == "transformer_replacement":
            t_id = action.parameters.get("transformer_id") or action.parameters.get("target")
            if not t_id:
                return False, "Missing required parameter 'transformer_id' for transformer_replacement"
            if t_id not in state.transformers:
                return False, f"Transformer {t_id} does not exist"
            raw_kva = action.parameters.get("additional_kva", 250.0)
            try:
                add_kva = float(raw_kva)
                if add_kva <= 0.0:
                    return False, f"additional_kva must be positive (got {add_kva})"
            except (ValueError, TypeError):
                return False, f"Invalid additional_kva value '{raw_kva}'"

        return True, None

    def evaluate_sandbox(self, state: GridState, action: Action) -> SimulationResult:
        """
        Evaluates a candidate action on an isolated, deep-cloned sandbox state.
        Guarantees the live state is NEVER mutated.
        """
        sandbox = state.clone()
        is_valid, reason = self.validate_action(sandbox, action)

        if not is_valid:
            # Solve sandbox without applying invalid action and return rejection result
            result = self.solve(sandbox)
            result.action_applied = action.action_type
            result.action_valid = False
            result.rejection_reason = reason
            result.violations.append(
                ConstraintViolation(
                    violation_type=ViolationType.INVALID_ACTION,
                    target_id=action.action_type,
                    actual_value=0.0,
                    limit_value=0.0,
                    description=f"Action validation rejected: {reason}",
                )
            )
            result.is_stable = False
            return result

        # Apply action inside sandbox (simulates hypothetical planning outcome if planning action)
        self._execute_action_mutation(sandbox, action, is_sandbox=True)
        result = self.solve(sandbox)
        result.action_applied = action.action_type
        result.action_valid = True
        return result

    def apply_action(self, state: GridState, action: Action) -> GridState:
        """
        Validates and executes an approved action on the live GridState.
        Mutates the live state and re-solves.
        For planning actions (e.g. transformer_replacement), logs a planning work order
        rather than physically altering live operational hardware.
        """
        is_valid, reason = self.validate_action(state, action)
        if not is_valid:
            result = self.solve(state)
            result.action_applied = action.action_type
            result.action_valid = False
            result.rejection_reason = reason
            result.violations.append(
                ConstraintViolation(
                    violation_type=ViolationType.INVALID_ACTION,
                    target_id=action.action_type,
                    actual_value=0.0,
                    limit_value=0.0,
                    description=f"Action validation rejected: {reason}",
                )
            )
            result.is_stable = False
            state.latest_result = result
            return state

        self._execute_action_mutation(state, action, is_sandbox=False)
        state.applied_actions.append(action)
        self.solve(state)
        if state.latest_result:
            state.latest_result.action_applied = action.action_type
            state.latest_result.action_valid = True
            if action.action_type == "transformer_replacement":
                state.latest_result.summary += " [Planning work order logged; live physical hardware unchanged]"
        return state

    def _execute_action_mutation(
        self, state: GridState, action: Action, is_sandbox: bool = False
    ) -> None:
        """Internal helper to mutate state attributes according to action semantics."""
        atype = action.action_type

        if atype == "load_restriction":
            target = (
                action.parameters.get("target")
                or action.parameters.get("node_id")
                or action.parameters.get("load_id")
            )
            raw_reduction = action.parameters.get(
                "reduction_pct", action.parameters.get("reduction", 0.0)
            )
            reduction_pct = float(raw_reduction)
            for lz in state.load_zones.values():
                if lz.node_id == target or lz.load_id == target:
                    lz.curtailment_pct = reduction_pct

        elif atype in ("load_transfer", "close_tie_line"):
            line_id = (
                action.parameters.get("line_id")
                or action.parameters.get("line")
                or "L08"
            )
            raw_mw = action.parameters.get(
                "transfer_mw",
                action.parameters.get("mw", action.parameters.get("amount_mw", 0.100)),
            )
            transfer_mw = float(raw_mw)
            source = (
                action.parameters.get("source")
                or action.parameters.get("from")
                or action.parameters.get("from_node", "N08")
            )
            destination = (
                action.parameters.get("destination")
                or action.parameters.get("to")
                or action.parameters.get("to_node", "N04")
            )
            l08 = next((e for e in state.edges.values() if e.line_id == line_id), None)
            if l08:
                l08.status = LineStatus.CLOSED
                state.active_transfers[line_id] = {
                    "transfer_mw": transfer_mw,
                    "source": str(source),
                    "destination": str(destination),
                    "line_id": str(line_id),
                }

        elif atype == "isolate_transformer":
            t_id = action.parameters.get("transformer_id") or action.parameters.get("target")
            if t_id and t_id in state.transformers:
                state.transformers[t_id].status = TransformerStatus.ISOLATED

        elif atype == "transformer_replacement":
            t_id = action.parameters.get("transformer_id") or action.parameters.get("target")
            add_kva = float(action.parameters.get("additional_kva", 250.0))
            if is_sandbox:
                # Sandbox evaluates hypothetical post-upgrade state for planning assessment
                if t_id and t_id in state.transformers:
                    t = state.transformers[t_id]
                    t.rating_kva += add_kva
                    t.prior_failures = 0
                    t.age_years = 0
                    t.status = TransformerStatus.NORMAL
            else:
                # Live execution logs planning work order; does NOT alter live physical hardware
                state.planning_work_orders.append(
                    {
                        "action_type": "transformer_replacement",
                        "transformer_id": t_id,
                        "additional_kva": add_kva,
                        "status": "WORK_ORDER_LOGGED",
                    }
                )
