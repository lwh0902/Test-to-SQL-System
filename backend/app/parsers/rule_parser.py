"""规则 Parser - 关键词匹配实现

将自然语言问题解析为结构化 QueryIntent。
第 1 阶段用规则实现，后面替换为 LLM Parser 时输出格式不变。
"""

from datetime import datetime, timedelta

from app.models.schemas import QueryIntent, TimeRange


# 指标关键词映射
METRIC_KEYWORDS: dict[str, list[str]] = {
    "scan_success_rate": ["成功率", "成功", "通过率", "失败率"],
    "scan_count": ["扫描次数", "扫描量", "调用量", "请求量", "次数", "数量"],
    "error_distribution": ["错误分布", "错误类型", "失败原因", "异常分布", "失败类型"],
    "api_success_rate": ["api成功率", "接口成功率", "接口错误", "api错误"],
    "api_response_time": ["响应时间", "耗时", "延迟", "rt", "response time"],
    "feature_usage": ["功能使用", "使用量", "功能调用量", "功能统计"],
}

# 查询类型关键词映射
QUERY_TYPE_KEYWORDS: dict[str, list[str]] = {
    "trend": ["趋势", "变化", "走势", "曲线", "每天", "每日", "按天", "按日"],
    "breakdown": ["分布", "拆解", "按", "分组", "对比", "各", "分类", "top"],
    "comparison": ["对比", "同比", "环比", "比较"],
    "fact": ["多少", "总计", "平均", "总", "目前", "当前", "整体", "汇总"],
    "anomaly_breakdown": ["为什么", "原因", "为什么下降", "为什么升高", "怎么回事", "异常原因", "成功率下降", "失败增多"],
}

# 维度关键词映射
DIMENSION_KEYWORDS: dict[str, list[str]] = {
    "date": ["按天", "按日", "每天", "每日", "日期", "趋势"],
    "scan_type": ["扫描类型", "扫描方式", "按类型"],
    "device_type": ["设备", "设备类型", "ios", "android", "平台"],
    "error_type": ["错误类型", "失败原因", "异常类型"],
    "api_name": ["接口", "api", "按接口"],
    "feature_name": ["功能", "按功能"],
}

# 时间关键词解析
TIME_KEYWORDS: dict[str, int] = {
    "7天": 7, "七天": 7, "一周": 7, "最近一周": 7,
    "14天": 14, "两周": 14, "最近两周": 14,
    "30天": 30, "三十天": 30, "一个月": 30, "最近一个月": 30,
    "3天": 3, "三天": 3, "近三天": 3,
    "1天": 1, "今天": 1, "今日": 1,
    "昨天": 1, "昨日": 1,
}


def parse_question(question: str) -> QueryIntent:
    question_lower = question.lower().strip()

    # 1. 匹配指标
    metric, metric_confidence = _match_metric(question_lower)

    # 2. 匹配查询类型
    query_type, type_confidence = _match_query_type(question_lower)

    # 3. 匹配时间范围
    time_range = _match_time_range(question_lower)

    # 4. 匹配维度
    dimensions = _match_dimensions(question_lower)

    # 5. 匹配过滤条件
    filters = _match_filters(question_lower)

    # 6. 计算置信度
    confidence = _calculate_confidence(
        metric, metric_confidence, query_type, type_confidence
    )

    # 7. 如果指标没匹配到，生成反问原因
    clarification_reason = None
    if not metric:
        clarification_reason = "无法识别要查询的指标"

    return QueryIntent(
        metric=metric,
        query_type=query_type,
        time_range=time_range,
        dimensions=dimensions,
        filters=filters,
        confidence=confidence,
        clarification_reason=clarification_reason,
    )


def _match_metric(question: str) -> tuple[str | None, float]:
    best_metric = None
    best_score = 0.0

    for metric, keywords in METRIC_KEYWORDS.items():
        for keyword in keywords:
            if keyword in question:
                score = len(keyword) / 10.0  # 更长的关键词权重更高
                if score > best_score:
                    best_score = score
                    best_metric = metric

    confidence = min(best_score + 0.5, 1.0) if best_metric else 0.0
    return best_metric, confidence


def _match_query_type(question: str) -> tuple[str | None, float]:
    best_type = None
    best_score = 0.0

    for qtype, keywords in QUERY_TYPE_KEYWORDS.items():
        for keyword in keywords:
            if keyword in question:
                score = len(keyword) / 10.0
                if score > best_score:
                    best_score = score
                    best_type = qtype

    confidence = min(best_score + 0.6, 1.0) if best_type else 0.0

    # 如果没匹配到具体类型，带时间范围的默认走 trend
    if not best_type:
        return "trend", 0.5

    return best_type, confidence


def _match_time_range(question: str) -> TimeRange | None:
    days = None

    # 精确匹配时间关键词
    for keyword, d in sorted(TIME_KEYWORDS.items(), key=lambda x: -len(x[0])):
        if keyword in question:
            days = d
            break

    # 如果有"最近 N 天"模式
    if days is None:
        import re
        match = re.search(r"最近\s*(\d+)\s*天", question)
        if match:
            days = int(match.group(1))

    if days is None:
        days = 7  # 默认7天

    now = datetime.now()
    start = now - timedelta(days=days)
    return TimeRange(
        start=start.strftime("%Y-%m-%d"),
        end=now.strftime("%Y-%m-%d"),
    )


def _match_dimensions(question: str) -> list[str]:
    dimensions = []
    for dim, keywords in DIMENSION_KEYWORDS.items():
        for keyword in keywords:
            if keyword in question.lower():
                if dim not in dimensions:
                    dimensions.append(dim)
                break
    return dimensions


def _match_filters(question: str) -> dict:
    filters = {}

    # 匹配扫描类型
    for st in ["id_card", "bank_card", "face", "ocr"]:
        cn_map = {
            "id_card": "身份证", "bank_card": "银行卡",
            "face": "人脸", "ocr": "ocr",
        }
        patterns = [cn_map[st]]
        if st == "ocr":
            patterns.append("文字识别")
            patterns.append("OCR")
        for p in patterns:
            if p.lower() in question.lower():
                filters["scan_type"] = st
                return filters

    # 匹配设备类型
    if "ios" in question.lower() or "苹果" in question:
        filters["device_type"] = "ios"
    elif "android" in question.lower() or "安卓" in question:
        filters["device_type"] = "android"

    # 匹配 API 名称
    import re
    api_match = re.search(r"/api/v\d+/[a-z]+", question)
    if api_match:
        filters["api_name"] = api_match.group()

    return filters


def _calculate_confidence(
    metric: str | None, metric_conf: float,
    query_type: str | None, type_conf: float,
) -> float:
    if not metric:
        return 0.0
    # 加权平均：指标权重 0.7，查询类型权重 0.3
    return round(metric_conf * 0.7 + type_conf * 0.3, 2)
