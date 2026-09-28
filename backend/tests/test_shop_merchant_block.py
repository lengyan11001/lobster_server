"""店铺被停用/驳回后：商家不能登录商家后台，也不能操作/上架。"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from backend.app.api.auth import router as auth_router  # noqa: E402
from backend.app.api.shop import cms_router, router as shop_router  # noqa: E402
from backend.app.db import Base, get_db  # noqa: E402
from backend.app.shop_models import ShopMerchant, ShopProduct  # noqa: E402

PHONE = "13900000007"
PASSWORD = "pass1234"


@pytest.fixture()
def ctx():
    engine = create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)
    Base.metadata.create_all(engine)
    db = sessionmaker(bind=engine, autoflush=False, autocommit=False)()
    app = FastAPI()
    app.include_router(auth_router, prefix="/auth")
    app.include_router(shop_router)
    app.include_router(cms_router)

    def _override():
        yield db

    app.dependency_overrides[get_db] = _override
    with TestClient(app) as client:
        yield client, db


def _login(client):
    return client.post("/auth/login-phone-password", json={"account": PHONE, "password": PASSWORD})


def _register(client):
    return client.post(
        "/api/shop-cms/merchants/register",
        json={"phone": PHONE, "password": PASSWORD, "company_name": "\u5929\u6c34\u82b1\u725b\u679c\u5e97"},
    )


def test_suspended_merchant_cannot_login_or_operate(ctx):
    client, db = ctx
    assert _register(client).status_code == 200
    login = _login(client)
    assert login.status_code == 200
    token = login.json()["access_token"]
    auth = {"Authorization": "Bearer " + token}

    created = client.post(
        "/api/shop-cms/products",
        headers=auth,
        json={"title": "\u82f9\u679c", "price_cents": 1999, "gallery": ["/shop-uploads/m1/a.png"], "detail_text": "\u597d\u5403"},
    )
    assert created.status_code == 200
    product_id = created.json()["product"]["id"]
    assert client.post("/api/shop-cms/products/%d/publish?on=true" % product_id, headers=auth).status_code == 200

    merchant = db.query(ShopMerchant).filter(ShopMerchant.slug.isnot(None)).first()
    merchant.status = "suspended"
    db.commit()

    blocked = _login(client)
    assert blocked.status_code == 403 and "\u505c\u7528" in blocked.json()["detail"]
    assert client.get("/api/shop-cms/products", headers=auth).status_code == 403
    assert client.post("/api/shop-cms/products/%d/publish?on=true" % product_id, headers=auth).status_code == 403
    assert client.get("/api/shop-cms/merchants/me", headers=auth).status_code == 403

    merchant.status = "rejected"
    db.commit()
    rejected = _login(client)
    assert rejected.status_code == 403 and "\u672a\u901a\u8fc7" in rejected.json()["detail"]

    merchant.status = "active"
    db.commit()
    assert _login(client).status_code == 200
    assert client.get("/api/shop-cms/products", headers=auth).status_code == 200
    assert client.post("/api/shop-cms/products/%d/publish?on=true" % product_id, headers=auth).status_code == 200
