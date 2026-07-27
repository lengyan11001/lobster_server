"""共享 fixtures：最小 FastAPI 装配、独立 SQLite 和富友 MD5 聚合支付模拟。"""
from __future__ import annotations

import json
import os
import sys
import uuid
from pathlib import Path
from typing import Any, Iterator

import pytest

# 让 backend.* 可 import（pytest 从仓库根跑时）
ROOT = Path(__file__).resolve().parent.parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))


# ── 全局禁用 pydantic-settings 读 .env，避免污染 ──
os.environ.setdefault("LOBSTER_ADMIN_USERNAME", "")
os.environ.setdefault("LOBSTER_ADMIN_PASSWORD", "")


@pytest.fixture
def patch_fuiou_settings(monkeypatch):
    """配置当前 MD5 聚合支付字段，URL 使用 example.test，避免任何真实请求。"""
    from backend.app.core.config import settings

    monkeypatch.setattr(settings, "fuiou_mchnt_cd", "0001000F0040992", raising=False)
    monkeypatch.setattr(settings, "fuiou_mchnt_key", "test-fuiou-md5-secret", raising=False)
    monkeypatch.setattr(settings, "fuiou_precreate_url", "https://example.test/aggregatePay/preCreate", raising=False)
    monkeypatch.setattr(settings, "fuiou_query_url", "https://example.test/aggregatePay/commonQuery", raising=False)
    monkeypatch.setattr(settings, "fuiou_refund_url", "https://example.test/aggregatePay/commonRefund", raising=False)
    monkeypatch.setattr(settings, "fuiou_term_id", "88888888", raising=False)
    monkeypatch.setattr(settings, "fuiou_default_order_type", "WECHAT", raising=False)
    monkeypatch.setattr(settings, "fuiou_order_prefix", "10000", raising=False)
    monkeypatch.setattr(settings, "fuiou_ins_cd", None, raising=False)
    monkeypatch.setattr(settings, "fuiou_repeat_order", None, raising=False)
    monkeypatch.setattr(settings, "lobster_edition", "online", raising=False)
    monkeypatch.setattr(settings, "lobster_independent_auth", True, raising=False)
    return settings


@pytest.fixture
def db_engine(tmp_path):
    """每个测试一个独立 SQLite 文件 + 全部 ORM 表 create_all。"""
    from sqlalchemy import create_engine

    from backend.app.db import Base
    from backend.app import models  # noqa: F401  确保 models 注册到 Base.metadata

    db_path = tmp_path / "test.db"
    eng = create_engine(f"sqlite:///{db_path}", connect_args={"check_same_thread": False})
    Base.metadata.create_all(eng)
    yield eng
    eng.dispose()


@pytest.fixture
def db_session_factory(db_engine):
    from sqlalchemy.orm import sessionmaker

    return sessionmaker(autocommit=False, autoflush=False, bind=db_engine)


@pytest.fixture
def db_session(db_session_factory):
    s = db_session_factory()
    try:
        yield s
    finally:
        s.close()


@pytest.fixture
def test_user(db_session):
    """创建一个测试用户（id=1, 100 积分），返回 User 对象。"""
    from datetime import datetime
    from decimal import Decimal

    from backend.app.models import User

    u = User(
        email="alice@test.local",
        hashed_password="x",
        credits=Decimal("100.0000"),
        role="user",
        preferred_model="sutui",
        created_at=datetime.utcnow(),
    )
    db_session.add(u)
    db_session.commit()
    db_session.refresh(u)
    return u


@pytest.fixture
def other_user(db_session):
    """另一个用户，用于跨用户访问安全测试。"""
    from datetime import datetime
    from decimal import Decimal

    from backend.app.models import User

    u = User(
        email="bob@test.local",
        hashed_password="x",
        credits=Decimal("0.0000"),
        role="user",
        preferred_model="sutui",
        created_at=datetime.utcnow(),
    )
    db_session.add(u)
    db_session.commit()
    db_session.refresh(u)
    return u


@pytest.fixture
def client(db_session_factory, test_user, patch_fuiou_settings):
    """最小 FastAPI app：只挂 billing_router；override get_db + get_current_user。"""
    from fastapi import FastAPI
    from fastapi.testclient import TestClient

    from backend.app.api.auth import get_current_user
    from backend.app.api.billing import router as billing_router
    from backend.app.db import get_db

    app = FastAPI()
    app.include_router(billing_router, prefix="")

    def _get_db_override():
        s = db_session_factory()
        try:
            yield s
        finally:
            s.close()

    def _get_current_user_override():
        # 始终返回 test_user（id=1）
        s = db_session_factory()
        try:
            from backend.app.models import User
            return s.query(User).filter(User.id == test_user.id).first()
        finally:
            s.close()

    app.dependency_overrides[get_db] = _get_db_override
    app.dependency_overrides[get_current_user] = _get_current_user_override

    return TestClient(app)


@pytest.fixture
def client_as_other(db_session_factory, other_user, patch_fuiou_settings, test_user):
    """同一个 app，但 current_user 切到 other_user，用来测跨用户访问。"""
    from fastapi import FastAPI
    from fastapi.testclient import TestClient

    from backend.app.api.auth import get_current_user
    from backend.app.api.billing import router as billing_router
    from backend.app.db import get_db

    app = FastAPI()
    app.include_router(billing_router, prefix="")

    def _get_db_override():
        s = db_session_factory()
        try:
            yield s
        finally:
            s.close()

    def _get_current_user_override():
        s = db_session_factory()
        try:
            from backend.app.models import User
            return s.query(User).filter(User.id == other_user.id).first()
        finally:
            s.close()

    app.dependency_overrides[get_db] = _get_db_override
    app.dependency_overrides[get_current_user] = _get_current_user_override

    return TestClient(app)


# ── 工具：构造模拟富友异步通知 / 响应 ──


@pytest.fixture
def make_notify():
    """构造当前 MD5 协议的富友成功回调。"""
    def _build(
        values: dict[str, Any],
        with_sign: bool = True,
        mchnt_cd: str = "0001000F0040992",
    ) -> dict[str, Any]:
        from backend.app.services.fuiou_pay import _md5

        data: dict[str, Any] = {
            "result_code": "000000",
            "result_msg": "success",
            "mchnt_cd": mchnt_cd,
            "mchnt_order_no": values.get("mchnt_order_no") or values.get("order_id") or "",
            "settle_order_amt": str(values.get("settle_order_amt") or values.get("order_amt") or ""),
            "order_amt": str(values.get("order_amt") or ""),
            "txn_fin_ts": str(values.get("txn_fin_ts") or "20260727120000"),
            "reserved_fy_settle_dt": str(values.get("reserved_fy_settle_dt") or "20260727"),
            "random_str": str(values.get("random_str") or "notify-random"),
            "transaction_id": values.get("transaction_id") or values.get("channel_order_no") or "",
        }
        data.update({key: value for key, value in values.items() if key not in {"order_id", "order_st", "channel_order_no"}})
        if with_sign:
            parts = [
                data.get("result_code", ""),
                data.get("result_msg", ""),
                data.get("mchnt_cd", ""),
                data.get("mchnt_order_no", ""),
                data.get("settle_order_amt", ""),
                data.get("order_amt", ""),
                data.get("txn_fin_ts", ""),
                data.get("reserved_fy_settle_dt", ""),
                data.get("random_str", ""),
                "test-fuiou-md5-secret",
            ]
            data["full_sign"] = _md5("|".join(str(item) for item in parts))
        return data

    return _build


@pytest.fixture
def make_fuiou_response():
    """构造当前聚合支付下单或查单接口的 JSON 响应。"""
    def _build(
        values: dict[str, Any],
        result_code: str = "000000",
        result_msg: str = "成功",
    ) -> dict[str, Any]:
        return {
            "result_code": result_code,
            "result_msg": result_msg,
            "mchnt_cd": "0001000F0040992",
            "random_str": "response-random",
            **values,
        }

    return _build


@pytest.fixture
def patch_httpx_post(monkeypatch):
    """工厂：把 httpx.AsyncClient.post 替换为 callable，断言 URL/body 并返回构造好的 JSON。

    用法：
        patch_httpx_post(lambda url, body: ({"resp_code":"0000",...}, 200))
    """
    captured: list[dict[str, Any]] = []

    class _FakeResponse:
        def __init__(self, json_obj: dict, status: int = 200):
            self._j = json_obj
            self.status_code = status
            self.text = json.dumps(json_obj, ensure_ascii=False)

        def raise_for_status(self):
            if self.status_code >= 400:
                raise RuntimeError(f"HTTP {self.status_code}")

        def json(self):
            return self._j

    def _factory(handler):
        async def fake_post(self, url, json=None, headers=None, **kw):  # noqa: A002
            captured.append({"url": url, "body": json, "headers": headers})
            ret = handler(url, json or {})
            if isinstance(ret, tuple):
                resp_obj, status = ret
            else:
                resp_obj, status = ret, 200
            return _FakeResponse(resp_obj, status)

        import httpx
        monkeypatch.setattr(httpx.AsyncClient, "post", fake_post)
        return captured

    return _factory


def gen_out_trade_no() -> str:
    return f"R1_{uuid.uuid4().hex[:8]}"
