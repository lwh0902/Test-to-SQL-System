"""LangGraph 状态机 - DataPilot Agent

节点：
0. intent_router: 意图路由（data_query / follow_up / chat / help）
1. planner: 解析用户问题 → QueryIntent
2. metric_resolver: 获取指标配置（按空间）
3. permission_guard: 权限校验
4. sql_generator: 渲染 SQL
5. sql_guard: 安全校验
6. query_executor: 执行查询
7. persister: 持久化消息和 trace（所有路径必经）
"""

from __future__ import annotations

import os
import json

from dataclasses import dataclass, field
from typing import Any

from langgraph.graph import StateGraph, END

from app.models.schemas import QueryIntent, TimeRange, TraceStep
from app.services.metric_service import get_metric_config, render_sql, list_metrics, get_chart_config
from app.parsers import parse_question
from app.services.query_service import execute_query, generate_trace_id
from app.guards.sql_guard import SQLGuard
from app.guards.permission_guard import PermissionGuard
from app.services.persistence import persist_from_agent_state


@dataclass
class AgentState:
    question: str = ""
    user_role: str = "tester"
    workspace_id: str = "default"
    space_id: str = "tech_quality"
    user_id: int = 1
    session_id: str | None = None
    trace_id: str = ""
    selected_metric: str | None = None
    selected_query_type: str | None = None

    # 路由
    route: str = "data_query"  # data_query / follow_up / chat / help / plan_execute

    # 上下文
    chat_history: list[dict] = field(default_factory=list)
    working_memory: dict | None = None

    # Plan-and-Execute
    plan: list[dict] = field(default_factory=list)       # [{question, metric, query_type}, ...]
    plan_results: list[dict] = field(default_factory=list)  # [{question, rows, columns, chart}, ...]
    plan_step_index: int = 0

    # 各节点输出
    intent: QueryIntent | None = None
    metric_config: dict | None = None
    sql: str = ""
    params: dict = field(default_factory=dict)
    columns: list[str] = field(default_factory=list)
    rows: list[dict] = field(default_factory=dict)

    # 控制流
    response_type: str = "answer"
    message: str = ""
    candidates: list[dict] = field(default_factory=list)
    error_code: str = ""
    trace: list[dict] = field(default_factory=list)
    chart: dict | None = None

    # SSE 事件
    events: list[dict] = field(default_factory=list)


def _add_event(state: AgentState, event_type: str, data: dict) -> None:
    state.events.append({"event": event_type, "data": data})


# ======== Intent Router ========

# 数据查询相关关键词（两个空间的指标关键词合集）
_DATA_KEYWORDS = [
    "趋势", "销量", "销售额", "订单", "转化率", "退款", "客单价", "商品",
    "成功率", "扫描", "错误", "API", "响应时间", "功能使用",
    "对比", "分布", "排行", "渠道", "用户数", "支付",
    "多少", "总计", "平均", "汇总", "按天", "按日", "按渠道", "按商品",
    "最近", "这周", "上周", "环比", "同比",
    "拆解", "拆一下", "按", "top",
    "为什么", "怎么回事", "原因", "下降", "升高", "异常",
]

# 闲聊关键词
_CHAT_KEYWORDS = [
    "你好", "嗨", "hello", "hi", "谢谢", "感谢", "再见", "拜拜",
    "你是谁", "你叫什么", "你是什么", "介绍一下你",
    "今天天气", "讲个笑话",
]

# 帮助关键词
_HELP_KEYWORDS = [
    "怎么用", "帮助", "help", "你能做什么", "功能", "使用说明",
    "支持哪些", "有什么指标", "怎么查",
]

# 指代/追问关键词（需要上下文）
_FOLLOW_UP_KEYWORDS = [
    "按渠道拆", "按商品拆", "拆一下", "换个", "按", "那",
    "订单数呢", "退款率呢", "它", "这个", "那个",
    "再按", "换成", "换个维度",
]

# Plan-and-Execute 触发关键词（需要多步推理）
_PLAN_EXECUTE_KEYWORDS = [
    "为什么", "怎么回事", "原因是什么", "分析原因",
    "异常原因", "下降原因", "升高原因", "为什么下降", "为什么升高",
    "根因", "归因", "总结一下", "综合分析",
    "周报", "月报", "日报", "报告",
]


def _route_with_rules(question: str) -> str | None:
    """规则快速分类，返回 None 表示无法判断需要 LLM"""
    q = question.lower().strip()

    # 短问题 + 不含数据关键词 → 大概率闲聊
    if len(q) <= 6 and not any(kw in q for kw in _DATA_KEYWORDS):
        if any(kw in q for kw in _CHAT_KEYWORDS):
            return "chat"
        if any(kw in q for kw in _HELP_KEYWORDS):
            return "help"

    # 明确的闲聊
    if any(kw in q for kw in _CHAT_KEYWORDS):
        # 但如果同时包含数据关键词，优先当数据查询
        if not any(kw in q for kw in _DATA_KEYWORDS):
            return "chat"

    # 明确的帮助
    if any(kw in q for kw in _HELP_KEYWORDS):
        if not any(kw in q for kw in _DATA_KEYWORDS):
            return "help"

    # Plan-and-Execute 触发词
    if any(kw in q for kw in _PLAN_EXECUTE_KEYWORDS):
        return "plan_execute"

    # 包含数据关键词 → 数据查询
    if any(kw in q for kw in _DATA_KEYWORDS):
        # 包含追问关键词 → follow_up
        if any(kw in q for kw in _FOLLOW_UP_KEYWORDS):
            return "follow_up"
        return "data_query"

    # 包含追问关键词但无数据关键词 → follow_up（依赖上下文补全）
    if any(kw in q for kw in _FOLLOW_UP_KEYWORDS):
        return "follow_up"

    return None


def _route_with_llm(question: str) -> str:
    """LLM 兜底分类"""
    from anthropic import Anthropic

    client = Anthropic(
        api_key=os.getenv("LLM_API_KEY"),
        base_url=os.getenv("LLM_BASE_URL", "https://api.deepseek.com/anthropic"),
    )

    try:
        response = client.messages.create(
            model=os.getenv("LLM_MODEL", "deepseek-v4-flash"),
            max_tokens=32,
            system="你是一个意图分类器。判断用户消息属于哪种类型，只输出一个词：\n- data_query: 数据查询请求（查指标、看趋势、看报表）\n- chat: 闲聊、打招呼、非业务问题\n- help: 求助、问怎么用\n\n只输出类型名，不要解释。",
            messages=[{"role": "user", "content": question}],
        )
        text = ""
        for block in response.content:
            if hasattr(block, "text"):
                text = block.text.strip().lower()
                break

        if "chat" in text:
            return "chat"
        if "help" in text:
            return "help"
        return "data_query"
    except Exception:
        return "data_query"


def intent_router(state: AgentState) -> dict:
    """意图路由节点：分类后写入 state.route"""
    question = state.question.strip()

    # 1. 规则分类（零成本）
    route = _route_with_rules(question)

    # 2. 规则无法判断 → LLM 兜底
    if route is None:
        route = _route_with_llm(question)

    state.route = route
    state.trace_id = generate_trace_id()

    _add_event(state, "intent_router", {
        "status": "done",
        "route": route,
    })

    return {"route": state.route, "trace_id": state.trace_id, "events": state.events}


def context_resolver(state: AgentState) -> dict:
    """上下文解析节点：对 follow_up 用 working_memory + 最近消息补全问题"""
    from app.services.persistence import load_recent_messages, load_working_memory

    # 加载上下文
    history = load_recent_messages(state.session_id, limit=6)
    wm = load_working_memory(state.session_id)
    state.chat_history = history
    state.working_memory = wm

    # 只有 follow_up 才需要 LLM 补全
    if state.route != "follow_up" or not history:
        _add_event(state, "context_resolver", {"status": "done", "resolved": False})
        return {"chat_history": state.chat_history, "working_memory": state.working_memory,
                "events": state.events}

    # 构建上下文摘要给 LLM
    history_lines = []
    for msg in history:
        role_label = "用户" if msg["role"] == "user" else "AI"
        content = msg["content"]
        # AI 消息带上结构化信息
        if msg["role"] == "assistant" and msg.get("meta"):
            meta = msg["meta"]
            if meta.get("intent"):
                intent = meta["intent"]
                history_lines.append(f"  → 查询指标: {intent.get('metric')}, 类型: {intent.get('query_type')}, 时间: {intent.get('time_range')}")
        history_lines.append(f"{role_label}: {content}")

    # working_memory 摘要
    wm_lines = []
    if wm:
        if wm.get("last_metric"):
            wm_lines.append(f"上次指标: {wm['last_metric']}")
        if wm.get("last_query_type"):
            wm_lines.append(f"上次查询类型: {wm['last_query_type']}")
        if wm.get("last_time_range"):
            wm_lines.append(f"上次时间范围: {wm['last_time_range']}")

    prompt = (
        "你是一个对话上下文解析器。根据历史对话和工作记忆，把用户的当前问题补全为独立可执行的查询。\n\n"
    )
    if wm_lines:
        prompt += "工作记忆:\n" + "\n".join(f"- {l}" for l in wm_lines) + "\n\n"
    prompt += "历史对话:\n" + "\n".join(history_lines) + f"\n\n当前问题: {state.question}\n\n"
    prompt += (
        "规则:\n"
        "- 如果用户换了指标但没说时间，沿用上次的时间范围\n"
        "- 如果用户说'拆一下/按X拆'，说明要切换为 breakdown 查询\n"
        "- 只输出补全后的完整问题，不要解释，不要加引号\n"
    )

    from anthropic import Anthropic
    client = Anthropic(
        api_key=os.getenv("LLM_API_KEY"),
        base_url=os.getenv("LLM_BASE_URL", "https://api.deepseek.com/anthropic"),
    )

    try:
        response = client.messages.create(
            model=os.getenv("LLM_MODEL", "deepseek-v4-flash"),
            max_tokens=128,
            messages=[{"role": "user", "content": prompt}],
        )
        resolved = ""
        for block in response.content:
            if hasattr(block, "text"):
                resolved = block.text.strip()
                break
        if resolved:
            state.question = resolved
    except Exception:
        pass

    _add_event(state, "context_resolver", {"status": "done", "resolved": bool(resolved)})
    return {"question": state.question, "chat_history": state.chat_history,
            "working_memory": state.working_memory, "events": state.events}


def chat_responder(state: AgentState) -> dict:
    """闲聊回复节点"""
    from anthropic import Anthropic

    client = Anthropic(
        api_key=os.getenv("LLM_API_KEY"),
        base_url=os.getenv("LLM_BASE_URL", "https://api.deepseek.com/anthropic"),
    )

    try:
        response = client.messages.create(
            model=os.getenv("LLM_MODEL", "deepseek-v4-flash"),
            max_tokens=256,
            system="你是 DataPilot Agent，一个 AI 数据分析助手。友好简短地回复用户的闲聊。不超过两句话。",
            messages=[{"role": "user", "content": state.question}],
        )
        text = ""
        for block in response.content:
            if hasattr(block, "text"):
                text = block.text.strip()
                break
        state.message = text or "你好！我是 DataPilot Agent，可以帮你查询数据分析。"
    except Exception:
        state.message = "你好！我是 DataPilot Agent，可以帮你查询数据分析。试试问我'最近7天销售额趋势'。"

    state.response_type = "chat"
    state.trace.append({"node": "chat_responder", "status": "done", "output": {}})

    _add_event(state, "chat_response", {"status": "done"})
    return {"message": state.message, "response_type": state.response_type,
            "events": state.events, "trace": state.trace}


def help_responder(state: AgentState) -> dict:
    """帮助回复节点"""
    metrics = list_metrics(state.space_id)
    metric_list = "\n".join(f"  - {m['name']}：{m['description']}" for m in metrics)

    state.message = (
        f"**DataPilot Agent 使用指南**\n\n"
        f"你可以用自然语言向我提问，我会自动查询数据并生成图表。\n\n"
        f"**当前空间支持的指标：**\n{metric_list}\n\n"
        f"**示例问题：**\n"
        f"  - 最近7天销售额趋势\n"
        f"  - 按渠道拆解订单数\n"
        f"  - 为什么最近成功率下降\n"
    )
    state.response_type = "help"
    state.trace.append({"node": "help_responder", "status": "done", "output": {}})

    _add_event(state, "help_response", {"status": "done"})
    return {"message": state.message, "response_type": state.response_type,
            "events": state.events, "trace": state.trace}


def planner(state: AgentState) -> dict:
    intent = parse_question(state.question, state.selected_metric, state.selected_query_type, space_id=state.space_id)
    state.intent = intent
    state.trace_id = generate_trace_id()

    _add_event(state, "planner", {
        "status": "done",
        "intent": intent.model_dump(),
    })
    state.trace.append({"node": "planner", "status": "done", "output": {"intent": intent.model_dump()}})

    if not intent.metric or intent.confidence < 0.3:
        state.response_type = "clarification"
        state.message = intent.clarification_reason or "无法识别您的问题，请选择一个指标"
        state.candidates = list_metrics(state.space_id)
        state.trace.append({"node": "clarification", "status": "done", "output": {}})

    return {"intent": state.intent, "trace_id": state.trace_id,
            "response_type": state.response_type, "message": state.message,
            "candidates": state.candidates, "events": state.events, "trace": state.trace}


def metric_resolver(state: AgentState) -> dict:
    if state.response_type != "answer":
        return {}

    config = get_metric_config(state.space_id, state.intent.metric)
    if not config:
        state.response_type = "error"
        state.message = f"指标配置不存在: {state.intent.metric}"
        state.trace.append({"node": "metric_resolver", "status": "error", "output": {"error": state.message}})
        return {"response_type": state.response_type, "message": state.message, "events": state.events, "trace": state.trace}

    state.metric_config = config
    _add_event(state, "metric_resolved", {
        "status": "done",
        "metric": state.intent.metric,
        "metric_name": config.get("name", ""),
    })
    state.trace.append({"node": "metric_resolver", "status": "done", "output": {"metric": state.intent.metric}})
    return {"metric_config": config, "events": state.events, "trace": state.trace}


def permission_guard(state: AgentState) -> dict:
    if state.response_type != "answer":
        return {}

    guard = PermissionGuard()
    result = guard.check(state.intent, state.user_role, metric_config=state.metric_config)

    _add_event(state, "guard_check", {
        "status": "passed" if result.passed else "denied",
        "guard_type": "permission",
        "code": result.code,
        "message": result.message,
    })

    if not result.passed:
        state.response_type = "error"
        state.message = result.message or "权限不足"

    state.trace.append({"node": "permission_guard", "status": "passed" if result.passed else "denied",
                        "output": {"passed": result.passed, "code": result.code}})
    return {"response_type": state.response_type, "message": state.message, "events": state.events, "trace": state.trace}


def sql_generator(state: AgentState) -> dict:
    if state.response_type != "answer":
        return {}

    sql, params = render_sql(state.intent, state.metric_config, state.workspace_id)
    state.sql = sql.strip()
    state.params = params

    _add_event(state, "sql_generated", {
        "status": "done",
        "sql": state.sql,
    })
    state.trace.append({"node": "sql_generator", "status": "done", "output": {"sql": state.sql}})
    return {"sql": state.sql, "params": params, "events": state.events, "trace": state.trace}


def sql_guard_node(state: AgentState) -> dict:
    if state.response_type != "answer":
        return {}

    guard = SQLGuard(
        permitted_tables=set(state.metric_config.get("permitted_tables", [])),
        sensitive_fields=set(state.metric_config.get("sensitive_fields", [])),
    )
    result = guard.check(state.sql)

    _add_event(state, "guard_check", {
        "status": "passed" if result.passed else "denied",
        "guard_type": "sql",
        "code": result.code,
        "message": result.message,
    })

    if not result.passed:
        state.response_type = "error"
        state.message = result.message or "SQL 安全校验未通过"

    state.trace.append({"node": "sql_guard", "status": "passed" if result.passed else "denied",
                        "output": {"passed": result.passed, "code": result.code, "message": result.message}})
    return {"response_type": state.response_type, "message": state.message, "events": state.events, "trace": state.trace}


def query_executor(state: AgentState) -> dict:
    if state.response_type != "answer":
        return {}

    columns, rows = execute_query(state.sql, state.params)
    state.columns = columns
    state.rows = rows

    _add_event(state, "query_done", {
        "status": "done",
        "rows": len(rows),
        "columns": columns,
    })

    metric_names = {
        "scan_success_rate": "扫描成功率", "scan_count": "扫描次数",
        "error_distribution": "错误分布", "api_success_rate": "API成功率",
        "api_response_time": "API响应时间", "feature_usage": "功能使用量",
        "gmv": "销售额", "order_count": "订单数", "avg_order_value": "客单价",
        "pay_conversion_rate": "支付转化率", "refund_rate": "退款率",
        "product_sales_rank": "商品销量排行", "channel_sales": "渠道销售额",
        "new_users": "新增用户数", "pay_user_count": "支付用户数",
    }
    label = metric_names.get(state.intent.metric, state.intent.metric)
    if state.intent.query_type == "trend" and len(rows) > 1:
        state.message = f"查询到 {len(rows)} 条{label}趋势数据。"
    elif state.intent.query_type == "breakdown" and len(rows) > 1:
        state.message = f"查询到 {len(rows)} 个分组的{label}数据。"
    elif len(rows) == 1:
        parts = [f"{col}={val}" for col, val in rows[0].items()]
        state.message = f"{label}: {', '.join(parts)}"
    else:
        state.message = f"查询到 {len(rows)} 条{label}数据。"

    chart_config = get_chart_config(state.metric_config, state.intent.query_type or "fact")

    state.trace.append({"node": "query_executor", "status": "done",
                        "output": {"rows": len(rows), "columns": columns}})
    return {"columns": columns, "rows": rows, "message": state.message, "chart": chart_config,
            "events": state.events, "trace": state.trace}


def persister(state: AgentState) -> dict:
    """持久化节点：保存消息和 trace，所有路径必经"""
    try:
        persist_from_agent_state(state)
    except Exception:
        pass  # 持久化失败不影响响应
    return {}


# ======== Plan-and-Execute 节点 ========

def plan_generator(state: AgentState) -> dict:
    """用 LLM 分析问题，生成多步查询计划"""
    from anthropic import Anthropic

    metrics = list_metrics(state.space_id)
    metric_list = "\n".join(f"- {m['key']}: {m['name']} — {m['description']}" for m in metrics)

    prompt = (
        f"用户问了一个需要多步分析的复杂问题。请制定查询计划。\n\n"
        f"可用指标：\n{metric_list}\n\n"
        f"用户问题：{state.question}\n\n"
        f"请输出一个 JSON 数组，每个元素是一步查询：\n"
        f'[{{"question": "查询描述", "metric": "指标key", "query_type": "trend或fact或breakdown"}}]\n\n'
        f"规则：\n"
        f"- 每步查一个指标，2-4 步为宜\n"
        f"- 先查整体趋势，再查拆解维度\n"
        f"- query_type: trend=趋势, fact=汇总值, breakdown=按维度拆解\n"
        f"- 只输出 JSON 数组，不要解释\n"
    )

    plan = []
    try:
        client = Anthropic(
            api_key=os.getenv("LLM_API_KEY"),
            base_url=os.getenv("LLM_BASE_URL", "https://api.deepseek.com/anthropic"),
        )
        response = client.messages.create(
            model=os.getenv("LLM_MODEL", "deepseek-v4-flash"),
            max_tokens=512,
            messages=[{"role": "user", "content": prompt}],
        )
        text = ""
        for block in response.content:
            if hasattr(block, "text"):
                text = block.text.strip()
                break

        # 提取 JSON
        start = text.find("[")
        end = text.rfind("]")
        if start != -1 and end != -1:
            plan = json.loads(text[start:end + 1])
    except Exception:
        pass

    if not plan:
        # 降级为单步查询
        state.route = "data_query"
        state.trace.append({"node": "plan_generator", "status": "fallback", "output": {"reason": "plan_generation_failed"}})
        return {"route": state.route, "events": state.events, "trace": state.trace}

    state.plan = plan
    state.trace.append({"node": "plan_generator", "status": "done", "output": {"steps": len(plan)}})
    _add_event(state, "plan_generated", {"status": "done", "steps": len(plan)})

    return {"plan": state.plan, "events": state.events, "trace": state.trace}


def step_executor(state: AgentState) -> dict:
    """执行 plan 中的当前步骤（复用现有 data_query 逻辑）"""
    idx = state.plan_step_index
    if idx >= len(state.plan):
        return {}

    step = state.plan[idx]
    step_question = step.get("question", "")
    metric = step.get("metric", "")
    query_type = step.get("query_type", "trend")

    # 用 LLM parser 解析这一步
    intent = parse_question(step_question, selected_metric=metric, selected_query_type=query_type, space_id=state.space_id)

    result = {"step": idx + 1, "question": step_question, "metric": metric}

    if not intent.metric:
        result["error"] = "无法解析指标"
        state.plan_results.append(result)
        state.plan_step_index = idx + 1
        return {"plan_results": state.plan_results, "plan_step_index": state.plan_step_index,
                "events": state.events, "trace": state.trace}

    # 获取指标配置
    config = get_metric_config(state.space_id, intent.metric)
    if not config:
        result["error"] = f"指标不存在: {intent.metric}"
        state.plan_results.append(result)
        state.plan_step_index = idx + 1
        return {"plan_results": state.plan_results, "plan_step_index": state.plan_step_index,
                "events": state.events, "trace": state.trace}

    # 权限检查
    guard = PermissionGuard()
    perm = guard.check(intent, state.user_role, metric_config=config)
    if not perm.passed:
        result["error"] = perm.message
        state.plan_results.append(result)
        state.plan_step_index = idx + 1
        return {"plan_results": state.plan_results, "plan_step_index": state.plan_step_index,
                "events": state.events, "trace": state.trace}

    # 生成 SQL
    sql, params = render_sql(intent, config, state.workspace_id)

    # SQL 安全检查
    sql_guard = SQLGuard(
        permitted_tables=set(config.get("permitted_tables", [])),
        sensitive_fields=set(config.get("sensitive_fields", [])),
    )
    guard_result = sql_guard.check(sql)
    if not guard_result.passed:
        result["error"] = guard_result.message
        state.plan_results.append(result)
        state.plan_step_index = idx + 1
        return {"plan_results": state.plan_results, "plan_step_index": state.plan_step_index,
                "events": state.events, "trace": state.trace}

    # 执行查询
    columns, rows = execute_query(sql, params)
    chart_config = get_chart_config(config, intent.query_type or "fact")

    result["rows"] = rows
    result["columns"] = columns
    result["chart"] = chart_config
    result["sql"] = sql.strip()

    state.plan_results.append(result)
    state.plan_step_index = idx + 1

    _add_event(state, "plan_step_done", {
        "status": "done", "step": idx + 1, "total": len(state.plan),
        "metric": metric, "rows": len(rows),
    })
    state.trace.append({"node": f"step_{idx+1}", "status": "done",
                        "output": {"metric": metric, "rows": len(rows)}})

    return {"plan_results": state.plan_results, "plan_step_index": state.plan_step_index,
            "events": state.events, "trace": state.trace}


def plan_should_continue(state: AgentState) -> str:
    """判断是否还有 plan 步骤要执行"""
    if state.plan_step_index < len(state.plan):
        return "continue"
    return "summarize"


def summary_agent(state: AgentState) -> dict:
    """用 LLM 综合所有步骤结果，生成归因总结"""
    from anthropic import Anthropic

    # 构建结果摘要
    results_text = ""
    for i, r in enumerate(state.plan_results):
        results_text += f"\n步骤{i+1}: {r.get('question', '')}\n"
        if r.get("error"):
            results_text += f"  错误: {r['error']}\n"
        else:
            rows = r.get("rows", [])
            columns = r.get("columns", [])
            if rows and columns:
                results_text += f"  数据列: {', '.join(columns)}\n"
                for j, row in enumerate(rows[:8]):
                    vals = ", ".join(f"{c}={row.get(c)}" for c in columns)
                    results_text += f"  第{j+1}行: {vals}\n"
                if len(rows) > 8:
                    results_text += f"  ...共 {len(rows)} 行\n"

    prompt = (
        f"用户问题：{state.question}\n\n"
        f"系统执行了 {len(state.plan_results)} 步查询，结果如下：\n"
        f"{results_text}\n"
        f"请用简洁的中文综合分析这些数据，回答用户的问题。要求：\n"
        f"- 先给出结论（1-2句话）\n"
        f"- 然后用数据支撑结论\n"
        f"- 如果有异常或风险，明确指出\n"
        f"- 总共不超过 200 字"
    )

    message = ""
    try:
        client = Anthropic(
            api_key=os.getenv("LLM_API_KEY"),
            base_url=os.getenv("LLM_BASE_URL", "https://api.deepseek.com/anthropic"),
        )
        response = client.messages.create(
            model=os.getenv("LLM_MODEL", "deepseek-v4-flash"),
            max_tokens=512,
            messages=[{"role": "user", "content": prompt}],
        )
        for block in response.content:
            if hasattr(block, "text"):
                message = block.text.strip()
                break
    except Exception:
        message = "多步分析完成，但总结生成失败。请查看上方各步骤的查询结果。"

    state.message = message
    state.response_type = "answer"

    # 合并所有步骤的 rows/columns/chart（取结果最多的那一步）
    best_result = None
    for r in state.plan_results:
        if r.get("rows") and (not best_result or len(r["rows"]) > len(best_result["rows"])):
            best_result = r

    if best_result:
        state.rows = best_result.get("rows", [])
        state.columns = best_result.get("columns", [])
        state.chart = best_result.get("chart")
        state.sql = best_result.get("sql", "")

    _add_event(state, "summary_done", {"status": "done"})
    state.trace.append({"node": "summary_agent", "status": "done", "output": {}})

    return {"message": state.message, "response_type": state.response_type,
            "rows": state.rows, "columns": state.columns, "chart": state.chart,
            "sql": state.sql, "events": state.events, "trace": state.trace}


def should_continue(state: AgentState) -> str:
    if state.response_type == "clarification":
        return "skip_to_end"
    if state.response_type == "error":
        return "skip_to_end"
    return "continue"


def route_intent(state: AgentState) -> str:
    """intent_router 之后的路由分发"""
    route = state.route
    if route == "chat":
        return "chat"
    if route == "help":
        return "help"
    if route == "follow_up":
        return "follow_up"
    if route == "plan_execute":
        return "plan_execute"
    # data_query 直接走 planner
    return "query"


def build_graph() -> StateGraph:
    graph = StateGraph(AgentState)

    graph.add_node("intent_router", intent_router)
    graph.add_node("context_resolver", context_resolver)
    graph.add_node("chat_responder", chat_responder)
    graph.add_node("help_responder", help_responder)
    graph.add_node("planner", planner)
    graph.add_node("metric_resolver", metric_resolver)
    graph.add_node("permission_guard", permission_guard)
    graph.add_node("sql_generator", sql_generator)
    graph.add_node("sql_guard", sql_guard_node)
    graph.add_node("query_executor", query_executor)
    graph.add_node("plan_generator", plan_generator)
    graph.add_node("step_executor", step_executor)
    graph.add_node("summary_agent", summary_agent)
    graph.add_node("persister", persister)

    graph.set_entry_point("intent_router")

    # 路由分发
    graph.add_conditional_edges("intent_router", route_intent, {
        "chat": "chat_responder",
        "help": "help_responder",
        "follow_up": "context_resolver",
        "query": "planner",
        "plan_execute": "plan_generator",
    })

    # follow_up: context_resolver → planner
    graph.add_edge("context_resolver", "planner")

    # chat/help 直接去 persister
    graph.add_edge("chat_responder", "persister")
    graph.add_edge("help_responder", "persister")

    # Plan-and-Execute: plan_generator → step_executor (loop) → summary_agent
    graph.add_conditional_edges("plan_generator", lambda s: "query" if s.route == "data_query" else "execute_plan", {
        "query": "planner",
        "execute_plan": "step_executor",
    })
    graph.add_conditional_edges("step_executor", plan_should_continue, {
        "continue": "step_executor",
        "summarize": "summary_agent",
    })
    graph.add_edge("summary_agent", "persister")

    # data_query 流水线
    graph.add_conditional_edges("planner", should_continue, {
        "continue": "metric_resolver",
        "skip_to_end": "persister",
    })
    graph.add_conditional_edges("metric_resolver", should_continue, {
        "continue": "permission_guard",
        "skip_to_end": "persister",
    })
    graph.add_conditional_edges("permission_guard", should_continue, {
        "continue": "sql_generator",
        "skip_to_end": "persister",
    })
    graph.add_conditional_edges("sql_generator", should_continue, {
        "continue": "sql_guard",
        "skip_to_end": "persister",
    })
    graph.add_conditional_edges("sql_guard", should_continue, {
        "continue": "query_executor",
        "skip_to_end": "persister",
    })
    graph.add_edge("query_executor", "persister")
    graph.add_edge("persister", END)

    return graph.compile()
