"""bhzn.top/admin 的商家审核接口（/admin/api/shop-merchants）。"""
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

from backend.app.api import admin as admin_api  # noqa: E402
from backend.app.core.config import settings  # noqa: E402
from backend.app.db import Base, get_db  # noqa: E402
from backend.app.shop_models import ShopMerchant, ShopOrder, ShopProduct  # noqa: E402

ADMIN_TOKEN = "lobster-admin-test-admin"
HEADERS = {"X-Admin-Token": ADMIN_TOKEN}


@pytest.fixture()
def ctx(monkeypatch):
    engine = create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)
    Base.metadata.create_all(engine)
    db = sessionmaker(bind=engine, autoflush=False, autocommit=False)()
    db.add(ShopMerchant(user_id=501, slug="apple-shop", company_name="\u7518\u8083\u82b1\u725b\u679c", status="pending", contact_phone="13800000001"))
    db.add(ShopMerchant(user_id=502, slug="coffee-shop", company_name="\u4e91\u5357\u5496\u5561\u8c46", status="active", contact_phone="13800000002"))
    db.commit()
    merchants = db.query(ShopMerchant).order_by(ShopMerchant.id).all()
    db.add(ShopProduct(merchant_id=merchants[0].id, spu="P1", title="\u82f9\u679c", price_cents=1000, stock=1, status="on_sale"))
    db.add(ShopOrder(order_no="N1", merchant_id=merchants[0].id, pay_amount_cents=1000, status="paid"))
    db.commit()

    monkeypatch.setattr(settings, "lobster_admin_username", "admin")
    monkeypatch.setattr(settings, "lobster_admin_password", "test-admin")
    app = FastAPI()
    app.include_router(admin_api.router)

    def _override():
        yield db

    app.dependency_overrides[get_db] = _override
    with TestClient(app) as client:
        yield client, merchants


def test_admin_shop_merchants_requires_admin_token(ctx):
    client, _ = ctx
    assert client.get("/admin/api/shop-merchants").status_code == 401
    assert client.get("/admin/api/shop-merchants", headers={"X-Admin-Token": "nope"}).status_code == 401
    assert client.get("/admin/api/shop-merchants", headers=HEADERS).status_code == 200


def test_admin_shop_merchants_pagination_query_detail_status(ctx):
    client, merchants = ctx
    page1 = client.get("/admin/api/shop-merchants?page=1&size=1", headers=HEADERS).json()
    assert page1["total"] == 2 and page1["pages"] == 2 and len(page1["items"]) == 1
    assert page1["items"][0]["slug"] == "coffee-shop"      # id 倒序
    assert page1["items"][0]["status_label"] == "\u6b63\u5e38"
    page2 = client.get("/admin/api/shop-merchants?page=2&size=1", headers=HEADERS).json()
    assert page2["items"][0]["slug"] == "apple-shop"

    assert client.get("/admin/api/shop-merchants?q=\u82b1\u725b", headers=HEADERS).json()["total"] == 1
    assert client.get("/admin/api/shop-merchants?q=13800000002", headers=HEADERS).json()["total"] == 1
    assert client.get("/admin/api/shop-merchants?status=pending", headers=HEADERS).json()["total"] == 1

    detail = client.get("/admin/api/shop-merchants/%d" % merchants[0].id, headers=HEADERS).json()
    assert detail["merchant"]["company_name"] == "\u7518\u8083\u82b1\u725b\u679c"
    assert len(detail["products"]) == 1 and len(detail["orders"]) == 1
    assert client.get("/admin/api/shop-merchants/999999", headers=HEADERS).status_code == 404

    patched = client.patch("/admin/api/shop-merchants/%d" % merchants[0].id, headers=HEADERS, json={"status": "active"})
    assert patched.status_code == 200 and patched.json()["merchant"]["status"] == "active"
    assert client.patch("/admin/api/shop-merchants/%d" % merchants[0].id, headers=HEADERS, json={"status": "oops"}).status_code == 400


def test_admin_shop_merchants_accepts_bearer_token(ctx):
    """页面里其他接口用 Authorization: Bearer，商家接口也一样认。"""
    client, _ = ctx
    r = client.get("/admin/api/shop-merchants", headers={"Authorization": "Bearer " + ADMIN_TOKEN})
    assert r.status_code == 200 and r.json()["total"] == 2
    bad = client.get("/admin/api/shop-merchants", headers={"Authorization": "Bearer lobster-admin-wrong"})
    assert bad.status_code == 401


def test_admin_shop_merchants_empty_header_message(ctx):
    """空白凭证要明确提示重新登录，而不是报 404/模糊错误。"""
    client, _ = ctx
    r = client.get("/admin/api/shop-merchants", headers={"X-Admin-Token": "   "})
    assert r.status_code == 401 and "重新登录" in r.json()["detail"]
