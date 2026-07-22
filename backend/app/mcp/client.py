"""MCP Client — Agent 通过 MCP 协议调用数据库工具

封装 MCP tools/list + tools/call 调用，供 Agent 节点使用。
"""

from __future__ import annotations

from app.mcp.server import MCPServer
from app.mcp.tools import get_tool_names


class MCPClient:
    """MCP Client — 绑定到特定空间，代理所有 MCP 调用"""

    def __init__(self, space_id: str) -> None:
        self._server = MCPServer(space_id)
        self.space_id = space_id

    def list_tools(self) -> list[dict]:
        """MCP tools/list"""
        return self._server.list_tools()

    def call_tool(self, tool_name: str, arguments: dict | None = None) -> dict:
        """MCP tools/call"""
        return self._server.call_tool(tool_name, arguments or {})

    def list_tables(self) -> list[str]:
        result = self.call_tool("list_tables")
        return result.get("tables", [])

    def describe_table(self, table_name: str) -> dict:
        return self.call_tool("describe_table", {"table_name": table_name})

    def get_schema(self) -> dict:
        return self.call_tool("get_schema")

    def has_tool(self, tool_name: str) -> bool:
        return tool_name in get_tool_names()
