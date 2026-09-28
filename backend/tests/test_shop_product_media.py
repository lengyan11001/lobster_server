"""商家后台商品图片/详情：只从上传图片与纯文字来（不接受 URL / JSON / HTML 文本）。"""
from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from backend.app.api.shop import ProductReq, _apply_product_media, _text_blocks_to_html  # noqa: E402
from backend.app.shop_models import ShopProduct  # noqa: E402


def test_text_blocks_to_html_splits_and_escapes():
    html = _text_blocks_to_html("\u5356\u70b9\uff1a<b>\u65b0\u9c9c</b>\n\n\u7834\u635f\u5305\u8d54")
    assert html == "<p>\u5356\u70b9\uff1a&lt;b&gt;\u65b0\u9c9c&lt;/b&gt;</p><p>\u7834\u635f\u5305\u8d54</p>"


def test_apply_product_media_uses_uploaded_images_and_plain_text():
    product = ShopProduct(merchant_id=1, title="\u82f9\u679c")
    body = ProductReq(
        title="\u82f9\u679c",
        gallery=["/shop-uploads/m1/a.png", "/shop-uploads/m1/b.png"],
        detail_images=["/shop-uploads/m1/c.png"],
        detail_text="\u7b2c\u4e00\u884c\n\u7b2c\u4e8c\u884c",
        specs={"\u4ea7\u5730": "\u7518\u8083\u5929\u6c34"},
        detail_html="<script>alert(1)</script>",
    )
    _apply_product_media(product, body)
    assert product.cover_url == "/shop-uploads/m1/a.png"          # 第一张图自动作为主图
    assert product.media["gallery"] == ["/shop-uploads/m1/a.png", "/shop-uploads/m1/b.png"]
    assert product.media["detail_images"] == ["/shop-uploads/m1/c.png"]
    assert product.media["detail_text"] == "\u7b2c\u4e00\u884c\n\u7b2c\u4e8c\u884c"
    assert product.detail_html == "<p>\u7b2c\u4e00\u884c</p><p>\u7b2c\u4e8c\u884c</p>"   # 纯文字自动转段落
    assert "<script>" not in product.detail_html


def test_apply_product_media_keeps_uploaded_cover_when_body_has_no_gallery():
    product = ShopProduct(
        merchant_id=1,
        title="\u82f9\u679c",
        cover_url="/shop-uploads/m1/keep.png",
        media={"gallery": ["/shop-uploads/m1/keep.png"]},
    )
    body = ProductReq(title="\u82f9\u679c", cover_url="https://evil.example/x.png")
    _apply_product_media(product, body)
    assert product.cover_url == "/shop-uploads/m1/keep.png"
    assert product.media["gallery"] == ["/shop-uploads/m1/keep.png"]
