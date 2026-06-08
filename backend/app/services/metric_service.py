"""指标服务 - 从数据库按空间加载指标配置，渲染 SQL 模板"""

import json
from datetime import datetime, timedelta

from sqlalchemy import text
from jinja2 import Template

from app.core.database import engine
from app.models.schemas import QueryIntent


def load_metrics(space_id: str) -> dict:
    """从数据库加载指定空间的全部指标配置"""
    with engine.connect() as conn:
        result = conn.execute(text(
            "SELECT metric_key, config FROM metric_templates WHERE space_id = :space_id AND status = 'active'"
        ), {"space_id": space_id})
        metrics = {}
        for row in result.fetchall():
            config = json.loads(row[1]) if isinstance(row[1], str) else row[1]
            metrics[row[0]] = config
    return metrics


def get_metric_config(space_id: str, metric_name: str) -> dict | None:
    """获取指定空间内的单个指标配置"""
    with engine.connect() as conn:
        result = conn.execute(text(
            "SELECT config FROM metric_templates WHERE space_id = :space_id AND metric_key = :key AND status = 'active'"
        ), {"space_id": space_id, "key": metric_name})
        row = result.fetchone()
        if not row:
            return None
        return json.loads(row[0]) if isinstance(row[0], str) else row[0]


def list_metrics(space_id: str) -> list[dict]:
    """列出指定空间的所有指标"""
    with engine.connect() as conn:
        result = conn.execute(text(
            "SELECT metric_key, name, description, config FROM metric_templates WHERE space_id = :space_id AND status = 'active' ORDER BY sort_order"
        ), {"space_id": space_id})
        metrics = []
        for row in result.fetchall():
            config = json.loads(row[3]) if isinstance(row[3], str) else row[3]
            allowed_types = config.get("allowed_query_types", ["fact"])
            metrics.append({
                "key": row[0],
                "name": row[1],
                "description": row[2],
                "default_query_type": allowed_types[0] if allowed_types else "fact",
                "default_time_range": "last_7_days",
            })
    return metrics


def resolve_dimension(intent: QueryIntent, metric_config: dict) -> str:
    """根据 intent 和 metric 配置决定最终维度"""
    allowed = metric_config.get("allowed_dimensions", [])
    default = metric_config.get("default_dimension", "date")

    if intent.dimensions:
        for dim in intent.dimensions:
            if dim in allowed:
                return dim
    return default


def render_sql(intent: QueryIntent, metric_config: dict, workspace_id: str = "default") -> tuple[str, dict]:
    """渲染 SQL 模板，返回 (sql, params)

    支持单表和多表（JOIN）模板：
    - 单表：permitted_tables 只有一项，模板用 {{ table }}
    - 多表：metric_config 中定义 table_aliases，模板用 {{ orders }}/{{ items }} 等别名
    - comparison: 查两个时间段，返回 (sql, params)，params 包含对比期参数
    """
    query_type = intent.query_type or "fact"
    templates = metric_config["sql_templates"]

    # comparison: 有专用模板用专用模板，否则用 trend
    effective_type = query_type
    if query_type == "comparison":
        effective_type = "comparison" if "comparison" in templates else ("trend" if "trend" in templates else "fact")

    if effective_type not in templates:
        effective_type = "fact"

    template_str = templates[effective_type]
    dimension = resolve_dimension(intent, metric_config)
    permitted_tables = metric_config["permitted_tables"]
    table = permitted_tables[0]

    # 表别名映射（用于 JOIN 模板）
    table_aliases = metric_config.get("table_aliases", {})

    # 决定 GROUP BY 子句
    if dimension == "date":
        main_table = table_aliases.get("main", table)
        group_by = f"DATE({main_table}.created_at)"
    else:
        group_by = f"{table}.{dimension}"

    # 渲染模板
    template = Template(template_str)
    render_ctx = {
        "table": table,
        "dimension": dimension,
        "group_by": group_by,
        "filters": intent.filters or {},
    }
    # 注入表别名
    for alias, real_name in table_aliases.items():
        if alias != "main":
            render_ctx[alias] = real_name
        else:
            render_ctx["table"] = real_name

    sql = template.render(**render_ctx)

    # 构建参数绑定
    now = datetime.now()
    if intent.time_range:
        start_time = intent.time_range.start
        end_time = intent.time_range.end
    else:
        start_time = (now - timedelta(days=7)).strftime("%Y-%m-%d")
        end_time = now.strftime("%Y-%m-%d")

    params: dict = {
        "start_time": start_time,
        "end_time": end_time,
        "workspace_id": workspace_id,
        "limit": 1000,
    }

    # comparison 时增加对比期参数
    if query_type == "comparison" and intent.time_range:
        try:
            start_dt = datetime.strptime(start_time, "%Y-%m-%d")
            end_dt = datetime.strptime(end_time, "%Y-%m-%d")
            delta = (end_dt - start_dt).days
            # 对比期 = 前推同样长度
            prev_start = (start_dt - timedelta(days=delta)).strftime("%Y-%m-%d")
            prev_end = start_time
            params["prev_start_time"] = prev_start
            params["prev_end_time"] = prev_end
            # 实际查询范围扩大到两个周期
            params["start_time"] = prev_start
        except ValueError:
            pass

    # 添加过滤条件参数
    if intent.filters:
        for key, val in intent.filters.items():
            params[key] = val

    # 清理未使用的参数
    clean_params = {}
    for key, val in params.items():
        placeholder = f":{key}"
        if placeholder in sql:
            clean_params[key] = val
    clean_params["start_time"] = params["start_time"]
    clean_params["end_time"] = params["end_time"]
    clean_params["workspace_id"] = params["workspace_id"]
    clean_params["limit"] = params["limit"]
    if "prev_start_time" in params:
        clean_params["prev_start_time"] = params["prev_start_time"]

    return sql, clean_params


def get_chart_config(metric_config: dict, query_type: str) -> dict | None:
    """获取指定 query_type 的图表配置"""
    charts = metric_config.get("chart", {})
    if not charts:
        return None
    config = charts.get(query_type) or charts.get("trend") or charts.get("breakdown")
    return config
