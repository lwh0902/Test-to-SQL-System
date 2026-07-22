"""脱敏安全审计事件。绝不记录密码、token、SQL 参数或结果行。"""
import logging

logger = logging.getLogger("datapilot.security")


def audit_security_event(event: str, request_id: str, user_id: int | None = None, **fields) -> None:
    safe_fields = {k: v for k, v in fields.items() if k not in {"password", "token", "sql", "params", "rows"}}
    logger.info("security_event=%s request_id=%s user_id=%s fields=%s", event, request_id, user_id, safe_fields)
