"""IPC bridge for communication between MCP server and Foundation daemon.

The bridge has two sides:
- BridgeServer: runs inside the daemon, dispatches incoming requests
- BridgeClient: runs inside the MCP server subprocess, sends requests

Communication is over a Unix domain socket using newline-delimited JSON.
"""

from __future__ import annotations

import asyncio
import contextlib
import json
import logging
import uuid
from collections.abc import Awaitable, Callable
from pathlib import Path
from typing import Any

logger = logging.getLogger("foundation.mcp.bridge")

# Type for bridge method handlers
BridgeHandler = Callable[[dict[str, Any]], Awaitable[dict[str, Any]]]


class BridgeServer:
    """Unix socket server that runs inside the Foundation daemon.

    Accepts JSON-RPC-style requests from the MCP server process and dispatches
    to registered handler methods.
    """

    def __init__(self, socket_path: str | Path) -> None:
        self.socket_path = Path(socket_path)
        self._handlers: dict[str, BridgeHandler] = {}
        self._server: asyncio.Server | None = None

    def register(self, method: str, handler: BridgeHandler) -> None:
        """Register a handler for a bridge method."""
        self._handlers[method] = handler

    async def start(self) -> None:
        """Start listening on the Unix domain socket."""
        # Remove stale socket file
        self.socket_path.unlink(missing_ok=True)
        self.socket_path.parent.mkdir(parents=True, exist_ok=True)
        self._server = await asyncio.start_unix_server(
            self._handle_connection, path=str(self.socket_path)
        )
        logger.info("Bridge server listening on %s", self.socket_path)

    async def stop(self) -> None:
        """Stop the server and clean up."""
        if self._server:
            self._server.close()
            await self._server.wait_closed()
        self.socket_path.unlink(missing_ok=True)
        logger.info("Bridge server stopped")

    async def _handle_connection(
        self,
        reader: asyncio.StreamReader,
        writer: asyncio.StreamWriter,
    ) -> None:
        """Handle a single connection from the MCP server."""
        try:
            while True:
                line = await reader.readline()
                if not line:
                    break
                try:
                    request = json.loads(line.decode("utf-8"))
                except json.JSONDecodeError:
                    response: dict[str, Any] = {"error": "invalid JSON"}
                    writer.write(json.dumps(response).encode("utf-8") + b"\n")
                    await writer.drain()
                    continue

                req_id = request.get("id", "")
                method = request.get("method", "")
                params = request.get("params", {})

                handler = self._handlers.get(method)
                if handler is None:
                    response = {
                        "id": req_id,
                        "error": f"unknown method: {method}",
                    }
                else:
                    try:
                        result = await handler(params)
                        response = {"id": req_id, "result": result}
                    except Exception as e:
                        logger.exception("Bridge handler error for %s", method)
                        response = {"id": req_id, "error": str(e)}

                writer.write(json.dumps(response).encode("utf-8") + b"\n")
                await writer.drain()
        except asyncio.CancelledError:
            pass
        except Exception:
            logger.exception("Bridge connection error")
        finally:
            writer.close()
            with contextlib.suppress(Exception):
                await writer.wait_closed()


class BridgeClient:
    """Unix socket client that runs inside the MCP server process.

    Sends requests to the daemon's BridgeServer and waits for responses.
    """

    def __init__(self, socket_path: str | Path) -> None:
        self.socket_path = Path(socket_path)
        self._reader: asyncio.StreamReader | None = None
        self._writer: asyncio.StreamWriter | None = None
        self._lock = asyncio.Lock()

    async def connect(self) -> None:
        """Connect to the daemon's bridge socket."""
        self._reader, self._writer = await asyncio.open_unix_connection(str(self.socket_path))
        logger.info("Bridge client connected to %s", self.socket_path)

    async def close(self) -> None:
        """Close the connection."""
        if self._writer:
            self._writer.close()
            with contextlib.suppress(Exception):
                await self._writer.wait_closed()
            self._reader = None
            self._writer = None

    async def call(self, method: str, params: dict[str, Any]) -> dict[str, Any]:
        """Send a request and wait for the response."""
        if self._writer is None or self._reader is None:
            raise RuntimeError("Bridge client not connected")

        req_id = str(uuid.uuid4())[:8]
        request = {"id": req_id, "method": method, "params": params}

        async with self._lock:
            self._writer.write(json.dumps(request).encode("utf-8") + b"\n")
            await self._writer.drain()

            line = await self._reader.readline()
            if not line:
                raise RuntimeError("Bridge connection closed")

            response = json.loads(line.decode("utf-8"))

        if "error" in response and response["error"] is not None:
            raise RuntimeError(f"Bridge error: {response['error']}")

        result: dict[str, Any] = response.get("result", {})
        return result
