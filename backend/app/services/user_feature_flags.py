from __future__ import annotations

from sqlalchemy.orm import Session

from ..models import UserSkillVisibility

DOUYIN_LEADS_FEATURE_ID = "douyin_leads"
DOUYIN_LEADS_ACCESS_KEY = "douyin_leads_access"

FEATURE_FLAG_PACKAGES: tuple[dict, ...] = (
    {
        "id": DOUYIN_LEADS_FEATURE_ID,
        "name": "抖音获客入口",
        "store_visibility": "入口权限",
        "unlock_price_yuan": None,
        "unlock_price_credits": None,
        "capabilities_count": 0,
        "feature_key": DOUYIN_LEADS_ACCESS_KEY,
    },
)


def user_has_feature(db: Session, user_id: int, feature_id: str) -> bool:
    if not user_id or not feature_id:
        return False
    row = (
        db.query(UserSkillVisibility.id)
        .filter(
            UserSkillVisibility.user_id == int(user_id),
            UserSkillVisibility.package_id == feature_id,
        )
        .first()
    )
    return row is not None


def user_feature_flags(db: Session, user_id: int) -> dict[str, bool]:
    douyin_leads = user_has_feature(db, user_id, DOUYIN_LEADS_FEATURE_ID)
    return {
        DOUYIN_LEADS_ACCESS_KEY: douyin_leads,
        DOUYIN_LEADS_FEATURE_ID: douyin_leads,
    }
