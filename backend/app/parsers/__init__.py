"""Parser 工厂 - 根据配置选择规则 Parser 或 LLM Parser"""

import os

from app.models.schemas import QueryIntent


def parse_question(
    question: str,
    selected_metric: str | None = None,
    selected_query_type: str | None = None,
    space_id: str = "tech_quality",
) -> QueryIntent:
    # 用户从候选列表点击时，直接构造结构化 Intent
    if selected_metric:
        from app.parsers.rule_parser import parse_question as rule_parse
        intent = rule_parse(question)
        intent.metric = selected_metric
        if selected_query_type:
            intent.query_type = selected_query_type
        intent.confidence = 1.0
        intent.clarification_reason = None
        return intent

    use_llm = os.getenv("USE_LLM_PARSER", "true").lower() == "true"

    if use_llm:
        from app.parsers.llm_parser import parse_with_llm
        return parse_with_llm(question, space_id=space_id)
    else:
        from app.parsers.rule_parser import parse_question as rule_parse
        return rule_parse(question)
