"""API 端到端 + 安全场景：fuiou-create / fuiou-notify / fuiou-query 全分支。

测试约定：
- 所有外部 HTTP（富友网关）通过 patch_httpx_post 拦截，
  返回测试构造的"模拟富友"响应（用 fake_keys 加密）。
- DB 用每测一份的 SQLite，干净隔离。
- 鉴权 override 到固定的 test_user（id=1）；跨用户场景用 client_as_other。
"""
from __future__ import annotations

import json
import time
from decimal import Decimal

import pytest


def _create_pending_order(db_session, user_id: int, *, credits: int = 100, amount_yuan: int = 1, out_trade_no: str = "R1_test") -> str:
    """直接往 DB 插一条 pending 订单，避开 create 接口 → 用于 notify/query 测试。"""
    from backend.app.models import RechargeOrder

    o = RechargeOrder(
        user_id=user_id,
        amount_yuan=amount_yuan,
        amount_fen=0,
        credits=credits,
        status="pending",
        out_trade_no=out_trade_no,
        payment_method="fuiou",
    )
    db_session.add(o)
    db_session.commit()
    db_session.refresh(o)
    return o.out_trade_no


# ── 1. POST /api/recharge/fuiou-create ──

class TestFuiouCreate:
    def test_not_configured_returns_400(self, client, monkeypatch, patch_fuiou_settings):
        monkeypatch.setattr(patch_fuiou_settings, "fuiou_mchnt_cd", "", raising=False)
        r = client.post("/api/recharge/fuiou-create", json={"price_yuan": 1, "credits": 100})
        assert r.status_code == 400
        assert "未配置富友支付" in r.json()["detail"]

    def test_create_success_returns_qr_code(self, client, patch_httpx_post, make_fuiou_response):
        captured = patch_httpx_post(lambda url, body: make_fuiou_response(
            {"order_id": "ignored", "order_info": "https://qr.example/abc123"},
            resp_code="0000",
        ))
        r = client.post("/api/recharge/fuiou-create", json={"price_yuan": 1, "credits": 100})
        assert r.status_code == 200, r.text
        d = r.json()
        assert d["qr_code"] == "https://qr.example/abc123"
        assert d["credits"] == 100
        assert d["amount_yuan"] == 1.0
        assert d["status"] == "pending"
        assert d["out_trade_no"].startswith("R1_")
        assert d["order_date"]
        # 富友网关被调用，URL 与 .env 一致
        assert len(captured) == 1
        assert captured[0]["url"] == "https://example.test/aggpos/order.fuiou"
        # 商户号 + 报文体合规
        body = captured[0]["body"]
        assert body["mchnt_cd"] == "0001000F0040992"
        assert body["message"] and isinstance(body["message"], str)

    def test_fuiou_resp_code_non_zero_502(self, client, patch_httpx_post, make_fuiou_response):
        patch_httpx_post(lambda url, body: make_fuiou_response(
            {}, resp_code="9999", resp_desc="商户号不存在"
        ))
        r = client.post("/api/recharge/fuiou-create", json={"price_yuan": 1, "credits": 100})
        assert r.status_code == 502
        assert "商户号不存在" in r.json()["detail"]

    def test_fuiou_no_order_info_502(self, client, patch_httpx_post, make_fuiou_response):
        """resp_code=0000 但 order_info 为空 → 拒绝。"""
        patch_httpx_post(lambda url, body: make_fuiou_response(
            {"order_id": "x", "order_info": ""}, resp_code="0000"
        ))
        r = client.post("/api/recharge/fuiou-create", json={"price_yuan": 1, "credits": 100})
        assert r.status_code == 502
        assert "未返回二维码" in r.json()["detail"]

    def test_upstream_exception_502(self, client, monkeypatch):
        """httpx raise 异常 → 502 + 不抛回栈。"""
        async def _boom(self, url, **kw):
            raise RuntimeError("network down")

        import httpx
        monkeypatch.setattr(httpx.AsyncClient, "post", _boom)
        r = client.post("/api/recharge/fuiou-create", json={"price_yuan": 1, "credits": 100})
        assert r.status_code == 502
        assert "请稍后重试" in r.json()["detail"]

    @pytest.mark.parametrize("body,expected_msg", [
        ({"price_yuan": -1, "credits": 100}, "正数"),
        ({"price_yuan": 0, "credits": 0}, "正数"),
        ({}, "请选择套餐"),
        ({"package_index": 999}, "无效套餐"),
    ])
    def test_bad_input(self, client, body, expected_msg):
        r = client.post("/api/recharge/fuiou-create", json=body)
        assert r.status_code == 400
        assert expected_msg in r.json()["detail"]


# ── 2. POST /api/recharge/fuiou-notify ──

class TestFuiouNotify:
    def test_legit_notify_credits_user(self, client, db_session, test_user, make_notify):
        out_no = _create_pending_order(db_session, test_user.id, credits=100, amount_yuan=1)
        env = make_notify({
            "order_id": out_no,
            "order_st": "1",
            "order_amt": "100",  # 1 元 = 100 分
            "channel_order_no": "WX_4200001234",
        })
        r = client.post("/api/recharge/fuiou-notify", json=env)
        assert r.status_code == 200
        assert r.text == "SUCCESS"
        # 用户积分 +100，订单 paid
        from backend.app.models import RechargeOrder, User
        db_session.expire_all()
        u = db_session.query(User).filter(User.id == test_user.id).first()
        assert float(u.credits) == 200.0  # 初始 100 + 充值 100
        o = db_session.query(RechargeOrder).filter(RechargeOrder.out_trade_no == out_no).first()
        assert o.status == "paid"
        assert o.callback_amount_fen == 100
        assert o.wechat_transaction_id == "WX_4200001234"

    def test_repeat_notify_idempotent(self, client, db_session, test_user, make_notify):
        """同一订单第二次回调 → 返回 SUCCESS 但**不重复加积分**。"""
        out_no = _create_pending_order(db_session, test_user.id, credits=100, amount_yuan=1)
        env = make_notify({"order_id": out_no, "order_st": "1", "order_amt": "100"})
        r1 = client.post("/api/recharge/fuiou-notify", json=env)
        r2 = client.post("/api/recharge/fuiou-notify", json=env)
        assert r1.status_code == 200 and r2.status_code == 200
        from backend.app.models import User
        db_session.expire_all()
        u = db_session.query(User).filter(User.id == test_user.id).first()
        assert float(u.credits) == 200.0  # 只加了一次

    def test_amount_mismatch_rejected(self, client, db_session, test_user, make_notify):
        """订单金额 1 元(100 分)，回调说 999 分 → 拒绝且不入账。"""
        out_no = _create_pending_order(db_session, test_user.id, credits=100, amount_yuan=1)
        env = make_notify({"order_id": out_no, "order_st": "1", "order_amt": "999"})
        r = client.post("/api/recharge/fuiou-notify", json=env)
        assert r.status_code == 400
        from backend.app.models import RechargeOrder, User
        db_session.expire_all()
        u = db_session.query(User).filter(User.id == test_user.id).first()
        o = db_session.query(RechargeOrder).filter(RechargeOrder.out_trade_no == out_no).first()
        assert float(u.credits) == 100.0
        assert o.status == "pending"

    def test_mchnt_cd_mismatch_rejected(self, client, db_session, test_user, make_notify):
        out_no = _create_pending_order(db_session, test_user.id)
        env = make_notify({"order_id": out_no, "order_st": "1", "order_amt": "100"}, mchnt_cd="OTHER_MCHNT")
        r = client.post("/api/recharge/fuiou-notify", json=env)
        assert r.status_code == 400
        from backend.app.models import User
        db_session.expire_all()
        u = db_session.query(User).filter(User.id == test_user.id).first()
        assert float(u.credits) == 100.0

    def test_message_tampered_rejected(self, client, db_session, test_user, make_notify):
        out_no = _create_pending_order(db_session, test_user.id)
        env = make_notify({"order_id": out_no, "order_st": "1", "order_amt": "100"})
        env["message"] = env["message"][:16] + ("X" * 16) + env["message"][32:]
        r = client.post("/api/recharge/fuiou-notify", json=env)
        assert r.status_code == 400

    def test_invalid_json_rejected(self, client):
        r = client.post(
            "/api/recharge/fuiou-notify",
            content=b"this is not json",
            headers={"Content-Type": "application/json"},
        )
        assert r.status_code == 400

    def test_unknown_order_returns_success_but_noop(self, client, db_session, test_user, make_notify):
        """订单不存在 → 返回 SUCCESS（不让攻击者通过 4xx 探测订单），但不动账。"""
        env = make_notify({"order_id": "R_NOT_EXIST", "order_st": "1", "order_amt": "100"})
        r = client.post("/api/recharge/fuiou-notify", json=env)
        assert r.status_code == 200
        assert r.text == "SUCCESS"

    def test_non_paid_status_returns_success_no_credit(self, client, db_session, test_user, make_notify):
        """order_st != '1' 直接 SUCCESS 不入账。"""
        out_no = _create_pending_order(db_session, test_user.id)
        env = make_notify({"order_id": out_no, "order_st": "4", "order_amt": "100"})
        r = client.post("/api/recharge/fuiou-notify", json=env)
        assert r.status_code == 200
        from backend.app.models import RechargeOrder, User
        db_session.expire_all()
        assert db_session.query(RechargeOrder).filter(RechargeOrder.out_trade_no == out_no).first().status == "pending"
        assert float(db_session.query(User).filter(User.id == test_user.id).first().credits) == 100.0

    def test_missing_amount_rejected(self, client, db_session, test_user, make_notify):
        out_no = _create_pending_order(db_session, test_user.id)
        env = make_notify({"order_id": out_no, "order_st": "1"})
        r = client.post("/api/recharge/fuiou-notify", json=env)
        assert r.status_code == 400

    def test_amount_non_numeric_rejected(self, client, db_session, test_user, make_notify):
        out_no = _create_pending_order(db_session, test_user.id)
        env = make_notify({"order_id": out_no, "order_st": "1", "order_amt": "abc"})
        r = client.post("/api/recharge/fuiou-notify", json=env)
        assert r.status_code == 400

    def test_notify_when_unconfigured_500(self, client, monkeypatch, patch_fuiou_settings, make_notify):
        """fuiou 没配 → 通知必须拒绝，避免空配置下被任意调用。"""
        # 先做 notify 需要的 env（复用前面的 fixtures）；再撤掉 mchnt_cd
        env = make_notify({"order_id": "x", "order_st": "1", "order_amt": "100"})
        monkeypatch.setattr(patch_fuiou_settings, "fuiou_mchnt_cd", "", raising=False)
        r = client.post("/api/recharge/fuiou-notify", json=env)
        assert r.status_code == 500


# ── 3. GET /api/recharge/fuiou-query ──

class TestFuiouQuery:
    def test_query_paid_credits_user(self, client, db_session, test_user, patch_httpx_post, make_fuiou_response):
        out_no = _create_pending_order(db_session, test_user.id, credits=100, amount_yuan=1)
        patch_httpx_post(lambda url, body: make_fuiou_response(
            {"order_id": out_no, "order_st": "1", "order_amt": "100", "channel_order_no": "WX_X1"},
            resp_code="0000",
        ))
        r = client.get(f"/api/recharge/fuiou-query?out_trade_no={out_no}")
        assert r.status_code == 200, r.text
        d = r.json()
        assert d["status"] == "paid"
        assert d["credits"] == 100
        # DB 已入账
        from backend.app.models import User
        db_session.expire_all()
        assert float(db_session.query(User).filter(User.id == test_user.id).first().credits) == 200.0

    def test_query_pending(self, client, db_session, test_user, patch_httpx_post, make_fuiou_response):
        out_no = _create_pending_order(db_session, test_user.id)
        patch_httpx_post(lambda url, body: make_fuiou_response(
            {"order_id": out_no, "order_st": "0"}, resp_code="0000"
        ))
        r = client.get(f"/api/recharge/fuiou-query?out_trade_no={out_no}")
        assert r.json()["status"] == "pending"

    def test_query_amount_mismatch_pending(self, client, db_session, test_user, patch_httpx_post, make_fuiou_response):
        """查询返回的金额与订单金额不一致 → 报"金额校验未通过"，不入账。"""
        out_no = _create_pending_order(db_session, test_user.id, credits=100, amount_yuan=1)
        patch_httpx_post(lambda url, body: make_fuiou_response(
            {"order_id": out_no, "order_st": "1", "order_amt": "999"}, resp_code="0000"
        ))
        r = client.get(f"/api/recharge/fuiou-query?out_trade_no={out_no}")
        d = r.json()
        assert d["status"] == "pending"
        assert "金额校验" in d["message"]

    def test_query_other_users_order_404(self, client_as_other, db_session, test_user):
        """test_user 的订单，other_user 来查 → 404，不能跨用户访问。"""
        out_no = _create_pending_order(db_session, test_user.id)
        r = client_as_other.get(f"/api/recharge/fuiou-query?out_trade_no={out_no}")
        assert r.status_code == 404

    def test_query_unknown_order_404(self, client):
        r = client.get("/api/recharge/fuiou-query?out_trade_no=R_NOT_EXIST")
        assert r.status_code == 404

    def test_query_already_paid_no_double_credit(self, client, db_session, test_user):
        """订单已 paid → 直接返回 paid，且不再调网关、不再加积分。"""
        from backend.app.models import RechargeOrder
        from datetime import datetime

        out_no = _create_pending_order(db_session, test_user.id, credits=100, amount_yuan=1)
        o = db_session.query(RechargeOrder).filter(RechargeOrder.out_trade_no == out_no).first()
        o.status = "paid"
        o.paid_at = datetime.utcnow()
        db_session.commit()

        # 不 monkeypatch httpx.post —— 如果代码错误地去查富友会因 unconfigured URL 失败
        r = client.get(f"/api/recharge/fuiou-query?out_trade_no={out_no}")
        assert r.status_code == 200
        assert r.json()["status"] == "paid"
        from backend.app.models import User
        db_session.expire_all()
        assert float(db_session.query(User).filter(User.id == test_user.id).first().credits) == 100.0

    def test_query_unconfigured_400(self, client, monkeypatch, patch_fuiou_settings, db_session, test_user):
        out_no = _create_pending_order(db_session, test_user.id)
        monkeypatch.setattr(patch_fuiou_settings, "fuiou_mchnt_cd", "", raising=False)
        r = client.get(f"/api/recharge/fuiou-query?out_trade_no={out_no}")
        assert r.status_code == 400


# ── 4. 跨用户安全性 / 通用安全 ──

class TestSecurity:
    def test_create_does_not_leak_other_user(self, client_as_other, db_session, patch_httpx_post, make_fuiou_response):
        """other_user 创建订单 → 订单的 user_id 是 other_user 而不是 test_user。"""
        patch_httpx_post(lambda url, body: make_fuiou_response(
            {"order_info": "https://qr.example/x"}, resp_code="0000"
        ))
        r = client_as_other.post("/api/recharge/fuiou-create", json={"price_yuan": 1, "credits": 100})
        assert r.status_code == 200
        out_no = r.json()["out_trade_no"]
        from backend.app.models import RechargeOrder, User
        from sqlalchemy.orm import sessionmaker
        s = db_session
        o = s.query(RechargeOrder).filter(RechargeOrder.out_trade_no == out_no).first()
        # other_user 是后建的，id 一定 > test_user.id
        bob = s.query(User).filter(User.email == "bob@test.local").first()
        alice = s.query(User).filter(User.email == "alice@test.local").first()
        assert o.user_id == bob.id
        assert o.user_id != alice.id

    def test_replay_with_old_message_after_paid_blocked(self, client, db_session, test_user, make_notify):
        """先合法支付一次 → 攻击者重放同样 envelope → 余额不会再增加（幂等）。

        与 test_repeat_notify_idempotent 重叠但更明确突出"重放攻击"语义。
        """
        out_no = _create_pending_order(db_session, test_user.id, credits=100, amount_yuan=1)
        env = make_notify({"order_id": out_no, "order_st": "1", "order_amt": "100"})
        client.post("/api/recharge/fuiou-notify", json=env)
        # 假设攻击者一小时后再 POST 一次同样的 envelope（含原 sign）
        for _ in range(5):
            r = client.post("/api/recharge/fuiou-notify", json=env)
            assert r.status_code == 200 and r.text == "SUCCESS"
        from backend.app.models import User
        db_session.expire_all()
        # 用户余额仍然只 +100
        assert float(db_session.query(User).filter(User.id == test_user.id).first().credits) == 200.0

    def test_create_fen_amount_propagated_to_fuiou(
        self, client, patch_httpx_post, make_fuiou_response, fake_keys,
    ):
        """端到端：发给富友的报文 order_amt 必须等于 amount * 100（分）。"""
        from backend.app.services.fuiou_pay import _rsa_decrypt

        captured = patch_httpx_post(lambda url, body: make_fuiou_response(
            {"order_info": "https://qr.example/x"}, resp_code="0000"
        ))
        r = client.post("/api/recharge/fuiou-create", json={"price_yuan": 12, "credits": 1200})
        assert r.status_code == 200
        # 解密发给富友的 message
        msg_cipher = captured[0]["body"]["message"]
        plain = json.loads(_rsa_decrypt(msg_cipher, fake_keys["fuiou_priv"]))
        assert plain["order_amt"] == "1200"  # 12 元 = 1200 分
        assert plain["mchnt_cd"] == "0001000F0040992"
        assert plain["order_pay_type"] == "FAPPLET"
        assert plain["ver"] == "1.0.0"
        assert plain["back_notify_url"].endswith("/api/recharge/fuiou-notify")
