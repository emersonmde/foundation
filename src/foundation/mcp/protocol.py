"""JSON-RPC message types for the MCP stdio transport."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass
class JsonRpcRequest:
    """An incoming JSON-RPC 2.0 request."""

    method: str
    id: int | str | None = None
    params: dict[str, Any] = field(default_factory=dict)

    @classmethod
    def from_dict(cls, raw: dict[str, Any]) -> JsonRpcRequest:
        return cls(
            method=raw.get("method", ""),
            id=raw.get("id"),
            params=raw.get("params", {}),
        )


@dataclass
class JsonRpcResponse:
    """An outgoing JSON-RPC 2.0 response."""

    id: int | str | None
    result: Any = None
    error: dict[str, Any] | None = None

    def to_dict(self) -> dict[str, Any]:
        resp: dict[str, Any] = {"jsonrpc": "2.0", "id": self.id}
        if self.error is not None:
            resp["error"] = self.error
        else:
            resp["result"] = self.result
        return resp


@dataclass
class ToolDefinition:
    """MCP tool definition returned by tools/list."""

    name: str
    description: str
    input_schema: dict[str, Any]

    def to_dict(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "description": self.description,
            "inputSchema": self.input_schema,
        }


def error_response(
    req_id: int | str | None,
    code: int,
    message: str,
) -> JsonRpcResponse:
    """Build a JSON-RPC error response."""
    return JsonRpcResponse(id=req_id, error={"code": code, "message": message})


# Standard JSON-RPC error codes
PARSE_ERROR = -32700
INVALID_REQUEST = -32600
METHOD_NOT_FOUND = -32601
INVALID_PARAMS = -32602
INTERNAL_ERROR = -32603
