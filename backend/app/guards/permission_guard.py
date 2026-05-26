"""权限 Guard - 业务权限校验

与 SQL Guard 分开，只检查业务权限：
1. 用户角色是否有权查某指标
2. 时间范围是否超限
3. 维度是否合法
"""

from datetime import datetime

from app.models.schemas import QueryIntent, GuardCheckResult


class PermissionGuard:
    def check(self, intent: QueryIntent, user_role: str = "tester", metric_config: dict | None = None) -> GuardCheckResult:
        if not intent.metric:
            return GuardCheckResult(
                passed=False, code="NO_METRIC",
                message="未指定查询指标",
            )

        config = metric_config
        if not config:
            return GuardCheckResult(
                passed=False, code="METRIC_NOT_FOUND",
                message=f"指标不存在: {intent.metric}",
            )

        # 1. 角色权限检查
        allowed_roles = config.get("roles", [])
        if allowed_roles and user_role not in allowed_roles:
            return GuardCheckResult(
                passed=False, code="ROLE_DENIED",
                message=f"角色 {user_role} 无权查看此指标",
                detail=f"required: {allowed_roles}",
            )

        # 2. 查询类型检查
        allowed_types = config.get("allowed_query_types", [])
        if intent.query_type and intent.query_type not in allowed_types:
            return GuardCheckResult(
                passed=False, code="QUERY_TYPE_DENIED",
                message=f"此指标不支持 {intent.query_type} 查询类型",
                detail=f"allowed: {allowed_types}",
            )

        # 3. 时间范围检查
        max_days = config.get("max_time_range_days", 90)
        if intent.time_range:
            try:
                start = datetime.strptime(intent.time_range.start, "%Y-%m-%d")
                end = datetime.strptime(intent.time_range.end, "%Y-%m-%d")
                delta = (end - start).days
                if delta > max_days:
                    return GuardCheckResult(
                        passed=False, code="TIME_RANGE_EXCEEDED",
                        message=f"时间范围不能超过 {max_days} 天",
                        detail=f"requested: {delta} days",
                    )
            except ValueError:
                return GuardCheckResult(
                    passed=False, code="INVALID_TIME_RANGE",
                    message="时间格式无效",
                )

        # 4. 维度检查
        allowed_dims = config.get("allowed_dimensions", [])
        for dim in intent.dimensions:
            if dim not in allowed_dims:
                return GuardCheckResult(
                    passed=False, code="DIMENSION_DENIED",
                    message=f"此指标不支持维度: {dim}",
                    detail=f"allowed: {allowed_dims}",
                )

        return GuardCheckResult(passed=True)
