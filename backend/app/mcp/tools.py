"""MCP Tool 定义 — 标准 JSON Schema"""

TOOLS = [
    {
        "name": "list_tables",
        "description": "列出用户数据库中的所有表",
        "inputSchema": {
            "type": "object",
            "properties": {},
        },
    },
    {
        "name": "describe_table",
        "description": "获取指定表的列信息（列名、类型、注释）",
        "inputSchema": {
            "type": "object",
            "properties": {
                "table_name": {
                    "type": "string",
                    "description": "表名",
                },
            },
            "required": ["table_name"],
        },
    },
    {
        "name": "get_schema",
        "description": "获取用户数据库的完整表结构（所有表 + 所有列）",
        "inputSchema": {
            "type": "object",
            "properties": {},
        },
    },
]


def get_tool_names() -> list[str]:
    return [t["name"] for t in TOOLS]


def get_tool_schema(name: str) -> dict | None:
    for t in TOOLS:
        if t["name"] == name:
            return t
    return None
