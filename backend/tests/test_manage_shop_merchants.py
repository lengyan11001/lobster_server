"""管理后台商家 tab：列表分页 / 查询 / 明细 / 状态调整（仅平台管理员）。"""
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

from backend.app.api import manage as manage_api  # noqa: E402
from backend.app.core.config import settings  # noqa: E402
from backend.app.db import Base, get_db  # noqa: E402
from backend.app.shop_models import ShopCommission, ShopMerchant, ShopOrder, ShopProduct  # noqa: E402

ADMIN_HEADERS = {"Authorization": "Bearer lobster-admin-test-admin"}


@pytest.fixture()
def ctx(monkeypatch):
    engine = create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)
    Session = sessionmaker(bind=engine, autoflush=False, autocommit=False)
    Base.metadata.create_all(engine)
    db = Session()
    seeds = [
        ("\u7518\u8083\u82b1\u725b\u679c\u4e13\u8425\u5e97", "apple-shop", "active", "13800000001"),
        ("\u4e91\u5357\u5496\u5561\u8c46\u5b50", "coffee-shop", "pending", "13800000002"),
        ("\u65b0\u7586\u548c\u7530\u7389\u67a3", "jujube-shop", "suspended", "13800000003"),
    ]
    for idx, (name, slug, status, phone) in enumerate(seeds, start=1):
        db.add(ShopMerchant(user_id=200 + idx, slug=slug, company_name=name, status=status, contact_phone=phone))
    db.commit()
    merchants = db.query(ShopMerchant).order_by(ShopMerchant.id).all()
    for i in range(3):
        db.add(ShopProduct(merchant_id=merchants[0].id, spu="P%d" % i, title="\u5546\u54c1%d" % i,
                           price_cents=1000, stock=1, status="on_sale" if i < 2 else "draft", commission_bp=1000))
    db.add(ShopOrder(order_no="NO1", merchant_id=merchants[0].id, pay_amount_cents=5000, status="paid",
                     commission_cents=500, commission_status="pending"))
    db.commit()
    db.add(ShopCommission(order_id=1, promoter_user_id=9, merchant_id=merchants[0].id, product_id=1,
                          base_cents=5000, rate_bp=1000, amount_cents=500, status="pending"))
    db.commit()

    monkeypatch.setattr(settings, "lobster_admin_password", "test-admin")
    app = FastAPI()
    app.include_router(manage_api.router)

    def _override():
        yield db

    app.dependency_overrides[get_db] = _override
    with TestClient(app) as client:
        yield client, merchants


def test_shop_merchants_requires_admin(ctx):
    client, _ = ctx
    assert client.get("/api/manage/shop-merchants").status_code == 401
    assert client.get("/api/manage/shop-merchants", headers={"Authorization": "Bearer nope"}).status_code == 401


def test_shop_merchants_pagination_and_query(ctx):
    client, merchants = ctx
    page1 = client.get("/api/manage/shop-merchants?page=1&size=2", headers=ADMIN_HEADERS).json()
    assert page1["total"] == 3 and page1["pages"] == 2 and page1["size"] == 2
    assert len(page1["items"]) == 2
    page2 = client.get("/api/manage/shop-merchants?page=2&size=2", headers=ADMIN_HEADERS).json()
    assert len(page2["items"]) == 1
    assert {i["id"] for i in page1["items"]} & {i["id"] for i in page2["items"]} == set()

    by_name = client.get("/api/manage/shop-merchants?q=\u5496\u5561", headers=ADMIN_HEADERS).json()
    assert by_name["total"] == 1 and by_name["items"][0]["slug"] == "coffee-shop"
    by_phone = client.get("/api/manage/shop-merchants?q=13800000003", headers=ADMIN_HEADERS).json()
    assert by_phone["total"] == 1 and by_phone["items"][0]["slug"] == "jujube-shop"
    by_status = client.get("/api/manage/shop-merchants?status=active", headers=ADMIN_HEADERS).json()
    assert by_status["total"] == 1 and by_status["items"][0]["slug"] == "apple-shop"


def test_shop_merchants_aggregates_detail_and_status(ctx):
    client, merchants = ctx
    data = client.get("/api/manage/shop-merchants?q=apple", headers=ADMIN_HEADERS).json()
    row = data["items"][0]
    assert row["product_count"] == 3 and row["on_sale_count"] == 2
    assert row["order_count"] == 1 and row["pay_amount_cents"] == 5000
    assert row["commission_pending_cents"] == 500 and row["commission_settled_cents"] == 0
    assert data["summary"]["products_total"] == 3 and data["summary"]["merchants_total"] == 1

    detail = client.get("/api/manage/shop-merchants/%d" % row["id"], headers=ADMIN_HEADERS).json()
    assert detail["ok"] is True and len(detail["products"]) == 3 and len(detail["orders"]) == 1

    patched = client.patch("/api/manage/shop-merchants/%d" % merchants[1].id, headers=ADMIN_HEADERS,
                           json={"status": "active"})
    assert patched.status_code == 200 and patched.json()["merchant"]["status"] == "active"
    assert client.patch("/api/manage/shop-merchants/%d" % merchants[1].id, headers=ADMIN_HEADERS,
                        json={"status": "whatever"}).status_code == 400
