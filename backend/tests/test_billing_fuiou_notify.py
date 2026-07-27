"""富友 MD5 聚合支付 API、回调安全和入账幂等测试。"""
from __future__ import annotations

from decimal import Decimal

import pytest


def _create_pending_order(
    db_session,
    user_id: int,
    *,
    credits: int = 100,
    amount_yuan: int = 1,
    out_trade_no: str = "1000020260727test",
) -> str:
    from backend.app.models import RechargeOrder

    row = RechargeOrder(
        user_id=user_id,
        amount_yuan=amount_yuan,
        amount_fen=0,
        credits=credits,
        status="pending",
        out_trade_no=out_trade_no,
        payment_method="fuiou_wechat",
    )
    db_session.add(row)
    db_session.commit()
    return row.out_trade_no


class TestFuiouCreate:
    def test_not_configured_returns_400(self, client, monkeypatch, patch_fuiou_settings):
        monkeypatch.setattr(patch_fuiou_settings, "fuiou_mchnt_cd", "")
        response = client.post("/api/recharge/fuiou-create", json={"price_yuan": 1, "credits": 100})
        assert response.status_code == 400
        assert "未配置富友支付" in response.json()["detail"]

    def test_create_success_returns_qr_code(self, client, patch_httpx_post, make_fuiou_response):
        captured = patch_httpx_post(
            lambda _url, _body: make_fuiou_response({"qr_code": "https://qr.example/abc123"})
        )
        response = client.post("/api/recharge/fuiou-create", json={"price_yuan": 1, "credits": 100})

        assert response.status_code == 200, response.text
        payload = response.json()
        assert payload["qr_code"] == "https://qr.example/abc123"
        assert payload["credits"] == 100
        assert payload["amount_yuan"] == 1.0
        assert payload["payment_type"] == "WECHAT"
        assert payload["out_trade_no"].startswith("10000")
        assert captured[0]["url"].endswith("/aggregatePay/preCreate")
        assert captured[0]["body"]["mchnt_cd"] == "0001000F0040992"
        assert captured[0]["body"]["order_amt"] == "100"
        assert captured[0]["body"]["sign"]

    def test_gateway_error_returns_502(self, client, patch_httpx_post, make_fuiou_response):
        patch_httpx_post(
            lambda _url, _body: make_fuiou_response(
                {},
                result_code="999999",
                result_msg="商户号不存在",
            )
        )
        response = client.post("/api/recharge/fuiou-create", json={"price_yuan": 1, "credits": 100})
        assert response.status_code == 502
        assert "商户号不存在" in response.json()["detail"]

    def test_success_without_qr_code_returns_502(self, client, patch_httpx_post, make_fuiou_response):
        patch_httpx_post(lambda _url, _body: make_fuiou_response({}))
        response = client.post("/api/recharge/fuiou-create", json={"price_yuan": 1, "credits": 100})
        assert response.status_code == 502
        assert "未返回二维码" in response.json()["detail"]

    def test_upstream_exception_returns_502(self, client, monkeypatch):
        async def fail_post(self, url, **kwargs):
            raise RuntimeError("network down")

        import httpx

        monkeypatch.setattr(httpx.AsyncClient, "post", fail_post)
        response = client.post("/api/recharge/fuiou-create", json={"price_yuan": 1, "credits": 100})
        assert response.status_code == 502
        assert "请稍后重试" in response.json()["detail"]

    @pytest.mark.parametrize(
        "body, expected",
        [
            ({"price_yuan": -1, "credits": 100}, "正数"),
            ({"price_yuan": 0, "credits": 0}, "正数"),
            ({}, "请选择套餐"),
            ({"package_index": 999}, "无效套餐"),
        ],
    )
    def test_bad_input(self, client, body, expected):
        response = client.post("/api/recharge/fuiou-create", json=body)
        assert response.status_code == 400
        assert expected in response.json()["detail"]


class TestFuiouNotify:
    def test_valid_notify_credits_user(self, client, db_session, test_user, make_notify):
        out_trade_no = _create_pending_order(db_session, test_user.id)
        data = make_notify(
            {
                "mchnt_order_no": out_trade_no,
                "order_amt": "100",
                "transaction_id": "WX_4200001234",
            }
        )
        response = client.post("/api/recharge/fuiou-notify", json=data)

        assert response.status_code == 200
        assert response.text == "1"
        from backend.app.models import RechargeOrder, User

        db_session.expire_all()
        user = db_session.query(User).filter(User.id == test_user.id).first()
        order = db_session.query(RechargeOrder).filter(RechargeOrder.out_trade_no == out_trade_no).first()
        assert user.credits == Decimal("200.0000")
        assert order.status == "paid"
        assert order.callback_amount_fen == 100
        assert order.wechat_transaction_id == "WX_4200001234"

    def test_repeat_notify_is_idempotent(self, client, db_session, test_user, make_notify):
        out_trade_no = _create_pending_order(db_session, test_user.id)
        data = make_notify({"mchnt_order_no": out_trade_no, "order_amt": "100"})
        for _ in range(5):
            response = client.post("/api/recharge/fuiou-notify", json=data)
            assert response.status_code == 200
            assert response.text == "1"
        from backend.app.models import User

        db_session.expire_all()
        assert db_session.query(User).filter(User.id == test_user.id).first().credits == Decimal("200.0000")

    def test_amount_mismatch_is_rejected(self, client, db_session, test_user, make_notify):
        out_trade_no = _create_pending_order(db_session, test_user.id)
        data = make_notify({"mchnt_order_no": out_trade_no, "order_amt": "999"})
        response = client.post("/api/recharge/fuiou-notify", json=data)
        assert response.status_code == 400
        assert response.text == "0"

    def test_tampered_signature_is_rejected(self, client, db_session, test_user, make_notify):
        out_trade_no = _create_pending_order(db_session, test_user.id)
        data = make_notify({"mchnt_order_no": out_trade_no, "order_amt": "100"})
        data["full_sign"] = "0" * 32
        response = client.post("/api/recharge/fuiou-notify", json=data)
        assert response.status_code == 400
        assert response.text == "0"

    def test_wrong_merchant_is_rejected(self, client, db_session, test_user, make_notify):
        out_trade_no = _create_pending_order(db_session, test_user.id)
        data = make_notify(
            {"mchnt_order_no": out_trade_no, "order_amt": "100"},
            mchnt_cd="OTHER_MCHNT",
        )
        assert client.post("/api/recharge/fuiou-notify", json=data).status_code == 400

    def test_invalid_json_is_rejected(self, client):
        response = client.post(
            "/api/recharge/fuiou-notify",
            content=b"not-json",
            headers={"Content-Type": "application/json"},
        )
        assert response.status_code == 400
        assert response.text == "0"

    def test_unknown_order_returns_success_without_credit(self, client, db_session, test_user, make_notify):
        data = make_notify({"mchnt_order_no": "10000-not-found", "order_amt": "100"})
        response = client.post("/api/recharge/fuiou-notify", json=data)
        assert response.status_code == 200
        assert response.text == "1"
        from backend.app.models import User

        db_session.expire_all()
        assert db_session.query(User).filter(User.id == test_user.id).first().credits == Decimal("100.0000")

    @pytest.mark.parametrize("bad_amount", [None, "abc"])
    def test_missing_or_invalid_amount_is_rejected(self, client, db_session, test_user, make_notify, bad_amount):
        out_trade_no = _create_pending_order(db_session, test_user.id)
        values = {"mchnt_order_no": out_trade_no}
        if bad_amount is not None:
            values["order_amt"] = bad_amount
        response = client.post("/api/recharge/fuiou-notify", json=make_notify(values))
        assert response.status_code == 400
        assert response.text == "0"

    def test_unconfigured_notify_returns_500(self, client, monkeypatch, patch_fuiou_settings, make_notify):
        data = make_notify({"mchnt_order_no": "order", "order_amt": "100"})
        monkeypatch.setattr(patch_fuiou_settings, "fuiou_mchnt_key", "")
        response = client.post("/api/recharge/fuiou-notify", json=data)
        assert response.status_code == 500
        assert response.text == "0"


class TestFuiouQuery:
    def test_paid_query_credits_user(self, client, db_session, test_user, patch_httpx_post, make_fuiou_response):
        out_trade_no = _create_pending_order(db_session, test_user.id)
        patch_httpx_post(
            lambda _url, _body: make_fuiou_response(
                {
                    "mchnt_order_no": out_trade_no,
                    "trans_stat": "SUCCESS",
                    "order_amt": "100",
                    "transaction_id": "WX_QUERY",
                }
            )
        )
        response = client.get(f"/api/recharge/fuiou-query?out_trade_no={out_trade_no}")
        assert response.status_code == 200
        assert response.json()["status"] == "paid"
        from backend.app.models import User

        db_session.expire_all()
        assert db_session.query(User).filter(User.id == test_user.id).first().credits == Decimal("200.0000")

    def test_pending_query_stays_pending(self, client, db_session, test_user, patch_httpx_post, make_fuiou_response):
        out_trade_no = _create_pending_order(db_session, test_user.id)
        patch_httpx_post(
            lambda _url, _body: make_fuiou_response(
                {"mchnt_order_no": out_trade_no, "trans_stat": "NOTPAY", "order_amt": "100"}
            )
        )
        response = client.get(f"/api/recharge/fuiou-query?out_trade_no={out_trade_no}")
        assert response.json() == {"status": "pending", "trans_stat": "NOTPAY"}

    def test_query_amount_mismatch_stays_pending(self, client, db_session, test_user, patch_httpx_post, make_fuiou_response):
        out_trade_no = _create_pending_order(db_session, test_user.id)
        patch_httpx_post(
            lambda _url, _body: make_fuiou_response(
                {"mchnt_order_no": out_trade_no, "trans_stat": "SUCCESS", "order_amt": "999"}
            )
        )
        response = client.get(f"/api/recharge/fuiou-query?out_trade_no={out_trade_no}")
        assert response.json()["status"] == "pending"
        assert "金额校验" in response.json()["message"]

    def test_cross_user_query_is_404(self, client_as_other, db_session, test_user):
        out_trade_no = _create_pending_order(db_session, test_user.id)
        assert client_as_other.get(f"/api/recharge/fuiou-query?out_trade_no={out_trade_no}").status_code == 404

    def test_unknown_order_is_404(self, client):
        assert client.get("/api/recharge/fuiou-query?out_trade_no=not-found").status_code == 404

    def test_paid_order_does_not_query_or_credit_again(self, client, db_session, test_user):
        from backend.app.models import RechargeOrder, User

        out_trade_no = _create_pending_order(db_session, test_user.id)
        order = db_session.query(RechargeOrder).filter(RechargeOrder.out_trade_no == out_trade_no).first()
        order.status = "paid"
        db_session.commit()
        response = client.get(f"/api/recharge/fuiou-query?out_trade_no={out_trade_no}")
        assert response.json()["status"] == "paid"
        db_session.expire_all()
        assert db_session.query(User).filter(User.id == test_user.id).first().credits == Decimal("100.0000")

    def test_unconfigured_query_is_400(self, client, db_session, test_user, monkeypatch, patch_fuiou_settings):
        out_trade_no = _create_pending_order(db_session, test_user.id)
        monkeypatch.setattr(patch_fuiou_settings, "fuiou_mchnt_key", "")
        assert client.get(f"/api/recharge/fuiou-query?out_trade_no={out_trade_no}").status_code == 400


class TestSecurity:
    def test_create_uses_current_plain_json_and_fen_amount(
        self,
        client,
        patch_httpx_post,
        make_fuiou_response,
    ):
        captured = patch_httpx_post(
            lambda _url, _body: make_fuiou_response({"qr_code": "https://qr.example/order"})
        )
        response = client.post("/api/recharge/fuiou-create", json={"price_yuan": 12, "credits": 1200})
        assert response.status_code == 200
        body = captured[0]["body"]
        assert body["order_amt"] == "1200"
        assert body["mchnt_cd"] == "0001000F0040992"
        assert body["order_type"] == "WECHAT"
        assert body["version"] == "1.0"
        assert body["notify_url"].endswith("/api/recharge/fuiou-notify")
