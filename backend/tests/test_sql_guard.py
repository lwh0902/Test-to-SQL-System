"""SQL Guard 测试用例

安全规则：
- 只允许 SELECT
- 禁止多语句
- 禁止 SELECT *
- 禁止敏感字段 (phone, id_card, password)
- 禁止 DDL/DML (INSERT, UPDATE, DELETE, DROP, ALTER, CREATE, TRUNCATE)
- 必须 LIMIT
- 禁止访问未授权表
- 禁止危险函数 (LOAD_FILE, INTO OUTFILE, BENCHMARK, SLEEP)
"""

import pytest

from app.guards.sql_guard import SQLGuard, GuardResult


@pytest.fixture
def guard():
    return SQLGuard(
        permitted_tables={"scan_records", "feature_events", "api_logs"},
        sensitive_fields={"phone", "id_card", "password"},
        max_limit=10000,
    )


# ======== 通过用例 ========


class TestSQLGuardPass:
    def test_simple_select_with_limit(self, guard):
        result = guard.check("SELECT id, status FROM scan_records WHERE id = 1 LIMIT 100")
        assert result.passed is True

    def test_select_with_date_filter(self, guard):
        result = guard.check(
            "SELECT DATE(created_at) AS dt, COUNT(*) AS cnt "
            "FROM scan_records WHERE created_at >= '2024-05-01' "
            "AND created_at < '2024-05-22' GROUP BY dt ORDER BY dt ASC LIMIT 100"
        )
        assert result.passed is True

    def test_select_with_join(self, guard):
        result = guard.check(
            "SELECT sr.id, sr.status FROM scan_records sr "
            "JOIN api_logs al ON sr.user_id = al.user_id "
            "WHERE sr.created_at >= '2024-05-01' LIMIT 50"
        )
        assert result.passed is True

    def test_select_with_subquery(self, guard):
        result = guard.check(
            "SELECT error_type, COUNT(*) AS cnt FROM scan_records "
            "WHERE status = 'failed' AND error_type IS NOT NULL "
            "AND created_at >= (SELECT DATE_SUB(NOW(), INTERVAL 7 DAY)) "
            "GROUP BY error_type LIMIT 20"
        )
        assert result.passed is True

    def test_select_aggregation(self, guard):
        result = guard.check(
            "SELECT AVG(response_time_ms) AS avg_ms, MAX(response_time_ms) AS max_ms "
            "FROM api_logs WHERE created_at >= '2024-05-01' LIMIT 1"
        )
        assert result.passed is True

    def test_select_case_when(self, guard):
        result = guard.check(
            "SELECT ROUND(SUM(CASE WHEN status = 'success' THEN 1 ELSE 0 END) * 100.0 "
            "/ NULLIF(COUNT(*), 0), 2) AS success_rate FROM scan_records LIMIT 1"
        )
        assert result.passed is True

    def test_select_all_permitted_tables(self, guard):
        for table in ["scan_records", "feature_events", "api_logs"]:
            result = guard.check(f"SELECT id FROM {table} LIMIT 10")
            assert result.passed is True, f"Should allow SELECT from {table}"

    def test_select_with_parameterized_values(self, guard):
        result = guard.check(
            "SELECT id, status FROM scan_records WHERE created_at >= :start_time LIMIT 100"
        )
        assert result.passed is True

    def test_select_with_alias_no_star(self, guard):
        result = guard.check(
            "SELECT sr.id, sr.status, sr.scan_type FROM scan_records AS sr LIMIT 50"
        )
        assert result.passed is True


# ======== 拦截用例 ========


class TestSQLGuardReject:
    def test_reject_update(self, guard):
        result = guard.check("UPDATE users SET name = 'hacked' WHERE id = 1")
        assert result.passed is False
        assert result.code == "DANGEROUS_OPERATION"

    def test_reject_delete(self, guard):
        result = guard.check("DELETE FROM scan_records WHERE id = 1")
        assert result.passed is False
        assert result.code == "DANGEROUS_OPERATION"

    def test_reject_drop(self, guard):
        result = guard.check("DROP TABLE scan_records")
        assert result.passed is False
        assert result.code == "DANGEROUS_OPERATION"

    def test_reject_insert(self, guard):
        result = guard.check("INSERT INTO scan_records (id, status) VALUES (1, 'success')")
        assert result.passed is False
        assert result.code == "DANGEROUS_OPERATION"

    def test_reject_alter(self, guard):
        result = guard.check("ALTER TABLE scan_records ADD COLUMN test VARCHAR(100)")
        assert result.passed is False
        assert result.code == "DANGEROUS_OPERATION"

    def test_reject_select_star(self, guard):
        result = guard.check("SELECT * FROM scan_records LIMIT 100")
        assert result.passed is False
        assert result.code == "SELECT_STAR_DENIED"

    def test_reject_select_star_with_alias(self, guard):
        result = guard.check("SELECT sr.* FROM scan_records sr LIMIT 100")
        assert result.passed is False
        assert result.code == "SELECT_STAR_DENIED"

    def test_reject_sensitive_field_phone(self, guard):
        result = guard.check("SELECT phone FROM scan_records LIMIT 100")
        assert result.passed is False
        assert result.code == "SENSITIVE_FIELD_DENIED"

    def test_reject_sensitive_field_id_card(self, guard):
        result = guard.check("SELECT id_card FROM scan_records LIMIT 100")
        assert result.passed is False
        assert result.code == "SENSITIVE_FIELD_DENIED"

    def test_reject_no_limit(self, guard):
        result = guard.check("SELECT id, status FROM scan_records WHERE id = 1")
        assert result.passed is False
        assert result.code == "NO_LIMIT"

    def test_reject_multi_statement(self, guard):
        result = guard.check(
            "SELECT id FROM scan_records LIMIT 1; DELETE FROM scan_records"
        )
        assert result.passed is False
        assert result.code == "MULTI_STATEMENT"

    def test_reject_unpermitted_table(self, guard):
        result = guard.check("SELECT id FROM users LIMIT 10")
        assert result.passed is False
        assert result.code == "TABLE_NOT_PERMITTED"

    def test_reject_truncate(self, guard):
        result = guard.check("TRUNCATE TABLE scan_records")
        assert result.passed is False
        assert result.code == "DANGEROUS_OPERATION"

    def test_reject_sql_injection_comment(self, guard):
        result = guard.check(
            "SELECT * FROM scan_records WHERE 1=1; DROP TABLE scan_records --"
        )
        assert result.passed is False

    def test_reject_into_outfile(self, guard):
        result = guard.check(
            "SELECT id, status FROM scan_records INTO OUTFILE '/tmp/data.csv' LIMIT 10"
        )
        assert result.passed is False

    def test_reject_load_file(self, guard):
        result = guard.check(
            "SELECT LOAD_FILE('/etc/passwd') FROM scan_records LIMIT 1"
        )
        assert result.passed is False
        assert result.code == "DANGEROUS_FUNCTION"

    def test_reject_sleep(self, guard):
        result = guard.check(
            "SELECT SLEEP(5) FROM scan_records LIMIT 1"
        )
        assert result.passed is False
        assert result.code == "DANGEROUS_FUNCTION"

    def test_reject_benchmark(self, guard):
        result = guard.check(
            "SELECT BENCHMARK(10000000, SHA1('test')) FROM scan_records LIMIT 1"
        )
        assert result.passed is False
        assert result.code == "DANGEROUS_FUNCTION"
