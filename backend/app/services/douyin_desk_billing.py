"""抖音信息台计费：做同款（视频编辑，2026-11-25 起上游用 wan3.0-video）与榜单搜索。

上游价（2026-11-25 官方《模型调用计费》原文，华北2 北京）：
- 万相3.0-视频生成 计费规则：**输入视频和输出视频均按视频秒数计费**；
  请求失败不产生费用；**无免费额度**（所有地域）。
  计费公式：计费时长 = 输入视频时长 + 输出视频时长。
- wan3.0-video（我们用的标准版）：
    480P = 0.3 元/秒、720P = 0.6 元/秒、1080P = 1.2 元/秒（原价，限时 7 折 → 0.21/0.42/0.84）
- wan3.0-video-prime（优先版，能力对齐标准版，资源紧张时按标准速率计费）：
    480P = 0.45 元/秒、720P = 0.9 元/秒、1080P = 1.8 元/秒
  我们按**原价**计成本，这样限时折扣结束后不会亏。
- TikHub：我们每天采集的 10 个抖音榜单端点，每个 0.001 USD，合计 0.01 USD/天（≈0.075 元/天）；
  做同款取原视频 fetch_one_video_v2 = 0.001 USD/次；
  **2026-10-11 起：信息台搜索默认改成拿用户关键词实时问 TikHub**（`POST /api/v1/douyin/search/fetch_video_search_v2`，
  0.01 USD/次 ≈ 7.5 算力/次，按关键词个数计次）；`source=snapshot` 的本地快照搜索仍是零上游成本。

我方定价（都可用环境变量改）：
- 做同款：credits = ceil(输入+输出秒数) × 单价(0.6 元/秒) × 100 算力/元 × 倍率(1.5)
  → 15 秒片 ≈ 2700 算力（27 元），5 秒片 ≈ 900 算力（9 元）。失败全额退（上游也不计费）。
- 搜索：TikHub 实时搜索按上游价 7.5 算力/次（0.01 USD × 7.5 × 100）扣费，实际扣 = 单价 × 调用次数；
  本地快照搜索（source=snapshot）按 DOUYIN_DESK_SEARCH_CREDITS，默认 1 算力/次（0 = 免费）。
"""
from __future__ import annotations

import os
from decimal import Decimal, ROUND_CEILING
from typing import Any, Dict, Optional

from fastapi import HTTPException
from sqlalchemy.orm import Session

from .credit_ledger import append_credit_ledger
from .credits_amount import credits_json_float, quantize_credits, user_balance_decimal

CREDITS_PER_YUAN = Decimal("100")
_DEFAULT_480P_YUAN_PER_SECOND = Decimal("0.3")   # 3.0 北京 480P 原价 0.3 元/秒
_DEFAULT_YUAN_PER_SECOND = Decimal("0.6")     # 720P 输入+输出单价
_DEFAULT_1080P_YUAN_PER_SECOND = Decimal("1.2")  # 3.0 北京 1080P 原价 1.2 元/秒（3.0 有 480P 档）
_DEFAULT_BILLABLE_SIDES = Decimal("2")        # 输入 1 + 输出 1
_DEFAULT_MARKUP = Decimal("1.5")              # 与 wan_role 一致的加价倍率
_DEFAULT_SEARCH_CREDITS = Decimal("1")        # 每次搜索


def _decimal_env(name: str, default: Decimal) -> Decimal:
    raw = (os.environ.get(name) or "").strip()
    if not raw:
        return default
    try:
        value = Decimal(raw)
    except Exception:  # noqa: BLE001
        return default
    return value if value >= 0 else default


RESOLUTIONS = ("480P", "720P", "1080P")   # 2026-10-05：上游换成 wan3.0-video-prime 后支持 480P，放给用户选
DEFAULT_RESOLUTION = "720P"


def normalize_resolution(raw: object) -> str:
    value = str(raw or "").strip().upper()
    return value if value in RESOLUTIONS else DEFAULT_RESOLUTION


def yuan_per_second(resolution: object = DEFAULT_RESOLUTION) -> Decimal:
    """按分辨率取单价：480P 0.3 元/秒、720P 0.6 元/秒、1080P 1.2 元/秒（wan3.0-video 原价，输入+输出都按这个价）。"""
    normalized = normalize_resolution(resolution)
    if normalized == "1080P":
        return _decimal_env("DOUYIN_VIDEOEDIT_1080P_YUAN_PER_SECOND", _DEFAULT_1080P_YUAN_PER_SECOND)
    if normalized == "480P":
        return _decimal_env("DOUYIN_VIDEOEDIT_480P_YUAN_PER_SECOND", _DEFAULT_480P_YUAN_PER_SECOND)
    return _decimal_env("DOUYIN_VIDEOEDIT_YUAN_PER_SECOND", _DEFAULT_YUAN_PER_SECOND)


def billable_sides() -> Decimal:
    return _decimal_env("DOUYIN_VIDEOEDIT_BILLABLE_SIDES", _DEFAULT_BILLABLE_SIDES) or _DEFAULT_BILLABLE_SIDES


def markup() -> Decimal:
    return _decimal_env("DOUYIN_VIDEOEDIT_MARKUP", _DEFAULT_MARKUP) or Decimal("1")


def search_credits() -> Decimal:
    return quantize_credits(_decimal_env("DOUYIN_DESK_SEARCH_CREDITS", _DEFAULT_SEARCH_CREDITS))


def estimate_imitation(seconds: Any, resolution: object = DEFAULT_RESOLUTION, *,
                       sides: Optional[Decimal] = None) -> Dict[str, Any]:
    """按「输入+输出都计费」估算一次做同款的成本与扣费（分辨率影响单价）。

    sides 默认取 billable_sides()（=2，输入+输出各一份）；当调用方已经按
    「输入秒数 + 输出秒数」算好总秒数时，传 sides=1，避免重复乘。
    """
    try:
        value = Decimal(str(seconds or 0))
    except Exception:  # noqa: BLE001
        value = Decimal("0")
    if value <= 0:
        value = Decimal("5")
    factor = billable_sides() if sides is None else Decimal(str(sides))
    billable = (value * factor).to_integral_value(rounding=ROUND_CEILING)
    rate = yuan_per_second(resolution)
    cost_yuan = (Decimal(billable) * rate).quantize(Decimal("0.0001"))
    credits = quantize_credits(cost_yuan * CREDITS_PER_YUAN * markup())
    return {
        "seconds": int(value),
        "billable_seconds": int(billable),
        "cost_yuan": float(cost_yuan),
        "credits": credits_json_float(credits),
        "resolution": normalize_resolution(resolution),
        "yuan_per_second": float(rate),
        "billable_sides": float(billable_sides() if sides is None else Decimal(str(sides))),
        "markup": float(markup()),
    }


def _apply(db: Session, user: Any, credits: Decimal, *, reason: str, refund: bool = False) -> Decimal:
    if credits <= 0:
        return Decimal("0")
    balance = user_balance_decimal(user)
    if not refund and balance < credits:
        raise HTTPException(
            status_code=402,
            detail=f"算力不足：本次预计消耗 {credits_json_float(credits)} 算力，当前余额 {credits_json_float(balance)}。请充值后重试。",
        )
    delta = credits if refund else -credits
    user.credits = quantize_credits(balance + delta)
    append_credit_ledger(db, int(getattr(user, "id", 0) or 0), delta, "refund" if refund else "deduct",
                         quantize_credits(user.credits), meta={"reason": reason})
    db.commit()
    return credits


def check_balance(db: Session, user: Any, credits: Decimal) -> None:
    """只做余额校验（不扣费、不写流水）。

    2026-10-05：跟创改成「按原视频实际时长」计费后，扣费时机挪到拿到时长之后；
    但提交前仍要先按上限确认余额够，避免没钱还去跑一遍下载/裁剪/上传。
    """
    need = quantize_credits(credits or 0)
    if need <= 0:
        return
    try:
        db.refresh(user)
    except Exception:  # noqa: BLE001 用例/跨会话场景下 user 可能不绑定当前 session，用现值即可
        pass
    balance = user_balance_decimal(user)
    if balance < need:
        raise HTTPException(
            status_code=402,
            detail=f"算力不足：本次最多预计消耗 {credits_json_float(need)} 算力，当前余额 {credits_json_float(balance)}。请充值后重试。",
        )


def deduct(db: Session, user: Any, credits: Decimal, *, reason: str) -> Decimal:
    return _apply(db, user, quantize_credits(credits), reason=reason)


def refund(db: Session, user: Any, credits: Decimal, *, reason: str) -> Decimal:
    return _apply(db, user, quantize_credits(credits), reason=reason, refund=True)
