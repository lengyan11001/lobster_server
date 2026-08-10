import asyncio

from backend.app.api import provider_balances
from backend.app.services import provider_balance_monitor


def test_deepseek_balance_parses_cny_account(monkeypatch):
    monkeypatch.setenv("DEEPSEEK_API_KEY", "deepseek-test-token")

    async def fake_get_json(url, headers):
        assert url == "https://api.deepseek.com/user/balance"
        assert headers["Authorization"] == "Bearer deepseek-test-token"
        return 200, {
            "is_available": True,
            "balance_infos": [
                {
                    "currency": "CNY",
                    "total_balance": "36.19",
                    "granted_balance": "1.00",
                    "topped_up_balance": "35.19",
                }
            ],
        }, ""

    monkeypatch.setattr(provider_balances, "_get_json", fake_get_json)
    result = asyncio.run(provider_balances.query_deepseek_credit("now"))

    assert result["ok"] is True
    assert result["balance"] == 36.19
    assert result["balance_unit"] == "CNY"
    assert result["account_available"] is True


def test_tikhub_balance_parses_user_info(monkeypatch):
    monkeypatch.setenv("TIKHUB_API_KEY", "tikhub-test-token")
    monkeypatch.setenv("TIKHUB_API_BASE", "https://api.tikhub.io/")

    async def fake_get_json(url, headers):
        assert url == "https://api.tikhub.io/api/v1/tikhub/user/get_user_info"
        assert headers["Authorization"] == "Bearer tikhub-test-token"
        return 200, {
            "code": 200,
            "user_data": {
                "balance": 13.531,
                "free_credit": 0,
                "account_disabled": False,
                "is_active": True,
            },
        }, ""

    monkeypatch.setattr(provider_balances, "_get_json", fake_get_json)
    result = asyncio.run(provider_balances.query_tikhub_credit("now"))

    assert result["ok"] is True
    assert result["balance"] == 13.531
    assert result["balance_unit"] == "tikhub_credit"
    assert result["account_active"] is True


def test_sutui_balance_deduplicates_server_tokens(monkeypatch):
    for name in list(provider_balances.os.environ):
        if name.startswith("SUTUI_SERVER_TOKEN"):
            monkeypatch.delenv(name, raising=False)
    monkeypatch.setenv("SUTUI_SERVER_TOKENS_BIHUO", "token-a,token-b")
    monkeypatch.setenv("SUTUI_SERVER_TOKENS_USER", "token-a")
    monkeypatch.setenv("SUTUI_API_BASE", "https://api.apiz.ai")

    async def fake_get_json(url, headers):
        token = headers["Authorization"].removeprefix("Bearer ")
        balance = {"token-a": 100, "token-b": 25}[token]
        return 200, {"code": 200, "data": {"balance": balance}}, ""

    monkeypatch.setattr(provider_balances, "_get_json", fake_get_json)
    results = asyncio.run(provider_balances.query_sutui_balances("now"))

    assert len(results) == 2
    assert [item["balance"] for item in results] == [100.0, 25.0]
    assert all(item["provider_group"] == "sutui_apiz" for item in results)
    assert all("token-a" not in item["url"] and "token-b" not in item["url"] for item in results)


def test_sms_monitor_uses_primary_aliyun_channel(monkeypatch):
    monkeypatch.setenv("ALIYUN_SMS_ACCESS_KEY_ID", "aliyun-ak")
    monkeypatch.setenv("ALIYUN_SMS_ACCESS_KEY_SECRET", "aliyun-sk")
    monkeypatch.setenv("IHUYI_SMS_ACCOUNT", "ihuyi-account")
    monkeypatch.setenv("IHUYI_SMS_PASSWORD", "ihuyi-password")

    async def fake_aliyun(checked_at):
        return {"provider": "sms_aliyun", "ok": True, "balance": 88}

    async def fail_ihuyi(checked_at):
        raise AssertionError("standby IHuYi account must not be queried while Aliyun is active")

    monkeypatch.setattr(provider_balances, "query_aliyun_account_balance", fake_aliyun)
    monkeypatch.setattr(provider_balances, "query_ihuyi_sms_balance", fail_ihuyi)
    results = asyncio.run(provider_balances.query_sms_balances("now"))

    assert [item["provider"] for item in results] == ["sms_aliyun"]


def test_collect_skips_excluded_yunwu(monkeypatch):
    def single(provider):
        async def query(checked_at):
            return {"provider": provider, "ok": True, "balance": 100}

        return query

    monkeypatch.setattr(provider_balances, "query_hifly_credit", single("hifly"))
    monkeypatch.setattr(provider_balances, "query_comfly_credit", single("comfly"))
    monkeypatch.setattr(provider_balances, "query_openmind_credit", single("openmind"))
    monkeypatch.setattr(provider_balances, "query_deepseek_credit", single("deepseek"))
    monkeypatch.setattr(provider_balances, "query_tikhub_credit", single("tikhub"))
    monkeypatch.setattr(provider_balances, "query_tos_account_balance", single("tos"))

    async def sutui(checked_at):
        return [{"provider": "sutui_apiz_bihuo_1", "provider_group": "sutui_apiz", "ok": True, "balance": 100}]

    async def sms(checked_at):
        return [{"provider": "sms_aliyun", "ok": True, "balance": 100}]

    async def yunwu(checked_at):
        raise AssertionError("Yunwu must not be queried by the Feishu monitor")

    monkeypatch.setattr(provider_balances, "query_sutui_balances", sutui)
    monkeypatch.setattr(provider_balances, "query_sms_balances", sms)
    monkeypatch.setattr(provider_balances, "query_yunwu_credit", yunwu)

    result = asyncio.run(provider_balances.collect_provider_balances(excluded_providers={"yunwu"}))

    assert result["ok"] is True
    assert "yunwu" not in result["summary"]
    assert result["summary"]["sutui_apiz_bihuo_1"]["balance"] == 100


def test_feishu_card_excludes_yunwu_by_default(monkeypatch):
    monkeypatch.delenv("PROVIDER_BALANCE_MONITOR_EXCLUDED_PROVIDERS", raising=False)
    card = provider_balance_monitor.build_feishu_card(
        {
            "ok": False,
            "checked_at": "now",
            "providers": [
                {"provider": "yunwu", "name": "Yunwu", "ok": False, "error": "timeout"},
                {"provider": "deepseek", "name": "DeepSeek", "ok": True, "balance": 80, "balance_unit": "CNY"},
            ],
        },
        threshold=50,
    )

    content = card["card"]["elements"][0]["text"]["content"]
    assert "Yunwu" not in content
    assert "DeepSeek" in content
    assert card["card"]["header"]["template"] == "green"


def test_monitor_tick_passes_yunwu_exclusion(monkeypatch):
    monkeypatch.setenv("PROVIDER_BALANCE_FEISHU_WEBHOOK", "https://example.test/webhook")
    monkeypatch.delenv("PROVIDER_BALANCE_MONITOR_EXCLUDED_PROVIDERS", raising=False)
    captured = {}

    async def fake_collect(*, excluded_providers):
        captured["excluded"] = excluded_providers
        return {"ok": True, "providers": [], "checked_at": "now"}

    async def fake_post(data, *, webhook, threshold):
        captured["webhook"] = webhook
        return {"code": 0}

    monkeypatch.setattr(provider_balance_monitor, "collect_provider_balances", fake_collect)
    monkeypatch.setattr(provider_balance_monitor, "post_provider_balance_to_feishu", fake_post)
    asyncio.run(provider_balance_monitor.provider_balance_monitor_tick())

    assert captured["excluded"] == {"yunwu"}
    assert captured["webhook"] == "https://example.test/webhook"
