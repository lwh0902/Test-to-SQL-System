from __future__ import annotations

import os
import re
from contextlib import contextmanager
from dataclasses import dataclass
from typing import Iterator

import pymysql

from .schema_factory import SchemaSuite


class SemanticGateInfrastructureError(RuntimeError):
    pass


@dataclass(frozen=True)
class MySQLSettings:
    host: str
    port: int
    user: str
    password: str


def settings() -> MySQLSettings:
    return MySQLSettings(
        host=os.getenv("DB_HOST", "127.0.0.1"),
        port=int(os.getenv("DB_PORT") or 3306),
        user=os.getenv("DB_USER", "root"),
        password=os.getenv("DB_PASSWORD", ""),
    )


def require_mysql() -> MySQLSettings:
    cfg = settings()
    try:
        conn = pymysql.connect(
            host=cfg.host,
            port=cfg.port,
            user=cfg.user,
            password=cfg.password,
            connect_timeout=3,
        )
        conn.close()
    except Exception as exc:
        raise SemanticGateInfrastructureError(f"mandatory MySQL unavailable: {exc}") from exc
    return cfg


def _statements(sql: str) -> list[str]:
    statements: list[str] = []
    buf: list[str] = []
    for line in sql.splitlines():
        if not line.strip():
            continue
        buf.append(line)
        if line.rstrip().endswith(";"):
            statements.append("\n".join(buf))
            buf = []
    if buf:
        statements.append("\n".join(buf))
    return statements


@contextmanager
def provision_suite(suite: SchemaSuite) -> Iterator[tuple[MySQLSettings, str]]:
    cfg = require_mysql()
    db = f"dp_semgate_{suite.family}_{suite.seed}"
    if not re.fullmatch(r"dp_semgate_[a-z]+_[0-9]+", db):
        raise ValueError(f"unsafe semantic-gate database name: {db}")
    conn = pymysql.connect(
        host=cfg.host,
        port=cfg.port,
        user=cfg.user,
        password=cfg.password,
        autocommit=True,
    )
    try:
        cur = conn.cursor()
        cur.execute(f"DROP DATABASE IF EXISTS `{db}`")
        cur.execute(f"CREATE DATABASE `{db}` CHARACTER SET utf8mb4")
        cur.execute(f"USE `{db}`")
        for statement in _statements(suite.ddl + "\n" + suite.seed_sql):
            cur.execute(statement)
        yield cfg, db
    finally:
        try:
            conn.cursor().execute(f"DROP DATABASE IF EXISTS `{db}`")
        finally:
            conn.close()

