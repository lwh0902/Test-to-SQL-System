from app.guards.sql_guard import FreeformSQLGuard


def test_freeform_sql_rejects_table_outside_space_schema():
    guard = FreeformSQLGuard(permitted_tables={"orders"})
    result = guard.check("SELECT id FROM mysql.user LIMIT 1")
    assert not result.passed
    assert result.code == "TABLE_NOT_PERMITTED"


def test_freeform_sql_rejects_columns_outside_schema():
    guard = FreeformSQLGuard(permitted_tables={"orders"}, permitted_columns={"orders": {"id", "amount"}})
    result = guard.check("SELECT name FROM orders LIMIT 1")
    assert not result.passed
    assert result.code == "COLUMN_NOT_PERMITTED"


def test_freeform_sql_rejects_join_and_subquery_until_lineage_policy_exists():
    guard = FreeformSQLGuard(permitted_tables={"orders"}, permitted_columns={"orders": {"id", "amount"}})
    assert guard.check("SELECT o.id FROM orders o JOIN users u ON u.id = o.id LIMIT 1").code == "COMPLEX_QUERY_DENIED"
    assert guard.check("SELECT id FROM (SELECT id FROM orders) x LIMIT 1").code == "COMPLEX_QUERY_DENIED"
