# -*- coding: utf-8 -*-
"""本地实测：投稿 → 我的投稿 → 商家查看 → 使用(attach) 全链路（SQLite 内存库 + TestClient）。"""
import sys, os
sys.path.insert(0, r"D:\lobster_server\backend")

from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.db import get_db
from app.models import Base, User
from app.shop_models import ShopMerchant, ShopProduct, ShopProductSubmission
from app.api import shop as shop_api

engine = create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)
Base.metadata.create_all(bind=engine, tables=[
    User.__table__, ShopMerchant.__table__, ShopProduct.__table__, ShopProductSubmission.__table__,
])
SessionLocal = sessionmaker(bind=engine, autoflush=False, autocommit=False)



def test_submission_flow():
    """投稿 → 我的投稿 → 商家查看 → 使用（attach/download）全链路。"""
    db = SessionLocal()
    buyer = User(email="submitter@example.com", hashed_password="x")
    db.add(buyer)
    merchant_user = User(email="merchant@example.com", hashed_password="x")
    db.add(merchant_user)
    db.flush()
    merchant = ShopMerchant(user_id=int(merchant_user.id), slug="demo-shop", company_name="演示商家", status="active")
    db.add(merchant)
    db.flush()
    product = ShopProduct(merchant_id=int(merchant.id), spu="P0001", title="苹果 15 手机壳", status="on_sale",
                          cover_url="https://cdn.example.com/cover.jpg", price_cents=9900,
                          media={"gallery": ["https://cdn.example.com/g1.jpg"], "detail_images": ["https://cdn.example.com/d1.jpg"]})
    db.add(product)
    db.commit()
    db.refresh(product)
    pid = int(product.id)
    uid_submitter = int(buyer.id)
    uid_merchant = int(merchant_user.id)
    merchant_id = int(merchant.id)
    db.close()

    app = FastAPI()
    app.include_router(shop_api.router)
    app.include_router(shop_api.cms_router)

    def _db():
        s = SessionLocal()
        try:
            yield s
        finally:
            s.close()

    CURRENT = {"user": None}
    def _current_user():
        return CURRENT["user"]

    app.dependency_overrides[get_db] = _db
    app.dependency_overrides[shop_api.get_current_user] = _current_user
    client = TestClient(app)

    def as_user(uid):
        s = SessionLocal()
        try:
            CURRENT["user"] = s.query(User).filter(User.id == uid).first()
        finally:
            s.close()

    # 1) 投稿
    as_user(uid_submitter)
    r = client.post("/api/shop/submissions", json={
        "product_id": pid, "note": "实拍图", "source": "online_submit",
        "items": [
            {"asset_id": "a1", "url": "https://cdn.example.com/u1.jpg", "thumb_url": "https://cdn.example.com/u1.jpg",
             "media_type": "image", "title": "实拍图1"},
            {"asset_id": "a2", "url": "https://cdn.example.com/u2.mp4", "media_type": "video", "title": "短视频"},
        ],
    })
    print("POST submissions:", r.status_code, r.json().get("created"), r.json().get("total"))
    assert r.status_code == 200 and r.json()["created"] == 2
    sub_ids = [it["id"] for it in r.json()["items"]]

    # 1b) 重复投稿同一素材 -> 不新建
    r = client.post("/api/shop/submissions", json={"product_id": pid, "items": [{"url": "https://cdn.example.com/u1.jpg", "media_type": "image"}]})
    print("POST submissions (dup):", r.status_code, "created=", r.json().get("created"), "total=", r.json().get("total"))
    assert r.json()["created"] == 0 and r.json()["total"] == 1

    # 1c) 空素材被拦
    r = client.post("/api/shop/submissions", json={"product_id": pid, "items": []})
    print("POST submissions (empty):", r.status_code, r.json().get("detail"))
    assert r.status_code == 400

    # 2) 我的投稿
    r = client.get("/api/shop/submissions/mine?page=1&size=10")
    body = r.json()
    print("GET mine:", r.status_code, "total=", body["total"], "product=", body["items"][0]["product"]["title"], "merchant=", body["items"][0]["merchant"]["company_name"])
    assert r.status_code == 200 and body["total"] == 2

    # 3) 商品详情（商家素材 + 我的投稿）
    r = client.get("/api/shop/submissions/product/%d" % pid)
    body = r.json()
    print("GET product detail:", r.status_code, "materials=", [m["kind"] for m in body["materials"]], "mine=", len(body["my_submissions"]))
    assert r.status_code == 200 and {m["kind"] for m in body["materials"]} >= {"cover", "gallery", "detail"}
    assert len(body["my_submissions"]) == 2

    # 4) 商家查看
    as_user(uid_merchant)
    r = client.get("/api/shop-cms/submissions?keyword=%E8%8B%B9%E6%9E%9C")
    body = r.json()
    print("GET cms submissions (keyword 苹果):", r.status_code, "total=", body["total"], "submitter=", body["items"][0]["submitter"]["phone"], "product=", body["items"][0]["product"]["title"])
    assert r.status_code == 200 and body["total"] == 2

    r = client.get("/api/shop-cms/submissions?product_id=%d&status=new" % pid)
    print("GET cms submissions (product+status):", r.status_code, r.json()["total"])
    assert r.json()["total"] == 2

    # 5) 使用（下载）
    sid = sub_ids[0]
    r = client.post("/api/shop-cms/submissions/%d/use" % sid, json={"mode": "download"})
    print("POST use(download):", r.status_code, r.json().get("mode"), r.json().get("url"), "status=", r.json()["item"]["status"])
    assert r.status_code == 200 and r.json()["item"]["status"] == "used"

    # 6) 使用（放进商品素材）
    sid2 = sub_ids[1]
    r = client.post("/api/shop-cms/submissions/%d/use" % sid2, json={"mode": "attach"})
    body = r.json()
    print("POST use(attach):", r.status_code, body.get("mode"), "materials=", [m["url"] for m in (body["product"]["media"].get("materials") or [])])
    assert r.status_code == 200

    s = SessionLocal()
    prod = s.query(ShopProduct).filter(ShopProduct.id == pid).first()
    print("DB product.media.materials:", [(m["url"], m.get("submission_id")) for m in (prod.media or {}).get("materials") or []])
    assert len((prod.media or {}).get("materials") or []) == 1

    # 7) 商家自己看商品详情时，采纳的投稿素材也在 materials 里
    as_user(uid_submitter)
    r = client.get("/api/shop/submissions/product/%d" % pid)
    kinds = [(m["kind"], m["url"]) for m in r.json()["materials"]]
    print("product materials after attach:", kinds)
    assert any(k == "submission" for k, _ in kinds)
    s.close()

    # 8) 别的商家看不到这条投稿
    other = User(email="other@example.com", hashed_password="x")
    s = SessionLocal(); s.add(other); s.flush(); s.add(ShopMerchant(user_id=int(other.id), slug="other", company_name="别家", status="active")); s.commit(); other_id = int(other.id); s.close()
    as_user(other_id)
    r = client.get("/api/shop-cms/submissions")
    print("other merchant sees:", r.status_code, r.json()["total"])
    assert r.json()["total"] == 0
    r = client.post("/api/shop-cms/submissions/%d/use" % sid2, json={"mode": "attach"})
    print("other merchant use ->", r.status_code, r.json().get("detail"))
    assert r.status_code == 404

    print("\nALL SUBMISSION TESTS PASSED")
