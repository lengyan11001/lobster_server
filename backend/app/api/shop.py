"""shop / shopcms API（P0 骨架，2026-09-28）。

- /api/shop/*      独立站（公开）+ 选品广场 + 推广链接（登录用户）
- /api/shop-cms/*  商家后台（公司、商品、分佣配置；复用现有 users 认证）

设计见 start_entry_sandbox/shop_plan/DESIGN.md。
"""
from __future__ import annotations

import hashlib
import hmac
import html
import os
import secrets
import time
from decimal import Decimal
from datetime import datetime
import subprocess
import tempfile
import urllib.request
from pathlib import Path
from typing import Any, Dict, List, Optional

from fastapi import APIRouter, Depends, File, HTTPException, Request, UploadFile
from fastapi.responses import FileResponse, Response
from fastapi.security import OAuth2PasswordBearer
from pydantic import BaseModel, Field
from sqlalchemy import func
from sqlalchemy.orm import Session
from sqlalchemy.orm.attributes import flag_modified

from ..db import get_db
from ..core.config import settings
from ..models import User
from ..services import shop_commission as sc
from ..services import shop_submission_billing as ssb
from ..services.credits_amount import credits_json_float
from ..services.shop_theme import resolve_theme, theme_css_vars
from ..services.shop_merchant_status import shop_merchant_blocked_reason
from ..shop_models import (
    ShopCommission,
    ShopMerchant,
    ShopOrder,
    ShopOrderItem,
    ShopProduct,
    ShopProductSubmission,
    ShopReferral,
    ShopReferralClick,
)
from .auth import PHONE_EMAIL_SUFFIX, get_current_user, get_password_hash

router = APIRouter()          # 独立站 / 选品广场（公开 + 登录用户）
cms_router = APIRouter()      # 商家后台

SHOP_BASE_URL = "https://shop.bhzn.top"
SHOP_UPLOADS_DIR = Path(__file__).resolve().parents[3] / "shop_uploads"
SHOP_UPLOADS_URL = "/shop-uploads"
SHOP_IMAGE_EXTENSIONS = {"jpg", "jpeg", "png", "webp", "gif"}
SHOP_IMAGE_MAX_BYTES = 8 * 1024 * 1024
MERCHANT_ROLE = "merchant"
ON_SALE = "on_sale"


# ────────────────────────── 公共小工具 ──────────────────────────

def _slug_of(name: str) -> str:
    base = "".join(ch for ch in (name or "") if ch.isalnum()).lower()[:24] or "shop"
    return f"{base}-{secrets.token_hex(3)}"


def _merchant_of(db: Session, user: User) -> ShopMerchant:
    merchant = db.query(ShopMerchant).filter(ShopMerchant.user_id == int(user.id)).first()
    if merchant is None:
        raise HTTPException(status_code=403, detail="当前账号还不是商家，请先注册开店")
    # 店铺被停用 / 驳回后，商家后台所有接口（含上架）都不再可用
    blocked = shop_merchant_blocked_reason(merchant.status)
    if blocked:
        raise HTTPException(status_code=403, detail=blocked)
    return merchant


def _text_blocks_to_html(text: str) -> str:
    """把商家输入的纯文字转成安全段落 HTML（商家不需要写 HTML）。"""
    blocks = str(text or "").replace("\r\n", "\n").replace("\r", "\n").split("\n")
    return "".join(f"<p>{html.escape(block.strip())}</p>" for block in blocks if block.strip())


def _apply_product_media(product: ShopProduct, body: "ProductReq") -> None:
    """商品图片/详情只从上传结果与纯文字来：不接受 URL、JSON、HTML 文本。"""
    media = dict(product.media or {})
    gallery = [str(u).strip() for u in (body.gallery or []) if str(u or "").strip()]
    detail_images = [str(u).strip() for u in (body.detail_images or []) if str(u or "").strip()]
    if gallery:
        media["gallery"] = gallery
    if detail_images:
        media["detail_images"] = detail_images
    detail_text = str(body.detail_text or "").strip()
    if detail_text:
        media["detail_text"] = detail_text
        product.detail_html = _text_blocks_to_html(detail_text)
    product.media = media
    if gallery:
        product.cover_url = gallery[0]
    elif not product.cover_url and str(body.cover_url or "").strip():
        product.cover_url = str(body.cover_url).strip()


def _product_payload(product: ShopProduct, merchant: Optional[ShopMerchant] = None, *, promoter_view: bool = False) -> Dict[str, Any]:
    data: Dict[str, Any] = {
        "id": product.id,
        "title": product.title,
        "subtitle": product.subtitle,
        "category": product.category,
        "brand": product.brand,
        "cover_url": product.cover_url,
        "media": product.media or {},
        "gallery": (product.media or {}).get("gallery", []),
        "detail_images": (product.media or {}).get("detail_images", []),
        "detail_text": (product.media or {}).get("detail_text", ""),
        "detail_html": product.detail_html or "",
        "specs": product.specs or {},
        "commission_bp_own": int(product.commission_bp or 0),
        "price_cents": int(product.price_cents or 0),
        "market_price_cents": int(product.market_price_cents or 0),
        "stock": int(product.stock or 0),
        "sales": int(product.sales or 0),
        "tags": [t for t in str(product.tags or "").split(",") if t],
        "status": product.status,
        "merchant_id": product.merchant_id,
    }
    if merchant is not None:
        data["merchant"] = {"slug": merchant.slug, "company_name": merchant.company_name, "logo_url": merchant.logo_url}
        data["commission_bp"] = sc.effective_rate_bp(product.commission_bp, merchant.commission_default_bp, commission_type=product.commission_type)
        data["commission_estimate_cents"] = sc.compute_commission(
            pay_amount_cents=int(product.price_cents or 0),
            product_bp=product.commission_bp,
            merchant_default_bp=merchant.commission_default_bp,
            commission_type=product.commission_type,
            commission_fixed_cents=product.commission_fixed_cents,
        ).amount_cents
    if promoter_view:
        data["referral_link"] = None
    return data


# ── 访客（无登录态）也能下单：无 token 返回 None，无效 token 不 401；带 token 时仍解析出用户 ──
_optional_bearer = OAuth2PasswordBearer(tokenUrl="/auth/login", auto_error=False)


async def get_optional_user(
    request: Request,
    token: Optional[str] = Depends(_optional_bearer),
    db: Session = Depends(get_db),
) -> Optional[User]:
    if not token:
        return None
    try:
        return await get_current_user(request, token=token, db=db)
    except HTTPException:
        return None

# ────────────────────────── 商家后台 /api/shop-cms ──────────────────────────

class MerchantRegisterReq(BaseModel):
    phone: str = Field(..., min_length=6, max_length=32)
    password: str = Field(..., min_length=6, max_length=64)
    company_name: str = Field(..., min_length=1, max_length=128)
    company_type: str = "company"
    license_no: str = ""
    contact_name: str = ""
    region: str = ""


class MerchantUpdateReq(BaseModel):
    company_name: Optional[str] = None
    company_type: Optional[str] = None
    license_no: Optional[str] = None
    region: Optional[str] = None
    contact_name: Optional[str] = None
    contact_phone: Optional[str] = None
    intro: Optional[str] = None
    logo_url: Optional[str] = None
    banner_url: Optional[str] = None
    commission_default_bp: Optional[int] = Field(None, ge=0, le=5000)
    platform_fee_bp: Optional[int] = Field(None, ge=0, le=5000)
    commission_cap_cents: Optional[int] = Field(None, ge=0)
    commission_min_cents: Optional[int] = Field(None, ge=0)
    attribution_days: Optional[int] = Field(None, ge=1, le=365)
    settle_type: Optional[str] = None
    settle_account: Optional[str] = None
    settle_holder: Optional[str] = None
    theme: Optional[dict] = None


class ProductReq(BaseModel):
    title: str = Field(..., min_length=1, max_length=200)
    subtitle: str = ""
    category: str = ""
    keywords: str = ""
    brand: str = ""
    cover_url: str = ""
    # 图片/详情都由后台上传控件与纯文字产生，不接受 URL / JSON / HTML 文本
    gallery: List[str] = Field(default_factory=list)
    detail_images: List[str] = Field(default_factory=list)
    detail_text: str = ""
    media: Optional[dict] = None
    detail_html: str = ""
    specs: Optional[dict] = None
    price_cents: int = Field(0, ge=0)
    market_price_cents: int = Field(0, ge=0)
    stock: int = Field(0, ge=0)
    commission_bp: int = Field(0, ge=0, le=5000)
    commission_type: str = "percent"
    commission_fixed_cents: int = Field(0, ge=0)
    tags: str = ""
    status: Optional[str] = None


@cms_router.post("/api/shop-cms/merchants/register", summary="商家注册开店")
def merchant_register(body: MerchantRegisterReq, db: Session = Depends(get_db)) -> Dict[str, Any]:
    email = f"{body.phone.strip()}{PHONE_EMAIL_SUFFIX}"  # 与 /auth 手机号登录一致
    exists = db.query(User).filter(User.email == email).first()
    if exists is not None:
        raise HTTPException(status_code=400, detail="该手机号已注册，请直接登录")
    user = User(email=email, hashed_password=get_password_hash(body.password), role=MERCHANT_ROLE, credits=0)
    db.add(user)
    db.flush()
    merchant = ShopMerchant(
        user_id=int(user.id),
        slug=_slug_of(body.company_name),
        company_name=body.company_name.strip(),
        company_type=body.company_type or "company",
        license_no=body.license_no or "",
        region=body.region or "",
        contact_name=body.contact_name or "",
        contact_phone=body.phone.strip(),
        status="pending",
    )
    db.add(merchant)
    db.commit()
    db.refresh(merchant)
    return {"ok": True, "merchant_id": merchant.id, "slug": merchant.slug, "status": merchant.status, "user_id": int(user.id)}


@cms_router.get("/api/shop-cms/merchants/me", summary="商家资料")
def merchant_me(current_user: User = Depends(get_current_user), db: Session = Depends(get_db)) -> Dict[str, Any]:
    merchant = _merchant_of(db, current_user)
    return {
        "ok": True,
        "credits": credits_json_float(ssb.balance_of(current_user)),
        "merchant": {
            "id": merchant.id,
            "slug": merchant.slug,
            "company_name": merchant.company_name,
            "company_type": merchant.company_type,
            "license_no": merchant.license_no,
            "region": merchant.region,
            "contact_name": merchant.contact_name,
            "contact_phone": merchant.contact_phone,
            "intro": merchant.intro,
            "logo_url": merchant.logo_url,
            "banner_url": merchant.banner_url,
            "commission_default_bp": merchant.commission_default_bp,
            "platform_fee_bp": merchant.platform_fee_bp,
            "commission_cap_cents": merchant.commission_cap_cents,
            "commission_min_cents": merchant.commission_min_cents,
            "attribution_days": merchant.attribution_days,
            "settle_type": merchant.settle_type,
            "settle_account": merchant.settle_account,
            "settle_holder": merchant.settle_holder,
            "status": merchant.status,
            "theme": merchant.theme or {},
        },
    }


@cms_router.put("/api/shop-cms/merchants/me", summary="更新商家资料/分佣默认值")
def merchant_update(body: MerchantUpdateReq, current_user: User = Depends(get_current_user), db: Session = Depends(get_db)) -> Dict[str, Any]:
    merchant = _merchant_of(db, current_user)
    for field, value in body.model_dump(exclude_unset=True).items():
        if value is not None and hasattr(merchant, field):
            setattr(merchant, field, value)
    db.commit()
    return {"ok": True}


@cms_router.get("/api/shop-cms/products", summary="商品列表")
def cms_products(
    status: str = "",
    keyword: str = "",
    page: int = 1,
    size: int = 20,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> Dict[str, Any]:
    merchant = _merchant_of(db, current_user)
    query = db.query(ShopProduct).filter(ShopProduct.merchant_id == merchant.id)
    if status:
        query = query.filter(ShopProduct.status == status)
    text = str(keyword or "").strip()
    if text:
        like = f"%{text}%"
        query = query.filter(
            ShopProduct.title.ilike(like) | ShopProduct.spu.ilike(like) | ShopProduct.keywords.ilike(like)
        )
    total = query.count()
    rows = query.order_by(ShopProduct.id.desc()).offset(max(0, (page - 1) * size)).limit(min(size, 100)).all()
    return {"ok": True, "total": total, "items": [_product_payload(row, merchant) for row in rows]}


@cms_router.post("/api/shop-cms/products", summary="新建商品")
def cms_product_create(body: ProductReq, current_user: User = Depends(get_current_user), db: Session = Depends(get_db)) -> Dict[str, Any]:
    merchant = _merchant_of(db, current_user)
    product = ShopProduct(
        merchant_id=merchant.id,
        spu=f"P{secrets.token_hex(4).upper()}",
        **body.model_dump(exclude={"status", "gallery", "detail_images", "detail_text"}),
    )
    if body.status in {"draft", "reviewing", ON_SALE, "off_shelf"}:
        product.status = body.status
    _apply_product_media(product, body)
    db.add(product)
    db.commit()
    db.refresh(product)
    return {"ok": True, "product": _product_payload(product, merchant)}


@cms_router.put("/api/shop-cms/products/{product_id}", summary="编辑商品")
def cms_product_update(
    product_id: int,
    body: ProductReq,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> Dict[str, Any]:
    merchant = _merchant_of(db, current_user)
    product = db.query(ShopProduct).filter(ShopProduct.id == product_id, ShopProduct.merchant_id == merchant.id).first()
    if product is None:
        raise HTTPException(status_code=404, detail="商品不存在")
    for field, value in body.model_dump(exclude_unset=True, exclude={"gallery", "detail_images", "detail_text"}).items():
        if value is not None and hasattr(product, field):
            setattr(product, field, value)
    _apply_product_media(product, body)
    db.commit()
    return {"ok": True, "product": _product_payload(product, merchant)}


@cms_router.post("/api/shop-cms/products/{product_id}/publish", summary="上架/下架")
def cms_product_publish(
    product_id: int,
    on: bool = True,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> Dict[str, Any]:
    merchant = _merchant_of(db, current_user)
    product = db.query(ShopProduct).filter(ShopProduct.id == product_id, ShopProduct.merchant_id == merchant.id).first()
    if product is None:
        raise HTTPException(status_code=404, detail="商品不存在")
    if on and int(product.price_cents or 0) <= 0:
        raise HTTPException(status_code=400, detail="商品价格未设置，不能上架")
    product.status = ON_SALE if on else "off_shelf"
    product.published_at = datetime.utcnow() if on else product.published_at
    db.commit()
    return {"ok": True, "status": product.status}


@cms_router.post("/api/shop-cms/uploads", summary="商品图片上传（商家后台）")
async def cms_upload_image(
    file: UploadFile = File(...),
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> Dict[str, Any]:
    """后台上传控件直接传图，返回可用图片 URL；商家不需要自己填链接。"""
    merchant = _merchant_of(db, current_user)
    raw = await file.read()
    if not raw:
        raise HTTPException(status_code=400, detail="文件为空，请重新选择图片")
    if len(raw) > SHOP_IMAGE_MAX_BYTES:
        raise HTTPException(status_code=400, detail="图片不能超过 8MB，请压缩后再传")
    name = str(file.filename or "")
    ext = name.rsplit(".", 1)[-1].lower() if "." in name else ""
    if ext not in SHOP_IMAGE_EXTENSIONS:
        raise HTTPException(status_code=400, detail="只支持 jpg / png / webp / gif 图片")
    folder = SHOP_UPLOADS_DIR / f"m{merchant.id}"
    folder.mkdir(parents=True, exist_ok=True)
    filename = f"{int(time.time())}_{secrets.token_hex(6)}.{ext}"
    (folder / filename).write_bytes(raw)
    return {"ok": True, "url": f"{SHOP_UPLOADS_URL}/m{merchant.id}/{filename}", "bytes": len(raw)}


@cms_router.get("/api/shop-cms/orders", summary="订单列表")
def cms_orders(
    status: str = "",
    page: int = 1,
    size: int = 20,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> Dict[str, Any]:
    merchant = _merchant_of(db, current_user)
    query = db.query(ShopOrder).filter(ShopOrder.merchant_id == merchant.id)
    if status:
        query = query.filter(ShopOrder.status == status)
    total = query.count()
    rows = query.order_by(ShopOrder.id.desc()).offset(max(0, (page - 1) * size)).limit(min(size, 100)).all()
    return {
        "ok": True,
        "total": total,
        "items": [
            {
                "order_no": row.order_no,
                "status": row.status,
                "pay_amount_cents": int(row.pay_amount_cents or 0),
                "commission_cents": int(row.commission_cents or 0),
                "commission_status": row.commission_status,
                "promoter_user_id": row.promoter_user_id,
                "created_at": row.created_at.isoformat() if row.created_at else None,
            }
            for row in rows
        ],
    }


@cms_router.get("/api/shop-cms/commissions/summary", summary="佣金概览")
def cms_commission_summary(current_user: User = Depends(get_current_user), db: Session = Depends(get_db)) -> Dict[str, Any]:
    merchant = _merchant_of(db, current_user)
    rows = (
        db.query(ShopCommission.status, func.coalesce(func.sum(ShopCommission.amount_cents), 0))
        .filter(ShopCommission.merchant_id == merchant.id)
        .group_by(ShopCommission.status)
        .all()
    )
    return {"ok": True, "by_status": {str(status): int(amount) for status, amount in rows}}


# ────────────────────────── 独立站 /api/shop ──────────────────────────

@router.get("/api/shop/store/{slug}", summary="店铺首页数据")
def store_home(slug: str, db: Session = Depends(get_db)) -> Dict[str, Any]:
    merchant = db.query(ShopMerchant).filter(ShopMerchant.slug == slug).first()
    if merchant is None:
        raise HTTPException(status_code=404, detail="店铺不存在")
    products = (
        db.query(ShopProduct)
        .filter(ShopProduct.merchant_id == merchant.id, ShopProduct.status == ON_SALE)
        .order_by(ShopProduct.heat.desc(), ShopProduct.id.desc())
        .limit(60)
        .all()
    )
    return {
        "ok": True,
        "theme": resolve_theme(merchant.theme, category=(products[0].category if products else "")),
        "theme_css": theme_css_vars(resolve_theme(merchant.theme)),
        "store": {
            "slug": merchant.slug,
            "company_name": merchant.company_name,
            "intro": merchant.intro,
            "logo_url": merchant.logo_url,
            "banner_url": merchant.banner_url,
            "theme": merchant.theme or {},
        },
        "items": [_product_payload(row, merchant) for row in products],
    }


@router.get("/api/shop/products/{product_id}", summary="商品详情")
def product_detail(product_id: int, db: Session = Depends(get_db)) -> Dict[str, Any]:
    product = db.query(ShopProduct).filter(ShopProduct.id == product_id).first()
    if product is None or product.status not in {ON_SALE, "off_shelf"}:
        raise HTTPException(status_code=404, detail="商品不存在或未上架")
    merchant = db.query(ShopMerchant).filter(ShopMerchant.id == product.merchant_id).first()
    data = _product_payload(product, merchant, promoter_view=True)
    data["detail_html"] = product.detail_html or ""
    data["specs"] = product.specs or {}
    data["buy_url"] = f"{SHOP_BASE_URL}/p/{product.id}"
    return {"ok": True, "product": data}


@router.get("/api/shop/plaza", summary="Online 选品广场")
def plaza(
    category: str = "",
    keyword: str = "",
    sort: str = "heat",
    page: int = 1,
    size: int = 24,
    db: Session = Depends(get_db),
) -> Dict[str, Any]:
    query = (
        db.query(ShopProduct, ShopMerchant)
        .join(ShopMerchant, ShopMerchant.id == ShopProduct.merchant_id)
        .filter(ShopProduct.status == ON_SALE, ShopMerchant.status == "active")
    )
    if category:
        query = query.filter(ShopProduct.category == category)
    if keyword:
        like = f"%{keyword}%"
        query = query.filter(ShopProduct.title.ilike(like) | ShopProduct.keywords.ilike(like))
    order = ShopProduct.heat.desc() if sort == "heat" else ShopProduct.id.desc()
    total = query.count()
    rows = query.order_by(order).offset(max(0, (page - 1) * size)).limit(min(size, 100)).all()
    return {"ok": True, "total": total, "items": [_product_payload(product, merchant) for product, merchant in rows]}


class PlazaLinkReq(BaseModel):
    product_id: int
    source: str = "online_plaza"


@router.post("/api/shop/plaza/link", summary="一键复制我的推广链接")
def plaza_link(body: PlazaLinkReq, current_user: User = Depends(get_current_user), db: Session = Depends(get_db)) -> Dict[str, Any]:
    product = db.query(ShopProduct).filter(ShopProduct.id == body.product_id, ShopProduct.status == ON_SALE).first()
    if product is None:
        raise HTTPException(status_code=404, detail="商品不存在或未上架")
    referral = (
        db.query(ShopReferral)
        .filter(ShopReferral.product_id == product.id, ShopReferral.promoter_user_id == int(current_user.id))
        .first()
    )
    if referral is None:
        referral = ShopReferral(
            code=secrets.token_urlsafe(6).replace("-", "").replace("_", "")[:10],
            merchant_id=product.merchant_id,
            product_id=product.id,
            promoter_user_id=int(current_user.id),
            source=str(body.source or "online_plaza")[:24],
        )
        db.add(referral)
        db.commit()
        db.refresh(referral)
    merchant = db.query(ShopMerchant).filter(ShopMerchant.id == product.merchant_id).first()
    plan = sc.compute_commission(
        pay_amount_cents=int(product.price_cents or 0),
        product_bp=product.commission_bp,
        merchant_default_bp=merchant.commission_default_bp if merchant else sc.COMMISSION_DEFAULT_BP,
        commission_type=product.commission_type,
        commission_fixed_cents=product.commission_fixed_cents,
        cap_cents=merchant.commission_cap_cents if merchant else 0,
        min_cents=merchant.commission_min_cents if merchant else 0,
    )
    return {
        "ok": True,
        "code": referral.code,
        "link": sc.referral_link(SHOP_BASE_URL, product.id, int(current_user.id), referral.code),
        "commission_bp": plan.rate_bp,
        "commission_estimate_cents": plan.amount_cents,
        "title": product.title,
        "cover_url": product.cover_url,
        "price_cents": int(product.price_cents or 0),
    }


class ReferralClickReq(BaseModel):
    product_id: int
    promoter_user_id: int = 0
    code: str = ""
    visitor_id: str = ""
    landing_url: str = ""


@router.post("/api/shop/referral/click", summary="归因点击记录")
def referral_click(body: ReferralClickReq, request: Request, db: Session = Depends(get_db)) -> Dict[str, Any]:
    promoter_id = int(body.promoter_user_id or 0)
    referral = db.query(ShopReferral).filter(ShopReferral.code == body.code).first() if body.code else None
    if referral is not None:
        promoter_id = int(referral.promoter_user_id)
        referral.click_count = int(referral.click_count or 0) + 1
    click = ShopReferralClick(
        referral_code=str(body.code or ""),
        product_id=int(body.product_id or 0),
        promoter_user_id=promoter_id,
        visitor_id=str(body.visitor_id or "")[:64],
        ip=(request.client.host if request.client else "")[:64],
        ua=str(request.headers.get("user-agent") or "")[:300],
        landing_url=str(body.landing_url or "")[:512],
    )
    db.add(click)
    db.commit()
    attribution_days = sc.ATTRIBUTION_DAYS_DEFAULT
    merchant = db.query(ShopMerchant).filter(ShopMerchant.id == referral.merchant_id).first() if referral else None
    if merchant is not None and merchant.attribution_days:
        attribution_days = int(merchant.attribution_days)
    return {"ok": True, "promoter_user_id": promoter_id, "attribution_days": attribution_days}


class OrderCreateReq(BaseModel):
    product_id: int
    qty: int = Field(1, ge=1, le=99)
    buyer_name: str = ""
    buyer_phone: str = ""
    buyer_address: str = ""
    referral_code: str = ""
    promoter_user_id: int = 0
    shipping_cents: int = Field(0, ge=0)
    remark: str = ""


@router.post("/api/shop/orders", summary="下单（P0：货到付款/线下）")
def order_create(
    body: OrderCreateReq,
    current_user: Optional[User] = Depends(get_optional_user),
    db: Session = Depends(get_db),
) -> Dict[str, Any]:
    product = db.query(ShopProduct).filter(ShopProduct.id == body.product_id, ShopProduct.status == ON_SALE).first()
    if product is None:
        raise HTTPException(status_code=404, detail="商品不存在或未上架")
    if int(product.stock or 0) < int(body.qty):
        raise HTTPException(status_code=400, detail="库存不足")
    merchant = db.query(ShopMerchant).filter(ShopMerchant.id == product.merchant_id).first()
    goods = int(product.price_cents or 0) * int(body.qty)
    pay_amount = goods + int(body.shipping_cents or 0)

    promoter_id: Optional[int] = None
    referral_code = str(body.referral_code or "")
    if referral_code:
        referral = db.query(ShopReferral).filter(ShopReferral.code == referral_code).first()
        if referral is not None and int(referral.product_id) == int(product.id):
            promoter_id = int(referral.promoter_user_id)
    elif body.promoter_user_id:
        promoter_id = int(body.promoter_user_id)

    self_buy = bool(current_user is not None and promoter_id is not None and int(current_user.id) == int(promoter_id))
    allowed, reason = sc.should_attribute(self_buy=self_buy, within_window=True)
    plan = sc.compute_commission(
        pay_amount_cents=pay_amount,
        shipping_cents=int(body.shipping_cents or 0),
        product_bp=product.commission_bp,
        merchant_default_bp=merchant.commission_default_bp if merchant else sc.COMMISSION_DEFAULT_BP,
        commission_type=product.commission_type,
        commission_fixed_cents=product.commission_fixed_cents,
        qty=int(body.qty),
        cap_cents=merchant.commission_cap_cents if merchant else 0,
        min_cents=merchant.commission_min_cents if merchant else 0,
    )
    commission_cents = plan.amount_cents if (promoter_id is not None and allowed) else 0

    next_seq = int(db.query(func.coalesce(func.max(ShopOrder.id), 0)).scalar() or 0) + 1
    order = ShopOrder(
        order_no=sc.make_order_no(product.merchant_id, seq=next_seq),
        merchant_id=product.merchant_id,
        buyer_user_id=int(current_user.id) if current_user is not None else None,
        buyer_name=body.buyer_name[:64],
        buyer_phone=body.buyer_phone[:32],
        buyer_address=body.buyer_address[:300],
        goods_cents=goods,
        shipping_cents=int(body.shipping_cents or 0),
        pay_amount_cents=pay_amount,
        channel="offline",
        status="pending",
        promoter_user_id=promoter_id,
        referral_code=referral_code[:32],
        commission_cents=commission_cents,
        commission_status="none" if commission_cents <= 0 else "pending",
        remark=body.remark[:300],
    )
    db.add(order)
    db.flush()
    db.add(
        ShopOrderItem(
            order_id=int(order.id),
            product_id=product.id,
            title=product.title[:200],
            cover_url=product.cover_url or "",
            price_cents=int(product.price_cents or 0),
            qty=int(body.qty),
            subtotal_cents=goods,
        )
    )
    if commission_cents > 0 and promoter_id is not None:
        # 佣金明细落地：与订单上的 commission_cents 同源（plan.base/rate），状态与订单一致
        db.add(
            ShopCommission(
                order_id=int(order.id),
                promoter_user_id=int(promoter_id),
                merchant_id=int(product.merchant_id),
                product_id=int(product.id),
                base_cents=int(plan.base_cents or 0),
                rate_bp=int(plan.rate_bp or 0),
                amount_cents=int(commission_cents),
                status=str(order.commission_status or "pending"),
                reason="",
            )
        )
    db.commit()
    db.refresh(order)
    return {
        "ok": True,
        "order_no": order.order_no,
        "pay_amount_cents": pay_amount,
        "commission_cents": commission_cents,
        "commission_blocked_reason": "" if commission_cents > 0 or promoter_id is None else reason,
        "status": order.status,
    }

# ────────────────────────── 素材投稿（Online 用户 → 商家商品） ──────────────────────────

SUBMISSION_STATUSES = ("new", "used", "rejected")


def _masked_phone(value: str) -> str:
    text = str(value or "").strip()
    if len(text) >= 7 and text.isdigit():
        return text[:3] + "****" + text[-4:]
    if len(text) > 4:
        return text[:2] + "***" + text[-2:]
    return text


_VIDEO_SUFFIXES = (".mp4", ".mov", ".m4v", ".webm", ".mkv", ".avi", ".flv", ".ts")


def _submission_kind(row: Any, raw: Any = None) -> str:
    """投稿素材类型：先看 media_type，再用 URL 后缀兜底。

    历史数据里出现过"视频文件被标成 image"（客户端猜错），导致商家后台按图片去预览 → 裂图。
    """
    text = str(raw if raw is not None else getattr(row, "media_type", "") or "").lower()
    if "video" in text:
        return "video"
    url = str(getattr(row, "url", "") or "").lower().split("?")[0]
    if url.endswith(_VIDEO_SUFFIXES):
        return "video"
    if "image" in text:
        return "image"
    return "image"


def _submission_public_url(row: "ShopProductSubmission", request: Optional[Request]) -> str:
    """给商家/投稿人一个可访问地址：优先公开源地址，否则现签一个素材文件地址。"""
    url = str(row.url or "").strip()
    needs_fresh = (not url.startswith(("http://", "https://"))) or ("token=" in url) or ("42.194.209.150" in url)
    if needs_fresh and row.asset_id and request is not None:
        try:
            from .assets import build_asset_file_url

            fresh = build_asset_file_url(request, str(row.asset_id))
            if fresh:
                return fresh
        except Exception:
            pass
    return url


def _submission_payload(
    row: "ShopProductSubmission",
    *,
    request: Optional[Request] = None,
    product: Optional[ShopProduct] = None,
    merchant: Optional[ShopMerchant] = None,
    submitter: Optional[User] = None,
    expose_original: bool = True,
) -> Dict[str, Any]:
    url = _submission_public_url(row, request)
    accepted = bool(getattr(row, "accepted_at", None))
    price = int(row.price_credits or 0) or int(ssb.price_credits(row.media_type))
    preview_url = "" if expose_original else _submission_preview_url(row)
    data: Dict[str, Any] = {
        "id": int(row.id),
        "product_id": int(row.product_id),
        "merchant_id": int(row.merchant_id),
        "asset_id": row.asset_id or "",
        "media_type": row.media_type or "",
        "title": row.title or "",
        "url": url if (expose_original or accepted) else "",
        "thumb_url": (preview_url if not expose_original else (
            (row.thumb_url or url) if str(row.media_type or "") != "video" else (row.thumb_url or "")
        )),
        "preview_url": preview_url,
        "preview_kind": ("" if expose_original else _submission_kind(row)),
        "poster_url": ((preview_url + "&poster=1") if (not expose_original and _submission_kind(row) == "video") else ""),
        "accepted": accepted,
        "price_credits": price,
        "accepted_at": row.accepted_at.isoformat() if getattr(row, "accepted_at", None) else None,
        "note": row.note or "",
        "status": row.status or "new",
        "source": row.source or "",
        "used_mode": row.used_mode or "",
        "created_at": row.created_at.isoformat() if row.created_at else "",
        "used_at": row.used_at.isoformat() if row.used_at else None,
    }
    if product is not None:
        data["product"] = {
            "id": int(product.id),
            "title": product.title or "",
            "cover_url": product.cover_url or "",
            "price_cents": int(product.price_cents or 0),
            "status": product.status or "",
        }
    if merchant is not None:
        data["merchant"] = {
            "id": int(merchant.id),
            "slug": merchant.slug or "",
            "company_name": merchant.company_name or "",
            "logo_url": merchant.logo_url or "",
        }
    if submitter is not None:
        data["submitter"] = {
            "user_id": int(submitter.id),
            "name": str(getattr(submitter, "name", "") or getattr(submitter, "nickname", "") or ""),
            "phone": _masked_phone(str(getattr(submitter, "phone", "") or getattr(submitter, "username", "") or "")),
        }
    return data


def _product_materials(product: ShopProduct) -> List[Dict[str, Any]]:
    """商品上「商家自己上传的素材」：图集 + 详情图 + 商家采纳的投稿素材。"""
    media = product.media or {}
    out: List[Dict[str, Any]] = []

    def add(url: Any, title: str, kind: str) -> None:
        text = str(url or "").strip()
        if not text:
            return
        if any(row["url"] == text for row in out):
            return
        out.append({"url": text, "title": title, "kind": kind})

    add(product.cover_url, "商品主图", "cover")
    for index, url in enumerate(media.get("gallery") or []):
        add(url, "图集 %d" % (index + 1), "gallery")
    for index, url in enumerate(media.get("detail_images") or []):
        add(url, "详情图 %d" % (index + 1), "detail")
    for row in media.get("materials") or []:
        if isinstance(row, dict):
            add(row.get("url"), str(row.get("title") or "投稿素材"), "submission")
        else:
            add(row, "投稿素材", "submission")
    return out


class SubmissionItemReq(BaseModel):
    asset_id: str = Field("", max_length=64)
    url: str = Field("", max_length=1024)
    thumb_url: str = Field("", max_length=1024)
    media_type: str = Field("", max_length=24)
    title: str = Field("", max_length=200)


class SubmissionCreateReq(BaseModel):
    product_id: int
    note: str = Field("", max_length=300)
    source: str = Field("online_submit", max_length=24)
    items: List[SubmissionItemReq] = Field(default_factory=list)


def _submission_rows_query(db: Session, **filters: Any):
    return db.query(ShopProductSubmission).filter(**filters) if filters else db.query(ShopProductSubmission)


@router.post("/api/shop/submissions", summary="给商家商品投稿素材")
def create_submissions(
    body: SubmissionCreateReq,
    request: Request,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> Dict[str, Any]:
    product = (
        db.query(ShopProduct)
        .filter(ShopProduct.id == int(body.product_id), ShopProduct.status == ON_SALE)
        .first()
    )
    if product is None:
        raise HTTPException(status_code=404, detail="商品不存在或已下架")
    items: List[SubmissionItemReq] = []
    seen: set = set()
    for item in body.items:
        url = str(item.url or "").strip()
        if not url.startswith(("http://", "https://")):
            continue
        key = (url, str(item.asset_id or ""))
        if key in seen:
            continue
        seen.add(key)
        items.append(item)
    if not items:
        raise HTTPException(status_code=400, detail="请先选择要投递的素材")
    if len(items) > 20:
        raise HTTPException(status_code=400, detail="一次最多投递 20 个素材")

    created = 0
    rows: List[ShopProductSubmission] = []
    for item in items:
        url = str(item.url or "").strip()
        row = (
            db.query(ShopProductSubmission)
            .filter(
                ShopProductSubmission.product_id == int(product.id),
                ShopProductSubmission.submitter_user_id == int(current_user.id),
                ShopProductSubmission.url == url,
            )
            .first()
        )
        if row is None:
            row = ShopProductSubmission(
                product_id=int(product.id),
                merchant_id=int(product.merchant_id),
                submitter_user_id=int(current_user.id),
                source=str(body.source or "online_submit")[:24],
                asset_id=str(item.asset_id or "")[:64],
                media_type=str(item.media_type or "")[:24],
                title=str(item.title or "")[:200],
                url=url,
                thumb_url=str(item.thumb_url or "")[:1024],
                note=str(body.note or "")[:300],
                status="new",
            )
            db.add(row)
            created += 1
        else:
            row.asset_id = str(item.asset_id or row.asset_id or "")[:64]
            row.media_type = str(item.media_type or row.media_type or "")[:24]
            row.title = str(item.title or row.title or "")[:200]
            row.thumb_url = str(item.thumb_url or row.thumb_url or "")[:1024]
            if body.note:
                row.note = str(body.note)[:300]
            if str(row.status or "") == "rejected":
                row.status = "new"
        rows.append(row)
    db.commit()
    for row in rows:
        db.refresh(row)
    return {
        "ok": True,
        "created": created,
        "total": len(rows),
        "product": _submission_payload(rows[0], request=request, product=product)["product"],
        "items": [_submission_payload(row, request=request) for row in rows],
    }


@router.get("/api/shop/submissions/mine", summary="我的投稿列表")
def my_submissions(
    request: Request,
    page: int = 1,
    size: int = 20,
    product_id: int = 0,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> Dict[str, Any]:
    query = db.query(ShopProductSubmission).filter(
        ShopProductSubmission.submitter_user_id == int(current_user.id)
    )
    if int(product_id or 0) > 0:
        query = query.filter(ShopProductSubmission.product_id == int(product_id))
    total = query.count()
    rows = (
        query.order_by(ShopProductSubmission.created_at.desc(), ShopProductSubmission.id.desc())
        .offset(max(0, (max(1, int(page)) - 1) * int(size)))
        .limit(max(1, min(int(size), 100)))
        .all()
    )
    products: Dict[int, ShopProduct] = {}
    merchants: Dict[int, ShopMerchant] = {}
    if rows:
        product_ids = {int(row.product_id) for row in rows}
        for product in db.query(ShopProduct).filter(ShopProduct.id.in_(product_ids)).all():
            products[int(product.id)] = product
        merchant_ids = {int(row.merchant_id) for row in rows}
        for merchant in db.query(ShopMerchant).filter(ShopMerchant.id.in_(merchant_ids)).all():
            merchants[int(merchant.id)] = merchant
    return {
        "ok": True,
        "total": total,
        "page": max(1, int(page)),
        "size": max(1, min(int(size), 100)),
        "items": [
            _submission_payload(
                row,
                request=request,
                product=products.get(int(row.product_id)),
                merchant=merchants.get(int(row.merchant_id)),
            )
            for row in rows
        ],
    }


@router.get("/api/shop/submissions/product/{product_id}", summary="投稿商品详情（含商家素材与我的投稿）")
def submission_product_detail(
    product_id: int,
    request: Request,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> Dict[str, Any]:
    product = db.query(ShopProduct).filter(ShopProduct.id == int(product_id)).first()
    if product is None:
        raise HTTPException(status_code=404, detail="商品不存在")
    merchant = db.query(ShopMerchant).filter(ShopMerchant.id == product.merchant_id).first()
    mine = (
        db.query(ShopProductSubmission)
        .filter(
            ShopProductSubmission.product_id == int(product.id),
            ShopProductSubmission.submitter_user_id == int(current_user.id),
        )
        .order_by(ShopProductSubmission.created_at.desc(), ShopProductSubmission.id.desc())
        .all()
    )
    return {
        "ok": True,
        "product": _product_payload(product, merchant),
        "materials": _product_materials(product),
        "my_submissions": [_submission_payload(row, request=request) for row in mine],
    }


class SubmissionUseReq(BaseModel):
    mode: str = Field("attach", max_length=16)  # attach=放进商品素材 / download=只下载
    note: str = Field("", max_length=300)


@cms_router.get("/api/shop-cms/submissions", summary="商家：按商品/关键词查看投稿素材")
def cms_submissions(
    request: Request,
    product_id: int = 0,
    keyword: str = "",
    status: str = "",
    page: int = 1,
    size: int = 20,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> Dict[str, Any]:
    merchant = _merchant_of(db, current_user)
    query = db.query(ShopProductSubmission).filter(ShopProductSubmission.merchant_id == int(merchant.id))
    if int(product_id or 0) > 0:
        query = query.filter(ShopProductSubmission.product_id == int(product_id))
    if str(status or "") in SUBMISSION_STATUSES:
        query = query.filter(ShopProductSubmission.status == str(status))
    text = str(keyword or "").strip()
    if text:
        like = f"%{text}%"
        product_ids = [
            int(row_id)
            for (row_id,) in db.query(ShopProduct.id)
            .filter(
                ShopProduct.merchant_id == int(merchant.id),
                ShopProduct.title.ilike(like) | ShopProduct.spu.ilike(like) | ShopProduct.keywords.ilike(like),
            )
            .all()
        ]
        query = query.filter(
            ShopProductSubmission.product_id.in_(product_ids or [-1])
            | ShopProductSubmission.title.ilike(like)
        )
    total = query.count()
    rows = (
        query.order_by(ShopProductSubmission.created_at.desc(), ShopProductSubmission.id.desc())
        .offset(max(0, (max(1, int(page)) - 1) * int(size)))
        .limit(max(1, min(int(size), 100)))
        .all()
    )
    products: Dict[int, ShopProduct] = {}
    submitters: Dict[int, User] = {}
    if rows:
        for product in db.query(ShopProduct).filter(ShopProduct.id.in_({int(r.product_id) for r in rows})).all():
            products[int(product.id)] = product
        for user in db.query(User).filter(User.id.in_({int(r.submitter_user_id) for r in rows})).all():
            submitters[int(user.id)] = user
    return {
        "ok": True,
        "total": total,
        "page": max(1, int(page)),
        "size": max(1, min(int(size), 100)),
        "items": [
            _submission_payload(
                row,
                request=request,
                product=products.get(int(row.product_id)),
                submitter=submitters.get(int(row.submitter_user_id)),
                expose_original=False,
            )
            for row in rows
        ],
    }


_PREVIEW_TOKEN_TTL = 86400  # 预览图签名有效期（秒）


def _submission_preview_token(submission_id: int, merchant_id: int, expiry_ts: int) -> str:
    """预览图给 <img> 用，不能带 Authorization 头，所以走 URL 签名（和素材文件同一套 HMAC）。"""
    raw = "shop-submission:%d:%d:%d" % (int(submission_id), int(merchant_id), int(expiry_ts))
    return hmac.new(settings.secret_key.encode("utf-8"), raw.encode("utf-8"), hashlib.sha256).hexdigest()


def _submission_preview_url(row: "ShopProductSubmission", request: Optional[Request] = None) -> str:
    expiry = int(time.time()) + _PREVIEW_TOKEN_TTL
    token = _submission_preview_token(int(row.id), int(row.merchant_id), expiry)
    return "/api/shop-cms/submissions/%d/preview?token=%s&expiry=%d" % (int(row.id), token, expiry)



def _submission_preview_target(row: "ShopProductSubmission") -> Path:
    folder = Path(tempfile.gettempdir()) / "shop_submission_previews"
    folder.mkdir(parents=True, exist_ok=True)
    stamp = (row.updated_at or row.created_at or datetime.utcnow()).strftime("%Y%m%d%H%M%S")
    return folder / ("submission_%d_%s.jpg" % (int(row.id), stamp))


_WATERMARK_FONTS = (
    "/usr/share/fonts/opentype/noto/NotoSansCJK-Regular.ttc",
    "/usr/share/fonts/truetype/noto/NotoSansCJK-Regular.ttc",
    "/usr/share/fonts/truetype/wqy/wqy-zenhei.ttc",
    "/usr/share/fonts/truetype/wqy/wqy-microhei.ttc",
    "/usr/share/fonts/truetype/arphic/uming.ttc",
)


def _watermark_font(size: int):
    """优先中文字体；服务器没装中文字体就退回默认字体，此时水印用英文（避免方块乱码）。"""
    from PIL import ImageFont  # noqa: PLC0415

    for path in _WATERMARK_FONTS:
        if os.path.exists(path):
            try:
                return ImageFont.truetype(path, size), True
            except Exception:  # noqa: BLE001
                continue
    try:
        return ImageFont.load_default(size=size), False
    except Exception:  # noqa: BLE001
        return ImageFont.load_default(), False


def _watermarked_preview(img: Any) -> Any:
    from PIL import Image, ImageDraw  # noqa: PLC0415

    img = img.convert("RGB")
    if img.width > 720:
        img = img.resize((720, max(1, int(img.height * 720 / img.width))))
    overlay = Image.new("RGBA", img.size, (255, 255, 255, 0))
    draw = ImageDraw.Draw(overlay)
    font, has_cjk = _watermark_font(max(13, img.width // 26))
    text = "投稿预览 · 未采纳" if has_cjk else "PREVIEW - shop.bhzn.top"
    step_x = max(150, img.width // 2)
    step_y = max(70, img.height // 4)
    for y in range(0, img.height + step_y, step_y):
        for x in range(-step_x // 2, img.width + step_x, step_x):
            draw.text((x, y), text, font=font, fill=(255, 255, 255, 130))
    return Image.alpha_composite(img.convert("RGBA"), overlay).convert("RGB")

def _build_submission_preview(row: "ShopProductSubmission") -> Path:
    """生成带水印的预览图（图片缩放；视频取首帧），只给商家「看」，不给原文件。"""
    from io import BytesIO

    from PIL import Image  # noqa: PLC0415

    src = str(row.url or "").strip()
    if not src.startswith(("http://", "https://")):
        raise HTTPException(status_code=400, detail="这条投稿没有可预览的地址")
    target = _submission_preview_target(row)
    if target.exists() and target.stat().st_size > 0:
        return target
    media = str(row.media_type or "").lower()
    if "video" in media:
        tmp = tempfile.NamedTemporaryFile(suffix=".jpg", delete=False)
        tmp.close()
        try:
            from .assets import _find_asset_ffmpeg  # noqa: PLC0415

            ffmpeg = _find_asset_ffmpeg()
            subprocess.run(
                # 取第 1 秒处的帧：很多视频开头是黑帧，直接取首帧会得到全黑封面
                [ffmpeg, "-y", "-loglevel", "error", "-ss", "1", "-i", src, "-frames:v", "1", "-vf", "scale=720:-2", tmp.name],
                timeout=90,
                check=True,
            )
            img = Image.open(tmp.name)
            img.load()
        except Exception as exc:  # noqa: BLE001
            raise HTTPException(status_code=422, detail="视频预览生成失败，请稍后重试") from exc
        finally:
            try:
                os.unlink(tmp.name)
            except Exception:  # noqa: BLE001
                pass
    else:
        try:
            req = urllib.request.Request(src, headers={"User-Agent": "lobster-preview/1.0"})
            with urllib.request.urlopen(req, timeout=60) as resp:  # noqa: S310
                data = resp.read(30 * 1024 * 1024)
            img = Image.open(BytesIO(data))
            img.load()
        except Exception as exc:  # noqa: BLE001
            raise HTTPException(status_code=422, detail="素材预览生成失败，请稍后重试") from exc
    out = _watermarked_preview(img)
    out.save(target, "JPEG", quality=82)
    return target


_VIDEO_PREVIEW_SECONDS = 20  # 水印预览只给前 20 秒
_VIDEO_PREVIEW_FONTS = (
    "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
    "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf",
)


def _build_submission_video_preview(row: "ShopProductSubmission") -> Path:
    """视频预览：低清 + 水印的可播放 mp4（采纳前商家只能看到这一段）。"""
    src = str(row.url or "").strip()
    if not src.startswith(("http://", "https://")):
        raise HTTPException(status_code=400, detail="这条投稿没有可预览的地址")
    target = _submission_preview_target(row).with_suffix(".mp4")
    if target.exists() and target.stat().st_size > 0:
        return target
    from .assets import _find_asset_ffmpeg  # noqa: PLC0415

    vf = "scale='min(720,iw)':-2,drawtext=text='PREVIEW - shop.bhzn.top':fontcolor=white@0.38:fontsize=26:x=(w-text_w)/2:y=(h-text_h)/2"
    for font in _VIDEO_PREVIEW_FONTS:
        if os.path.exists(font):
            vf += ":fontfile=" + font
            break
    cmd = [
        _find_asset_ffmpeg(), "-y", "-loglevel", "error", "-i", src,
        "-t", str(_VIDEO_PREVIEW_SECONDS), "-vf", vf,
        "-c:v", "libx264", "-preset", "veryfast", "-crf", "30", "-an",
        "-movflags", "+faststart", str(target),
    ]
    try:
        subprocess.run(cmd, timeout=240, check=True)
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(status_code=422, detail="视频预览生成失败，请稍后重试") from exc
    return target


def _merchant_submission(db: Session, merchant: "ShopMerchant", submission_id: int) -> "ShopProductSubmission":
    row = (
        db.query(ShopProductSubmission)
        .filter(
            ShopProductSubmission.id == int(submission_id),
            ShopProductSubmission.merchant_id == int(merchant.id),
        )
        .first()
    )
    if row is None:
        raise HTTPException(status_code=404, detail="投稿记录不存在")
    return row


def _attach_submission_material(db: Session, merchant: "ShopMerchant", row: "ShopProductSubmission", request: Request) -> "ShopProduct":
    product = (
        db.query(ShopProduct)
        .filter(ShopProduct.id == int(row.product_id), ShopProduct.merchant_id == int(merchant.id))
        .first()
    )
    if product is None:
        raise HTTPException(status_code=404, detail="商品不存在")
    url = _submission_public_url(row, request)
    media = dict(product.media or {})
    materials = media.get("materials")
    materials = list(materials) if isinstance(materials, list) else []
    if not any(isinstance(x, dict) and str(x.get("url") or "") == url for x in materials):
        materials.insert(0, {
            "url": url,
            "title": str(row.title or row.media_type or "投稿素材")[:200],
            "media_type": row.media_type or "",
            "submission_id": int(row.id),
            "submitter_user_id": int(row.submitter_user_id),
            "added_at": datetime.utcnow().isoformat(),
        })
    media["materials"] = materials[:200]
    product.media = media
    flag_modified(product, "media")
    return product


def _accept_submission(db: Session, merchant: "ShopMerchant", row: "ShopProductSubmission", request: Request,
                       current_user: User) -> Dict[str, Any]:
    """采纳：商家扣积分、投稿人加积分、素材进商品素材；同一条只扣一次。"""
    product = _attach_submission_material(db, merchant, row, request)
    already = bool(getattr(row, "accepted_at", None))
    charged = Decimal("0")
    # 扣费必须落在当前请求的 session 上（跨 session 的 User 对象不会被提交）
    payer = db.query(User).filter(User.id == int(getattr(current_user, "id", 0) or 0)).first() or current_user
    if not already:
        price = ssb.price_credits(row.media_type)
        ssb.ensure_balance(payer, price)
        charged = ssb.charge(
            db, payer, price,
            ref_id=str(int(row.id)),
            description="采纳投稿素材 #%d（%s）" % (int(row.id), product.title or ""),
        )
        submitter = db.query(User).filter(User.id == int(row.submitter_user_id)).first()
        if submitter is not None:
            ssb.reward(
                db, submitter, price,
                ref_id=str(int(row.id)),
                description="投稿素材被采纳 #%d（%s）" % (int(row.id), product.title or ""),
            )
        row.price_credits = int(price)
        row.accepted_at = datetime.utcnow()
        row.accepted_by_user_id = int(getattr(current_user, "id", 0) or 0)
    row.status = "used"
    row.used_mode = "accept"
    row.used_at = datetime.utcnow()
    db.commit()
    db.refresh(row)
    db.refresh(product)
    return {
        "ok": True,
        "already_accepted": already,
        "charged_credits": credits_json_float(charged),
        "price_credits": int(row.price_credits or 0),
        "credits_balance": credits_json_float(ssb.balance_of(payer)),
        "item": _submission_payload(row, request=request, expose_original=True),
        "product": _product_payload(product, merchant),
    }


class SubmissionAcceptReq(BaseModel):
    note: str = Field("", max_length=300)


@cms_router.post("/api/shop-cms/submissions/{submission_id}/accept", summary="采纳投稿素材（扣商家积分、给投稿人加积分）")
def cms_accept_submission(
    submission_id: int,
    request: Request,
    body: Optional[SubmissionAcceptReq] = None,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> Dict[str, Any]:
    merchant = _merchant_of(db, current_user)
    row = _merchant_submission(db, merchant, submission_id)
    return _accept_submission(db, merchant, row, request, current_user)


@cms_router.post("/api/shop-cms/submissions/{submission_id}/use", summary="采纳（旧入口，等价 accept）")
def cms_use_submission(
    submission_id: int,
    body: SubmissionUseReq,
    request: Request,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> Dict[str, Any]:
    merchant = _merchant_of(db, current_user)
    row = _merchant_submission(db, merchant, submission_id)
    if str(body.mode or "attach") == "download":
        if not bool(getattr(row, "accepted_at", None)):
            raise HTTPException(status_code=403, detail="采纳后才能下载原素材")
        row.used_at = datetime.utcnow()
        row.used_mode = "download"
        db.commit()
        db.refresh(row)
        return {"ok": True, "mode": "download", "url": _submission_public_url(row, request),
                "item": _submission_payload(row, request=request, expose_original=True)}
    result = _accept_submission(db, merchant, row, request, current_user)
    result["mode"] = "attach"
    return result


@cms_router.get("/api/shop-cms/submissions/{submission_id}/download", summary="下载投稿原素材（采纳后）")
def cms_download_submission(
    submission_id: int,
    request: Request,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> Dict[str, Any]:
    merchant = _merchant_of(db, current_user)
    row = _merchant_submission(db, merchant, submission_id)
    if not bool(getattr(row, "accepted_at", None)):
        raise HTTPException(status_code=403, detail="采纳后才能下载原素材")
    url = _submission_public_url(row, request)
    if not url:
        raise HTTPException(status_code=404, detail="素材地址不可用")
    return {"ok": True, "url": url, "title": row.title or "", "media_type": row.media_type or ""}


@cms_router.get("/api/shop-cms/submissions/{submission_id}/preview", summary="预览投稿素材（带水印，不提供原文件）")
def cms_submission_preview(
    submission_id: int,
    request: Request,
    token: str = "",
    expiry: int = 0,
    current_user: Optional[User] = Depends(get_optional_user),
    db: Session = Depends(get_db),
) -> Response:
    row = db.query(ShopProductSubmission).filter(ShopProductSubmission.id == int(submission_id)).first()
    if row is None:
        raise HTTPException(status_code=404, detail="投稿记录不存在")
    if token and expiry:
        expect = _submission_preview_token(int(row.id), int(row.merchant_id), int(expiry))
        if int(expiry) < int(time.time()) or not hmac.compare_digest(token, expect):
            raise HTTPException(status_code=401, detail="预览链接无效或已过期")
    else:
        if current_user is None:
            raise HTTPException(status_code=401, detail="未授权")
        merchant = _merchant_of(db, current_user)
        if merchant is None or int(row.merchant_id) != int(merchant.id):
            raise HTTPException(status_code=404, detail="投稿记录不存在")
    wants_poster = str(request.query_params.get("poster") or "") in ("1", "true")
    is_video = _submission_kind(row) == "video"
    if is_video and not wants_poster:
        vpath = _build_submission_video_preview(row)
        # FileResponse 支持 Range 请求（拖动进度条/边下边播），整块 read_bytes 会让播放卡顿
        return FileResponse(str(vpath), media_type="video/mp4",
                            headers={"Cache-Control": "public, max-age=60"})
    path = _build_submission_preview(row)
    return FileResponse(str(path), media_type="image/jpeg",
                        headers={"Cache-Control": "public, max-age=60"})


@router.get("/api/shop/submissions/pricing", summary="投稿被采纳能拿多少积分")
def submissions_pricing() -> Dict[str, Any]:
    return {"ok": True, "credits": ssb.pricing_table()}


@cms_router.post("/api/shop-cms/submissions/{submission_id}/reject", summary="商家：忽略投稿素材")
def cms_reject_submission(
    submission_id: int,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> Dict[str, Any]:
    merchant = _merchant_of(db, current_user)
    row = (
        db.query(ShopProductSubmission)
        .filter(
            ShopProductSubmission.id == int(submission_id),
            ShopProductSubmission.merchant_id == int(merchant.id),
        )
        .first()
    )
    if row is None:
        raise HTTPException(status_code=404, detail="投稿记录不存在")
    row.status = "rejected"
    row.used_mode = ""
    row.used_at = None
    db.commit()
    db.refresh(row)
    return {"ok": True, "item": _submission_payload(row)}

