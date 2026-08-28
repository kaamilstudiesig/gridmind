"""
FastAPI Dashboard Backend API for GridMind Command Center.

Provides REST and WebSocket endpoints for live telemetry, multi-agent operations,
human approval checkpoints, simulation plan comparisons, and audit log streaming.
"""

from __future__ import annotations

import asyncio
import json
import logging
from pathlib import Path
from typing import Any, Optional

from fastapi import FastAPI, HTTPException, WebSocket, WebSocketDisconnect
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from gridmind.service import GridMindService
from agent.incident_manager import IncidentCommander
from agent.models import IncidentRecord, IncidentState

logger = logging.getLogger("gridmind.dashboard.api")


# Request models
class LoadScenarioRequest(BaseModel):
    scenario_id: str = Field(default="SC01", description="Scenario ID to load (e.g. 'SC01')")


class ApproveActionRequest(BaseModel):
    plan_id: Optional[str] = Field(default=None, description="Optional specific plan ID to approve")


class RejectActionRequest(BaseModel):
    reason: str = Field(default="Operator rejected proposed recommendation", description="Reason for rejection")


class ActionEvaluationRequest(BaseModel):
    action_type: str
    parameters: dict[str, Any] = Field(default_factory=dict)


class ActionExecutionRequest(BaseModel):
    action_type: str
    parameters: dict[str, Any] = Field(default_factory=dict)


class ConnectionManager:
    """Manages active WebSocket connections for live broadcasting."""

    def __init__(self) -> None:
        self.active_connections: list[WebSocket] = []

    async def connect(self, websocket: WebSocket) -> None:
        await websocket.accept()
        self.active_connections.append(websocket)

    def disconnect(self, websocket: WebSocket) -> None:
        if websocket in self.active_connections:
            self.active_connections.remove(websocket)

    async def broadcast(self, message: dict[str, Any]) -> None:
        text = json.dumps(message)
        dead = []
        for conn in self.active_connections:
            try:
                await conn.send_text(text)
            except Exception:
                dead.append(conn)
        for conn in dead:
            self.disconnect(conn)


def create_dashboard_app(
    service: Optional[GridMindService] = None,
    commander: Optional[IncidentCommander] = None,
    static_dir: Optional[str] = None,
) -> FastAPI:
    """Factory creating the FastAPI dashboard application."""
    svc = service or GridMindService()
    cmd = commander or IncidentCommander(svc)
    ws_manager = ConnectionManager()

    app = FastAPI(
        title="GridMind Command Center API",
        description="Electrical Grid Incident Commander and Telemetry REST API",
        version="0.1.0",
    )

    app.add_middleware(
        CORSMiddleware,
        allow_origins=["*"],
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )

    # Attach state references to app
    app.state.service = svc
    app.state.commander = cmd
    app.state.ws_manager = ws_manager

    # Resolve static assets folder
    if static_dir is None:
        base = Path(__file__).resolve().parent / "static"
    else:
        base = Path(static_dir)

    # -------------------------------------------------------------
    # REST Endpoints
    # -------------------------------------------------------------

    @app.get("/api/status")
    async def get_system_status() -> dict[str, Any]:
        """Returns overall system operational health and active incident state."""
        inc = cmd.current_incident
        grid = svc.get_grid_state()
        return {
            "status": "OPERATIONAL",
            "active_scenario": svc.active_scenario_id,
            "grid_stable": grid.is_stable,
            "active_incident_id": inc.incident_id if inc else None,
            "incident_state": inc.state.value if inc else "IDLE",
            "frequency_hz": grid.frequency_hz,
            "total_demand_kw": grid.total_demand_kw,
            "violations_count": len(grid.active_violations),
        }

    @app.get("/api/grid-state")
    async def get_grid_state() -> dict[str, Any]:
        """Returns live grid topology nodes, lines, transformers, and telemetry."""
        return svc.get_grid_state().to_dict()

    @app.get("/api/incident")
    async def get_incident() -> dict[str, Any]:
        """Returns the current incident record with full agent reasoning and plan comparison."""
        inc = cmd.current_incident
        if not inc:
            # If no incident initialized, initialize one
            inc = cmd.start_incident(svc.active_scenario_id or "SC01")
        return inc.to_dict()

    @app.post("/api/scenario/load")
    async def load_scenario(req: LoadScenarioRequest) -> dict[str, Any]:
        """Loads a scenario and initializes a fresh incident lifecycle."""
        try:
            inc = cmd.start_incident(req.scenario_id)
            await ws_manager.broadcast({
                "type": "SCENARIO_LOADED",
                "scenario_id": req.scenario_id,
                "incident": inc.to_dict(),
            })
            return {
                "success": True,
                "scenario_id": req.scenario_id,
                "incident": inc.to_dict(),
            }
        except ValueError as err:
            raise HTTPException(status_code=400, detail=str(err))

    @app.post("/api/incident/investigate")
    async def investigate_incident() -> dict[str, Any]:
        """Triggers the autonomous multi-agent investigation pipeline."""
        inc = cmd.investigate()
        await ws_manager.broadcast({
            "type": "INVESTIGATION_COMPLETED",
            "incident": inc.to_dict(),
        })
        return inc.to_dict()

    @app.post("/api/incident/approve")
    async def approve_action(req: ApproveActionRequest) -> dict[str, Any]:
        """Approves and executes the proposed action with verification."""
        try:
            inc = cmd.approve_action(req.plan_id)
            await ws_manager.broadcast({
                "type": "ACTION_APPROVED_AND_EXECUTED",
                "incident": inc.to_dict(),
            })
            return inc.to_dict()
        except ValueError as err:
            raise HTTPException(status_code=400, detail=str(err))

    @app.post("/api/incident/reject")
    async def reject_action(req: RejectActionRequest) -> dict[str, Any]:
        """Rejects the recommended action and triggers replanning."""
        try:
            inc = cmd.reject_action(req.reason)
            await ws_manager.broadcast({
                "type": "ACTION_REJECTED_REPLANNED",
                "incident": inc.to_dict(),
            })
            return inc.to_dict()
        except ValueError as err:
            raise HTTPException(status_code=400, detail=str(err))

    @app.get("/api/plans")
    async def get_plans() -> list[dict[str, Any]]:
        """Returns evaluated candidate plans and scoring breakdown."""
        inc = cmd.current_incident
        if not inc or not inc.candidate_plans:
            return []
        return [p.to_dict() for p in inc.candidate_plans]

    @app.get("/api/timeline")
    async def get_timeline() -> list[dict[str, Any]]:
        """Returns the full audit log timeline."""
        inc = cmd.current_incident
        if not inc:
            return []
        return [e.to_dict() for e in inc.timeline]

    # -------------------------------------------------------------
    # WebSocket Streaming
    # -------------------------------------------------------------

    @app.websocket("/ws")
    async def websocket_endpoint(websocket: WebSocket) -> None:
        """Real-time event streaming for telemetry, agent events, and approvals."""
        await ws_manager.connect(websocket)
        try:
            # Send initial state
            inc = cmd.current_incident
            grid = svc.get_grid_state()
            await websocket.send_text(json.dumps({
                "type": "INITIAL_STATE",
                "grid_state": grid.to_dict(),
                "incident": inc.to_dict() if inc else None,
            }))
            while True:
                data = await websocket.receive_text()
                # Process incoming commands from UI
                try:
                    payload = json.loads(data)
                    action = payload.get("action")
                    if action == "investigate":
                        res = cmd.investigate()
                        await ws_manager.broadcast({"type": "INVESTIGATION_COMPLETED", "incident": res.to_dict()})
                    elif action == "approve":
                        plan_id = payload.get("plan_id")
                        res = cmd.approve_action(plan_id)
                        await ws_manager.broadcast({"type": "ACTION_APPROVED_AND_EXECUTED", "incident": res.to_dict()})
                    elif action == "reject":
                        reason = payload.get("reason", "Operator rejected recommendation")
                        res = cmd.reject_action(reason)
                        await ws_manager.broadcast({"type": "ACTION_REJECTED_REPLANNED", "incident": res.to_dict()})
                    elif action == "load_scenario":
                        sc_id = payload.get("scenario_id", "SC01")
                        res = cmd.start_incident(sc_id)
                        await ws_manager.broadcast({"type": "SCENARIO_LOADED", "scenario_id": sc_id, "incident": res.to_dict()})
                except Exception as ex:
                    logger.error("Error processing websocket message: %s", ex)
        except WebSocketDisconnect:
            ws_manager.disconnect(websocket)

    # -------------------------------------------------------------
    # Static Assets & Frontend Routing
    # -------------------------------------------------------------

    if base.is_dir():
        app.mount("/static", StaticFiles(directory=str(base)), name="static")

        @app.get("/")
        async def index_view() -> FileResponse:
            index_file = base / "index.html"
            if index_file.is_file():
                return FileResponse(str(index_file))
            return JSONResponse({"message": "GridMind API is running. index.html not found."})

    return app
