"""L0 write refuse — before LLM Supervisor / schema / data_map (dev_spec §4.6).

Deterministic. No DB. No LLM.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Optional


# SQL verbs (word-boundary-ish; allow leading whitespace / comments stripped)
_SQL_WRITE_RE = re.compile(
    r"(?is)(?:^|;|\b)"
    r"(?:INSERT|UPDATE|DELETE|DROP|ALTER|TRUNCATE|REPLACE|CREATE|GRANT|REVOKE)\b"
)

# High-confidence Chinese / mixed write intents
# Avoid matching analysis nouns like 删除率 / 写入量 — require write object or imperative.
_CN_WRITE_PATTERNS = (
    re.compile(r"(?i)帮我执行\s*[:：]?\s*(INSERT|UPDATE|DELETE|DROP|ALTER|TRUNCATE|CREATE)"),
    re.compile(r"(?i)执行\s*[:：]?\s*(INSERT|UPDATE|DELETE|DROP|ALTER|TRUNCATE|CREATE)\b"),
    re.compile(
        r"(?i)\b(INSERT|UPDATE|DELETE|DROP|ALTER|TRUNCATE|REPLACE|CREATE|GRANT|REVOKE)\b"
        r"(?:\s+(?:INTO|FROM|TABLE|INDEX|DATABASE|USER|IF)\b|\s+[`\"\w])"
    ),
    # 删除/删掉 … 表/数据/记录（允许中间夹表名）
    re.compile(r"(?:请)?(?:帮我)?(?:删除|删掉|删了).{0,32}?(表|数据|记录|行|全部|所有|库|测试)"),
    re.compile(r"(?:请)?(?:帮我)?(?:把|将)?.{0,40}?清空"),
    re.compile(r"清空\s*(?:一下\s*)?(表|数据|库|全部|所有)"),
    re.compile(r"(?:请)?(?:帮我)?(?:写入|插入)\s*(?:一下)?\s*(数据|记录|一行|一条|到表)"),
    re.compile(r"改表|修改表结构|删库|删表"),
    # Only treat “改成” as a mutation when its target is clearly persisted data.
    # “改成按月趋势 / 改成按品类拆开” are safe query follow-ups.
    re.compile(r"(?:请)?(?:帮我)?(?:把|将).{0,40}?(?:字段|列|数据|记录|金额|状态|值).{0,24}?(?:全改成|全部改成|都改成|改成|改为|更新为|设置成)"),
    re.compile(r"(?:请)?(?:帮我)?(?:更新|修改).{0,24}?(表|字段|列|数据|记录|金额|amount)"),
    re.compile(r"DROP\s+TABLE", re.I),
    re.compile(r"TRUNCATE\s+TABLE", re.I),
)

_WRITE_MESSAGE = (
    "系统为只读分析环境，不允许执行写操作（INSERT/UPDATE/DELETE/DROP 等）。"
    "未连接数据库执行该请求。如需查询数据，请改用 SELECT 或自然语言描述分析问题。"
)


@dataclass(frozen=True)
class L0WriteDecision:
    refuse: bool
    reason: str = ""
    matched: str = ""
    extracted_sql: str = ""

    @property
    def l0_refuse_write(self) -> bool:
        return self.refuse


def _strip_noise(text: str) -> str:
    t = (text or "").strip()
    # strip markdown code fences lightly
    t = re.sub(r"```(?:sql)?\s*", " ", t, flags=re.I)
    t = t.replace("```", " ")
    return t


def detect_write_intent(question: str) -> L0WriteDecision:
    """Return refuse=True when question is an explicit write intent/SQL."""
    q = _strip_noise(question)
    if not q:
        return L0WriteDecision(False)

    # Fast path: pure SQL write
    if _SQL_WRITE_RE.search(q):
        m = _SQL_WRITE_RE.search(q)
        verb = (m.group(0) if m else "WRITE").strip().upper()
        # Avoid false positive on "created_at" etc. — SQL verb already word-bound
        return L0WriteDecision(
            True,
            reason="sql_write_verb",
            matched=verb,
            extracted_sql=q if len(q) < 500 else q[:500],
        )

    for pat in _CN_WRITE_PATTERNS:
        m = pat.search(q)
        if m:
            return L0WriteDecision(
                True,
                reason="nl_write_intent",
                matched=m.group(0)[:80],
                extracted_sql="",
            )

    return L0WriteDecision(False)


def refuse_message() -> str:
    return _WRITE_MESSAGE
