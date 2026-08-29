"""
FastAPI Dashboard Backend for GridMind Command Center.
Exposes read-side grid telemetry, persistent audit records, actual agent activity events,
scenario loading, and human-in-the-loop Commander approval delegation.
"""

from __future__ import annotations

import argparse
from datetime import datetime, timezone
import logging
from pathlib import Path
from typing import Any, Optional

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import HTMLResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from pydantic import BaseModel, Field
import uvicorn

from gridmind.audit_store import AuditStore
from gridmind.commander import AuditRecord, AuditRecordStatus, GridMindCommander
from gridmind.contract import GridStateResponse, IncidentStateResponse
from gridmind.llm import LLMClient
from gridmind.service import GridMindService, SUPPORTED_SCENARIOS

logger = logging.getLogger("gridmind.dashboard")

BASE_DIR = Path(__file__).resolve().parent
TEMPLATES_DIR = BASE_DIR / "templates"
STATIC_DIR = BASE_DIR / "static"


class ScenarioLoadRequest(BaseModel):
    scenario_id: str = Field(..., description="Scenario identifier (e.g. SC01, SC01-B, BASE)")


class ApprovalRequest(BaseModel):
    approved_by: Optional[str] = Field(default="operator_lead", description="Operator identifier")
    reason: Optional[str] = Field(default="Operator verified safety constraints", description="Approval justification")
    incident_id: Optional[str] = Field(default=None, description="Optional target incident ID")


class RejectionRequest(BaseModel):
    approved_by: Optional[str] = Field(default="operator_lead", description="Operator identifier")
    reason: Optional[str] = Field(default="Rejected by operator override", description="Rejection rationale")
    incident_id: Optional[str] = Field(default=None, description="Optional target incident ID")


def extract_incident_events(record_dict: dict[str, Any]) -> list[dict[str, Any]]:
    """
    Deterministically transforms an actual AuditRecord into chronological observability events.
    Strict Invariant: Emits ONLY events with real evidence in the record. Zero fabricated events.
    """
    events: list[dict[str, Any]] = []
    ts = record_dict.get("created_at") or datetime.now(timezone.utc).isoformat()
    updated_ts = record_dict.get("updated_at") or ts
    scenario_id = record_dict.get("scenario_id", "UNKNOWN")
    pre_evidence = record_dict.get("pre_state_evidence", [])
    pre_info = pre_evidence[0] if pre_evidence else {}
    specialist_results = record_dict.get("specialist_results", {})

    # Stage 1: Incident & State Telemetry Inspection
    events.append({
        "timestamp": ts,
        "stage": "operations",
        "event_type": "tool_call",
        "tool_name": "get_incident_state",
        "summary": f"Inspected active incident state and topology for scenario '{scenario_id}'.",
        "status": "success",
    })

    violation_descs = pre_info.get("active_violations", [])
    tripped = pre_info.get("tripped_lines", [])
    overheated = pre_info.get("overheated_transformers", [])
    events.append({
        "timestamp": ts,
        "stage": "operations",
        "event_type": "tool_result",
        "tool_name": "get_incident_state",
        "summary": (
            f"State returned: is_stable={pre_info.get('is_stable', False)}, "
            f"tripped_lines={tripped}, overheated_transformers={overheated}, "
            f"active_violations={len(violation_descs)}."
        ),
        "status": "success",
    })

    # Stage 2 & 3: Operations Reasoning & Candidate Generation
    if "operations" in specialist_results:
        op = specialist_results["operations"]
        if op.get("finding"):
            events.append({
                "timestamp": ts,
                "stage": "operations",
                "event_type": "reasoning_summary",
                "tool_name": None,
                "summary": f"Operations finding: {op.get('finding')}",
                "status": "success" if op.get("status") == "ACCEPT" else "rejected",
            })
        candidates = op.get("candidates", [])
        if candidates:
            cand_summaries = [f"{c.get('candidate_id', '')}:{c.get('action_type')}" for c in candidates]
            events.append({
                "timestamp": ts,
                "stage": "operations",
                "event_type": "reasoning_summary",
                "tool_name": None,
                "summary": f"Operations proposed {len(candidates)} plausible candidate(s): {', '.join(cand_summaries)}.",
                "status": "success",
            })

    # Stage 4: Sandbox Evaluations (actual evaluate_action tool calls)
    if "safety" in specialist_results:
        safety = specialist_results["safety"]
        safety_evidence = safety.get("evidence", [])
        for ev in safety_evidence:
            action = ev.get("action", {})
            cid = action.get("candidate_id", "C")
            atype = action.get("action_type", "unknown")
            params = action.get("parameters", {})
            events.append({
                "timestamp": ts,
                "stage": "safety",
                "event_type": "tool_call",
                "tool_name": "evaluate_action",
                "summary": f"Sandbox simulation requested for candidate {cid} ({atype}) with parameters {params}.",
                "status": "pending",
            })
            valid = ev.get("action_valid", False)
            stable = ev.get("is_stable", False)
            temp_t04 = ev.get("predicted_temp_t04")
            viols = ev.get("violations", [])
            is_ok = valid and stable and len(viols) == 0
            temp_str = f"T04={temp_t04:.2f}°C" if isinstance(temp_t04, (int, float)) else "T04=N/A"
            events.append({
                "timestamp": ts,
                "stage": "safety",
                "event_type": "tool_result",
                "tool_name": "evaluate_action",
                "summary": (
                    f"Sandbox outcome for {cid} ({atype}): valid={valid}, stable={stable}, "
                    f"{temp_str}, active_violations={len(viols)}."
                ),
                "status": "success" if is_ok else "rejected",
            })

        # Stage 5: Safety Analysis
        if safety.get("finding"):
            events.append({
                "timestamp": ts,
                "stage": "safety",
                "event_type": "reasoning_summary",
                "tool_name": None,
                "summary": f"Safety evaluation verdict: {safety.get('finding')}",
                "status": "success" if safety.get("status") == "ACCEPT" else "rejected",
            })

    # Stage 6: Planning Analysis
    if "planning" in specialist_results:
        planning = specialist_results["planning"]
        if planning.get("finding"):
            events.append({
                "timestamp": ts,
                "stage": "planning",
                "event_type": "reasoning_summary",
                "tool_name": None,
                "summary": f"Planning assessment: {planning.get('finding')}",
                "status": "success",
            })

    # Stage 7: Commander Recommendation
    rec_action = record_dict.get("recommended_action")
    status_str = record_dict.get("status", "")
    if rec_action:
        events.append({
            "timestamp": ts,
            "stage": "commander",
            "event_type": "recommendation",
            "tool_name": None,
            "summary": (
                f"Commander synthesized recommendation: {rec_action.get('action_type')} "
                f"({rec_action.get('candidate_id', '')}) with parameters {rec_action.get('parameters', {})}."
            ),
            "status": "success",
        })
    else:
        events.append({
            "timestamp": ts,
            "stage": "commander",
            "event_type": "recommendation",
            "tool_name": None,
            "summary": f"No operational action recommended. Incident status set to '{status_str}'.",
            "status": "rejected" if status_str in ("NO_SAFE_ACTION", "ESCALATED") else "success",
        })

    # Stage 8: Human Approval Checkpoint
    approval_data = record_dict.get("approval", {})
    if status_str == AuditRecordStatus.PENDING_APPROVAL.value:
        events.append({
            "timestamp": ts,
            "stage": "approval",
            "event_type": "approval_required",
            "tool_name": None,
            "summary": "Execution paused at PENDING_APPROVAL gate. Awaiting explicit operator authorization.",
            "status": "pending",
        })
    elif approval_data.get("approved") is True:
        events.append({
            "timestamp": approval_data.get("timestamp", updated_ts),
            "stage": "approval",
            "event_type": "approval_required",
            "tool_name": None,
            "summary": (
                f"Operator '{approval_data.get('approved_by')}' approved intervention. "
                f"Reason: {approval_data.get('reason') or 'Standard operating procedure'}."
            ),
            "status": "success",
        })
    elif status_str == AuditRecordStatus.REJECTED_BY_HUMAN.value or approval_data.get("approved") is False:
        events.append({
            "timestamp": approval_data.get("timestamp", updated_ts),
            "stage": "approval",
            "event_type": "approval_required",
            "tool_name": None,
            "summary": (
                f"Operator '{approval_data.get('approved_by')}' rejected intervention. "
                f"Reason: {approval_data.get('reason') or 'Operator override'}."
            ),
            "status": "rejected",
        })

    # Stage 9: Live Execution & Post-Action Verification
    execution_data = record_dict.get("execution", {})
    if execution_data.get("executed") is True:
        events.append({
            "timestamp": updated_ts,
            "stage": "execution",
            "event_type": "execution",
            "tool_name": "execute_action",
            "summary": f"Dispatched authorized action '{rec_action.get('action_type') if rec_action else ''}' to live grid.",
            "status": "success",
        })
        verification_data = record_dict.get("verification", {})
        is_verified = verification_data.get("verified", False)
        post_stable = verification_data.get("post_state_stable", False)
        post_viols = verification_data.get("active_violations", [])
        events.append({
            "timestamp": updated_ts,
            "stage": "verification",
            "event_type": "verification",
            "tool_name": "get_grid_state",
            "summary": (
                f"Post-action verification: verified={is_verified}, post_state_stable={post_stable}, "
                f"remaining_violations={len(post_viols)}. Status={status_str}."
            ),
            "status": "success" if is_verified else "failed",
        })
    elif status_str == AuditRecordStatus.EXECUTION_REJECTED.value:
        events.append({
            "timestamp": updated_ts,
            "stage": "execution",
            "event_type": "execution",
            "tool_name": "execute_action",
            "summary": f"Execution rejected by simulator validation: {execution_data.get('response', {}).get('error_message') or 'Action rejected'}.",
            "status": "failed",
        })

    return events


def create_dashboard_app(
    service: Optional[GridMindService] = None,
    audit_store: Optional[AuditStore] = None,
    commander: Optional[GridMindCommander] = None,
    data_dir: str = "gridmind_data/curated",
    llm_client: Optional[LLMClient] = None,
) -> FastAPI:
    """
    Factory function creating the GridMind Command Center FastAPI application.
    """
    app_service = service or GridMindService(data_dir=data_dir)
    app_audit_store = audit_store or AuditStore()
    app_llm = llm_client or LLMClient()
    app_commander = commander or GridMindCommander(
        service=app_service,
        audit_store=app_audit_store,
        llm_client=app_llm,
    )

    app = FastAPI(
        title="GridMind Command Center",
        description="Operational command-center and observability dashboard for GridMind Agentic Orchestration",
        version="0.1.0",
    )

    # Ensure static and template directories exist
    TEMPLATES_DIR.mkdir(parents=True, exist_ok=True)
    STATIC_DIR.mkdir(parents=True, exist_ok=True)
    (STATIC_DIR / "css").mkdir(parents=True, exist_ok=True)
    (STATIC_DIR / "js").mkdir(parents=True, exist_ok=True)

    app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")
    templates = Jinja2Templates(directory=TEMPLATES_DIR)

    @app.get("/", response_class=HTMLResponse)
    async def index(request: Request):
        return templates.TemplateResponse(
            request=request,
            name="index.html",
            context={
                "title": "GridMind Command Center",
                "version": "0.1.0",
            },
        )

    @app.get("/api/status")
    async def get_status():
        """Returns aggregated status: active scenario, grid telemetry, incident state, and latest audit record."""
        grid_state = app_service.get_grid_state()
        inc_state = app_service.get_incident_state()
        records = app_audit_store.list()
        latest_record = records[0] if records else None
        state_revision = app_service.get_state_revision()

        return JSONResponse({
            "scenario_id": app_service.active_scenario_id,
            "state_revision": state_revision,
            "grid_state": grid_state.to_dict(),
            "incident_state": inc_state.to_dict(),
            "latest_record": latest_record,
            "total_audit_records": len(records),
            "commander_status": latest_record.get("status") if latest_record else "NOMINAL",
        })

    @app.get("/api/events/{incident_id}")
    async def get_incident_events(incident_id: str):
        """Retrieves structured, actual observability events for a specific incident."""
        record = app_audit_store.get(incident_id)
        if not record:
            raise HTTPException(status_code=404, detail=f"Incident '{incident_id}' not found")
        events = extract_incident_events(record)
        return JSONResponse({"incident_id": incident_id, "events": events})

    @app.get("/api/audit/records")
    async def list_audit_records(status: Optional[str] = None):
        """Retrieves all AuditRecords from durable SQLite storage."""
        records = app_audit_store.list(status=status)
        return JSONResponse({"records": records, "count": len(records)})

    @app.get("/api/audit/records/{incident_id}")
    async def get_audit_record(incident_id: str):
        """Retrieves a single AuditRecord by ID."""
        record = app_audit_store.get(incident_id)
        if not record:
            raise HTTPException(status_code=404, detail=f"Incident '{incident_id}' not found")
        return JSONResponse(record)

    @app.get("/api/grid/live")
    async def get_live_grid():
        """Read-only live grid state inspection."""
        grid_state = app_service.get_grid_state()
        return JSONResponse(grid_state.to_dict())

    @app.get("/api/incident/live")
    async def get_live_incident():
        """Read-only active incident telemetry inspection."""
        inc_state = app_service.get_incident_state()
        return JSONResponse(inc_state.to_dict())

    @app.get("/api/scenarios")
    async def list_scenarios():
        """Returns supported scenarios."""
        return JSONResponse({
            "scenarios": sorted(list(SUPPORTED_SCENARIOS)),
            "active": app_service.active_scenario_id,
        })

    @app.post("/api/scenario/load")
    async def load_scenario(req: ScenarioLoadRequest):
        """
        Loads and resets ONLY the requested scenario.
        Guarantees: Does NOT automatically trigger Commander planning, does NOT execute anything.
        """
        try:
            inc_state = app_service.load_scenario(req.scenario_id)
            grid_state = app_service.get_grid_state()
            state_revision = app_service.get_state_revision()
            return JSONResponse({
                "success": True,
                "scenario_id": app_service.active_scenario_id,
                "state_revision": state_revision,
                "incident_state": inc_state.to_dict(),
                "grid_state": grid_state.to_dict(),
                "message": f"Loaded scenario '{app_service.active_scenario_id}'. Ready for investigation.",
            })
        except ValueError as err:
            raise HTTPException(status_code=400, detail=str(err))

    @app.post("/api/commander/plan")
    async def trigger_plan():
        """Triggers the full Commander multi-specialist investigation and planning cycle."""
        try:
            plan_result = app_commander.plan_incident_response()
            return JSONResponse(plan_result.to_dict())
        except Exception as err:
            logger.exception("Error during commander planning: %s", err)
            raise HTTPException(status_code=500, detail=str(err))

    @app.post("/api/commander/approve")
    async def approve_action(req: ApprovalRequest):
        """
        Human-in-the-Loop authorization gate.
        Delegates strictly to GridMindCommander.approve_and_execute().
        Guarantees: Dashboard cannot modify action_type or parameters, cannot bypass validation.
        """
        target_id = req.incident_id
        if not target_id:
            records = app_audit_store.list(status=AuditRecordStatus.PENDING_APPROVAL.value)
            if not records:
                raise HTTPException(
                    status_code=400,
                    detail="No incident currently in PENDING_APPROVAL status to approve."
                )
            target_id = records[0]["incident_id"]

        try:
            approval_payload = {
                "approved": True,
                "approved_by": req.approved_by or "operator_lead",
                "reason": req.reason or "Authorized by control room operator",
            }
            record = app_commander.approve_and_execute(
                approval=approval_payload,
                incident_id=target_id,
            )
            return JSONResponse({
                "success": True,
                "record": record.to_dict(),
                "message": f"Incident '{target_id}' executed with final status: '{record.status}'",
            })
        except ValueError as err:
            raise HTTPException(status_code=400, detail=str(err))
        except Exception as err:
            logger.exception("Error during commander approval: %s", err)
            raise HTTPException(status_code=500, detail=str(err))

    @app.post("/api/commander/reject")
    async def reject_action(req: RejectionRequest):
        """
        Human rejection pathway.
        Delegates strictly to GridMindCommander.approve_and_execute(approved=False).
        """
        target_id = req.incident_id
        if not target_id:
            records = app_audit_store.list(status=AuditRecordStatus.PENDING_APPROVAL.value)
            if not records:
                raise HTTPException(
                    status_code=400,
                    detail="No incident currently in PENDING_APPROVAL status to reject."
                )
            target_id = records[0]["incident_id"]

        try:
            approval_payload = {
                "approved": False,
                "approved_by": req.approved_by or "operator_lead",
                "reason": req.reason or "Operator override / rejected",
            }
            record = app_commander.approve_and_execute(
                approval=approval_payload,
                incident_id=target_id,
            )
            return JSONResponse({
                "success": True,
                "record": record.to_dict(),
                "message": f"Incident '{target_id}' rejected by human operator.",
            })
        except ValueError as err:
            raise HTTPException(status_code=400, detail=str(err))
        except Exception as err:
            logger.exception("Error during commander rejection: %s", err)
            raise HTTPException(status_code=500, detail=str(err))

    return app


def run_dashboard(
    host: str = "127.0.0.1",
    port: int = 8080,
    data_dir: str = "gridmind_data/curated",
    log_level: str = "info",
) -> None:
    """Runs the GridMind Command Center dashboard synchronously via Uvicorn."""
    app = create_dashboard_app(data_dir=data_dir)
    uvicorn.run(app, host=host, port=port, log_level=log_level)


def main() -> None:
    """CLI entrypoint for the GridMind Command Center."""
    parser = argparse.ArgumentParser(
        description="Run GridMind Command Center & Observability Dashboard"
    )
    parser.add_argument("--host", type=str, default="127.0.0.1", help="Host to bind to (default: 127.0.0.1)")
    parser.add_argument("--port", type=int, default=8080, help="Port to listen on (default: 8080)")
    parser.add_argument("--data-dir", type=str, default="gridmind_data/curated", help="Path to curated grid data")
    parser.add_argument("--log-level", type=str, default="info", help="Logging level")
    args = parser.parse_args()

    print(f"Starting GridMind Command Center on http://{args.host}:{args.port}")
    run_dashboard(
        host=args.host,
        port=args.port,
        data_dir=args.data_dir,
        log_level=args.log_level,
    )


if __name__ == "__main__":
    main()
