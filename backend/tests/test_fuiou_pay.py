"""富友 MD5 聚合支付 service 层单元测试。"""
from __future__ import annotations

import asyncio
import hashlib
import re

import pytest


MCHNT_KEY = "test-fuiou-md5-secret"


def _expected(parts: list[str]) -> str:
    return hashlib.md5("|".join(parts).encode("utf-8")).hexdigest()


class TestSigning:
    def test_md5(self):
        from backend.app.services.fuiou_pay import _md5

        assert _md5("富友|test") == hashlib.md5("富友|test".encode("utf-8")).hexdigest()

    def test_precreate_sign_field_order(self):
        from backend.app.services.fuiou_pay import _sign_precreate

        params = {
            "mchnt_cd": "merchant",
            "order_type": "WECHAT",
            "order_amt": "100",
            "mchnt_order_no": "order-1",
            "txn_begin_ts": "20260727120000",
            "goods_des": "recharge",
            "term_id": "88888888",
            "term_ip": "127.0.0.1",
            "notify_url": "https://example.test/notify",
            "random_str": "random",
            "version": "1.0",
        }
        assert _sign_precreate(params, MCHNT_KEY) == _expected([*params.values(), MCHNT_KEY])

    def test_query_sign_field_order(self):
        from backend.app.services.fuiou_pay import _sign_query

        params = {
            "mchnt_cd": "merchant",
            "order_type": "WECHAT",
            "mchnt_order_no": "order-1",
            "term_id": "88888888",
            "random_str": "random",
            "version": "1.0",
        }
        assert _sign_query(params, MCHNT_KEY) == _expected([*params.values(), MCHNT_KEY])

    def test_refund_sign_field_order(self):
        from backend.app.services.fuiou_pay import _sign_refund

        params = {
            "mchnt_cd": "merchant",
            "order_type": "WECHAT",
            "mchnt_order_no": "order-1",
            "refund_order_no": "refund-1",
            "total_amt": "100",
            "refund_amt": "100",
            "term_id": "88888888",
            "random_str": "random",
            "version": "1.0",
        }
        assert _sign_refund(params, MCHNT_KEY) == _expected([*params.values(), MCHNT_KEY])


class TestParseNotify:
    def test_valid_full_sign(self, patch_fuiou_settings, make_notify):
        from backend.app.services.fuiou_pay import parse_notify

        data = make_notify({"mchnt_order_no": "1000020260727abc", "order_amt": "100"})
        ok, parsed = parse_notify(data)
        assert ok is True
        assert parsed == data

    def test_missing_sign_is_rejected(self, patch_fuiou_settings, make_notify):
        from backend.app.services.fuiou_pay import parse_notify

        ok, _ = parse_notify(make_notify({"mchnt_order_no": "order", "order_amt": "100"}, with_sign=False))
        assert ok is False

    def test_tampered_amount_is_rejected(self, patch_fuiou_settings, make_notify):
        from backend.app.services.fuiou_pay import parse_notify

        data = make_notify({"mchnt_order_no": "order", "order_amt": "100"})
        data["order_amt"] = "999"
        ok, _ = parse_notify(data)
        assert ok is False

    def test_wrong_merchant_is_rejected(self, patch_fuiou_settings, make_notify):
        from backend.app.services.fuiou_pay import parse_notify

        ok, _ = parse_notify(make_notify({"mchnt_order_no": "order", "order_amt": "100"}, mchnt_cd="other"))
        assert ok is False

    def test_non_success_result_is_rejected(self, patch_fuiou_settings, make_notify):
        from backend.app.services.fuiou_pay import parse_notify

        data = make_notify(
            {
                "mchnt_order_no": "order",
                "order_amt": "100",
                "result_code": "999999",
                "result_msg": "failed",
            }
        )
        ok, parsed = parse_notify(data)
        assert ok is False
        assert parsed == data

    def test_non_dict_is_rejected(self, patch_fuiou_settings):
        from backend.app.services.fuiou_pay import parse_notify

        assert parse_notify("invalid") == (False, {})  # type: ignore[arg-type]


class TestConfigured:
    def test_all_current_fields_set(self, patch_fuiou_settings):
        from backend.app.services.fuiou_pay import fuiou_configured

        assert fuiou_configured() is True

    @pytest.mark.parametrize("missing", ["fuiou_mchnt_cd", "fuiou_mchnt_key", "fuiou_precreate_url"])
    def test_required_field_missing(self, patch_fuiou_settings, monkeypatch, missing):
        from backend.app.services.fuiou_pay import fuiou_configured

        monkeypatch.setattr(patch_fuiou_settings, missing, "")
        assert fuiou_configured() is False


class TestOrderApi:
    def test_precreate_request_and_response(self, patch_fuiou_settings, patch_httpx_post):
        from backend.app.services.fuiou_pay import _sign_precreate, fuiou_order_pay

        captured = patch_httpx_post(
            lambda _url, _body: {
                "result_code": "000000",
                "result_msg": "成功",
                "qr_code": "https://qr.example/order",
            }
        )
        result = asyncio.run(
            fuiou_order_pay(
                mchnt_order_no="1000020260727abc",
                order_amt_fen=1200,
                notify_url="https://example.test/notify",
                goods_des="recharge 1200 credits",
                order_type="WECHAT",
            )
        )

        assert result["ok"] is True
        assert result["qr_code"] == "https://qr.example/order"
        assert captured[0]["url"].endswith("/aggregatePay/preCreate")
        body = captured[0]["body"]
        assert body["order_amt"] == "1200"
        assert body["order_type"] == "WECHAT"
        assert body["version"] == "1.0"
        assert body["sign"] == _sign_precreate(body, MCHNT_KEY)

    def test_query_response_mapping(self, patch_fuiou_settings, patch_httpx_post):
        from backend.app.services.fuiou_pay import _sign_query, fuiou_order_query

        captured = patch_httpx_post(
            lambda _url, _body: {
                "result_code": "000000",
                "result_msg": "成功",
                "trans_stat": "SUCCESS",
                "order_amt": "1200",
                "transaction_id": "wx-transaction",
            }
        )
        result = asyncio.run(fuiou_order_query(mchnt_order_no="1000020260727abc", order_type="WECHAT"))

        assert result["ok"] is True
        assert result["trans_stat"] == "SUCCESS"
        assert result["order_amt_fen"] == 1200
        assert result["transaction_id"] == "wx-transaction"
        body = captured[0]["body"]
        assert body["sign"] == _sign_query(body, MCHNT_KEY)


class TestUtils:
    def test_order_no_format_and_length(self, patch_fuiou_settings):
        from backend.app.services.fuiou_pay import gen_order_no

        order_no = gen_order_no()
        assert order_no.startswith("10000")
        assert len(order_no) <= 30
        assert re.fullmatch(r"[A-Za-z0-9]+", order_no)

    def test_order_no_unique(self, patch_fuiou_settings):
        from backend.app.services.fuiou_pay import gen_order_no

        assert len({gen_order_no() for _ in range(100)}) == 100

    def test_timestamp_format(self):
        from backend.app.services.fuiou_pay import _now_ts

        assert re.fullmatch(r"\d{14}", _now_ts())
