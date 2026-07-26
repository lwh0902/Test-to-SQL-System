from __future__ import annotations

import hashlib
import random
from dataclasses import dataclass, field

from .contracts import (
    FilterExpectation,
    MeasureExpectation,
    SemanticExpectation,
)


@dataclass(frozen=True)
class FamilyDefinition:
    family: str
    fact: str
    dimension: str
    value: str
    status: str
    time: str
    group: str
    business_subject: str
    failed_value: str
    table_comment: str


@dataclass
class SchemaSuite:
    family: str
    seed: int
    ddl: str
    seed_sql: str
    identifiers: dict[str, str]
    single_turn_cases: list[SemanticExpectation] = field(default_factory=list)
    followup_chains: list[tuple[SemanticExpectation, ...]] = field(default_factory=list)
    ground_truth: dict[str, float | int | str] = field(default_factory=dict)
    sensitive_values: tuple[str, ...] = ()

    def physical(self, logical: str) -> str:
        return self.identifiers[logical]


FAMILIES = (
    FamilyDefinition("commerce", "orders", "customers", "amount", "status", "created_at", "category", "订单", "failed", "订单交易事实"),
    FamilyDefinition("billing", "invoices", "plans", "total_due", "state", "issued_at", "plan_code", "账单", "overdue", "订阅账单事实"),
    FamilyDefinition("support", "tickets", "agents", "resolution_minutes", "state", "opened_at", "priority", "工单", "failed", "客户支持工单"),
    FamilyDefinition("iot", "readings", "devices", "reading_value", "alarm_state", "recorded_at", "site_code", "设备读数", "alarm", "设备遥测读数"),
    FamilyDefinition("warehouse", "stock_moves", "products", "quantity", "direction", "moved_at", "sku_group", "库存流水", "out", "库存变动流水"),
    FamilyDefinition("ambiguous", "journal_a", "journal_b", "metric_x", "flag_x", "ts_x", "bucket_x", "业务记录", "bad", ""),
)


def _token(seed: int, family: str, logical: str) -> str:
    raw = f"{seed}:{family}:{logical}".encode()
    return hashlib.sha256(raw).hexdigest()[:7]


def _identifiers(defn: FamilyDefinition, seed: int) -> dict[str, str]:
    logical = {
        "fact": defn.fact,
        "dimension_table": defn.dimension,
        "id": "id",
        "dimension_id": "dimension_id",
        "value": defn.value,
        "status": defn.status,
        "time": defn.time,
        "group": defn.group,
        "phone": "contact_phone",
        "decoy": "activity_log",
        "decoy_id": "log_id",
        "decoy_value": "score_value",
        "decoy_status": "log_state",
        "decoy_time": "logged_at",
    }
    out: dict[str, str] = {}
    for role, base in logical.items():
        suffix = _token(seed, defn.family, role)[:4]
        if defn.family in {"iot", "ambiguous"}:
            out[role] = f"x_{suffix}"
        elif defn.family == "warehouse":
            cn = {
                "fact": "库存流水",
                "dimension_table": "商品主数据",
                "id": "记录编号",
                "dimension_id": "商品编号",
                "value": "变动数量",
                "status": "出入方向",
                "time": "发生时间",
                "group": "商品分组",
                "phone": "联系人手机",
                "decoy": "盘点日志",
                "decoy_id": "日志编号",
                "decoy_value": "盘点得分",
                "decoy_status": "日志状态",
                "decoy_time": "日志时间",
            }[role]
            out[role] = f"{cn}_{suffix}"
        else:
            out[role] = f"{base}_{suffix}"
    return out


def _ddl(defn: FamilyDefinition, ids: dict[str, str]) -> str:
    comment = defn.table_comment.replace("'", "''")
    second_comment = "辅助维度" if defn.family != "ambiguous" else ""
    dimension = f"""CREATE TABLE `{ids['dimension_table']}` (
  `{ids['dimension_id']}` BIGINT NOT NULL COMMENT '{second_comment}',
  `label` VARCHAR(64) NOT NULL,
  PRIMARY KEY (`{ids['dimension_id']}`)
);"""
    columns = [
        f"`{ids['dimension_id']}` BIGINT NULL",
        f"`{ids['value']}` DECIMAL(12,2) NOT NULL",
        f"`{ids['status']}` VARCHAR(32) NOT NULL",
        f"`{ids['time']}` DATETIME NOT NULL",
        f"`{ids['group']}` VARCHAR(32) NOT NULL",
        f"`{ids['phone']}` VARCHAR(32) NULL",
        f"`noise_{_token(19, defn.family, 'noise')[:4]}` VARCHAR(20) NULL",
    ]
    random.Random(sum(ord(ch) for ch in ids["fact"])).shuffle(columns)
    constraints = [
        f"PRIMARY KEY (`{ids['id']}`)",
        f"KEY `idx_time` (`{ids['time']}`)",
        f"KEY `idx_status` (`{ids['status']}`)",
    ]
    if defn.family in {"commerce", "support", "warehouse"}:
        constraints.append(
            f"CONSTRAINT `fk_{_token(7, defn.family, 'fk')}` FOREIGN KEY (`{ids['dimension_id']}`) "
            f"REFERENCES `{ids['dimension_table']}` (`{ids['dimension_id']}`)"
        )
    fact_defs = [f"`{ids['id']}` BIGINT NOT NULL COMMENT '{comment}'", *columns, *constraints]
    fact = f"CREATE TABLE `{ids['fact']}` (\n  " + ",\n  ".join(fact_defs) + "\n);"
    decoy = f"""CREATE TABLE `{ids['decoy']}` (
  `{ids['decoy_id']}` BIGINT NOT NULL COMMENT 'unrelated operational log',
  `{ids['decoy_value']}` DECIMAL(12,2) NOT NULL,
  `{ids['decoy_status']}` VARCHAR(32) NOT NULL,
  `{ids['decoy_time']}` DATETIME NOT NULL,
  PRIMARY KEY (`{ids['decoy_id']}`)
);"""
    parts = [dimension, fact, decoy]
    if sum(ord(ch) for ch in ids["decoy"]) % 2:
        parts = [decoy, dimension, fact]
    return "\n".join(parts)


def _seed_sql(defn: FamilyDefinition, ids: dict[str, str], *, reverse: bool) -> str:
    dim = (
        f"INSERT INTO `{ids['dimension_table']}` (`{ids['dimension_id']}`, `label`) "
        "VALUES (1, 'A'), (2, 'B'), (3, 'C');"
    )
    rows = []
    for i in range(1, 121):
        status = defn.failed_value if i > 110 else "ok"
        amount = i
        group = ("A", "B", "C")[(i - 1) % 3]
        phone = "13800138000" if i == 1 else "NULL"
        phone_sql = f"'{phone}'" if phone != "NULL" else "NULL"
        rows.append(
            f"({i}, {(i - 1) % 3 + 1}, {amount}, '{status}', "
            f"'2025-01-{((i - 1) % 28) + 1:02d} 12:00:00', '{group}', {phone_sql})"
        )
    if reverse:
        rows.reverse()
    insert = (
        f"INSERT INTO `{ids['fact']}` (`{ids['id']}`, `{ids['dimension_id']}`, `{ids['value']}`, "
        f"`{ids['status']}`, `{ids['time']}`, `{ids['group']}`, `{ids['phone']}`) VALUES\n"
        + ",\n".join(rows)
        + ";"
    )
    decoy_rows = ", ".join(
        f"({i}, {1000 + i}, 'ok', '2025-02-{i:02d} 00:00:00')" for i in range(1, 8)
    )
    decoy = (
        f"INSERT INTO `{ids['decoy']}` (`{ids['decoy_id']}`, `{ids['decoy_value']}`, "
        f"`{ids['decoy_status']}`, `{ids['decoy_time']}`) VALUES {decoy_rows};"
    )
    return dim + "\n" + insert + "\n" + decoy


def _cases(defn: FamilyDefinition, ids: dict[str, str]) -> list[SemanticExpectation]:
    fact = ids["fact"]
    count_phrases = (
        f"{fact} 一共有多少条记录？",
        f"请统计表 {fact} 的行数",
        f"count rows in {fact}",
        f"{defn.business_subject}总数是多少？",
        f"目前共有多少{defn.business_subject}？",
    )
    sum_phrases = (
        f"{fact} 的 {ids['value']} 合计是多少？",
        f"汇总{defn.business_subject}的{ids['value']}",
        f"sum {ids['value']} from {fact}",
        f"{defn.business_subject}总金额或总值是多少？",
        f"计算全部{defn.business_subject}的数值总和",
    )
    failed_phrases = (
        f"{fact} 中 {ids['status']}={defn.failed_value} 的有多少？",
        f"失败的{defn.business_subject}有多少？",
        f"统计{defn.failed_value}状态的{defn.business_subject}数量",
        f"{defn.business_subject}失败率是多少？",
    )
    group_phrases = (
        f"按 {ids['group']} 看{defn.business_subject}数量前3名",
        f"{defn.business_subject}按分组排名前3",
        f"group {fact} by {ids['group']} top 3",
    )
    time_phrases = (
        f"2025年1月{defn.business_subject}数量",
        f"2025-01-01到2025-01-31的{defn.business_subject}总值",
        f"最近的{defn.business_subject}趋势",
    )
    questions = count_phrases + sum_phrases + failed_phrases + group_phrases + time_phrases
    cases: list[SemanticExpectation] = []
    for index, question in enumerate(questions):
        measures = (MeasureExpectation("count", ids["id"], fact),)
        filters: tuple[FilterExpectation, ...] = ()
        dimensions: tuple[str, ...] = ()
        order: tuple[str, ...] = ()
        limit = None
        values: dict[str, float | int | str] = {}
        behavior = "answer"
        slots: tuple[str, ...] = ()
        if index < 5:
            values = {"value": 120}
        if 5 <= index < 10:
            measures = (MeasureExpectation("sum", ids["value"], fact),)
            values = {"value": 7260}
        elif 10 <= index < 14:
            filters = (FilterExpectation(ids["status"], defn.failed_value, table=fact),)
            values = {"value": 10}
            if index == 13:
                measures = (MeasureExpectation("rate", ids["id"], fact),)
                values = {"value": 10 / 120}
        elif 14 <= index < 17:
            dimensions = (ids["group"],)
            order = ("desc",)
            limit = 3
        elif index == 17:
            values = {"value": 120}
        elif index == 18:
            measures = (MeasureExpectation("sum", ids["value"], fact),)
            values = {"value": 7260}
        elif index == 19:
            behavior = "clarify"
            slots = ("time_range",)
            measures = ()
        if defn.family == "ambiguous" and not question.startswith(fact) and fact not in question:
            behavior = "clarify"
            slots = ("subject",)
            measures = ()
            filters = ()
            dimensions = ()
            order = ()
            limit = None
            values = {}
        cases.append(
            SemanticExpectation(
                case_id=f"{defn.family}_single_{index:02d}",
                family=defn.family,
                question=question,
                behavior=behavior,
                subject_table=fact if behavior == "answer" else None,
                sql_tables=frozenset({fact}) if behavior == "answer" else frozenset(),
                measures=measures,
                filters=filters,
                dimensions=dimensions,
                order_by=order,
                limit=limit,
                expected_values=values,
                clarify_slots=slots,
            )
        )
    return cases


def _chains(defn: FamilyDefinition, ids: dict[str, str]) -> list[tuple[SemanticExpectation, ...]]:
    fact = ids["fact"]
    followups = (
        ("其中失败的有多少？", (FilterExpectation(ids["status"], defn.failed_value, table=fact),), (), False),
        (f"按{ids['group']}拆开", (), (ids["group"],), False),
        ("那失败率呢？", (FilterExpectation(ids["status"], defn.failed_value, table=fact),), (), False),
        ("前3名", (), (), False),
        ("只看2025年1月", (), (), False),
        ("再查一次完全相同的结果", (), (), True),
        ("总数和失败数都给我", (FilterExpectation(ids["status"], defn.failed_value, table=fact),), (), False),
    )
    chains = []
    for index, (followup, filters, dims, reuse) in enumerate(followups):
        first = SemanticExpectation(
            case_id=f"{defn.family}_chain_{index:02d}_1",
            family=defn.family,
            question=f"{fact} 一共有多少条记录？",
            subject_table=fact,
            sql_tables=frozenset({fact}),
            measures=(MeasureExpectation("count", ids["id"], fact),),
        )
        second = SemanticExpectation(
            case_id=f"{defn.family}_chain_{index:02d}_2",
            family=defn.family,
            question=followup,
            subject_table=fact,
            sql_tables=frozenset({fact}),
            measures=(MeasureExpectation("count", ids["id"], fact),),
            filters=filters,
            dimensions=dims,
            limit=3 if followup == "前3名" else None,
            expect_reuse=reuse,
        )
        chains.append((first, second))
    return chains


def build_schema_suites(seed: int = 20260726) -> list[SchemaSuite]:
    rng = random.Random(seed)
    suites = []
    for defn in FAMILIES:
        family_seed = rng.randrange(1, 2**31)
        ids = _identifiers(defn, family_seed)
        suites.append(
            SchemaSuite(
                family=defn.family,
                seed=family_seed,
                ddl=_ddl(defn, ids),
                seed_sql=_seed_sql(defn, ids, reverse=bool(family_seed % 2)),
                identifiers=ids,
                single_turn_cases=_cases(defn, ids),
                followup_chains=_chains(defn, ids),
                ground_truth={"count": 120, "sum": 7260, "failed_count": 10, "failed_rate": 10 / 120},
                sensitive_values=("13800138000",),
            )
        )
    return suites
