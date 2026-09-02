from __future__ import annotations

import pytest


def test_tikhub_prices_convert_usd_to_lobster_credits(monkeypatch):
    from backend.app.services import tikhub_pricing as pricing

    monkeypatch.setattr(pricing.settings, "tikhub_usd_to_cny_rate", 7.5, raising=False)
    monkeypatch.setattr(pricing.settings, "tikhub_query_unit_credits", 1.0, raising=False)

    assert pricing.query_price("cheap", endpoint_path="/api/v1/douyin/app/v3/fetch_hot_search_list", require_known=True) == 1
    assert pricing.query_price("wechat", endpoint_path="/api/v1/wechat_search/v2/fetch_search", require_known=True) == 7.5
    assert pricing.query_price("linkedin_profile", endpoint_path="/api/v1/linkedin/web_v2/get_user_profile", require_known=True) == 6
    assert pricing.query_price("linkedin_posts", endpoint_path="/api/v1/linkedin/web_v2/get_user_posts", require_known=True) == 37.5


def test_unknown_tikhub_endpoint_fails_closed():
    from backend.app.services.tikhub_pricing import query_price

    with pytest.raises(ValueError):
        query_price("new_endpoint", endpoint_path="/api/v1/not-a-real-endpoint", require_known=True)


def test_all_current_user_facing_tikhub_paths_have_prices():
    from backend.app.api.alibaba_customer_research import _UPSTREAM_TASKS
    from backend.app.api.ip_content_studio import _ENDPOINTS
    from backend.app.services.tikhub_pricing import known_price_table

    paths = {str(spec.get("path") or "") for spec in _ENDPOINTS.values()}
    paths.update(str(spec.get("path") or "") for spec in _UPSTREAM_TASKS.values())
    assert paths <= set(known_price_table())


def test_platform_daily_tikhub_paths_have_prices():
    from backend.app.services.douyin_platform_information_desk import PUBLIC_DAILY_ENDPOINTS
    from backend.app.services.tikhub_pricing import known_price_table

    paths = {str(spec.get("path") or "") for spec in PUBLIC_DAILY_ENDPOINTS}
    assert paths <= set(known_price_table())


def test_price_breakdown_is_auditable():
    from backend.app.services.tikhub_pricing import price_breakdown

    result = price_breakdown("wechat_search_v2", "/api/v1/wechat_search/v2/fetch_search")
    assert result["provider_cost_usd"] == 0.01
    assert result["credits_per_cny"] == 100
    assert result["lobster_credits"] == 7.5
    assert result["price_table_as_of"] == "2026-09-02"


def test_tikhub_transport_failure_only_charges_when_request_may_have_been_sent():
    import httpx

    from backend.app.api import ip_content_studio as studio

    assert not studio._tikhub_transport_charge_uncertain(httpx.ConnectTimeout("connect"))
    assert not studio._tikhub_transport_charge_uncertain(httpx.PoolTimeout("pool"))
    assert not studio._tikhub_transport_charge_uncertain(httpx.ConnectError("dns"))
    assert studio._tikhub_transport_charge_uncertain(httpx.ReadTimeout("read"))
    assert studio._tikhub_transport_charge_uncertain(httpx.RemoteProtocolError("reset"))
