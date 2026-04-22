"""共享 fixtures：最小 FastAPI 装配 + 独立 SQLite + 模拟"富友"密钥对。

测试设计要点：
- 仓库 `certs/fuiou/` 里只有【富友公钥】，没有富友私钥（我们是商户端）。
- 测试需要"模拟富友" → 临时生成一对 RSA-2048 密钥充当模拟富友，
  monkeypatch FUIOU_FUIOU_PUBLIC_KEY_PATH 指向这把模拟公钥，
  这样测试里既能"以商户身份发出加密报文"也能"以富友身份解出 / 加密回应"。
- 同理商户密钥也独立生成一对，避免污染仓库里的真测试密钥。
"""
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


def _gen_pair(bits: int = 2048) -> tuple[Any, Any]:
    from Crypto.PublicKey import RSA

    key = RSA.generate(bits)
    return key, key.publickey()


@pytest.fixture(scope="session")
def fake_keys(tmp_path_factory) -> dict[str, Any]:
    """生成两对 RSA 密钥：模拟商户 + 模拟富友；返回 .pem 路径与 key 对象。"""
    base = tmp_path_factory.mktemp("fuiou_keys")
    merchant_priv, merchant_pub = _gen_pair(2048)
    fuiou_priv, fuiou_pub = _gen_pair(2048)
    paths = {}
    for name, key in [
        ("merchant_priv", merchant_priv),
        ("merchant_pub", merchant_pub),
        ("fuiou_priv", fuiou_priv),
        ("fuiou_pub", fuiou_pub),
    ]:
        p = base / f"{name}.pem"
        p.write_bytes(key.export_key("PEM"))
        paths[name + "_path"] = str(p)
    return {
        "merchant_priv": merchant_priv,
        "merchant_pub": merchant_pub,
        "fuiou_priv": fuiou_priv,
        "fuiou_pub": fuiou_pub,
        **paths,
    }


@pytest.fixture(scope="session")
def fake_keys_1024(tmp_path_factory) -> dict[str, Any]:
    """1024 bit 等价对，专门测富友测试环境真实位数。"""
    base = tmp_path_factory.mktemp("fuiou_keys_1024")
    mp, _ = _gen_pair(1024)
    fp, _ = _gen_pair(1024)
    pm = base / "merchant_priv_1024.pem"
    pf = base / "fuiou_pub_1024.pem"
    pm.write_bytes(mp.export_key("PEM"))
    pf.write_bytes(fp.publickey().export_key("PEM"))
    return {
        "merchant_priv": mp,
        "fuiou_pub": fp.publickey(),
        "merchant_priv_path": str(pm),
        "fuiou_pub_path": str(pf),
    }


@pytest.fixture(autouse=True)
def _reset_fuiou_caches():
    """每个测试前后清空模块级 _key_cache，避免 monkeypatch 路径变更后仍命中旧 key。"""
    from backend.app.services import fuiou_pay

    fuiou_pay._key_cache.clear()
    yield
    fuiou_pay._key_cache.clear()


@pytest.fixture
def patch_fuiou_settings(monkeypatch, fake_keys):
    """把 settings.fuiou_* 都指向"模拟富友"的密钥对，URL 用 example.test，避免任何真请求。"""
    from backend.app.core.config import settings

    monkeypatch.setattr(settings, "fuiou_mchnt_cd", "0001000F0040992", raising=False)
    monkeypatch.setattr(settings, "fuiou_merchant_private_key_path", fake_keys["merchant_priv_path"], raising=False)
    monkeypatch.setattr(settings, "fuiou_fuiou_public_key_path", fake_keys["fuiou_pub_path"], raising=False)
    monkeypatch.setattr(settings, "fuiou_gateway_pay_url", "https://example.test/aggpos/order.fuiou", raising=False)
    monkeypatch.setattr(settings, "fuiou_gateway_query_url", "https://example.test/aggpos/orderQuery.fuiou", raising=False)
    monkeypatch.setattr(settings, "fuiou_gateway_close_url", "https://example.test/close.fuiou", raising=False)
    monkeypatch.setattr(settings, "fuiou_order_pay_type", "FAPPLET", raising=False)
    monkeypatch.setattr(settings, "fuiou_ver", "1.0.0", raising=False)
    monkeypatch.setattr(settings, "fuiou_term_id", "", raising=False)
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

def _rsa_encrypt_with(plain: str, pub_key) -> str:
    """用任意 RSA 公钥分块加密 → b64。"""
    from backend.app.services.fuiou_pay import _rsa_encrypt

    return _rsa_encrypt(plain, pub_key)


def _rsa_decrypt_with(cipher_b64: str, priv_key) -> str:
    from backend.app.services.fuiou_pay import _rsa_decrypt

    return _rsa_decrypt(cipher_b64, priv_key)


def _rsa_sign_with(text: str, priv_key) -> str:
    from backend.app.services.fuiou_pay import _rsa_sign

    return _rsa_sign(text, priv_key)


@pytest.fixture
def make_notify(fake_keys):
    """工厂：传入业务字段 → 返回完整可用的"富友异步通知"信封 dict。

    富友返回数据 = 用商户公钥加密 message。
    通知里如果带 sign，是富友用富友私钥对明文 message 字符串签的。
    """
    def _build(plain: dict[str, Any], with_sign: bool = True, mchnt_cd: str = "0001000F0040992") -> dict[str, Any]:
        plain_text = json.dumps(plain, ensure_ascii=False, separators=(",", ":"))
        cipher = _rsa_encrypt_with(plain_text, fake_keys["merchant_pub"])
        env: dict[str, Any] = {"mchnt_cd": mchnt_cd, "message": cipher}
        if with_sign:
            env["sign"] = _rsa_sign_with(plain_text, fake_keys["fuiou_priv"])
        return env

    return _build


@pytest.fixture
def make_fuiou_response(fake_keys):
    """工厂：模拟"富友 HTTP 响应"信封：富友用商户公钥加密 message。"""
    def _build(plain: dict[str, Any], resp_code: str = "0000", resp_desc: str = "成功") -> dict[str, Any]:
        if plain:
            plain_text = json.dumps(plain, ensure_ascii=False, separators=(",", ":"))
            cipher = _rsa_encrypt_with(plain_text, fake_keys["merchant_pub"])
        else:
            cipher = ""
        return {
            "mchnt_cd": "0001000F0040992",
            "message": cipher,
            "resp_code": resp_code,
            "resp_desc": resp_desc,
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
