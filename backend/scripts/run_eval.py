"""评测脚本 - 对 eval_cases.yml 跑 Parser 并计算准确率

用法:
  python scripts/run_eval.py              # 规则 Parser
  USE_LLM_PARSER=true python scripts/run_eval.py  # LLM Parser
"""

import os
import yaml

# 设置 parser 模式
from app.parsers import parse_question


def load_eval_cases(path: str = None) -> list[dict]:
    if path is None:
        import os
        base = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
        path = os.path.join(base, "config", "eval_cases.yml")
    with open(path) as f:
        return yaml.safe_load(f)


def run_eval(cases: list[dict]) -> dict:
    results = []
    correct_metric = 0
    correct_type = 0
    correct_clarify = 0
    correct_filters = 0
    correct_dimensions = 0
    total = len(cases)

    for case in cases:
        question = case["question"]
        intent = parse_question(question)

        result = {"question": question, "passed": True, "errors": []}

        # 检查指标
        expected_metric = case.get("expected_metric")
        if expected_metric is not None:
            if intent.metric != expected_metric:
                result["passed"] = False
                result["errors"].append(
                    f"metric: expected={expected_metric}, got={intent.metric}"
                )
            else:
                correct_metric += 1
        elif intent.metric is not None:
            # 期望不匹配但匹配到了，只有当 should_clarify=true 时才算问题
            pass

        # 检查查询类型
        expected_type = case.get("expected_query_type")
        if expected_type:
            if intent.query_type != expected_type:
                result["passed"] = False
                result["errors"].append(
                    f"query_type: expected={expected_type}, got={intent.query_type}"
                )
            else:
                correct_type += 1

        # 检查是否应该反问
        should_clarify = case.get("should_clarify", False)
        if should_clarify:
            if intent.clarification_reason is None:
                result["passed"] = False
                result["errors"].append("should have clarified but didn't")
            else:
                correct_clarify += 1
        elif intent.clarification_reason is not None:
            if expected_metric is not None:
                result["passed"] = False
                result["errors"].append("clarified when it shouldn't have")

        # 检查过滤条件
        expected_filters = case.get("expected_filters", {})
        if expected_filters:
            for key, val in expected_filters.items():
                if intent.filters.get(key) != val:
                    result["passed"] = False
                    result["errors"].append(
                        f"filter.{key}: expected={val}, got={intent.filters.get(key)}"
                    )
                else:
                    correct_filters += 1

        # 检查维度
        expected_dims = case.get("expected_dimensions", [])
        if expected_dims:
            for dim in expected_dims:
                if dim not in intent.dimensions:
                    result["passed"] = False
                    result["errors"].append(
                        f"dimension: expected {dim}, got={intent.dimensions}"
                    )
                else:
                    correct_dimensions += 1

        results.append(result)

    # 计算指标
    metric_total = sum(1 for c in cases if c.get("expected_metric") is not None)
    type_total = sum(1 for c in cases if c.get("expected_query_type"))
    clarify_total = sum(1 for c in cases if c.get("should_clarify"))
    filter_total = sum(len(c.get("expected_filters", {})) for c in cases)
    dim_total = sum(len(c.get("expected_dimensions", [])) for c in cases)

    passed = sum(1 for r in results if r["passed"])

    return {
        "total": total,
        "passed": passed,
        "accuracy": round(passed / total * 100, 1) if total else 0,
        "metric_accuracy": round(correct_metric / metric_total * 100, 1) if metric_total else 0,
        "type_accuracy": round(correct_type / type_total * 100, 1) if type_total else 0,
        "clarify_accuracy": round(correct_clarify / clarify_total * 100, 1) if clarify_total else 0,
        "results": results,
    }


def main():
    parser_mode = "LLM" if os.getenv("USE_LLM_PARSER", "false").lower() == "true" else "规则"
    cases = load_eval_cases()
    report = run_eval(cases)

    print(f"=== 评测报告 (Parser: {parser_mode}) ===")
    print(f"总用例: {report['total']}")
    print(f"全部通过: {report['passed']}/{report['total']} ({report['accuracy']}%)")
    print(f"指标准确率: {report['metric_accuracy']}%")
    print(f"类型准确率: {report['type_accuracy']}%")
    print(f"反问准确率: {report['clarify_accuracy']}%")
    print()

    failures = [r for r in report["results"] if not r["passed"]]
    if failures:
        print(f"--- 失败用例 ({len(failures)}) ---")
        for f in failures:
            print(f"  Q: {f['question']}")
            for err in f["errors"]:
                print(f"    ✗ {err}")
            print()
    else:
        print("全部通过!")


if __name__ == "__main__":
    main()
