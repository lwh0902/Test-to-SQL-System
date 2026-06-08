"""测试图结构包含新增节点和路由"""

from app.services.agent import build_graph, route_intent, AgentState


def test_graph_has_diagnosis_handler_node():
    graph = build_graph()
    assert "diagnosis_handler" in graph.nodes


def test_graph_has_table_query_handler_node():
    graph = build_graph()
    assert "table_query_handler" in graph.nodes


def test_graph_has_persister_node():
    graph = build_graph()
    assert "persister" in graph.nodes


def test_route_intent_includes_all_routes():
    """route_intent 能处理所有路由"""
    expected = {
        "chat": "chat",
        "help": "help",
        "schema_help": "schema_help",
        "follow_up": "follow_up",
        "plan_execute": "plan_execute",
        "diagnosis": "diagnosis",
        "table_query": "table_query",
        "data_query": "query",
    }
    for route, expected_result in expected.items():
        state = AgentState(route=route)
        result = route_intent(state)
        assert result == expected_result, f"Route '{route}': expected '{expected_result}', got '{result}'"


def test_graph_builds_without_error():
    """图能正常构建"""
    graph = build_graph()
    assert graph is not None
