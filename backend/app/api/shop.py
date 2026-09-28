"""shop / shopcms API（P0 骨架，2026-09-28）。

- /api/shop/*      独立站（公开）+ 选品广场 + 推广链接（登录用户）
- /api/shop-cms/*  商家后台（公司、商品、分佣配置；复用现有 users 认证）

设计见 start_entry_sandbox/shop_plan/DESIGN.md。
"""
from __future__ import annotations

import secrets
from datetime import datetime
from typing import Any, Dict, List, Optional

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, Field
from sqlalchemy import func
from sqlalchemy.orm import Session

from ..db import get_db
from ..models import User
from ..services import shop_commission as sc
from ..services.shop_theme import resolve_theme, theme_css_vars
from ..shop_models import (
    ShopCommission,
    ShopMerchant,
    ShopOrder,
    ShopOrderItem,
    ShopProduct,
    ShopReferral,
    ShopReferralClick,
)
from .auth import get_current_user, get_password_hash

router = APIRouter()          # 独立站 / 选品广场（公开 + 登录用户）
cms_router = APIRouter()      # 商家后台

SHOP_BASE_URL = "https://shop.bhzn.top"
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
    return merchant


def _product_payload(product: ShopProduct, merchant: Optional[ShopMerchant] = None, *, promoter_view: bool = False) -> Dict[str, Any]:
    data: Dict[str, Any] = {
        "id": product.id,
        "title": product.title,
        "subtitle": product.subtitle,
        "category": product.category,
        "brand": product.brand,
        "cover_url": product.cover_url,
        "media": product.media or {},
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
    email = f"{body.phone.strip()}@shop.lobster.local"
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
    page: int = 1,
    size: int = 20,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> Dict[str, Any]:
    merchant = _merchant_of(db, current_user)
    query = db.query(ShopProduct).filter(ShopProduct.merchant_id == merchant.id)
    if status:
        query = query.filter(ShopProduct.status == status)
    total = query.count()
    rows = query.order_by(ShopProduct.id.desc()).offset(max(0, (page - 1) * size)).limit(min(size, 100)).all()
    return {"ok": True, "total": total, "items": [_product_payload(row, merchant) for row in rows]}


@cms_router.post("/api/shop-cms/products", summary="新建商品")
def cms_product_create(body: ProductReq, current_user: User = Depends(get_current_user), db: Session = Depends(get_db)) -> Dict[str, Any]:
    merchant = _merchant_of(db, current_user)
    product = ShopProduct(merchant_id=merchant.id, spu=f"P{secrets.token_hex(4).upper()}", **body.model_dump(exclude={"status"}))
    if body.status in {"draft", "reviewing", ON_SALE, "off_shelf"}:
        product.status = body.status
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
    for field, value in body.model_dump(exclude_unset=True).items():
        if value is not None and hasattr(product, field):
            setattr(product, field, value)
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
    current_user: Optional[User] = Depends(get_current_user),
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

    order = ShopOrder(
        order_no=sc.make_order_no(product.merchant_id, seq=int(func.coalesce(func.max(ShopOrder.id), 0) or 0) + 1 if False else 0),
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
