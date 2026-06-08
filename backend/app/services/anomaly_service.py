"""异常拆解服务

流程：
1. 查最近 N 天整体指标趋势
2. 找到下降最大的时间点
3. 对异常时段按多维度拆解
4. 生成描述性总结

支持多空间：tech_quality (scan_records) 和 ecommerce (ecom_orders)
"""

from datetime import datetime, timedelta

from sqlalchemy import text

from app.core.database import engine


# 不同空间/指标的异常查询配置
ANOMALY_CONFIGS = {
    "scan_success_rate": {
        "table": "scan_records",
        "trend_sql": """
            SELECT DATE(created_at) AS date,
                ROUND(SUM(CASE WHEN status = 'success' THEN 1 ELSE 0 END) * 100.0 / NULLIF(COUNT(*), 0), 2) AS value,
                COUNT(*) AS total_count
            FROM scan_records
            WHERE created_at >= :start_time AND created_at < :end_time AND workspace_id = :workspace_id
            GROUP BY DATE(created_at) ORDER BY date ASC LIMIT :limit
        """,
        "value_label": "成功率",
        "value_format": "percent",
        "breakdown_queries": {
            "error_type": "SELECT error_type, COUNT(*) AS error_count FROM scan_records WHERE status = 'failed' AND DATE(created_at) = :target_date AND workspace_id = :workspace_id AND error_type IS NOT NULL GROUP BY error_type ORDER BY error_count DESC LIMIT 10",
            "device_type": "SELECT device_type, ROUND(SUM(CASE WHEN status = 'success' THEN 1 ELSE 0 END) * 100.0 / NULLIF(COUNT(*), 0), 2) AS value, COUNT(*) AS total_count FROM scan_records WHERE DATE(created_at) = :target_date AND workspace_id = :workspace_id GROUP BY device_type ORDER BY total_count DESC LIMIT 10",
            "scan_type": "SELECT scan_type, ROUND(SUM(CASE WHEN status = 'success' THEN 1 ELSE 0 END) * 100.0 / NULLIF(COUNT(*), 0), 2) AS value, COUNT(*) AS total_count FROM scan_records WHERE DATE(created_at) = :target_date AND workspace_id = :workspace_id GROUP BY scan_type ORDER BY total_count DESC LIMIT 10",
        },
    },
    "gmv": {
        "table": "ecom_orders",
        "trend_sql": """
            SELECT DATE(created_at) AS date,
                SUM(pay_amount) AS value,
                COUNT(*) AS total_count
            FROM ecom_orders
            WHERE created_at >= :start_time AND created_at < :end_time
                AND status IN ('paid', 'shipped', 'completed') AND workspace_id = :workspace_id
            GROUP BY DATE(created_at) ORDER BY date ASC LIMIT :limit
        """,
        "value_label": "销售额",
        "value_format": "number",
        "breakdown_queries": {
            "source_channel": "SELECT source_channel AS dim_value, SUM(pay_amount) AS value, COUNT(*) AS total_count FROM ecom_orders WHERE DATE(created_at) = :target_date AND status IN ('paid','shipped','completed') AND workspace_id = :workspace_id GROUP BY source_channel ORDER BY value DESC LIMIT 10",
            "category": "SELECT oi.category AS dim_value, SUM(oi.subtotal) AS value, COUNT(*) AS total_count FROM ecom_order_items oi JOIN ecom_orders o ON oi.order_id = o.id WHERE DATE(o.created_at) = :target_date AND o.status IN ('paid','shipped','completed') AND o.workspace_id = :workspace_id GROUP BY oi.category ORDER BY value DESC LIMIT 10",
        },
    },
    "order_count": {
        "table": "ecom_orders",
        "trend_sql": """
            SELECT DATE(created_at) AS date,
                COUNT(*) AS value,
                SUM(CASE WHEN status = 'completed' THEN 1 ELSE 0 END) AS completed_count
            FROM ecom_orders
            WHERE created_at >= :start_time AND created_at < :end_time AND workspace_id = :workspace_id
            GROUP BY DATE(created_at) ORDER BY date ASC LIMIT :limit
        """,
        "value_label": "订单数",
        "value_format": "number",
        "breakdown_queries": {
            "source_channel": "SELECT source_channel AS dim_value, COUNT(*) AS value FROM ecom_orders WHERE DATE(created_at) = :target_date AND workspace_id = :workspace_id GROUP BY source_channel ORDER BY value DESC LIMIT 10",
            "status": "SELECT status AS dim_value, COUNT(*) AS value FROM ecom_orders WHERE DATE(created_at) = :target_date AND workspace_id = :workspace_id GROUP BY status ORDER BY value DESC LIMIT 10",
        },
    },
}


def run_anomaly_breakdown(
    metric: str = "scan_success_rate",
    days: int = 14,
    workspace_id: str = "default",
    space_id: str = "",
) -> dict:
    config = ANOMALY_CONFIGS.get(metric, ANOMALY_CONFIGS["scan_success_rate"])

    now = datetime.now()
    params = {
        "start_time": (now - timedelta(days=days)).strftime("%Y-%m-%d"),
        "end_time": now.strftime("%Y-%m-%d"),
        "workspace_id": workspace_id,
        "limit": 1000,
    }

    if space_id:
        from app.core.engine_registry import engine_registry
        eng = engine_registry.get_engine(space_id)
    else:
        from app.core.database import engine
        eng = engine

    # 1. 查趋势
    with eng.connect() as conn:
        trend_result = conn.execute(text(config["trend_sql"]), params)
        trend_rows = [_row_to_dict(r, trend_result.keys()) for r in trend_result.fetchall()]

    if len(trend_rows) < 3:
        return {"summary": "数据不足，无法进行异常分析", "trend": trend_rows}

    # 2. 找下降最大的点
    max_drop = 0
    drop_idx = -1
    for i in range(1, len(trend_rows)):
        prev = trend_rows[i - 1]["value"] or 0
        curr = trend_rows[i]["value"] or 0
        drop = prev - curr
        if drop > max_drop:
            max_drop = drop
            drop_idx = i

    if drop_idx < 0 or max_drop < 1:
        return {
            "summary": f"最近 {days} 天{config['value_label']}整体平稳，未检测到显著下降",
            "trend": trend_rows,
        }

    drop_date = trend_rows[drop_idx]["date"]
    value_label = config["value_label"]
    drop_from = trend_rows[drop_idx - 1]["value"]
    drop_to = trend_rows[drop_idx]["value"]

    # 3. 对异常日期做多维拆解
    breakdowns = {}
    with eng.connect() as conn:
        for dim_name, dim_sql in config.get("breakdown_queries", {}).items():
            dim_result = conn.execute(text(dim_sql), {
                "target_date": str(drop_date),
                "workspace_id": workspace_id,
            })
            breakdowns[dim_name] = [_row_to_dict(r, dim_result.keys()) for r in dim_result.fetchall()]

    # 4. 生成描述性总结
    if config["value_format"] == "percent":
        summary_parts = [
            f"检测到 {drop_date} {value_label}从 {drop_from}% 下降到 {drop_to}%（下降 {max_drop:.1f} 个百分点）。",
        ]
    else:
        summary_parts = [
            f"检测到 {drop_date} {value_label}从 {drop_from} 下降到 {drop_to}（下降 {max_drop:.1f}）。",
        ]

    for dim_name, dim_rows in breakdowns.items():
        if dim_rows:
            top = dim_rows[0]
            first_val = list(top.values())
            if len(first_val) >= 2:
                dim_label = first_val[0]
                dim_value = first_val[1]
                summary_parts.append(f"{dim_name} 中 {dim_label} 数值最高（{dim_value}）。")

    summary_parts.append("建议优先排查以上异常维度。")

    return {
        "summary": " ".join(summary_parts),
        "drop_date": str(drop_date),
        "drop_from": drop_from,
        "drop_to": drop_to,
        "drop_points": max_drop,
        "trend": trend_rows,
        "breakdowns": breakdowns,
    }


def _row_to_dict(row, keys) -> dict:
    from decimal import Decimal
    result = {}
    for k, v in zip(keys, row):
        if isinstance(v, Decimal):
            result[k] = float(v)
        elif isinstance(v, datetime):
            result[k] = v.strftime("%Y-%m-%d %H:%M:%S")
        elif hasattr(v, "strftime"):
            result[k] = str(v)
        else:
            result[k] = v
    return result
