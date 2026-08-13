from contextlib import contextmanager

from app.services import authorized_connection_service as connections


class Result:
    def __init__(self, row):
        self._row = row

    def fetchone(self):
        return self._row


class Connection:
    def __init__(self, row):
        self.row = row
        self.calls = []

    def execute(self, statement, params):
        self.calls.append((str(statement), params))
        return Result(self.row)


class Engine:
    def __init__(self, row):
        self.connection = Connection(row)

    @contextmanager
    def connect(self):
        yield self.connection


def test_private_space_resolves_only_its_owners_active_connection(monkeypatch):
    engine = Engine(("private", "conn-1", "db.example", 3306, "reader", "cipher", "sales"))
    monkeypatch.setattr(connections, "engine", engine)
    monkeypatch.setattr(connections, "decrypt_password", lambda value: f"plain:{value}")
    monkeypatch.setattr(connections, "connection_target_allowed", lambda _: True)

    resolved = connections.resolve_authorized_connection(user_id=7, space_id="private-space")

    assert resolved == {
        "host": "db.example", "port": 3306, "user": "reader",
        "password": "plain:cipher", "database": "sales",
    }
    sql, params = engine.connection.calls[0]
    assert "dc.user_id = sp.user_id" in sql
    assert "dc.status = 'active'" in sql
    assert params == {"space_id": "private-space", "user_id": 7}


def test_private_space_without_owned_active_connection_fails_closed(monkeypatch):
    monkeypatch.setattr(connections, "engine", Engine(None))

    try:
        connections.resolve_authorized_connection(user_id=8, space_id="private-space")
    except connections.ConnectionUnavailable as error:
        assert str(error) == "data_source_unavailable"
    else:
        raise AssertionError("missing authorized connection must not fall back")


def test_private_space_rejects_preexisting_connection_outside_egress_policy(monkeypatch):
    engine = Engine(("private", "conn-1", "169.254.169.254", 3306, "reader", "cipher", "sales"))
    monkeypatch.setattr(connections, "engine", engine)
    monkeypatch.setattr(connections, "connection_target_allowed", lambda _: False, raising=False)

    try:
        connections.resolve_authorized_connection(user_id=7, space_id="private-space")
    except connections.ConnectionUnavailable as error:
        assert str(error) == "data_source_unavailable"
    else:
        raise AssertionError("old connection outside policy must not be used")


def test_public_space_resolves_platform_managed_connection(monkeypatch):
    engine = Engine(("public", None, "managed.example", 3306, "readonly", "cipher", "travel"))
    monkeypatch.setattr(connections, "engine", engine)
    monkeypatch.setattr(connections, "decrypt_password", lambda value: value)

    resolved = connections.resolve_authorized_connection(user_id=9, space_id="travel_b2b")

    assert resolved["host"] == "managed.example"
    sql, params = engine.connection.calls[0]
    assert "managed_space_connections" in sql
    assert params == {"space_id": "travel_b2b", "user_id": 9}
