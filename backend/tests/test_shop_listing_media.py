"""2026-10-11：商品拆两套素材 —— 商家自己上传的图（media） vs 独立站素材（listing_media）。

方案 A：状态（status）只管独立站上下架；online 赚钱能不能投看「资料完善」，
两者解耦，已上架也能继续采纳新素材改 listing_media。
"""
from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from backend.app.api.shop import (  # noqa: E402
    ON_SALE,
    _normalized_listing_media,
    _product_payload,
    _product_ready_for_earn,
)
from backend.app.shop_models import ShopProduct  # noqa: E402


def _product(**kwargs) -> ShopProduct:
    base = {"merchant_id": 1, "title": "\u684c\u9762\u52a0\u6e7f\u5668", "price_cents": 9900, "status": "draft",
            "cover_url": "/shop-uploads/m1/cover.png", "media": {"gallery": ["/shop-uploads/m1/a.png"]}}
    base.update(kwargs)
    return ShopProduct(**base)


def test_listing_falls_back_to_merchant_images():
    """没配独立站素材时，店铺页退回商家自己上传的图（老商品不炸）。"""
    listing = _normalized_listing_media(_product())
    assert listing["cover"] == "/shop-uploads/m1/cover.png"
    assert listing["gallery"] == ["/shop-uploads/m1/a.png"]
    assert listing["from_listing"] is False


def test_listing_media_wins_over_merchant_images():
    """配了独立站素材就用它，商家自己上传的图不动（两套分开）。"""
    product = _product(listing_media={"cover": "/listing/cover2.png",
                                      "gallery": ["/listing/g1.png"],
                                      "detail_images": ["/listing/d1.png"],
                                      "updated_at": "2026-10-11T10:00:00"})
    listing = _normalized_listing_media(product)
    assert listing["cover"] == "/listing/cover2.png"
    assert listing["gallery"] == ["/listing/g1.png"]
    assert listing["detail_images"] == ["/listing/d1.png"]
    assert listing["from_listing"] is True
    assert product.media["gallery"] == ["/shop-uploads/m1/a.png"]   # 原始资料不受影响


def test_ready_for_earn_is_independent_from_on_sale():
    """资料完善 = 有标题 + 有价格 + 有图；draft/上架都算可投。"""
    assert _product_ready_for_earn(_product(status="draft")) is True
    assert _product_ready_for_earn(_product(status=ON_SALE)) is True
    assert _product_ready_for_earn(_product(title="")) is False
    assert _product_ready_for_earn(_product(price_cents=0)) is False
    assert _product_ready_for_earn(_product(cover_url="", media={})) is False


def test_payload_exposes_listing_and_ready_flag():
    payload = _product_payload(_product(status="draft"))
    assert payload["ready_for_earn"] is True
    assert payload["listing"]["gallery"] == ["/shop-uploads/m1/a.png"]
    assert payload["gallery"] == ["/shop-uploads/m1/a.png"]        # 原始资料仍照旧给
    assert payload["status"] == "draft"
