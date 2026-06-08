"""MCP Tool 定义 — 标准 JSON Schema"""

TOOLS = [
    {
        "name": "execute_query",
        "description": "在用户数据库上执行只读 SQL 查询，返回列名和行数据",
        "inputSchema": {
            "type": "object",
            "properties": {
                "sql": {
                    "type": "string",
                    "description": "SELECT SQL 语句，必须包含 LIMIT",
                },
                "params": {
                    "type": "object",
                    "description": "SQL 参数绑定（可选）",
                    "additionalProperties": True,
                },
            },
            "required": ["sql"],
        },
    },
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
