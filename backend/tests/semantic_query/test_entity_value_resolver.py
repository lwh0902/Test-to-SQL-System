from app.agents.analysis_spec import FilterExpr


def test_resolves_a_unique_full_business_name_from_user_question():
    from app.services.entity_value_service import EntityValue, resolve_filter_values

    filters = [
        FilterExpr("city", "=", "杭州", "tb_orders"),
        FilterExpr("channel_name", "=", "阳光假期门店", "tb_orders"),
    ]
    resolved = resolve_filter_values(
        filters,
        "杭州阳光假期门店近14天订单数",
        [
            EntityValue("channel_name", "杭州阳光假期门店"),
            EntityValue("channel_name", "上海阳光假期门店"),
        ],
    )

    assert [(item.field, item.value) for item in resolved.filters] == [
        ("channel_name", "杭州阳光假期门店"),
    ]
    assert resolved.ambiguities == []


def test_does_not_guess_when_multiple_full_names_match_question():
    from app.services.entity_value_service import EntityValue, resolve_filter_values

    resolved = resolve_filter_values(
        [FilterExpr("channel_name", "=", "阳光假期", "tb_orders")],
        "阳光假期近14天订单数",
        [
            EntityValue("channel_name", "杭州阳光假期门店"),
            EntityValue("channel_name", "上海阳光假期门店"),
        ],
    )

    assert resolved.filters == []
    assert resolved.ambiguities == ["channel_name"]


def test_entity_dictionary_bootstrap_is_best_effort(monkeypatch):
    import app.services.entity_value_service as service

    monkeypatch.setattr(service, "ensure_entity_value_table", lambda: False)

    assert service.sync_entity_values_if_stale(
        space_id="travel_b2b", model=object(), connection={"database": "datacheck"}
    ) == 0
