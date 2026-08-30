"""
FastAPI Dashboard Backend for GridMind Command Center.
Exposes read-side grid telemetry, persistent audit records, actual agent activity events,
scenario loading, and role-based human-in-the-loop Commander approval delegation.
"""

from __future__ import annotations

import argparse
import asyncio
from contextlib import asynccontextmanager
from dataclasses import dataclass
from datetime import datetime, timezone
import json
import logging
import os
from pathlib import Path
from typing import Any, Optional

from fastapi import Depends, FastAPI, HTTPException, Header, Query, Request, status
from fastapi.responses import HTMLResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from pydantic import BaseModel, Field
import uvicorn

from gridmind.audit_store import AuditStore
from gridmind.commander import AuditRecord, AuditRecordStatus, GridMindCommander
from gridmind.contract import GridStateResponse, IncidentStateResponse
from gridmind.llm import LLMClient
from gridmind.mcp_server import GridMindMCPServer
from gridmind.service import GridMindService, SUPPORTED_SCENARIOS

logger = logging.getLogger("gridmind.dashboard")

BASE_DIR = Path(__file__).resolve().parent
TEMPLATES_DIR = BASE_DIR / "templates"
STATIC_DIR = BASE_DIR / "static"


# ==============================================================================
# Authentication & Role-Based Access Control (RBAC)
# ==============================================================================

@dataclass
class AuthenticatedUser:
    username: str
    role: str
    roles: list[str]


DEFAULT_AUTH_TOKENS: dict[str, dict[str, Any]] = {
    "gm-lead-token-secret": {
        "username": "operator_alice",
        "role": "operator_lead",
        "roles": ["viewer", "operator", "operator_lead", "admin"],
    },
    "gm-operator-token-secret": {
        "username": "operator_bob",
        "role": "operator",
        "roles": ["viewer", "operator"],
    },
    "gm-viewer-token-secret": {
        "username": "viewer_charlie",
        "role": "viewer",
        "roles": ["viewer"],
    },
}


def get_token_registry() -> dict[str, dict[str, Any]]:
    """Loads token mapping from GRIDMIND_AUTH_TOKENS env var or falls back to defaults."""
    env_tokens = os.environ.get("GRIDMIND_AUTH_TOKENS")
    if env_tokens:
        try:
            return json.loads(env_tokens)
        except Exception as e:
            logger.warning("Failed to parse GRIDMIND_AUTH_TOKENS JSON (%s); using defaults.", e)
    return DEFAULT_AUTH_TOKENS


def get_current_user(authorization: Optional[str] = Header(default=None)) -> AuthenticatedUser:
    """
    Authenticates operator from Bearer token in the Authorization header.
    Rejects unauthenticated requests with 401.
    """
    if not authorization:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Missing Authorization header. Bearer token required.",
            headers={"WWW-Authenticate": "Bearer"},
        )

    parts = authorization.strip().split()
    if len(parts) != 2 or parts[0].lower() != "bearer":
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid Authorization header format. Expected 'Bearer <token>'.",
            headers={"WWW-Authenticate": "Bearer"},
        )

    token = parts[1]
    registry = get_token_registry()
    user_info = registry.get(token)
    if not user_info:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid or expired bearer token.",
            headers={"WWW-Authenticate": "Bearer"},
        )

    roles = user_info.get("roles", [user_info.get("role", "viewer")])
    return AuthenticatedUser(
        username=user_info.get("username", "anonymous_operator"),
        role=user_info.get("role", "viewer"),
        roles=roles,
    )


def require_role(required_role: str):
    """Dependency factory checking that the authenticated user possesses the required role."""
    def role_checker(user: AuthenticatedUser = Depends(get_current_user)) -> AuthenticatedUser:
        if required_role not in user.roles:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail=f"Forbidden: Operator '{user.username}' lacks required role '{required_role}'.",
            )
        return user
    return role_checker


# ==============================================================================
# Request Models
# ==============================================================================

class ScenarioLoadRequest(BaseModel):
    scenario_id: str = Field(..., description="Scenario identifier (e.g. SC01, SC01-B, BASE)")


class ApprovalRequest(BaseModel):
    reason: Optional[str] = Field(default="Authorized by control room operator", description="Approval justification")
    incident_id: Optional[str] = Field(default=None, description="Optional target incident ID")


class RejectionRequest(BaseModel):
    reason: Optional[str] = Field(default="Rejected by operator override", description="Rejection rationale")
    incident_id: Optional[str] = Field(default=None, description="Optional target incident ID")


# ==============================================================================
# Observability Event Extraction
# ==============================================================================

def extract_incident_events(record_dict: dict[str, Any]) -> list[dict[str, Any]]:
    """
    Deterministically transforms an actual AuditRecord into chronological observability events.
    Strict Invariant: Emits ONLY events with real evidence in the record. Zero fabricated events.
    Event Taxonomy:
    - state_inspection
    - sandbox_evaluation
    - reasoning_summary
    - recommendation
    - approval_checkpoint
    - execution_dispatch
    - verification_result
    """
    events: list[dict[str, Any]] = []
    ts = record_dict.get("created_at") or ""
    updated_ts = record_dict.get("updated_at") or ts
    scenario_id = record_dict.get("scenario_id", "UNKNOWN")
    pre_evidence = record_dict.get("pre_state_evidence", [])
    specialist_results = record_dict.get("specialist_results", {})

    # 1. State Inspection Evidence (emitted ONLY when actual pre_state_evidence exists)
    if pre_evidence and isinstance(pre_evidence, list) and len(pre_evidence) > 0 and isinstance(pre_evidence[0], dict) and pre_evidence[0]:
        pre_info = pre_evidence[0]
        violation_descs = pre_info.get("active_violations", [])
        tripped = pre_info.get("tripped_lines", [])
        overheated = pre_info.get("overheated_transformers", [])
        events.append({
            "timestamp": ts,
            "stage": "operations",
            "event_type": "state_inspection",
            "summary": (
                f"Active incident state for scenario '{scenario_id}': is_stable={pre_info.get('is_stable', False)}, "
                f"tripped_lines={tripped}, overheated_transformers={overheated}, "
                f"active_violations={len(violation_descs)}."
            ),
            "status": "success",
        })

    # 2. Operations Specialist Reasoning & Candidate Identification
    if "operations" in specialist_results:
        op = specialist_results["operations"]
        if op.get("finding"):
            events.append({
                "timestamp": ts,
                "stage": "operations",
                "event_type": "reasoning_summary",
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
                "summary": f"Operations proposed {len(candidates)} plausible candidate(s): {', '.join(cand_summaries)}.",
                "status": "success",
            })

    # 3. Sandbox Evaluations (from actual safety evaluation evidence)
    if "safety" in specialist_results:
        safety = specialist_results["safety"]
        safety_evidence = safety.get("evidence", [])
        for ev in safety_evidence:
            action = ev.get("action", {})
            cid = action.get("candidate_id", "C")
            atype = action.get("action_type", "unknown")
            params = action.get("parameters", {})
            valid = ev.get("action_valid", False)
            stable = ev.get("is_stable", False)
            temp_t04 = ev.get("predicted_temp_t04")
            viols = ev.get("violations", [])
            is_ok = valid and stable and len(viols) == 0
            temp_str = f"predicted T04={temp_t04:.2f}°C" if isinstance(temp_t04, (int, float)) else "T04=N/A"
            events.append({
                "timestamp": ts,
                "stage": "safety",
                "event_type": "sandbox_evaluation",
                "summary": (
                    f"Sandbox evaluation for candidate {cid} ({atype}, params={params}): "
                    f"valid={valid}, stable={stable}, {temp_str}, active_violations={len(viols)}."
                ),
                "status": "success" if is_ok else "rejected",
                "candidate": action,
                "evidence": ev,
            })

        # 4. Safety Specialist Reasoning
        if safety.get("finding"):
            events.append({
                "timestamp": ts,
                "stage": "safety",
                "event_type": "reasoning_summary",
                "summary": f"Safety evaluation verdict: {safety.get('finding')}",
                "status": "success" if safety.get("status") == "ACCEPT" else "rejected",
            })

    # 5. Planning Specialist Reasoning
    if "planning" in specialist_results:
        planning = specialist_results["planning"]
        if planning.get("finding"):
            events.append({
                "timestamp": ts,
                "stage": "planning",
                "event_type": "reasoning_summary",
                "summary": f"Planning assessment: {planning.get('finding')}",
                "status": "success",
            })

    # 6. Commander Synthesized Recommendation
    rec_action = record_dict.get("recommended_action")
    status_str = record_dict.get("status", "")
    if rec_action:
        events.append({
            "timestamp": ts,
            "stage": "commander",
            "event_type": "recommendation",
            "summary": (
                f"Commander recommendation: {rec_action.get('action_type')} "
                f"({rec_action.get('candidate_id', '')}) with parameters {rec_action.get('parameters', {})}."
            ),
            "status": "success",
        })
    else:
        events.append({
            "timestamp": ts,
            "stage": "commander",
            "event_type": "recommendation",
            "summary": f"No operational action recommended. Incident status set to '{status_str}'.",
            "status": "rejected" if status_str in ("NO_SAFE_ACTION", "ESCALATED") else "success",
        })

    # 7. Human Approval Checkpoint
    approval_data = record_dict.get("approval", {})
    if status_str == AuditRecordStatus.PENDING_APPROVAL.value:
        events.append({
            "timestamp": ts,
            "stage": "approval",
            "event_type": "approval_checkpoint",
            "summary": "Execution paused at PENDING_APPROVAL checkpoint. Awaiting explicit operator authorization.",
            "status": "pending",
        })
    elif approval_data.get("approved") is True:
        events.append({
            "timestamp": approval_data.get("timestamp") or updated_ts,
            "stage": "approval",
            "event_type": "approval_checkpoint",
            "summary": (
                f"Operator '{approval_data.get('approved_by')}' approved intervention. "
                f"Reason: {approval_data.get('reason') or 'Standard operating procedure'}."
            ),
            "status": "success",
        })
    elif status_str == AuditRecordStatus.REJECTED_BY_HUMAN.value or approval_data.get("approved") is False:
        events.append({
            "timestamp": approval_data.get("timestamp") or updated_ts,
            "stage": "approval",
            "event_type": "approval_checkpoint",
            "summary": (
                f"Operator '{approval_data.get('approved_by')}' rejected intervention. "
                f"Reason: {approval_data.get('reason') or 'Operator override'}."
            ),
            "status": "rejected",
        })

    # 8. Execution Dispatch (emitted ONLY when execution actually occurred or was rejected)
    execution_data = record_dict.get("execution", {})
    if execution_data.get("executed") is True:
        events.append({
            "timestamp": updated_ts,
            "stage": "execution",
            "event_type": "execution_dispatch",
            "summary": f"Dispatched authorized action '{rec_action.get('action_type') if rec_action else ''}' to live grid engine.",
            "status": "success",
        })
        # 9. Verification Result (derived from actual verification data)
        verification_data = record_dict.get("verification", {})
        is_verified = verification_data.get("verified", False)
        post_stable = verification_data.get("post_state_stable", False)
        post_viols = verification_data.get("active_violations", [])
        events.append({
            "timestamp": updated_ts,
            "stage": "verification",
            "event_type": "verification_result",
            "summary": (
                f"Post-action verification: verified={is_verified}, post_state_stable={post_stable}, "
                f"remaining_violations={len(post_viols)}. Status={status_str}."
            ),
            "status": "success" if is_verified else "failed",
            "verification": verification_data,
        })
    elif status_str == AuditRecordStatus.EXECUTION_REJECTED.value:
        events.append({
            "timestamp": updated_ts,
            "stage": "execution",
            "event_type": "execution_dispatch",
            "summary": f"Execution rejected by simulator validation: {execution_data.get('response', {}).get('error_message') or 'Action rejected'}.",
            "status": "failed",
        })

    return events


# ==============================================================================
# FastAPI Application Factory
# ==============================================================================

def create_dashboard_app(
    service: Optional[GridMindService] = None,
    audit_store: Optional[AuditStore] = None,
    commander: Optional[GridMindCommander] = None,
    data_dir: str = "gridmind_data/curated",
    llm_client: Optional[LLMClient] = None,
    mount_mcp: bool = True,
) -> FastAPI:
    """
    Factory function creating the GridMind Command Center FastAPI application.
    Hosts the operator dashboard UI, authenticated REST APIs, and mounts the
    Streamable HTTP & SSE MCP server on a single unified process.
    """
    app_service = service or GridMindService(data_dir=data_dir)
    app_audit_store = audit_store or AuditStore()
    app_llm = llm_client or (commander.llm_client if commander else None) or LLMClient()
    app_commander = commander or GridMindCommander(
        service=app_service,
        audit_store=app_audit_store,
        llm_client=app_llm,
    )

    # Initialize MCP Server wrapper sharing the exact same singleton service & commander
    mcp_wrapper = GridMindMCPServer(
        service=app_service,
        commander=app_commander,
        audit_store=app_audit_store,
        data_dir=data_dir,
    )
    mcp_server = mcp_wrapper.server

    streamable_app = mcp_server.streamable_http_app(streamable_http_path="/mcp")
    sse_app = mcp_server.sse_app(sse_path="/sse", message_path="/messages")

    @asynccontextmanager
    async def app_lifespan(app: FastAPI):
        async with mcp_server.session_manager.run():
            yield

    app = FastAPI(
        title="GridMind Command Center",
        description="Operational command-center and observability dashboard for GridMind Agentic Orchestration",
        version="0.1.0",
        lifespan=app_lifespan if mount_mcp else None,
    )

    if mount_mcp:
        for route in streamable_app.routes:
            app.router.routes.append(route)
        for route in sse_app.routes:
            app.router.routes.append(route)

    # Ensure static and template directories exist
    TEMPLATES_DIR.mkdir(parents=True, exist_ok=True)
    STATIC_DIR.mkdir(parents=True, exist_ok=True)
    (STATIC_DIR / "css").mkdir(parents=True, exist_ok=True)
    (STATIC_DIR / "js").mkdir(parents=True, exist_ok=True)

    app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")
    templates = Jinja2Templates(directory=TEMPLATES_DIR)

    @app.get("/health")
    async def health_check():
        tools = await mcp_server.list_tools()
        return {
            "status": "healthy",
            "service": "gridmind-unified",
            "version": "0.1.0",
            "mcp_version": "2.1.1",
            "active_scenario": app_service.active_scenario_id,
            "transports": ["streamable-http", "sse"],
            "endpoints": {
                "dashboard": "/",
                "streamable_http": "/mcp",
                "sse": "/sse",
                "messages": "/messages",
                "health": "/health",
            },
            "tools": [
                {
                    "name": t.name,
                    "description": t.description,
                    "read_only": t.annotations.read_only_hint if t.annotations else None,
                    "destructive": t.annotations.destructive_hint if t.annotations else None,
                }
                for t in tools
            ],
        }

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
        """
        Returns aggregated status: active scenario, grid telemetry, incident state,
        and latest audit record SCOPED TO THE ACTIVE SCENARIO.
        Uses efficient SQL queries to avoid materializing the entire audit table.
        """
        active_sc = app_service.active_scenario_id
        grid_state = app_service.get_grid_state()
        inc_state = app_service.get_incident_state()
        state_revision = app_service.get_state_revision()

        # Retrieve latest record for active scenario efficiently
        latest_record = app_audit_store.get_latest(scenario_id=active_sc)
        total_count = app_audit_store.count()

        return JSONResponse({
            "scenario_id": active_sc,
            "state_revision": state_revision,
            "grid_state": grid_state.to_dict(),
            "incident_state": inc_state.to_dict(),
            "latest_record": latest_record,
            "total_audit_records": total_count,
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
    async def list_audit_records(
        status: Optional[str] = Query(default=None),
        scenario_id: Optional[str] = Query(default=None),
        limit: int = Query(default=20, ge=1, le=100),
        offset: int = Query(default=0, ge=0),
    ):
        """Retrieves paginated AuditRecords from durable SQLite storage."""
        records = app_audit_store.list(
            status=status,
            scenario_id=scenario_id,
            limit=limit,
            offset=offset,
        )
        total = app_audit_store.count(status=status, scenario_id=scenario_id)
        return JSONResponse({
            "records": records,
            "count": len(records),
            "limit": limit,
            "offset": offset,
            "total": total,
        })

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

    @app.get("/api/diagnostics")
    async def get_diagnostics(
        user: AuthenticatedUser = Depends(require_role("viewer")),
    ):
        """
        Returns verified real system connectivity, MCP tools, and storage diagnostics.
        Protected: Requires authenticated operator session (viewer, operator, or operator_lead).
        Security: Does not expose server filesystem paths, secrets, or internal deployment directories.
        """
        tools = (await mcp_server.list_tools()) if mount_mcp else []
        active_sc = app_service.active_scenario_id
        latest_rec = app_audit_store.get_latest(scenario_id=active_sc)
        total_recs = app_audit_store.count()

        mcp_info = {
            "status": "online" if mount_mcp else "not_mounted",
            "transports": ["streamable-http", "sse"] if mount_mcp else [],
            "endpoints": {
                "streamable_http": "/mcp",
                "sse": "/sse",
                "messages": "/messages",
            } if mount_mcp else {},
            "tools_count": len(tools),
            "tools": [t.name for t in tools],
        }

        return JSONResponse({
            "status": "healthy",
            "service": "gridmind-unified",
            "operator": user.username,
            "role": user.role,
            "active_scenario": active_sc,
            "state_revision": app_service.get_state_revision(),
            "mcp": mcp_info,
            "commander": {
                "status": "ready",
                "shared_service": True,
                "llm_model": getattr(app_llm, "model", "default"),
                "is_degraded_mode": getattr(app_llm, "api_key", None) is None,
            },
            "audit_store": {
                "status": "connected",
                "storage_type": "sqlite_wal",
                "total_records": total_recs,
                "latest_incident_id": latest_rec["incident_id"] if latest_rec else None,
                "latest_status": latest_rec["status"] if latest_rec else "NOMINAL",
            },
        })


    # ==========================================================================
    # State-Changing Endpoints (Protected by Authentication & RBAC)
    # ==========================================================================

    @app.post("/api/scenario/load")
    async def load_scenario(
        req: ScenarioLoadRequest,
        user: AuthenticatedUser = Depends(require_role("operator")),
    ):
        """
        Loads and resets ONLY the requested scenario.
        Protected: Requires 'operator' or 'admin' role.
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
                "operator": user.username,
                "message": f"Loaded scenario '{app_service.active_scenario_id}'. Ready for investigation.",
            })
        except ValueError as err:
            raise HTTPException(status_code=400, detail=str(err))

    @app.post("/api/commander/plan")
    async def trigger_plan(
        user: AuthenticatedUser = Depends(require_role("operator")),
    ):
        """
        Triggers the Commander multi-specialist investigation and planning cycle.
        Protected: Requires 'operator' role.
        Dispatches synchronous planning work to a worker thread via asyncio.to_thread
        to avoid blocking the FastAPI event loop.
        """
        try:
            plan_result = await asyncio.to_thread(app_commander.plan_incident_response)
            return JSONResponse(plan_result.to_dict())
        except Exception as err:
            logger.exception("Error during commander planning: %s", err)
            raise HTTPException(status_code=500, detail=str(err))

    @app.post("/api/commander/approve")
    async def approve_action(
        req: ApprovalRequest,
        user: AuthenticatedUser = Depends(require_role("operator_lead")),
    ):
        """
        Human-in-the-Loop authorization gate.
        Protected: Requires 'operator_lead' or 'admin' role.
        Derives approved_by strictly from authenticated operator identity (prevents spoofing).
        Enforces scenario isolation: rejects cross-scenario execution attempts.
        Runs synchronous execution in worker thread via asyncio.to_thread.
        """
        active_sc = app_service.active_scenario_id
        target_id = req.incident_id

        if target_id:
            # Verify incident belongs to active scenario
            existing_rec = app_audit_store.get(target_id)
            if not existing_rec:
                raise HTTPException(status_code=404, detail=f"Incident '{target_id}' not found")
            if existing_rec.get("scenario_id") != active_sc:
                raise HTTPException(
                    status_code=400,
                    detail=(
                        f"Cannot approve incident '{target_id}' belonging to scenario '{existing_rec.get('scenario_id')}' "
                        f"while active scenario is '{active_sc}'."
                    ),
                )
        else:
            # Resolve pending record strictly for the active scenario
            pending_rec = app_audit_store.get_pending_for_scenario(active_sc)
            if not pending_rec:
                raise HTTPException(
                    status_code=400,
                    detail=f"No incident currently in PENDING_APPROVAL for active scenario '{active_sc}'."
                )
            target_id = pending_rec["incident_id"]

        try:
            approval_payload = {
                "approved": True,
                "approved_by": user.username,  # Derived strictly from authenticated identity
                "reason": req.reason or "Authorized by control room operator",
            }
            record = await asyncio.to_thread(
                app_commander.approve_and_execute,
                approval=approval_payload,
                incident_id=target_id,
            )
            return JSONResponse({
                "success": True,
                "record": record.to_dict(),
                "operator": user.username,
                "message": f"Incident '{target_id}' executed with final status: '{record.status}'",
            })
        except ValueError as err:
            raise HTTPException(status_code=400, detail=str(err))
        except Exception as err:
            logger.exception("Error during commander approval: %s", err)
            raise HTTPException(status_code=500, detail=str(err))

    @app.post("/api/commander/reject")
    async def reject_action(
        req: RejectionRequest,
        user: AuthenticatedUser = Depends(require_role("operator_lead")),
    ):
        """
        Human rejection pathway.
        Protected: Requires 'operator_lead' or 'admin' role.
        Derives approved_by strictly from authenticated operator identity.
        Enforces scenario isolation: rejects cross-scenario rejection attempts.
        """
        active_sc = app_service.active_scenario_id
        target_id = req.incident_id

        if target_id:
            existing_rec = app_audit_store.get(target_id)
            if not existing_rec:
                raise HTTPException(status_code=404, detail=f"Incident '{target_id}' not found")
            if existing_rec.get("scenario_id") != active_sc:
                raise HTTPException(
                    status_code=400,
                    detail=(
                        f"Cannot reject incident '{target_id}' belonging to scenario '{existing_rec.get('scenario_id')}' "
                        f"while active scenario is '{active_sc}'."
                    ),
                )
        else:
            pending_rec = app_audit_store.get_pending_for_scenario(active_sc)
            if not pending_rec:
                raise HTTPException(
                    status_code=400,
                    detail=f"No incident currently in PENDING_APPROVAL for active scenario '{active_sc}'."
                )
            target_id = pending_rec["incident_id"]

        try:
            approval_payload = {
                "approved": False,
                "approved_by": user.username,  # Derived strictly from authenticated identity
                "reason": req.reason or "Operator override / rejected",
            }
            record = await asyncio.to_thread(
                app_commander.approve_and_execute,
                approval=approval_payload,
                incident_id=target_id,
            )
            return JSONResponse({
                "success": True,
                "record": record.to_dict(),
                "operator": user.username,
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
    """CLI entrypoint for the GridMind Command Center & Unified MCP Server."""
    env_host = os.environ.get("HOST", "127.0.0.1")
    env_port = int(os.environ.get("PORT", os.environ.get("DASHBOARD_PORT", "8080")))

    parser = argparse.ArgumentParser(
        description="Run GridMind Command Center Dashboard & Unified MCP Server"
    )
    parser.add_argument("--host", type=str, default=env_host, help=f"Host to bind to (default: {env_host})")
    parser.add_argument("--port", type=int, default=env_port, help=f"Port to listen on (default: {env_port})")
    parser.add_argument("--data-dir", type=str, default="gridmind_data/curated", help="Path to curated grid data")
    parser.add_argument("--log-level", type=str, default="info", help="Logging level")
    args = parser.parse_args()

    print(f"Starting GridMind Unified Command Center & MCP Server on http://{args.host}:{args.port}")
    print(f"  - Web Dashboard:           http://{args.host}:{args.port}/")
    print(f"  - Streamable HTTP MCP:     http://{args.host}:{args.port}/mcp")
    print(f"  - SSE MCP Transport:       http://{args.host}:{args.port}/sse")
    print(f"  - Health / Tool Discovery: http://{args.host}:{args.port}/health")

    run_dashboard(
        host=args.host,
        port=args.port,
        data_dir=args.data_dir,
        log_level=args.log_level,
    )


if __name__ == "__main__":
    main()
