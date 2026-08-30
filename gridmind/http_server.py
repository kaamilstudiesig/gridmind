"""
HTTP Transport Server for GridMind Model Context Protocol (MCP).

Exposes seven deterministic grid simulation and planning tools to TrueForge and
remote MCP connectors over HTTP:
- Streamable HTTP (modern MCP 2.x standard) at endpoint: /mcp
- Server-Sent Events (SSE fallback) at endpoint: /sse and /messages
- Health check / metadata at endpoint: /health and /

Architecture:
    TrueForge / MCP Client (Streamable HTTP / SSE)
          ↓
    HTTP Starlette ASGI App
          ↓
    MCPServer (mcp_server.py)
          ↓
    GridMindService (service.py)
          ↓
    GridMindEngine (engine.py)
"""

from __future__ import annotations

import argparse
from contextlib import asynccontextmanager
import os
from typing import Any, Optional
import uvicorn
from starlette.applications import Starlette
from starlette.responses import JSONResponse
from starlette.routing import Route

from gridmind.audit_store import AuditStore
from gridmind.commander import GridMindCommander
from gridmind.mcp_server import GridMindMCPServer
from gridmind.service import GridMindService


def create_http_app(
    wrapper: Optional[GridMindMCPServer] = None,
    service: Optional[GridMindService] = None,
    commander: Optional[GridMindCommander] = None,
    audit_store: Optional[AuditStore] = None,
    data_dir: str = "gridmind_data/curated",
    streamable_path: str = "/mcp",
    sse_path: str = "/sse",
    message_path: str = "/messages",
) -> Starlette:
    """
    Creates and returns a Starlette ASGI application that hosts both Streamable HTTP
    and SSE transports for GridMind MCP, using a single shared GridMindService instance.
    """
    mcp_wrapper = wrapper or GridMindMCPServer(
        service=service,
        commander=commander,
        audit_store=audit_store,
        data_dir=data_dir,
    )
    server = mcp_wrapper.server

    streamable_app = server.streamable_http_app(streamable_http_path=streamable_path)
    sse_app = server.sse_app(sse_path=sse_path, message_path=message_path)

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

    routes = [
        Route("/health", health_endpoint, methods=["GET"]),
        Route("/", health_endpoint, methods=["GET"]),
        *streamable_app.routes,
        *sse_app.routes,
    ]

    return Starlette(
        debug=False,
        routes=routes,
        lifespan=lifespan,
    )


def run_http_server(
    host: str = "127.0.0.1",
    port: int = 8000,
    data_dir: str = "gridmind_data/curated",
    log_level: str = "info",
) -> None:
    """Runs the GridMind MCP HTTP server synchronously via Uvicorn."""
    app = create_http_app(data_dir=data_dir)
    uvicorn.run(
        app,
        host=host,
        port=port,
        log_level=log_level,
    )


def main() -> None:
    """CLI entrypoint for running the GridMind MCP HTTP server."""
    env_host = os.environ.get("HOST", "127.0.0.1")
    env_port = int(os.environ.get("PORT", os.environ.get("MCP_PORT", "8000")))

    parser = argparse.ArgumentParser(
        description="Run GridMind MCP Server over HTTP (Streamable HTTP & SSE for TrueForge)"
    )
    parser.add_argument(
        "--host",
        type=str,
        default=env_host,
        help=f"Host interface to bind to (default: {env_host})",
    )
    parser.add_argument(
        "--port",
        type=int,
        default=env_port,
        help=f"Port to listen on (default: {env_port})",
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
    print(f"Starting GridMind MCP HTTP Server on http://{args.host}:{args.port}")
    print(f"  - Streamable HTTP endpoint: http://{args.host}:{args.port}/mcp")
    print(f"  - SSE endpoint:            http://{args.host}:{args.port}/sse")
    print(f"  - Health check:            http://{args.host}:{args.port}/health")

    run_http_server(
        host=args.host,
        port=args.port,
        data_dir=args.data_dir,
        log_level=args.log_level,
    )


if __name__ == "__main__":
    main()
