from app.agents.guarded_mysql_executor import GuardedMySQLExecutor


class FakeResult:
    returns_rows = False


class FakeConnection:
    def __init__(self):
        self.calls = []

    def execute(self, statement, params=None):
        self.calls.append((str(statement), params))
        return FakeResult()


class FakeEngine:
    def __init__(self):
        self.connection = FakeConnection()

    def connect(self):
        connection = self.connection

        class Context:
            def __enter__(self):
                return connection

            def __exit__(self, *_):
                return False

        return Context()


def test_executor_passes_bound_params_and_sets_query_timeout():
    engine = FakeEngine()
    executor = GuardedMySQLExecutor(host="db", user="reader", database="x", engine=engine)

    outcome = executor.execute("SELECT 1 WHERE :value = :value LIMIT 1", params={"value": "safe"})

    assert outcome.status.value == "SUCCESS_EMPTY"
    assert "MAX_EXECUTION_TIME" in engine.connection.calls[1][0]
    assert engine.connection.calls[-1][1] == {"value": "safe"}


def test_executor_rejects_file_delay_and_lock_functions_before_database_access():
    engine = FakeEngine()
    executor = GuardedMySQLExecutor(host="db", user="reader", database="x", engine=engine)

    outcome = executor.execute("SELECT SLEEP(2) LIMIT 1")

    assert outcome.status.value == "SQL_REJECTED"
    assert engine.connection.calls == []
