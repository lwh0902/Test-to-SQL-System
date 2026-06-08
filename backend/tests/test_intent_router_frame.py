"""测试 intent_router 加载 frame + diagnosis/table_query 路由"""

import pytest

from app.services.agent import (
    _should_diagnose,
    _classify_table_intent,
    route_intent,
    AgentState,
)


# ======== _should_diagnose ========

def test_should_diagnose_no_data_and_why():
    wm = {"last_result_status": "no_data", "last_target": "api_logs"}
    assert _should_diagnose("为什么没数据", wm) is True


def test_should_diagnose_error_and_why():
    wm = {"last_result_status": "error", "last_target": "scan_records"}
    assert _should_diagnose("怎么回事", wm) is True


def test_should_diagnose_success_and_why():
    wm = {"last_result_status": "success", "last_target": "api_logs"}
    assert _should_diagnose("为什么", wm) is False


def test_should_diagnose_no_wm():
    assert _should_diagnose("为什么", None) is False


def test_should_diagnose_no_data_and_thanks():
    wm = {"last_result_status": "no_data", "last_target": "api_logs"}
    assert _should_diagnose("谢谢", wm) is False


def test_should_diagnose_no_data_and_general_question():
    wm = {"last_result_status": "no_data", "last_target": "api_logs"}
    assert _should_diagnose("最近7天销售额", wm) is False


def test_should_diagnose_no_data_and_diagnosis_patterns():
    wm = {"last_result_status": "no_data"}
    patterns = ["查不到", "没有数据", "为空", "怎么没有", "是不是不对", "为什么查不到", "什么原因"]
    for p in patterns:
        assert _should_diagnose(p, wm) is True, f"Pattern '{p}' should trigger diagnosis"


# ======== _classify_table_intent ========

def test_classify_table_intent_view():
    assert _classify_table_intent("看 api_logs") == "table_query"


def test_classify_table_intent_query():
    assert _classify_table_intent("查 scan_records 表") == "table_query"


def test_classify_table_intent_help_me():
    assert _classify_table_intent("帮我查一下 orders") == "table_query"


def test_classify_table_intent_what_is():
    assert _classify_table_intent("api_logs 是什么") == "schema_help"


def test_classify_table_intent_fields():
    assert _classify_table_intent("scan_records 有哪些字段") == "schema_help"


def test_classify_table_intent_bare_table_name():
    assert _classify_table_intent("api_logs") == "schema_help"


# ======== route_intent ========

def test_route_intent_diagnosis():
    state = AgentState(route="diagnosis")
    assert route_intent(state) == "diagnosis"


def test_route_intent_table_query():
    state = AgentState(route="table_query")
    assert route_intent(state) == "table_query"


def test_route_intent_data_query():
    state = AgentState(route="data_query")
    assert route_intent(state) == "query"


def test_route_intent_follow_up():
    state = AgentState(route="follow_up")
    assert route_intent(state) == "follow_up"


def test_route_intent_plan_execute():
    state = AgentState(route="plan_execute")
    assert route_intent(state) == "plan_execute"


def test_route_intent_chat():
    state = AgentState(route="chat")
    assert route_intent(state) == "chat"


def test_route_intent_help():
    state = AgentState(route="help")
    assert route_intent(state) == "help"


def test_route_intent_schema_help():
    state = AgentState(route="schema_help")
    assert route_intent(state) == "schema_help"
