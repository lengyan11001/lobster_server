# -*- coding: utf-8 -*-
"""投稿素材：投稿 → 商家只看得到带水印预览（不能下载）→ 采纳扣商家积分并给投稿人加积分。"""
import os
import sys
import tempfile
import threading
from decimal import Decimal
from http.server import BaseHTTPRequestHandler, HTTPServer

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.db import get_db
from app.models import Base, CreditLedger, User
from app.shop_models import ShopMerchant, ShopProduct, ShopProductSubmission
from app.api import shop as shop_api

IMAGE_BYTES = b""


def _start_image_server() -> str:
    """起一个本地 http 服务，模拟公网素材地址（预览接口要能拉图）。"""
    from PIL import Image
    import io as _io

    buf = _io.BytesIO()
    Image.new("RGB", (1600, 1200), (200, 120, 90)).save(buf, "PNG")
    payload = buf.getvalue()

    class Handler(BaseHTTPRequestHandler):
        def do_GET(self):  # noqa: N802
            self.send_response(200)
            self.send_header("Content-Type", "image/png")
            self.send_header("Content-Length", str(len(payload)))
            self.end_headers()
            self.wfile.write(payload)

        def log_message(self, *args):  # noqa: D102
            return

    server = HTTPServer(("127.0.0.1", 0), Handler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    return "http://127.0.0.1:%d/photo.png" % server.server_address[1]


IMAGE_URL = _start_image_server()

engine = create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)
Base.metadata.create_all(bind=engine, tables=[
    User.__table__, CreditLedger.__table__, ShopMerchant.__table__,
    ShopProduct.__table__, ShopProductSubmission.__table__,
])
SessionLocal = sessionmaker(bind=engine, autoflush=False, autocommit=False)

_db = SessionLocal()
submitter = User(email="submitter@example.com", hashed_password="x", credits=0)
merchant_user = User(email="merchant@example.com", hashed_password="x", credits=1000)
other_user = User(email="other@example.com", hashed_password="x", credits=1000)
_db.add_all([submitter, merchant_user, other_user])
_db.flush()
merchant = ShopMerchant(user_id=int(merchant_user.id), slug="demo", company_name="演示商家", status="active")
other_merchant = ShopMerchant(user_id=int(other_user.id), slug="other", company_name="别家", status="active")
_db.add_all([merchant, other_merchant])
_db.flush()
product = ShopProduct(merchant_id=int(merchant.id), spu="P1", title="苹果 15 手机壳", status="on_sale",
                      cover_url="https://cdn.example.com/cover.jpg", price_cents=9900,
                      media={"gallery": ["https://cdn.example.com/g1.jpg"]})
_db.add(product)
_db.commit()
IDS = {
    "submitter": int(submitter.id),
    "merchant_user": int(merchant_user.id),
    "other_user": int(other_user.id),
    "merchant": int(merchant.id),
    "other_merchant": int(other_merchant.id),
    "product": int(product.id),
}
_db.close()

app = FastAPI()
app.include_router(shop_api.router)
app.include_router(shop_api.cms_router)
CURRENT = {"user": None}


def _get_db():
    s = SessionLocal()
    try:
        yield s
    finally:
        s.close()


def _current_user():
    return CURRENT["user"]


app.dependency_overrides[get_db] = _get_db
app.dependency_overrides[shop_api.get_current_user] = _current_user
client = TestClient(app)


def as_user(key):
    s = SessionLocal()
    try:
        CURRENT["user"] = s.query(User).filter(User.id == IDS[key]).first()
    finally:
        s.close()


def credits_of(key):
    s = SessionLocal()
    try:
        return Decimal(str(s.query(User).filter(User.id == IDS[key]).first().credits or 0))
    finally:
        s.close()


def submit_image(title="实拍图"):
    import uuid as _uuid
    unique_url = IMAGE_URL + "?k=" + _uuid.uuid4().hex[:8]
    as_user("submitter")
    r = client.post("/api/shop/submissions", json={
        "product_id": IDS["product"], "source": "online_submit",
        "items": [{"asset_id": "a1", "url": unique_url, "thumb_url": unique_url, "media_type": "image", "title": title}],
    })
    assert r.status_code == 200, r.text
    return int(r.json()["items"][0]["id"])


def test_price_table_defaults():
    r = client.get("/api/shop/submissions/pricing")
    assert r.status_code == 200
    assert r.json()["credits"]["image"] == 100
    assert r.json()["credits"]["video"] == 300


def test_merchant_cannot_see_or_download_original_before_accept():
    sid = submit_image("未采纳素材")
    as_user("merchant_user")
    r = client.get("/api/shop-cms/submissions?product_id=%d" % IDS["product"])
    item = [x for x in r.json()["items"] if x["id"] == sid][0]
    assert item["url"] == "", "未采纳时不能把原素材地址给商家"
    assert item["accepted"] is False
    assert item["price_credits"] == 100
    assert item["preview_url"].endswith("/preview")

    r = client.get("/api/shop-cms/submissions/%d/download" % sid)
    assert r.status_code == 403, r.text

    r = client.post("/api/shop-cms/submissions/%d/use" % sid, json={"mode": "download"})
    assert r.status_code == 403, r.text

    r = client.get("/api/shop-cms/submissions/%d/preview" % sid)
    assert r.status_code == 200, r.text
    assert r.headers["content-type"].startswith("image/jpeg")
    assert len(r.content) > 1000
    assert len(r.content) != len(bytes(0)) and r.content[:2] == b"\xff\xd8", "预览必须是 jpeg"


def test_preview_is_downscaled_and_watermarked():
    from PIL import Image
    import io as _io

    sid = submit_image("水印素材")
    as_user("merchant_user")
    r = client.get("/api/shop-cms/submissions/%d/preview" % sid)
    assert r.status_code == 200
    img = Image.open(_io.BytesIO(r.content))
    assert img.width <= 720, img.size
    # 水印：把原图（纯色 1600x1200）与预览对比，预览里必须有明显不同的像素
    colors = img.convert("RGB").getcolors(maxcolors=100000)
    assert len(colors) > 5, "预览图应当带水印文字，而不是纯色原图"


def test_accept_charges_merchant_and_pays_submitter():
    before_merchant = credits_of("merchant_user")
    before_submitter = credits_of("submitter")
    sid = submit_image("要采纳的图")
    as_user("merchant_user")
    r = client.post("/api/shop-cms/submissions/%d/accept" % sid)
    assert r.status_code == 200, r.text
    body = r.json()
    assert float(body["charged_credits"]) == 100.0, body
    assert float(body["price_credits"]) == 100.0
    assert credits_of("merchant_user") == before_merchant - Decimal("100")
    assert credits_of("submitter") == before_submitter + Decimal("100")

    # 素材已进商品素材
    s = SessionLocal()
    prod = s.query(ShopProduct).filter(ShopProduct.id == IDS["product"]).first()
    urls = [m["url"] for m in (prod.media or {}).get("materials") or []]
    assert any(u.startswith(IMAGE_URL) for u in urls), urls
    # 流水两笔：商家 -100、投稿人 +100
    rows = s.query(CreditLedger).filter(CreditLedger.ref_type == "shop_submission").all()
    deltas = sorted(float(x.delta) for x in rows if str(x.ref_id) == str(sid))
    assert deltas == [-100.0, 100.0], deltas
    s.close()

    # 采纳后才给原素材，并且可下载
    r = client.get("/api/shop-cms/submissions?product_id=%d" % IDS["product"])
    item = [x for x in r.json()["items"] if x["id"] == sid][0]
    assert item["url"].startswith(IMAGE_URL) and item["accepted"] is True
    r = client.get("/api/shop-cms/submissions/%d/download" % sid)
    assert r.status_code == 200 and r.json()["url"].startswith(IMAGE_URL)


def test_accept_is_idempotent():
    sid = submit_image("重复采纳")
    as_user("merchant_user")
    client.post("/api/shop-cms/submissions/%d/accept" % sid)
    after_first = credits_of("merchant_user")
    r = client.post("/api/shop-cms/submissions/%d/accept" % sid)
    assert r.status_code == 200
    assert r.json()["already_accepted"] is True
    assert float(r.json()["charged_credits"]) == 0.0
    assert credits_of("merchant_user") == after_first


def test_insufficient_credits_blocks_and_changes_nothing():
    s = SessionLocal()
    s.query(User).filter(User.id == IDS["merchant_user"]).update({"credits": 50}, synchronize_session=False)
    s.commit()
    s.close()
    sid = submit_image("余额不足")
    as_user("merchant_user")
    r = client.post("/api/shop-cms/submissions/%d/accept" % sid)
    assert r.status_code == 402, r.text
    assert "算力不足" in r.json()["detail"]
    assert credits_of("merchant_user") == Decimal("50")
    assert credits_of("submitter") == credits_of("submitter")
    s = SessionLocal()
    row = s.query(ShopProductSubmission).filter(ShopProductSubmission.id == sid).first()
    assert row.accepted_at is None and row.status == "new"
    s.close()
    s = SessionLocal()
    s.query(User).filter(User.id == IDS["merchant_user"]).update({"credits": 5000}, synchronize_session=False)
    s.commit()
    s.close()


def test_other_merchant_cannot_touch_submission():
    sid = submit_image("别家看不到")
    as_user("other_user")
    assert client.get("/api/shop-cms/submissions/%d/preview" % sid).status_code == 404
    assert client.post("/api/shop-cms/submissions/%d/accept" % sid).status_code == 404
    assert client.get("/api/shop-cms/submissions/%d/download" % sid).status_code == 404


def test_submitter_sees_own_original():
    sid = submit_image("我的投稿")
    as_user("submitter")
    r = client.get("/api/shop/submissions/mine?product_id=%d" % IDS["product"])
    item = [x for x in r.json()["items"] if x["id"] == sid][0]
    assert item["url"].startswith(IMAGE_URL)
    assert item["accepted"] is False
    assert item["price_credits"] == 100
