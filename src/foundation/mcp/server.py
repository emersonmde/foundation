"""MCP stdio server — reads JSON-RPC from stdin, dispatches to tool handlers.

This module runs as a subprocess spawned by claude -p via --mcp-config.
It communicates with the Foundation daemon via a Unix domain socket (IPC bridge)
for operations that require daemon state (messaging, sub-agents, wait).

Environment variables (set by the parent daemon):
    FOUNDATION_DB_PATH: Path to the SQLite database
    FOUNDATION_IPC_SOCKET: Path to the daemon's IPC bridge socket
    FOUNDATION_MEMORY_DIR: Path to the memory/knowledge directory
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import sys
from pathlib import Path
from typing import Any

import aiosqlite

from foundation.db.connection import connect
from foundation.mcp.bridge import BridgeClient
from foundation.mcp.protocol import (
    METHOD_NOT_FOUND,
    JsonRpcRequest,
    JsonRpcResponse,
    error_response,
)
from foundation.mcp.tools import (
    TOOL_DEFINITIONS,
    handle_cancel_agent,
    handle_create_task,
    handle_query_tasks,
    handle_read_memory,
    handle_request_human_input,
    handle_send_message,
    handle_spawn_agent,
    handle_update_memory,
    handle_update_task,
    handle_wait,
)

logger = logging.getLogger("foundation.mcp.server")

# MCP protocol version
PROTOCOL_VERSION = "2024-11-05"


class McpServer:
    """MCP server that reads from stdin and writes to stdout."""

    def __init__(
        self,
        db: aiosqlite.Connection,
        bridge: BridgeClient,
        memory_dir: Path,
    ) -> None:
        self._db = db
        self._bridge = bridge
        self._memory_dir = memory_dir

    async def handle_request(self, request: JsonRpcRequest) -> JsonRpcResponse:
        """Dispatch a JSON-RPC request to the appropriate handler."""
        method = request.method

        if method == "initialize":
            return self._handle_initialize(request)
        elif method == "notifications/initialized":
            # Client notification, no response needed
            return JsonRpcResponse(id=request.id, result={})
        elif method == "tools/list":
            return self._handle_tools_list(request)
        elif method == "tools/call":
            return await self._handle_tools_call(request)
        else:
            return error_response(request.id, METHOD_NOT_FOUND, f"Unknown method: {method}")

    def _handle_initialize(self, request: JsonRpcRequest) -> JsonRpcResponse:
        """Handle the MCP initialize handshake."""
        return JsonRpcResponse(
            id=request.id,
            result={
                "protocolVersion": PROTOCOL_VERSION,
                "capabilities": {
                    "tools": {"listChanged": False},
                },
                "serverInfo": {
                    "name": "foundation",
                    "version": "0.4.0",
                },
            },
        )

    def _handle_tools_list(self, request: JsonRpcRequest) -> JsonRpcResponse:
        """Return available tool definitions."""
        return JsonRpcResponse(
            id=request.id,
            result={"tools": [t.to_dict() for t in TOOL_DEFINITIONS]},
        )

    async def _handle_tools_call(self, request: JsonRpcRequest) -> JsonRpcResponse:
        """Dispatch a tool call to the appropriate handler."""
        tool_name = request.params.get("name", "")
        arguments = request.params.get("arguments", {})

        try:
            result = await self._dispatch_tool(tool_name, arguments)
            return JsonRpcResponse(
                id=request.id,
                result={
                    "content": [{"type": "text", "text": json.dumps(result)}],
                },
            )
        except Exception as e:
            logger.exception("Tool call error: %s", tool_name)
            return JsonRpcResponse(
                id=request.id,
                result={
                    "content": [{"type": "text", "text": json.dumps({"error": str(e)})}],
                    "isError": True,
                },
            )

    async def _dispatch_tool(self, tool_name: str, params: dict[str, Any]) -> dict[str, Any]:
        """Route a tool call to its handler."""
        # DB-backed tools
        if tool_name == "create_task":
            return await handle_create_task(self._db, params)
        elif tool_name == "update_task":
            return await handle_update_task(self._db, params)
        elif tool_name == "query_tasks":
            return await handle_query_tasks(self._db, params)
        # Bridge-backed tools (messaging, agents, wait)
        elif tool_name == "send_message":
            return await handle_send_message(self._bridge, params)
        elif tool_name == "request_human_input":
            return await handle_request_human_input(self._bridge, params)
        elif tool_name == "spawn_agent":
            return await handle_spawn_agent(self._bridge, params)
        elif tool_name == "cancel_agent":
            return await handle_cancel_agent(self._bridge, params)
        # File-backed tools (memory)
        elif tool_name == "read_memory":
            return await handle_read_memory(self._memory_dir, params)
        elif tool_name == "update_memory":
            return await handle_update_memory(self._memory_dir, params)
        # Bridge-backed (wait)
        elif tool_name == "wait":
            return await handle_wait(self._bridge, params)
        else:
            return {"error": f"Unknown tool: {tool_name}"}


async def run_server() -> None:
    """Main entry point: run the MCP server on stdin/stdout."""
    db_path = os.environ.get("FOUNDATION_DB_PATH", "")
    ipc_socket = os.environ.get("FOUNDATION_IPC_SOCKET", "")
    memory_dir = Path(os.environ.get("FOUNDATION_MEMORY_DIR", "memory"))

    if not db_path:
        print("Error: FOUNDATION_DB_PATH not set", file=sys.stderr)
        sys.exit(1)

    db = await connect(Path(db_path))
    bridge = BridgeClient(ipc_socket)

    if ipc_socket:
        try:
            await bridge.connect()
        except Exception as e:
            print(f"Warning: Could not connect to IPC bridge: {e}", file=sys.stderr)

    server = McpServer(db, bridge, memory_dir)

    # Read from stdin, write to stdout (newline-delimited JSON)
    reader = asyncio.StreamReader()
    protocol = asyncio.StreamReaderProtocol(reader)
    await asyncio.get_event_loop().connect_read_pipe(lambda: protocol, sys.stdin)

    while True:
        line = await reader.readline()
        if not line:
            break

        line_str = line.decode("utf-8").strip()
        if not line_str:
            continue

        try:
            raw = json.loads(line_str)
        except json.JSONDecodeError:
            continue

        request = JsonRpcRequest.from_dict(raw)
        response = await server.handle_request(request)

        # Only send response if the request had an ID (not a notification)
        if request.id is not None:
            output = json.dumps(response.to_dict()) + "\n"
            sys.stdout.write(output)
            sys.stdout.flush()

    await bridge.close()
    await db.close()


def main() -> None:
    """Synchronous entry point."""
    asyncio.run(run_server())


if __name__ == "__main__":
    main()
