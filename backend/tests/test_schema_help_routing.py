from app.services.agent import AgentState, _route_with_rules, database_profile_responder, intent_router, route_intent, schema_help_responder


def test_schema_help_questions_route_without_llm():
    assert _route_with_rules("现在数据库有什么表") == "schema_help"
    assert _route_with_rules("这个空间能查什么数据") == "schema_help"
    assert _route_with_rules("帮我看看表结构") == "schema_help"


def test_database_profile_questions_route_without_llm():
    assert _route_with_rules("现在接入的数据库是什么") == "database_profile"
    assert _route_with_rules("这个库是干嘛的") == "database_profile"
    assert _route_with_rules("整体介绍一下每个表的作用") == "database_profile"
    assert route_intent(AgentState(route="database_profile")) == "database_profile"


def test_known_table_mentions_route_to_schema_help_without_llm():
    # "我要看api_logs" 匹配 table_view 模式 → table_query（不是 schema_help）
    state = AgentState(question="我要看api_logs（API 调用日志表）", space_id="tech_quality")
    result = intent_router(state)
    assert result["route"] == "table_query"


def test_known_table_bare_name_routes_to_schema_help():
    # 仅提表名不带查看动词 → schema_help
    state = AgentState(question="api_logs 是什么", space_id="tech_quality")
    result = intent_router(state)
    assert result["route"] == "schema_help"


def test_schema_help_responder_returns_data_map(monkeypatch):
    """schema_help_responder 现在返回 response_type=data_map 和结构化数据"""
    fake_data_map = {
        "space_id": "tech_quality", "mode": "preset",
        "summary": {"table_count": 3, "field_count": 24, "metric_count": 6},
        "tables": [], "metrics": [], "recommended_questions": [],
    }
    fake_db_identity = {
        "space_id": "tech_quality", "space_name": "质量保障",
        "is_preset": True, "table_count": 3,
        "connection": {"db_type": "mysql", "host_masked": "(系统预设)", "port": 3306, "db_name": "datacheck"},
    }

    monkeypatch.setattr("app.services.data_map_service.get_data_map", lambda sid: fake_data_map)
    monkeypatch.setattr("app.services.data_map_service.get_db_identity", lambda sid: fake_db_identity)
    state = AgentState(question="有什么表", space_id="tech_quality")

    result = schema_help_responder(state)

    assert result["response_type"] == "data_map"
    assert "3 张表" in result["message"]
    assert state.trace[-1]["node"] == "schema_help_responder"
    assert state.trace[-1]["output"]["data_map"] == fake_data_map
    assert state.trace[-1]["output"]["db_identity"] == fake_db_identity


def test_database_profile_responder_returns_database_file(monkeypatch):
    """数据库档案回复应该聚焦连接身份、表作用和可问问题，不返回完整 data_map 卡片"""
    from app.agents.answer_assembly import AssembledAnswer

    monkeypatch.setattr("app.agents.answer_assembly.assemble_answer", lambda *_args, **_kwargs: AssembledAnswer(
        kind="database_profile",
        message="当前连接到 MySQL 数据库 `datacheck`。\n\n主要数据表：\n- api_logs：记录 API 调用日志。",
        response_type="answer",
        source="inventory",
    ))
    state = AgentState(question="现在接入的数据库是什么", space_id="tech_quality")

    result = database_profile_responder(state)

    assert result["response_type"] == "answer"
    assert "datacheck" in result["message"]
    assert "api_logs" in result["message"]
    assert state.trace[-1]["node"] == "database_profile_responder"
