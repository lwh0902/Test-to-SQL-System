"""LLM Parser - 用 DeepSeek v4 Flash 做结构化解析

替换规则 Parser，输出格式与 RuleParser 完全一致（QueryIntent）。
支持按 space_id 动态加载指标和维度。
"""

import json
import os

from anthropic import Anthropic

from app.models.schemas import QueryIntent, TimeRange
from app.services.metric_service import list_metrics as _list_metrics

BASE_PROMPT = """你是一个数据查询意图解析器。将用户的自然语言问题解析为结构化 JSON。

可用的指标列表：
{metrics}

只输出一个 JSON 对象，格式如下，不要输出其他任何文字：
{{"metric": "指标key", "query_type": "trend或fact或breakdown或comparison或anomaly_breakdown", "time_range": {{"start": "YYYY-MM-DD", "end": "YYYY-MM-DD"}}, "dimensions": [], "filters": {{}}, "confidence": 0.95, "clarification_reason": null}}

规则：
- trend: 用户想看趋势变化（按时间）
- fact: 用户想知道一个汇总数值
- breakdown: 用户想按某个维度拆解
- comparison: 用户想对比不同时期
- anomaly_breakdown: 用户问"为什么"下降/升高、问异常原因、问"怎么回事"，想了解指标变化的根因
- 如果问题模糊无法确定指标，confidence 设为 0.3 以下，clarification_reason 填原因，metric 填 null
- dimensions: {dimensions}
- filters: {filters}
- 今天是 {today}"""

# 按空间定义维度和过滤器提示
SPACE_DIMENSIONS = {
    "tech_quality": "date, scan_type, device_type, error_type, api_name, feature_name",
    "ecommerce": "date, category, source_channel, reason, product_name, register_channel, status",
}

SPACE_FILTERS = {
    "tech_quality": "scan_type (id_card/bank_card/face/ocr), device_type (ios/android/web), api_name",
    "ecommerce": "source_channel (direct/search/social/ads/email), category (手机/电脑/配件/家居/服饰/食品), product_name",
}


def _get_client() -> Anthropic:
    return Anthropic(
        api_key=os.getenv("LLM_API_KEY"),
        base_url=os.getenv("LLM_BASE_URL", "https://api.deepseek.com/anthropic"),
    )


def _get_model() -> str:
    return os.getenv("LLM_MODEL", "deepseek-v4-flash")


def _extract_text(response) -> str:
    for block in response.content:
        if type(block).__name__ == "TextBlock" and hasattr(block, "text"):
            return block.text.strip()
    return ""


def parse_with_llm(question: str, space_id: str = "tech_quality") -> QueryIntent:
    client = _get_client()

    metrics = _list_metrics(space_id)
    metrics_str = "\n".join(f"- {m['key']}: {m['name']} - {m['description']}" for m in metrics)

    from datetime import datetime
    today = datetime.now().strftime("%Y-%m-%d")

    dimensions = SPACE_DIMENSIONS.get(space_id, "date")
    filters = SPACE_FILTERS.get(space_id, "")

    system = BASE_PROMPT.format(
        metrics=metrics_str, today=today,
        dimensions=dimensions, filters=filters,
    )

    try:
        response = client.messages.create(
            model=_get_model(),
            max_tokens=1024,
            system=system,
            messages=[{"role": "user", "content": question}],
        )

        text = _extract_text(response)
        if not text:
            return QueryIntent(confidence=0.0, clarification_reason="LLM 返回为空")

        if text.startswith("```"):
            text = text.split("```")[1]
            if text.startswith("json"):
                text = text[4:]
            text = text.strip()

        start = text.find("{")
        if start == -1:
            return QueryIntent(confidence=0.0, clarification_reason="LLM 未返回 JSON")

        end = text.rfind("}")
        if end == -1:
            return QueryIntent(confidence=0.0, clarification_reason="LLM JSON 截断")

        json_str = text[start:end + 1]
        parsed = json.loads(json_str)

        time_range_raw = parsed.get("time_range")
        time_range = None
        if time_range_raw and isinstance(time_range_raw, dict) and time_range_raw.get("start") and time_range_raw.get("end"):
            time_range = TimeRange(**time_range_raw)

        return QueryIntent(
            metric=parsed.get("metric"),
            query_type=parsed.get("query_type"),
            time_range=time_range,
            dimensions=parsed.get("dimensions", []),
            filters=parsed.get("filters", {}),
            confidence=parsed.get("confidence", 0.5),
            clarification_reason=parsed.get("clarification_reason") or None,
        )
    except json.JSONDecodeError as e:
        return QueryIntent(
            confidence=0.0,
            clarification_reason=f"JSON 解析失败: {str(e)}",
        )
    except Exception as e:
        return QueryIntent(
            confidence=0.0,
            clarification_reason=f"LLM 调用失败: {str(e)}",
        )
