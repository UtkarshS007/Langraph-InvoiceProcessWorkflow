# src/mcp/client.py
"""
MCP client abstraction.

Initial Implementation - Plan A (simplest, no external servers required):
- LocalMCPClient routes tool calls to in-process registries in common_server/atlas_server.

Later Implementation (Plan B):
- Swap to MultiServerMCPClient (stdio/http) without changing runner logic much.
"""

from __future__ import annotations

from typing import Any, Dict, Literal

from src.mcp import common_server, atlas_server

ServerName = Literal["COMMON", "ATLAS"]


class LocalMCPClient:
    """
    Minimal "MCP-like" client: call tools by (server, tool_name, payload).
    """

    def call(self, server: ServerName, tool: str, payload: Dict[str, Any]) -> Dict[str, Any]:
        if server == "COMMON":
            return common_server.call_tool(tool, payload)
        if server == "ATLAS":
            return atlas_server.call_tool(tool, payload)
        raise ValueError(f"Unknown server: {server}")
