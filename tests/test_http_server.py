"""
Unit and integration tests for the GridMind Model Context Protocol (MCP) HTTP Server.

Tests:
1. HTTP Health / Smoke endpoint reachability (GET /health and GET /)
2. Tool discovery over Streamable HTTP (/mcp)
3. Read-only tools invocation over Streamable HTTP
4. Candidate action evaluation over Streamable HTTP (sandbox immutability)
5. Live action execution over Streamable HTTP (state mutation)
6. Action validation rejection over Streamable HTTP
7. Tool discovery and tool invocation over SSE transport (/sse)
8. Shared persistent state verification across HTTP requests
"""

import asyncio
import json
import socket
import threading
import time
import unittest
from typing import Any
import httpx2
import uvicorn
from mcp.client.session import ClientSession
from mcp.client.sse import sse_client
from mcp.client.streamable_http import streamable_http_client

from gridmind.http_server import create_http_app
from gridmind.mcp_server import GridMindMCPServer
from gridmind.service import GridMindService


def _get_free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


class TestMCPServerHTTP(unittest.IsolatedAsyncioTestCase):
    """Tests MCP HTTP server transports (Streamable HTTP, SSE, and Health check)."""

    server_thread: threading.Thread
    test_port: int
    base_url: str
    service: GridMindService
    wrapper: GridMindMCPServer
    uvicorn_server: uvicorn.Server

    @classmethod
    def setUpClass(cls) -> None:
        cls.test_port = _get_free_port()
        cls.base_url = f"http://127.0.0.1:{cls.test_port}"
        cls.service = GridMindService(data_dir="gridmind_data/curated")
        cls.wrapper = GridMindMCPServer(service=cls.service)
        cls.app = create_http_app(wrapper=cls.wrapper)

        config = uvicorn.Config(
            cls.app,
            host="127.0.0.1",
            port=cls.test_port,
            log_level="error",
        )
        cls.uvicorn_server = uvicorn.Server(config)

        def _run_server():
            cls.uvicorn_server.run()

        cls.server_thread = threading.Thread(target=_run_server, daemon=True)
        cls.server_thread.start()

        # Wait until server is reachable
        for _ in range(50):
            try:
                with httpx2.Client() as client:
                    resp = client.get(f"{cls.base_url}/health", timeout=1.0)
                    if resp.status_code == 200:
                        break
            except Exception:
                time.sleep(0.05)

    @classmethod
    def tearDownClass(cls) -> None:
        cls.uvicorn_server.should_exit = True

    async def test_01_http_health_smoke_test(self) -> None:
        """Tests that GET /health and GET / return 200 OK with server metadata and tool list."""
        async with httpx2.AsyncClient() as client:
            res = await client.get(f"{self.base_url}/health")
            self.assertEqual(res.status_code, 200)
            data = res.json()
            self.assertEqual(data["status"], "healthy")
            self.assertEqual(data["service"], "gridmind-mcp")
            self.assertIn("streamable-http", data["transports"])
            self.assertIn("sse", data["transports"])
            self.assertEqual(len(data["tools"]), 6)

            # Test root endpoint /
            res_root = await client.get(f"{self.base_url}/")
            self.assertEqual(res_root.status_code, 200)
            self.assertEqual(res_root.json()["status"], "healthy")

    async def test_02_streamable_http_tool_discovery(self) -> None:
        """Tests that all 6 tools are discoverable over Streamable HTTP (/mcp)."""
        async with streamable_http_client(f"{self.base_url}/mcp") as (read, write):
            async with ClientSession(read, write) as session:
                await session.initialize()
                tools_resp = await session.list_tools()
                tool_names = [t.name for t in tools_resp.tools]
                self.assertEqual(len(tool_names), 6)
                self.assertIn("get_grid_state", tool_names)
                self.assertIn("get_incident_state", tool_names)
                self.assertIn("evaluate_action", tool_names)
                self.assertIn("execute_action", tool_names)
                self.assertIn("get_last_simulation_result", tool_names)
                self.assertIn("load_scenario", tool_names)

    async def test_03_streamable_http_read_only_tools(self) -> None:
        """Tests invoking read-only tools over Streamable HTTP."""
        async with streamable_http_client(f"{self.base_url}/mcp") as (read, write):
            async with ClientSession(read, write) as session:
                await session.initialize()

                # 1. load_scenario SC01
                sc_res = await session.call_tool("load_scenario", {"scenario_id": "SC01"})
                self.assertFalse(sc_res.is_error)
                sc_data = json.loads(sc_res.content[0].text)
                self.assertFalse(sc_data["is_stable"])
                self.assertIn("T04", sc_data["overheated_transformers"])

                # 2. get_incident_state
                inc_res = await session.call_tool("get_incident_state", {})
                self.assertFalse(inc_res.is_error)
                inc_data = json.loads(inc_res.content[0].text)
                self.assertEqual(inc_data["scenario_id"], "SC01")
                self.assertIn("L08", inc_data["tripped_lines"])

                # 3. get_grid_state
                grid_res = await session.call_tool("get_grid_state", {})
                self.assertFalse(grid_res.is_error)
                grid_data = json.loads(grid_res.content[0].text)
                self.assertFalse(grid_data["is_stable"])
                self.assertEqual(len(grid_data["nodes"]), 10)

                # 4. get_last_simulation_result
                last_res = await session.call_tool("get_last_simulation_result", {})
                self.assertFalse(last_res.is_error)
                last_data = json.loads(last_res.content[0].text)
                self.assertFalse(last_data["is_stable"])

    async def test_04_streamable_http_evaluate_action_sandbox_isolation(self) -> None:
        """Tests sandbox evaluation over Streamable HTTP preserves live state immutability."""
        async with streamable_http_client(f"{self.base_url}/mcp") as (read, write):
            async with ClientSession(read, write) as session:
                await session.initialize()
                await session.call_tool("load_scenario", {"scenario_id": "SC01"})

                # Capture state before
                before_res = await session.call_tool("get_grid_state", {})
                before_data = json.loads(before_res.content[0].text)

                # Evaluate load restriction on N08
                eval_res = await session.call_tool(
                    "evaluate_action",
                    {
                        "action_type": "load_restriction",
                        "parameters": {"target": "N08", "reduction_pct": 15.0},
                    },
                )
                self.assertFalse(eval_res.is_error)
                eval_data = json.loads(eval_res.content[0].text)
                self.assertTrue(eval_data["action_valid"])
                self.assertTrue(eval_data["is_stable"])

                # Capture state after and assert immutability
                after_res = await session.call_tool("get_grid_state", {})
                after_data = json.loads(after_res.content[0].text)
                self.assertEqual(before_data["is_stable"], after_data["is_stable"])
                self.assertEqual(
                    before_data["active_violations"], after_data["active_violations"]
                )

    async def test_05_streamable_http_execute_action_live_mutation(self) -> None:
        """Tests live execution over Streamable HTTP mutates the shared grid state."""
        async with streamable_http_client(f"{self.base_url}/mcp") as (read, write):
            async with ClientSession(read, write) as session:
                await session.initialize()
                await session.call_tool("load_scenario", {"scenario_id": "SC01"})

                # Execute load restriction
                exec_res = await session.call_tool(
                    "execute_action",
                    {
                        "action_type": "load_restriction",
                        "parameters": {"target": "N08", "reduction_pct": 15.0},
                    },
                )
                self.assertFalse(exec_res.is_error)
                exec_data = json.loads(exec_res.content[0].text)
                self.assertTrue(exec_data["success"])
                self.assertTrue(exec_data["is_stable"])

                # Confirm live state is recovered to stable
                grid_res = await session.call_tool("get_grid_state", {})
                grid_data = json.loads(grid_res.content[0].text)
                self.assertTrue(grid_data["is_stable"])

    async def test_06_streamable_http_invalid_action_rejection(self) -> None:
        """Tests rejection of invalid/unknown actions over Streamable HTTP."""
        async with streamable_http_client(f"{self.base_url}/mcp") as (read, write):
            async with ClientSession(read, write) as session:
                await session.initialize()
                await session.call_tool("load_scenario", {"scenario_id": "SC01"})

                # Invalid transfer on tripped L08
                inv_res = await session.call_tool(
                    "evaluate_action",
                    {
                        "action_type": "load_transfer",
                        "parameters": {
                            "line_id": "L08",
                            "source": "N08",
                            "destination": "N04",
                            "transfer_mw": 0.100,
                        },
                    },
                )
                inv_data = json.loads(inv_res.content[0].text)
                self.assertFalse(inv_data["action_valid"])
                self.assertIn("tripped", inv_data["rejection_reason"].lower())

    async def test_07_sse_tool_discovery_and_call(self) -> None:
        """Tests that client can connect and call tools over SSE fallback transport (/sse)."""
        async with sse_client(f"{self.base_url}/sse") as (read, write):
            async with ClientSession(read, write) as session:
                await session.initialize()
                tools_resp = await session.list_tools()
                self.assertEqual(len(tools_resp.tools), 6)

                res = await session.call_tool("get_grid_state", {})
                self.assertFalse(res.is_error)
                data = json.loads(res.content[0].text)
                self.assertTrue(49.5 <= data["frequency_hz"] <= 50.5)


if __name__ == "__main__":
    unittest.main()
