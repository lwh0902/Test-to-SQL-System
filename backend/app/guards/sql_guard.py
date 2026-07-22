"""SQL Guard - 基于 sqlglot AST 的 SQL 安全校验

安全规则：
1. 只允许 SELECT
2. 禁止多语句
3. 禁止 SELECT *
4. 禁止敏感字段
5. 禁止 DDL/DML
6. 必须 LIMIT
7. 禁止访问未授权表
8. 禁止危险函数
"""

from dataclasses import dataclass

import sqlglot
from sqlglot import exp


SENSITIVE_FIELD_PATTERNS = {"phone", "mobile", "email", "id_card", "password", "secret", "token", "credit_card"}

DANGEROUS_FUNCTIONS = {"LOAD_FILE", "BENCHMARK", "SLEEP", "GET_LOCK", "RELEASE_LOCK"}
DANGEROUS_STATEMENTS = {
    exp.Insert, exp.Update, exp.Delete, exp.Drop,
    exp.Alter, exp.Create, exp.TruncateTable,
}


@dataclass
class GuardResult:
    passed: bool
    code: str | None = None
    message: str | None = None
    detail: str | None = None


class FreeformSQLGuard:
    """自由模式 SQL 守卫 — 用于用户空间（无预定义指标模板）

    通用安全规则（不限制表名）：
    1. 只允许 SELECT
    2. 禁止多语句
    3. 禁止 SELECT *
    4. 禁止 DDL/DML
    5. 必须 LIMIT（自动追加，最大 500）
    6. 禁止危险函数
    7. 禁止敏感字段（phone/password/id_card 等）
    8. 大表无 WHERE 拦截
    """

    MAX_LIMIT = 500

    def __init__(self, permitted_tables: set[str] | None = None, permitted_columns: dict[str, set[str]] | None = None):
        self.permitted_tables = {table.lower() for table in (permitted_tables or set())}
        self.permitted_columns = {
            table.lower(): {column.lower() for column in columns}
            for table, columns in (permitted_columns or {}).items()
        }

    def check(self, sql: str) -> GuardResult:
        try:
            statements = sqlglot.parse(sql, dialect="mysql")
        except sqlglot.errors.ParseError as e:
            return GuardResult(False, "PARSE_ERROR", "SQL 解析失败", str(e))

        statements = [s for s in statements if s is not None]
        if len(statements) > 1:
            return GuardResult(False, "MULTI_STATEMENT", "禁止多语句执行")
        if len(statements) == 0:
            return GuardResult(False, "EMPTY_SQL", "SQL 为空")

        stmt = statements[0]

        if not isinstance(stmt, exp.Select):
            if any(isinstance(stmt, t) for t in DANGEROUS_STATEMENTS):
                return GuardResult(False, "DANGEROUS_OPERATION", f"禁止执行 {type(stmt).__name__} 操作")
            return GuardResult(False, "NOT_SELECT", "只允许 SELECT 查询")

        # 自由查询缺少可验证的别名/血缘模型时，复杂查询一律拒绝而非猜测授权。
        if stmt.find(exp.Join) or stmt.find(exp.Subquery) or stmt.args.get("with"):
            return GuardResult(False, "COMPLEX_QUERY_DENIED", "自由查询暂不支持 JOIN、子查询或 CTE")

        has_where = bool(stmt.find(exp.Where))

        for node in stmt.walk():
            if any(isinstance(node, statement_type) for statement_type in DANGEROUS_STATEMENTS):
                return GuardResult(False, "DANGEROUS_OPERATION", "禁止嵌套写操作")
            if isinstance(node, exp.Anonymous) and node.name.upper() in DANGEROUS_FUNCTIONS:
                return GuardResult(False, "DANGEROUS_FUNCTION", f"禁止使用函数 {node.name.upper()}")

            if isinstance(node, exp.Column):
                col_name = node.name.lower()
                if col_name in SENSITIVE_FIELD_PATTERNS:
                    return GuardResult(False, "SENSITIVE_FIELD_DENIED", f"禁止访问敏感字段: {col_name}")
                if self.permitted_columns:
                    table_name = (node.table or "").lower()
                    allowed = self.permitted_columns.get(table_name)
                    if allowed is None and len(self.permitted_columns) == 1:
                        allowed = next(iter(self.permitted_columns.values()))
                    if allowed is not None and col_name not in allowed:
                        return GuardResult(False, "COLUMN_NOT_PERMITTED", f"无权访问字段: {col_name}")

        if self._has_select_star(stmt):
            return GuardResult(False, "SELECT_STAR_DENIED", "禁止 SELECT *，请明确列出查询字段")

        if self.permitted_tables:
            for table in stmt.find_all(exp.Table):
                if table.db or table.catalog or table.name.lower() not in self.permitted_tables:
                    return GuardResult(False, "TABLE_NOT_PERMITTED", "无权访问表")

        if not has_where and not stmt.find(exp.Limit):
            return GuardResult(False, "NO_WHERE_NO_LIMIT", "查询缺少 WHERE 条件且无 LIMIT，可能扫描全表")

        limit = stmt.find(exp.Limit)
        try:
            if limit and int(limit.expression.this) > self.MAX_LIMIT:
                return GuardResult(False, "LIMIT_EXCEEDED", f"LIMIT 不能超过 {self.MAX_LIMIT}")
        except (ValueError, TypeError, AttributeError):
            return GuardResult(False, "INVALID_LIMIT", "LIMIT 必须是正整数")

        return GuardResult(True)

    def ensure_limit(self, sql: str) -> str:
        """如果 SQL 没有 LIMIT，自动追加"""
        try:
            statements = sqlglot.parse(sql, dialect="mysql")
            stmt = [s for s in statements if s is not None][0]
            if isinstance(stmt, exp.Select) and not stmt.find(exp.Limit):
                return f"{sql.rstrip(';')} LIMIT {self.MAX_LIMIT}"
        except Exception:
            pass
        return sql

    def _has_select_star(self, stmt: exp.Select) -> bool:
        for select_expr in stmt.expressions:
            if isinstance(select_expr, exp.Star):
                return True
            inner = select_expr
            if isinstance(inner, exp.Alias):
                inner = inner.this
            if isinstance(inner, (exp.Count, exp.Sum, exp.Avg, exp.Min, exp.Max)):
                continue
            if isinstance(inner, exp.Star):
                return True
            if isinstance(inner, exp.Column) and isinstance(inner.this, exp.Star):
                return True
        return False


class SQLGuard:
    def __init__(
        self,
        permitted_tables: set[str],
        sensitive_fields: set[str],
        max_limit: int = 10000,
    ):
        self.permitted_tables = {t.lower() for t in permitted_tables}
        self.sensitive_fields = {f.lower() for f in sensitive_fields}
        self.max_limit = max_limit

    def check(self, sql: str) -> GuardResult:
        # 1. 解析 SQL，检查多语句
        try:
            statements = sqlglot.parse(sql, dialect="mysql")
        except sqlglot.errors.ParseError as e:
            return GuardResult(False, "PARSE_ERROR", "SQL 解析失败", str(e))

        statements = [s for s in statements if s is not None]
        if len(statements) > 1:
            return GuardResult(False, "MULTI_STATEMENT", "禁止多语句执行")
        if len(statements) == 0:
            return GuardResult(False, "EMPTY_SQL", "SQL 为空")

        stmt = statements[0]

        # 2. 检查语句类型 - 只允许 SELECT
        if not isinstance(stmt, exp.Select):
            if any(isinstance(stmt, t) for t in DANGEROUS_STATEMENTS):
                return GuardResult(False, "DANGEROUS_OPERATION", f"禁止执行 {type(stmt).__name__} 操作")
            return GuardResult(False, "NOT_SELECT", "只允许 SELECT 查询")

        # 3. 遍历 AST 检查各规则
        for node in stmt.walk():
            # 检查危险函数（Anonymous 节点覆盖 SLEEP/BENCHMARK/LOAD_FILE）
            if isinstance(node, exp.Anonymous) and node.name.upper() in DANGEROUS_FUNCTIONS:
                return GuardResult(
                    False, "DANGEROUS_FUNCTION",
                    f"禁止使用函数 {node.name.upper()}",
                    str(node),
                )

            # 检查敏感字段
            if isinstance(node, exp.Column):
                col_name = node.name.lower()
                if col_name in self.sensitive_fields:
                    return GuardResult(
                        False, "SENSITIVE_FIELD_DENIED",
                        f"禁止访问敏感字段: {col_name}",
                        f"field: {col_name}",
                    )

        # 4. 检查 SELECT *
        if self._has_select_star(stmt):
            return GuardResult(False, "SELECT_STAR_DENIED", "禁止 SELECT *")

        # 5. 提取并检查表名
        tables = self._extract_tables(stmt)
        for table in tables:
            if table.lower() not in self.permitted_tables:
                return GuardResult(
                    False, "TABLE_NOT_PERMITTED",
                    f"无权访问表: {table}",
                    f"table: {table}",
                )

        # 6. 检查 LIMIT
        if not stmt.find(exp.Limit):
            return GuardResult(False, "NO_LIMIT", "查询必须包含 LIMIT 子句")

        limit = stmt.find(exp.Limit)
        try:
            value = int(limit.expression.this)
            if value > self.max_limit:
                return GuardResult(False, "LIMIT_EXCEEDED", f"LIMIT 不能超过 {self.max_limit}")
        except (TypeError, ValueError, AttributeError):
            return GuardResult(False, "INVALID_LIMIT", "LIMIT 必须是正整数")

        return GuardResult(True)

    def _has_select_star(self, stmt: exp.Select) -> bool:
        for select_expr in stmt.expressions:
            # 裸 SELECT *
            if isinstance(select_expr, exp.Star):
                return True
            # Alias 包裹的情况：展开 alias 检查内部
            inner = select_expr
            if isinstance(inner, exp.Alias):
                inner = inner.this
            # inner 如果是聚合函数（COUNT/SUM 等），里面的 * 是合法的
            if isinstance(inner, (exp.Count, exp.Sum, exp.Avg, exp.Min, exp.Max)):
                continue
            # 如果内部是裸 Star（如 SELECT sr.*）
            if isinstance(inner, exp.Star):
                return True
            # Column 包含 Star（如 table.*）
            if isinstance(inner, exp.Column) and isinstance(inner.this, exp.Star):
                return True
        return False

    def _extract_tables(self, stmt: exp.Select) -> set[str]:
        tables = set()
        for table_node in stmt.find_all(exp.Table):
            table_name = table_node.name
            if table_name:
                tables.add(table_name)
        return tables
