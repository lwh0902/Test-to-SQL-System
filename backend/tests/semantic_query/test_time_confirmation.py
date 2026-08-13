from datetime import date


def test_query_without_time_requires_today_confirmation():
    from app.agents.time_confirmation import detect_time_confirmation

    result = detect_time_confirmation("按渠道看订单数", today=date(2026, 8, 13))

    assert result is not None
    assert result.reason == "missing_time"
    assert (result.start, result.end) == ("2026-08-13", "2026-08-13")
    assert "今天" in result.prompt


def test_month_without_year_requires_current_year_confirmation():
    from app.agents.time_confirmation import detect_time_confirmation

    result = detect_time_confirmation("7月GMV是多少", today=date(2026, 8, 13))

    assert result is not None
    assert result.reason == "missing_year"
    assert (result.start, result.end) == ("2026-07-01", "2026-07-31")


def test_summer_month_range_without_year_shows_explicit_candidate_dates():
    from app.agents.time_confirmation import detect_time_confirmation

    result = detect_time_confirmation("暑假7到8月订单数趋势", today=date(2026, 8, 13))

    assert result is not None
    assert result.reason == "missing_year"
    assert (result.start, result.end) == ("2026-07-01", "2026-08-31")
    assert "2026年7月1日" in result.prompt
    assert "2026年8月31日" in result.prompt


def test_explicit_year_does_not_require_confirmation():
    from app.agents.time_confirmation import detect_time_confirmation

    assert detect_time_confirmation("2026年7月GMV", today=date(2026, 8, 13)) is None


def test_relative_time_does_not_require_confirmation():
    from app.agents.time_confirmation import detect_time_confirmation

    assert detect_time_confirmation("近14天订单数", today=date(2026, 8, 13)) is None


def test_confirmation_reply_can_accept_reject_or_replace_time():
    from app.agents.time_confirmation import classify_confirmation_reply

    assert classify_confirmation_reply("确定", today=date(2026, 8, 13)).action == "confirm"
    assert classify_confirmation_reply("不是", today=date(2026, 8, 13)).action == "reject"
    replacement = classify_confirmation_reply("改成2025年7到8月", today=date(2026, 8, 13))
    assert replacement.action == "replace"
    assert (replacement.start, replacement.end) == ("2025-07-01", "2025-08-31")


def test_unrelated_query_overrides_pending_confirmation():
    from app.agents.time_confirmation import classify_confirmation_reply

    assert classify_confirmation_reply("这个库有什么表", today=date(2026, 8, 13)).action == "new_query"
