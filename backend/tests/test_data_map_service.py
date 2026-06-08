from app.services import data_map_service


def test_preset_space_uses_system_schema_and_metric_bound_questions(monkeypatch):
    monkeypatch.setattr(data_map_service, "list_space_metric_configs", lambda space_id: [
        {
            "key": "scan_success_rate",
            "name": "扫描成功率",
            "description": "各类扫描的成功率统计",
            "config": {
                "allowed_query_types": ["fact", "trend", "breakdown"],
                "permitted_tables": ["scan_records"],
            },
        }
    ])

    data_map = data_map_service.get_data_map("tech_quality")

    assert data_map["mode"] == "preset"
    assert data_map["summary"]["table_count"] >= 1
    assert data_map["tables"][0]["name"] == "scan_records"
    assert data_map["tables"][0]["title"] == "扫描记录表"
    question = data_map["recommended_questions"][0]
    assert question["metric"] == "scan_success_rate"
    assert question["query_type"] == "trend"
    assert question["source"] == "metric"


def test_user_space_uses_stored_schema_and_infers_columns(monkeypatch):
    monkeypatch.setattr(data_map_service, "get_space", lambda space_id: {
        "id": space_id,
        "name": "自建空间",
        "dataset_id": "user",
        "db_schema": {
            "orders": {
                "comment": "",
                "columns": [
                    {"name": "id", "type": "bigint", "comment": ""},
                    {"name": "created_at", "type": "datetime", "comment": ""},
                    {"name": "status", "type": "varchar(32)", "comment": ""},
                    {"name": "amount", "type": "decimal(10,2)", "comment": ""},
                ],
            }
        },
    })
    monkeypatch.setattr(data_map_service, "list_space_metric_configs", lambda space_id: [])

    data_map = data_map_service.get_data_map("custom_space")

    assert data_map["mode"] == "schema"
    table = data_map["tables"][0]
    assert table["name"] == "orders"
    assert table["time_columns"] == ["created_at"]
    assert "amount" in table["measure_columns"]
    assert "status" in table["dimension_columns"]
    assert data_map["recommended_questions"][0]["table"] == "orders"
    assert data_map["recommended_questions"][0]["source"] == "schema"


def test_schema_help_answer_mentions_tables_and_examples(monkeypatch):
    monkeypatch.setattr(data_map_service, "get_data_map", lambda space_id: {
        "space_id": space_id,
        "mode": "preset",
        "summary": {"table_count": 1, "field_count": 4, "metric_count": 1},
        "tables": [
            {
                "name": "scan_records",
                "title": "扫描记录表",
                "description": "记录扫描任务结果",
                "key_columns": ["created_at", "status", "error_type"],
            }
        ],
        "recommended_questions": [
            {"text": "最近7天扫描成功率趋势", "metric": "scan_success_rate", "query_type": "trend"}
        ],
    })

    answer = data_map_service.render_schema_help("tech_quality")

    assert "发现 1 张表" in answer
    assert "scan_records" in answer
    assert "最近7天扫描成功率趋势" in answer


def test_schema_help_can_answer_single_table_question(monkeypatch):
    monkeypatch.setattr(data_map_service, "get_data_map", lambda space_id: {
        "space_id": space_id,
        "mode": "preset",
        "summary": {"table_count": 2, "field_count": 8, "metric_count": 1},
        "tables": [
            {
                "name": "scan_records",
                "title": "扫描记录表",
                "description": "记录每次扫描任务的类型、结果、错误原因、设备和耗时。",
                "columns": [
                    {"name": "created_at", "type": "datetime", "comment": "创建时间"},
                    {"name": "scan_type", "type": "enum", "comment": "扫描类型"},
                    {"name": "status", "type": "enum", "comment": "扫描结果"},
                    {"name": "error_type", "type": "varchar", "comment": "错误类型"},
                ],
                "key_columns": ["created_at", "scan_type", "status", "error_type"],
            },
            {
                "name": "api_logs",
                "title": "API 调用日志表",
                "description": "记录 API 调用情况。",
                "columns": [],
                "key_columns": [],
            },
        ],
        "recommended_questions": [
            {"text": "最近7天扫描成功率趋势", "metric": "scan_success_rate", "query_type": "trend"},
            {"text": "最近7天API响应时间趋势", "metric": "api_response_time", "query_type": "trend"},
        ],
    })

    answer = data_map_service.render_schema_help("tech_quality", "扫描记录表记录了什么内容")

    assert "扫描记录表主要记录" in answer
    assert "扫描类型" in answer
    assert "扫描结果" in answer
    assert "api_logs" not in answer
    assert "当前空间发现" not in answer
