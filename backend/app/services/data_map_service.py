"""Data map service - turns space schema and metrics into a user-facing map."""

from __future__ import annotations

from app.services.space_service import get_space, list_space_metric_configs
from app.core.database import engine
from app.core.engine_registry import engine_registry
from sqlalchemy import text


SYSTEM_SCHEMAS: dict[str, dict[str, dict]] = {
    "tech_quality": {
        "scan_records": {
            "title": "扫描记录表",
            "description": "记录每次扫描任务的类型、结果、错误原因、设备和耗时。",
            "columns": [
                {"name": "id", "type": "bigint", "comment": "主键"},
                {"name": "user_id", "type": "bigint", "comment": "关联用户"},
                {"name": "scan_type", "type": "enum", "comment": "扫描类型"},
                {"name": "status", "type": "enum", "comment": "扫描结果"},
                {"name": "error_type", "type": "varchar", "comment": "错误类型"},
                {"name": "device_type", "type": "enum", "comment": "设备类型"},
                {"name": "duration_ms", "type": "int", "comment": "耗时（毫秒）"},
                {"name": "created_at", "type": "datetime", "comment": "创建时间"},
            ],
        },
        "feature_events": {
            "title": "特征事件表",
            "description": "记录功能事件、功能名称、成功状态和错误码。",
            "columns": [
                {"name": "id", "type": "bigint", "comment": "主键"},
                {"name": "event_type", "type": "varchar", "comment": "事件类型"},
                {"name": "feature_name", "type": "varchar", "comment": "功能名称"},
                {"name": "is_success", "type": "tinyint", "comment": "是否成功"},
                {"name": "error_code", "type": "varchar", "comment": "错误码"},
                {"name": "created_at", "type": "datetime", "comment": "创建时间"},
            ],
        },
        "api_logs": {
            "title": "API 调用日志表",
            "description": "记录 API 名称、状态码、响应时间和异常信息。",
            "columns": [
                {"name": "id", "type": "bigint", "comment": "主键"},
                {"name": "api_name", "type": "varchar", "comment": "API 名称"},
                {"name": "status_code", "type": "int", "comment": "HTTP 状态码"},
                {"name": "response_time_ms", "type": "int", "comment": "响应时间（毫秒）"},
                {"name": "is_error", "type": "tinyint", "comment": "是否异常"},
                {"name": "created_at", "type": "datetime", "comment": "创建时间"},
            ],
        },
    },
    "ecommerce": {
        "ecom_users": {
            "title": "电商用户表",
            "description": "记录用户画像、注册渠道和首次下单时间。",
            "columns": [
                {"name": "id", "type": "bigint", "comment": "主键"},
                {"name": "nickname", "type": "varchar", "comment": "昵称"},
                {"name": "register_channel", "type": "varchar", "comment": "注册渠道"},
                {"name": "first_order_at", "type": "datetime", "comment": "首次下单时间"},
                {"name": "created_at", "type": "datetime", "comment": "创建时间"},
            ],
        },
        "ecom_products": {
            "title": "商品表",
            "description": "记录商品名称、分类、品牌、售价和上下架状态。",
            "columns": [
                {"name": "id", "type": "bigint", "comment": "主键"},
                {"name": "name", "type": "varchar", "comment": "商品名称"},
                {"name": "category", "type": "varchar", "comment": "分类"},
                {"name": "brand", "type": "varchar", "comment": "品牌"},
                {"name": "price", "type": "decimal", "comment": "售价"},
                {"name": "status", "type": "enum", "comment": "状态"},
                {"name": "created_at", "type": "datetime", "comment": "创建时间"},
            ],
        },
        "ecom_orders": {
            "title": "订单表",
            "description": "记录订单金额、支付状态、支付渠道和流量来源。",
            "columns": [
                {"name": "id", "type": "bigint", "comment": "主键"},
                {"name": "order_no", "type": "varchar", "comment": "订单号"},
                {"name": "user_id", "type": "bigint", "comment": "用户 ID"},
                {"name": "status", "type": "enum", "comment": "订单状态"},
                {"name": "pay_amount", "type": "decimal", "comment": "实付金额"},
                {"name": "source_channel", "type": "varchar", "comment": "流量渠道"},
                {"name": "created_at", "type": "datetime", "comment": "创建时间"},
            ],
        },
        "ecom_order_items": {
            "title": "订单明细表",
            "description": "记录订单中的商品、分类、单价、数量和小计。",
            "columns": [
                {"name": "id", "type": "bigint", "comment": "主键"},
                {"name": "order_id", "type": "bigint", "comment": "订单 ID"},
                {"name": "product_id", "type": "bigint", "comment": "商品 ID"},
                {"name": "category", "type": "varchar", "comment": "分类快照"},
                {"name": "quantity", "type": "int", "comment": "数量"},
                {"name": "subtotal", "type": "decimal", "comment": "小计"},
                {"name": "created_at", "type": "datetime", "comment": "创建时间"},
            ],
        },
        "ecom_traffic_events": {
            "title": "流量事件表",
            "description": "记录浏览、加购、结算和支付点击等流量行为。",
            "columns": [
                {"name": "id", "type": "bigint", "comment": "主键"},
                {"name": "event_type", "type": "enum", "comment": "事件类型"},
                {"name": "source_channel", "type": "varchar", "comment": "流量渠道"},
                {"name": "device_type", "type": "enum", "comment": "设备类型"},
                {"name": "created_at", "type": "datetime", "comment": "创建时间"},
            ],
        },
        "ecom_refunds": {
            "title": "退款表",
            "description": "记录退款金额、退款原因和处理状态。",
            "columns": [
                {"name": "id", "type": "bigint", "comment": "主键"},
                {"name": "order_id", "type": "bigint", "comment": "订单 ID"},
                {"name": "product_id", "type": "bigint", "comment": "商品 ID"},
                {"name": "refund_amount", "type": "decimal", "comment": "退款金额"},
                {"name": "reason", "type": "varchar", "comment": "退款原因"},
                {"name": "status", "type": "enum", "comment": "状态"},
                {"name": "created_at", "type": "datetime", "comment": "创建时间"},
            ],
        },
    },
}

TABLE_TITLE_FALLBACKS = {
    "orders": "订单表",
    "order_items": "订单明细表",
    "users": "用户表",
    "products": "商品表",
    "events": "事件表",
    "logs": "日志表",
}


def get_db_identity(space_id: str) -> dict | None:
    """返回空间的数据库身份信息（脱敏后）"""
    with engine.connect() as conn:
        result = conn.execute(text(
            "SELECT sp.name, sp.description, sp.connection_id, sp.db_schema, "
            "       dc.db_type, dc.host, dc.port, dc.db_name, dc.last_tested_at "
            "FROM analysis_spaces sp "
            "LEFT JOIN db_connections dc ON sp.connection_id = dc.id AND dc.status = 'active' "
            "WHERE sp.id = :space_id"
        ), {"space_id": space_id})
        row = result.fetchone()
    if not row:
        return None

    db_schema = row[3]
    schema = db_schema if isinstance(db_schema, dict) else (__import__("json").loads(db_schema) if db_schema else {})
    is_preset = space_id in SYSTEM_SCHEMAS
    if is_preset:
        schema = SYSTEM_SCHEMAS.get(space_id, schema)
    table_count = len(schema) if schema else 0

    identity: dict = {
        "space_id": space_id,
        "space_name": row[0],
        "space_description": row[1] or "",
        "is_preset": is_preset,
        "table_count": table_count,
    }

    if row[2]:  # connection_id — 用户自建连接
        identity["connection"] = {
            "db_type": row[4] or "mysql",
            "host_masked": _mask_host(row[5]),
            "port": row[6],
            "db_name": row[7],
            "last_tested_at": str(row[8]) if row[8] else None,
        }
    elif is_preset:
        identity["connection"] = {
            "db_type": "mysql",
            "host_masked": "(系统预设)",
            "port": 3306,
            "db_name": "datacheck",
            "last_tested_at": None,
        }

    return identity


def _mask_host(host: str | None) -> str:
    """脱敏 host：保留首尾，中间用 *** 替代"""
    if not host:
        return "***"
    if len(host) <= 6:
        return host[:2] + "***"
    return host[:3] + "***" + host[-2:]


SENSITIVE_FIELDS = {"phone", "mobile", "email", "id_card", "password", "secret", "token", "credit_card"}


def safe_sample_table(space_id: str, table_name: str, limit: int = 3) -> list[dict]:
    """安全采样表数据：过滤敏感字段后返回样例行"""
    from app.core.database import engine as default_engine

    if not _is_safe_identifier(table_name):
        return []

    eng = engine_registry.get_engine(space_id) if space_id else default_engine

    # 1. 获取表的列名，过滤敏感字段
    with eng.connect() as conn:
        cols_result = conn.execute(text(
            "SELECT COLUMN_NAME FROM INFORMATION_SCHEMA.COLUMNS "
            "WHERE TABLE_SCHEMA = DATABASE() AND TABLE_NAME = :tbl ORDER BY ORDINAL_POSITION"
        ), {"tbl": table_name})
        all_columns = [r[0] for r in cols_result.fetchall()]

    safe_columns = [c for c in all_columns if c.lower() not in SENSITIVE_FIELDS]
    if not safe_columns:
        return []

    # 2. 采样数据
    cols_str = ", ".join(f"`{c}`" for c in safe_columns)
    with eng.connect() as conn:
        result = conn.execute(text(f"SELECT {cols_str} FROM `{table_name}` LIMIT :lim"), {"lim": limit})
        rows = [dict(zip(safe_columns, r)) for r in result.fetchall()]

    return rows


def _is_safe_identifier(identifier: str | None) -> bool:
    """只允许普通 MySQL identifier，避免动态表名拼接被污染。"""
    if not identifier:
        return False
    return identifier.replace("_", "").isalnum() and not identifier[0].isdigit()


def profile_tables(space_id: str) -> dict:
    """安全采样 + LLM 生成表描述，结果缓存到 db_schema JSON"""
    import json
    import os

    space = get_space(space_id)
    if not space:
        return {"profiled": 0, "error": "space not found"}

    schema = space.get("db_schema") or {}
    if not schema:
        return {"profiled": 0, "error": "no schema"}

    # 系统预设空间已有描述，跳过
    if space_id in SYSTEM_SCHEMAS:
        return {"profiled": 0, "skipped": "preset"}

    profiled = 0
    for table_name, table_info in schema.items():
        if not isinstance(table_info, dict):
            continue
        # 已有描述就跳过
        if table_info.get("description") and table_info.get("_profiled"):
            continue

        # 安全采样
        samples = []
        try:
            samples = safe_sample_table(space_id, table_name, limit=3)
        except Exception:
            pass

        # 构建列摘要
        columns = table_info.get("columns", [])
        col_summary = []
        for c in columns[:20]:
            line = f"- {c.get('name', '')} ({c.get('type', '')})"
            if c.get("comment"):
                line += f": {c['comment']}"
            col_summary.append(line)

        # LLM 生成描述
        description = _llm_profile_table(table_name, "\n".join(col_summary), samples)
        if description:
            table_info["description"] = description
            table_info["_profiled"] = True
            profiled += 1

        # 保存样例数据（脱敏后）
        if samples:
            table_info["sample_data"] = samples

    # 写回 db_schema
    if profiled > 0:
        with engine.connect() as conn:
            conn.execute(text(
                "UPDATE analysis_spaces SET db_schema = :schema WHERE id = :id"
            ), {"schema": json.dumps(schema, ensure_ascii=False, default=str), "id": space_id})
            conn.commit()

    return {"profiled": profiled}


def _llm_profile_table(table_name: str, col_summary: str, samples: list[dict]) -> str:
    """调用 LLM 为单张表生成中文描述"""
    import os
    try:
        from anthropic import Anthropic
    except ImportError:
        return ""

    sample_text = ""
    if samples:
        lines = []
        for row in samples[:2]:
            lines.append("  " + ", ".join(f"{k}={v}" for k, v in row.items()))
        sample_text = "\n样例数据：\n" + "\n".join(lines)

    prompt = (
        f"表名：{table_name}\n"
        f"字段：\n{col_summary}\n"
        f"{sample_text}\n\n"
        f"请用一句中文（不超过 40 字）描述这张表的用途。只输出描述，不要其他内容。"
    )

    try:
        client = Anthropic(
            api_key=os.getenv("LLM_API_KEY"),
            base_url=os.getenv("LLM_BASE_URL", "https://api.deepseek.com/anthropic"),
        )
        response = client.messages.create(
            model=os.getenv("LLM_MODEL", "deepseek-v4-flash"),
            max_tokens=100,
            messages=[{"role": "user", "content": prompt}],
        )
        for block in response.content:
            if hasattr(block, "text"):
                return block.text.strip()
    except Exception:
        pass
    return ""


def get_data_map(space_id: str) -> dict:
    """返回空间的结构化数据地图"""
    metrics = list_space_metric_configs(space_id)
    schema = SYSTEM_SCHEMAS.get(space_id)
    mode = "preset" if schema else "schema"
    if schema is None:
        space = get_space(space_id) or {}
        schema = _normalize_user_schema(space.get("db_schema") or {})

    tables = [_build_table_entry(name, info) for name, info in schema.items()]
    metric_entries = [_build_metric_entry(metric) for metric in metrics]
    questions = _metric_questions(metric_entries)
    if not questions:
        questions = _schema_questions(tables)

    return {
        "space_id": space_id,
        "mode": mode,
        "summary": {
            "table_count": len(tables),
            "field_count": sum(len(table["columns"]) for table in tables),
            "metric_count": len(metric_entries),
        },
        "tables": tables,
        "metrics": metric_entries,
        "recommended_questions": questions[:8],
    }


def render_database_profile(space_id: str) -> str:
    """生成适合小白阅读的数据库档案说明。"""
    data_map = get_data_map(space_id)
    identity = get_db_identity(space_id) or {}
    summary = data_map["summary"]
    connection = identity.get("connection") or {}

    if connection:
        conn_line = (
            f"当前接入的是 {connection.get('db_type', 'mysql').upper()} 数据库 "
            f"`{connection.get('db_name', '未知库')}`（{connection.get('host_masked', '***')}:{connection.get('port', '-') }）。"
        )
    else:
        conn_line = f"当前空间是 `{space_id}`，暂未读取到外部数据库连接信息。"

    preset_line = "这是系统预设空间。" if identity.get("is_preset") else "这是用户自建数据库空间。"
    lines = [
        conn_line,
        f"{preset_line}我识别到 {summary['table_count']} 张表、{summary['field_count']} 个字段、{summary['metric_count']} 个可直接分析的指标。",
        "",
        "主要数据表：",
    ]

    for table in data_map["tables"][:10]:
        key_cols = "、".join(table.get("key_columns", [])[:4]) or "暂无关键字段"
        lines.append(f"- {table['title']}（{table['name']}）：{table['description']} 关键字段：{key_cols}")

    questions = data_map.get("recommended_questions") or []
    if questions:
        lines.extend(["", "你可以直接这样问："])
        lines.extend(f"- {q['text']}" for q in questions[:6])

    return "\n".join(lines)


def render_schema_help(space_id: str, question: str = "") -> str:
    data_map = get_data_map(space_id)
    matched_table = _find_table_in_question(data_map["tables"], question)
    if matched_table:
        return _render_table_help(matched_table, data_map.get("recommended_questions") or [])

    summary = data_map["summary"]
    lines = [
        f"当前空间发现 {summary['table_count']} 张表、{summary['field_count']} 个字段、{summary['metric_count']} 个推荐指标。",
        "",
        "主要数据表：",
    ]
    for table in data_map["tables"][:6]:
        keys = "、".join(table["key_columns"][:4])
        lines.append(f"- {table['name']}（{table['title']}）：{table['description']} 关键字段：{keys}")

    questions = data_map.get("recommended_questions") or []
    if questions:
        lines.extend(["", "你可以直接这样问："])
        lines.extend(f"- {q['text']}" for q in questions[:6])
    return "\n".join(lines)


def get_table_aliases(space_id: str) -> list[str]:
    schema = SYSTEM_SCHEMAS.get(space_id)
    if schema is None:
        space = get_space(space_id) or {}
        schema = _normalize_user_schema(space.get("db_schema") or {})

    aliases: list[str] = []
    for table_name, table_info in schema.items():
        title = table_info.get("title") or table_info.get("comment") or ""
        aliases.extend([table_name, title, title.replace("表", "")])
    return [alias.lower() for alias in aliases if alias]


def _find_table_in_question(tables: list[dict], question: str) -> dict | None:
    if not question:
        return None
    normalized_question = question.lower()
    for table in tables:
        candidates = {
            table.get("name", "").lower(),
            table.get("title", "").lower(),
            table.get("title", "").replace("表", "").lower(),
        }
        if any(candidate and candidate in normalized_question for candidate in candidates):
            return table
    return None


def _render_table_help(table: dict, questions: list[dict]) -> str:
    title = table["title"]
    lines = [
        f"{title}主要记录：{table['description']}",
        "",
    ]
    columns = table.get("columns") or []
    if columns:
        lines.append("它包含这些关键信息：")
        for column in columns[:8]:
            label = column.get("comment") or column.get("name")
            lines.append(f"- {label}（{column.get('name')}）")

    matched_questions = [
        q for q in questions
        if table["name"] in (q.get("table") or "") or table["name"] in (q.get("text") or "") or q.get("source") == "metric"
    ][:4]
    if matched_questions:
        lines.extend(["", "你可以直接这样问："])
        lines.extend(f"- {q['text']}" for q in matched_questions)
    return "\n".join(lines)


def _normalize_user_schema(schema: dict) -> dict:
    normalized = {}
    for table_name, table_info in schema.items():
        if isinstance(table_info, list):
            columns = table_info
            comment = ""
        else:
            columns = table_info.get("columns", [])
            comment = table_info.get("comment", "") or table_info.get("title", "")
        normalized[table_name] = {
            "title": _guess_table_title(table_name, comment),
            "description": comment or f"包含 {len(columns)} 个字段，可用于明细查询和基础统计。",
            "columns": columns,
        }
    return normalized


def _build_table_entry(table_name: str, table_info: dict) -> dict:
    columns = [_normalize_column(col) for col in table_info.get("columns", [])]
    time_columns = [c["name"] for c in columns if _is_time_column(c)]
    measure_columns = [c["name"] for c in columns if _is_measure_column(c)]
    dimension_columns = [c["name"] for c in columns if _is_dimension_column(c)]
    key_columns = _dedupe(time_columns + dimension_columns + measure_columns)[:6]

    return {
        "name": table_name,
        "title": table_info.get("title") or _guess_table_title(table_name, ""),
        "description": table_info.get("description") or table_info.get("comment") or "数据表",
        "columns": columns,
        "key_columns": key_columns,
        "time_columns": time_columns,
        "measure_columns": measure_columns,
        "dimension_columns": dimension_columns,
    }


def _build_metric_entry(metric: dict) -> dict:
    config = metric.get("config", {})
    query_types = config.get("allowed_query_types", ["fact"])
    return {
        "key": metric["key"],
        "name": metric["name"],
        "description": metric.get("description", ""),
        "query_types": query_types,
        "tables": config.get("permitted_tables", []),
        "recommended_questions": _questions_for_metric(metric["key"], metric["name"], query_types),
    }


def _questions_for_metric(metric_key: str, metric_name: str, query_types: list[str]) -> list[dict]:
    questions = []
    if "trend" in query_types:
        questions.append({"text": f"最近7天{metric_name}趋势", "metric": metric_key, "query_type": "trend", "source": "metric"})
    if "breakdown" in query_types:
        questions.append({"text": f"按维度拆解{metric_name}", "metric": metric_key, "query_type": "breakdown", "source": "metric"})
    if "fact" in query_types:
        questions.append({"text": f"最近7天{metric_name}是多少", "metric": metric_key, "query_type": "fact", "source": "metric"})
    return questions


def _metric_questions(metrics: list[dict]) -> list[dict]:
    questions = []
    for metric in metrics:
        questions.extend(metric["recommended_questions"])
    return questions


def _schema_questions(tables: list[dict]) -> list[dict]:
    questions = []
    for table in tables:
        if table["time_columns"]:
            questions.append({"text": f"按天统计 {table['name']} 的记录数", "table": table["name"], "intent": "trend_count", "source": "schema"})
        questions.append({"text": f"查看 {table['name']} 最近10条数据", "table": table["name"], "intent": "recent_rows", "source": "schema"})
    return questions


def _normalize_column(column: dict) -> dict:
    return {
        "name": column.get("name", ""),
        "type": str(column.get("type", "")).lower(),
        "comment": column.get("comment", "") or "",
    }


def _is_time_column(column: dict) -> bool:
    name = column["name"].lower()
    col_type = column["type"].lower()
    return "time" in name or name.endswith("_at") or "date" in name or "datetime" in col_type or "timestamp" in col_type


def _is_measure_column(column: dict) -> bool:
    name = column["name"].lower()
    col_type = column["type"].lower()
    if name == "id" or name.endswith("_id"):
        return False
    return any(token in name for token in ("amount", "price", "count", "duration", "total", "quantity", "gmv")) or any(
        token in col_type for token in ("int", "decimal", "float", "double")
    )


def _is_dimension_column(column: dict) -> bool:
    name = column["name"].lower()
    if _is_time_column(column) or _is_measure_column(column):
        return False
    return any(token in name for token in ("status", "type", "category", "channel", "source", "brand", "reason", "code", "name"))


def _guess_table_title(table_name: str, comment: str) -> str:
    if comment:
        return comment
    compact = table_name.removeprefix("ecom_")
    for token, title in TABLE_TITLE_FALLBACKS.items():
        if token in compact:
            return title
    return table_name


def _dedupe(items: list[str]) -> list[str]:
    seen = set()
    result = []
    for item in items:
        if item and item not in seen:
            seen.add(item)
            result.append(item)
    return result
