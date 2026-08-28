"""
HTTP Transport Server for GridMind Model Context Protocol (MCP) and Command Center Dashboard.

Exposes:
- Streamable HTTP (modern MCP 2.x standard) at endpoint: /mcp
- Server-Sent Events (SSE fallback) at endpoint: /sse and /messages
- Health check / metadata at endpoint: /health
- Interactive Web Command Center at endpoint: / (HTML) and /static/*
- REST & WebSocket telemetry API at endpoints: /api/* and /ws

Architecture:
    TrueForge / MCP Client (Streamable HTTP / SSE)
          ↓
    Command Center UI / Browser (REST / WebSockets)
          ↓
    Unified Starlette/FastAPI HTTP Server (http_server.py)
          ↓
    Incident Commander / MCPServer (mcp_server.py)
          ↓
    GridMindService (service.py)
          ↓
    GridMindEngine (engine.py)
"""

from __future__ import annotations

import argparse
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any, Optional
import uvicorn
from starlette.applications import Starlette
from starlette.middleware import Middleware
from starlette.middleware.cors import CORSMiddleware
from starlette.responses import FileResponse, JSONResponse
from starlette.routing import Mount, Route, WebSocketRoute
from starlette.staticfiles import StaticFiles

from gridmind.mcp_server import GridMindMCPServer
from gridmind.service import GridMindService
from agent.incident_manager import IncidentCommander
from dashboard.api import create_dashboard_app


def create_http_app(
    wrapper: Optional[GridMindMCPServer] = None,
    service: Optional[GridMindService] = None,
    commander: Optional[IncidentCommander] = None,
    data_dir: str = "gridmind_data/curated",
    streamable_path: str = "/mcp",
    sse_path: str = "/sse",
    message_path: str = "/messages",
    static_dir: Optional[str] = None,
) -> Starlette:
    """
    Creates and returns a Starlette ASGI application hosting Streamable HTTP,
    SSE MCP transports, REST APIs, WebSockets, and the Command Center UI,
    backed by a single shared GridMindService instance.
    """
    if wrapper is not None:
        mcp_wrapper = wrapper
        svc = wrapper.service
    else:
        svc = service or GridMindService(data_dir=data_dir)
        mcp_wrapper = GridMindMCPServer(service=svc, data_dir=data_dir)

    server = mcp_wrapper.server
    cmd = commander or IncidentCommander(svc)

    dashboard_app = create_dashboard_app(
        service=svc,
        commander=cmd,
        static_dir=static_dir,
    )

    streamable_app = server.streamable_http_app(streamable_http_path=streamable_path)
    sse_app = server.sse_app(sse_path=sse_path, message_path=message_path)

    # Static assets directory
    if static_dir is None:
        static_path = Path(__file__).resolve().parent.parent / "dashboard" / "static"
    else:
        static_path = Path(static_dir)

    @asynccontextmanager
    async def lifespan(app: Starlette):
        async with server.session_manager.run():
            yield

    async def health_endpoint(request: Any) -> JSONResponse:
        tools = await server.list_tools()
        return JSONResponse(
            {
                "status": "healthy",
                "service": "gridmind-mcp",
                "version": "0.1.0",
                "mcp_version": "2.1.1",
                "transports": ["streamable-http", "sse"],
                "endpoints": {
                    "streamable_http": streamable_path,
                    "sse": sse_path,
                    "messages": message_path,
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
        )

    async def root_endpoint(request: Any) -> Any:
        accept = request.headers.get("accept", "")
        index_file = static_path / "index.html"
        # If requested by a web browser, serve the Command Center UI
        if "text/html" in accept and index_file.is_file():
            return FileResponse(str(index_file))
        # Otherwise return health check metadata for MCP/REST callers
        return await health_endpoint(request)

    routes = [
        Route("/health", health_endpoint, methods=["GET"]),
        Route("/", root_endpoint, methods=["GET"]),
        *streamable_app.routes,
        *sse_app.routes,
    ]

    # Mount static assets if directory exists
    if static_path.is_dir():
        routes.append(
            Mount("/static", app=StaticFiles(directory=str(static_path)), name="static")
        )

    # Mount dashboard sub-app for /api and /ws
    routes.append(
        Mount("/api", app=dashboard_app)
    )

    middleware = [
        Middleware(
            CORSMiddleware,
            allow_origins=["*"],
            allow_credentials=True,
            allow_methods=["*"],
            allow_headers=["*"],
        )
    ]

    return Starlette(
        debug=False,
        routes=routes,
        middleware=middleware,
        lifespan=lifespan,
    )


def run_http_server(
    host: str = "127.0.0.1",
    port: int = 8000,
    data_dir: str = "gridmind_data/curated",
    log_level: str = "info",
) -> None:
    """Runs the GridMind MCP & Dashboard HTTP server synchronously via Uvicorn."""
    app = create_http_app(data_dir=data_dir)
    uvicorn.run(
        app,
        host=host,
        port=port,
        log_level=log_level,
    )


def main() -> None:
    """CLI entrypoint for running the GridMind HTTP server."""
    parser = argparse.ArgumentParser(
        description="Run GridMind Unified MCP & Command Center Server"
    )
    parser.add_argument(
        "--host",
        type=str,
        default="127.0.0.1",
        help="Host interface to bind to (default: 127.0.0.1)",
    )
    parser.add_argument(
        "--port",
        type=int,
        default=8000,
        help="Port to listen on (default: 8000)",
    )
    parser.add_argument(
        "--data-dir",
        type=str,
        default="gridmind_data/curated",
        help="Path to curated dataset directory (default: gridmind_data/curated)",
    )
    parser.add_argument(
        "--log-level",
        type=str,
        default="info",
        choices=["debug", "info", "warning", "error", "critical"],
        help="Logging level for Uvicorn (default: info)",
    )

    args = parser.parse_args()
    print(f"Starting GridMind Unified Command Center & MCP Server on http://{args.host}:{args.port}")
    print(f"  - Command Center UI:       http://{args.host}:{args.port}/")
    print(f"  - Streamable HTTP MCP:     http://{args.host}:{args.port}/mcp")
    print(f"  - SSE MCP endpoint:        http://{args.host}:{args.port}/sse")
    print(f"  - Health check:            http://{args.host}:{args.port}/health")

    run_http_server(
        host=args.host,
        port=args.port,
        data_dir=args.data_dir,
        log_level=args.log_level,
    )


if __name__ == "__main__":
    main()
