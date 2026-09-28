"""shop / shopcms 数据模型（2026-09-28，P0）。

设计见 start_entry_sandbox/shop_plan/DESIGN.md。
金额统一用「分」（Integer/BigInteger）；比例统一用「基点 bp」（300 = 3%）。
"""
from __future__ import annotations

from datetime import datetime
from typing import Optional

from sqlalchemy import BigInteger, Boolean, DateTime, ForeignKey, Index, Integer, JSON, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from .models import Base


def _now() -> datetime:
    return datetime.utcnow()


class ShopMerchant(Base):
    """商家（一个公司 = 一个店铺）。user_id 指向 users（role=merchant）。"""

    __tablename__ = "shop_merchants"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    user_id: Mapped[int] = mapped_column(Integer, ForeignKey("users.id"), unique=True, index=True)
    slug: Mapped[str] = mapped_column(String(64), unique=True, index=True)
    company_name: Mapped[str] = mapped_column(String(128), default="")
    company_type: Mapped[str] = mapped_column(String(32), default="company")  # company/individual
    license_no: Mapped[str] = mapped_column(String(64), default="")
    region: Mapped[str] = mapped_column(String(64), default="")
    contact_name: Mapped[str] = mapped_column(String(64), default="")
    contact_phone: Mapped[str] = mapped_column(String(32), default="")
    intro: Mapped[str] = mapped_column(Text, default="")
    logo_url: Mapped[str] = mapped_column(String(512), default="")
    banner_url: Mapped[str] = mapped_column(String(512), default="")
    # 分佣默认值（商品可覆盖）
    commission_default_bp: Mapped[int] = mapped_column(Integer, default=1000)  # 10%
    platform_fee_bp: Mapped[int] = mapped_column(Integer, default=300)  # 3%
    commission_cap_cents: Mapped[int] = mapped_column(BigInteger, default=0)  # 0=不限
    commission_min_cents: Mapped[int] = mapped_column(BigInteger, default=0)  # 0=不保底
    attribution_days: Mapped[int] = mapped_column(Integer, default=30)
    # 结算账户
    settle_type: Mapped[str] = mapped_column(String(16), default="")  # alipay/wechat/bank
    settle_account: Mapped[str] = mapped_column(String(128), default="")
    settle_holder: Mapped[str] = mapped_column(String(64), default="")
    status: Mapped[str] = mapped_column(String(16), default="draft", index=True)  # draft/pending/active/suspended
    theme: Mapped[Optional[dict]] = mapped_column(JSON, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=_now)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=_now, onupdate=_now)


class ShopProduct(Base):
    """商品（SPU 级；规格放 specs JSON，P0 不做 SKU 表）。"""

    __tablename__ = "shop_products"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    merchant_id: Mapped[int] = mapped_column(Integer, ForeignKey("shop_merchants.id"), index=True)
    spu: Mapped[str] = mapped_column(String(64), default="", index=True)
    title: Mapped[str] = mapped_column(String(200), default="")
    subtitle: Mapped[str] = mapped_column(String(300), default="")
    category: Mapped[str] = mapped_column(String(64), default="", index=True)
    keywords: Mapped[str] = mapped_column(String(300), default="")
    brand: Mapped[str] = mapped_column(String(64), default="")
    cover_url: Mapped[str] = mapped_column(String(512), default="")
    media: Mapped[Optional[dict]] = mapped_column(JSON, nullable=True)  # {gallery:[], video:"", detail:[]}
    detail_html: Mapped[str] = mapped_column(Text, default="")
    specs: Mapped[Optional[dict]] = mapped_column(JSON, nullable=True)
    price_cents: Mapped[int] = mapped_column(BigInteger, default=0)
    market_price_cents: Mapped[int] = mapped_column(BigInteger, default=0)
    stock: Mapped[int] = mapped_column(Integer, default=0)
    sales: Mapped[int] = mapped_column(Integer, default=0)
    # 分佣：commission_bp=0 表示用商家默认
    commission_bp: Mapped[int] = mapped_column(Integer, default=0)
    commission_type: Mapped[str] = mapped_column(String(16), default="percent")  # percent/fixed
    commission_fixed_cents: Mapped[int] = mapped_column(BigInteger, default=0)
    status: Mapped[str] = mapped_column(String(16), default="draft", index=True)  # draft/reviewing/on_sale/off_shelf
    heat: Mapped[int] = mapped_column(Integer, default=0)
    tags: Mapped[str] = mapped_column(String(300), default="")
    ai: Mapped[Optional[dict]] = mapped_column(JSON, nullable=True)
    published_at: Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=_now)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=_now, onupdate=_now)

    __table_args__ = (Index("ix_shop_products_merchant_status", "merchant_id", "status"),)


class ShopReferral(Base):
    """推广链接（商家商品 × 推广者，幂等）。"""

    __tablename__ = "shop_referrals"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    code: Mapped[str] = mapped_column(String(32), unique=True, index=True)
    merchant_id: Mapped[int] = mapped_column(Integer, ForeignKey("shop_merchants.id"), index=True)
    product_id: Mapped[int] = mapped_column(Integer, ForeignKey("shop_products.id"), index=True)
    promoter_user_id: Mapped[int] = mapped_column(Integer, ForeignKey("users.id"), index=True)
    source: Mapped[str] = mapped_column(String(24), default="online_plaza")
    click_count: Mapped[int] = mapped_column(Integer, default=0)
    order_count: Mapped[int] = mapped_column(Integer, default=0)
    gmv_cents: Mapped[int] = mapped_column(BigInteger, default=0)
    commission_cents: Mapped[int] = mapped_column(BigInteger, default=0)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=_now)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=_now, onupdate=_now)

    __table_args__ = (UniqueConstraint("product_id", "promoter_user_id", name="uq_shop_referral_product_promoter"),)


class ShopReferralClick(Base):
    """归因点击流水（用于后置归因与风控）。"""

    __tablename__ = "shop_referral_clicks"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    referral_code: Mapped[str] = mapped_column(String(32), default="", index=True)
    product_id: Mapped[int] = mapped_column(Integer, default=0, index=True)
    promoter_user_id: Mapped[int] = mapped_column(Integer, default=0, index=True)
    visitor_id: Mapped[str] = mapped_column(String(64), default="")
    ip: Mapped[str] = mapped_column(String(64), default="")
    ua: Mapped[str] = mapped_column(String(300), default="")
    landing_url: Mapped[str] = mapped_column(String(512), default="")
    created_at: Mapped[datetime] = mapped_column(DateTime, default=_now)


class ShopOrder(Base):
    """订单（P0：积分 / 货到付款；后续接微信、支付宝）。"""

    __tablename__ = "shop_orders"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    order_no: Mapped[str] = mapped_column(String(40), unique=True, index=True)
    merchant_id: Mapped[int] = mapped_column(Integer, ForeignKey("shop_merchants.id"), index=True)
    buyer_user_id: Mapped[Optional[int]] = mapped_column(Integer, ForeignKey("users.id"), nullable=True, index=True)
    buyer_name: Mapped[str] = mapped_column(String(64), default="")
    buyer_phone: Mapped[str] = mapped_column(String(32), default="")
    buyer_address: Mapped[str] = mapped_column(String(300), default="")
    goods_cents: Mapped[int] = mapped_column(BigInteger, default=0)
    shipping_cents: Mapped[int] = mapped_column(BigInteger, default=0)
    pay_amount_cents: Mapped[int] = mapped_column(BigInteger, default=0)
    channel: Mapped[str] = mapped_column(String(16), default="offline")
    status: Mapped[str] = mapped_column(String(16), default="pending", index=True)
    promoter_user_id: Mapped[Optional[int]] = mapped_column(Integer, nullable=True, index=True)
    referral_code: Mapped[str] = mapped_column(String(32), default="")
    commission_cents: Mapped[int] = mapped_column(BigInteger, default=0)
    commission_status: Mapped[str] = mapped_column(String(16), default="none", index=True)
    tracking_no: Mapped[str] = mapped_column(String(64), default="")
    remark: Mapped[str] = mapped_column(String(300), default="")
    created_at: Mapped[datetime] = mapped_column(DateTime, default=_now)
    paid_at: Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True)
    shipped_at: Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True)
    completed_at: Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True)


class ShopOrderItem(Base):
    __tablename__ = "shop_order_items"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    order_id: Mapped[int] = mapped_column(Integer, ForeignKey("shop_orders.id"), index=True)
    product_id: Mapped[int] = mapped_column(Integer, ForeignKey("shop_products.id"), index=True)
    title: Mapped[str] = mapped_column(String(200), default="")
    cover_url: Mapped[str] = mapped_column(String(512), default="")
    price_cents: Mapped[int] = mapped_column(BigInteger, default=0)
    qty: Mapped[int] = mapped_column(Integer, default=1)
    subtotal_cents: Mapped[int] = mapped_column(BigInteger, default=0)
    specs_text: Mapped[str] = mapped_column(String(200), default="")


class ShopCommission(Base):
    """佣金流水（一单一结）。"""

    __tablename__ = "shop_commissions"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    order_id: Mapped[int] = mapped_column(Integer, ForeignKey("shop_orders.id"), unique=True, index=True)
    promoter_user_id: Mapped[int] = mapped_column(Integer, ForeignKey("users.id"), index=True)
    merchant_id: Mapped[int] = mapped_column(Integer, index=True)
    product_id: Mapped[int] = mapped_column(Integer, index=True)
    base_cents: Mapped[int] = mapped_column(BigInteger, default=0)
    rate_bp: Mapped[int] = mapped_column(Integer, default=0)
    amount_cents: Mapped[int] = mapped_column(BigInteger, default=0)
    status: Mapped[str] = mapped_column(String(16), default="pending", index=True)
    reason: Mapped[str] = mapped_column(String(200), default="")
    settlement_id: Mapped[Optional[int]] = mapped_column(Integer, nullable=True, index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=_now)
    confirmed_at: Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True)
    settled_at: Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True)


class ShopSettlement(Base):
    """结算批（推广者 / 商家）。"""

    __tablename__ = "shop_settlements"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    subject_type: Mapped[str] = mapped_column(String(16), default="promoter")  # promoter/merchant
    subject_user_id: Mapped[int] = mapped_column(Integer, index=True)
    period_start: Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True)
    period_end: Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True)
    order_count: Mapped[int] = mapped_column(Integer, default=0)
    gmv_cents: Mapped[int] = mapped_column(BigInteger, default=0)
    commission_cents: Mapped[int] = mapped_column(BigInteger, default=0)
    platform_fee_cents: Mapped[int] = mapped_column(BigInteger, default=0)
    status: Mapped[str] = mapped_column(String(16), default="draft", index=True)  # draft/paid
    paid_at: Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True)
    operator: Mapped[str] = mapped_column(String(64), default="")
    remark: Mapped[str] = mapped_column(String(300), default="")
    created_at: Mapped[datetime] = mapped_column(DateTime, default=_now)


class ShopPayoutAccount(Base):
    __tablename__ = "shop_payout_accounts"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    owner_user_id: Mapped[int] = mapped_column(Integer, ForeignKey("users.id"), index=True)
    account_type: Mapped[str] = mapped_column(String(16), default="alipay")
    account: Mapped[str] = mapped_column(String(128), default="")
    holder: Mapped[str] = mapped_column(String(64), default="")
    is_default: Mapped[bool] = mapped_column(Boolean, default=False)
    verified: Mapped[bool] = mapped_column(Boolean, default=False)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=_now)
