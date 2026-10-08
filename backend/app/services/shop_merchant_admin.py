"""商家（shopcms）全平台数据：列表 / 明细 / 状态（manage 与 /admin 管理后台共用）。"""
from __future__ import annotations

from typing import Any, Dict, List, Optional

from sqlalchemy import case, func, or_
from sqlalchemy.orm import Session

from ..models import User
from ..shop_models import ShopCommission, ShopMerchant, ShopOrder, ShopProduct

SHOP_MERCHANT_STATUS_LABEL = {
    "draft": "草稿",
    "pending": "待审核",
    "active": "正常",
    "suspended": "已停用",
    "rejected": "已驳回",
}

SHOP_ORDER_STATUS_LABEL = {
    "pending": "待付款",
    "paid": "已付款",
    "shipped": "已发货",
    "completed": "已完成",
    "refunding": "退款中",
    "refunded": "已退款",
    "cancelled": "已取消",
}

SHOP_COMMISSION_STATUS_LABEL = {
    "none": "无",
    "pending": "待确认",
    "confirmed": "已确认",
    "settled": "已结算",
    "invalid": "已作废",
}


def merchant_products_summary(db: Session, ids: List[int]) -> Dict[int, Dict[str, int]]:
    out: Dict[int, Dict[str, int]] = {}
    if not ids:
        return out
    rows = (
        db.query(
            ShopProduct.merchant_id,
            func.count(ShopProduct.id),
            func.sum(case((ShopProduct.status == "on_sale", 1), else_=0)),
        )
        .filter(ShopProduct.merchant_id.in_(ids))
        .group_by(ShopProduct.merchant_id)
        .all()
    )
    for merchant_id, total, on_sale in rows:
        out[int(merchant_id)] = {"products_total": int(total or 0), "products_on_sale": int(on_sale or 0)}
    return out


def merchant_orders_summary(db: Session, ids: List[int]) -> Dict[int, Dict[str, int]]:
    out: Dict[int, Dict[str, int]] = {}
    if not ids:
        return out
    rows = (
        db.query(
            ShopOrder.merchant_id,
            func.count(ShopOrder.id),
            func.coalesce(func.sum(ShopOrder.pay_amount_cents), 0),
        )
        .filter(ShopOrder.merchant_id.in_(ids))
        .group_by(ShopOrder.merchant_id)
        .all()
    )
    for merchant_id, total, pay in rows:
        out[int(merchant_id)] = {"orders_total": int(total or 0), "pay_amount_cents": int(pay or 0)}
    return out


def merchant_commission_summary(db: Session, ids: List[int]) -> Dict[int, Dict[str, int]]:
    out: Dict[int, Dict[str, int]] = {}
    if not ids:
        return out
    rows = (
        db.query(
            ShopCommission.merchant_id,
            ShopCommission.status,
            func.coalesce(func.sum(ShopCommission.amount_cents), 0),
        )
        .filter(ShopCommission.merchant_id.in_(ids))
        .group_by(ShopCommission.merchant_id, ShopCommission.status)
        .all()
    )
    for merchant_id, status, amount in rows:
        bucket = out.setdefault(
            int(merchant_id),
            {
                "commission_pending_cents": 0,
                "commission_confirmed_cents": 0,
                "commission_settled_cents": 0,
                "commission_total_cents": 0,
            },
        )
        cents = int(amount or 0)
        key = str(status or "").lower()
        if key == "pending":
            bucket["commission_pending_cents"] += cents
        elif key == "confirmed":
            bucket["commission_confirmed_cents"] += cents
        elif key == "settled":
            bucket["commission_settled_cents"] += cents
        bucket["commission_total_cents"] += cents
    return out


def shop_merchant_json(
    merchant: ShopMerchant,
    *,
    email: str = "",
    products: Optional[Dict[str, int]] = None,
    orders: Optional[Dict[str, int]] = None,
    commissions: Optional[Dict[str, int]] = None,
) -> Dict[str, Any]:
    return {
        "id": int(merchant.id),
        "user_id": int(merchant.user_id or 0),
        "email": str(email or ""),
        "slug": merchant.slug,
        "company_name": merchant.company_name,
        "company_type": merchant.company_type,
        "license_no": merchant.license_no,
        "region": merchant.region,
        "contact_name": merchant.contact_name,
        "contact_phone": merchant.contact_phone,
        "intro": merchant.intro,
        "logo_url": merchant.logo_url,
        "status": merchant.status,
        "status_label": SHOP_MERCHANT_STATUS_LABEL.get(str(merchant.status or ""), str(merchant.status or "")),
        "platform_fee_bp": int(merchant.platform_fee_bp or 0),
        "attribution_days": int(merchant.attribution_days or 0),
        "created_at": merchant.created_at.isoformat() if merchant.created_at else "",
        "product_count": int((products or {}).get("products_total", 0)),
        "on_sale_count": int((products or {}).get("products_on_sale", 0)),
        "order_count": int((orders or {}).get("orders_total", 0)),
        "pay_amount_cents": int((orders or {}).get("pay_amount_cents", 0)),
        "commission_pending_cents": int((commissions or {}).get("commission_pending_cents", 0)),
        "commission_confirmed_cents": int((commissions or {}).get("commission_confirmed_cents", 0)),
        "commission_settled_cents": int((commissions or {}).get("commission_settled_cents", 0)),
        "commission_total_cents": int((commissions or {}).get("commission_total_cents", 0)),
    }


def _merchant_rows(db: Session, ids: List[int]) -> Dict[int, Dict[str, Any]]:
    products = merchant_products_summary(db, ids)
    orders = merchant_orders_summary(db, ids)
    commissions = merchant_commission_summary(db, ids)
    return {
        int(mid): {
            "products": products.get(int(mid)),
            "orders": orders.get(int(mid)),
            "commissions": commissions.get(int(mid)),
        }
        for mid in ids
    }


def list_shop_merchants(
    db: Session,
    *,
    q: str = "",
    status: str = "",
    page: int = 1,
    size: int = 20,
) -> Dict[str, Any]:
    page = max(1, int(page or 1))
    size = max(1, min(100, int(size or 20)))
    query = db.query(ShopMerchant)
    key = str(q or "").strip()
    if key:
        like = f"%{key}%"
        query = query.filter(
            or_(
                ShopMerchant.company_name.ilike(like),
                ShopMerchant.slug.ilike(like),
                ShopMerchant.contact_name.ilike(like),
                ShopMerchant.contact_phone.ilike(like),
                ShopMerchant.region.ilike(like),
            )
        )
    wanted = str(status or "").strip()
    if wanted:
        query = query.filter(ShopMerchant.status == wanted)
    total = int(query.count() or 0)
    rows = query.order_by(ShopMerchant.id.desc()).offset((page - 1) * size).limit(size).all()
    ids = [int(r.id) for r in rows]
    buckets = _merchant_rows(db, ids)
    user_ids = [int(r.user_id or 0) for r in rows if int(r.user_id or 0)]
    emails = dict(db.query(User.id, User.email).filter(User.id.in_(user_ids)).all()) if user_ids else {}
    items = [
        shop_merchant_json(
            r,
            email=str(emails.get(int(r.user_id or 0), "") or ""),
            products=(buckets.get(int(r.id)) or {}).get("products"),
            orders=(buckets.get(int(r.id)) or {}).get("orders"),
            commissions=(buckets.get(int(r.id)) or {}).get("commissions"),
        )
        for r in rows
    ]
    summary = {
        "merchants_total": total,
        "merchants_active": int(db.query(func.count(ShopMerchant.id)).filter(ShopMerchant.status == "active").scalar() or 0),
        "merchants_pending": int(db.query(func.count(ShopMerchant.id)).filter(ShopMerchant.status == "pending").scalar() or 0),
        "products_total": int(db.query(func.count(ShopProduct.id)).scalar() or 0),
        "orders_total": int(db.query(func.count(ShopOrder.id)).scalar() or 0),
        "pay_amount_cents": int(db.query(func.coalesce(func.sum(ShopOrder.pay_amount_cents), 0)).scalar() or 0),
        "commission_total_cents": int(db.query(func.coalesce(func.sum(ShopCommission.amount_cents), 0)).scalar() or 0),
        "commission_pending_cents": int(
            db.query(func.coalesce(func.sum(ShopCommission.amount_cents), 0))
            .filter(ShopCommission.status == "pending")
            .scalar()
            or 0
        ),
    }
    return {
        "ok": True,
        "total": total,
        "page": page,
        "size": size,
        "pages": max(1, (total + size - 1) // size) if total else 1,
        "items": items,
        "summary": summary,
        "statuses": SHOP_MERCHANT_STATUS_LABEL,
    }


def shop_merchant_detail(db: Session, merchant_id: int) -> Dict[str, Any]:
    merchant = db.query(ShopMerchant).filter(ShopMerchant.id == int(merchant_id)).first()
    if merchant is None:
        raise LookupError("merchant not found")
    email = ""
    if merchant.user_id:
        row = db.query(User.email).filter(User.id == int(merchant.user_id)).first()
        email = str(row[0]) if row else ""
    buckets = _merchant_rows(db, [int(merchant.id)])[int(merchant.id)]
    recent_products = (
        db.query(ShopProduct)
        .filter(ShopProduct.merchant_id == int(merchant.id))
        .order_by(ShopProduct.id.desc())
        .limit(10)
        .all()
    )
    recent_orders = (
        db.query(ShopOrder)
        .filter(ShopOrder.merchant_id == int(merchant.id))
        .order_by(ShopOrder.id.desc())
        .limit(10)
        .all()
    )
    return {
        "ok": True,
        "merchant": shop_merchant_json(
            merchant,
            email=email,
            products=buckets.get("products"),
            orders=buckets.get("orders"),
            commissions=buckets.get("commissions"),
        ),
        "products": [
            {
                "id": int(p.id),
                "title": p.title,
                "price_cents": int(p.price_cents or 0),
                "stock": int(p.stock or 0),
                "sales": int(p.sales or 0),
                "status": p.status,
                "commission_bp": int(p.commission_bp or 0),
                "cover_url": p.cover_url,
                "created_at": p.created_at.isoformat() if p.created_at else "",
            }
            for p in recent_products
        ],
        "orders": [
            {
                "id": int(o.id),
                "order_no": o.order_no,
                "status": o.status,
                "status_label": SHOP_ORDER_STATUS_LABEL.get(str(o.status or ""), str(o.status or "")),
                "pay_amount_cents": int(o.pay_amount_cents or 0),
                "commission_cents": int(o.commission_cents or 0),
                "commission_status": o.commission_status,
                "created_at": o.created_at.isoformat() if o.created_at else "",
            }
            for o in recent_orders
        ],
    }


def set_shop_merchant_status(db: Session, merchant_id: int, status: str) -> Dict[str, Any]:
    wanted = str(status or "").strip().lower()
    if wanted not in SHOP_MERCHANT_STATUS_LABEL:
        raise ValueError("状态只能是：" + " / ".join(SHOP_MERCHANT_STATUS_LABEL))
    merchant = db.query(ShopMerchant).filter(ShopMerchant.id == int(merchant_id)).first()
    if merchant is None:
        raise LookupError("merchant not found")
    merchant.status = wanted
    db.commit()
    db.refresh(merchant)
    return {"ok": True, "merchant": shop_merchant_json(merchant)}
