"""商家店铺状态对商家能力的影响（公司 / 管理后台 / 商家后台共用）。"""
from __future__ import annotations

from typing import Optional

# 被停用 / 驳回的店铺：不允许登录商家后台，也不允许继续操作。
SHOP_MERCHANT_BLOCKED_STATUSES = {
    "suspended": "店铺已停用，请联系平台处理",
    "rejected": "店铺审核未通过，请联系平台处理",
}


def shop_merchant_blocked_reason(status: Optional[str]) -> str:
    """返回阻止原因；空字符串表示该状态不限制。"""
    return SHOP_MERCHANT_BLOCKED_STATUSES.get(str(status or "").strip().lower(), "")
