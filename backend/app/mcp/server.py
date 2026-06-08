"""MCP Server — 暴露 tools/list 和 tools/call 接口

每个 MCP Server 实例绑定一个数据库连接，通过 engine_registry 获取底层引擎。
遵循 MCP 协议标准：tool name + input schema + 执行结果。
"""

from __future__ import annotations

import json

from sqlalchemy import text

from app.core.engine_registry import engine_registry
from app.core.database import engine as system_engine
from app.mcp.tools import TOOLS, get_tool_schema


class MCPServer:
    """MCP Server — 绑定到特定空间的数据库连接"""

    def __init__(self, space_id: str) -> None:
        self.space_id = space_id

    def _get_engine(self):
        if self.space_id:
            return engine_registry.get_engine(self.space_id)
        return system_engine

    def list_tools(self) -> list[dict]:
        """MCP tools/list — 返回所有可用工具定义"""
        return TOOLS

    def call_tool(self, tool_name: str, arguments: dict) -> dict:
        """MCP tools/call — 执行指定工具并返回结果"""
        schema = get_tool_schema(tool_name)
        if not schema:
            return {"error": f"Unknown tool: {tool_name}"}

        handler = {
            "execute_query": self._execute_query,
            "list_tables": self._list_tables,
            "describe_table": self._describe_table,
            "get_schema": self._get_schema,
        }.get(tool_name)

        if not handler:
            return {"error": f"No handler for tool: {tool_name}"}

        try:
            return handler(**arguments)
        except Exception as e:
            return {"error": str(e)}

    def _execute_query(self, sql: str, params: dict | None = None) -> dict:
        eng = self._get_engine()
        with eng.connect() as conn:
            result = conn.execute(text(sql), params or {})
            columns = list(result.keys())
            rows = []
            for row in result.fetchall():
                row_dict = {}
                for col, val in zip(columns, row):
                    row_dict[col] = self._serialize(val)
                rows.append(row_dict)
        return {"columns": columns, "rows": rows, "row_count": len(rows)}

    def _list_tables(self) -> dict:
        eng = self._get_engine()
        with eng.connect() as conn:
            result = conn.execute(text("SHOW TABLES"))
            tables = [row[0] for row in result.fetchall()]
        return {"tables": tables}

    def _describe_table(self, table_name: str) -> dict:
        eng = self._get_engine()
        with eng.connect() as conn:
            result = conn.execute(text(f"DESCRIBE {table_name}"))
            columns = []
            for row in result.fetchall():
                columns.append({
                    "field": row[0],
                    "type": row[1],
                    "null": row[2],
                    "key": row[3],
                    "default": str(row[4]) if row[4] else None,
                    "extra": row[5],
                })
        return {"table": table_name, "columns": columns}

    def _get_schema(self) -> dict:
        eng = self._get_engine()
        with eng.connect() as conn:
            tables_result = conn.execute(text("SHOW TABLES"))
            tables = [row[0] for row in tables_result.fetchall()]
            schema = {}
            for table_name in tables:
                desc_result = conn.execute(text(f"DESCRIBE {table_name}"))
                columns = []
                for row in desc_result.fetchall():
                    columns.append({"field": row[0], "type": row[1], "key": row[3]})
                schema[table_name] = columns
        return {"schema": schema}

    @staticmethod
    def _serialize(val):
        from decimal import Decimal
        from datetime import datetime, date
        if isinstance(val, Decimal):
            return float(val)
        if isinstance(val, datetime):
            return val.strftime("%Y-%m-%d %H:%M:%S")
        if isinstance(val, date):
            return val.strftime("%Y-%m-%d")
        return val
