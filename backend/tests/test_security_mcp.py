from app.mcp.server import MCPServer


def test_mcp_does_not_expose_raw_sql_execution():
    server = MCPServer("space")
    assert "execute_query" not in [tool["name"] for tool in server.list_tools()]
    assert server.call_tool("execute_query", {"sql": "DELETE FROM users"})["error"] == "Unknown tool: execute_query"
