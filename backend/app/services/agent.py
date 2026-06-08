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
import re

from dataclasses import dataclass, field
from typing import Any

from langgraph.graph import StateGraph, END

from app.models.schemas import QueryIntent, TimeRange, TraceStep
from app.services.metric_service import get_metric_config, render_sql, list_metrics, get_chart_config
from app.parsers import parse_question
from app.services.query_service import execute_query, generate_trace_id, QuerySafeError
from app.guards.sql_guard import SQLGuard
from app.guards.permission_guard import PermissionGuard
from app.services.persistence import persist_from_agent_state
from app.services.anomaly_service import run_anomaly_breakdown
from app.services.data_map_service import SYSTEM_SCHEMAS, get_table_aliases, render_schema_help, safe_sample_table


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
    plan_goal: str = ""                                    # 计划总体目标
    plan: list[dict] = field(default_factory=list)         # [{id, title, metric, query_type, time_range, dimensions, purpose}, ...]
    plan_results: list[dict] = field(default_factory=list)  # [{step_id, step, question, rows, columns, chart}, ...]
    plan_step_index: int = 0
    plan_valid: bool = True                                # plan_validator 结果

    # 各节点输出
    intent: QueryIntent | None = None
    metric_config: dict | None = None
    sql: str = ""
    params: dict = field(default_factory=dict)
    columns: list[str] = field(default_factory=list)
    rows: list[dict] = field(default_factory=list)

    # 控制流
    response_type: str = "answer"
    message: str = ""
    candidates: list[dict] = field(default_factory=list)
    error_code: str = ""
    trace: list[dict] = field(default_factory=list)
    chart: dict | None = None

    # SSE 事件
    events: list[dict] = field(default_factory=list)

    # 自由模式（用户空间无指标模板）
    is_freeform: bool = False
    db_schema: dict | None = None
    table_target: str | None = None

    # MCP Client（用于通过 MCP 协议访问用户数据库）
    mcp_client: any = None  # app.mcp.client.MCPClient


def _add_event(state: AgentState, event_type: str, data: dict) -> None:
    state.events.append({"event": event_type, "data": data})


# ======== Intent Router ========

# 规则路由 — 明确场景（零延迟，零成本）
_RULE_CHAT = {"你好", "嗨", "hello", "hi", "谢谢", "感谢", "再见", "拜拜", "你是谁", "你叫什么", "你是什么", "介绍一下你", "今天天气", "讲个笑话"}
_RULE_HELP = {"怎么用", "帮助", "help", "你能做什么", "使用说明", "支持哪些", "有什么指标", "怎么查"}
_SCHEMA_HELP_KEYWORDS = ("有什么表", "数据库有什么", "有哪些数据", "能查什么", "可查什么", "数据结构", "表结构", "字段", "schema")
_DATABASE_PROFILE_KEYWORDS = (
    "接入的数据库", "当前数据库", "连接的数据库", "数据库是什么", "这个库是干嘛",
    "数据库是干嘛", "每个表的作用", "各个表的作用", "整体介绍", "数据库说明",
)

_DIAGNOSIS_PATTERNS = ("为什么", "怎么回事", "为什么没", "为什么是", "查不到",
                       "没有数据", "为空", "怎么是", "怎么没有", "是不是不对",
                       "为什么查不到", "什么原因")

_TABLE_VIEW_PATTERNS = ("看", "查", "打开", "查看", "浏览", "列出", "显示", "展示",
                        "我要看", "帮我查", "看一下")


def _should_diagnose(question: str, wm: dict | None) -> bool:
    """判断是否需要对上一轮结果做诊断：语义像追问 + frame 有可诊断状态"""
    if not wm:
        return False
    last_status = wm.get("last_result_status")
    if last_status not in ("no_data", "error"):
        return False
    q = question.strip()
    return any(p in q for p in _DIAGNOSIS_PATTERNS)


def _classify_table_intent(question: str) -> str:
    """区分 '看某表' (table_query) vs '某表是什么' (schema_help)"""
    q = question.strip()
    if any(p in q for p in _TABLE_VIEW_PATTERNS):
        return "table_query"
    return "schema_help"


def _route_with_rules(question: str) -> str | None:
    """规则快速分类，返回 None 表示需 LLM 判断"""
    q = question.lower().strip()

    # 短问题 + 无数据痕迹 → 大概率闲聊或帮助
    if len(q) <= 6:
        if q in _RULE_CHAT:
            return "chat"
        if q in _RULE_HELP:
            return "help"

    # 明确闲聊
    if q in _RULE_CHAT:
        return "chat"

    # 明确帮助
    if q in _RULE_HELP:
        return "help"

    if any(keyword in q for keyword in _DATABASE_PROFILE_KEYWORDS):
        return "database_profile"

    if any(keyword in q for keyword in _SCHEMA_HELP_KEYWORDS):
        return "schema_help"

    return None


def _route_with_llm(question: str) -> str:
    """LLM 兜底分类 — 识别全部路由类型"""
    from anthropic import Anthropic

    client = Anthropic(
        api_key=os.getenv("LLM_API_KEY"),
        base_url=os.getenv("LLM_BASE_URL", "https://api.deepseek.com/anthropic"),
    )

    try:
        response = client.messages.create(
            model=os.getenv("LLM_MODEL", "deepseek-v4-flash"),
            max_tokens=32,
            system=(
                "你是意图分类器。判断用户消息类型，只输出一个词：\n"
                "- data_query: 查询数据（指标、趋势、报表、数值、排序、分布）\n"
                "- follow_up: 基于上文追问或调整（拆解、换个维度、换指标）\n"
                "- plan_execute: 需要多步分析的复杂问题（为什么、原因分析、综合报告）\n"
                "- table_query: 用户要查看某张具体表的数据（看api_logs、查某张表）\n"
                "- database_profile: 用户询问当前接入的数据库、数据库用途、每个表的作用\n"
                "- schema_help: 询问数据库有什么表、有哪些数据、表结构、字段、能查什么\n"
                "- diagnosis: 用户追问上一轮查询为什么没数据、为什么报错\n"
                "- chat: 闲聊、打招呼\n"
                "- help: 求助、问怎么用\n"
                "只输出类型名。"
            ),
            messages=[{"role": "user", "content": question}],
        )
        text = ""
        for block in response.content:
            if hasattr(block, "text"):
                text = block.text.strip().lower()
                break

        for route in ("diagnosis", "database_profile", "schema_help", "table_query", "follow_up", "plan_execute", "data_query", "chat", "help"):
            if route in text:
                return route
        return "data_query"
    except Exception:
        return "data_query"


def _mentions_known_table(space_id: str, question: str) -> bool:
    q = question.lower().strip()
    try:
        aliases = get_table_aliases(space_id)
    except Exception:
        return False
    return any(alias and alias in q for alias in aliases)


def _find_table_target(space_id: str, question: str) -> str | None:
    """从用户问题中解析当前空间内的真实表名。"""
    q = question.lower().strip()
    schema = _load_space_schema(space_id) or {}
    for table_name, table_info in schema.items():
        title = ""
        if isinstance(table_info, dict):
            title = table_info.get("title") or table_info.get("comment") or ""
        aliases = [table_name, title, title.replace("表", "")]
        if any(alias and alias.lower() in q for alias in aliases):
            return table_name
    return None


def intent_router(state: AgentState) -> dict:
    """意图路由节点：分类后写入 state.route"""
    from app.services.persistence import load_working_memory

    question = state.question.strip()

    _add_event(state, "run_started", {"text": "开始分析你的问题"})

    # 加载上一轮任务帧
    wm = load_working_memory(state.session_id)
    state.working_memory = wm

    # 1. 规则分类（零成本）
    route = _route_with_rules(question)

    # 2. 检查是否需要 diagnosis（语义追问 + frame 有 no_data/error）
    if route is None and _should_diagnose(question, wm):
        route = "diagnosis"

    # 3. 表名提及 → 区分 table_query vs schema_help
    if route is None and _mentions_known_table(state.space_id, question):
        route = _classify_table_intent(question)

    # 4. LLM 兜底
    if route is None:
        route = _route_with_llm(question)

    state.route = route
    state.trace_id = generate_trace_id()

    route_labels = {
        "data_query": "识别为数据查询",
        "plan_execute": "识别为多步分析任务",
        "follow_up": "识别为上下文追问",
        "chat": "识别为普通对话",
        "help": "识别为帮助问题",
        "diagnosis": "识别为结果诊断",
        "table_query": "识别为表数据查询",
        "database_profile": "识别为数据库档案",
        "schema_help": "识别为表结构查询",
    }

    _add_event(state, "route_done", {
        "route": route,
        "label": route_labels.get(route, f"识别为{route}"),
    })

    return {"route": state.route, "trace_id": state.trace_id, "events": state.events,
            "working_memory": state.working_memory}


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


def schema_help_responder(state: AgentState) -> dict:
    """数据地图回复节点 — 返回结构化 data_map"""
    from app.services.data_map_service import get_data_map, get_db_identity

    data_map = get_data_map(state.space_id)
    db_identity = get_db_identity(state.space_id)

    # 生成简要文字描述作为消息
    summary = data_map.get("summary", {})
    table_count = summary.get("table_count", 0)
    field_count = summary.get("field_count", 0)
    metric_count = summary.get("metric_count", 0)

    if db_identity and db_identity.get("connection"):
        conn = db_identity["connection"]
        identity_line = f"当前连接到 {conn['db_type']} 数据库 `{conn['db_name']}`（{conn['host_masked']}:{conn['port']}），"
    else:
        identity_line = f"当前空间 `{state.space_id}`，"

    state.message = (
        f"{identity_line}共 {table_count} 张表、{field_count} 个字段、{metric_count} 个推荐指标。"
    )

    state.response_type = "data_map"
    # 存储结构化数据供 SSE 和前端使用
    state.chart = None  # 不走图表路径
    state.candidates = []  # 不走候选路径
    # 通过 trace 传递 data_map 和 db_identity
    state.trace.append({
        "node": "schema_help_responder",
        "status": "done",
        "output": {"data_map": data_map, "db_identity": db_identity},
    })

    _add_event(state, "schema_help_response", {"status": "done"})
    return {"message": state.message, "response_type": state.response_type,
            "events": state.events, "trace": state.trace}


def database_profile_responder(state: AgentState) -> dict:
    """数据库档案回复：连接身份 + 每张表作用 + 可问问题。"""
    from app.services.data_map_service import render_database_profile

    state.message = render_database_profile(state.space_id)
    state.response_type = "answer"
    state.trace.append({"node": "database_profile_responder", "status": "done", "output": {}})

    _add_event(state, "database_profile_response", {"status": "done"})
    return {"message": state.message, "response_type": state.response_type,
            "events": state.events, "trace": state.trace}


def diagnosis_handler(state: AgentState) -> dict:
    """诊断上一轮查询为什么没有结果"""
    from anthropic import Anthropic
    from app.core.engine_registry import engine_registry
    from app.core.database import engine as default_engine
    from app.services.data_map_service import _is_safe_identifier
    from sqlalchemy import text

    wm = state.working_memory or {}
    target = wm.get("last_target", "未知目标")
    last_sql = wm.get("last_sql", "")
    last_status = wm.get("last_result_status", "unknown")
    last_error = wm.get("last_error")

    _add_event(state, "diagnosis_started", {"target": target})

    checks: dict = {}
    eng = engine_registry.get_engine(state.space_id) if state.space_id else default_engine
    schema = _load_space_schema(state.space_id) or {}
    known_tables = set(schema.keys())

    try:
        if target not in known_tables or not _is_safe_identifier(target):
            checks["table_exists"] = False
            checks["reason"] = "target_not_in_space_schema"
            raise StopIteration

        with eng.connect() as conn:
            # 1. 表是否存在
            result = conn.execute(text(
                "SELECT COUNT(*) FROM information_schema.tables "
                "WHERE table_schema = DATABASE() AND table_name = :name"
            ), {"name": target})
            checks["table_exists"] = result.scalar() > 0

            if checks["table_exists"]:
                # 2. 表总行数
                result = conn.execute(text(f"SELECT COUNT(*) FROM `{target}`"))
                checks["total_rows"] = result.scalar()

                # 3. 最新数据时间
                for col in ("created_at", "updated_at", "date", "timestamp"):
                    try:
                        result = conn.execute(text(f"SELECT MAX(`{col}`) FROM `{target}`"))
                        val = result.scalar()
                        if val:
                            checks["latest_data_time"] = str(val)
                            break
                    except Exception:
                        continue

                # 4. 无业务过滤的样例存在性，用固定模板诊断，不改写上一条 SQL
                result = conn.execute(text(f"SELECT 1 FROM `{target}` LIMIT 1"))
                checks["has_any_row"] = result.fetchone() is not None
    except StopIteration:
        pass
    except Exception as e:
        checks["error"] = str(e)

    # 用 LLM 生成诊断结论
    prompt = (
        f"你是 DataPilot Agent 数据分析助手。\n"
        f"用户查询了 '{target}'，但返回了 0 条数据（状态：{last_status}）。\n\n"
        f"系统诊断结果：\n"
    )
    for k, v in checks.items():
        prompt += f"- {k}: {v}\n"
    prompt += f"\n原始 SQL: {last_sql}\n"
    if last_error:
        prompt += f"错误信息: {last_error}\n"
    prompt += (
        "\n请用简洁的中文解释为什么查不到数据，并给出建议（改时间范围、去掉筛选条件等）。"
        "2-3句话，包含具体数字。"
    )

    message = ""
    try:
        client = Anthropic(
            api_key=os.getenv("LLM_API_KEY"),
            base_url=os.getenv("LLM_BASE_URL", "https://api.deepseek.com/anthropic"),
        )
        response = client.messages.create(
            model=os.getenv("LLM_MODEL", "deepseek-v4-flash"),
            max_tokens=256,
            messages=[{"role": "user", "content": prompt}],
        )
        for block in response.content:
            if hasattr(block, "text"):
                message = block.text.strip()
                break
    except Exception:
        pass

    if not message:
        if checks.get("table_exists") is False:
            message = f"表 '{target}' 不存在。请检查表名是否正确。"
        elif checks.get("total_rows") == 0:
            message = f"表 '{target}' 目前没有任何数据。请确认数据是否已导入。"
        else:
            message = f"查询 '{target}' 返回了 0 条数据。请尝试调整时间范围或筛选条件。"

    state.message = message
    state.response_type = "answer"
    state.trace.append({"node": "diagnosis_handler", "status": "done",
                        "output": {"checks": checks}})

    _add_event(state, "diagnosis_done", {"checks": checks})
    return {"message": state.message, "response_type": state.response_type,
            "events": state.events, "trace": state.trace}


def table_query_handler(state: AgentState) -> dict:
    """表查询处理：识别表名，设置 freeform 模式后走 freeform SQL 路径"""
    from app.services.persistence import load_recent_messages, load_working_memory

    history = load_recent_messages(state.session_id, limit=6)
    wm = load_working_memory(state.session_id)
    state.chat_history = history
    state.working_memory = wm
    state.is_freeform = True
    state.db_schema = _load_space_schema(state.space_id)
    state.table_target = _find_table_target(state.space_id, state.question)

    _add_event(state, "table_query", {"status": "done", "target": state.table_target})
    return {"is_freeform": state.is_freeform, "db_schema": state.db_schema,
            "table_target": state.table_target,
            "chat_history": state.chat_history, "working_memory": state.working_memory,
            "events": state.events}


def planner(state: AgentState) -> dict:
    _add_event(state, "planner_started", {"text": "正在理解查询意图"})

    # 检测是否为自由模式（用户空间无指标模板）
    is_freeform = _is_freeform_space(state.space_id)
    state.is_freeform = is_freeform

    if is_freeform:
        # 自由模式：加载 db_schema，创建 MCP client
        state.db_schema = _load_space_schema(state.space_id)
        try:
            from app.mcp.client import MCPClient
            state.mcp_client = MCPClient(state.space_id)
        except Exception:
            state.mcp_client = None
        state.trace_id = generate_trace_id()
        _add_event(state, "planner", {
            "status": "done",
            "mode": "freeform",
        })
        state.trace.append({"node": "planner", "status": "done", "output": {"mode": "freeform"}})
        return {"is_freeform": True, "db_schema": state.db_schema, "trace_id": state.trace_id,
                "mcp_client": state.mcp_client, "events": state.events, "trace": state.trace}

    # 指标模板模式：用 parser 解析
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

    _add_event(state, "metric_resolving", {"text": "正在匹配业务指标"})
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

    _add_event(state, "permission_checking", {"text": "正在校验访问权限"})
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

    _add_event(state, "sql_generating", {"text": "正在生成查询语句"})
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

    _add_event(state, "sql_checking", {"text": "正在执行 SQL 安全校验"})
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


def _check_empty_result(sql: str, space_id: str | None, table_target: str | None = None) -> str | None:
    """0 行结果自检：确认表是否有数据，返回提示信息或 None"""
    import re
    from datetime import datetime
    from sqlalchemy import text
    from app.services.data_map_service import _is_safe_identifier

    schema = _load_space_schema(space_id or "") or {}
    table_name = table_target
    if not table_name:
        match = re.search(r'FROM\s+`?(\w+)`?', sql, re.IGNORECASE)
        table_name = match.group(1) if match else None
    if not table_name or table_name not in schema or not _is_safe_identifier(table_name):
        return None

    try:
        from app.core.engine_registry import engine_registry
        from app.core.database import engine as default_engine
        eng = engine_registry.get_engine(space_id) if space_id else default_engine

        with eng.connect() as conn:
            # 1. 检查表是否存在
            check = conn.execute(text(
                "SELECT COUNT(*) FROM INFORMATION_SCHEMA.TABLES "
                "WHERE TABLE_SCHEMA = DATABASE() AND TABLE_NAME = :tbl"
            ), {"tbl": table_name})
            if check.fetchone()[0] == 0:
                return f"未找到表 {table_name}，可能表名有误。"

            # 2. 检查表总行数
            count_result = conn.execute(text(f"SELECT COUNT(*) FROM `{table_name}`"))
            total = count_result.fetchone()[0]

            if total == 0:
                return f"表 {table_name} 当前没有数据（共 0 行）。"

            # 3. 检查最新时间（如果有时间字段）
            latest = None
            latest_column = None
            table_info = schema.get(table_name, {})
            candidate_time_columns = [
                c.get("name") for c in table_info.get("columns", [])
                if isinstance(c, dict) and (
                    "time" in c.get("name", "").lower()
                    or c.get("name", "").lower().endswith("_at")
                    or "date" in c.get("name", "").lower()
                    or "time" in str(c.get("type", "")).lower()
                    or "date" in str(c.get("type", "")).lower()
                )
            ] or ["created_at", "updated_at", "date", "timestamp", "time"]
            for suffix in candidate_time_columns:
                try:
                    r = conn.execute(text(
                        f"SELECT MAX(`{suffix}`) FROM `{table_name}`"
                    ))
                    val = r.fetchone()[0]
                    if val:
                        latest = str(val)
                        latest_column = suffix
                        break
                except Exception:
                    continue

            hint = f"表 {table_name} 共有 {total} 条数据，但当前筛选条件下没有匹配结果。"
            if latest:
                hint += f" 最新数据时间：{latest}。"
                date_literals = re.findall(r"\d{4}-\d{2}-\d{2}", sql)
                try:
                    latest_dt = datetime.fromisoformat(latest[:10])
                    query_dates = [datetime.fromisoformat(d) for d in date_literals]
                    if query_dates and min(query_dates) > latest_dt:
                        hint += f" 这次查询时间晚于最新数据，优先把时间范围调早或放宽。"
                    else:
                        hint += f" 可能是 `{latest_column}` 时间范围、过滤条件或 SQL 字段口径不匹配。"
                except Exception:
                    hint += " 可能是时间范围、过滤条件或 SQL 字段口径不匹配。"
            else:
                hint += " 可能是过滤条件过窄，或 SQL 使用的字段口径和实际数据不匹配。"
            hint += " 可以先查看最近有数据的时间段，或减少筛选条件后重试。"
            return hint
    except Exception:
        return None


def query_executor(state: AgentState) -> dict:
    if state.response_type != "answer":
        return {}

    _add_event(state, "query_running", {"text": "正在查询数据库"})
    try:
        columns, rows = execute_query(state.sql, state.params, space_id=state.space_id)
    except QuerySafeError as e:
        state.response_type = "error"
        state.message = str(e)
        state.trace.append({"node": "query_executor", "status": "denied", "output": {"error": str(e)}})
        return {"response_type": "error", "message": state.message, "events": state.events, "trace": state.trace}
    state.columns = columns
    state.rows = rows

    _add_event(state, "query_done", {
        "status": "done",
        "rows": len(rows),
        "columns": columns,
    })

    if not state.intent:
        if len(rows) == 0 and state.sql:
            no_data_hint = _check_empty_result(state.sql, state.space_id, state.table_target)
            state.message = no_data_hint or "当前查询没有返回数据。"
        else:
            target_label = state.table_target or "表数据"
            state.message = f"查询到 {len(rows)} 条 {target_label} 数据。"
        state.trace.append({"node": "query_executor", "status": "done",
                            "output": {"rows": len(rows), "columns": columns}})
        return {"columns": columns, "rows": rows, "message": state.message, "chart": None,
                "events": state.events, "trace": state.trace}

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

    # 0 行结果自检：检查表是否有数据，主动说明原因
    if len(rows) == 0 and state.sql:
        no_data_hint = _check_empty_result(state.sql, state.space_id, state.table_target)
        if no_data_hint:
            state.message = no_data_hint

    chart_config = get_chart_config(state.metric_config, state.intent.query_type or "fact")

    state.trace.append({"node": "query_executor", "status": "done",
                        "output": {"rows": len(rows), "columns": columns}})
    return {"columns": columns, "rows": rows, "message": state.message, "chart": chart_config,
            "events": state.events, "trace": state.trace}


def anomaly_breakdown_node(state: AgentState) -> dict:
    """异常归因节点：调用 anomaly_service 进行多维度拆解分析"""
    metric = state.intent.metric if state.intent else ""
    result = run_anomaly_breakdown(
        metric=metric,
        days=14,
        workspace_id=state.workspace_id,
        space_id=state.space_id,
    )

    trend_rows = result.get("trend", [])
    summary = result.get("summary", "异常分析完成")

    state.message = summary
    state.response_type = "answer"

    if trend_rows:
        state.columns = list(trend_rows[0].keys())
        state.rows = trend_rows

    chart_config = get_chart_config(state.metric_config, "trend")

    _add_event(state, "anomaly_breakdown", {
        "status": "done",
        "drop_date": result.get("drop_date", ""),
        "summary": summary,
    })
    state.trace.append({"node": "anomaly_breakdown", "status": "done",
                        "output": {"drop_date": result.get("drop_date"), "summary": summary}})

    return {"message": state.message, "response_type": state.response_type,
            "columns": state.columns, "rows": state.rows, "chart": chart_config,
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
    from datetime import datetime, timedelta

    _add_event(state, "plan_started", {})

    metrics = list_metrics(state.space_id)
    metric_list = "\n".join(f"- {m['key']}: {m['name']} — {m['description']}" for m in metrics)

    today = datetime.now()
    default_start = (today - timedelta(days=14)).strftime("%Y-%m-%d")
    default_end = today.strftime("%Y-%m-%d")

    prompt = (
        f"用户问了一个需要多步分析的复杂问题。请制定查询计划。\n\n"
        f"可用指标：\n{metric_list}\n\n"
        f"今天是 {today.strftime('%Y-%m-%d')}。\n"
        f"用户问题：{state.question}\n\n"
        f'请输出 JSON 对象（不要输出其他文字）：\n'
        f'{{"goal": "一句话描述分析目标", "steps": [{{"title": "步骤标题", "metric": "指标key", '
        f'"query_type": "trend或fact或breakdown", "time_range": {{"start": "YYYY-MM-DD", "end": "YYYY-MM-DD"}}, '
        f'"dimensions": ["date"], "purpose": "这步要确认什么"}}]}}\n\n'
        f"规则：\n"
        f"- 每步查一个指标，2-4 步为宜\n"
        f"- 先查整体趋势，再查拆解维度\n"
        f"- query_type: trend=趋势, fact=汇总值, breakdown=按维度拆解\n"
        f"- time_range 默认 {default_start} ~ {default_end}\n"
        f"- dimensions 用数组，如 [\"date\"] 或 [\"source_channel\"]\n"
    )

    plan = []
    goal = ""
    try:
        client = Anthropic(
            api_key=os.getenv("LLM_API_KEY"),
            base_url=os.getenv("LLM_BASE_URL", "https://api.deepseek.com/anthropic"),
        )
        response = client.messages.create(
            model=os.getenv("LLM_MODEL", "deepseek-v4-flash"),
            max_tokens=768,
            messages=[{"role": "user", "content": prompt}],
        )
        text = ""
        for block in response.content:
            if hasattr(block, "text"):
                text = block.text.strip()
                break

        # 提取 JSON 对象
        start = text.find("{")
        end = text.rfind("}")
        if start != -1 and end != -1:
            parsed = json.loads(text[start:end + 1])
            goal = parsed.get("goal", "")
            plan = parsed.get("steps", [])
    except Exception:
        pass

    if not plan:
        state.route = "data_query"
        state.trace.append({"node": "plan_generator", "status": "fallback", "output": {"reason": "plan_generation_failed"}})
        return {"route": state.route, "events": state.events, "trace": state.trace}

    # 补充 step id
    for i, step in enumerate(plan):
        step["id"] = f"step_{i + 1}"
        step.setdefault("title", step.get("question", f"步骤{i+1}"))
        step.setdefault("dimensions", ["date"])
        step.setdefault("purpose", "")
        step.setdefault("time_range", {"start": default_start, "end": default_end})

    state.plan = plan
    state.plan_goal = goal

    _add_event(state, "plan_generated", {
        "status": "done",
        "goal": goal,
        "steps": [{"id": s["id"], "title": s["title"]} for s in plan],
    })
    state.trace.append({"node": "plan_generator", "status": "done", "output": {"goal": goal, "steps": len(plan)}})

    return {"plan": state.plan, "plan_goal": state.plan_goal, "events": state.events, "trace": state.trace}


def plan_validator(state: AgentState) -> dict:
    """校验 plan 中每个 step 的指标、query_type、time_range"""
    from datetime import datetime, timedelta

    valid_steps = []
    available_metrics = {m["key"] for m in list_metrics(state.space_id)}
    valid_query_types = {"trend", "fact", "breakdown", "comparison"}
    max_range_days = 90
    today = datetime.now()

    for step in state.plan:
        errors = []

        # 检查指标存在
        metric = step.get("metric", "")
        if metric not in available_metrics:
            errors.append(f"指标不存在: {metric}")

        # 检查 query_type 合法
        qt = step.get("query_type", "")
        if qt not in valid_query_types:
            errors.append(f"不支持的查询类型: {qt}")

        # 检查 time_range ≤ 90天
        tr = step.get("time_range")
        if tr and tr.get("start") and tr.get("end"):
            try:
                start = datetime.strptime(tr["start"], "%Y-%m-%d")
                end = datetime.strptime(tr["end"], "%Y-%m-%d")
                if (end - start).days > max_range_days:
                    errors.append(f"时间范围超过 {max_range_days} 天")
                if start > today or end > today:
                    errors.append("时间范围不能超过今天")
            except ValueError:
                errors.append("时间格式无效")

        if not errors:
            valid_steps.append(step)

    all_invalid = len(valid_steps) == 0
    if all_invalid:
        # 全部无效 → 回退单查询
        state.plan_valid = False
        state.route = "data_query"
        _add_event(state, "plan_validated", {
            "status": "fallback",
            "reason": "所有步骤均无效，回退单查询",
        })
        state.trace.append({"node": "plan_validator", "status": "fallback",
                            "output": {"reason": "all_steps_invalid"}})
        return {"plan_valid": state.plan_valid, "route": state.route,
                "events": state.events, "trace": state.trace}

    removed = len(state.plan) - len(valid_steps)
    state.plan = valid_steps
    state.plan_valid = True
    _add_event(state, "plan_validated", {
        "status": "done",
        "valid_steps": len(valid_steps),
        "removed": removed,
    })
    state.trace.append({"node": "plan_validator", "status": "done",
                        "output": {"valid": len(valid_steps), "removed": removed}})
    return {"plan": state.plan, "plan_valid": state.plan_valid,
            "events": state.events, "trace": state.trace}


def step_executor(state: AgentState) -> dict:
    """执行 plan 中的当前步骤（复用现有 data_query 逻辑）"""
    idx = state.plan_step_index
    if idx >= len(state.plan):
        return {}

    step = state.plan[idx]
    step_id = step.get("id", f"step_{idx + 1}")
    step_title = step.get("title", f"步骤{idx + 1}")
    metric = step.get("metric", "")
    query_type = step.get("query_type", "trend")
    step_question = step.get("question", step_title)
    total = len(state.plan)

    _add_event(state, "step_started", {
        "step_id": step_id, "step_number": idx + 1, "total": total,
        "title": step_title,
    })

    # 用 LLM parser 解析这一步
    intent = parse_question(step_question, selected_metric=metric, selected_query_type=query_type, space_id=state.space_id)

    result = {"step_id": step_id, "step": idx + 1, "question": step_question,
              "title": step_title, "metric": metric}

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
    try:
        columns, rows = execute_query(sql, params, space_id=state.space_id)
    except QuerySafeError as e:
        result["error"] = str(e)
        state.plan_results.append(result)
        state.plan_step_index = idx + 1
        return {"plan_results": state.plan_results, "plan_step_index": state.plan_step_index,
                "events": state.events, "trace": state.trace}
    chart_config = get_chart_config(config, intent.query_type or "fact")

    result["rows"] = rows
    result["columns"] = columns
    result["chart"] = chart_config
    result["sql"] = sql.strip()

    state.plan_results.append(result)
    state.plan_step_index = idx + 1

    _add_event(state, "step_completed", {
        "step_id": step_id, "step_number": idx + 1, "total": total,
        "rows": len(rows), "has_chart": chart_config is not None,
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
    """用 LLM 综合所有步骤结果，流式生成归因总结"""
    from anthropic import Anthropic

    _add_event(state, "summary_started", {})

    # 构建结果摘要
    results_text = ""
    for i, r in enumerate(state.plan_results):
        title = r.get("title", r.get("question", f"步骤{i+1}"))
        results_text += f"\n步骤{i+1}: {title}\n"
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
        with client.messages.stream(
            model=os.getenv("LLM_MODEL", "deepseek-v4-flash"),
            max_tokens=512,
            messages=[{"role": "user", "content": prompt}],
        ) as stream:
            for text in stream.text_stream:
                message += text
                _add_event(state, "answer_chunk", {"text": text})
    except Exception:
        message = "多步分析完成，但总结生成失败。请查看上方各步骤的查询结果。"

    if not message:
        message = "多步分析完成。请查看上方各步骤的查询结果。"

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


def _is_freeform_space(space_id: str) -> bool:
    """判断空间是否为自由模式（无预定义指标模板）"""
    metrics = list_metrics(space_id)
    return len(metrics) == 0


def _load_space_schema(space_id: str) -> dict | None:
    """加载空间的 db_schema（用户空间的自动发现结果）"""
    import json as _json
    from app.core.database import engine as _engine
    if space_id in SYSTEM_SCHEMAS:
        return SYSTEM_SCHEMAS[space_id]
    with _engine.connect() as conn:
        result = conn.execute(text(
            "SELECT db_schema FROM analysis_spaces WHERE id = :id"
        ), {"id": space_id})
        row = result.fetchone()
    if not row or not row[0]:
        return None
    return _json.loads(row[0]) if isinstance(row[0], str) else row[0]


def freeform_sql_generator(state: AgentState) -> dict:
    """自由模式 SQL 生成 — LLM 根据 db_schema 直接生成 SQL"""
    from anthropic import Anthropic

    _add_event(state, "sql_generating", {"text": "正在根据数据库结构生成查询"})

    schema = state.db_schema
    if not schema and state.mcp_client:
        try:
            mcp_result = state.mcp_client.get_schema()
            schema = mcp_result.get("schema")
        except Exception:
            pass
    if not schema:
        state.response_type = "error"
        state.message = "无法获取数据库结构信息，请重新创建空间"
        return {"response_type": state.response_type, "message": state.message, "events": state.events}

    # 构建 schema 摘要给 LLM（含列级 comment + 安全样例值）
    schema_lines = []
    for table_name, table_info in list(schema.items())[:20]:
        if not (isinstance(table_info, dict) and "columns" in table_info):
            continue
        table_comment = table_info.get("comment", "")
        header = f"- {table_name}"
        if table_comment:
            header += f" ({table_comment})"
        schema_lines.append(header)

        col_parts = []
        for c in table_info["columns"][:15]:
            part = f"  {c['name']} {c['type']}"
            if c.get("comment"):
                part += f" -- {c['comment']}"
            col_parts.append(part)
        schema_lines.extend(col_parts)

        # 安全采样 2 行样例值，帮助 LLM 理解数据内容
        try:
            samples = safe_sample_table(state.space_id or "", table_name, limit=2)
            if samples:
                sample_line = "  样例: " + " | ".join(
                    f"{k}={v}" for k, v in samples[0].items()
                )
                schema_lines.append(sample_line)
        except Exception:
            pass

    schema_text = "\n".join(schema_lines)

    prompt = (
        f"用户在一个 MySQL 数据库上提问。请生成一条只读 SQL 查询。\n\n"
        f"数据库表结构：\n{schema_text}\n\n"
        f"用户问题：{state.question}\n\n"
        f"规则：\n"
        f"- 只生成 SELECT 语句\n"
        f"- 禁止 SELECT *，必须明确列出字段\n"
        f"- 必须 LIMIT，最大 500 行\n"
        f"- 如果没有时间条件，加上合理的 WHERE 时间范围（最近 7 天或 30 天）\n"
        f"- 用 MySQL 语法\n"
        f"- 只输出一条 SQL，不要解释，不要 markdown 代码块\n"
    )

    sql = ""
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
                sql = block.text.strip()
                break
    except Exception:
        state.response_type = "error"
        state.message = "SQL 生成失败，请稍后重试"
        return {"response_type": state.response_type, "message": state.message, "events": state.events}

    # 清理 LLM 输出中的 markdown 代码块
    if sql.startswith("```"):
        lines = sql.split("\n")
        sql = "\n".join(lines[1:-1]) if len(lines) > 2 else sql.replace("```", "")
    sql = sql.strip().rstrip(";")

    if not sql:
        state.response_type = "error"
        state.message = "SQL 生成失败，请换个方式提问"
        return {"response_type": state.response_type, "message": state.message, "events": state.events}

    state.sql = sql
    state.params = {}

    _add_event(state, "sql_generated", {"status": "done", "sql": sql})
    state.trace.append({"node": "freeform_sql_generator", "status": "done", "output": {"sql": sql}})
    return {"sql": state.sql, "params": state.params, "events": state.events, "trace": state.trace}


def freeform_sql_guard_node(state: AgentState) -> dict:
    """自由模式 SQL 安全校验 — 仅通用规则"""
    if state.response_type != "answer":
        return {}

    from app.guards.sql_guard import FreeformSQLGuard
    guard = FreeformSQLGuard()

    # 先自动补 LIMIT
    state.sql = guard.ensure_limit(state.sql)

    result = guard.check(state.sql)
    _add_event(state, "guard_check", {
        "status": "passed" if result.passed else "denied",
        "guard_type": "freeform_sql",
        "code": result.code,
        "message": result.message,
    })

    if not result.passed:
        state.response_type = "error"
        state.message = result.message or "SQL 安全校验未通过"

    state.trace.append({"node": "freeform_sql_guard", "status": "passed" if result.passed else "denied",
                        "output": {"passed": result.passed, "code": result.code}})
    return {"response_type": state.response_type, "message": state.message,
            "sql": state.sql, "events": state.events, "trace": state.trace}


def route_after_planner(state: AgentState) -> str:
    """planner 之后路由：clarification/error → skip, 自由模式 → freeform, 指标 → metric"""
    if state.response_type in ("clarification", "error"):
        return "skip_to_end"
    if state.is_freeform:
        return "freeform"
    return "metric"


def route_after_metric(state: AgentState) -> str:
    """metric_resolver 之后路由：anomaly_breakdown 走专用节点，其余走正常流水线"""
    if state.intent and state.intent.query_type == "anomaly_breakdown":
        return "anomaly"
    return "query"


def route_intent(state: AgentState) -> str:
    """intent_router 之后的路由分发"""
    route = state.route
    if route == "chat":
        return "chat"
    if route == "help":
        return "help"
    if route == "schema_help":
        return "schema_help"
    if route == "database_profile":
        return "database_profile"
    if route == "follow_up":
        return "follow_up"
    if route == "plan_execute":
        return "plan_execute"
    if route == "diagnosis":
        return "diagnosis"
    if route == "table_query":
        return "table_query"
    # data_query 直接走 planner
    return "query"


def build_graph() -> StateGraph:
    graph = StateGraph(AgentState)

    graph.add_node("intent_router", intent_router)
    graph.add_node("context_resolver", context_resolver)
    graph.add_node("chat_responder", chat_responder)
    graph.add_node("help_responder", help_responder)
    graph.add_node("schema_help_responder", schema_help_responder)
    graph.add_node("database_profile_responder", database_profile_responder)
    graph.add_node("planner", planner)
    graph.add_node("freeform_sql_generator", freeform_sql_generator)
    graph.add_node("freeform_sql_guard", freeform_sql_guard_node)
    graph.add_node("metric_resolver", metric_resolver)
    graph.add_node("permission_guard", permission_guard)
    graph.add_node("sql_generator", sql_generator)
    graph.add_node("sql_guard", sql_guard_node)
    graph.add_node("query_executor", query_executor)
    graph.add_node("anomaly_breakdown", anomaly_breakdown_node)
    graph.add_node("plan_generator", plan_generator)
    graph.add_node("plan_validator", plan_validator)
    graph.add_node("step_executor", step_executor)
    graph.add_node("summary_agent", summary_agent)
    graph.add_node("persister", persister)
    graph.add_node("diagnosis_handler", diagnosis_handler)
    graph.add_node("table_query_handler", table_query_handler)

    graph.set_entry_point("intent_router")

    # 路由分发
    graph.add_conditional_edges("intent_router", route_intent, {
        "chat": "chat_responder",
        "help": "help_responder",
        "schema_help": "schema_help_responder",
        "database_profile": "database_profile_responder",
        "follow_up": "context_resolver",
        "query": "planner",
        "plan_execute": "plan_generator",
        "diagnosis": "diagnosis_handler",
        "table_query": "table_query_handler",
    })

    # follow_up: context_resolver → planner
    graph.add_edge("context_resolver", "planner")

    # chat/help/schema_help 直接去 persister
    graph.add_edge("chat_responder", "persister")
    graph.add_edge("help_responder", "persister")
    graph.add_edge("schema_help_responder", "persister")
    graph.add_edge("database_profile_responder", "persister")

    # diagnosis → persister
    graph.add_edge("diagnosis_handler", "persister")

    # table_query → freeform_sql_generator
    graph.add_edge("table_query_handler", "freeform_sql_generator")

    # Plan-and-Execute: plan_generator → plan_validator → step_executor (loop) → summary_agent
    graph.add_conditional_edges("plan_generator", lambda s: "query" if s.route == "data_query" else "validate", {
        "query": "planner",
        "validate": "plan_validator",
    })
    graph.add_conditional_edges("plan_validator", lambda s: "query" if not s.plan_valid else "execute", {
        "query": "planner",
        "execute": "step_executor",
    })
    graph.add_conditional_edges("step_executor", plan_should_continue, {
        "continue": "step_executor",
        "summarize": "summary_agent",
    })
    graph.add_edge("summary_agent", "persister")

    # planner 之后：自由模式 or 指标模板模式 or skip(clarification/error)
    graph.add_conditional_edges("planner", route_after_planner, {
        "freeform": "freeform_sql_generator",
        "metric": "metric_resolver",
        "skip_to_end": "persister",
    })

    # 自由模式流水线：freeform_sql_generator → freeform_sql_guard → query_executor
    graph.add_conditional_edges("freeform_sql_generator", should_continue, {
        "continue": "freeform_sql_guard",
        "skip_to_end": "persister",
    })
    graph.add_conditional_edges("freeform_sql_guard", should_continue, {
        "continue": "query_executor",
        "skip_to_end": "persister",
    })

    # 指标模板模式流水线
    graph.add_conditional_edges("metric_resolver", route_after_metric, {
        "anomaly": "anomaly_breakdown",
        "query": "permission_guard",
    })
    graph.add_conditional_edges("anomaly_breakdown", should_continue, {
        "continue": "persister",
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


# 模块级编译一次，全局复用
_graph = build_graph()


def get_graph():
    return _graph
